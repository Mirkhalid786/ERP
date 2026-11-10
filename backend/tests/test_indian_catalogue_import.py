"""
Tests for Large-Scale Indian Branded Medicine Catalogue Import (JuniorAlive Dataset).

Tests run against a disposable in-memory SQLite database, verifying:
  - Row parsing (brands, salts, strengths, dosage forms, packaging units, MRP, discontinued).
  - Preview generation, field coverage, and duplicate detection.
  - Safe transactional import without physical stock or batches.
  - Multi-word search in PO medicine selector (by brand, salt, strength, dosage form, manufacturer).
  - Repeatable import idempotency.
"""
import io
import pytest
from decimal import Decimal
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.auth import AuditLog
from app.models.inventory import (
    Category, Manufacturer, Medicine, MedicineBatch, MedicineUnitConversion, StockMovement, Unit,
)
from app.services.indian_medicine_import import (
    parse_indian_medicine_row, preview_indian_catalogue, import_indian_catalogue,
)
from tests.conftest import login, setup_pharmacy


async def count(engine, model, *where):
    async with async_sessionmaker(engine)() as s:
        return (await s.execute(select(func.count()).select_from(model).where(*where))).scalar()


def test_parser_sample_records():
    # 1. Augmentin 625 Duo Tablet
    row1 = {
        "id": "1",
        "name": "Augmentin 625 Duo Tablet",
        "price": "223.42",
        "Is_discontinued": "FALSE",
        "manufacturer_name": "Glaxo SmithKline Pharmaceuticals Ltd",
        "type": "allopathy",
        "pack_size_label": "strip of 10 tablets",
        "short_composition1": "Amoxycillin (500mg)",
        "short_composition2": "Clavulanic Acid (125mg)",
        "salt_composition": "Amoxycillin (500mg) + Clavulanic Acid (125mg)",
    }
    p1 = parse_indian_medicine_row(row1)
    assert p1 is not None
    assert p1["name"] == "Augmentin 625 Duo Tablet"
    assert p1["brand_name"] == "Augmentin"
    assert p1["strength"] == "625 Duo"
    assert p1["dosage_form"] == "Tablet"
    assert p1["base_unit_name"] == "Tablet"
    assert p1["pack_unit_name"] == "Strip"
    assert p1["pack_size"] == Decimal("10")
    assert p1["manufacturer_name"] == "Glaxo SmithKline Pharmaceuticals Ltd"
    assert p1["category_name"] == "Antibiotics"
    assert p1["mrp"] == Decimal("223.42")
    assert p1["is_discontinued"] is False
    assert p1["requires_prescription"] is True
    assert p1["source_id"] == "1"

    # 2. Ascoril LS Syrup
    row2 = {
        "id": "3",
        "name": "Ascoril LS Syrup",
        "price": "108.00",
        "Is_discontinued": "FALSE",
        "manufacturer_name": "Glenmark Pharmaceuticals Ltd",
        "type": "allopathy",
        "pack_size_label": "bottle of 100 ml Syrup",
        "short_composition1": "Ambroxol (30mg/5ml)",
        "short_composition2": "Levosalbutamol (1mg/5ml)",
        "salt_composition": "Ambroxol (30mg/5ml) + Levosalbutamol (1mg/5ml) + Guaifenesin (50mg/5ml)",
    }
    p2 = parse_indian_medicine_row(row2)
    assert p2 is not None
    assert p2["dosage_form"] == "Syrup"
    assert p2["base_unit_name"] == "Bottle"
    assert p2["pack_unit_name"] == "Bottle"
    assert p2["pack_size"] == Decimal("100")
    assert p2["category_name"] == "Respiratory & Antiallergic"

    # 3. Discontinued product with pack size variant
    row3 = {
        "id": "713",
        "name": "Allegra 120mg Tablet",
        "price": "150.00",
        "Is_discontinued": "TRUE",
        "manufacturer_name": "Sanofi India Ltd",
        "type": "allopathy",
        "pack_size_label": "strip of 6 tablets",
        "short_composition1": "Fexofenadine (120mg)",
        "short_composition2": "",
        "salt_composition": "",
    }
    p3 = parse_indian_medicine_row(row3)
    assert p3 is not None
    assert p3["is_discontinued"] is True
    assert p3["generic_name"] == "Fexofenadine (120mg)"
    assert p3["pack_size"] == Decimal("6")


