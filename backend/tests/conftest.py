"""
pytest configuration and shared fixtures.

Test database strategy:
  - Uses an in-memory SQLite database (via aiosqlite) for speed and isolation.
  - Each test function gets a fresh database with all tables created.
  - No MySQL required to run tests — but MySQL integration tests can be
    run separately with a real database URL.
"""
import asyncio
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.core.config import settings
from app.core.database import Base, get_db, get_session_factory
from app.core.rate_limit import login_rate_limiter


# ── Test database setup ────────────────────────────────────────────────────────

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="function")
async def test_engine():
    """Create a fresh in-memory SQLite engine for each test function."""
    engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def test_db(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """Provide a fresh database session for each test function."""
    TestSessionLocal = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    async with TestSessionLocal() as session:
        yield session


@pytest_asyncio.fixture(scope="function")
async def client(test_engine) -> AsyncGenerator[AsyncClient, None]:
    """
    HTTP test client with overridden database dependency.
    Each test gets an isolated database.
    """
    TestSessionLocal = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

    async def override_get_db():
        async with TestSessionLocal() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_session_factory] = lambda: TestSessionLocal
    login_rate_limiter.reset()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as ac:
        async def add_csrf_header(request):
            # Behave like the frontend: echo the CSRF cookie on state-changing requests.
            # Tests that set the header themselves (even to "") are left untouched.
            if request.method not in ("GET", "HEAD", "OPTIONS") and "x-csrf-token" not in request.headers:
                token = ac.cookies.get(settings.CSRF_COOKIE_NAME)
                if token:
                    request.headers["X-CSRF-Token"] = token

        ac.event_hooks["request"].append(add_csrf_header)
        yield ac

    app.dependency_overrides.clear()
    login_rate_limiter.reset()


# ── Shared helpers ─────────────────────────────────────────────────────────────

VALID_SETUP_PAYLOAD = {
    "pharmacy_name": "Test Pharmacy",
    "legal_name": "Test Pharmacy Pvt Ltd",
    "admin_name": "Admin User",
    "admin_username": "admin",
    "admin_password": "Admin123",
    "invoice_prefix": "INV",
    "state_code": "MH",
    "city": "Mumbai",
}


async def setup_pharmacy(client: AsyncClient) -> dict:
    """Run first-time pharmacy setup and return the response JSON."""
    response = await client.post("/api/v1/setup", json=VALID_SETUP_PAYLOAD)
    assert response.status_code == 201, response.text
    return response.json()


async def login(client: AsyncClient, username="admin", password="Admin123") -> dict:
    """Login and return response JSON. Session cookie is stored in the client."""
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest_asyncio.fixture(scope="function")
async def second_client(client) -> AsyncGenerator[AsyncClient, None]:
    """
    Another browser on the same test database (e.g. a cashier signed in while the
    admin changes their account). Shares the `client` fixture's DB overrides.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as other:
        async def add_csrf_header(request):
            if request.method not in ("GET", "HEAD", "OPTIONS") and "x-csrf-token" not in request.headers:
                token = other.cookies.get(settings.CSRF_COOKIE_NAME)
                if token:
                    request.headers["X-CSRF-Token"] = token

        other.event_hooks["request"].append(add_csrf_header)
        yield other
