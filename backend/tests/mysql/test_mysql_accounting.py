"""Phase 5A ledger under real MySQL concurrency (row locks, unique indexes, CHECK constraints)."""
import asyncio
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core.clock import today_local
from app.models.accounting import JournalEntry
from app.models.auth import User
from app.services import ledger
from tests.conftest import VALID_SETUP_PAYLOAD
from tests.mysql.conftest import pytestmark  # noqa: F401  (skip without TEST_MYSQL_URL)
from tests.sales_helpers import key

START = today_local().replace(day=1)


async def books(client: AsyncClient) -> dict:
    assert (await client.post("/api/v1/setup", json=VALID_SETUP_PAYLOAD)).status_code == 201
    assert (await client.post("/api/v1/auth/login", json={"username": "admin", "password": "Admin123"})).status_code == 200
    assert (await client.post("/api/v1/accounting/setup", json={"books_start_date": str(START)})).status_code == 201
    return {a["system_key"] or a["code"]: a for a in (await client.get("/api/v1/accounting/accounts")).json()}


def jl(account, debit=0, credit=0):
    return {"account_id": account["id"], "debit": str(debit), "credit": str(credit)}


async def draft(client, acc, amount="100"):
    resp = await client.post("/api/v1/accounting/journals", json={
        "idempotency_key": key(), "entry_date": str(today_local()), "narration": "Concurrency test",
        "lines": [jl(acc["CASH"], debit=amount), jl(acc["CAPITAL"], credit=amount)]})
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_same_draft_posted_twice_concurrently_posts_once(mysql_client: AsyncClient):
    acc = await books(mysql_client)
    d = await draft(mysql_client, acc)
    results = await asyncio.gather(*[mysql_client.post(f"/api/v1/accounting/journals/{d['id']}/post") for _ in range(3)])
    assert sorted(r.status_code for r in results) == [200, 409, 409], [r.text for r in results]
    tb = (await mysql_client.get("/api/v1/accounting/trial-balance")).json()
    assert tb["totals"]["closing_debit"] == "100.00" and tb["is_balanced"]


@pytest.mark.asyncio
async def test_same_source_key_from_two_sessions_posts_once(mysql_client: AsyncClient):
    acc = await books(mysql_client)
    factory = mysql_client.session_factory

    async def attempt():
        async with factory() as db:
            user = (await db.execute(select(User).where(User.username == "admin"))).scalar_one()
            lines = [ledger.LineIn(acc["CASH"]["id"], debit=Decimal("55")), ledger.LineIn(acc["CAPITAL"]["id"], credit=Decimal("55"))]
            try:
                entry, replayed = await ledger.post_entry(db, user, entry_date=today_local(), narration="Event",
                                                          lines=lines, source_type="TEST", source_id="9", source_key="TEST:9")
                await db.commit()
                return "replayed" if replayed else "posted"
            except ledger.DuplicateSource:
                await db.rollback()
                return "duplicate-blocked"

    outcomes = await asyncio.gather(*[attempt() for _ in range(4)])
    assert outcomes.count("posted") == 1, outcomes
    async with factory() as db:
        n = (await db.execute(select(func.count(JournalEntry.id)).where(JournalEntry.source_key == "TEST:9"))).scalar_one()
    assert n == 1


@pytest.mark.asyncio
async def test_concurrent_postings_get_unique_consecutive_numbers(mysql_client: AsyncClient):
    acc = await books(mysql_client)
    drafts = [await draft(mysql_client, acc, str(10 + i)) for i in range(6)]
    results = await asyncio.gather(*[mysql_client.post(f"/api/v1/accounting/journals/{d['id']}/post") for d in drafts])
    assert all(r.status_code == 200 for r in results), [r.text for r in results]
    numbers = sorted(r.json()["entry_number"] for r in results)
    assert [n.rsplit("/", 1)[1] for n in numbers] == [f"{i:05d}" for i in range(1, 7)]


