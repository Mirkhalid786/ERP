"""Phase 5A: general ledger foundation."""
from datetime import timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import today_local
from app.models.accounting import JournalEntry, JournalLine
from app.models.auth import AuditLog, User
from app.models.sales import CustomerLedgerEntry
from app.services import ledger
from tests.conftest import login, setup_pharmacy
from tests.sales_helpers import D, add_user, create_customer, key, login_as

START = today_local().replace(day=1)


def factory(test_engine):
    return async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)


async def books(client: AsyncClient) -> dict:
    await setup_pharmacy(client)
    await login(client)
    resp = await client.post("/api/v1/accounting/setup", json={"books_start_date": str(START)})
    assert resp.status_code == 201, resp.text
    return {a["system_key"] or a["code"]: a for a in (await client.get("/api/v1/accounting/accounts")).json()}


def jline(account, debit=0, credit=0, **extra):
    return {"account_id": account["id"], "debit": str(debit), "credit": str(credit), **extra}


async def post_journal(client, lines, entry_date=None, narration="Test journal", expect=200, **extra):
    draft = await client.post("/api/v1/accounting/journals", json={
        "idempotency_key": key(), "entry_date": str(entry_date or today_local()), "narration": narration,
        "lines": lines, **extra})
    assert draft.status_code == 201, draft.text
    posted = await client.post(f"/api/v1/accounting/journals/{draft.json()['id']}/post")
    assert posted.status_code == expect, posted.text
    return posted.json() if expect == 200 else (draft.json(), posted.json())


async def tb(client):
    return (await client.get("/api/v1/accounting/trial-balance")).json()


# ── Setup and chart ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_setup_seeds_chart_and_periods(client: AsyncClient):
    acc = await books(client)
    assert ledger.REQUIRED_SYSTEM_KEYS <= set(acc)
    assert acc["AR"]["party_type"] == "CUSTOMER" and acc["AR"]["is_control"] is True
    assert acc["DRAWINGS"]["normal_balance"] == "DEBIT" and acc["SALES_RETURNS"]["normal_balance"] == "DEBIT"
    status = (await client.get("/api/v1/accounting/status")).json()
    assert status["is_set_up"] and status["missing_system_accounts"] == []
    assert (await client.post("/api/v1/accounting/setup", json={"books_start_date": str(START)})).status_code == 409

    years = (await client.get("/api/v1/accounting/periods")).json()
    periods = years[0]["periods"]
    assert len(periods) == 12 and years[0]["start_date"].endswith("-04-01")
    assert all(p["status"] == ("OPEN" if p["start_date"] >= str(START) else "PRE_OPENING") for p in periods)


@pytest.mark.asyncio
async def test_account_rules(client: AsyncClient):
    acc = await books(client)
    child = await client.post("/api/v1/accounting/accounts", json={
        "code": "1122", "name": "Bank - Savings", "account_type": "ASSET", "parent_id": acc["1120"]["id"]})
    assert child.status_code == 201 and child.json()["normal_balance"] == "DEBIT"
    wrong_type = await client.post("/api/v1/accounting/accounts", json={
        "code": "1123", "name": "Odd", "account_type": "EXPENSE", "parent_id": acc["1120"]["id"]})
    assert wrong_type.status_code == 422
    under_leaf = await client.post("/api/v1/accounting/accounts", json={
        "code": "1124", "name": "Odd", "account_type": "ASSET", "parent_id": acc["CASH"]["id"]})
    assert under_leaf.status_code == 422
    dup = await client.post("/api/v1/accounting/accounts", json={"code": "1122", "name": "Dup", "account_type": "ASSET"})
    assert dup.status_code == 409

    assert (await client.put(f"/api/v1/accounting/accounts/{acc['CASH']['id']}", json={"is_active": False})).status_code == 409
    assert (await client.delete(f"/api/v1/accounting/accounts/{acc['CASH']['id']}")).status_code == 409

    await post_journal(client, [jline(child.json(), debit=500), jline(acc["CAPITAL"], credit=500)])
    used = child.json()["id"]
    assert (await client.put(f"/api/v1/accounting/accounts/{used}", json={"is_active": False})).status_code == 409
    assert (await client.put(f"/api/v1/accounting/accounts/{used}", json={"code": "1199"})).status_code == 409
    assert (await client.delete(f"/api/v1/accounting/accounts/{used}")).status_code == 409
    renamed = await client.put(f"/api/v1/accounting/accounts/{used}", json={"name": "Bank - Savings (SBI)"})
    assert renamed.status_code == 200

    unused = (await client.post("/api/v1/accounting/accounts", json={"code": "5295", "name": "Unused", "account_type": "EXPENSE",
                                                                    "parent_id": acc["5200"]["id"]})).json()
    assert (await client.put(f"/api/v1/accounting/accounts/{unused['id']}", json={"is_active": False})).status_code == 200
    assert (await client.delete(f"/api/v1/accounting/accounts/{unused['id']}")).status_code == 204


