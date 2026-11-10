"""Stage 5B: automatic general-ledger postings from finalised business documents."""
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import today_local
from app.models.accounting import GlPostingIssue, JournalEntry
from app.models.payables import Expense
from tests.conftest import login
from tests.sales_helpers import D, add_user, create_customer, key, line, login_as, receive_stock, sell
from tests.test_purchasing import setup_pharmacy_and_catalogue

TODAY = today_local()
START = TODAY.replace(day=1)


async def books(client: AsyncClient, data=None) -> dict:
    data = data or await setup_pharmacy_and_catalogue(client)
    resp = await client.post("/api/v1/accounting/setup", json={"books_start_date": str(START)})
    assert resp.status_code == 201, resp.text
    return data


async def balances(client) -> dict:
    tb = (await client.get("/api/v1/accounting/trial-balance")).json()
    assert tb["is_balanced"], tb["totals"]
    return {r["code"]: D(r["closing_debit"]) - D(r["closing_credit"]) for r in tb["rows"]}


async def recon(client) -> dict:
    return (await client.get("/api/v1/accounting/postings/reconciliation")).json()


async def journals(engine, prefix: str = "") -> list:
    async with async_sessionmaker(engine, class_=AsyncSession)() as db:
        stmt = select(JournalEntry.source_key).where(JournalEntry.source_key.like(f"{prefix}%"))
        return list((await db.execute(stmt.order_by(JournalEntry.id))).scalars())


def assert_clean(r: dict):
    assert r["missing"] == [] and r["mismatched"] == [] and r["unbuildable"] == [] and r["open_issues"] == [], r
    for c in r["controls"]:
        assert c["ok"], c
    assert r["ok"]


async def bill_for(client, data, grn_batch: dict, number="GL-BILL-1"):
    grns = (await client.get("/api/v1/purchases/receipts")).json()
    grns = grns if isinstance(grns, list) else grns["items"]
    grn = next(g for g in grns if g["status"] == "POSTED")
    grn = (await client.get(f"/api/v1/purchases/receipts/{grn['id']}")).json()
    gi = grn["items"][0]
    resp = await client.post("/api/v1/purchases/invoices", json={
        "supplier_id": data["supplier"]["id"], "invoice_number": number, "grn_id": grn["id"],
        "invoice_date": str(TODAY), "received_date": str(TODAY), "supplier_declared_total": "336.00",
        "items": [{"grn_item_id": gi["id"], "medicine_id": gi["medicine_id"], "batch_number": gi["batch_number"],
                   "packaging_unit_id": gi["packaging_unit_id"], "billed_qty_pack": "10.00", "purchase_rate": "30.00",
                   "tax_rate_id": data["gst_12"]["id"]}]})
    assert resp.status_code == 201, resp.text
    fin = await client.post(f"/api/v1/purchases/invoices/{resp.json()['id']}/finalize", json={"acknowledge_warnings": True})
    assert fin.status_code == 200, fin.text
    return fin.json()


