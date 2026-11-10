"""Phase 4 reports, dashboard and worker (test mode)."""
from datetime import date, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import worker
from tests.sales_helpers import D, create_customer, key, line, sales_setup, sell


@pytest.mark.asyncio
async def test_summary_dashboard_and_csv_distinguish_sales_tax_and_collections(client: AsyncClient):
    data = await sales_setup(client)
    customer = await create_customer(client)
    await sell(client, [line(data, "2")], [{"method": "CASH", "tendered": "100"}])                    # ₹84
    await sell(client, [line(data, "1", discount_percent="10")], [{"method": "UPI", "amount": "38.00"}])  # ₹38
    credit = await sell(client, [line(data, "5")], [{"method": "CASH", "amount": "60.00"}], customer_id=customer["id"])
    await client.post(f"/api/v1/customers/{customer['id']}/payments", json={
        "idempotency_key": key(), "method": "UPI", "amount": "50.00", "reference": "U-2"})

    summary = (await client.get("/api/v1/reports/sales/summary")).json()["data"]
    assert summary["bills"] == 3
    assert D(summary["gross_sales"]) == D("336.00")                # 84 + 42 + 210 before discount
    assert D(summary["discounts"]) == D("4.20")
    assert D(summary["net_sales"]) == D("332.00")                  # 84 + 38 + 210 (incl. round-off)
    assert D(summary["taxable_value"]) + D(summary["tax"]) + D(summary["round_off"]) == D("332.00")
    assert D(summary["credit_given"]) == D("150.00")
    assert D(summary["collections_total"]) == D("232.00")          # 84 + 38 + 60 at sale + 50 later
    assert D(summary["collections_by_method"]["UPI"]["collections"]) == D("50.00")

    dash = (await client.get("/api/v1/reports/sales/dashboard")).json()
    assert D(dash["outstanding_receivables"]) == D("100.00")
    assert D(dash["today"]["net_sales"]) == D("332.00")

    tax = (await client.get("/api/v1/reports/sales/tax")).json()["data"]["sales"][0]
    assert D(tax["tax_rate"]) == D("12.00") and D(tax["value_incl_tax"]) == D("331.80")

    csv = await client.get("/api/v1/reports/sales/register?format=csv")
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    assert csv.text.splitlines()[0].startswith("invoice_number,")
    assert credit["invoice_number"] in csv.text

    aging = (await client.get("/api/v1/receivables/aging")).json()
    assert D(aging["totals"]["not_due"]) == D("100.00")


@pytest.mark.asyncio
async def test_worker_dry_run_reports_without_writing(client: AsyncClient, test_engine):
    data = await sales_setup(client)
    customer = await create_customer(client)
    inv = await sell(client, [line(data, "2")], [], customer_id=customer["id"])
    factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
    due = date.fromisoformat(inv["due_date"])

    dry = await worker.run_once(factory, dry_run=True, on_date=due + timedelta(days=1))
    assert dry["reminder_candidates"][0]["purpose"] == "REMINDER_OVERDUE"
    assert (await client.get("/api/v1/messaging/outbox")).json()["total"] == 0

    real = await worker.run_once(factory, on_date=due + timedelta(days=1))
    assert real["reminder_candidates"][0]["message_id"]
    assert (await client.get("/api/v1/messaging/outbox?status=PENDING")).json()["total"] == 1