# ── Posting rules ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_balanced_journal_posts_and_trial_balance_balances(client: AsyncClient):
    acc = await books(client)
    entry = await post_journal(client, [jline(acc["CASH"], debit="10000.50"), jline(acc["CAPITAL"], credit="10000.50")],
                               narration="Owner brings capital")
    assert entry["status"] == "POSTED" and entry["entry_number"].startswith("JV/")
    assert D(entry["total_debit"]) == D(entry["total_credit"]) == D("10000.50")
    await post_journal(client, [jline(acc["5210"], debit=3000), jline(acc["CASH"], credit=3000)], narration="Rent")
    t = await tb(client)
    assert t["is_balanced"] and D(t["totals"]["closing_debit"]) == D(t["totals"]["closing_credit"])
    rows = {r["code"]: r for r in t["rows"]}
    assert D(rows["1110"]["closing_debit"]) == D("7000.50") and D(rows["3100"]["closing_credit"]) == D("10000.50")
    integrity = (await client.get("/api/v1/accounting/integrity")).json()
    assert integrity["ok"] and integrity["entries_checked"] == 2


@pytest.mark.asyncio
async def test_invalid_journals_are_rejected(client: AsyncClient):
    acc = await books(client)
    _, err = await post_journal(client, [jline(acc["CASH"], debit=100), jline(acc["CAPITAL"], credit=90)], expect=422)
    assert "differ by ₹10.00" in err["detail"]

    both = await client.post("/api/v1/accounting/journals", json={"entry_date": str(today_local()), "narration": "Bad",
                              "lines": [jline(acc["CASH"], debit=5, credit=5), jline(acc["CAPITAL"], credit=0)]})
    assert both.status_code == 422
    one = await client.post("/api/v1/accounting/journals", json={"entry_date": str(today_local()), "narration": "Bad",
                             "lines": [jline(acc["CASH"], debit=5)]})
    assert one.status_code == 422
    neg = await client.post("/api/v1/accounting/journals", json={"entry_date": str(today_local()), "narration": "Bad",
                             "lines": [jline(acc["CASH"], debit=-5), jline(acc["CAPITAL"], credit=-5)]})
    assert neg.status_code == 422

    for bad_account, message in ((acc["1100"], "group"), (acc["INVENTORY"], "control account"), (acc["AR"], "control account")):
        _, err = await post_journal(client, [jline(bad_account, debit=50), jline(acc["CAPITAL"], credit=50)], expect=422)
        assert message in err["detail"]
    early = await client.post("/api/v1/accounting/journals", json={
        "entry_date": str(START - timedelta(days=1)), "narration": "Before the books",
        "lines": [jline(acc["CASH"], debit=50), jline(acc["CAPITAL"], credit=50)]})
    assert early.status_code == 409 and "before the books' opening date" in early.json()["detail"]
    assert (await tb(client))["rows"] == []