# ── Full cycle ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_business_cycle_posts_balanced_journals_matching_subledgers(client: AsyncClient, test_engine):
    data = await books(client)
    batch = await receive_stock(client, data, "GL-B1")                       # 10 strips @ ₹30 (150 tablets)
    b = await balances(client)
    assert b["1150"] == D("300.00") and b["2115"] == D("-300.00")            # Inventory Dr / GRNI Cr

    bill = await bill_for(client, data, batch)                                # ₹300 + GST 36
    b = await balances(client)
    assert b.get("2115", 0) == 0 and b["2110"] == D("-336.00") and b["1164"] == D("36.00")

    for amount, method, ref in (("136.00", "BANK_TRANSFER", "UTR-GL-1"), ("200.00", "UPI", "UPI-GL-1")):
        resp = await client.post("/api/v1/supplier-payments", json={
            "idempotency_key": key(), "supplier_id": data["supplier"]["id"], "payment_date": str(TODAY),
            "method": method, "reference": ref, "amount": amount, "auto_allocate": True})
        assert resp.status_code == 201, resp.text
    b = await balances(client)
    assert b.get("2110", 0) == 0 and b["1121"] == D("-336.00")

    shift = (await client.post("/api/v1/shifts", json={"opening_cash": "0.00"})).json()
    cash_sale = await sell(client, [line(data, "2")], [{"method": "CASH", "tendered": "100"}])
    cust = await create_customer(client)
    credit = await sell(client, [line(data, "3")], [{"method": "UPI", "amount": "20.00", "reference": "U1"}],
                        customer_id=cust["id"])
    coll = await client.post(f"/api/v1/customers/{cust['id']}/payments", json={
        "idempotency_key": key(), "method": "CASH", "amount": "50.00"})
    assert coll.status_code == 201, coll.text
    ret = await client.post("/api/v1/sales/returns", json={
        "idempotency_key": key(), "invoice_id": cash_sale["id"], "reason": "Not needed",
        "items": [{"invoice_item_id": cash_sale["items"][0]["id"], "quantity_base": "15", "condition": "SALEABLE"}]})
    assert ret.status_code == 201, ret.text
    void_me = await sell(client, [line(data, "1")], [{"method": "CASH", "amount": "42.00"}])
    voided = await client.post(f"/api/v1/sales/invoices/{void_me['id']}/void", json={"reason": "Billed twice", "settlement": "REFUND"})
    assert voided.status_code == 200, voided.text

    cats = {c["code"]: c for c in (await client.get("/api/v1/expenses/categories")).json()}
    exp = await client.post("/api/v1/expenses", json={
        "idempotency_key": key(), "category_id": cats["ELECTRICITY"]["id"], "expense_date": str(TODAY),
        "amount": "1180.00", "gst_amount": "180.00", "payee": "MSEDCL", "payment_method": "BANK_TRANSFER", "reference": "EB-1"})
    assert exp.status_code == 201, exp.text
    wrong = await client.post("/api/v1/expenses", json={
        "idempotency_key": key(), "category_id": cats["MISC"]["id"], "expense_date": str(TODAY),
        "amount": "99.00", "payee": "Wrong entry", "payment_method": "CASH", "cash_source": "DRAWER"})
    assert wrong.status_code == 201, wrong.text
    assert (await client.post(f"/api/v1/expenses/{wrong.json()['id']}/reverse",
                              json={"reason": "Entered against the wrong payee"})).status_code == 200
    dep = await client.post(f"/api/v1/shifts/{shift['id']}/cash-movements", json={
        "movement_type": "PAID_OUT", "category": "BANK_DEPOSIT", "amount": "40.00", "reason": "Deposit", "reference": "SLIP-1"})
    assert dep.status_code == 201, dep.text
    safe = await client.post(f"/api/v1/shifts/{shift['id']}/cash-movements", json={
        "movement_type": "PAID_OUT", "category": "TRANSFER_TO_SAFE", "amount": "10.00", "reason": "To safe"})
    assert safe.status_code == 201
    expected = D((await client.get(f"/api/v1/shifts/{shift['id']}")).json()["expected_cash"])
    close = await client.post(f"/api/v1/shifts/{shift['id']}/close",
                              json={"counted_cash": str(expected - D("2.00")), "variance_note": "Coins short"})
    assert close.status_code == 200, close.text
    adj = await client.post("/api/v1/inventory/adjustments", json={
        "reason_code": "EXPIRY", "items": [{"medicine_id": data["medicine"]["id"], "batch_id": batch["id"],
                                            "physical_stock_base": str(D((await client.get(f"/api/v1/batches/{batch['id']}")).json()["current_stock_base"]) - 5)}]})
    assert adj.status_code == 201, adj.text

    b = await balances(client)
    assert b["5170"] == D("2.00")                                             # Cash shortage
    assert b["5150"] == D("10.00")                                            # 5 tablets x ₹2 expired
    assert b["5230"] == D("1000.00") and b["1164"] == D("216.00")             # Electricity net of GST
    assert b.get("5290", 0) == 0                                              # Reversed expense nets to zero
    assert b["1131"] == D("20.00")                                            # UPI at sale awaits settlement
    keys = await journals(test_engine)
    assert len(keys) == len(set(keys))
    for prefix in ("GOODS_RECEIPT:", "SUPPLIER_BILL:", "SUPPLIER_PAYMENT:", "SALE:", "CUSTOMER_RECEIPT:", "SALES_RETURN:",
                   "SALE_VOID:", "CUSTOMER_REFUND:", "EXPENSE:", "EXPENSE_REVERSAL:", "CASH_MOVEMENT:", "SHIFT_VARIANCE:",
                   "STOCK_ADJUSTMENT:"):
        assert any(k.startswith(prefix) for k in keys), prefix
    assert len([k for k in keys if k.startswith("CASH_MOVEMENT:")]) == 1     # Drawer→safe moves no money in the GL
    r = await recon(client)
    assert_clean(r)
    ar = next(c for c in r["controls"] if c["control"].startswith("Trade Receivables"))
    assert D(ar["gl"]) == D(ar["operational"]) and D(ar["gl"]) > 0


