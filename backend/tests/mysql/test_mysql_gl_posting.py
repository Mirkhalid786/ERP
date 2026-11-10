"""Automatic GL postings under real MySQL concurrency."""
import asyncio

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.core.clock import today_local
from app.models.accounting import JournalEntry
from app.models.auth import User
from app.services import gl_posting
from tests.mysql.conftest import pytestmark  # noqa: F401  (skip without TEST_MYSQL_URL)
from tests.sales_helpers import key, line, receive_stock
from tests.test_purchasing import setup_pharmacy_and_catalogue


async def books(client: AsyncClient) -> dict:
    data = await setup_pharmacy_and_catalogue(client)
    resp = await client.post("/api/v1/accounting/setup", json={"books_start_date": str(today_local().replace(day=1))})
    assert resp.status_code == 201, resp.text
    await receive_stock(client, data, "MY-GL-B1", strips="20.00")
    return data


@pytest.mark.asyncio
async def test_concurrent_sales_each_post_exactly_one_balanced_journal(mysql_client: AsyncClient):
    data = await books(mysql_client)
    bodies = [{"idempotency_key": key(), "customer_id": None, "items": [line(data, "1")],
               "payments": [{"method": "CASH", "amount": "42.00"}]} for _ in range(6)]
    bodies.append(bodies[0])                     # A double-click of the first sale
    results = await asyncio.gather(*[mysql_client.post("/api/v1/sales/invoices", json=b) for b in bodies])
    assert all(r.status_code in (200, 201) for r in results), [r.text for r in results]
    async with mysql_client.session_factory() as db:
        keys = (await db.execute(select(JournalEntry.source_key).where(JournalEntry.source_key.like("SALE:%")))).scalars().all()
        numbers = (await db.execute(select(JournalEntry.entry_number))).scalars().all()
    assert len(keys) == 6 == len(set(keys))
    assert len(numbers) == len(set(numbers))
    tb = (await mysql_client.get("/api/v1/accounting/trial-balance")).json()
    assert tb["is_balanced"]
    rec = (await mysql_client.get("/api/v1/accounting/postings/reconciliation")).json()
    assert rec["ok"], rec


@pytest.mark.asyncio
async def test_concurrent_retries_of_one_event_post_once(mysql_client: AsyncClient):
    data = await books(mysql_client)
    sale = (await mysql_client.post("/api/v1/sales/invoices", json={
        "idempotency_key": key(), "customer_id": None, "items": [line(data, "1")],
        "payments": [{"method": "UPI", "amount": "42.00", "reference": "U-RACE"}]})).json()
    factory = mysql_client.session_factory
    async with factory() as db:                   # Remove the journal to simulate a missed posting
        entry = (await db.execute(select(JournalEntry).where(JournalEntry.source_key == f"SALE:{sale['id']}"))).scalar_one()
        await db.delete(entry)
        await db.commit()

    async def attempt():
        async with factory() as db:
            user = (await db.execute(select(User).where(User.username == "admin"))).scalar_one()
            outcome = await gl_posting.record(db, user.pharmacy_id, user.id, "SALE", sale["id"])
            await db.commit()
            return outcome

    outcomes = await asyncio.gather(*[attempt() for _ in range(4)])
    assert outcomes.count("posted") == 1, outcomes
    async with factory() as db:
        n = (await db.execute(select(func.count(JournalEntry.id)).where(JournalEntry.source_key == f"SALE:{sale['id']}"))).scalar_one()
    assert n == 1
