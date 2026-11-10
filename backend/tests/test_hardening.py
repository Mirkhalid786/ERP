"""
Regression tests for the medium-priority review fixes:
  1. CSRF: state-changing requests with a session must echo the CSRF cookie.
  2. Login: unknown users go through a real Argon2 check; repeated failures are rate limited.
  3. Document numbers come from a locked counter that starts above existing numbers.
  4. Invoice payments: amount capped at the invoice total, status must match the amount.
  5. Supplier state code follows the GSTIN and cannot contradict it.
  6. Stock ledger total_cost is quantity x unit cost for receipts and returns.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from httpx import AsyncClient
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import today_local
from app.core.config import settings
from app.core.security import csrf_token_for_session
from app.models.numbering import DocumentSequence
from app.models.purchasing import PurchaseOrder
from app.services.auth import get_dummy_password_hash
from tests.conftest import login, setup_pharmacy
from tests.test_purchasing import setup_pharmacy_and_catalogue
from tests.test_purchasing_fixes import grn_line, receive_and_post, return_line, return_payload


SUPPLIER = {"name": "CSRF Test Distributor"}


# ── 1. CSRF ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_login_sets_readable_csrf_cookie_bound_to_session(client: AsyncClient):
    await setup_pharmacy(client)
    resp = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "Admin123"})
    assert resp.status_code == 200

    session_token = resp.cookies[settings.SESSION_COOKIE_NAME]
    csrf_cookie = resp.cookies[settings.CSRF_COOKIE_NAME]
    assert csrf_cookie == csrf_token_for_session(session_token)
    assert csrf_cookie != session_token

    csrf_set_cookie = next(
        h for h in resp.headers.get_list("set-cookie") if h.startswith(f"{settings.CSRF_COOKIE_NAME}=")
    )
    assert "httponly" not in csrf_set_cookie.lower()  # the frontend must be able to read it


@pytest.mark.asyncio
async def test_state_changing_request_without_valid_csrf_header_is_rejected(client: AsyncClient):
    await setup_pharmacy(client)
    await login(client)

    missing = await client.post("/api/v1/suppliers", json=SUPPLIER, headers={"X-CSRF-Token": ""})
    assert missing.status_code == 403
    assert "csrf" in missing.json()["detail"].lower()

    forged = await client.post("/api/v1/suppliers", json=SUPPLIER, headers={"X-CSRF-Token": "0" * 64})
    assert forged.status_code == 403

    # Nothing was created by the rejected requests
    listed = await client.get("/api/v1/suppliers")
    assert listed.status_code == 200
    assert "CSRF Test Distributor" not in listed.text

    # The conftest client echoes the cookie like the frontend does
    ok = await client.post("/api/v1/suppliers", json=SUPPLIER)
    assert ok.status_code == 201, ok.text


@pytest.mark.asyncio
async def test_csrf_not_required_for_reads_or_requests_without_session(client: AsyncClient):
    await setup_pharmacy(client)
    # No session cookie: normal auth applies (401), not CSRF (403)
    resp = await client.post("/api/v1/suppliers", json=SUPPLIER, headers={"X-CSRF-Token": ""})
    assert resp.status_code == 401

    await login(client)
    assert (await client.get("/api/v1/suppliers", headers={"X-CSRF-Token": ""})).status_code == 200


@pytest.mark.asyncio
async def test_me_reissues_missing_csrf_cookie_and_logout_clears_it(client: AsyncClient):
    await setup_pharmacy(client)
    await login(client)
    session_token = client.cookies[settings.SESSION_COOKIE_NAME]

    # Simulate a session created before CSRF cookies existed
    client.cookies.delete(settings.CSRF_COOKIE_NAME)
    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.cookies[settings.CSRF_COOKIE_NAME] == csrf_token_for_session(session_token)

    logout = await client.post("/api/v1/auth/logout")
    assert logout.status_code == 200
    cleared = [h for h in logout.headers.get_list("set-cookie") if h.startswith(f"{settings.CSRF_COOKIE_NAME}=")]
    assert cleared and ("max-age=0" in cleared[0].lower() or "expires=" in cleared[0].lower())


# ── 2. Login timing and rate limiting ──────────────────────────────────────────

def test_dummy_hash_for_unknown_users_is_a_real_argon2_hash():
    dummy = get_dummy_password_hash()
    assert dummy.startswith("$argon2id$")
    # A malformed hash would raise InvalidHashError instantly; a real one does the full work
    with pytest.raises(VerifyMismatchError):
        PasswordHasher().verify(dummy, "any-password")


async def _attempt(client: AsyncClient, username: str, password: str):
    return await client.post("/api/v1/auth/login", json={"username": username, "password": password})


@pytest.mark.asyncio
async def test_repeated_failed_logins_are_rate_limited(client: AsyncClient):
    await setup_pharmacy(client)
    for _ in range(settings.LOGIN_MAX_FAILURES_PER_USER):
        assert (await _attempt(client, "admin", "WrongPass1")).status_code == 401

    # Locked out, even with the right password
    locked = await _attempt(client, "admin", "Admin123")
    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) > 0
    assert "too many failed login attempts" in locked.json()["detail"].lower()

    # The lock is per username: another account from the same IP can still try
    assert (await _attempt(client, "someone_else", "WrongPass1")).status_code == 401


@pytest.mark.asyncio
async def test_successful_login_resets_failure_count(client: AsyncClient):
    await setup_pharmacy(client)
    for _ in range(settings.LOGIN_MAX_FAILURES_PER_USER - 1):
        assert (await _attempt(client, "admin", "WrongPass1")).status_code == 401
    assert (await _attempt(client, "admin", "Admin123")).status_code == 200
    for _ in range(settings.LOGIN_MAX_FAILURES_PER_USER - 1):
        assert (await _attempt(client, "admin", "WrongPass1")).status_code == 401
    assert (await _attempt(client, "admin", "Admin123")).status_code == 200


@pytest.mark.asyncio
async def test_per_ip_limit_applies_across_usernames(client: AsyncClient):
    await setup_pharmacy(client)
    for i in range(settings.LOGIN_MAX_FAILURES_PER_IP):
        assert (await _attempt(client, f"guess_{i}", "WrongPass1")).status_code == 401
    assert (await _attempt(client, "admin", "Admin123")).status_code == 429


# ── 3. Document numbering ──────────────────────────────────────────────────────

async def _create_po(client: AsyncClient, data: dict) -> dict:
    resp = await client.post("/api/v1/purchases/orders", json={
        "supplier_id": data["supplier"]["id"],
        "order_date": str(date.today()),
        "items": [{
            "medicine_id": data["medicine"]["id"],
            "packaging_unit_id": data["strip_unit"]["id"],
            "ordered_qty_pack": "1.00",
            "purchase_rate": "10.00",
        }],
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_document_numbers_are_sequential_and_skip_existing_numbers(client: AsyncClient, test_engine):
    data = await setup_pharmacy_and_catalogue(client)
    first = await _create_po(client, data)
    second = await _create_po(client, data)
    prefix = first["po_number"].rsplit("-", 1)[0]
    assert first["po_number"] == f"{prefix}-0001"
    assert second["po_number"] == f"{prefix}-0002"

    Session = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as db:
        seq = (await db.execute(select(DocumentSequence).where(DocumentSequence.doc_type == "PO"))).scalar_one()
        assert seq.period_key == prefix and seq.last_value == 2

        # Documents numbered before the counter existed, with a gap (old count+1 would
        # have produced -0003 next time and collided after deletions/gaps).
        await db.execute(
            update(PurchaseOrder).where(PurchaseOrder.id == second["id"]).values(po_number=f"{prefix}-0009")
        )
        await db.execute(delete(DocumentSequence))
        await db.commit()

    third = await _create_po(client, data)
    assert third["po_number"] == f"{prefix}-0010"


@pytest.mark.asyncio
async def test_adjustment_numbers_use_the_daily_counter(client: AsyncClient):
    data = await setup_pharmacy_and_catalogue(client)
    grn = await receive_and_post(client, data["supplier"]["id"], [
        grn_line(data["medicine"]["id"], data["strip_unit"]["id"]),
    ])
    batch_id = grn["items"][0]["batch_id"]

    numbers = []
    for stock in ("140.00", "130.00"):
        resp = await client.post("/api/v1/inventory/adjustments", json={
            "reason_code": "BREAKAGE",
            "items": [{"medicine_id": data["medicine"]["id"], "batch_id": batch_id, "physical_stock_base": stock}],
        })
        assert resp.status_code == 201, resp.text
        numbers.append(resp.json()["adjustment_number"])

    today = today_local().strftime("%Y%m%d")   # business date, not the test machine's
    assert numbers == [f"ADJ-{today}-0001", f"ADJ-{today}-0002"]


# ── 4. Invoice payment validation ──────────────────────────────────────────────

async def _create_invoice(client: AsyncClient, data: dict) -> dict:
    resp = await client.post("/api/v1/purchases/invoices", json={
        "supplier_id": data["supplier"]["id"],
        "invoice_number": "PAY-001",
        "invoice_date": str(date.today()),
        "received_date": str(date.today()),
        "supplier_declared_total": "1000.00",
        "items": [{
            "medicine_id": data["medicine"]["id"],
            "batch_number": "PAY-B1",
            "packaging_unit_id": data["strip_unit"]["id"],
            "billed_qty_pack": "10.00",
            "purchase_rate": "100.00",
        }],
    })
    assert resp.status_code == 201, resp.text
    assert Decimal(str(resp.json()["calculated_grand_total"])) == Decimal("1000.00")
    return resp.json()


@pytest.mark.asyncio
async def test_paid_amount_can_no_longer_be_overwritten(client: AsyncClient):
    """Payments are dated supplier-payment records now; the old overwrite endpoint is retired."""
    data = await setup_pharmacy_and_catalogue(client)
    inv = await _create_invoice(client, data)
    url = f"/api/v1/purchases/invoices/{inv['id']}/payment"
    gone = await client.patch(url, json={"payment_status": "PAID", "paid_amount": "1000.00"})
    assert gone.status_code == 410 and "supplier payment" in gone.json()["detail"]
    fin = await client.post(f"/api/v1/purchases/invoices/{inv['id']}/finalize", json={"acknowledge_warnings": True})
    assert fin.status_code == 200, fin.text
    over = await client.post("/api/v1/supplier-payments", json={
        "idempotency_key": "overpay-0001", "supplier_id": data["supplier"]["id"], "payment_date": str(date.today()),
        "method": "BANK_TRANSFER", "reference": "UTR1", "amount": "1000.01",
        "allocations": [{"invoice_id": inv["id"], "amount": "1000.01"}]})
    assert over.status_code == 422 and "outstanding" in over.json()["detail"]
    refreshed = (await client.get(f"/api/v1/purchases/invoices/{inv['id']}")).json()
    assert refreshed["paid_amount"] in ("0.00", 0, "0") or Decimal(str(refreshed["paid_amount"])) == 0


# ── 5. Supplier GSTIN / state code consistency ─────────────────────────────────

@pytest.mark.asyncio
async def test_supplier_state_code_follows_gstin(client: AsyncClient):
    await setup_pharmacy(client)
    await login(client)

    mismatch = await client.post("/api/v1/suppliers", json={
        "name": "Mismatch Pharma", "gstin": "27AAACC1234D1Z8", "state_code": "29",
    })
    assert mismatch.status_code == 422
    errors = mismatch.json()["detail"]                 # field-level validation error on state_code
    assert errors[0]["loc"][-1] == "state_code" and "does not match the gstin" in errors[0]["msg"].lower()

    created = await client.post("/api/v1/suppliers", json={"name": "Moving Pharma", "gstin": "27AAACC1234D1Z8"})
    assert created.status_code == 201
    supplier = created.json()
    assert supplier["state_code"] == "27"

    # Supplier re-registers in Karnataka: state code must follow the new GSTIN
    moved = await client.put(f"/api/v1/suppliers/{supplier['id']}", json={"gstin": "29AAACC1234D1Z8"})
    assert moved.status_code == 200, moved.text
    assert moved.json()["state_code"] == "29"

    contradict = await client.put(f"/api/v1/suppliers/{supplier['id']}", json={"state_code": "27"})
    assert contradict.status_code == 422

    # Without a GSTIN the state code can be set freely
    unregistered = await client.post("/api/v1/suppliers", json={"name": "Local Stockist", "state_code": "27"})
    assert unregistered.status_code == 201
    assert unregistered.json()["state_code"] == "27"


# ── 6. Stock ledger total_cost ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ledger_total_cost_is_quantity_times_unit_cost(client: AsyncClient):
    data = await setup_pharmacy_and_catalogue(client)
    sid, mid, uid = data["supplier"]["id"], data["medicine"]["id"], data["strip_unit"]["id"]

    # 10 paid + 2 free - 1 rejected strips @ 30 + 12% GST; 1 strip = 15 tablets
    grn = await receive_and_post(client, sid, [grn_line(
        mid, uid, rate="30.00", free_qty_pack="2.00", rejected_qty_pack="1.00",
        rejection_reason="Broken blister", tax_rate_id=data["gst_12"]["id"],
    )])
    batch_id = grn["items"][0]["batch_id"]

    ret = await client.post("/api/v1/purchases/returns", json=return_payload(
        sid, [return_line(mid, batch_id, uid, qty="1.00", tax_rate_id=data["gst_12"]["id"])],
    ))
    assert ret.status_code == 201, ret.text
    assert (await client.post(f"/api/v1/purchases/returns/{ret.json()['id']}/post")).status_code == 200

    movements = {m["movement_type"]: m for m in
                 (await client.get(f"/api/v1/inventory/movements?batch_id={batch_id}")).json()}

    receipt = movements["GOODS_RECEIPT"]
    # 165 accepted tablets x 1.6667 (300 / 180) = 275.01, not the 336.00 invoice line incl. GST
    assert Decimal(str(receipt["unit_cost"])) == Decimal("1.6667")
    assert Decimal(str(receipt["total_cost"])) == Decimal("275.01")

    outward = movements["RETURN_OUTWARD"]
    # 15 tablets x 1.6667 = 25.00, not the 33.60 refund value incl. GST
    assert Decimal(str(outward["total_cost"])) == Decimal("25.00")
