"""
MySQL 8.4 integration fixtures (row locks, READ COMMITTED, unique-index races).

Runs only when TEST_MYSQL_URL points at a DISPOSABLE database, e.g.
    TEST_MYSQL_URL=mysql+aiomysql://pharmacy_app:<password>@127.0.0.1:3306/pharmacy_erp_test
Every test drops and recreates all tables in that database. Never point it at real data.
"""
import os
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.database import Base, engine_options, get_db, get_session_factory
from app.core.rate_limit import login_rate_limiter
from app.main import app

MYSQL_URL = os.environ.get("TEST_MYSQL_URL", "")

pytestmark = pytest.mark.skipif(not MYSQL_URL, reason="TEST_MYSQL_URL not set")


@pytest_asyncio.fixture(scope="function")
async def mysql_engine():
    if not MYSQL_URL:
        pytest.skip("TEST_MYSQL_URL not set")
    if not MYSQL_URL.rstrip("/").endswith("_test"):
        pytest.fail("TEST_MYSQL_URL must name a database ending in '_test'")
    engine = create_async_engine(MYSQL_URL, pool_size=10, max_overflow=10, **engine_options(MYSQL_URL))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def mysql_client(mysql_engine) -> AsyncGenerator[AsyncClient, None]:
    factory = async_sessionmaker(bind=mysql_engine, class_=AsyncSession, expire_on_commit=False,
                                 autocommit=False, autoflush=False)

    async def override_get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_session_factory] = lambda: factory
    login_rate_limiter.reset()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        async def add_csrf_header(request):
            if request.method not in ("GET", "HEAD", "OPTIONS") and "x-csrf-token" not in request.headers:
                token = ac.cookies.get(settings.CSRF_COOKIE_NAME)
                if token:
                    request.headers["X-CSRF-Token"] = token
        ac.event_hooks["request"].append(add_csrf_header)
        ac.session_factory = factory
        yield ac
    app.dependency_overrides.clear()