@pytest.mark.asyncio
async def test_customer_credit_return_and_payment_reversal(client: AsyncClient):
    data = await books(client)
    await receive_stock(client, data, "GL-B2")
    cust = await create_customer(client)
    inv = await sell(client, [line(data, "2")], [], customer_id=cust["id"])                 # Fully on credit
    pay = await client.post(f"/api/v1/customers/{cust['id']}/payments", json={
        "idempotency_key": key(), "method": "UPI", "amount": "30.00", "reference": "UPI-77"})
    assert pay.status_code == 201
    rev = await client.post(f"/api/v1/sales/payments/{pay.json()['id']}/reverse", json={"reason": "UPI never arrived"})
    assert rev.status_code == 200, rev.text
    ret = await client.post("/api/v1/sales/returns", json={
        "idempotency_key": key(), "invoice_id": inv["id"], "reason": "Damaged pack", "settlement": "CUSTOMER_CREDIT",
        "items": [{"invoice_item_id": inv["items"][0]["id"], "quantity_base": "15", "condition": "DAMAGED"}]})
    assert ret.status_code == 201, ret.text
    adj = await client.post(f"/api/v1/customers/{cust['id']}/adjustments", json={
        "invoice_id": inv["id"], "adjustment_type": "WRITE_OFF", "amount": "5.00", "reason": "Cannot recover small balance"})
    assert adj.status_code == 201, adj.text
    b = await balances(client)
    assert b.get("1131", 0) == 0                                              # Reversed UPI receipt
    assert b["5180"] == D("5.00") and b["5150"] > 0                           # Bad debt; damaged return written off
    assert_clean(await recon(client))


# ── Supplier side details ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bill_without_receipt_and_cancellation_reversal(client: AsyncClient, test_engine):
    data = await books(client)
    resp = await client.post("/api/v1/purchases/invoices", json={
        "supplier_id": data["supplier"]["id"], "invoice_number": "NOGRN-1", "invoice_date": str(TODAY),
        "received_date": str(TODAY), "supplier_declared_total": "200.00", "other_charges": "20.00",
        "items": [{"medicine_id": data["medicine"]["id"], "batch_number": "X1", "packaging_unit_id": data["strip_unit"]["id"],
                   "billed_qty_pack": "6.00", "purchase_rate": "30.00"}]})
    inv = resp.json()
    assert (await client.post(f"/api/v1/purchases/invoices/{inv['id']}/finalize", json={"acknowledge_warnings": True})).status_code == 200
    b = await balances(client)
    assert b["2115"] == D("180.00") and b["5110"] == D("20.00") and b["2110"] == D("-200.00")   # GRNI debit until received
    cancel = await client.post(f"/api/v1/purchases/invoices/{inv['id']}/cancel", json={"reason": "Supplier sent a revised bill"})
    assert cancel.status_code == 200, cancel.text
    b = await balances(client)
    assert b.get("2115", 0) == 0 and b.get("2110", 0) == 0
    async with async_sessionmaker(test_engine, class_=AsyncSession)() as db:
        original = (await db.execute(select(JournalEntry).where(JournalEntry.source_key == f"SUPPLIER_BILL:{inv['id']}"))).scalar_one()
        assert original.status == "REVERSED" and original.reversed_by_entry_id
    assert_clean(await recon(client))


