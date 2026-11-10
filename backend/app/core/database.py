"""
Database engine and session factory.
Uses SQLAlchemy 2.x async engine for FastAPI request handlers
and a sync engine for Alembic migrations.
"""
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from datetime import datetime, timezone

from sqlalchemy import DateTime, MetaData, event
from sqlalchemy.orm.attributes import set_committed_value

from app.core.config import settings

# ── Naming conventions for Alembic constraint auto-naming ─────────────────────
# This ensures generated migration names are deterministic across databases.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    """Shared base class for all ORM models."""
    metadata = metadata


@event.listens_for(Base, "load", propagate=True)
@event.listens_for(Base, "refresh", propagate=True)
def _mark_loaded_datetimes_utc(target, context, attrs=None):
    """
    Timestamps are stored in UTC, but MySQL DATETIME returns them without a zone.
    Mark them as UTC on load so the API serialises "...+00:00" and browsers show
    local time correctly (instead of treating UTC wall time as local).
    """
    for prop in target.__mapper__.column_attrs:
        column = prop.columns[0]
        if isinstance(column.type, DateTime) and column.type.timezone:
            value = target.__dict__.get(prop.key)
            if isinstance(value, datetime) and value.tzinfo is None:
                set_committed_value(target, prop.key, value.replace(tzinfo=timezone.utc))


# ── Async engine (used by FastAPI request handlers) ────────────────────────────
def engine_options(url: str) -> dict:
    """
    MySQL runs at READ COMMITTED: after a request waits on a row lock (stock batch,
    customer account, number sequence), its following reads see the data committed
    by the request it waited for. Under the default REPEATABLE READ they would read
    an older snapshot, e.g. a customer balance without a sale that just committed.
    """
    return {"isolation_level": "READ COMMITTED"} if url.startswith("mysql") else {}


engine = create_async_engine(
    settings.DATABASE_URL,
    **engine_options(settings.DATABASE_URL),
    echo=settings.is_development,
    pool_pre_ping=True,          # Reconnect after DB restart
    pool_recycle=3600,           # Recycle connections after 1 hour
    pool_size=5,
    max_overflow=10,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncSession:
    """
    FastAPI dependency: provides a database session per request.
    Session is committed or rolled back, then closed when the request ends.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


def get_session_factory():
    """
    FastAPI dependency: a session factory for work that must commit in its own
    transactions (e.g. sending a WhatsApp message after the request's data is saved).
    Tests override it to point at the test database.
    """
    return AsyncSessionLocal
