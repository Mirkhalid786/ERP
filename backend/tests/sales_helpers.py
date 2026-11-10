"""Shared helpers for Phase 4 (POS / sales / credit) tests."""
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from httpx import AsyncClient

from tests.test_purchasing import setup_pharmacy_and_catalogue


def D(value) -> Decimal:
    return Decimal(str(value))


def key() -> str:
    return uuid.uuid4().hex


async def receive_stock(client: AsyncClient, data: dict, batch: str, strips: str = "10.00",
                        selling: str = "42.00", mrp: str = "45.00", rate: str = "30.00",
                        expiry_days: int = 365, medicine: Optional[dict] = None) -> dict:
    """Receive and post strips of a medicine (1 strip = 15 tablets). Returns the batch dict."""
    med = medicine or data["medicine"]
    grn = await client.post("/api/v1/purchases/receipts", json={
        "supplier_id": data["supplier"]["id"],
        "receipt_date": str(date.today()),
        "items": [{
            "medicine_id": med["id"],
            "batch_number": batch,
            "expiry_date": str(date.today() + timedelta(days=expiry_days)),
            "packaging_unit_id": data["strip_unit"]["id"],
            "received_qty_pack": strips,
            "purchase_rate": rate,
            "mrp": mrp,
            "selling_rate": selling,
            "tax_rate_id": data["gst_12"]["id"],
        }],
    })
    assert grn.status_code == 201, grn.text
    posted = await client.post(f"/api/v1/purchases/receipts/{grn.json()['id']}/post")
    assert posted.status_code == 200, posted.text
    batch_id = posted.json()["items"][0]["batch_id"]
    return (await client.get(f"/api/v1/batches/{batch_id}")).json()


async def sales_setup(client: AsyncClient, stock_strips: str = "10.00") -> dict:
    """Pharmacy, admin login, catalogue, and one batch of Dolo (strips @ ₹42, MRP ₹45)."""
    data = await setup_pharmacy_and_catalogue(client)
    data["batch"] = await receive_stock(client, data, "SALE-B1", strips=stock_strips)
    return data


async def create_customer(client: AsyncClient, name: str = "Ramesh Kumar", mobile: Optional[str] = "9876543210",
                          credit_limit: str = "5000.00", consent: bool = True, **extra) -> dict:
    resp = await client.post("/api/v1/customers", json={
        "name": name, "mobile": mobile, "credit_limit": credit_limit, "payment_terms_days": 30,
        "whatsapp_consent": consent, **extra,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def line(data: dict, qty: str = "1", unit: str = "strip", **extra) -> dict:
    unit_id = data["strip_unit"]["id"] if unit == "strip" else data["tablet_unit"]["id"]
    return {"medicine_id": extra.pop("medicine_id", data["medicine"]["id"]), "unit_id": unit_id,
            "quantity": qty, **extra}


async def sell(client: AsyncClient, items: list, payments: list, customer_id: Optional[int] = None,
               idempotency_key: Optional[str] = None, expect: int = 201, **extra):
    body = {"idempotency_key": idempotency_key or key(), "customer_id": customer_id,
            "items": items, "payments": payments, **extra}
    resp = await client.post("/api/v1/sales/invoices", json=body)
    assert resp.status_code == expect, resp.text
    return resp.json()


async def batch_stock(client: AsyncClient, batch_id: int) -> Decimal:
    return D((await client.get(f"/api/v1/batches/{batch_id}")).json()["current_stock_base"])


async def add_user(client: AsyncClient, role: str, username: str, password: str = "Staff1234") -> dict:
    roles = {r["name"]: r for r in (await client.get("/api/v1/roles")).json()}
    resp = await client.post("/api/v1/users", json={
        "name": f"{role} User", "username": username, "password": password, "role_id": roles[role]["id"],
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


async def login_as(client: AsyncClient, username: str, password: str):
    resp = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