def test_preview_first_1000_records():
    prev = preview_indian_catalogue(max_rows=1000)
    assert prev["total_source_records"] == 1001 or prev["total_source_records"] == 1000
    assert prev["valid_records"] >= 995
    assert prev["rejected_records"] == 0
    assert prev["unique_manufacturers"] > 50
    assert prev["conversions_count"] > 900
    assert len(prev["sample_records"]) == 10


@pytest.mark.asyncio
async def test_indian_catalogue_import_isolated(client: AsyncClient, test_engine):
    """Test importing a subset of the Indian medicine dataset into disposable isolated DB."""
    await setup_pharmacy(client)
    await login(client)

    # Preview API
    p_res = await client.get("/api/v1/medicines/import/indian/preview")
    assert p_res.status_code == 200, p_res.text
    p_data = p_res.json()
    assert "total_source_records" in p_data
    assert p_data["valid_records"] > 250000

    # Commit API with limit=100 for fast isolated test
    c_res = await client.post("/api/v1/medicines/import/indian/commit", data={"limit": "100", "batch_size": "50"})
    assert c_res.status_code == 200, c_res.text
    c_data = c_res.json()
    assert c_data["created_medicines"] > 90
    assert c_data["created_manufacturers"] > 20
    assert c_data["created_conversions"] > 80
    assert c_data["errors"] == 0

    # Verify database counts
    med_count = await count(test_engine, Medicine)
    assert med_count == c_data["created_medicines"]
    assert await count(test_engine, MedicineBatch) == 0  # No stock created!
    assert await count(test_engine, StockMovement) == 0  # No movement created!
    assert await count(test_engine, AuditLog, AuditLog.action == "medicine.catalogue_import_bulk_indian") == 1

    # Repeat import: idempotency test (should skip existing duplicates)
    c_res2 = await client.post("/api/v1/medicines/import/indian/commit", data={"limit": "100", "batch_size": "50"})
    assert c_res2.status_code == 200
    c_data2 = c_res2.json()
    assert c_data2["created_medicines"] == 0
    assert c_data2["skipped_duplicates"] >= 90
    assert await count(test_engine, Medicine) == med_count  # Still exactly the same count


@pytest.mark.asyncio
async def test_search_by_brand_salt_mfr_strength(client: AsyncClient, test_engine):
    """Test searching imported Indian medicines by brand, salt, strength, dosage form, and manufacturer."""
    await setup_pharmacy(client)
    await login(client)

    # Import first 50 records
    await client.post("/api/v1/medicines/import/indian/commit", data={"limit": "50", "batch_size": "50"})

    # 1. Search by brand name
    r_brand = await client.get("/api/v1/medicines", params={"search": "Augmentin"})
    assert r_brand.status_code == 200
    hits_brand = r_brand.json()
    assert len(hits_brand) > 0
    assert "Augmentin" in hits_brand[0]["name"]
    assert hits_brand[0]["mrp"] is not None
    assert hits_brand[0]["source_name"] == "junioralive/Indian-Medicine-Dataset"

    # 2. Search by active salt + strength
    r_salt = await client.get("/api/v1/medicines", params={"search": "Amoxycillin 500mg"})
    assert r_salt.status_code == 200
    hits_salt = r_salt.json()
    assert len(hits_salt) > 0
    assert any("Amoxycillin" in m["generic_name"] for m in hits_salt)

    # 3. Search by manufacturer
    r_mfr = await client.get("/api/v1/medicines", params={"search": "Cipla"})
    assert r_mfr.status_code == 200
    hits_mfr = r_mfr.json()
    assert len(hits_mfr) > 0
    assert all("Cipla" in (m["manufacturer_name"] or "") or "Cipla" in m["name"] for m in hits_mfr)

    # 4. Search by dosage form + brand
    r_form = await client.get("/api/v1/medicines", params={"search": "Syrup Ascoril"})
    assert r_form.status_code == 200
    hits_form = r_form.json()
    assert len(hits_form) > 0
    assert "Ascoril" in hits_form[0]["name"]
    assert hits_form[0]["dosage_form"] == "Syrup"
