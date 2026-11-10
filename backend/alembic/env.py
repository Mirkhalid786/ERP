"""
Alembic environment configuration.
Supports async engine for online mode and sync engine for offline mode.
Loads DATABASE_URL_SYNC from environment variables — never from alembic.ini.
"""
import os
import sys
from logging.config import fileConfig

# Add backend root to sys.path so 'app' can be imported reliably
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import engine_from_config, pool
from alembic import context

# Load all models so Alembic autogenerate can detect them
from app.models import *  # noqa: F401, F403
from app.core.database import Base

# ── Alembic config object ──────────────────────────────────────────────────────
config = context.config
fileConfig(config.config_file_name)

# Override the database URL from environment (never from hardcoded ini)
DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")
if not DATABASE_URL_SYNC:
    # Fallback for local development without Docker
    from app.core.config import settings
    DATABASE_URL_SYNC = settings.DATABASE_URL_SYNC

config.set_main_option("sqlalchemy.url", DATABASE_URL_SYNC)

target_metadata = Base.metadata

# ── Migration guard ────────────────────────────────────────────────────────────
# Commands that change the database (upgrade, downgrade, stamp, ensure_version) run
# only when ALLOW_DB_MIGRATIONS=1 is set explicitly for that command. The one exception
# is an `upgrade` when the database is already at the code head: it has nothing to apply.
# The command is identified from the callback Alembic passes to this environment, so the
# guard applies equally to the CLI, the programmatic API (alembic.command.*) and the
# container start command. Unknown commands are treated as changing commands (fail
# closed). Offline mode (--sql) only prints SQL and never connects, so it is not guarded.
ALLOW_ENV = "ALLOW_DB_MIGRATIONS"
READ_ONLY_COMMANDS = {
    "display_version",           # alembic current
    "show_current",              # alembic show current
    "_display_current_history",  # alembic history -i
    "edit_current",              # alembic edit current
    "retrieve_migrations",       # alembic check / revision --autogenerate (compare only)
    "nothing",                   # alembic revision (writes a file, not the database)
}
GUARD_EXIT_CODE = 3


def _running_command() -> str:
    proxy = getattr(context, "_proxy", None)
    fn = getattr(proxy, "context_opts", {}).get("fn") if proxy is not None else None
    return getattr(fn, "__name__", "") or "<unknown>"


def migration_guard(connection) -> None:
    command = _running_command()
    if command in READ_ONLY_COMMANDS:
        return
    from alembic.runtime.migration import MigrationContext
    current = MigrationContext.configure(connection).get_current_revision()   # read-only SELECT
    # End the implicit transaction that SELECT opened. Otherwise Alembic would treat the
    # connection's transaction as externally managed and never commit the migration.
    connection.rollback()
    head = context.script.get_current_head()
    if os.environ.get(ALLOW_ENV) == "1":
        print(f"[migration guard] {ALLOW_ENV}=1: allowing '{command}' (database at {current}, code head {head})",
              file=sys.stderr)
        return
    if command == "upgrade" and current == head:
        return                     # Already at head: nothing will be applied
    print(f"[migration guard] REFUSED '{command}': database at {current}, code head {head}. "
          f"Nothing was changed. Back up the database first, then run this command with {ALLOW_ENV}=1 "
          "(see docs/RECOVERY_DEPLOYMENT_PLAN.md).", file=sys.stderr)
    sys.exit(GUARD_EXIT_CODE)


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode.
    Generates SQL without connecting to the database.
    Useful for reviewing or applying migrations manually.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Run migrations in 'online' mode.
    Connects to the database and applies migrations directly.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        migration_guard(connection)
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
