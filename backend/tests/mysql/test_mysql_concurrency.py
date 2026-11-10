"""
Concurrency on real MySQL 8.4 (InnoDB row locks). Requests run truly concurrently
on separate connections via asyncio.gather.
"""
import asyncio

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.models.inventory import StockMovement
from app.models.sales import CustomerPayment, SalesInvoice
from tests.mysql.conftest import pytestmark  # noqa: F401  (skip without TEST_MYSQL_URL)
from tests.sales_helpers import D, batch_stock, create_customer, key, line, receive_stock, sales_setup


def sale_body(data, strips="1", **extra):
    return {"idempotency_key": extra.pop("idempotency_key", key()), "items": [line(data, strips, **extra.pop("line", {}))],
            "payments": extra.pop("payments", [{"method": "CASH", "tendered": "500"}]), **extra}


async def count(client, stmt) -> int:
    async with client.session_factory() as db:
        return (await db.execute(stmt)).scalar_one()


@pytest.mark.asyncio
async def test_concurrent_sales_never_oversell(mysql_client: AsyncClient):
    data = await sales_setup(mysql_client, stock_strips="1.00")          # exactly 15 tablets
    results = await asyncio.gather(*[
        mysql_client.post("/api/v1/sales/invoices", json=sale_body(data, "1")) for _ in range(4)
    ])
    codes = sorted(r.status_code for r in results)
    assert codes == [201, 422, 422, 422], [r.text for r in results]
    assert await batch_stock(mysql_client, data["batch"]["id"]) == D("0.00")
    assert await count(mysql_client, select(func.count(StockMovement.id))
                       .where(StockMovement.movement_type == "SALE")) == 1


@pytest.mark.asyncio
async def test_concurrent_duplicate_submission_sells_once(mysql_client: AsyncClient):
    data = await sales_setup(mysql_client)
    body = sale_body(data, "2")
    results = await asyncio.gather(*[mysql_client.post("/api/v1/sales/invoices", json=body) for _ in range(3)])
    assert sorted(r.status_code for r in results) == [200, 200, 201], [r.text for r in results]
    assert len({r.json()["id"] for r in results}) == 1
    assert await batch_stock(mysql_client, data["batch"]["id"]) == D("120.00")
    assert await count(mysql_client, select(func.count(SalesInvoice.id))) == 1


@pytest.mark.asyncio
async def test_concurrent_invoice_numbers_are_unique_and_consecutive(mysql_client: AsyncClient):
    data = await sales_setup(mysql_client)
    results = await asyncio.gather(*[
        mysql_client.post("/api/v1/sales/invoices", json=sale_body(data, "1")) for _ in range(6)
    ])
    assert all(r.status_code == 201 for r in results), [r.text for r in results]
    numbers = sorted(r.json()["invoice_number"] for r in results)
    assert [n.rsplit("/", 1)[1] for n in numbers] == [f"{i:05d}" for i in range(1, 7)]


@pytest.mark.asyncio
async def test_concurrent_credit_sales_respect_credit_limit(mysql_client: AsyncClient):
    data = await sales_setup(mysql_client)
    rs100 = await receive_stock(mysql_client, data, "RS100", strips="20.00", selling="100.00", mrp="100.00",
                                rate="70.00", expiry_days=300)
    customer = await create_customer(mysql_client, credit_limit="500.00")
    bodies = [sale_body(data, "4", line={"batch_id": rs100["id"]}, payments=[], customer_id=customer["id"])
              for _ in range(2)]                                          # ₹400 each; only one fits
    results = await asyncio.gather(*[mysql_client.post("/api/v1/sales/invoices", json=b) for b in bodies])
    codes = sorted(r.status_code for r in results)
    assert codes[0] == 201 and codes[1] in (403, 422), [r.text for r in results]
    owed = (await mysql_client.get(f"/api/v1/customers/{customer['id']}")).json()["outstanding_balance"]
    assert D(owed) == D("400.00")


@pytest.mark.asyncio
async def test_concurrent_duplicate_payment_posts_once(mysql_client: AsyncClient):
    data = await sales_setup(mysql_client)
    customer = await create_customer(mysql_client)
    sale = await mysql_client.post("/api/v1/sales/invoices", json=sale_body(
        data, "5", payments=[], customer_id=customer["id"]))
    assert sale.status_code == 201
    body = {"idempotency_key": key(), "method": "CASH", "amount": "100.00"}
    results = await asyncio.gather(*[
        mysql_client.post(f"/api/v1/customers/{customer['id']}/payments", json=body) for _ in range(3)
    ])
    assert all(r.status_code == 201 for r in results), [r.text for r in results]
    assert len({r.json()["id"] for r in results}) == 1
    assert await count(mysql_client, select(func.count(CustomerPayment.id))
                       .where(CustomerPayment.source == "COLLECTION")) == 1
    owed = (await mysql_client.get(f"/api/v1/customers/{customer['id']}")).json()["outstanding_balance"]
    assert D(owed) == D("110.00")                                         # 210 - 100


@pytest.mark.asyncio
async def test_concurrent_returns_cannot_exceed_sold_quantity(mysql_client: AsyncClient):
    data = await sales_setup(mysql_client)
    sale = (await mysql_client.post("/api/v1/sales/invoices", json=sale_body(data, "1"))).json()  # 15 tablets
    item_id = sale["items"][0]["id"]
    results = await asyncio.gather(*[
        mysql_client.post("/api/v1/sales/returns", json={
            "idempotency_key": key(), "invoice_id": sale["id"], "reason": "Concurrent return",
            "items": [{"invoice_item_id": item_id, "quantity_base": "10", "condition": "SALEABLE"}]})
        for _ in range(2)
    ])
    assert sorted(r.status_code for r in results) == [201, 422], [r.text for r in results]
    assert await batch_stock(mysql_client, data["batch"]["id"]) == D("145.00")
