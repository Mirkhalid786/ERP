"""
Payment documents & history: customer payment receipts, supplier payment
vouchers, their PDFs, and the payment-history search/filters.

The financial flows themselves (allocation, advances, reversal, journals,
reconciliation) are covered by test_sales_credit / test_payables /
test_accounting; here we pin the *documents* built from those records:
numbers, allocation breakdowns, balance-after figures and reprint safety.
"""
from datetime import timedelta

import pytest
from httpx import AsyncClient

from tests.sales_helpers import D, add_user, create_customer, key, line, login_as, receive_stock, sales_setup, sell
from tests.test_payables import TODAY, finalized_bill, pay
from tests.test_purchasing import setup_pharmacy_and_catalogue


async def credit_customer_with_bill(client: AsyncClient):
    """Customer with one ₹1,000 credit bill (10 strips @ ₹100)."""
    data = await sales_setup(client)
    data["rs100"] = await receive_stock(client, data, "RS100", strips="20.00", selling="100.00",
                                        mrp="100.00", rate="70.00", expiry_days=300)
    customer = await create_customer(client)
    inv = await sell(client, [line(data, "10", batch_id=data["rs100"]["id"])], [], customer_id=customer["id"])
    assert D(inv["balance_due"]) == D("1000.00")
    return data, customer, inv