@pytest.mark.asyncio
async def test_duplicate_source_and_draft_idempotency(client: AsyncClient, test_engine):
    acc = await books(client)
    k = key()
    body = {"idempotency_key": k, "entry_date": str(today_local()), "narration": "Same click twice",
            "lines": [jline(acc["CASH"], debit=100), jline(acc["CAPITAL"], credit=100)]}
    a = await client.post("/api/v1/accounting/journals", json=body)
    b = await client.post("/api/v1/accounting/journals", json=body)
    assert a.json()["id"] == b.json()["id"]
    assert (await client.post(f"/api/v1/accounting/journals/{a.json()['id']}/post")).status_code == 200
    assert (await client.post(f"/api/v1/accounting/journals/{a.json()['id']}/post")).status_code == 409

    async with factory(test_engine)() as db:
        user = (await db.execute(select(User))).scalars().first()
        lines = [ledger.LineIn(acc["CASH"]["id"], debit=Decimal("75")), ledger.LineIn(acc["CAPITAL"]["id"], credit=Decimal("75"))]
        first, replay1 = await ledger.post_entry(db, user, entry_date=today_local(), narration="Event", lines=lines,
                                                  source_type="TEST", source_id="1", source_key="TEST:1")
        second, replay2 = await ledger.post_entry(db, user, entry_date=today_local(), narration="Event", lines=lines,
                                                   source_type="TEST", source_id="1", source_key="TEST:1")
        assert first.id == second.id and replay1 is False and replay2 is True
        changed = [ledger.LineIn(acc["CASH"]["id"], debit=Decimal("80")), ledger.LineIn(acc["CAPITAL"]["id"], credit=Decimal("80"))]
        with pytest.raises(Exception) as exc:
            await ledger.post_entry(db, user, entry_date=today_local(), narration="Event", lines=changed,
                                    source_type="TEST", source_id="1", source_key="TEST:1")
        assert getattr(exc.value, "status_code", None) == 409
        await db.commit()
        count = (await db.execute(select(func.count(JournalEntry.id)).where(JournalEntry.source_key == "TEST:1"))).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_failure_during_posting_leaves_nothing(client: AsyncClient, test_engine, monkeypatch):
    acc = await books(client)
    draft = (await client.post("/api/v1/accounting/journals", json={
        "entry_date": str(today_local()), "narration": "Will fail",
        "lines": [jline(acc["CASH"], debit=40), jline(acc["CAPITAL"], credit=40)]})).json()

    async def boom(*args, **kwargs):
        raise RuntimeError("simulated crash while numbering")

    monkeypatch.setattr(ledger, "_journal_number", boom)
    with pytest.raises(RuntimeError):
        await client.post(f"/api/v1/accounting/journals/{draft['id']}/post")
    monkeypatch.undo()
    async with factory(test_engine)() as db:
        entry = (await db.execute(select(JournalEntry).where(JournalEntry.id == draft["id"]))).scalar_one()
        assert entry.status == "DRAFT" and entry.entry_number is None
    assert (await tb(client))["rows"] == []
    assert (await client.post(f"/api/v1/accounting/journals/{draft['id']}/post")).status_code == 200


# ── Reversal ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reversal_preserves_original_and_audit(client: AsyncClient, test_engine):
    acc = await books(client)
    original = await post_journal(client, [jline(acc["5230"], debit=1200), jline(acc["CASH"], credit=1200)],
                                  narration="Electricity bill entered twice")
    rev = await client.post(f"/api/v1/accounting/journals/{original['id']}/reverse", json={"reason": "Duplicate entry"})
    assert rev.status_code == 200, rev.text
    reversal = rev.json()
    assert reversal["source_type"] == "REVERSAL" and reversal["reversal_of_number"] == original["entry_number"]
    assert {(l["account_id"], l["debit"], l["credit"]) for l in reversal["lines"]} == {
        (l["account_id"], l["credit"], l["debit"]) for l in original["lines"]}

    again = (await client.get(f"/api/v1/accounting/journals/{original['id']}")).json()
    assert again["status"] == "REVERSED" and again["reversed_by_number"] == reversal["entry_number"]
    assert again["lines"] == original["lines"]                         # unchanged
    assert (await tb(client))["rows"][0]["closing_debit"] == "0.00"
    assert (await client.post(f"/api/v1/accounting/journals/{original['id']}/reverse", json={"reason": "Again!"})).status_code == 409
    assert (await client.post(f"/api/v1/accounting/journals/{reversal['id']}/reverse", json={"reason": "Undo undo"})).status_code == 409
    assert (await client.delete(f"/api/v1/accounting/journals/{original['id']}")).status_code == 409
    assert (await client.get("/api/v1/accounting/integrity")).json()["ok"]

    async with factory(test_engine)() as db:
        actions = [a for (a,) in (await db.execute(select(AuditLog.action).where(AuditLog.entity_type == "journal_entry")))]
    assert actions.count("journal.post") == 2 and "journal.reverse" in actions


