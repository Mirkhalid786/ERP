"""
Migration guard (alembic/env.py) and the API / worker schema checks.

Everything here runs against throwaway SQLite files or in-memory databases; nothing
connects to MySQL. Alembic is exercised both through its CLI (as the container start
command does) and through the programmatic API, so neither path can bypass the guard.
"""
import asyncio
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import app.worker as worker
from app.core import schema_guard
from app.core.schema_guard import check_schema, code_head

BACKEND = Path(__file__).resolve().parents[1]
HEAD = code_head()


# ── Helpers ────────────────────────────────────────────────────────────────────

def alembic(db: Path, *args: str, allow: bool = False) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "ALLOW_DB_MIGRATIONS"}
    env["DATABASE_URL_SYNC"] = f"sqlite:///{db.as_posix()}"
    if allow:
        env["ALLOW_DB_MIGRATIONS"] = "1"
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND, env=env,
                          capture_output=True, text=True, timeout=120)


def snapshot(db: Path) -> tuple:
    """Every table and the stored revision — to prove a refused command changed nothing."""
    if not db.exists():
        return ("<no file>",)
    con = sqlite3.connect(db)
    try:
        tables = tuple(sorted(r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")))
        version = tuple(con.execute("SELECT version_num FROM alembic_version")) if "alembic_version" in tables else ()
        return tables, version
    finally:
        con.close()


def stamped(db: Path, revision: str) -> None:
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
    con.execute("INSERT INTO alembic_version VALUES (?)", (revision,))
    con.commit()
    con.close()


# ── 1. Read-only Alembic commands still work ───────────────────────────────────

def test_read_only_commands_work_without_the_flag(tmp_path):
    db = tmp_path / "ro.db"
    stamped(db, "008")
    before = snapshot(db)
    current = alembic(db, "current")
    assert current.returncode == 0, current.stderr
    assert "008" in current.stdout
    for args in (("history",), ("heads",), ("show", "head"), ("history", "-i")):
        r = alembic(db, *args)
        assert r.returncode == 0, (args, r.stderr)
    assert HEAD in alembic(db, "heads").stdout
    assert snapshot(db) == before


# ── 2. Changing commands are blocked unless ALLOW_DB_MIGRATIONS=1 ──────────────

@pytest.mark.parametrize("args", [("upgrade", "head"), ("upgrade", "+1"), ("downgrade", "-1"), ("downgrade", "base"),
                                  ("stamp", "head"), ("stamp", "007"), ("ensure_version",)])
def test_changing_commands_are_refused_on_an_older_database(tmp_path, args):
    db = tmp_path / "old.db"
    stamped(db, "008")
    before = snapshot(db)
    r = alembic(db, *args)
    assert r.returncode == 3, (r.returncode, r.stdout, r.stderr)
    assert "[migration guard] REFUSED" in r.stderr and "Nothing was changed" in r.stderr
    assert snapshot(db) == before                     # Still 008, no new tables


def test_upgrade_on_an_empty_database_is_refused_and_creates_nothing(tmp_path):
    db = tmp_path / "empty.db"
    sqlite3.connect(db).close()
    r = alembic(db, "upgrade", "head")
    assert r.returncode == 3 and "REFUSED 'upgrade'" in r.stderr
    assert snapshot(db) == ((), ())                   # Not even alembic_version


def test_downgrade_and_stamp_are_refused_even_at_head(tmp_path):
    db = tmp_path / "head.db"
    stamped(db, HEAD)
    before = snapshot(db)
    for args in (("downgrade", "-1"), ("stamp", HEAD), ("stamp", "base")):
        r = alembic(db, *args)
        assert r.returncode == 3, (args, r.stderr)
    assert snapshot(db) == before


def test_upgrade_when_already_at_head_is_a_permitted_no_op(tmp_path):
    db = tmp_path / "noop.db"
    stamped(db, HEAD)
    before = snapshot(db)
    r = alembic(db, "upgrade", "head")
    assert r.returncode == 0, r.stderr
    assert "REFUSED" not in r.stderr and snapshot(db) == before


def test_the_explicit_flag_allows_a_changing_command(tmp_path):
    db = tmp_path / "allowed.db"
    stamped(db, "008")
    r = alembic(db, "stamp", "009", allow=True)       # stamp only rewrites alembic_version: safe on SQLite
    assert r.returncode == 0, r.stderr
    assert "ALLOW_DB_MIGRATIONS=1: allowing 'do_stamp'" in r.stderr
    assert snapshot(db)[1] == (("009",),)
    other = dict(os.environ)
    for value in ("true", "yes", "0", " 1"):          # Only the exact value 1 counts
        other_db = tmp_path / f"v{abs(hash(value))}.db"
        stamped(other_db, "008")
        env = {**{k: v for k, v in other.items() if k != "ALLOW_DB_MIGRATIONS"},
               "DATABASE_URL_SYNC": f"sqlite:///{other_db.as_posix()}", "ALLOW_DB_MIGRATIONS": value}
        res = subprocess.run([sys.executable, "-m", "alembic", "stamp", "009"], cwd=BACKEND, env=env,
                             capture_output=True, text=True, timeout=120)
        assert res.returncode == 3, value
        assert snapshot(other_db)[1] == (("008",),)


# ── 3. No other path bypasses the guard ────────────────────────────────────────

def test_programmatic_alembic_api_is_guarded_too(tmp_path, monkeypatch):
    """alembic.command.* (what a script or another tool would call) goes through the same env.py guard."""
    from alembic import command
    from alembic.config import Config
    db = tmp_path / "api.db"
    stamped(db, "008")
    before = snapshot(db)
    monkeypatch.delenv("ALLOW_DB_MIGRATIONS", raising=False)
    monkeypatch.setenv("DATABASE_URL_SYNC", f"sqlite:///{db.as_posix()}")
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    for call in (lambda: command.upgrade(cfg, "head"), lambda: command.downgrade(cfg, "-1"),
                 lambda: command.stamp(cfg, "head")):
        with pytest.raises(SystemExit) as exc:
            call()
        assert exc.value.code == 3
    assert snapshot(db) == before
    command.current(cfg)                              # Read-only API call still works


def test_container_start_command_stops_before_the_server_on_an_old_schema(tmp_path):
    """The image CMD is `alembic upgrade head && uvicorn ...`: the guard's non-zero exit stops the chain."""
    db = tmp_path / "cmd.db"
    stamped(db, "008")
    env = {k: v for k, v in os.environ.items() if k != "ALLOW_DB_MIGRATIONS"}
    env["DATABASE_URL_SYNC"] = f"sqlite:///{db.as_posix()}"
    marker = tmp_path / "server_started"
    chain = f'"{sys.executable}" -m alembic upgrade head && "{sys.executable}" -c "open(r\'{marker}\', \'w\').close()"'
    r = subprocess.run(chain, shell=True, cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 3 and not marker.exists()
    assert snapshot(db)[1] == (("008",),)


# ── 4. API and worker refuse an incompatible schema without writing ────────────

async def _engine_with(revision=None):
    import tempfile
    path = Path(tempfile.mkdtemp()) / "guard.db"       # A file, so every connection sees the same database
    eng = create_async_engine(f"sqlite+aiosqlite:///{path.as_posix()}")
    async with eng.begin() as conn:
        await conn.execute(text("CREATE TABLE marker (id INTEGER PRIMARY KEY)"))
        if revision is not None:
            await conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
            await conn.execute(text("INSERT INTO alembic_version VALUES (:r)"), {"r": revision})
    return eng


async def _tables(eng) -> list:
    async with eng.connect() as conn:
        return sorted(r[0] for r in (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).all())


@pytest.mark.asyncio
async def test_check_schema_states_and_never_writes(monkeypatch):
    monkeypatch.delenv(schema_guard.UNMANAGED_ENV, raising=False)
    old = await _engine_with("008")
    state = await check_schema(old)
    assert not state["ok"] and state["database_revision"] == "008" and HEAD in state["reason"]
    current = await _engine_with(HEAD)
    assert (await check_schema(current))["ok"]
    unmanaged = await _engine_with(None)
    before = await _tables(unmanaged)
    state = await check_schema(unmanaged)
    assert not state["ok"] and not state["managed"]
    assert await _tables(unmanaged) == before          # No alembic_version created
    monkeypatch.setenv(schema_guard.UNMANAGED_ENV, "1")
    assert (await check_schema(unmanaged))["ok"]
    assert not (await check_schema(old))["ok"]          # The unmanaged flag cannot excuse a wrong revision
    for e in (old, current, unmanaged):
        await e.dispose()


@pytest.mark.asyncio
async def test_api_startup_refuses_an_old_schema(monkeypatch):
    import app.core.database as database
    import app.main as main
    eng = await _engine_with("008")
    from sqlalchemy.ext.asyncio import async_sessionmaker
    monkeypatch.setattr(database, "AsyncSessionLocal", async_sessionmaker(eng))
    monkeypatch.setattr(main, "engine", eng)
    good = await _engine_with(HEAD)
    try:
        with pytest.raises(RuntimeError, match="Refusing to start"):
            async with main.lifespan(main.app):
                pass
        monkeypatch.setattr(database, "AsyncSessionLocal", async_sessionmaker(good))
        monkeypatch.setattr(main, "engine", good)
        async with main.lifespan(main.app):
            pass                                       # Starts normally at head
    finally:
        await eng.dispose()
        await good.dispose()


@pytest.mark.asyncio
async def test_worker_does_no_work_against_an_old_schema(monkeypatch):
    eng = await _engine_with("008")
    calls = []

    async def no(*a, **k):
        calls.append(a)
        return {"reminder_candidates": [], "messages_attempted": 0}

    monkeypatch.setattr(worker, "engine", eng)
    monkeypatch.setattr(worker, "run_once", no)
    monkeypatch.setattr(worker.admin_ops, "worker_heartbeat", no)
    monkeypatch.setattr(worker.admin_ops, "maybe_scheduled_backup", no)
    monkeypatch.setattr(worker.settings, "WORKER_INTERVAL_SECONDS", 0.05)
    task = asyncio.create_task(worker.run_forever())
    await asyncio.sleep(0.3)                           # Several passes
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    try:
        assert calls == []                             # No sends, reminders, heartbeat or backups
        assert await _tables(eng) == ["alembic_version", "marker"]
    finally:
        await eng.dispose()


def test_worker_once_refuses_an_old_schema(monkeypatch):
    async def old_state():
        return {"ok": False, "reason": "database is at revision 008 but this code requires 010"}
    ran = []
    monkeypatch.setattr(worker, "_schema_state", old_state)
    monkeypatch.setattr(worker, "run_once", lambda *a, **k: ran.append(1))
    monkeypatch.setattr(sys, "argv", ["worker", "--once"])
    with pytest.raises(SystemExit) as exc:
        worker.main()
    assert exc.value.code == 3 and ran == []


@pytest.mark.asyncio
async def test_api_refuses_to_start_with_the_migration_flag_in_its_environment(monkeypatch):
    import app.core.database as database
    import app.main as main
    from sqlalchemy.ext.asyncio import async_sessionmaker
    eng = await _engine_with(HEAD)
    monkeypatch.setattr(database, "AsyncSessionLocal", async_sessionmaker(eng))
    monkeypatch.setattr(main, "engine", eng)
    monkeypatch.setenv("ALLOW_DB_MIGRATIONS", "1")
    try:
        with pytest.raises(RuntimeError, match="ALLOW_DB_MIGRATIONS is set"):
            async with main.lifespan(main.app):
                pass
    finally:
        await eng.dispose()
