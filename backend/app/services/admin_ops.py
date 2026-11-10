"""
Phase 6 / 7 administration: audit-log search, database backups and system health.

Backups
* A consistent logical dump (REPEATABLE READ snapshot, read-only) of every table, written
  as gzip-compressed SQL that the standard `mysql` client can restore. The file carries a
  manifest (row count per table, Alembic revision) and ends with a completion marker.
* Every backup gets a SHA-256 checksum and a `backup_history` row. Verification re-reads
  the file, checks the checksum, the completion marker and the per-table row counts
  against the manifest. If BACKUP_VERIFY_DATABASE is configured (a scratch schema whose
  name ends in "_restore_verify"), verification also restores into it and compares counts.
* An optional daily scheduled backup runs from the worker (setting backup.daily_time).
* Nothing here restores over the live database; restoring is an operator procedure
  (docs/DEPLOYMENT.md).
"""
import asyncio
import gzip
import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import unquote, urlparse

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import business_now, local_date
from app.core.config import settings
from app.models.admin import BackupRecord
from app.models.auth import AuditLog, Setting, User
from app.services import app_settings

HEARTBEAT_KEY = "system.worker_heartbeat"
MARKER = "-- PHARMACY-ERP DUMP COMPLETE"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Audit log ──────────────────────────────────────────────────────────────────

async def search_audit(db: AsyncSession, pharmacy_id: int, *, entity_type=None, action=None, user_id=None,
                       date_from=None, date_to=None, q=None, limit=100, offset=0) -> dict:
    from app.core.clock import day_bounds
    stmt = select(AuditLog, User.name).outerjoin(User, User.id == AuditLog.user_id).where(AuditLog.pharmacy_id == pharmacy_id)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if action:
        stmt = stmt.where(AuditLog.action.like(f"{action}%"))
    if user_id:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if date_from:
        stmt = stmt.where(AuditLog.timestamp >= day_bounds(date_from)[0])
    if date_to:
        stmt = stmt.where(AuditLog.timestamp < day_bounds(date_to)[1])
    if q:
        stmt = stmt.where(AuditLog.entity_id.like(f"%{q}%") | AuditLog.reason.like(f"%{q}%"))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(AuditLog.id.desc()).limit(limit).offset(offset))).all()
    facets = {
        "entity_types": sorted(t for t in (await db.execute(select(AuditLog.entity_type).where(
            AuditLog.pharmacy_id == pharmacy_id).distinct())).scalars() if t),
        "actions": sorted(a for a in (await db.execute(select(AuditLog.action).where(
            AuditLog.pharmacy_id == pharmacy_id).distinct())).scalars() if a),
    }
    return {"total": total, "facets": facets, "items": [{
        "id": a.id, "timestamp": a.timestamp, "user_id": a.user_id, "user_name": name, "action": a.action,
        "entity_type": a.entity_type, "entity_id": a.entity_id, "reason": a.reason, "ip_address": a.ip_address,
        "metadata": a.metadata_json,        # Services never write passwords or tokens here
    } for a, name in rows]}


# ── Worker heartbeat & system health ───────────────────────────────────────────

async def worker_heartbeat(db: AsyncSession) -> None:
    from app.models.auth import Pharmacy
    for pid in (await db.execute(select(Pharmacy.id))).scalars().all():
        row = (await db.execute(select(Setting).where(Setting.pharmacy_id == pid, Setting.setting_key == HEARTBEAT_KEY))).scalar_one_or_none()
        value = utcnow().isoformat()
        if row is None:
            db.add(Setting(pharmacy_id=pid, setting_key=HEARTBEAT_KEY, setting_value=value, description="Last worker pass (UTC)"))
        else:
            row.setting_value = value


def _alembic_head() -> Optional[str]:
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        cfg = Config(os.path.join(os.path.dirname(__file__), "..", "..", "alembic.ini"))
        cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "..", "alembic"))
        return ScriptDirectory.from_config(cfg).get_current_head()
    except Exception:
        return None


