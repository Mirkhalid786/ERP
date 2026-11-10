"""Tests for the first-time pharmacy setup endpoint."""
import pytest
from httpx import AsyncClient

from tests.conftest import VALID_SETUP_PAYLOAD, setup_pharmacy


@pytest.mark.asyncio
async def test_setup_status_initially_required(client: AsyncClient):
    """Setup status returns setup_required=True before any setup."""
    response = await client.get("/api/v1/setup/status")
    assert response.status_code == 200
    assert response.json()["setup_required"] is True


@pytest.mark.asyncio
async def test_setup_status_not_required_after_setup(client: AsyncClient):
    """Setup status returns setup_required=False after successful setup."""
    await setup_pharmacy(client)
    response = await client.get("/api/v1/setup/status")
    assert response.status_code == 200
    assert response.json()["setup_required"] is False


@pytest.mark.asyncio
async def test_first_run_setup_success(client: AsyncClient):
    """First-run setup creates pharmacy, admin user and default data."""
    response = await client.post("/api/v1/setup", json=VALID_SETUP_PAYLOAD)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Test Pharmacy"
    assert data["is_setup_complete"] is True
    assert data["invoice_prefix"] == "INV"
    assert "id" in data


@pytest.mark.asyncio
async def test_setup_returns_pharmacy_fields(client: AsyncClient):
    """Setup returns all expected pharmacy fields."""
    response = await client.post("/api/v1/setup", json=VALID_SETUP_PAYLOAD)
    data = response.json()
    assert data["city"] == "Mumbai"
    assert data["state_code"] == "MH"
    assert data["legal_name"] == "Test Pharmacy Pvt Ltd"


@pytest.mark.asyncio
async def test_setup_cannot_be_run_twice(client: AsyncClient):
    """Second setup call returns 409 Conflict."""
    await client.post("/api/v1/setup", json=VALID_SETUP_PAYLOAD)
    response = await client.post("/api/v1/setup", json=VALID_SETUP_PAYLOAD)
    assert response.status_code == 409
    assert "already configured" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_setup_validates_gstin_format(client: AsyncClient):
    """Invalid GSTIN format is rejected with 422."""
    payload = {**VALID_SETUP_PAYLOAD, "gstin": "INVALID_GSTIN"}
    response = await client.post("/api/v1/setup", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_setup_accepts_valid_gstin(client: AsyncClient):
    """Valid GSTIN format is accepted."""
    payload = {**VALID_SETUP_PAYLOAD, "gstin": "27ABCDE1234F1Z5"}
    response = await client.post("/api/v1/setup", json=payload)
    assert response.status_code == 201
    assert response.json()["gstin"] == "27ABCDE1234F1Z5"


@pytest.mark.asyncio
async def test_setup_requires_strong_password(client: AsyncClient):
    """Weak admin password (no uppercase, no digit) is rejected."""
    payload = {**VALID_SETUP_PAYLOAD, "admin_password": "weakpassword"}
    response = await client.post("/api/v1/setup", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_setup_requires_minimum_pharmacy_name(client: AsyncClient):
    """Pharmacy name shorter than 2 chars is rejected."""
    payload = {**VALID_SETUP_PAYLOAD, "pharmacy_name": "X"}
    response = await client.post("/api/v1/setup", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_setup_username_lowercased(client: AsyncClient):
    """Admin username is normalized to lowercase during setup."""
    payload = {**VALID_SETUP_PAYLOAD, "admin_username": "ADMIN_USER"}
    response = await client.post("/api/v1/setup", json=payload)
    # After setup, login with lowercase should work
    assert response.status_code == 201
    login_response = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin_user", "password": "Admin123"},
    )
    assert login_response.status_code == 200


@pytest.mark.asyncio
async def test_setup_without_optional_fields(client: AsyncClient):
    """Setup succeeds with only required fields provided."""
    minimal_payload = {
        "pharmacy_name": "Simple Pharmacy",
        "admin_name": "Admin",
        "admin_username": "admin",
        "admin_password": "Admin123",
        "invoice_prefix": "INV",
    }
    response = await client.post("/api/v1/setup", json=minimal_payload)
    assert response.status_code == 201
