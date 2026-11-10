"""
Phase 4 POS tests: sale finalisation, payments, FEFO, stock safety, GST/rounding,
prescriptions, idempotency, rollback, printing and authorisation.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.auth import AuditLog
from app.models.inventory import MedicineBatch, StockMovement
from app.models.sales import CustomerLedgerEntry, CustomerPayment, SalesInvoice
from tests.sales_helpers import (
    D, add_user, batch_stock, create_customer, key, line, login_as, receive_stock, sales_setup, sell,
)


def session(test_engine):
    return async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)


async def create_medicine(client: AsyncClient, data: dict, name: str, schedule: str = "NONE",
                          requires_rx: bool = False, barcode=None) -> dict:
    resp = await client.post("/api/v1/medicines", json={
        "name": name, "generic_name": "Test Molecule", "dosage_form": "Tablet",
        "base_unit_id": data["tablet_unit"]["id"], "tax_rate_id": data["gst_12"]["id"],
        "schedule_type": schedule, "requires_prescription": requires_rx, "barcode": barcode, "is_active": True,
    })
    assert resp.status_code == 201, resp.text
    med = resp.json()
    conv = await client.post(f"/api/v1/medicines/{med['id']}/conversions", json={
        "packaging_unit_id": data["strip_unit"]["id"], "conversion_factor": "15.0000",
        "is_sales_unit": True, "is_purchase_unit": True,
    })
    assert conv.status_code == 201, conv.text
    return med


# ── 1, 4: cash sale, stock deduction, ledger link, change ──────────────────────

@pytest.mark.asyncio
async def test_cash_sale_deducts_batch_and_links_stock_movement(client: AsyncClient):
    data = await sales_setup(client)                      # 150 tablets @ ₹2.80 (₹42 / strip)
    batch_id = data["batch"]["id"]

    inv = await sell(client, [line(data, "2")], [{"method": "CASH", "tendered": "100"}])

    assert inv["status"] == "FINALIZED" and inv["payment_status"] == "PAID"
    assert inv["invoice_number"].startswith("INV/")
    assert D(inv["grand_total"]) == D("84.00")
    assert D(inv["taxable_total"]) == D("75.00") and D(inv["tax_total"]) == D("9.00")
    assert D(inv["cgst_total"]) == D("4.50") and D(inv["sgst_total"]) == D("4.50")
    assert D(inv["change_due"]) == D("16.00")
    cash = inv["payments"][0]
    assert cash["method"] == "CASH" and D(cash["amount"]) == D("84.00")
    assert D(cash["amount_tendered"]) == D("100.00") and D(cash["change_returned"]) == D("16.00")
    assert D(inv["balance_due"]) == D("0.00")

    assert await batch_stock(client, batch_id) == D("120.00")
    movements = (await client.get(f"/api/v1/inventory/movements?batch_id={batch_id}")).json()
    sale = next(m for m in movements if m["movement_type"] == "SALE")
    assert D(sale["quantity_base"]) == D("-30.00") and D(sale["balance_after_base"]) == D("120.00")
    assert sale["reference_id"] == inv["invoice_number"]
    assert D(sale["total_cost"]) == D("60.00")           # 30 tablets × ₹2.00 cost


@pytest.mark.asyncio
async def test_sale_in_base_units_uses_converted_rate(client: AsyncClient):
    data = await sales_setup(client)
    inv = await sell(client, [line(data, "5", unit="tablet")], [{"method": "CASH", "amount": "14.00"}])
    item = inv["items"][0]
    assert D(item["selling_rate"]) == D("2.80") and D(item["mrp"]) == D("3.00")
    assert D(inv["grand_total"]) == D("14.00")


# ── 2, 3: UPI / card / split ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upi_and_card_payments_are_recorded_not_verified(client: AsyncClient):
    data = await sales_setup(client)
    upi = await sell(client, [line(data, "1")], [{"method": "UPI", "amount": "42.00", "reference": "UPI-7781"}])
    assert upi["payments"][0]["verification_status"] == "RECORDED"
    assert upi["payments"][0]["reference"] == "UPI-7781"
    card = await sell(client, [line(data, "1")], [{"method": "CARD", "amount": "42.00", "reference": "AUTH-55"}])
    assert card["payments"][0]["method"] == "CARD" and card["payment_status"] == "PAID"

    verified = await client.post(f"/api/v1/sales/payments/{upi['payments'][0]['id']}/verify",
                                 json={"reason": "Matched in bank app"})
    assert verified.status_code == 200 and verified.json()["verification_status"] == "VERIFIED"

    # Cash can't carry a non-cash "tendered"; UPI can't have tendered
    bad = await client.post("/api/v1/sales/invoices", json={
        "idempotency_key": key(), "items": [line(data, "1")],
        "payments": [{"method": "UPI", "amount": "42.00", "tendered": "50"}]})
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_split_payment_cash_change_only_on_cash(client: AsyncClient):
    data = await sales_setup(client)
    inv = await sell(client, [line(data, "2")], [
        {"method": "UPI", "amount": "50.00", "reference": "UPI-1"},
        {"method": "CASH", "tendered": "50.00"},            # ₹34 applied, ₹16 change
    ])
    by_method = {p["method"]: p for p in inv["payments"]}
    assert D(by_method["UPI"]["amount"]) == D("50.00")
    assert D(by_method["CASH"]["amount"]) == D("34.00") and D(by_method["CASH"]["change_returned"]) == D("16.00")
    assert D(inv["amount_paid"]) == D("84.00") and inv["payment_status"] == "PAID"

    over = await client.post("/api/v1/sales/invoices", json={
        "idempotency_key": key(), "items": [line(data, "1")],
        "payments": [{"method": "UPI", "amount": "50.00"}]})
    assert over.status_code == 422 and "exceed" in over.json()["detail"]


# ── 13: discounts, GST split and rounding ──────────────────────────────────────

@pytest.mark.asyncio
async def test_discount_gst_and_round_off(client: AsyncClient):
    data = await sales_setup(client)
    quote = await client.post("/api/v1/pos/quote", json={"items": [line(data, "1", discount_percent="10")]})
    assert quote.status_code == 200, quote.text
    inv = await sell(client, [line(data, "1", discount_percent="10")], [{"method": "CASH", "tendered": "50"}])
    item = inv["items"][0]
    # ₹42.00 - 10% = ₹37.80 incl. 12% GST → taxable 33.75, GST 4.05 (CGST 2.03 + SGST 2.02)
    assert D(item["discount_amount"]) == D("4.20") and D(item["line_total"]) == D("37.80")
    assert D(item["taxable_amount"]) == D("33.75")
    assert D(item["cgst_amount"]) + D(item["sgst_amount"]) == D("4.05")
    assert D(inv["round_off"]) == D("0.20") and D(inv["grand_total"]) == D("38.00")
    assert D(quote.json()["grand_total"]) == D(inv["grand_total"])   # quote == final bill

    too_much = await client.post("/api/v1/sales/invoices", json={
        "idempotency_key": key(), "items": [line(data, "1", discount_percent="25")],
        "payments": [{"method": "CASH", "tendered": "50"}]})
    assert too_much.status_code == 422 and "maximum" in too_much.json()["detail"]


# ── 10, 11: FEFO, expired / quarantined / insufficient stock ───────────────────

@pytest.mark.asyncio
async def test_fefo_allocates_earliest_expiry_first_across_batches(client: AsyncClient):
    data = await sales_setup(client, stock_strips="5.00")               # SALE-B1: 75 tabs, expiry +365
    early = await receive_stock(client, data, "EARLY-B", strips="2.00", expiry_days=100)   # 30 tabs

    inv = await sell(client, [line(data, "3")], [{"method": "CASH", "tendered": "200"}])   # 45 tablets
    allocations = {i["batch_number"]: D(i["quantity_base"]) for i in inv["items"]}
    assert allocations == {"EARLY-B": D("30.00"), "SALE-B1": D("15.00")}
    assert await batch_stock(client, early["id"]) == D("0.00")
    assert await batch_stock(client, data["batch"]["id"]) == D("60.00")


@pytest.mark.asyncio
async def test_expired_quarantined_and_insufficient_stock_are_rejected(client: AsyncClient, test_engine):
    data = await sales_setup(client, stock_strips="2.00")               # 30 tablets
    short = await client.post("/api/v1/sales/invoices", json={
        "idempotency_key": key(), "items": [line(data, "3")], "payments": [{"method": "CASH", "tendered": "500"}]})
    assert short.status_code == 422 and "Insufficient saleable stock" in short.json()["detail"]

    status = await client.patch(f"/api/v1/batches/{data['batch']['id']}/stock-status",
                                json={"stock_status": "QUARANTINED", "reason": "Supplier recall notice"})
    assert status.status_code == 200
    blocked = await client.post("/api/v1/sales/invoices", json={
        "idempotency_key": key(), "items": [line(data, "1")], "payments": [{"method": "CASH", "tendered": "50"}]})
    assert blocked.status_code == 422
    pinned = await client.post("/api/v1/sales/invoices", json={
        "idempotency_key": key(), "items": [line(data, "1", batch_id=data["batch"]["id"])],
        "payments": [{"method": "CASH", "tendered": "50"}]})
    assert pinned.status_code == 422 and "not saleable" in pinned.json()["detail"]

    await client.patch(f"/api/v1/batches/{data['batch']['id']}/stock-status",
                       json={"stock_status": "ACTIVE", "reason": "Released after check"})
    async with session(test_engine)() as db:
        await db.execute(update(MedicineBatch).where(MedicineBatch.id == data["batch"]["id"])
                         .values(expiry_date=date.today() - timedelta(days=1)))
        await db.commit()
    expired = await client.post("/api/v1/sales/invoices", json={
        "idempotency_key": key(), "items": [line(data, "1")], "payments": [{"method": "CASH", "tendered": "50"}]})
    assert expired.status_code == 422
    search = (await client.get("/api/v1/pos/search?q=dolo")).json()
    assert search[0]["batches"] == []                                    # expired stock is not offered
    assert await batch_stock(client, data["batch"]["id"]) == D("30.00")


@pytest.mark.asyncio
async def test_indivisible_units_and_search_by_barcode(client: AsyncClient):
    data = await sales_setup(client)
    half = await client.post("/api/v1/pos/quote", json={"items": [line(data, "1.5", unit="tablet")]})
    assert half.status_code == 422 and "cannot be split" in half.json()["detail"]

    med = await create_medicine(client, data, "Crocin Advance", barcode="8901234567890")
    await receive_stock(client, data, "CRO-1", medicine=med)
    found = (await client.get("/api/v1/pos/search?q=8901234567890")).json()
    assert found[0]["medicine_id"] == med["id"] and found[0]["matched_barcode"] is True
    assert len(found[0]["batches"]) == 1 and {u["unit_name"] for u in found[0]["units"]} == {"Tablet", "Strip"}


# ── 9: idempotent finalisation ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_retried_finalisation_returns_original_sale(client: AsyncClient):
    data = await sales_setup(client)
    k = key()
    first = await sell(client, [line(data, "2")], [{"method": "CASH", "tendered": "100"}], idempotency_key=k)
    retry = await sell(client, [line(data, "2")], [{"method": "CASH", "tendered": "100"}], idempotency_key=k, expect=200)
    assert retry["replayed"] is True and retry["id"] == first["id"]
    assert await batch_stock(client, data["batch"]["id"]) == D("120.00")      # deducted once

    changed = await client.post("/api/v1/sales/invoices", json={
        "idempotency_key": k, "items": [line(data, "3")], "payments": [{"method": "CASH", "tendered": "200"}]})
    assert changed.status_code == 409


# ── 27: failure safety ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_failure_during_finalisation_leaves_no_partial_state(client: AsyncClient, test_engine, monkeypatch):
    data = await sales_setup(client)
    customer = await create_customer(client)
    import app.services.sales as sales_service

    original = sales_service.post_ledger

    def failing_ledger(db, **kwargs):
        if kwargs["entry_type"] == "PAYMENT":
            raise RuntimeError("simulated crash after stock deduction")
        return original(db, **kwargs)

    monkeypatch.setattr(sales_service, "post_ledger", failing_ledger)
    with pytest.raises(RuntimeError):
        await client.post("/api/v1/sales/invoices", json={
            "idempotency_key": key(), "customer_id": customer["id"], "items": [line(data, "2")],
            "payments": [{"method": "CASH", "tendered": "100"}]})
    monkeypatch.setattr(sales_service, "post_ledger", original)

    async with session(test_engine)() as db:
        assert (await db.execute(select(func.count(SalesInvoice.id)))).scalar_one() == 0
        assert (await db.execute(select(func.count(CustomerPayment.id)))).scalar_one() == 0
        assert (await db.execute(select(func.count(CustomerLedgerEntry.id)))).scalar_one() == 0
        assert (await db.execute(select(func.count(StockMovement.id))
                                 .where(StockMovement.movement_type == "SALE"))).scalar_one() == 0
    assert await batch_stock(client, data["batch"]["id"]) == D("150.00")
    # The failed attempt did not consume an invoice number
    ok = await sell(client, [line(data, "1")], [{"method": "CASH", "tendered": "50"}])
    assert ok["invoice_number"].endswith("/00001")


# ── 14: prescription controls ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_prescription_medicines_need_details_and_pharmacist(client: AsyncClient):
    data = await sales_setup(client)
    rx_med = await create_medicine(client, data, "Azithral 500", schedule="H")
    await receive_stock(client, data, "AZI-1", medicine=rx_med)
    h1_med = await create_medicine(client, data, "Alprax 0.5", schedule="H1")
    await receive_stock(client, data, "ALP-1", medicine=h1_med)
    await add_user(client, "Cashier", "cashier1")

    items = [line(data, "1", medicine_id=rx_med["id"])]
    missing = await client.post("/api/v1/sales/invoices", json={
        "idempotency_key": key(), "items": items, "payments": [{"method": "CASH", "tendered": "50"}]})
    assert missing.status_code == 422 and "Prescription details are required" in missing.json()["detail"]

    rx = {"prescription_reference": "RX-2026-118", "prescriber_name": "Dr. Mehta", "patient_name": "Sunil Rao"}
    inv = await sell(client, items, [{"method": "CASH", "tendered": "50"}], prescription=rx)
    assert inv["prescription_required"] is True and inv["has_prescription"] is True

    h1 = await client.post("/api/v1/sales/invoices", json={
        "idempotency_key": key(), "items": [line(data, "1", medicine_id=h1_med["id"])],
        "payments": [{"method": "CASH", "tendered": "50"}], "prescription": rx})
    assert h1.status_code == 422 and "address" in h1.json()["detail"]

    details = await client.get(f"/api/v1/sales/invoices/{inv['id']}/prescription")
    assert details.status_code == 200 and details.json()["patient_name"] == "Sunil Rao"

    await login_as(client, "cashier1", "Staff1234")
    cashier_sale = await client.post("/api/v1/sales/invoices", json={
        "idempotency_key": key(), "items": items, "payments": [{"method": "CASH", "tendered": "50"}],
        "prescription": rx})
    assert cashier_sale.status_code == 403
    assert (await client.get(f"/api/v1/sales/invoices/{inv['id']}/prescription")).status_code == 403
    # The invoice itself shows only that a prescription exists, not its details
    assert "patient_name" not in (await client.get(f"/api/v1/sales/invoices/{inv['id']}")).text


# ── 19: printing / PDF ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reprint_labels_and_pdf_never_touch_stock(client: AsyncClient):
    data = await sales_setup(client)
    inv = await sell(client, [line(data, "2")], [{"method": "CASH", "tendered": "100"}])
    first = (await client.post(f"/api/v1/sales/invoices/{inv['id']}/print?layout=thermal")).json()
    again = (await client.post(f"/api/v1/sales/invoices/{inv['id']}/print?layout=a4")).json()
    assert first["copy_label"] == "ORIGINAL" and again["copy_label"] == "DUPLICATE"
    pdf = await client.get(f"/api/v1/sales/invoices/{inv['id']}/pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert pdf.headers["content-type"] == "application/pdf"
    assert await batch_stock(client, data["batch"]["id"]) == D("120.00")
    assert (await client.get("/api/v1/sales/invoices")).json()["total"] == 1


# ── 25: authorisation ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cashier_permissions_are_enforced_by_the_backend(client: AsyncClient):
    data = await sales_setup(client)
    customer = await create_customer(client)
    inv = await sell(client, [line(data, "1")], [{"method": "CASH", "tendered": "50"}])
    await add_user(client, "Cashier", "cashier2")
    await login_as(client, "cashier2", "Staff1234")

    ok = await sell(client, [line(data, "1")], [{"method": "CASH", "tendered": "50"}])   # plain sale is allowed
    assert ok["status"] == "FINALIZED"
    discount = await client.post("/api/v1/sales/invoices", json={
        "idempotency_key": key(), "items": [line(data, "1", discount_percent="5")],
        "payments": [{"method": "CASH", "tendered": "50"}]})
    assert discount.status_code == 403
    credit = await client.post("/api/v1/sales/invoices", json={
        "idempotency_key": key(), "customer_id": customer["id"], "items": [line(data, "1")], "payments": []})
    assert credit.status_code == 403
    assert (await client.post(f"/api/v1/sales/invoices/{inv['id']}/void",
                              json={"reason": "Testing void"})).status_code == 403
    assert (await client.get("/api/v1/reports/sales/summary")).status_code == 403
    assert (await client.get("/api/v1/messaging/config")).status_code == 403
    assert (await client.post(f"/api/v1/customers/{customer['id']}/adjustments", json={
        "invoice_id": inv["id"], "adjustment_type": "WRITE_OFF", "amount": "1", "reason": "Not allowed"})).status_code == 403


@pytest.mark.asyncio
async def test_sale_writes_audit_log_without_prescription_details(client: AsyncClient, test_engine):
    data = await sales_setup(client)
    inv = await sell(client, [line(data, "1")], [{"method": "CASH", "tendered": "50"}])
    async with session(test_engine)() as db:
        log = (await db.execute(select(AuditLog).where(AuditLog.action == "sale.finalize"))).scalar_one()
    assert inv["invoice_number"] in log.metadata_json
