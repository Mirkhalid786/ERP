"""
Unit and integration tests for Supplier management (Phase 3).
"""
import pytest
from httpx import AsyncClient

from tests.conftest import VALID_SETUP_PAYLOAD


async def setup_pharmacy(client: AsyncClient) -> dict:
    resp = await client.post("/api/v1/setup", json=VALID_SETUP_PAYLOAD)
    assert resp.status_code in (200, 201)
    # Login as admin
    login_resp = await client.post("/api/v1/auth/login", json={
        "username": VALID_SETUP_PAYLOAD["admin_username"],
        "password": VALID_SETUP_PAYLOAD["admin_password"],
    })
    assert login_resp.status_code == 200
    return login_resp.json()


@pytest.mark.asyncio
async def test_supplier_crud_lifecycle(client: AsyncClient):
    await setup_pharmacy(client)

    # 1. Create Supplier
    payload = {
        "name": "Apollo Distributing Corp",
        "legal_name": "Apollo Pharmaceuticals Pvt Ltd",
        "gstin": "27AABCA1234F1Z5",
        "drug_license_no": "MH-TZ1-20B-112233, MH-TZ1-21B-112234",
        "contact_person": "Ramesh Kumar",
        "phone": "9876543210",
        "email": "ramesh@apollo.test",
        "address": "Gala 4, Sector 18, Vashi",
        "city": "Navi Mumbai",
        "state": "Maharashtra",
        "pincode": "400703",
        "payment_terms_days": 21,
        "credit_limit": "250000.00",
        "notes": "Primary distributor for Sun and Cipla",
        "is_active": True,
    }
    create_resp = await client.post("/api/v1/suppliers", json=payload)
    assert create_resp.status_code == 201, create_resp.text
    supplier = create_resp.json()
    assert supplier["id"] > 0
    assert supplier["name"] == "Apollo Distributing Corp"
    assert supplier["state_code"] == "27"  # inferred from GSTIN
    assert supplier["payment_terms_days"] == 21
    supplier_id = supplier["id"]

    # 2. Get Detail
    get_resp = await client.get(f"/api/v1/suppliers/{supplier_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["name"] == "Apollo Distributing Corp"

    # 3. Update Supplier
    update_resp = await client.put(f"/api/v1/suppliers/{supplier_id}", json={
        "phone": "9123456789",
        "payment_terms_days": 30,
        "notes": "Updated terms to 30 days credit",
    })
    assert update_resp.status_code == 200
    updated = update_resp.json()
    assert updated["phone"] == "9123456789"
    assert updated["payment_terms_days"] == 30
    assert updated["notes"] == "Updated terms to 30 days credit"

    # 4. Search Supplier
    search_resp = await client.get("/api/v1/suppliers?search=apollo")
    assert search_resp.status_code == 200
    results = search_resp.json()
    assert len(results) == 1
    assert results[0]["id"] == supplier_id

    # 5. Delete Supplier (unreferenced)
    del_resp = await client.delete(f"/api/v1/suppliers/{supplier_id}")
    assert del_resp.status_code == 204

    # Confirm 404 after deletion
    get_again = await client.get(f"/api/v1/suppliers/{supplier_id}")
    assert get_again.status_code == 404


@pytest.mark.asyncio
async def test_duplicate_supplier_name_rejected(client: AsyncClient):
    await setup_pharmacy(client)

    payload = {
        "name": "MedPlus Wholesale",
        "gstin": "29AABCM5678G1Z9",
    }
    r1 = await client.post("/api/v1/suppliers", json=payload)
    assert r1.status_code == 201

    # Attempt duplicate
    r2 = await client.post("/api/v1/suppliers", json=payload)
    assert r2.status_code == 409
    assert "already exists" in r2.json()["detail"]


@pytest.mark.asyncio
async def test_invalid_gstin_format_rejected(client: AsyncClient):
    await setup_pharmacy(client)

    payload = {
        "name": "Bad Tax Supplier",
        "gstin": "INVALID_GST_123",
    }
    resp = await client.post("/api/v1/suppliers", json=payload)
    assert resp.status_code == 422
    assert "GSTIN must be in the format" in str(resp.json())


@pytest.mark.asyncio
async def test_supplier_permission_enforcement(client: AsyncClient):
    await setup_pharmacy(client)

    # Create Cashier user (who does NOT have suppliers.manage)
    # 1. Get roles
    roles_resp = await client.get("/api/v1/roles")
    roles = roles_resp.json()
    cashier_role = next(r for r in roles if r["name"] == "Cashier")

    user_resp = await client.post("/api/v1/users", json={
        "username": "cashier1",
        "name": "Cashier One",
        "password": "Password123!",
        "role_id": cashier_role["id"],
    })
    assert user_resp.status_code == 201

    # Login as cashier
    await client.post("/api/v1/auth/login", json={
        "username": "cashier1",
        "password": "Password123!",
    })

    # Cashier cannot create supplier
    create_resp = await client.post("/api/v1/suppliers", json={
        "name": "Unauthorized Supplier",
    })
    assert create_resp.status_code == 403
