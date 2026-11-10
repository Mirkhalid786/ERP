"""
Database schema version guard for the application processes (API server and worker).

The application only operates when the database's Alembic revision equals the newest
migration in the code ("code head"). Otherwise the API refuses to start and the worker
skips its work, so new code never runs against an old schema (or old code against a
newer one). Migrations themselves are applied only by an operator — see the guard in
alembic/env.py and docs/RECOVERY_DEPLOYMENT_PLAN.md.

The check is read-only: it looks for the alembic_version table and SELECTs from it.
It never creates tables, stamps versions or writes anything.

A database with no alembic_version table at all (e.g. a test or staging schema built
with create_all) is "unmanaged". It is refused too, unless ALLOW_UNMANAGED_SCHEMA=1 is
set; that flag has no effect on a database that does have an alembic_version table, so
it cannot be used to run against a live database on the wrong revision.
"""
import logging
import os
from functools import lru_cache
from typing import Optional

from sqlalchemy import inspect, text

log = logging.getLogger(__name__)

UNMANAGED_ENV = "ALLOW_UNMANAGED_SCHEMA"
BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@lru_cache(maxsize=1)
def code_head() -> str:
    """The newest migration revision shipped with this code (from the migration files; no DB access)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config(os.path.join(BACKEND_ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND_ROOT, "alembic"))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"Expected exactly one migration head in the code, found {heads}")
    return heads[0]


async def database_revision(conn) -> tuple:
    """(managed, revision). Read-only: an inspector check plus one SELECT."""
    managed = await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table("alembic_version"))
    if not managed:
        return False, None
    rows = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalars().all()
    if len(rows) != 1:
        return True, f"<{len(rows)} rows>"
    return True, rows[0]


async def check_schema(engine) -> dict:
    """Compare the database revision with the code head. Never writes; always rolls back."""
    head = code_head()
    async with engine.connect() as conn:
        try:
            managed, revision = await database_revision(conn)
        finally:
            await conn.rollback()
    reason: Optional[str] = None
    if not managed:
        ok = os.environ.get(UNMANAGED_ENV) == "1"
        if not ok:
            reason = (f"the database has no alembic_version table (unmanaged schema); code expects revision {head}. "
                      f"Initialise it with the controlled migration command, or set {UNMANAGED_ENV}=1 for a "
                      "test/staging schema built from the models")
    else:
        ok = revision == head
        if not ok:
            reason = (f"database is at revision {revision} but this code requires {head}. "
                      "Back up, then apply migrations with the controlled command (ALLOW_DB_MIGRATIONS=1)")
    return {"ok": ok, "managed": managed, "database_revision": revision, "code_head": head, "reason": reason}
