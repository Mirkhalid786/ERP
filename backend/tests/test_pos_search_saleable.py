"""
POS search must be a saleable-stock search, not a catalogue search.

A medicine appears in /api/v1/pos/search only when it is active, not
discontinued, and has at least one saleable batch (active, released,
unexpired, quantity > 0). Quantities aggregate across eligible batches
only. FEFO allocation and oversell protection at finalisation are covered
in test_sales_pos.py; the cases here pin the search contract itself.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.inventory import MedicineBatch
from tests.sales_helpers import D, batch_stock, line, receive_stock, sales_setup, sell


def session(test_engine):
    return async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)


async def search(client: AsyncClient, q: str, **params):
    resp = await client.get("/api/v1/pos/search", params={"q": q, **params})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def create_medicine(client: AsyncClient, data: dict, name: str, **extra) -> dict:
    resp = await client.post("/api/v1/medicines", json={
        "name": name, "generic_name": extra.pop("generic_name", "Test Molecule"),
        "dosage_form": "Tablet",
        "base_unit_id": data["tablet_unit"]["id"], "tax_rate_id": data["gst_12"]["id"],
        "schedule_type": "NONE", "is_active": True, **extra,
    })
    assert resp.status_code == 201, resp.text
    med = resp.json()
    conv = await client.post(f"/api/v1/medicines/{med['id']}/conversions", json={
        "packaging_unit_id": data["strip_unit"]["id"], "conversion_factor": "15.0000",
        "is_sales_unit": True, "is_purchase_unit": True,
    })
    assert conv.status_code == 201, conv.text
    return med


async def set_batch(test_engine, batch_id: int, **values):
    async with session(test_engine)() as db:
        await db.execute(update(MedicineBatch).where(MedicineBatch.id == batch_id).values(**values))
        await db.commit()


# ── 1: a catalogue medicine with no batches at all is excluded ────────────────

@pytest.mark.asyncio
async def test_medicine_without_batches_is_excluded(client: AsyncClient):
    data = await sales_setup(client)                                     # Dolo, 150 tabs in stock
    await create_medicine(client, data, "Zentox 500 Tablet")
    assert await search(client, "zentox") == []
    found = await search(client, "dolo")
    assert [m["medicine_id"] for m in found] == [data["medicine"]["id"]]


# ── 2: a batch whose remaining quantity is zero does not count ────────────────

@pytest.mark.asyncio
async def test_zero_quantity_batch_is_excluded(client: AsyncClient, test_engine):
    data = await sales_setup(client)
    await set_batch(test_engine, data["batch"]["id"], current_stock_base=Decimal("0"))
    assert await search(client, "dolo") == []


# ── 3: a medicine whose only batches are expired is excluded ─────────────────

@pytest.mark.asyncio
async def test_only_expired_batches_are_excluded(client: AsyncClient, test_engine):
    data = await sales_setup(client)
    await set_batch(test_engine, data["batch"]["id"], expiry_date=date.today() - timedelta(days=1))
    assert await search(client, "dolo") == []


# ── 4 + 5: expired batches ignored; valid batches aggregate into one row ─────

@pytest.mark.asyncio
async def test_expired_batches_ignored_and_valid_batches_aggregated(client: AsyncClient, test_engine):
    data = await sales_setup(client, stock_strips="10.00")               # SALE-B1: 150 tabs
    extra = await receive_stock(client, data, "EXTRA-B", strips="2.00")  # +30 tabs
    await set_batch(test_engine, extra["id"], expiry_date=date.today() - timedelta(days=1))

    found = await search(client, "dolo")
    assert len(found) == 1                                               # one medicine row, not one per batch
    assert D(found[0]["total_stock_base"]) == D("150.00")                # expired 30 not counted
    assert [b["batch_number"] for b in found[0]["batches"]] == ["SALE-B1"]


@pytest.mark.asyncio
async def test_multiple_saleable_batches_aggregate_quantity(client: AsyncClient):
    data = await sales_setup(client, stock_strips="10.00")               # 150 tabs
    await receive_stock(client, data, "EXTRA-B", strips="2.00")          # +30 tabs
    found = await search(client, "dolo")
    assert len(found) == 1
    assert D(found[0]["total_stock_base"]) == D("180.00")
    assert len(found[0]["batches"]) == 2


# ── 6: a partially consumed batch shows its actual remaining quantity ────────

@pytest.mark.asyncio
async def test_partially_consumed_stock_shows_remaining(client: AsyncClient):
    data = await sales_setup(client, stock_strips="10.00")               # 150 tabs
    await sell(client, [line(data, "1")], [{"method": "CASH", "tendered": "100"}])   # sells 1 strip = 15
    found = await search(client, "dolo")
    assert len(found) == 1 and D(found[0]["total_stock_base"]) == D("135.00")


# ── 7: discontinued and inactive medicines are excluded even with stock ──────

@pytest.mark.asyncio
async def test_discontinued_and_inactive_medicines_are_excluded(client: AsyncClient):
    data = await sales_setup(client)
    disc = await create_medicine(client, data, "Discmed 100 Tablet")
    await receive_stock(client, data, "DISC-1", medicine=disc)
    resp = await client.put(f"/api/v1/medicines/{disc['id']}", json={"is_discontinued": True})
    assert resp.status_code == 200, resp.text
    assert await search(client, "discmed") == []

    inact = await create_medicine(client, data, "Inactmed 100 Tablet")
    await receive_stock(client, data, "INACT-1", medicine=inact)
    resp = await client.put(f"/api/v1/medicines/{inact['id']}", json={"is_active": False})
    assert resp.status_code == 200, resp.text
    assert await search(client, "inactmed") == []


# ── 8: search still works across name/brand, generic, strength, manufacturer ─

@pytest.mark.asyncio
async def test_search_matches_brand_generic_strength_and_manufacturer(client: AsyncClient):
    data = await sales_setup(client)
    assert len(await search(client, "dolo")) == 1                        # brand / name
    assert len(await search(client, "paracetamol")) == 1                # generic (salt)
    assert len(await search(client, "650")) == 1                         # strength, carried in the name

    mfr = await client.post("/api/v1/manufacturers", json={"name": "Acme Lifesciences"})
    assert mfr.status_code in (200, 201), mfr.text
    med = await create_medicine(client, data, "Acmed 250 Tablet", manufacturer_id=mfr.json()["id"])
    await receive_stock(client, data, "ACME-1", medicine=med)
    found = await search(client, "acme lifesciences")
    assert [m["medicine_id"] for m in found] == [med["id"]]


# ── 9: limit counts saleable medicines only (no catalogue rows consume it) ───

@pytest.mark.asyncio
async def test_limit_applies_after_saleable_filtering(client: AsyncClient):
    data = await sales_setup(client)
    await create_medicine(client, data, "Pagmed Alpha Tablet")           # no stock, sorts first
    beta = await create_medicine(client, data, "Pagmed Beta Tablet")
    await receive_stock(client, data, "PAG-B", medicine=beta)
    gamma = await create_medicine(client, data, "Pagmed Gamma Tablet")
    await receive_stock(client, data, "PAG-G", medicine=gamma)

    page = await search(client, "pagmed", limit=2)
    assert [m["medicine_id"] for m in page] == [beta["id"], gamma["id"]]
    first = await search(client, "pagmed", limit=1)
    assert [m["medicine_id"] for m in first] == [beta["id"]]


# ── 12: selling the last unit removes the medicine from POS search ───────────

@pytest.mark.asyncio
async def test_sold_out_medicine_disappears_from_search(client: AsyncClient):
    data = await sales_setup(client, stock_strips="2.00")                # 30 tabs
    assert len(await search(client, "dolo")) == 1
    await sell(client, [line(data, "2")], [{"method": "CASH", "tendered": "200"}])   # sells all 30
    assert await batch_stock(client, data["batch"]["id"]) == D("0.00")
    assert await search(client, "dolo") == []