@pytest.mark.asyncio
async def test_supplier_return_and_payment_reversal(client: AsyncClient):
    from tests.test_purchasing_fixes import return_line, return_payload
    data = await books(client)
    batch = await receive_stock(client, data, "GL-B3")
    bill = await bill_for(client, data, batch)
    grn = next(g for g in ((await client.get("/api/v1/purchases/receipts")).json()) if g["status"] == "POSTED")
    grn = (await client.get(f"/api/v1/purchases/receipts/{grn['id']}")).json()
    ret = await client.post("/api/v1/purchases/returns", json=return_payload(
        data["supplier"]["id"], [return_line(data["medicine"]["id"], batch["id"], data["strip_unit"]["id"], qty="2.00",
                                             grn_item_id=grn["items"][0]["id"], tax_rate_id=data["gst_12"]["id"])],
        grn_id=grn["id"], invoice_id=bill["id"]))
    assert ret.status_code == 201, ret.text
    assert (await client.post(f"/api/v1/purchases/returns/{ret.json()['id']}/post")).status_code == 200
    pay = await client.post("/api/v1/supplier-payments", json={
        "idempotency_key": key(), "supplier_id": data["supplier"]["id"], "payment_date": str(TODAY),
        "method": "CHEQUE", "reference": "CHQ-9", "amount": "100.00", "auto_allocate": True})
    assert pay.status_code == 201
    assert (await client.post(f"/api/v1/supplier-payments/{pay.json()['id']}/reverse",
                              json={"reason": "Cheque bounced at the bank"})).status_code == 200
    b = await balances(client)
    assert b["1150"] == D("240.00")                                           # 300 received - 60 returned at cost
    assert b.get("1121", 0) == 0                                              # Bounced cheque reversed
    assert_clean(await recon(client))


# ── Reliability ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_nothing_posts_before_setup_and_retry_backfills_once(client: AsyncClient, test_engine):
    data = await setup_pharmacy_and_catalogue(client)
    await receive_stock(client, data, "PRE-B1")
    await sell(client, [line(data, "1")], [{"method": "CASH", "amount": "42.00"}])
    assert await journals(test_engine) == []
    assert (await client.get("/api/v1/accounting/postings/reconciliation")).json()["set_up"] is False
    await books(client, data)
    r = await recon(client)
    assert {m["source_key"].split(":")[0] for m in r["missing"]} == {"GOODS_RECEIPT", "SALE"} and not r["ok"]
    first = (await client.post("/api/v1/accounting/postings/retry", json={})).json()
    assert first["summary"]["posted"] == 2
    second = (await client.post("/api/v1/accounting/postings/retry", json={})).json()
    assert second["summary"]["posted"] == 0
    assert len(await journals(test_engine, "SALE:")) == 1
    assert_clean(await recon(client))


@pytest.mark.asyncio
async def test_posting_failure_never_blocks_the_sale_and_is_retried(client: AsyncClient, test_engine):
    data = await books(client)
    await receive_stock(client, data, "FAIL-B1")
    periods = (await client.get("/api/v1/accounting/periods")).json()[0]["periods"]
    current = next(p for p in periods if p["start_date"] <= str(TODAY) <= p["end_date"])
    assert (await client.post(f"/api/v1/accounting/periods/{current['id']}/close")).status_code == 200
    sale = await sell(client, [line(data, "1")], [{"method": "CASH", "amount": "42.00"}])   # Still sells
    issues = (await client.get("/api/v1/accounting/postings/issues")).json()
    assert len(issues) == 1 and issues[0]["source_key"] == f"SALE:{sale['id']}" and "closed" in issues[0]["error"]
    r = await recon(client)
    assert not r["ok"] and r["open_issues"] and r["missing"]

    await add_user(client, "Pharmacist", "pharma1")
    await login_as(client, "pharma1", "Staff1234")
    assert (await client.post("/api/v1/accounting/postings/retry", json={})).status_code == 403
    await login(client)
    still = (await client.post("/api/v1/accounting/postings/retry", json={})).json()
    assert still["summary"]["failed"] == 1
    assert (await client.post(f"/api/v1/accounting/periods/{current['id']}/reopen",
                              json={"reason": "Post the sale that arrived after closing"})).status_code == 200
    done = (await client.post("/api/v1/accounting/postings/retry", json={"source_key": f"SALE:{sale['id']}"})).json()
    assert done["results"][f"SALE:{sale['id']}"] == "posted"
    async with async_sessionmaker(test_engine, class_=AsyncSession)() as db:
        issue = (await db.execute(select(GlPostingIssue))).scalar_one()
        assert issue.status == "RESOLVED" and issue.attempts == 2 and issue.journal_entry_id
    assert_clean(await recon(client))