async def system_health(db: AsyncSession, pharmacy_id: int) -> dict:
    from app.models.accounting import GlPostingIssue
    from app.models.sales import MessageOutbox
    from app.services import ledger
    checks = {}
    try:
        version = (await db.execute(text("SELECT VERSION()"))).scalar_one()
        checks["database"] = {"ok": True, "version": version}
    except Exception as exc:     # pragma: no cover - the request itself needs the DB
        checks["database"] = {"ok": False, "error": str(exc)[:200]}
    try:
        async with db.begin_nested():
            current = (await db.execute(text("SELECT version_num FROM alembic_version"))).scalar_one_or_none()
    except Exception:
        current = None           # Not managed by Alembic (e.g. a test database)
    head = _alembic_head()
    checks["migrations"] = {"ok": head is None or current == head, "current": current, "code_head": head,
                            "detail": None if current == head else "Database revision differs from the application code; "
                                                                   "apply migrations during the next planned update"}
    try:
        usage = shutil.disk_usage(settings.BACKUP_DIR if os.path.isdir(settings.BACKUP_DIR) else ".")
        free_pct = round(usage.free * 100 / usage.total, 1)
        checks["disk"] = {"ok": free_pct >= 10, "free_gb": round(usage.free / 2**30, 1), "total_gb": round(usage.total / 2**30, 1),
                          "free_percent": free_pct, "path": settings.BACKUP_DIR}
    except OSError as exc:
        checks["disk"] = {"ok": False, "error": str(exc)[:200]}
    last = (await db.execute(select(BackupRecord).where(BackupRecord.pharmacy_id == pharmacy_id,
                                                        BackupRecord.status == "COMPLETED").order_by(BackupRecord.id.desc()).limit(1))).scalars().first()
    age_h = round((utcnow() - _aware(last.finished_at)).total_seconds() / 3600, 1) if last and last.finished_at else None
    checks["backup"] = {"ok": age_h is not None and age_h <= 26, "last_file": last.file_name if last else None,
                        "last_at": last.finished_at if last else None, "age_hours": age_h,
                        "verified": last.verify_status if last else None,
                        "detail": None if last else "No backup recorded by the application yet"}
    beat = await app_settings.get_setting(db, pharmacy_id, HEARTBEAT_KEY, "")
    beat_age = round((utcnow() - datetime.fromisoformat(beat)).total_seconds() / 60, 1) if beat else None
    checks["worker"] = {"ok": beat_age is not None and beat_age <= 10, "last_seen": beat or None, "minutes_ago": beat_age}
    pending = (await db.execute(select(func.count(MessageOutbox.id)).where(
        MessageOutbox.pharmacy_id == pharmacy_id, MessageOutbox.status.in_(("PENDING", "QUEUED"))))).scalar_one()
    failed = (await db.execute(select(func.count(MessageOutbox.id)).where(
        MessageOutbox.pharmacy_id == pharmacy_id, MessageOutbox.status == "FAILED"))).scalar_one()
    checks["messaging"] = {"ok": failed == 0, "pending": pending, "failed": failed,
                           "provider": settings.WHATSAPP_PROVIDER if hasattr(settings, "WHATSAPP_PROVIDER") else None}
    books = await ledger.books_start_date(db, pharmacy_id)
    issues = (await db.execute(select(func.count(GlPostingIssue.id)).where(
        GlPostingIssue.pharmacy_id == pharmacy_id, GlPostingIssue.status == "OPEN"))).scalar_one()
    checks["accounting"] = {"ok": issues == 0, "set_up": books is not None, "books_start_date": books, "open_posting_issues": issues}
    return {"ok": all(c.get("ok") for c in checks.values()), "environment": settings.APP_ENV, "checked_at": utcnow(), "checks": checks}


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


# ── Backups ────────────────────────────────────────────────────────────────────

def _connect(url: str, database: Optional[str] = None):
    import pymysql
    u = urlparse(url.replace("mysql+pymysql://", "mysql://").replace("mysql+aiomysql://", "mysql://"))
    return pymysql.connect(host=u.hostname, port=u.port or 3306, user=unquote(u.username or ""), password=unquote(u.password or ""),
                           database=database or u.path.lstrip("/"), charset="utf8mb4", autocommit=False)