@pytest.mark.asyncio
async def test_period_close_racing_with_posting_never_leaves_postings_in_a_closed_period(mysql_client: AsyncClient):
    acc = await books(mysql_client)
    periods = (await mysql_client.get("/api/v1/accounting/periods")).json()[0]["periods"]
    current = next(p for p in periods if p["start_date"] <= str(today_local()) <= p["end_date"])
    drafts = [await draft(mysql_client, acc, "20") for _ in range(4)]
    # Drafts block closing, so delete-free variant: post them while a close is attempted
    posts = [mysql_client.post(f"/api/v1/accounting/journals/{d['id']}/post") for d in drafts]
    close = mysql_client.post(f"/api/v1/accounting/periods/{current['id']}/close")
    results = await asyncio.gather(*posts, close)
    close_result = results[-1]
    if close_result.status_code == 200:
        # The close only succeeded once no drafts remained, i.e. after every posting committed
        assert all(r.status_code == 200 for r in results[:-1])
    else:
        assert close_result.status_code == 409
    async with mysql_client.session_factory() as db:
        rows = (await db.execute(text(
            "SELECT COUNT(*) FROM journal_entries je JOIN accounting_periods p ON p.id = je.period_id "
            "WHERE p.status = 'CLOSED' AND je.posted_at > p.closed_at"))).scalar_one()
    assert rows == 0


@pytest.mark.asyncio
async def test_database_rejects_unbalanced_or_two_sided_rows(mysql_client: AsyncClient):
    acc = await books(mysql_client)
    async with mysql_client.session_factory() as db:
        user_id = (await db.execute(select(User.id))).scalars().first()
        with pytest.raises((IntegrityError, OperationalError)):
            await db.execute(text(
                "INSERT INTO journal_entries (pharmacy_id, status, entry_date, source_type, narration, total_debit, "
                "total_credit, created_by, created_at) VALUES (1, 'POSTED', CURDATE(), 'X', 'x', 10, 9, :u, NOW())"), {"u": user_id})
        await db.rollback()
        d = await draft(mysql_client, acc)
        with pytest.raises((IntegrityError, OperationalError)):
            await db.execute(text(
                "INSERT INTO journal_lines (entry_id, line_no, account_id, debit, credit) VALUES (:e, 9, :a, 5, 5)"),
                {"e": d["id"], "a": acc["CASH"]["id"]})
        await db.rollback()


@pytest.mark.asyncio
async def test_direct_postings_racing_a_period_close(mysql_client: AsyncClient):
    """Postings hold a shared lock on the period; the close waits for them, later postings are refused."""
    acc = await books(mysql_client)
    periods = (await mysql_client.get("/api/v1/accounting/periods")).json()[0]["periods"]
    current = next(p for p in periods if p["start_date"] <= str(today_local()) <= p["end_date"])
    factory = mysql_client.session_factory

    async def post_one(i):
        async with factory() as db:
            user = (await db.execute(select(User).where(User.username == "admin"))).scalar_one()
            lines = [ledger.LineIn(acc["CASH"]["id"], debit=Decimal("5")), ledger.LineIn(acc["CAPITAL"]["id"], credit=Decimal("5"))]
            try:
                await ledger.post_entry(db, user, entry_date=today_local(), narration=f"Race {i}", lines=lines,
                                        source_type="TEST", source_id=str(i), source_key=f"RACE:{i}")
                await asyncio.sleep(0.05)          # Hold the shared lock a little while
                await db.commit()
                return "posted"
            except Exception as exc:               # 409 period closed
                await db.rollback()
                return f"refused:{getattr(exc, 'status_code', type(exc).__name__)}"

    tasks = [post_one(i) for i in range(8)]
    tasks.insert(4, mysql_client.post(f"/api/v1/accounting/periods/{current['id']}/close"))
    results = await asyncio.gather(*tasks)
    close = results.pop(4)
    assert close.status_code == 200, close.text
    assert all(r in ("posted", "refused:409") for r in results), results
    async with factory() as db:
        late = (await db.execute(text(
            "SELECT COUNT(*) FROM journal_entries je JOIN accounting_periods p ON p.id = je.period_id "
            "WHERE p.status = 'CLOSED' AND je.posted_at > p.closed_at"))).scalar_one()
        posted = (await db.execute(text("SELECT COUNT(*) FROM journal_entries WHERE source_type = 'TEST'"))).scalar_one()
    assert late == 0 and posted == results.count("posted")