async def collect(client: AsyncClient, customer_id: int, amount: str, **extra):
    resp = await client.post(f"/api/v1/customers/{customer_id}/payments", json={
        "idempotency_key": key(), "method": "CASH", "amount": amount, **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── Customer payment receipt ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_receipt_document_shows_allocations_and_balance_after(client: AsyncClient):
    _, customer, inv = await credit_customer_with_bill(client)
    payment = await collect(client, customer["id"], "300.00",
                            allocations=[{"invoice_id": inv["id"], "amount": "300.00"}])

    doc = (await client.get(f"/api/v1/sales/payments/{payment['id']}/document")).json()
    assert doc["kind"] == "CUSTOMER_PAYMENT_RECEIPT"
    assert doc["receipt_number"] == payment["receipt_number"]
    assert doc["customer"]["name"] == customer["name"]
    assert len(doc["allocations"]) == 1
    alloc = doc["allocations"][0]
    assert alloc["invoice_id"] == inv["id"] and alloc["invoice_number"] == inv["invoice_number"]
    assert D(alloc["amount"]) == D("300.00")
    assert D(doc["allocated_total"]) == D("300.00")
    assert D(doc["balance_after"]) == D("700.00")          # 1,000 bill − 300 paid
    assert D(doc["advance_unapplied"]) == D("0.00")
    assert doc["is_reversed"] is False and doc["received_by"]


@pytest.mark.asyncio
async def test_receipt_document_advance_and_balance_after_overpayment(client: AsyncClient):
    _, customer, inv = await credit_customer_with_bill(client)
    await collect(client, customer["id"], "300.00")
    payment = await collect(client, customer["id"], "900.00", method="UPI", reference="UTR-1")

    doc = (await client.get(f"/api/v1/sales/payments/{payment['id']}/document")).json()
    assert D(doc["allocated_total"]) == D("700.00")        # only what the bill still owed
    assert D(doc["advance_unapplied"]) == D("200.00")      # the rest is a genuine advance
    assert D(doc["balance_after"]) == D("-200.00")
    assert D(doc["advance_after"]) == D("200.00")


@pytest.mark.asyncio
async def test_receipt_pdf_and_reprint_after_reversal(client: AsyncClient):
    _, customer, _ = await credit_customer_with_bill(client)
    payment = await collect(client, customer["id"], "300.00")

    pdf = await client.get(f"/api/v1/sales/payments/{payment['id']}/document.pdf")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert pdf.content[:4] == b"%PDF"

    rev = await client.post(f"/api/v1/sales/payments/{payment['id']}/reverse",
                            json={"reason": "Paid twice by mistake"})
    assert rev.status_code == 200, rev.text
    doc = (await client.get(f"/api/v1/sales/payments/{payment['id']}/document")).json()
    assert doc["status"] == "REVERSED" and doc["is_reversed"] is True   # reprint is marked, never hidden


@pytest.mark.asyncio
async def test_customer_payment_history_search_and_filters(client: AsyncClient):
    _, customer, _ = await credit_customer_with_bill(client)
    p1 = await collect(client, customer["id"], "300.00")
    await collect(client, customer["id"], "200.00", method="UPI", reference="UTR-2")

    by_number = (await client.get("/api/v1/sales/payments", params={"q": p1["receipt_number"]})).json()
    assert by_number["total"] == 1 and by_number["items"][0]["id"] == p1["id"]
    by_method = (await client.get("/api/v1/sales/payments", params={"method": "UPI"})).json()
    assert by_method["total"] == 1 and D(by_method["items"][0]["amount"]) == D("200.00")
    by_customer = (await client.get("/api/v1/sales/payments", params={"customer_id": customer["id"]})).json()
    assert by_customer["total"] == 2


@pytest.mark.asyncio
async def test_receipt_document_permissions(client: AsyncClient):
    _, customer, _ = await credit_customer_with_bill(client)
    payment = await collect(client, customer["id"], "100.00")
    await add_user(client, "Inventory Manager", "invdoc")
    await login_as(client, "invdoc", "Staff1234")          # no payments.receive permission
    resp = await client.get(f"/api/v1/sales/payments/{payment['id']}/document")
    assert resp.status_code == 403


# ── Supplier payment voucher ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_voucher_document_matches_directive_allocation_example(client: AsyncClient):
    data = await setup_pharmacy_and_catalogue(client)
    c = await finalized_bill(client, data, number="DIR-C", qty="10.00", rate="100.00",
                             due_date=str(TODAY + timedelta(days=30)))   # ₹1,000
    b = await finalized_bill(client, data, number="DIR-B", qty="10.00", rate="80.00",
                             due_date=str(TODAY + timedelta(days=20)))   # ₹800
    a = await finalized_bill(client, data, number="DIR-A", qty="10.00", rate="25.00",
                             due_date=str(TODAY + timedelta(days=10)))   # ₹250
    payment = await pay(client, data, "500.00", auto_allocate=True)

    doc = (await client.get(f"/api/v1/supplier-payments/{payment['id']}/document")).json()
    assert doc["kind"] == "SUPPLIER_PAYMENT_VOUCHER"
    assert doc["payment_number"] == payment["payment_number"]
    assert doc["supplier"]["name"] == data["supplier"]["name"]
    allocs = {al["invoice_number"]: D(al["amount"]) for al in doc["allocations"]}
    assert allocs == {"DIR-A": D("250.00"), "DIR-B": D("250.00")}
    assert D(doc["advance_unallocated"]) == D("0.00")       # NOT an advance
    assert D(doc["payable_after"]) == D("1550.00")          # ₹2,050 − ₹500
    assert doc["recorded_by"]


@pytest.mark.asyncio
async def test_voucher_document_advance_and_pdf(client: AsyncClient):
    data = await setup_pharmacy_and_catalogue(client)
    await finalized_bill(client, data, number="ADV-1", qty="10.00", rate="30.00")   # ₹300
    payment = await pay(client, data, "500.00", auto_allocate=True)

    doc = (await client.get(f"/api/v1/supplier-payments/{payment['id']}/document")).json()
    assert D(doc["allocated_total"]) == D("300.00")
    assert D(doc["advance_unallocated"]) == D("200.00")     # genuine excess = advance
    assert D(doc["payable_after"]) == D("0.00")

    pdf = await client.get(f"/api/v1/supplier-payments/{payment['id']}/document.pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"

    by_number = (await client.get("/api/v1/supplier-payments",
                                  params={"q": payment["payment_number"]})).json()
    assert by_number["total"] == 1
    by_method = (await client.get("/api/v1/supplier-payments", params={"method": "CASH"})).json()
    assert by_method["total"] == 0                          # this payment was BANK_TRANSFER
