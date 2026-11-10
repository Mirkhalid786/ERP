"""
Stage 5B preparation: supplier invoice workflow, supplier payments, expenses and
categorised cash-shift movements (operational records, no GL posting).
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import today_local
from app.models.auth import AuditLog
from app.models.payables import SupplierPayment
from app.models.sales import CashMovement
from tests.conftest import login
from tests.sales_helpers import D, add_user, key, login_as
from tests.test_purchasing import setup_pharmacy_and_catalogue
from tests.test_purchasing_fixes import create_supplier, grn_line, receive_and_post, return_line, return_payload

TODAY = today_local()


def bill(data, number="BILL-001", grn=None, qty="10.00", rate="30.00", declared=None, supplier=None, **extra) -> dict:
    """Supplier bill for 10 strips at ₹30 (₹300, no GST) unless told otherwise."""
    item = {"medicine_id": data["medicine"]["id"], "batch_number": "PAY-B1", "packaging_unit_id": data["strip_unit"]["id"],
            "billed_qty_pack": qty, "purchase_rate": rate}
    if grn:
        item["grn_item_id"] = grn["items"][0]["id"]
        item["batch_number"] = grn["items"][0]["batch_number"]
    total = (Decimal(qty) * Decimal(rate)).quantize(Decimal("0.01"))
    payload = {"supplier_id": (supplier or data["supplier"])["id"], "invoice_number": number,
               "invoice_date": str(TODAY), "received_date": str(TODAY),
               "supplier_declared_total": str(declared if declared is not None else total), "items": [item],
               "grn_id": grn["id"] if grn else None}
    payload.update(extra)
    return payload


async def new_bill(client, data, **kw) -> dict:
    resp = await client.post("/api/v1/purchases/invoices", json=bill(data, **kw))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def finalized_bill(client, data, **kw) -> dict:
    inv = await new_bill(client, data, **kw)
    fin = await client.post(f"/api/v1/purchases/invoices/{inv['id']}/finalize", json={"acknowledge_warnings": True})
    assert fin.status_code == 200, fin.text
    return fin.json()


def payment(data, amount, supplier=None, **extra) -> dict:
    body = {"idempotency_key": key(), "supplier_id": (supplier or data["supplier"])["id"], "payment_date": str(TODAY),
            "method": "BANK_TRANSFER", "reference": f"UTR{key()[:10]}", "amount": str(amount)}
    body.update(extra)
    return body


async def pay(client, data, amount, expect=201, **extra) -> dict:
    resp = await client.post("/api/v1/supplier-payments", json=payment(data, amount, **extra))
    assert resp.status_code == expect, resp.text
    return resp.json()


async def invoice(client, inv_id) -> dict:
    return (await client.get(f"/api/v1/purchases/invoices/{inv_id}")).json()


async def audit_actions(engine, entity_type: str) -> list:
    async with async_sessionmaker(engine, class_=AsyncSession)() as db:
        return list((await db.execute(select(AuditLog.action).where(AuditLog.entity_type == entity_type)
                                      .order_by(AuditLog.id))).scalars())


async def reconciled(client) -> dict:
    rec = (await client.get("/api/v1/supplier-payments/reconciliation")).json()
    assert rec["ok"], rec["problems"]
    return rec


# ── Supplier invoices ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_invoice_workflow_draft_edit_finalize_and_lock(client: AsyncClient, test_engine):
    data = await setup_pharmacy_and_catalogue(client)
    grn = await receive_and_post(client, data["supplier"]["id"],
                                 [grn_line(data["medicine"]["id"], data["strip_unit"]["id"], batch="WF-B1")])
    inv = await new_bill(client, data, grn=grn, rate="31.00")
    assert inv["status"] == "DRAFT"

    check = (await client.get(f"/api/v1/purchases/invoices/{inv['id']}/finalize-check")).json()
    assert check["can_finalize"] and any("differs from GRN rate" in w for w in check["warnings"])
    need_ack = await client.post(f"/api/v1/purchases/invoices/{inv['id']}/finalize", json={})
    assert need_ack.status_code == 409 and need_ack.json()["detail"]["warnings"]

    # Draft edit: rate corrected to the GRN rate; totals recomputed
    edited = await client.put(f"/api/v1/purchases/invoices/{inv['id']}", json=bill(data, grn=grn))
    assert edited.status_code == 200 and D(edited.json()["calculated_grand_total"]) == D("300.00")
    fin = await client.post(f"/api/v1/purchases/invoices/{inv['id']}/finalize", json={})
    assert fin.status_code == 200, fin.text
    body = fin.json()
    assert body["status"] == "POSTED" and body["finalized_by_name"] == "Admin User"
    assert D(body["outstanding_amount"]) == D("300.00")

    again = await client.put(f"/api/v1/purchases/invoices/{inv['id']}", json=bill(data, grn=grn, rate="10.00"))
    assert again.status_code == 409
    assert (await client.post(f"/api/v1/purchases/invoices/{inv['id']}/finalize", json={})).status_code == 409
    assert await audit_actions(test_engine, "supplier_invoice") == [
        "supplier_invoice.create", "supplier_invoice.update", "supplier_invoice.finalize"]


@pytest.mark.asyncio
async def test_finalize_validates_grn_dates_totals_and_overbilling(client: AsyncClient):
    data = await setup_pharmacy_and_catalogue(client)
    grn = await receive_and_post(client, data["supplier"]["id"],
                                 [grn_line(data["medicine"]["id"], data["strip_unit"]["id"], batch="OB-B1")])
    await finalized_bill(client, data, number="OB-1", grn=grn, qty="6.00")

    over = await new_bill(client, data, number="OB-2", grn=grn, qty="5.00")     # 6 + 5 > 10 strips received
    resp = await client.post(f"/api/v1/purchases/invoices/{over['id']}/finalize", json={"acknowledge_warnings": True})
    assert resp.status_code == 422
    assert any("only 150" in e for e in resp.json()["detail"]["errors"])

    future = await new_bill(client, data, number="FUT-1", invoice_date=str(TODAY + timedelta(days=2)),
                            received_date=str(TODAY + timedelta(days=2)))
    resp = await client.post(f"/api/v1/purchases/invoices/{future['id']}/finalize", json={"acknowledge_warnings": True})
    assert resp.status_code == 422 and any("future" in e for e in resp.json()["detail"]["errors"])

    gap = await new_bill(client, data, number="GAP-1", declared="320.00")       # ₹20 off the printed bill
    resp = await client.post(f"/api/v1/purchases/invoices/{gap['id']}/finalize", json={"acknowledge_warnings": True})
    assert resp.status_code == 422 and any("discrepancy notes" in e for e in resp.json()["detail"]["errors"])

    draft_grn = (await client.post("/api/v1/purchases/receipts", json={
        "supplier_id": data["supplier"]["id"], "receipt_date": str(TODAY),
        "items": [grn_line(data["medicine"]["id"], data["strip_unit"]["id"], batch="DR-B1")]})).json()
    unposted = await new_bill(client, data, number="DRG-1", grn=draft_grn)
    resp = await client.post(f"/api/v1/purchases/invoices/{unposted['id']}/finalize", json={"acknowledge_warnings": True})
    assert resp.status_code == 422 and any("post it before" in e for e in resp.json()["detail"]["errors"])


@pytest.mark.asyncio
async def test_duplicate_supplier_invoice_numbers(client: AsyncClient):
    data = await setup_pharmacy_and_catalogue(client)
    await new_bill(client, data, number="GST/1001")
    for variant in ("GST/1001", "gst-1001", "GST 1001"):
        resp = await client.post("/api/v1/purchases/invoices", json=bill(data, number=variant))
        assert resp.status_code == 409, variant
    other = await create_supplier(client, "Other Distributor")
    assert (await client.post("/api/v1/purchases/invoices", json=bill(data, number="GST/1001", supplier=other))).status_code == 201
    last_year = TODAY.replace(year=TODAY.year - 1)
    resp = await client.post("/api/v1/purchases/invoices", json=bill(
        data, number="GST/1001", invoice_date=str(last_year), received_date=str(last_year)))
    assert resp.status_code == 201       # Suppliers restart their series each financial year


@pytest.mark.asyncio
async def test_cancel_rules_and_reentry_of_corrected_bill(client: AsyncClient, test_engine):
    data = await setup_pharmacy_and_catalogue(client)
    inv = await finalized_bill(client, data, number="CX-1")
    p = await pay(client, data, "100.00", allocations=[{"invoice_id": inv["id"], "amount": "100.00"}])
    blocked = await client.post(f"/api/v1/purchases/invoices/{inv['id']}/cancel", json={"reason": "Wrong rate on the bill"})
    assert blocked.status_code == 409 and "reverse those payments" in blocked.json()["detail"]
    await client.post(f"/api/v1/supplier-payments/{p['id']}/reverse", json={"reason": "Paid against the wrong bill"})
    short = await client.post(f"/api/v1/purchases/invoices/{inv['id']}/cancel", json={"reason": "wrong"})
    assert short.status_code == 422
    ok = await client.post(f"/api/v1/purchases/invoices/{inv['id']}/cancel", json={"reason": "Wrong rate on the bill"})
    assert ok.status_code == 200 and ok.json()["status"] == "CANCELLED"
    assert ok.json()["cancel_reason"] == "Wrong rate on the bill"

    # The original is kept; the corrected bill reuses the number and points at it
    redo = await client.post("/api/v1/purchases/invoices", json=bill(data, number="CX-1", rate="28.00",
                                                                     replaces_invoice_id=inv["id"]))
    assert redo.status_code == 201, redo.text
    assert redo.json()["replaces_invoice_id"] == inv["id"]
    assert (await invoice(client, inv["id"]))["status"] == "CANCELLED"
    bad = await client.post("/api/v1/purchases/invoices", json=bill(data, number="CX-2", replaces_invoice_id=redo.json()["id"]))
    assert bad.status_code == 422
    assert "supplier_invoice.cancel" in await audit_actions(test_engine, "supplier_invoice")


@pytest.mark.asyncio
async def test_posted_return_blocks_cancel_and_reduces_outstanding(client: AsyncClient):
    data = await setup_pharmacy_and_catalogue(client)
    grn = await receive_and_post(client, data["supplier"]["id"],
                                 [grn_line(data["medicine"]["id"], data["strip_unit"]["id"], batch="RT-B1")])
    inv = await finalized_bill(client, data, number="RT-1", grn=grn)
    ret = await client.post("/api/v1/purchases/returns", json=return_payload(
        data["supplier"]["id"], [return_line(data["medicine"]["id"], grn["items"][0]["batch_id"], data["strip_unit"]["id"],
                                             qty="2.00", grn_item_id=grn["items"][0]["id"])],
        grn_id=grn["id"], invoice_id=inv["id"]))
    assert ret.status_code == 201, ret.text
    assert (await client.post(f"/api/v1/purchases/returns/{ret.json()['id']}/post")).status_code == 200
    after = await invoice(client, inv["id"])
    returned = D(after["returned_amount"])
    assert returned > 0 and D(after["outstanding_amount"]) == D("300.00") - returned
    resp = await client.post(f"/api/v1/purchases/invoices/{inv['id']}/cancel", json={"reason": "Trying to cancel a returned bill"})
    assert resp.status_code == 409 and "supplier returns" in resp.json()["detail"]
    over = await client.post("/api/v1/supplier-payments", json=payment(data, "300.00",
                             allocations=[{"invoice_id": inv["id"], "amount": "300.00"}]))
    assert over.status_code == 422
    await pay(client, data, str(D("300.00") - returned), auto_allocate=True)
    assert (await invoice(client, inv["id"]))["payment_status"] == "PAID"
    rec = await reconciled(client)
    assert D(rec["suppliers"][0]["balance"]) == D("0.00")


# ── Supplier payments ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_partial_payments_allocations_and_balances(client: AsyncClient, test_engine):
    data = await setup_pharmacy_and_catalogue(client)
    a = await finalized_bill(client, data, number="PP-1")                                   # ₹300
    b = await finalized_bill(client, data, number="PP-2", qty="5.00")                       # ₹150
    draft = await new_bill(client, data, number="PP-3")

    to_draft = await client.post("/api/v1/supplier-payments", json=payment(
        data, "10.00", allocations=[{"invoice_id": draft["id"], "amount": "10.00"}]))
    assert to_draft.status_code == 422 and "open finalised" in to_draft.json()["detail"]

    p1 = await pay(client, data, "100.00", allocations=[{"invoice_id": a["id"], "amount": "100.00"}])
    assert p1["payment_number"].startswith("SP/") and D(p1["unallocated"]) == 0
    assert (await invoice(client, a["id"]))["payment_status"] == "PARTIALLY_PAID"
    too_much = await client.post("/api/v1/supplier-payments", json=payment(
        data, "250.00", allocations=[{"invoice_id": a["id"], "amount": "250.00"}]))
    assert too_much.status_code == 422 and "outstanding ₹200.00" in too_much.json()["detail"]
    split = await client.post("/api/v1/supplier-payments", json=payment(
        data, "50.00", allocations=[{"invoice_id": a["id"], "amount": "40.00"}, {"invoice_id": b["id"], "amount": "20.00"}]))
    assert split.status_code == 422 and "exceed" in split.json()["detail"]

    p2 = await pay(client, data, "400.00", auto_allocate=True)     # Oldest due first: 200 to A, 150 to B, 50 advance
    assert D(p2["allocated"]) == D("350.00") and D(p2["unallocated"]) == D("50.00")
    assert (await invoice(client, a["id"]))["payment_status"] == "PAID"
    assert (await invoice(client, b["id"]))["payment_status"] == "PAID"
    balances = (await client.get("/api/v1/supplier-payments/balances")).json()[0]
    assert D(balances["invoiced"]) == D("450.00") and D(balances["paid"]) == D("500.00")
    assert D(balances["balance"]) == D("-50.00") and D(balances["unallocated_payments"]) == D("50.00")

    c = await finalized_bill(client, data, number="PP-4", qty="2.00")                        # ₹60
    allocated = await client.post(f"/api/v1/supplier-payments/{p2['id']}/allocate", json={})
    assert allocated.status_code == 200 and D(allocated.json()["unallocated"]) == 0
    assert (await invoice(client, c["id"]))["payment_status"] == "PARTIALLY_PAID"
    assert D((await invoice(client, c["id"]))["outstanding_amount"]) == D("10.00")
    await reconciled(client)
    assert (await audit_actions(test_engine, "supplier_payment")).count("supplier_payment.create") == 2


@pytest.mark.asyncio
async def test_auto_allocation_matches_directive_example_oldest_due_first(client: AsyncClient):
    """
    Master-directive §7 example, encoded exactly:
      Invoice A ₹250, Invoice B ₹800, Invoice C ₹1,000 (total ₹2,050).
      Pay ₹500 with auto-allocation -> ₹250 to A, ₹250 to B, C untouched.
      Remaining: A ₹0, B ₹550, C ₹1,000 (total ₹1,550) and NO advance,
      because the payment does not exceed total outstanding.
    Invoices are created in reverse due-date order (C, B, A) so the test
    proves allocation follows due date, not creation order.
    """
    data = await setup_pharmacy_and_catalogue(client)
    c = await finalized_bill(client, data, number="DIR-C", qty="10.00", rate="100.00",
                             due_date=str(TODAY + timedelta(days=30)))   # ₹1,000
    b = await finalized_bill(client, data, number="DIR-B", qty="10.00", rate="80.00",
                             due_date=str(TODAY + timedelta(days=20)))   # ₹800
    a = await finalized_bill(client, data, number="DIR-A", qty="10.00", rate="25.00",
                             due_date=str(TODAY + timedelta(days=10)))   # ₹250

    p = await pay(client, data, "500.00", auto_allocate=True)
    assert D(p["allocated"]) == D("500.00")
    assert D(p["unallocated"]) == D("0.00")  # must NOT be classified as advance

    allocs = {al["invoice_id"]: D(al["amount"]) for al in p["allocations"] if al["status"] == "ACTIVE"}
    assert allocs == {a["id"]: D("250.00"), b["id"]: D("250.00")}

    inv_a, inv_b, inv_c = (await invoice(client, a["id"])), (await invoice(client, b["id"])), (await invoice(client, c["id"]))
    assert D(inv_a["outstanding_amount"]) == D("0.00") and inv_a["payment_status"] == "PAID"
    assert D(inv_b["outstanding_amount"]) == D("550.00") and inv_b["payment_status"] == "PARTIALLY_PAID"
    assert D(inv_c["outstanding_amount"]) == D("1000.00") and inv_c["payment_status"] == "UNPAID"

    balances = (await client.get("/api/v1/supplier-payments/balances")).json()[0]
    assert D(balances["balance"]) == D("1550.00")
    assert D(balances["unallocated_payments"]) == D("0.00")
    await reconciled(client)


@pytest.mark.asyncio
async def test_duplicate_payment_prevention(client: AsyncClient):
    data = await setup_pharmacy_and_catalogue(client)
    inv = await finalized_bill(client, data)
    body = payment(data, "100.00", reference="UTR-555", allocations=[{"invoice_id": inv["id"], "amount": "100.00"}])
    first = await client.post("/api/v1/supplier-payments", json=body)
    replay = await client.post("/api/v1/supplier-payments", json=body)
    assert first.status_code == 201 and replay.status_code == 200
    assert replay.json()["replayed"] and replay.json()["id"] == first.json()["id"]
    reused = await client.post("/api/v1/supplier-payments", json={**body, "amount": "90.00",
                               "allocations": [{"invoice_id": inv["id"], "amount": "90.00"}]})
    assert reused.status_code == 409 and "different payment" in reused.json()["detail"]
    same_ref = await client.post("/api/v1/supplier-payments", json=payment(data, "20.00", reference="utr 555"))
    assert same_ref.status_code == 409 and "already recorded" in same_ref.json()["detail"]
    assert (await client.post("/api/v1/supplier-payments", json=payment(data, "20.00", reference=None))).status_code == 422

    cash = payment(data, "75.00", method="CASH", cash_source="OFFICE_CASH", reference=None)
    assert (await client.post("/api/v1/supplier-payments", json=cash)).status_code == 201
    twin = await client.post("/api/v1/supplier-payments", json={**cash, "idempotency_key": key()})
    assert twin.status_code == 409 and "confirm" in twin.json()["detail"]
    confirmed = await client.post("/api/v1/supplier-payments", json={**cash, "idempotency_key": key(), "confirm_duplicate": True})
    assert confirmed.status_code == 201
    assert (await invoice(client, inv["id"]))["paid_amount"] == "100.00"
    await reconciled(client)


@pytest.mark.asyncio
async def test_payment_reversal_restores_outstanding(client: AsyncClient, test_engine):
    data = await setup_pharmacy_and_catalogue(client)
    inv = await finalized_bill(client, data)
    p = await pay(client, data, "300.00", reference="CHQ-77", method="CHEQUE", auto_allocate=True)
    assert (await invoice(client, inv["id"]))["payment_status"] == "PAID"
    short = await client.post(f"/api/v1/supplier-payments/{p['id']}/reverse", json={"reason": "bounce"})
    assert short.status_code == 422
    rev = await client.post(f"/api/v1/supplier-payments/{p['id']}/reverse", json={"reason": "Cheque bounced at the bank"})
    assert rev.status_code == 200, rev.text
    assert rev.json()["status"] == "REVERSED" and rev.json()["allocations"][0]["status"] == "REVERSED"
    after = await invoice(client, inv["id"])
    assert after["payment_status"] == "UNPAID" and D(after["outstanding_amount"]) == D("300.00")
    assert (await client.post(f"/api/v1/supplier-payments/{p['id']}/reverse",
                              json={"reason": "Cheque bounced at the bank"})).status_code == 409
    # The reversed cheque number may be recorded again (re-presented cheque)
    await pay(client, data, "300.00", reference="CHQ-77", method="CHEQUE", auto_allocate=True)
    await reconciled(client)
    assert "supplier_payment.reverse" in await audit_actions(test_engine, "supplier_payment")


@pytest.mark.asyncio
async def test_drawer_cash_payment_lowers_expected_cash(client: AsyncClient):
    data = await setup_pharmacy_and_catalogue(client)
    await finalized_bill(client, data)
    no_shift = await client.post("/api/v1/supplier-payments", json=payment(
        data, "50.00", method="CASH", cash_source="DRAWER", reference=None))
    assert no_shift.status_code == 409
    no_source = await client.post("/api/v1/supplier-payments", json=payment(data, "50.00", method="CASH", reference=None))
    assert no_source.status_code == 422
    shift = (await client.post("/api/v1/shifts", json={"opening_cash": "1000.00"})).json()
    drawer = await pay(client, data, "50.00", method="CASH", cash_source="DRAWER", reference=None, auto_allocate=True)
    assert drawer["shift_id"] == shift["id"]
    await pay(client, data, "70.00", method="CASH", cash_source="OFFICE_CASH", reference=None, auto_allocate=True)
    await pay(client, data, "30.00", method="UPI", auto_allocate=True)
    summary = (await client.get(f"/api/v1/shifts/{shift['id']}")).json()
    assert D(summary["supplier_payments_paid"]) == D("50.00") and D(summary["expected_cash"]) == D("950.00")
    await client.post(f"/api/v1/supplier-payments/{drawer['id']}/reverse", json={"reason": "Cash returned to the drawer"})
    assert D((await client.get(f"/api/v1/shifts/{shift['id']}")).json()["expected_cash"]) == D("1000.00")


@pytest.mark.asyncio
async def test_payables_permissions(client: AsyncClient):
    data = await setup_pharmacy_and_catalogue(client)
    inv = await new_bill(client, data)
    await add_user(client, "Pharmacist", "pharma1")
    await add_user(client, "Inventory Manager", "stock1")
    await login_as(client, "pharma1", "Staff1234")
    assert (await client.post(f"/api/v1/purchases/invoices/{inv['id']}/finalize", json={})).status_code == 403
    assert (await client.post("/api/v1/supplier-payments", json=payment(data, "10.00"))).status_code == 403
    assert (await client.get("/api/v1/supplier-payments")).status_code == 403
    await login_as(client, "stock1", "Staff1234")
    fin = await client.post(f"/api/v1/purchases/invoices/{inv['id']}/finalize", json={"acknowledge_warnings": True})
    assert fin.status_code == 200
    assert (await client.get("/api/v1/supplier-payments")).status_code == 200
    assert (await client.post("/api/v1/supplier-payments", json=payment(data, "10.00"))).status_code == 403
    cancel = await client.post(f"/api/v1/purchases/invoices/{inv['id']}/cancel", json={"reason": "Inventory manager tries"})
    assert cancel.status_code == 403
    await login(client)
    p = await pay(client, data, "10.00", auto_allocate=True)
    await login_as(client, "stock1", "Staff1234")
    assert (await client.post(f"/api/v1/supplier-payments/{p['id']}/reverse",
                              json={"reason": "Not allowed for this role"})).status_code == 403


# ── Expenses ───────────────────────────────────────────────────────────────────

async def categories(client) -> dict:
    return {c["code"]: c for c in (await client.get("/api/v1/expenses/categories")).json()}


def expense(cat, amount="500.00", **extra) -> dict:
    body = {"idempotency_key": key(), "category_id": cat["id"], "expense_date": str(TODAY), "amount": amount,
            "payee": "Shop owner", "payment_method": "BANK_TRANSFER", "reference": f"UTR{key()[:8]}"}
    body.update(extra)
    return body


@pytest.mark.asyncio
async def test_expense_recording_approval_and_reversal(client: AsyncClient, test_engine):
    await setup_pharmacy_and_catalogue(client)
    cats = await categories(client)
    assert {"RENT", "SALARY", "ELECTRICITY", "MISC"} <= set(cats) and cats["RENT"]["account_code_hint"] == "5210"

    owner = await client.post("/api/v1/expenses", json=expense(cats["RENT"], "12000.00", payee="Landlord", bill_number="R-OCT"))
    assert owner.status_code == 201 and owner.json()["status"] == "APPROVED"
    assert owner.json()["expense_number"].startswith("EXP/")

    await add_user(client, "Pharmacist", "pharma1")
    await login_as(client, "pharma1", "Staff1234")
    pend = await client.post("/api/v1/expenses", json=expense(cats["STATIONERY"], "240.00", payee="Stationers",
                                                               payment_method="CASH", cash_source="OFFICE_CASH", reference=None))
    assert pend.status_code == 201 and pend.json()["status"] == "PENDING_APPROVAL"
    assert (await client.post(f"/api/v1/expenses/{pend.json()['id']}/approve", json={})).status_code == 403
    drawer = await client.post("/api/v1/expenses", json=expense(cats["MISC"], "20.00", payment_method="CASH",
                                                                 cash_source="DRAWER", reference=None))
    assert drawer.status_code == 403

    await login(client)
    assert (await client.post(f"/api/v1/expenses/{pend.json()['id']}/reject", json={})).status_code == 422
    ok = await client.post(f"/api/v1/expenses/{pend.json()['id']}/approve", json={"note": "Bill seen"})
    assert ok.status_code == 200 and ok.json()["status"] == "APPROVED" and ok.json()["approved_by_name"] == "Admin User"
    listing = (await client.get("/api/v1/expenses")).json()
    assert D(listing["approved_total"]) == D("12240.00")

    rev = await client.post(f"/api/v1/expenses/{owner.json()['id']}/reverse", json={"reason": "Rent was entered twice"})
    assert rev.status_code == 200 and rev.json()["status"] == "REVERSED"
    assert (await client.post(f"/api/v1/expenses/{owner.json()['id']}/reverse",
                              json={"reason": "Rent was entered twice"})).status_code == 409
    assert D((await client.get("/api/v1/expenses")).json()["approved_total"]) == D("240.00")
    assert await audit_actions(test_engine, "expense") == ["expense.create", "expense.create", "expense.approve", "expense.reverse"]


@pytest.mark.asyncio
async def test_expense_validation_and_duplicates(client: AsyncClient):
    await setup_pharmacy_and_catalogue(client)
    cats = await categories(client)
    assert (await client.post("/api/v1/expenses", json=expense(cats["RENT"], reference=None))).status_code == 422
    assert (await client.post("/api/v1/expenses", json=expense(cats["RENT"], "100.00", gst_amount="120.00"))).status_code == 422
    assert (await client.post("/api/v1/expenses", json=expense(cats["RENT"], expense_date=str(TODAY + timedelta(days=1))))).status_code == 422
    assert (await client.post("/api/v1/expenses", json=expense(cats["RENT"], payment_method="CASH", reference=None))).status_code == 422

    first = expense(cats["ELECTRICITY"], "1800.00", gst_amount="0", payee="MSEDCL", bill_number="EB-10")
    assert (await client.post("/api/v1/expenses", json=first)).status_code == 201
    replay = await client.post("/api/v1/expenses", json=first)
    assert replay.status_code == 200 and replay.json()["replayed"]
    same_bill = await client.post("/api/v1/expenses", json=expense(cats["ELECTRICITY"], "1700.00", payee="msedcl", bill_number="EB-10"))
    assert same_bill.status_code == 409
    same_day = await client.post("/api/v1/expenses", json=expense(cats["ELECTRICITY"], "1800.00", payee="MSEDCL"))
    assert same_day.status_code == 409
    confirmed = await client.post("/api/v1/expenses", json=expense(cats["ELECTRICITY"], "1800.00", payee="MSEDCL",
                                                                   confirm_duplicate=True))
    assert confirmed.status_code == 201

    misc = cats["MISC"]
    off = await client.put(f"/api/v1/expenses/categories/{misc['id']}", json={"name": misc["name"], "is_active": False})
    assert off.status_code == 200
    assert (await client.post("/api/v1/expenses", json=expense(misc))).status_code == 422


@pytest.mark.asyncio
async def test_drawer_expense_and_cash_movement_categories(client: AsyncClient, test_engine):
    await setup_pharmacy_and_catalogue(client)
    cats = await categories(client)
    shift = (await client.post("/api/v1/shifts", json={"opening_cash": "500.00"})).json()
    tea = await client.post("/api/v1/expenses", json=expense(cats["STAFF_WELFARE"], "60.00", payee="Tea stall",
                                                              payment_method="CASH", cash_source="DRAWER", reference=None))
    assert tea.status_code == 201 and tea.json()["shift_id"] == shift["id"]
    await client.post("/api/v1/expenses", json=expense(cats["RENT"], "5000.00"))           # Bank: not in the drawer

    url = f"/api/v1/shifts/{shift['id']}/cash-movements"
    no_cat = await client.post(url, json={"movement_type": "PAID_OUT", "amount": "10.00", "reason": "Courier"})
    assert no_cat.status_code == 422
    expense_as_paidout = await client.post(url, json={"movement_type": "PAID_OUT", "category": "PETTY_EXPENSE",
                                                      "amount": "10.00", "reason": "Courier"})
    assert expense_as_paidout.status_code == 422 and "own screens" in expense_as_paidout.json()["detail"]
    no_slip = await client.post(url, json={"movement_type": "PAID_OUT", "category": "BANK_DEPOSIT", "amount": "200.00",
                                           "reason": "Evening deposit"})
    assert no_slip.status_code == 422
    dep = await client.post(url, json={"movement_type": "PAID_OUT", "category": "BANK_DEPOSIT", "amount": "200.00",
                                       "reason": "Evening deposit", "reference": "SLIP-9"})
    assert dep.status_code == 201
    vague = await client.post(url, json={"movement_type": "PAID_IN", "category": "OTHER_IN", "amount": "5.00", "reason": "misc"})
    assert vague.status_code == 422
    assert (await client.post(url, json={"movement_type": "PAID_IN", "category": "FLOAT_IN", "amount": "100.00",
                                         "reason": "Change from the safe"})).status_code == 201

    summary = (await client.get(f"/api/v1/shifts/{shift['id']}")).json()
    assert D(summary["expenses_paid"]) == D("60.00") and D(summary["paid_out"]) == D("200.00")
    assert D(summary["expected_cash"]) == D("340.00")        # 500 - 60 - 200 + 100
    assert {m["category"] for m in summary["cash_movements"]} == {"BANK_DEPOSIT", "FLOAT_IN"}

    # A movement recorded before categories existed can be classified once, without changing its amount
    async with async_sessionmaker(test_engine, class_=AsyncSession)() as db:
        legacy = CashMovement(pharmacy_id=1, shift_id=shift["id"], movement_type="PAID_OUT", amount=Decimal("50.00"),
                              reason="TEST courier charges", created_by=1)
        db.add(legacy)
        await db.flush()
        legacy_id = legacy.id
        await db.commit()
    assert (await client.get(f"/api/v1/shifts/{shift['id']}")).json()["expected_cash"] == "290.00"
    cls = f"/api/v1/shifts/cash-movements/{legacy_id}/classify"
    assert (await client.post(cls, json={"category": "FLOAT_IN", "note": "Wrong direction"})).status_code == 422
    assert (await client.post(cls, json={"category": "PETTY_EXPENSE", "note": "Courier paid before Expenses existed"})).status_code == 200
    assert (await client.post(cls, json={"category": "OTHER_OUT", "note": "Second change"})).status_code == 409
    assert (await client.get(f"/api/v1/shifts/{shift['id']}")).json()["expected_cash"] == "290.00"
    dep_id = dep.json()["id"]
    assert (await client.post(f"/api/v1/shifts/cash-movements/{dep_id}/classify",
                              json={"category": "OTHER_OUT", "note": "Not a legacy row"})).status_code == 409

    close = await client.post(f"/api/v1/shifts/{shift['id']}/close", json={"counted_cash": "290.00"})
    assert close.status_code == 200 and close.json()["variance"] == "0.00"
    # Reversing the drawer expense after the shift closed leaves the closed shift's figures alone
    await client.post(f"/api/v1/expenses/{tea.json()['id']}/reverse", json={"reason": "Recorded against the wrong day"})
    after = (await client.get(f"/api/v1/shifts/{shift['id']}")).json()
    assert after["expected_cash"] == "290.00" and D(after["expenses_paid"]) == D("60.00")


@pytest.mark.asyncio
async def test_expense_permissions_by_role(client: AsyncClient):
    await setup_pharmacy_and_catalogue(client)
    cats = await categories(client)
    await add_user(client, "Cashier", "cash1")
    await login_as(client, "cash1", "Staff1234")
    created = await client.post("/api/v1/expenses", json=expense(cats["CLEANING"], "150.00"))
    assert created.status_code == 201 and created.json()["status"] == "PENDING_APPROVAL"
    assert (await client.get("/api/v1/expenses")).status_code == 403            # Cashiers record, they do not browse
    assert (await client.post("/api/v1/expenses/categories", json={"code": "X", "name": "Xyz"})).status_code == 403
    assert (await client.post(f"/api/v1/expenses/{created.json()['id']}/reverse",
                              json={"reason": "Cashier cannot reverse"})).status_code == 403


@pytest.mark.asyncio
async def test_historical_summary_is_read_only(client: AsyncClient, test_engine):
    data = await setup_pharmacy_and_catalogue(client)
    await finalized_bill(client, data)
    await pay(client, data, "100.00", auto_allocate=True)
    before = await audit_actions(test_engine, "supplier_payment")
    resp = await client.get("/api/v1/accounting/historical-summary")
    assert resp.status_code == 200, resp.text
    summary = resp.json()
    items = {r["item"]: r for rows in summary["sections"].values() for r in rows}
    assert items["Supplier invoices (POSTED)"]["count"] == 1 and items["Supplier invoices (POSTED)"]["total"] == "300.00"
    assert items["Supplier payments (POSTED)"]["total"] == "100.00"
    assert await audit_actions(test_engine, "supplier_payment") == before
    async with async_sessionmaker(test_engine, class_=AsyncSession)() as db:
        assert len((await db.execute(select(SupplierPayment))).scalars().all()) == 1
