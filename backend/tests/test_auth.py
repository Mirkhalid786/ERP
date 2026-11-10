"""Tests for authentication: login, logout, session, /me endpoint."""
import pytest
from httpx import AsyncClient

from tests.conftest import setup_pharmacy, login, VALID_SETUP_PAYLOAD


@pytest.mark.asyncio
async def test_login_success(client: AsyncClient):
    """Successful login returns user data and sets session cookie."""
    await setup_pharmacy(client)
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "Admin123"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "admin"
    assert data["name"] == "Admin User"
    assert "Owner / Admin" in data["role"]
    assert "pharmacy_id" in data
    # Session cookie must be set
    assert "pharmacy_session" in response.cookies


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient):
    """Wrong password returns 401 with a generic error message."""
    await setup_pharmacy(client)
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "WrongPassword1"},
    )
    assert response.status_code == 401
    # Error message must not reveal whether user exists
    assert "username or password" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_login_unknown_user(client: AsyncClient):
    """Unknown username returns 401 (not 404) to prevent user enumeration."""
    await setup_pharmacy(client)
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "nonexistent_user", "password": "SomePass1"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_before_setup_returns_503(client: AsyncClient):
    """Login attempt before setup returns 503 Service Unavailable."""
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "Admin123"},
    )
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_me_endpoint_returns_user_profile(client: AsyncClient):
    """Authenticated /me endpoint returns user profile and permissions."""
    await setup_pharmacy(client)
    await login(client)
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "admin"
    assert isinstance(data["permissions"], list)
    assert len(data["permissions"]) > 0
    assert "sales.create" in data["permissions"]
    assert "users.manage" in data["permissions"]


@pytest.mark.asyncio
async def test_me_endpoint_unauthenticated(client: AsyncClient):
    """Unauthenticated /me returns 401."""
    await setup_pharmacy(client)
    # Do not log in
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_logout_clears_session(client: AsyncClient):
    """Logout invalidates the session. Subsequent requests return 401."""
    await setup_pharmacy(client)
    await login(client)

    # Verify logged in
    me_response = await client.get("/api/v1/auth/me")
    assert me_response.status_code == 200

    # Logout
    logout_response = await client.post("/api/v1/auth/logout")
    assert logout_response.status_code == 200
    assert "logged out" in logout_response.json()["message"].lower()

    # Verify session is gone
    me_after = await client.get("/api/v1/auth/me")
    assert me_after.status_code == 401


@pytest.mark.asyncio
async def test_session_cookie_is_httponly(client: AsyncClient):
    """Session cookie must have HttpOnly flag."""
    await setup_pharmacy(client)
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "Admin123"},
    )
    # httpx returns Set-Cookie headers; check cookie attributes
    set_cookie = response.headers.get("set-cookie", "")
    assert "httponly" in set_cookie.lower()


@pytest.mark.asyncio
async def test_admin_owner_has_all_permissions(client: AsyncClient):
    """Owner/Admin role has all defined permissions."""
    await setup_pharmacy(client)
    await login(client)
    response = await client.get("/api/v1/auth/me")
    permissions = response.json()["permissions"]

    # Critical permissions the owner must have
    required = [
        "sales.create", "sales.refund", "purchases.create", "purchases.post",
        "stock.adjust", "medicines.manage", "users.manage", "settings.manage",
        "backups.manage", "audit.view",
    ]
    for perm in required:
        assert perm in permissions, f"Owner/Admin must have permission: {perm}"
