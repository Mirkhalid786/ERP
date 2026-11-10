"""
Integration tests for Medicine Catalogue API routes.
"""
from decimal import Decimal
import pytest
from httpx import AsyncClient

from tests.conftest import setup_pharmacy, login


@pytest.mark.asyncio
async def test_create_medicine_success(client: AsyncClient):
    """Admin can create a valid medicine in catalogue."""
    await setup_pharmacy(client)
    await login(client)

    payload = {
        "name": "Augmentin 625 Duo",
        "brand_name": "Augmentin",
        "generic_name": "Amoxicillin and Potassium Clavulanate",
        "strength": "500mg + 125mg",
        "dosage_form": "Tablet",
        "hsn_code": "3004",
        "schedule_type": "H",
        "requires_prescription": True,
        "base_unit_id": 1,  # Tablet
        "barcode": "8901234567890",
        "reorder_level": 50.0,
        "reorder_quantity": 100.0,
    }
    res = await client.post("/api/v1/medicines", json=payload)
    assert res.status_code == 201
    data = res.json()
    assert data["name"] == "Augmentin 625 Duo"
    assert data["schedule_type"] == "H"
    assert data["requires_prescription"] is True
    assert data["barcode"] == "8901234567890"
    assert data["current_stock_base"] == "0.00"


@pytest.mark.asyncio
async def test_duplicate_barcode_rejected(client: AsyncClient):
    """Attempting to create another medicine with identical barcode must return 409 Conflict."""
    await setup_pharmacy(client)
    await login(client)

    payload1 = {
        "name": "Paracetamol 500",
        "generic_name": "Paracetamol",
        "dosage_form": "Tablet",
        "base_unit_id": 1,
        "barcode": "8909999999999",
    }
    res1 = await client.post("/api/v1/medicines", json=payload1)
    assert res1.status_code == 201

    payload2 = {
        "name": "Dolo 650",
        "generic_name": "Paracetamol",
        "dosage_form": "Tablet",
        "base_unit_id": 1,
        "barcode": "8909999999999",  # Duplicate barcode
    }
    res2 = await client.post("/api/v1/medicines", json=payload2)
    assert res2.status_code == 409
    assert "already assigned" in res2.json()["detail"]


@pytest.mark.asyncio
async def test_barcode_lookup(client: AsyncClient):
    """Verify scanning/looking up by barcode returns the correct medicine."""
    await setup_pharmacy(client)
    await login(client)

    payload = {
        "name": "Cetirizine 10mg",
        "generic_name": "Cetirizine Hydrochloride",
        "dosage_form": "Tablet",
        "base_unit_id": 1,
        "barcode": "8901112223334",
    }
    await client.post("/api/v1/medicines", json=payload)

    res = await client.get("/api/v1/medicines/lookup/barcode/8901112223334")
    assert res.status_code == 200
    assert res.json()["name"] == "Cetirizine 10mg"


@pytest.mark.asyncio
async def test_alternate_barcode_flow(client: AsyncClient):
    """Add and look up an alternate packaging barcode."""
    await setup_pharmacy(client)
    await login(client)

    res_med = await client.post("/api/v1/medicines", json={
        "name": "Azithromycin 500",
        "generic_name": "Azithromycin",
        "dosage_form": "Tablet",
        "base_unit_id": 1,
        "barcode": "PRIMARY-AZI-500",
    })
    med_id = res_med.json()["id"]

    # Add alternate barcode
    alt_res = await client.post(f"/api/v1/medicines/{med_id}/barcodes", json={
        "barcode": "ALT-STRIP-BARCODE",
        "description": "Strip Barcode",
    })
    assert alt_res.status_code == 201
    assert alt_res.json()["barcode"] == "ALT-STRIP-BARCODE"

    # Lookup by alternate barcode
    lookup_res = await client.get("/api/v1/medicines/lookup/barcode/ALT-STRIP-BARCODE")
    assert lookup_res.status_code == 200
    assert lookup_res.json()["name"] == "Azithromycin 500"


@pytest.mark.asyncio
async def test_packaging_conversion_flow(client: AsyncClient):
    """Add packaging unit conversion (1 Strip = 10 Tablets) to a medicine."""
    await setup_pharmacy(client)
    await login(client)

    res_med = await client.post("/api/v1/medicines", json={
        "name": "Metformin 500mg",
        "generic_name": "Metformin Hydrochloride",
        "dosage_form": "Tablet",
        "base_unit_id": 1,  # Tablet
    })
    med_id = res_med.json()["id"]

    # Add conversion: Unit 3 = Strip, conversion_factor = 10
    conv_res = await client.post(f"/api/v1/medicines/{med_id}/conversions", json={
        "packaging_unit_id": 3,  # Strip
        "conversion_factor": 10.0,
        "is_sales_unit": True,
        "is_purchase_unit": True,
    })
    assert conv_res.status_code == 201
    data = conv_res.json()
    assert Decimal(str(data["conversion_factor"])) == Decimal("10.0000")
    assert data["packaging_unit_name"] == "Strip"

    # Reject packaging unit identical to base unit
    same_res = await client.post(f"/api/v1/medicines/{med_id}/conversions", json={
        "packaging_unit_id": 1,  # Base unit = Tablet
        "conversion_factor": 1.0,
    })
    assert same_res.status_code == 422