# ── Periods ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_closed_period_blocks_posting_until_authorised_reopen(client: AsyncClient):
    acc = await books(client)
    periods = (await client.get("/api/v1/accounting/periods")).json()[0]["periods"]
    current = next(p for p in periods if p["start_date"] <= str(today_local()) <= p["end_date"])
    draft = (await client.post("/api/v1/accounting/journals", json={
        "entry_date": str(today_local()), "narration": "Late entry",
        "lines": [jline(acc["CASH"], debit=10), jline(acc["CAPITAL"], credit=10)]})).json()
    blocked = await client.post(f"/api/v1/accounting/periods/{current['id']}/close")
    assert blocked.status_code == 409 and "draft" in blocked.json()["detail"]
    assert (await client.delete(f"/api/v1/accounting/journals/{draft['id']}")).status_code == 204

    assert (await client.post(f"/api/v1/accounting/periods/{current['id']}/close")).status_code == 200
    refused = await client.post("/api/v1/accounting/journals", json={
        "entry_date": str(today_local()), "narration": "Draft into closed period",
        "lines": [jline(acc["CASH"], debit=10), jline(acc["CAPITAL"], credit=10)]})
    assert refused.status_code == 409 and "closed" in refused.json()["detail"]
    short = await client.post(f"/api/v1/accounting/periods/{current['id']}/reopen", json={"reason": "no"})
    assert short.status_code == 422
    ok = await client.post(f"/api/v1/accounting/periods/{current['id']}/reopen", json={"reason": "Missed rent bill, approved by owner"})
    assert ok.status_code == 200
    await post_journal(client, [jline(acc["CASH"], debit=10), jline(acc["CAPITAL"], credit=10)])
    period = next(p for p in (await client.get("/api/v1/accounting/periods")).json()[0]["periods"] if p["id"] == current["id"])
    assert period["reopen_reason"] == "Missed rent bill, approved by owner" and period["reopened_by"] == "Admin User"


# ── Permissions ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_non_admin_roles_cannot_touch_accounting(client: AsyncClient, second_client: AsyncClient):
    acc = await books(client)
    entry = await post_journal(client, [jline(acc["CASH"], debit=10), jline(acc["CAPITAL"], credit=10)])
    await add_user(client, "Pharmacist", "pharma9")
    await add_user(client, "Cashier", "cash9")
    for username in ("pharma9", "cash9"):
        await login_as(second_client, username, "Staff1234")
        for method, url, body in (
            ("get", "/api/v1/accounting/trial-balance", None),
            ("get", "/api/v1/accounting/accounts", None),
            ("post", "/api/v1/accounting/journals", {"entry_date": str(today_local()), "narration": "x",
                                                     "lines": [jline(acc["CASH"], debit=1), jline(acc["CAPITAL"], credit=1)]}),
            ("post", f"/api/v1/accounting/journals/{entry['id']}/reverse", {"reason": "Not allowed"}),
            ("post", "/api/v1/accounting/periods/1/close", None),
            ("post", "/api/v1/accounting/accounts", {"code": "9", "name": "Hack", "account_type": "ASSET"}),
        ):
            resp = await getattr(second_client, method)(url, **({"json": body} if body is not None else {}))
            assert resp.status_code == 403, (username, url, resp.text)