def _sql_value(conn, value) -> str:
    """SQL literal for a dumped value. Binary values (e.g. logo images) become hex literals: the dump is UTF-8
    text, and pymysql's escape() would emit raw bytes that cannot be written as text."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "X'" + bytes(value).hex() + "'"
    return conn.escape(value)


def _dump_to_file(path: str) -> dict:
    """Blocking: write the dump; returns manifest data. Runs in a worker thread."""
    conn = _connect(settings.DATABASE_URL_SYNC)
    manifest = {}
    try:
        cur = conn.cursor()
        cur.execute("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        cur.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
        cur.execute("SHOW FULL TABLES WHERE Table_type = 'BASE TABLE'")
        tables = sorted(r[0] for r in cur.fetchall())
        revision = None
        if "alembic_version" in tables:
            cur.execute("SELECT version_num FROM alembic_version")
            revision = (cur.fetchone() or [None])[0]
        for t in tables:
            cur.execute(f"SELECT COUNT(*) FROM `{t}`")
            manifest[t] = cur.fetchone()[0]
        with gzip.open(path, "wt", encoding="utf-8", newline="\n") as out:
            out.write(f"-- Pharmacy ERP logical backup {utcnow().isoformat()}\n")
            out.write(f"-- MANIFEST {json.dumps({'alembic_revision': revision, 'tables': manifest})}\n")
            out.write("SET NAMES utf8mb4;\nSET FOREIGN_KEY_CHECKS=0;\nSET UNIQUE_CHECKS=0;\n")
            for t in tables:
                cur.execute(f"SHOW CREATE TABLE `{t}`")
                create = cur.fetchone()[1]
                out.write(f"\n-- TABLE {t}\nDROP TABLE IF EXISTS `{t}`;\n{create};\n")
                cur.execute(f"SELECT * FROM `{t}`")
                cols = ", ".join(f"`{d[0]}`" for d in cur.description)
                written = 0
                while True:
                    batch = cur.fetchmany(500)
                    if not batch:
                        break
                    values = ",".join("(" + ",".join(_sql_value(conn, v) for v in row) + ")" for row in batch)
                    out.write(f"INSERT INTO `{t}` ({cols}) VALUES {values};\n")
                    written += len(batch)
                out.write(f"-- ROWS {t} {written}\n")
                if written != manifest[t]:
                    raise RuntimeError(f"Table {t}: wrote {written} rows but counted {manifest[t]}")
            out.write("SET FOREIGN_KEY_CHECKS=1;\nSET UNIQUE_CHECKS=1;\n")
            out.write(MARKER + "\n")
        conn.rollback()
        return {"tables": manifest, "alembic_revision": revision}
    finally:
        conn.close()


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


async def run_backup(db: AsyncSession, pharmacy_id: int, user_id: Optional[int], trigger: str) -> BackupRecord:
    os.makedirs(settings.BACKUP_DIR, exist_ok=True)
    stamp = business_now().strftime("%Y%m%d_%H%M%S")
    name = f"pharmacy_erp_{trigger.lower()}_{stamp}.sql.gz"
    path = os.path.join(settings.BACKUP_DIR, name)
    rec = BackupRecord(pharmacy_id=pharmacy_id, file_name=name, trigger=trigger, status="RUNNING", created_by=user_id)
    db.add(rec)
    await db.commit()          # The RUNNING row survives even if the dump fails
    try:
        data = await asyncio.to_thread(_dump_to_file, path)
        rec.status, rec.finished_at = "COMPLETED", utcnow()
        rec.size_bytes, rec.sha256 = os.path.getsize(path), await asyncio.to_thread(_sha256, path)
        rec.tables, rec.total_rows = len(data["tables"]), sum(data["tables"].values())
        rec.manifest_json, rec.alembic_revision = json.dumps(data["tables"]), data["alembic_revision"]
    except Exception as exc:
        rec.status, rec.finished_at, rec.error = "FAILED", utcnow(), str(exc)[:2000]
        if os.path.exists(path):
            os.replace(path, path + ".failed")
    db.add(AuditLog(pharmacy_id=pharmacy_id, user_id=user_id, action=f"backup.{rec.status.lower()}", entity_type="backup",
                    entity_id=str(rec.id), metadata_json=json.dumps({"file": name, "trigger": trigger, "sha256": rec.sha256,
                                                                     "rows": rec.total_rows, "error": rec.error})))
    await db.commit()
    return rec


def _verify_file(path: str, sha256: str, manifest: dict) -> tuple:
    problems = []
    if _sha256(path) != sha256:
        problems.append("Checksum differs from the one recorded when the backup was made")
    counts, complete = {}, False
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                if line.startswith("-- ROWS "):
                    _, _, table, n = line.split()
                    counts[table] = int(n)
                elif line.strip() == MARKER:
                    complete = True
    except (OSError, EOFError, UnicodeDecodeError, ValueError) as exc:
        problems.append(f"The file cannot be read as a backup: {exc}")
        return problems, counts
    if not complete:
        problems.append("The file is incomplete (no completion marker)")
    for table, n in manifest.items():
        if counts.get(table) != n:
            problems.append(f"{table}: {counts.get(table)} rows in file, {n} expected")
    return problems, counts


def _restore_into(path: str, database: str) -> dict:
    """Blocking: restore the dump into a scratch schema and count rows."""
    conn = _connect(settings.DATABASE_URL_SYNC, database=database)
    try:
        cur = conn.cursor()
        stmt = []
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                if line.startswith("--") and not stmt:
                    continue
                stmt.append(line)
                if line.rstrip().endswith(";"):
                    cur.execute("".join(stmt))
                    stmt = []
        conn.commit()
        cur.execute("SHOW FULL TABLES WHERE Table_type = 'BASE TABLE'")
        out = {}
        for (t, *_rest) in cur.fetchall():
            cur.execute(f"SELECT COUNT(*) FROM `{t}`")
            out[t] = cur.fetchone()[0]
        return out
    finally:
        conn.close()


async def verify_backup(db: AsyncSession, user: User, backup_id: int) -> BackupRecord:
    rec = (await db.execute(select(BackupRecord).where(BackupRecord.id == backup_id,
                                                       BackupRecord.pharmacy_id == user.pharmacy_id))).scalar_one_or_none()
    if rec is None:
        raise HTTPException(status_code=404, detail="Backup not found")
    if rec.status != "COMPLETED":
        raise HTTPException(status_code=409, detail="Only completed backups can be verified")
    path = os.path.join(settings.BACKUP_DIR, rec.file_name)
    if not os.path.exists(path):
        raise HTTPException(status_code=410, detail="The backup file is no longer in the backup folder")
    manifest = json.loads(rec.manifest_json or "{}")
    problems, _ = await asyncio.to_thread(_verify_file, path, rec.sha256, manifest)
    detail = {"file_checks": "passed" if not problems else problems}
    scratch = getattr(settings, "BACKUP_VERIFY_DATABASE", "") or ""
    live_db = urlparse(settings.DATABASE_URL_SYNC.replace("mysql+pymysql://", "mysql://")).path.lstrip("/")
    if scratch and not problems:
        if not scratch.endswith("_restore_verify") or scratch == live_db:
            problems.append("BACKUP_VERIFY_DATABASE must be a scratch schema ending in '_restore_verify'")
        else:
            try:
                restored = await asyncio.to_thread(_restore_into, path, scratch)
                diff = {t: (restored.get(t), n) for t, n in manifest.items() if restored.get(t) != n}
                detail["restore_test"] = "passed" if not diff else {"differences": diff}
                if diff:
                    problems.append("Restored row counts differ from the backup manifest")
            except Exception as exc:
                problems.append(f"Restore test failed: {str(exc)[:300]}")
    else:
        detail["restore_test"] = "not configured (set BACKUP_VERIFY_DATABASE to enable)" if not scratch else "skipped"
    rec.verified_at, rec.verify_status = utcnow(), "PASSED" if not problems else "FAILED"
    rec.verify_detail = json.dumps({**detail, "problems": problems})
    db.add(AuditLog(pharmacy_id=user.pharmacy_id, user_id=user.id, action="backup.verify", entity_type="backup",
                    entity_id=str(rec.id), metadata_json=json.dumps({"result": rec.verify_status, "problems": problems[:20]})))
    await db.flush()
    return rec


def backup_path(rec: BackupRecord) -> str:
    if not re.fullmatch(r"pharmacy_erp_[a-z]+_\d{8}_\d{6}\.sql\.gz", rec.file_name):
        raise HTTPException(status_code=422, detail="Unexpected backup file name")
    path = os.path.join(settings.BACKUP_DIR, rec.file_name)
    if not os.path.exists(path):
        raise HTTPException(status_code=410, detail="The backup file is no longer in the backup folder")
    return path


async def maybe_scheduled_backup(session_factory) -> Optional[str]:
    """Called by the worker: one SCHEDULED backup per day after backup.daily_time (HH:MM, local), if set."""
    from app.models.auth import Pharmacy
    async with session_factory() as db:
        pids = (await db.execute(select(Pharmacy.id))).scalars().all()
        for pid in pids:
            when = (await app_settings.get_setting(db, pid, "backup.daily_time", "")).strip()
            if not re.fullmatch(r"\d{2}:\d{2}", when):
                continue
            now = business_now()
            if now.strftime("%H:%M") < when:
                continue
            done = (await db.execute(select(BackupRecord).where(
                BackupRecord.pharmacy_id == pid, BackupRecord.trigger == "SCHEDULED",
                BackupRecord.status.in_(("COMPLETED", "RUNNING"))).order_by(BackupRecord.id.desc()).limit(1))).scalars().first()
            if done and local_date(_aware(done.started_at)) == now.date():
                continue
            rec = await run_backup(db, pid, None, "SCHEDULED")
            return rec.status
    return None