@pytest.mark.asyncio
async def test_replays_do_not_duplicate_and_tampering_is_detected(client: AsyncClient, test_engine):
    data = await books(client)
    await receive_stock(client, data, "DUP-B1")
    body_key = key()
    first = await sell(client, [line(data, "1")], [{"method": "CASH", "amount": "42.00"}], idempotency_key=body_key)
    resp = await client.post("/api/v1/sales/invoices", json={
        "idempotency_key": body_key, "customer_id": None, "items": [line(data, "1")],
        "payments": [{"method": "CASH", "amount": "42.00"}]})
    assert resp.status_code in (200, 201) and resp.json()["id"] == first["id"]
    assert len(await journals(test_engine, "SALE:")) == 1
    cats = {c["code"]: c for c in (await client.get("/api/v1/expenses/categories")).json()}
    exp = (await client.post("/api/v1/expenses", json={
        "idempotency_key": key(), "category_id": cats["RENT"]["id"], "expense_date": str(TODAY), "amount": "500.00",
        "payee": "Landlord", "payment_method": "UPI", "reference": "R-1"})).json()
    async with async_sessionmaker(test_engine, class_=AsyncSession)() as db:
        await db.execute(update(Expense).where(Expense.id == exp["id"]).values(amount=Decimal("550.00")))
        await db.commit()
    r = await recon(client)
    assert [m["source_key"] for m in r["mismatched"]] == [f"EXPENSE:{exp['id']}"]
    again = (await client.post("/api/v1/accounting/postings/retry", json={"source_key": f"EXPENSE:{exp['id']}"})).json()
    assert again["results"][f"EXPENSE:{exp['id']}"] == "failed"        # Different amounts: never silently re-posted
    async with async_sessionmaker(test_engine, class_=AsyncSession)() as db:
        n = (await db.execute(select(func.count(JournalEntry.id)).where(JournalEntry.source_key == f"EXPENSE:{exp['id']}"))).scalar_one()
    assert n == 1


@pytest.mark.asyncio
async def test_cutover_summary_is_read_only_and_books_stay_unset(client: AsyncClient, test_engine):
    data = await setup_pharmacy_and_catalogue(client)
    await receive_stock(client, data, "CUT-B1")
    cust = await create_customer(client)
    await sell(client, [line(data, "1")], [], customer_id=cust["id"])
    s = (await client.get("/api/v1/accounting/historical-summary")).json()
    assert s["customer_balances"][0]["balance"] == "42.00"
    assert D(s["goods_received_not_billed"]["unbilled_value"]) == D("300.00")
    assert s["balances_now"]["stock_value_at_cost"] == "270.00"     # 300 received - 15 tablets x ₹2 sold
    assert (await client.get("/api/v1/accounting/status")).json()["is_set_up"] is False
    assert await journals(test_engine) == []


# ── Eligibility, advances, credit voids, reversal audit ────────────────────────

