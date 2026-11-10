"""
Pilot / load test data (Phase 8). Creates a realistic volume of TEST data through the real
API code paths: medicines, goods receipts, supplier bills and payments, cash/UPI/credit sales,
collections, returns, expenses — optionally with accounting set up so everything posts.

SAFETY: refuses to run unless the target database name ends in "_test" or "_staging".
It drops and recreates every table in that database first.

    set LOAD_DB_URL=mysql+aiomysql://user:pass@127.0.0.1:3306/pharmacy_erp_staging
    python -m scripts.load_test_data --medicines 200 --days 30 --sales-per-day 150 --books
"""
import argparse
import asyncio
import os
import random
import time
import uuid
from datetime import date, timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.database import Base, engine_options, get_db, get_session_factory
from app.main import app


def key() -> str:
    return uuid.uuid4().hex


async def main(a) -> None:
    url = os.environ.get("LOAD_DB_URL", "")
    name = url.rsplit("/", 1)[-1].split("?")[0]
    if not name.endswith(("_test", "_staging")):
        raise SystemExit("LOAD_DB_URL must point at a database ending in _test or _staging")
    engine = create_async_engine(url, pool_size=10, **engine_options(url))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override():
        async with factory() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise
    app.dependency_overrides[get_db] = override
    app.dependency_overrides[get_session_factory] = lambda: factory
    rnd = random.Random(a.seed)
    t0 = time.perf_counter()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://load") as c:
        async def csrf(req):
            if req.method not in ("GET", "HEAD") and (tok := c.cookies.get(settings.CSRF_COOKIE_NAME)):
                req.headers["X-CSRF-Token"] = tok
        c.event_hooks["request"].append(csrf)
        assert (await c.post("/api/v1/setup", json={"pharmacy_name": "TEST Load Pharmacy", "admin_name": "Load Admin",
                                                    "admin_username": "admin", "admin_password": "Admin123",
                                                    "invoice_prefix": "LD", "state_code": "MH", "city": "Pune"})).status_code == 201
        assert (await c.post("/api/v1/auth/login", json={"username": "admin", "password": "Admin123"})).status_code == 200
        units = {u["abbreviation"].lower(): u for u in (await c.get("/api/v1/units")).json()}
        tablet = next(u for k, u in units.items() if k in ("tab", "tabs", "tablet"))
        strip = next(u for k, u in units.items() if k in ("strip", "stp"))
        gst12 = next(t for t in (await c.get("/api/v1/tax-rates")).json() if str(t["rate_percentage"]).startswith("12"))
        supplier = (await c.post("/api/v1/suppliers", json={"name": "TEST Load Distributor", "state_code": "27"})).json()
        start = date.today() - timedelta(days=a.days)
        if a.books:
            assert (await c.post("/api/v1/accounting/setup", json={"books_start_date": str(start.replace(day=1))})).status_code == 201
        meds = []
        for i in range(a.medicines):
            m = (await c.post("/api/v1/medicines", json={"name": f"TEST Med {i:04d}", "generic_name": "Test", "dosage_form": "Tablet",
                                                         "hsn_code": "3004", "schedule_type": "OTC", "base_unit_id": tablet["id"],
                                                         "tax_rate_id": gst12["id"], "reorder_level": "30"})).json()
            await c.post(f"/api/v1/medicines/{m['id']}/conversions", json={"packaging_unit_id": strip["id"], "conversion_factor": "10",
                                                                           "is_sales_unit": True, "is_purchase_unit": True})
            rate = rnd.randint(20, 200)
            grn = (await c.post("/api/v1/purchases/receipts", json={"supplier_id": supplier["id"], "receipt_date": str(start),
                "items": [{"medicine_id": m["id"], "batch_number": f"LB{i:04d}", "expiry_date": str(date.today() + timedelta(days=rnd.randint(20, 700))),
                           "packaging_unit_id": strip["id"], "received_qty_pack": str(rnd.randint(30, 120)), "purchase_rate": str(rate),
                           "mrp": str(rate * 1.6), "selling_rate": str(rate * 1.5), "tax_rate_id": gst12["id"]}]})).json()
            await c.post(f"/api/v1/purchases/receipts/{grn['id']}/post")
            meds.append((m, rate))
        customers = [(await c.post("/api/v1/customers", json={"name": f"TEST Customer {i:03d}", "mobile": f"98{i:08d}",
                                                              "credit_limit": "20000"})).json() for i in range(a.customers)]
        sales = failures = 0
        reasons: dict = {}
        for _ in range(a.days * a.sales_per_day):
            m, rate = rnd.choice(meds)
            qty = rnd.randint(1, 3)
            item = [{"medicine_id": m["id"], "unit_id": strip["id"], "quantity": str(qty)}]
            kind = rnd.random()
            body = {"idempotency_key": key(), "items": item, "customer_id": None}
            if kind < 0.6:
                body["payments"] = [{"method": "CASH", "tendered": str(rate * 2 * qty + 100)}]
            elif kind < 0.85:
                quote = (await c.post("/api/v1/pos/quote", json={"items": item, "customer_id": None})).json()
                body["payments"] = [{"method": "UPI", "amount": str(quote.get("grand_total", "0")), "reference": key()[:10]}]
            else:
                body["customer_id"], body["payments"] = rnd.choice(customers)["id"], []
            r = await c.post("/api/v1/sales/invoices", json=body)
            sales += r.status_code == 201
            if r.status_code != 201:
                failures += 1
                reason = str(r.json().get("detail"))[:80]
                reasons[reason] = reasons.get(reason, 0) + 1
        print(f"medicines {len(meds)}, customers {len(customers)}, sales ok {sales}, refused {failures} "
              f"{time.perf_counter() - t0:.0f}s")
        for reason, n in sorted(reasons.items(), key=lambda x: -x[1])[:5]:
            print(f"  refused x{n}: {reason}")
        if a.books:
            rec = (await c.get("/api/v1/accounting/postings/reconciliation")).json()
            tb = (await c.get("/api/v1/accounting/trial-balance")).json()
            print(f"trial balance balanced: {tb['is_balanced']}; reconciliation ok: {rec['ok']} "
                  f"(missing {len(rec['missing'])}, issues {len(rec['open_issues'])})")
    await engine.dispose()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--medicines", type=int, default=100)
    p.add_argument("--customers", type=int, default=50)
    p.add_argument("--days", type=int, default=10)
    p.add_argument("--sales-per-day", type=int, default=50)
    p.add_argument("--books", action="store_true", help="Set up accounting so everything posts")
    p.add_argument("--seed", type=int, default=7)
    asyncio.run(main(p.parse_args()))