@pytest.mark.asyncio
async def test_export_needs_permission_and_is_audited(client: AsyncClient, test_engine):
    acc = await books(client)
    await post_journal(client, [jline(acc["CASH"], debit=10), jline(acc["CAPITAL"], credit=10)])
    csv = await client.get("/api/v1/accounting/trial-balance?format=csv")
    assert csv.status_code == 200 and csv.text.startswith("account_id,code,name")
    async with factory(test_engine)() as db:
        assert (await db.execute(select(func.count(AuditLog.id)).where(AuditLog.action == "accounting.export"))).scalar_one() == 1


# ── Opening balances ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_opening_balances_preview_reconcile_and_post(client: AsyncClient):
    acc = await books(client)
    customer = await create_customer(client)
    supplier = (await client.post("/api/v1/suppliers", json={"name": "Opening Supplier", "state_code": "27"})).json()
    day = str(START - timedelta(days=10))
    lines = [
        jline(acc["CASH"], debit=12000),
        jline(acc["BANK"], debit=50000),
        jline(acc["INVENTORY"], debit=30000),
        {**jline(acc["AR"], debit=2500), "party_type": "CUSTOMER", "party_id": customer["id"],
         "document_number": "KHATA-17", "document_date": day, "due_date": str(START + timedelta(days=5))},
        {**jline(acc["AP"], credit=8000), "party_type": "SUPPLIER", "party_id": supplier["id"],
         "document_number": "CIP/2026/551", "document_date": day},
        jline(acc["CAPITAL"], credit=80000),
    ]
    batch = await client.post("/api/v1/accounting/opening-balances", json={
        "source_description": "Closing trial balance from previous software, certified by CA", "lines": lines})
    assert batch.status_code == 201, batch.text
    b = batch.json()
    p = b["preview"]
    assert D(p["total_debit"]) == D("94500.00") and D(p["total_credit"]) == D("88000.00")
    assert any("differ by ₹6500.00" in e for e in p["errors"]) and p["can_post"] is False
    inventory = next(r for r in p["reconciliation"] if r["item"] == "Inventory")
    assert D(inventory["erp"]) == D("0.00") and D(inventory["difference"]) == D("30000.00")
    assert any("Opening inventory" in w for w in p["warnings"])
    assert (await client.post(f"/api/v1/accounting/opening-balances/{b['id']}/post", json={})).status_code == 422

    # Lump-sum receivable (no document) and an income account are refused
    bad = await client.put(f"/api/v1/accounting/opening-balances/{b['id']}", json={
        "source_description": "Retry", "lines": [
            {**jline(acc["AR"], debit=100), "party_type": "CUSTOMER", "party_id": customer["id"]},
            jline(acc["SALES"], credit=100)]})
    errors = bad.json()["preview"]["errors"]
    assert any("not a lump sum" in e for e in errors) and any("income/expense" in e for e in errors)

    # Fix the capital figure; acknowledge the inventory warning; post
    lines[-1] = jline(acc["CAPITAL"], credit=86500)
    fixed = await client.put(f"/api/v1/accounting/opening-balances/{b['id']}", json={
        "source_description": "Closing trial balance from previous software, certified by CA", "lines": lines})
    assert fixed.json()["preview"]["errors"] == []
    needs_ack = await client.post(f"/api/v1/accounting/opening-balances/{b['id']}/post", json={})
    assert needs_ack.status_code == 422 and "acknowledge" in needs_ack.json()["detail"]
    posted = await client.post(f"/api/v1/accounting/opening-balances/{b['id']}/post", json={"acknowledge_warnings": True})
    assert posted.status_code == 200, posted.text
    entry = (await client.get(f"/api/v1/accounting/journals/{posted.json()['journal_entry_id']}")).json()
    assert entry["source_type"] == "OPENING" and entry["entry_date"] == str(START)
    ar_line = next(l for l in entry["lines"] if l["account_id"] == acc["AR"]["id"])
    assert ar_line["party_name"] == "Ramesh Kumar" and "KHATA-17" in ar_line["description"]

    assert (await client.put(f"/api/v1/accounting/opening-balances/{b['id']}", json={
        "source_description": "Change after posting", "lines": lines})).status_code == 409
    assert (await client.post(f"/api/v1/accounting/opening-balances/{b['id']}/post", json={"acknowledge_warnings": True})).status_code == 409
    assert (await client.post("/api/v1/accounting/opening-balances", json={"source_description": "Second", "lines": lines})).status_code == 409
    t = await tb(client)
    assert t["is_balanced"] and D(t["totals"]["closing_debit"]) == D("94500.00")
    ledger_ar = (await client.get(f"/api/v1/accounting/ledger/{acc['AR']['id']}?party_type=CUSTOMER&party_id={customer['id']}")).json()
    assert D(ledger_ar["closing_balance"]) == D("2500.00")