@pytest.mark.asyncio
async def test_drafts_pending_and_cancelled_documents_never_post(client: AsyncClient, test_engine):
    from tests.test_purchasing_fixes import grn_line
    data = await books(client)
    draft_grn = await client.post("/api/v1/purchases/receipts", json={
        "supplier_id": data["supplier"]["id"], "receipt_date": str(TODAY),
        "items": [grn_line(data["medicine"]["id"], data["strip_unit"]["id"], batch="DRAFT-G")]})
    assert draft_grn.status_code == 201
    bill = (await client.post("/api/v1/purchases/invoices", json={
        "supplier_id": data["supplier"]["id"], "invoice_number": "DRAFT-BILL", "invoice_date": str(TODAY),
        "received_date": str(TODAY), "supplier_declared_total": "60.00",
        "items": [{"medicine_id": data["medicine"]["id"], "batch_number": "Z", "packaging_unit_id": data["strip_unit"]["id"],
                   "billed_qty_pack": "2.00", "purchase_rate": "30.00"}]})).json()
    assert (await client.post(f"/api/v1/purchases/invoices/{bill['id']}/cancel",
                              json={"reason": "Entered by mistake, never finalised"})).status_code == 200
    cats = {c["code"]: c for c in (await client.get("/api/v1/expenses/categories")).json()}
    await add_user(client, "Pharmacist", "pharma1")
    await login_as(client, "pharma1", "Staff1234")
    pending = (await client.post("/api/v1/expenses", json={
        "idempotency_key": key(), "category_id": cats["CLEANING"]["id"], "expense_date": str(TODAY), "amount": "150.00",
        "payee": "Cleaner", "payment_method": "CASH", "cash_source": "OFFICE_CASH"})).json()
    rejected = (await client.post("/api/v1/expenses", json={
        "idempotency_key": key(), "category_id": cats["MISC"]["id"], "expense_date": str(TODAY), "amount": "70.00",
        "payee": "Unknown", "payment_method": "CASH", "cash_source": "OFFICE_CASH"})).json()
    await login(client)
    assert (await client.post(f"/api/v1/expenses/{rejected['id']}/reject", json={"note": "No bill"})).status_code == 200
    assert pending["status"] == "PENDING_APPROVAL"
    assert await journals(test_engine) == []                          # Nothing eligible yet
    assert_clean(await recon(client))
    assert (await client.post(f"/api/v1/expenses/{pending['id']}/approve", json={})).status_code == 200
    assert await journals(test_engine) == [f"EXPENSE:{pending['id']}"]


@pytest.mark.asyncio
async def test_advance_credit_application_and_void_kept_as_credit(client: AsyncClient, test_engine):
    data = await books(client)
    await receive_stock(client, data, "ADV-B1")
    cust = await create_customer(client)
    adv = await client.post(f"/api/v1/customers/{cust['id']}/payments", json={
        "idempotency_key": key(), "method": "CASH", "amount": "100.00"})              # Nothing owed: an advance
    assert adv.status_code == 201
    b = await balances(client)
    assert b["1140"] == D("-100.00") and b["1110"] == D("100.00")       # Advance = credit balance on AR
    inv = await sell(client, [line(data, "1")], [], customer_id=cust["id"])          # ₹42 on credit
    before = await journals(test_engine)
    applied = await client.post(f"/api/v1/customers/{cust['id']}/apply-credit", json={})
    assert applied.status_code == 200, applied.text
    assert await journals(test_engine) == before                       # Applying credit moves nothing in the GL
    paid = await sell(client, [line(data, "1")], [{"method": "UPI", "amount": "42.00", "reference": "U9"}], customer_id=cust["id"])
    v = await client.post(f"/api/v1/sales/invoices/{paid['id']}/void", json={"reason": "Wrong customer", "settlement": "CUSTOMER_CREDIT"})
    assert v.status_code == 200, v.text
    b = await balances(client)
    assert b["1131"] == D("42.00")                                     # The UPI money stays with the shop
    assert b["1140"] == D("-100.00")                                   # -100 advance + 42 sale - 42 voided-sale credit
    r = await recon(client)
    assert_clean(r)
    ar = next(c for c in r["controls"] if c["control"].startswith("Trade Receivables"))
    assert D(ar["gl"]) == D(ar["operational"]) == D("-100.00")
    keys = await journals(test_engine)
    assert [k.split(":")[0] for k in keys].count("CUSTOMER_REFUND") == 0


