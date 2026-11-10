"""Phase 6/7 administration API: audit log viewer, backups, system health, purchase report."""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import today_local
from app.core.database import get_db
from app.core.deps import AuthContext, require_context
from app.core.json import decimal_json
from app.models.admin import BackupRecord
from app.models.auth import AuditLog
from app.services import admin_ops, purchase_reports
from app.services.sales_reports import to_csv

router = APIRouter(tags=["Administration"])


@router.get("/admin/audit-logs")
async def audit_logs(
    entity_type: Optional[str] = None, action: Optional[str] = None, user_id: Optional[int] = None,
    date_from: Optional[date] = None, date_to: Optional[date] = None, q: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
    ctx: AuthContext = Depends(require_context("audit.view")), db: AsyncSession = Depends(get_db),
):
    return decimal_json(await admin_ops.search_audit(db, ctx.pharmacy_id, entity_type=entity_type, action=action, user_id=user_id,
                                                     date_from=date_from, date_to=date_to, q=q, limit=limit, offset=offset))


@router.get("/admin/system-health")
async def system_health(ctx: AuthContext = Depends(require_context("settings.manage")), db: AsyncSession = Depends(get_db)):
    return decimal_json(await admin_ops.system_health(db, ctx.pharmacy_id))


def _backup_out(r: BackupRecord) -> dict:
    return {"id": r.id, "file_name": r.file_name, "trigger": r.trigger, "status": r.status, "size_bytes": r.size_bytes,
            "sha256": r.sha256, "tables": r.tables, "total_rows": r.total_rows, "alembic_revision": r.alembic_revision,
            "error": r.error, "started_at": r.started_at, "finished_at": r.finished_at, "verified_at": r.verified_at,
            "verify_status": r.verify_status, "verify_detail": r.verify_detail}


@router.get("/admin/backups")
async def list_backups(ctx: AuthContext = Depends(require_context("backups.manage")), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(BackupRecord).where(BackupRecord.pharmacy_id == ctx.pharmacy_id)
                             .order_by(BackupRecord.id.desc()).limit(100))).scalars().all()
    return decimal_json([_backup_out(r) for r in rows])


@router.post("/admin/backups", status_code=201)
async def create_backup(ctx: AuthContext = Depends(require_context("backups.manage")), db: AsyncSession = Depends(get_db)):
    rec = await admin_ops.run_backup(db, ctx.pharmacy_id, ctx.user.id, "MANUAL")
    if rec.status != "COMPLETED":
        from fastapi import HTTPException
        # The FAILED row is already committed; tell the user why instead of a bare 500
        raise HTTPException(status_code=500, detail=f"Backup failed: {rec.error}")
    return decimal_json(_backup_out(rec), status_code=201)


@router.post("/admin/backups/{backup_id}/verify")
async def verify_backup(backup_id: int, ctx: AuthContext = Depends(require_context("backups.manage")),
                        db: AsyncSession = Depends(get_db)):
    return decimal_json(_backup_out(await admin_ops.verify_backup(db, ctx.user, backup_id)))


@router.get("/admin/backups/{backup_id}/download")
async def download_backup(backup_id: int, ctx: AuthContext = Depends(require_context("backups.manage")),
                          db: AsyncSession = Depends(get_db)):
    from fastapi import HTTPException
    rec = (await db.execute(select(BackupRecord).where(BackupRecord.id == backup_id,
                                                       BackupRecord.pharmacy_id == ctx.pharmacy_id))).scalar_one_or_none()
    if rec is None or rec.status != "COMPLETED":
        raise HTTPException(status_code=404, detail="Backup not found")
    path = admin_ops.backup_path(rec)
    db.add(AuditLog(pharmacy_id=ctx.pharmacy_id, user_id=ctx.user.id, action="backup.download", entity_type="backup",
                    entity_id=str(rec.id)))
    await db.commit()
    return FileResponse(path, media_type="application/gzip", filename=rec.file_name)


@router.get("/reports/purchases")
async def purchases_report(date_from: Optional[date] = None, date_to: Optional[date] = None, format: str = "json",
                           ctx: AuthContext = Depends(require_context("reports.view")), db: AsyncSession = Depends(get_db)):
    to = date_to or today_local()
    data = await purchase_reports.purchase_summary(db, ctx.pharmacy_id, date_from or to.replace(day=1), to)
    if format == "csv":
        if "reports.export" not in ctx.permissions:
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="Permission 'reports.export' is required to export")
        return PlainTextResponse(to_csv(data["suppliers"]), media_type="text/csv",
                                 headers={"Content-Disposition": "attachment; filename=purchase_summary.csv"})
    return decimal_json(data)
