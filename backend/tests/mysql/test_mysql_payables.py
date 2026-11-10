"""Supplier invoices, payments and expenses under real MySQL concurrency (row locks, unique keys, CHECKs)."""
import asyncio
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core.clock import today_local
from app.models.payables import SupplierPayment, SupplierPaymentAllocation
from app.models.purchasing import SupplierInvoice
from tests.mysql.conftest import pytestmark  # noqa: F401  (skip without TEST_MYSQL_URL)
from tests.sales_helpers import key
from tests.test_payables import bill, finalized_bill, new_bill, payment
from tests.test_purchasing import setup_pharmacy_and_catalogue
from tests.test_purchasing_fixes import grn_line, receive_and_post


@pytest.mark.asyncio
async def test_concurrent_payments_never_over_allocate_an_invoice(mysql_client: AsyncClient):
    data = await setup_pharmacy_and_catalogue(mysql_client)
    inv = await finalized_bill(mysql_client, data)                    # ₹300
    bodies = [payment(data, "200.00", allocations=[{"invoice_id": inv["id"], "amount": "200.00"}]) for _ in range(4)]
    results = await asyncio.gather(*[mysql_client.post("/api/v1/supplier-payments", json=b) for b in bodies])
    assert sorted(r.status_code for r in results) == [201, 422, 422, 422], [r.text for r in results]
    async with mysql_client.session_factory() as db:
        allocated = (await db.execute(select(func.sum(SupplierPaymentAllocation.amount)).where(
            SupplierPaymentAllocation.invoice_id == inv["id"], SupplierPaymentAllocation.status == "ACTIVE"))).scalar_one()
        cached = (await db.execute(select(SupplierInvoice.paid_amount).where(SupplierInvoice.id == inv["id"]))).scalar_one()
    assert allocated == Decimal("200.00") and cached == Decimal("200.00")
    rec = (await mysql_client.get("/api/v1/supplier-payments/reconciliation")).json()
    assert rec["ok"], rec["problems"]


@pytest.mark.asyncio
async def test_same_payment_submitted_concurrently_is_recorded_once(mysql_client: AsyncClient):
    data = await setup_pharmacy_and_catalogue(mysql_client)
    inv = await finalized_bill(mysql_client, data)
    body = payment(data, "100.00", allocations=[{"invoice_id": inv["id"], "amount": "100.00"}])
    results = await asyncio.gather(*[mysql_client.post("/api/v1/supplier-payments", json=body) for _ in range(4)])
    codes = sorted(r.status_code for r in results)
    assert codes.count(201) == 1 and set(codes) <= {200, 201, 409}, [r.text for r in results]
    same_ref = [payment(data, "10.00", reference="UTR-RACE-1") for _ in range(3)]
    ref_results = await asyncio.gather(*[mysql_client.post("/api/v1/supplier-payments", json=b) for b in same_ref])
    assert sorted(r.status_code for r in ref_results) == [201, 409, 409], [r.text for r in ref_results]
    async with mysql_client.session_factory() as db:
        n = (await db.execute(select(func.count(SupplierPayment.id)))).scalar_one()
        paid = (await db.execute(select(SupplierInvoice.paid_amount).where(SupplierInvoice.id == inv["id"]))).scalar_one()
    assert n == 2 and paid == Decimal("100.00")


@pytest.mark.asyncio
async def test_same_supplier_bill_saved_concurrently_is_recorded_once(mysql_client: AsyncClient):
    data = await setup_pharmacy_and_catalogue(mysql_client)
    variants = ["RACE/77", "race-77", "RACE 77", "RACE77"]
    results = await asyncio.gather(*[mysql_client.post("/api/v1/purchases/invoices", json=bill(data, number=v))
                                     for v in variants])
    assert sorted(r.status_code for r in results) == [201, 409, 409, 409], [r.text for r in results]


@pytest.mark.asyncio
async def test_concurrent_finalise_cannot_overbill_a_goods_receipt(mysql_client: AsyncClient):
    data = await setup_pharmacy_and_catalogue(mysql_client)
    grn = await receive_and_post(mysql_client, data["supplier"]["id"],
                                 [grn_line(data["medicine"]["id"], data["strip_unit"]["id"], batch="RACE-B1")])
    bills = [await new_bill(mysql_client, data, number=f"OB-{i}", grn=grn, qty="6.00") for i in range(3)]
    results = await asyncio.gather(*[mysql_client.post(f"/api/v1/purchases/invoices/{b['id']}/finalize",
                                                       json={"acknowledge_warnings": True}) for b in bills])
    assert sorted(r.status_code for r in results) == [200, 422, 422], [r.text for r in results]


@pytest.mark.asyncio
async def test_concurrent_reversals_reverse_once(mysql_client: AsyncClient):
    data = await setup_pharmacy_and_catalogue(mysql_client)
    await finalized_bill(mysql_client, data)
    p = (await mysql_client.post("/api/v1/supplier-payments", json=payment(data, "300.00", auto_allocate=True))).json()
    results = await asyncio.gather(*[mysql_client.post(f"/api/v1/supplier-payments/{p['id']}/reverse",
                                                       json={"reason": "Concurrent reversal test"}) for _ in range(3)])
    assert sorted(r.status_code for r in results) == [200, 409, 409], [r.text for r in results]
    rec = (await mysql_client.get("/api/v1/supplier-payments/reconciliation")).json()
    assert rec["ok"], rec["problems"]


@pytest.mark.asyncio
async def test_database_rejects_invalid_payment_and_expense_rows(mysql_client: AsyncClient):
    data = await setup_pharmacy_and_catalogue(mysql_client)
    cats = {c["code"]: c for c in (await mysql_client.get("/api/v1/expenses/categories")).json()}
    async with mysql_client.session_factory() as db:
        with pytest.raises((IntegrityError, OperationalError)):
            await db.execute(text(
                "INSERT INTO supplier_payments (pharmacy_id, payment_number, supplier_id, payment_date, method, amount, "
                "status, is_legacy, created_by, created_at) VALUES (1, 'X/1', :s, CURDATE(), 'CASH', 0, 'POSTED', 0, 1, NOW())"),
                {"s": data["supplier"]["id"]})
        await db.rollback()
        with pytest.raises((IntegrityError, OperationalError)):
            await db.execute(text(
                "INSERT INTO expenses (pharmacy_id, expense_number, category_id, expense_date, amount, gst_amount, payee, "
                "payment_method, status, created_by, created_at) VALUES (1, 'E/1', :c, CURDATE(), 100, 150, 'x', 'CASH', "
                "'APPROVED', 1, NOW())"), {"c": cats["MISC"]["id"]})
        await db.rollback()
        with pytest.raises((IntegrityError, OperationalError)):
            await db.execute(text(
                "INSERT INTO expenses (pharmacy_id, expense_number, category_id, expense_date, amount, gst_amount, payee, "
                "payment_method, status, created_by, created_at) VALUES (1, 'E/2', :c, CURDATE(), 100, 0, 'x', 'CASH', "
                "'PAID', 1, NOW())"), {"c": cats["MISC"]["id"]})
        await db.rollback()
    idem = {"idempotency_key": key(), "category_id": cats["MISC"]["id"], "expense_date": str(today_local()),
            "amount": "50.00", "payee": "Race payee", "payment_method": "UPI", "reference": "UPI-RACE"}
    results = await asyncio.gather(*[mysql_client.post("/api/v1/expenses", json=idem) for _ in range(3)])
    assert sorted(r.status_code for r in results).count(201) == 1, [r.text for r in results]