@pytest.mark.asyncio
async def test_reversals_are_linked_and_audited_and_retry_is_idempotent(client: AsyncClient, test_engine):
    from app.models.auth import AuditLog
    data = await books(client)
    cats = {c["code"]: c for c in (await client.get("/api/v1/expenses/categories")).json()}
    exp = (await client.post("/api/v1/expenses", json={
        "idempotency_key": key(), "category_id": cats["RENT"]["id"], "expense_date": str(TODAY), "amount": "800.00",
        "payee": "Landlord", "payment_method": "BANK_TRANSFER", "reference": "RENT-OCT"})).json()
    assert (await client.post(f"/api/v1/expenses/{exp['id']}/reverse", json={"reason": "Paid by the owner personally"})).status_code == 200
    async with async_sessionmaker(test_engine, class_=AsyncSession)() as db:
        orig = (await db.execute(select(JournalEntry).where(JournalEntry.source_key == f"EXPENSE:{exp['id']}"))).scalar_one()
        rev = (await db.execute(select(JournalEntry).where(JournalEntry.source_key == f"EXPENSE_REVERSAL:{exp['id']}"))).scalar_one()
        assert orig.status == "REVERSED" and orig.reversed_by_entry_id == rev.id and rev.reversal_of_id == orig.id
        assert rev.total_debit == orig.total_debit and rev.created_by and rev.posted_at
        assert (await db.execute(select(func.count(AuditLog.id)).where(AuditLog.action == "expense.reverse"))).scalar_one() == 1
    b = await balances(client)
    assert b.get("5210", 0) == 0 and b.get("1121", 0) == 0
    for _ in range(2):
        res = (await client.post("/api/v1/accounting/postings/retry", json={})).json()
        assert res["summary"]["posted"] == 0 and res["summary"]["failed"] == 0
    assert len(await journals(test_engine)) == 2


@pytest.mark.asyncio
async def test_supplier_credit_note_reduces_payable_and_posts(client: AsyncClient, test_engine):
    data = await books(client)
    batch = await receive_stock(client, data, "CN-B1")
    bill = await bill_for(client, data, batch)                                 # ₹336 owed
    body = {"idempotency_key": key(), "supplier_id": data["supplier"]["id"], "credit_note_number": "CN/77",
            "note_date": str(TODAY), "invoice_id": bill["id"], "reason_code": "RATE_DIFFERENCE",
            "taxable_amount": "50.00", "cgst_amount": "3.00", "sgst_amount": "3.00"}
    too_big = await client.post("/api/v1/supplier-credit-notes", json={**body, "idempotency_key": key(), "taxable_amount": "400.00"})
    assert too_big.status_code == 422
    bad_tax = await client.post("/api/v1/supplier-credit-notes", json={**body, "idempotency_key": key(), "igst_amount": "1.00"})
    assert bad_tax.status_code == 422
    cn = await client.post("/api/v1/supplier-credit-notes", json=body)
    assert cn.status_code == 201, cn.text
    assert D(cn.json()["total_amount"]) == D("56.00")
    dup = await client.post("/api/v1/supplier-credit-notes", json={**body, "idempotency_key": key(), "credit_note_number": "cn 77"})
    assert dup.status_code == 409
    inv = (await client.get(f"/api/v1/purchases/invoices/{bill['id']}")).json()
    assert D(inv["outstanding_amount"]) == D("280.00")
    b = await balances(client)
    assert b["2110"] == D("-280.00") and b["5110"] == D("-50.00") and b["1164"] == D("30.00")
    cancel_bill = await client.post(f"/api/v1/purchases/invoices/{bill['id']}/cancel", json={"reason": "Trying with a credit note open"})
    assert cancel_bill.status_code == 409 and "credit notes" in cancel_bill.json()["detail"]
    assert_clean(await recon(client))
    c = await client.post(f"/api/v1/supplier-credit-notes/{cn.json()['id']}/cancel", json={"reason": "Supplier withdrew the credit note"})
    assert c.status_code == 200 and c.json()["status"] == "CANCELLED"
    b = await balances(client)
    assert b["2110"] == D("-336.00") and b.get("5110", 0) == 0
    assert D((await client.get(f"/api/v1/purchases/invoices/{bill['id']}")).json()["outstanding_amount"]) == D("336.00")
    again = await client.post("/api/v1/supplier-credit-notes", json={**body, "idempotency_key": key()})   # Number freed
    assert again.status_code == 201
    assert_clean(await recon(client))