@pytest.mark.asyncio
async def test_opening_difference_only_to_suspense_when_explicit(client: AsyncClient):
    acc = await books(client)
    body = {"source_description": "Partial figures from old books", "post_difference_to_suspense": True,
            "lines": [jline(acc["CASH"], debit=1000), jline(acc["CAPITAL"], credit=900)]}
    b = (await client.post("/api/v1/accounting/opening-balances", json=body)).json()
    assert b["preview"]["errors"] == [] and any("Suspense" in w for w in b["preview"]["warnings"])
    posted = await client.post(f"/api/v1/accounting/opening-balances/{b['id']}/post", json={"acknowledge_warnings": True})
    entry = (await client.get(f"/api/v1/accounting/journals/{posted.json()['journal_entry_id']}")).json()
    suspense = next(l for l in entry["lines"] if l["account_id"] == acc["OPENING_DIFFERENCE"]["id"])
    assert D(suspense["credit"]) == D("100.00") and "UNRESOLVED" in suspense["description"]


@pytest.mark.asyncio
async def test_opening_refuses_parties_already_in_erp(client: AsyncClient, test_engine):
    acc = await books(client)
    customer = await create_customer(client)
    async with factory(test_engine)() as db:   # ERP udhaar recorded before the books' opening date
        user = (await db.execute(select(User))).scalars().first()
        entry = CustomerLedgerEntry(pharmacy_id=user.pharmacy_id, customer_id=customer["id"], entry_type="INVOICE",
                                    debit=Decimal("500"), credit=Decimal("0"), reference="OLD", description="Old bill",
                                    created_by=user.id)
        db.add(entry)
        await db.flush()
        from datetime import datetime, timezone
        await db.execute(update(CustomerLedgerEntry).where(CustomerLedgerEntry.id == entry.id)
                         .values(created_at=datetime(START.year, START.month, 1, tzinfo=timezone.utc) - timedelta(days=3)))
        await db.commit()
    b = (await client.post("/api/v1/accounting/opening-balances", json={
        "source_description": "Khata balances", "lines": [
            {**jline(acc["AR"], debit=500), "party_type": "CUSTOMER", "party_id": customer["id"],
             "document_number": "OLD", "document_date": str(START - timedelta(days=3))},
            jline(acc["CAPITAL"], credit=500)]})).json()
    assert any("already has ERP ledger activity" in e for e in b["preview"]["errors"])


# ── Integrity ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_integrity_check_detects_tampering(client: AsyncClient, test_engine):
    acc = await books(client)
    entry = await post_journal(client, [jline(acc["CASH"], debit=250), jline(acc["CAPITAL"], credit=250)])
    assert (await client.get("/api/v1/accounting/integrity")).json()["ok"]
    async with factory(test_engine)() as db:     # Someone edits the database directly
        await db.execute(update(JournalLine).where(JournalLine.entry_id == entry["id"], JournalLine.line_no == 1)
                         .values(debit=Decimal("200")))
        await db.commit()
    report = (await client.get("/api/v1/accounting/integrity")).json()
    assert not report["ok"]
    problems = " ".join(p["problem"] for p in report["problems"])
    assert "Unbalanced" in problems and "integrity hash" in problems
