"""
Phase 4 credit, returns, voids, day-end, WhatsApp and reminder tests.
External WhatsApp calls are mocked; nothing is ever sent.
"""
import hashlib
import hmac
import json
from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.models.auth import AuditLog
from app.services import messaging
from tests.sales_helpers import (
    D, add_user, batch_stock, create_customer, key, line, login_as, receive_stock, sales_setup, sell,
)


async def credit_setup(client: AsyncClient, credit_limit: str = "5000.00") -> dict:
    """Batch priced ₹100 / strip so ten strips make a ₹1,000 bill."""
    data = await sales_setup(client)
    data["rs100"] = await receive_stock(client, data, "RS100", strips="20.00", selling="100.00", mrp="100.00",
                                        rate="70.00", expiry_days=300)
    data["customer"] = await create_customer(client, credit_limit=credit_limit)
    return data


def rs100(data: dict, strips: str = "10") -> list:
    return [line(data, strips, batch_id=data["rs100"]["id"])]


def rs42(data: dict, strips: str = "1") -> list:
    """The ₹42 / strip batch (pinned: FEFO would otherwise pick the earlier-expiring ₹100 batch)."""
    return [line(data, strips, batch_id=data["batch"]["id"])]


async def collect(client, customer_id, amount, method="CASH", **extra):
    resp = await client.post(f"/api/v1/customers/{customer_id}/payments", json={
        "idempotency_key": extra.pop("idempotency_key", key()), "method": method, "amount": amount, **extra})
    return resp


async def balance(client, customer_id) -> Decimal:
    return D((await client.get(f"/api/v1/customers/{customer_id}")).json()["outstanding_balance"])


async def reconciled(client, customer_id) -> bool:
    return (await client.get(f"/api/v1/customers/{customer_id}/reconcile")).json()["is_reconciled"]


# ── 5, 6, 7: Scenario B and C ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_credit_sale_partial_payment_and_settlement(client: AsyncClient):
    data = await credit_setup(client)
    cid = data["customer"]["id"]

    inv = await sell(client, rs100(data), [{"method": "CASH", "tendered": "400"}], customer_id=cid)
    assert D(inv["grand_total"]) == D("1000.00")
    assert D(inv["amount_paid"]) == D("400.00") and D(inv["balance_due"]) == D("600.00")
    assert inv["payment_status"] == "PARTIALLY_PAID" and inv["is_credit_sale"] is True
    assert inv["due_date"] == str(date.today() + timedelta(days=30))
    assert await balance(client, cid) == D("600.00")

    first = await collect(client, cid, "300.00", allocations=[{"invoice_id": inv["id"], "amount": "300.00"}])
    assert first.status_code == 201, first.text
    after = (await client.get(f"/api/v1/sales/invoices/{inv['id']}")).json()
    assert D(after["balance_due"]) == D("300.00") and after["payment_status"] == "PARTIALLY_PAID"
    assert await balance(client, cid) == D("300.00")

    second = await collect(client, cid, "300.00", method="UPI", reference="UPI-99")
    assert second.status_code == 201
    settled = (await client.get(f"/api/v1/sales/invoices/{inv['id']}")).json()
    assert D(settled["balance_due"]) == D("0.00") and settled["payment_status"] == "PAID"
    assert len(settled["payments"]) == 3
    assert await balance(client, cid) == D("0.00") and await reconciled(client, cid)

    statement = (await client.get(f"/api/v1/customers/{cid}/statement")).json()
    assert [e["entry_type"] for e in statement["entries"]] == ["INVOICE", "PAYMENT", "PAYMENT", "PAYMENT"]
    assert D(statement["closing_balance"]) == D("0.00")


@pytest.mark.asyncio
async def test_full_credit_sale_needs_a_customer(client: AsyncClient):
    data = await credit_setup(client)
    walk_in = await client.post("/api/v1/sales/invoices", json={
        "idempotency_key": key(), "items": rs100(data, "1"), "payments": []})
    assert walk_in.status_code == 422 and "Select a customer" in walk_in.json()["detail"]
    inv = await sell(client, rs100(data, "2"), [], customer_id=data["customer"]["id"])
    assert inv["payment_status"] == "UNPAID" and D(inv["balance_due"]) == D("200.00")


@pytest.mark.asyncio
async def test_duplicate_payment_submission_is_recorded_once(client: AsyncClient):
    data = await credit_setup(client)
    cid = data["customer"]["id"]
    await sell(client, rs100(data), [], customer_id=cid)
    k = key()
    one = await collect(client, cid, "250.00", idempotency_key=k)
    two = await collect(client, cid, "250.00", idempotency_key=k)
    assert one.json()["id"] == two.json()["id"] and two.json()["replayed"] is True
    assert await balance(client, cid) == D("750.00")


@pytest.mark.asyncio
async def test_overpayment_becomes_advance_and_can_be_applied(client: AsyncClient):
    data = await credit_setup(client)
    cid = data["customer"]["id"]
    adv = await collect(client, cid, "150.00")               # nothing owed yet
    assert D(adv.json()["unapplied_amount"]) == D("150.00")
    assert await balance(client, cid) == D("-150.00")
    inv = await sell(client, rs100(data, "2"), [], customer_id=cid)
    applied = await client.post(f"/api/v1/customers/{cid}/apply-credit", json={})
    assert applied.status_code == 200 and D(applied.json()["applied"][0]["amount"]) == D("150.00")
    after = (await client.get(f"/api/v1/sales/invoices/{inv['id']}")).json()
    assert D(after["balance_due"]) == D("50.00")
    assert await balance(client, cid) == D("50.00") and await reconciled(client, cid)


# ── 8: credit limits and overrides ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_credit_limit_and_override_permissions(client: AsyncClient):
    data = await credit_setup(client, credit_limit="500.00")
    cid = data["customer"]["id"]
    await add_user(client, "Pharmacist", "pharma1")
    await login_as(client, "pharma1", "Staff1234")

    small = await sell(client, rs100(data, "4"), [], customer_id=cid)          # ₹400 within ₹500
    assert small["is_credit_sale"] is True
    over = await client.post("/api/v1/sales/invoices", json={
        "idempotency_key": key(), "customer_id": cid, "items": rs100(data, "2"), "payments": []})
    assert over.status_code == 403 and "Credit limit exceeded" in over.json()["detail"]

    await login_as(client, "admin", "Admin123")
    no_reason = await client.post("/api/v1/sales/invoices", json={
        "idempotency_key": key(), "customer_id": cid, "items": rs100(data, "2"), "payments": []})
    assert no_reason.status_code == 422
    approved = await sell(client, rs100(data, "2"), [], customer_id=cid,
                          credit_override_reason="Regular customer, owner approved")
    assert approved["is_credit_sale"] is True and await balance(client, cid) == D("600.00")


# ── 15, 16, 17: returns ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_partial_then_full_return_with_limits_and_stock_disposition(client: AsyncClient):
    data = await sales_setup(client)
    inv = await sell(client, [line(data, "2")], [{"method": "CASH", "tendered": "100"}])  # 30 tablets, ₹84
    item_id = inv["items"][0]["id"]
    batch_id = data["batch"]["id"]

    k = key()
    first = await client.post("/api/v1/sales/returns", json={
        "idempotency_key": k, "invoice_id": inv["id"], "reason": "Wrong strength",
        "items": [{"invoice_item_id": item_id, "quantity_base": "15", "condition": "SALEABLE"}]})
    assert first.status_code == 201, first.text
    r1 = first.json()
    assert D(r1["total_amount"]) == D("42.00") and D(r1["refund_amount"]) == D("42.00")
    assert r1["refund_method"] == "CASH" and r1["items"][0]["disposition"] == "RESTOCKED"
    assert await batch_stock(client, batch_id) == D("135.00")

    replay = await client.post("/api/v1/sales/returns", json={
        "idempotency_key": k, "invoice_id": inv["id"], "reason": "Wrong strength",
        "items": [{"invoice_item_id": item_id, "quantity_base": "15", "condition": "SALEABLE"}]})
    assert replay.json()["replayed"] is True and replay.json()["id"] == r1["id"]
    assert await batch_stock(client, batch_id) == D("135.00")

    too_many = await client.post("/api/v1/sales/returns", json={
        "idempotency_key": key(), "invoice_id": inv["id"], "reason": "More",
        "items": [{"invoice_item_id": item_id, "quantity_base": "16", "condition": "SALEABLE"}]})
    assert too_many.status_code == 422 and "can still be returned" in too_many.json()["detail"]

    second = await client.post("/api/v1/sales/returns", json={
        "idempotency_key": key(), "invoice_id": inv["id"], "reason": "Strip damaged",
        "items": [{"invoice_item_id": item_id, "quantity_base": "15", "condition": "DAMAGED"}]})
    r2 = second.json()
    assert r2["items"][0]["disposition"] == "WRITTEN_OFF"
    assert await batch_stock(client, batch_id) == D("135.00")                   # damaged goods not restocked
    final = (await client.get(f"/api/v1/sales/invoices/{inv['id']}")).json()
    assert final["return_status"] == "FULL" and D(final["returned_value"]) == D("84.00")
    assert D(final["refunded_amount"]) == D("84.00")

    again = await client.post("/api/v1/sales/returns", json={
        "idempotency_key": key(), "invoice_id": inv["id"], "reason": "Again",
        "items": [{"invoice_item_id": item_id, "quantity_base": "1", "condition": "SALEABLE"}]})
    assert again.status_code == 422


@pytest.mark.asyncio
async def test_return_on_credit_sale_reduces_receivable_first(client: AsyncClient):
    """Scenario F: return against a part-paid credit invoice."""
    data = await credit_setup(client)
    cid = data["customer"]["id"]
    inv = await sell(client, rs100(data), [{"method": "CASH", "tendered": "400"}], customer_id=cid)
    stock_before = await batch_stock(client, data["rs100"]["id"])
    ret = await client.post("/api/v1/sales/returns", json={
        "idempotency_key": key(), "invoice_id": inv["id"], "reason": "Doctor changed prescription",
        "items": [{"invoice_item_id": inv["items"][0]["id"], "quantity_base": "45", "condition": "SALEABLE"}]})
    assert ret.status_code == 201, ret.text
    r = ret.json()
    assert D(r["total_amount"]) == D("300.00") and D(r["applied_to_balance"]) == D("300.00")
    assert D(r["refund_amount"]) == D("0.00")
    after = (await client.get(f"/api/v1/sales/invoices/{inv['id']}")).json()
    assert D(after["balance_due"]) == D("300.00")
    assert await balance(client, cid) == D("300.00") and await reconciled(client, cid)
    assert await batch_stock(client, data["rs100"]["id"]) == stock_before + D("45.00")
    movements = (await client.get(f"/api/v1/inventory/movements?batch_id={data['rs100']['id']}")).json()
    assert any(m["movement_type"] == "SALES_RETURN" and D(m["quantity_base"]) == D("45.00") for m in movements)


@pytest.mark.asyncio
async def test_return_as_customer_credit_note(client: AsyncClient):
    data = await credit_setup(client)
    cid = data["customer"]["id"]
    inv = await sell(client, rs100(data, "2"), [{"method": "UPI", "amount": "200.00", "reference": "U1"}], customer_id=cid)
    ret = await client.post("/api/v1/sales/returns", json={
        "idempotency_key": key(), "invoice_id": inv["id"], "reason": "Not needed", "settlement": "CUSTOMER_CREDIT",
        "items": [{"invoice_item_id": inv["items"][0]["id"], "quantity_base": "15", "condition": "SALEABLE"}]})
    assert D(ret.json()["credit_note_amount"]) == D("100.00")
    assert await balance(client, cid) == D("-100.00") and await reconciled(client, cid)


# ── 18: voids ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_void_reverses_stock_money_and_ledger_with_audit(client: AsyncClient, test_engine):
    data = await credit_setup(client)
    cid = data["customer"]["id"]
    inv = await sell(client, rs100(data, "5"), [{"method": "CASH", "tendered": "200"}], customer_id=cid)
    stock_after_sale = await batch_stock(client, data["rs100"]["id"])
    assert await balance(client, cid) == D("300.00")

    voided = await client.post(f"/api/v1/sales/invoices/{inv['id']}/void", json={"reason": "Billed wrong customer"})
    assert voided.status_code == 200, voided.text
    v = voided.json()
    assert v["status"] == "VOIDED" and D(v["balance_due"]) == D("0.00") and D(v["refunded_amount"]) == D("200.00")
    assert await batch_stock(client, data["rs100"]["id"]) == stock_after_sale + D("75.00")
    assert await balance(client, cid) == D("0.00") and await reconciled(client, cid)
    assert (await client.post(f"/api/v1/sales/invoices/{inv['id']}/void",
                              json={"reason": "Twice"})).status_code == 409
    async with async_sessionmaker(bind=test_engine, class_=AsyncSession)() as db:
        log = (await db.execute(select(AuditLog).where(AuditLog.action == "sale.void"))).scalar_one()
    assert log.reason == "Billed wrong customer"

    returned = await sell(client, rs42(data), [{"method": "CASH", "tendered": "50"}])
    await client.post("/api/v1/sales/returns", json={
        "idempotency_key": key(), "invoice_id": returned["id"], "reason": "Return",
        "items": [{"invoice_item_id": returned["items"][0]["id"], "quantity_base": "5", "condition": "SALEABLE"}]})
    blocked = await client.post(f"/api/v1/sales/invoices/{returned['id']}/void", json={"reason": "After return"})
    assert blocked.status_code == 409


@pytest.mark.asyncio
async def test_payment_reversal_and_write_off(client: AsyncClient):
    data = await credit_setup(client)
    cid = data["customer"]["id"]
    inv = await sell(client, rs100(data), [], customer_id=cid)
    pay = (await collect(client, cid, "500.00", method="UPI", reference="UPI-BOUNCED")).json()
    rev = await client.post(f"/api/v1/sales/payments/{pay['id']}/reverse", json={"reason": "UPI never credited"})
    assert rev.status_code == 200 and rev.json()["status"] == "REVERSED"
    assert await balance(client, cid) == D("1000.00")
    wo = await client.post(f"/api/v1/customers/{cid}/adjustments", json={
        "invoice_id": inv["id"], "adjustment_type": "WRITE_OFF", "amount": "100.00", "reason": "Goodwill settlement"})
    assert wo.status_code == 201
    assert await balance(client, cid) == D("900.00") and await reconciled(client, cid)


# ── 26: day-end cash reconciliation (Scenario H) ───────────────────────────────

@pytest.mark.asyncio
async def test_shift_cash_reconciliation_separates_non_cash(client: AsyncClient):
    data = await credit_setup(client)
    cid = data["customer"]["id"]
    shift = (await client.post("/api/v1/shifts", json={"opening_cash": "1000.00"})).json()
    await sell(client, rs42(data, "2"), [{"method": "CASH", "tendered": "100"}])             # cash +84
    await sell(client, rs42(data, "2"), [{"method": "UPI", "amount": "84.00"}])              # UPI 84
    credit = await sell(client, rs100(data, "3"), [], customer_id=cid)                       # credit 300
    await collect(client, cid, "50.00")                                                      # cash +50
    cash_sale = await sell(client, rs42(data), [{"method": "CASH", "amount": "42.00"}])
    await client.post("/api/v1/sales/returns", json={                                        # cash -42
        "idempotency_key": key(), "invoice_id": cash_sale["id"], "reason": "Return",
        "items": [{"invoice_item_id": cash_sale["items"][0]["id"], "quantity_base": "15", "condition": "SALEABLE"}]})
    out = await client.post(f"/api/v1/shifts/{shift['id']}/cash-movements",
                            json={"movement_type": "PAID_OUT", "category": "TRANSFER_TO_SAFE", "amount": "20.00",
                                  "reason": "Excess cash to safe"})
    assert out.status_code == 201

    summary = (await client.get(f"/api/v1/shifts/{shift['id']}")).json()
    assert D(summary["cash_sales"]) == D("126.00") and D(summary["cash_collections"]) == D("50.00")
    assert D(summary["cash_refunds"]) == D("42.00") and D(summary["paid_out"]) == D("20.00")
    assert D(summary["expected_cash"]) == D("1114.00")       # 1000 + 126 + 50 - 42 - 20
    assert D(summary["non_cash_totals"]["UPI"]) == D("84.00")
    assert D(summary["non_cash_totals"]["CREDIT_SALES"]) == D(credit["grand_total"])

    no_note = await client.post(f"/api/v1/shifts/{shift['id']}/close", json={"counted_cash": "1110.00"})
    assert no_note.status_code == 422
    closed = (await client.post(f"/api/v1/shifts/{shift['id']}/close",
                                json={"counted_cash": "1110.00", "variance_note": "₹4 short, coins"})).json()
    assert D(closed["variance"]) == D("-4.00") and closed["needs_review"] is False

    day = (await client.get("/api/v1/shifts/day-end")).json()
    assert D(day["by_method"]["CASH"]["sales"]) == D("126.00")
    assert D(day["by_method"]["CASH"]["refunds"]) == D("42.00")
    assert D(day["by_method"]["UPI"]["sales"]) == D("84.00")
    assert "CREDIT" not in day["by_method"]


# ── 20: manual WhatsApp share ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_manual_whatsapp_share_link_and_duplicate_guard(client: AsyncClient):
    data = await credit_setup(client)
    cid = data["customer"]["id"]
    inv = await sell(client, rs100(data, "4"), [{"method": "UPI", "amount": "100.00", "reference": "U"}], customer_id=cid)
    resp = await client.post(f"/api/v1/sales/invoices/{inv['id']}/whatsapp", json={})
    assert resp.status_code == 201, resp.text
    msg = resp.json()
    assert msg["mode"] == "MANUAL" and msg["status"] == "MANUAL_SHARE_OPENED"
    assert "delivery not tracked" in msg["status_label"] and msg["delivered_at"] is None
    url = msg["share_url"]
    assert url.startswith("https://wa.me/919876543210?text=Hello%20Ramesh%20Kumar%2C")
    assert "%E2%82%B9400.00" in url and "Outstanding%3A%20%E2%82%B9300.00" in url
    assert "Dolo" not in msg["body"]                          # no medicine list in the message

    dup = await client.post(f"/api/v1/sales/invoices/{inv['id']}/whatsapp", json={})
    assert dup.status_code == 409
    forced = await client.post(f"/api/v1/sales/invoices/{inv['id']}/whatsapp", json={"force_resend": True})
    assert forced.status_code == 201

    no_consent = await create_customer(client, name="Asha", mobile="9123456780", consent=False)
    inv2 = await sell(client, rs100(data, "1"), [{"method": "CASH", "amount": "100.00"}], customer_id=no_consent["id"])
    refused = await client.post(f"/api/v1/sales/invoices/{inv2['id']}/whatsapp", json={})
    assert refused.status_code == 422
    allowed = await client.post(f"/api/v1/sales/invoices/{inv2['id']}/whatsapp", json={"consent_confirmed": True})
    assert allowed.status_code == 201
    assert (await client.get(f"/api/v1/customers/{no_consent['id']}")).json()["whatsapp_consent"] is True


# ── 21, 22: Cloud API (mocked) and webhook ─────────────────────────────────────

@pytest.fixture
def cloud_api(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_PROVIDER", "cloud_api")
    monkeypatch.setattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "1234567890")
    monkeypatch.setattr(settings, "WHATSAPP_ACCESS_TOKEN", "test-token-not-real")
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", "test-app-secret")
    monkeypatch.setattr(settings, "WHATSAPP_WEBHOOK_VERIFY_TOKEN", "verify-me")
    state = {"requests": [], "responses": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        status, body = state["responses"].pop(0) if state["responses"] else (200, {"messages": [{"id": "wamid.TEST1"}]})
        return httpx.Response(status, json=body)

    monkeypatch.setattr(messaging, "http_client_factory",
                        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return state


@pytest.mark.asyncio
async def test_cloud_api_send_failure_and_retry(client: AsyncClient, cloud_api):
    data = await credit_setup(client)
    inv = await sell(client, rs100(data, "1"), [{"method": "CASH", "amount": "100.00"}], customer_id=data["customer"]["id"])

    sent = (await client.post(f"/api/v1/sales/invoices/{inv['id']}/whatsapp", json={})).json()
    assert sent["mode"] == "CLOUD_API" and sent["status"] == "SENT"
    assert sent["provider_message_id"] == "wamid.TEST1" and sent["delivered_at"] is None
    req = cloud_api["requests"][0]
    assert req.url.path == "/v21.0/1234567890/messages"
    assert req.headers["Authorization"] == "Bearer test-token-not-real"
    payload = json.loads(req.content)
    assert payload["to"] == "919876543210" and payload["type"] == "text"

    cloud_api["responses"].append((400, {"error": {"message": "Recipient not on WhatsApp", "code": 131026}}))
    failed = (await client.post(f"/api/v1/sales/invoices/{inv['id']}/whatsapp", json={"force_resend": True})).json()
    assert failed["status"] == "FAILED" and "131026" in failed["last_error"]
    assert "test-token" not in failed["last_error"]
    retried = (await client.post(f"/api/v1/messaging/{failed['id']}/retry")).json()
    assert retried["id"] == failed["id"] and retried["status"] == "SENT" and retried["attempts"] == 2

    cloud_api["responses"].append((503, {"error": {"message": "Service unavailable"}}))
    later = (await client.post(f"/api/v1/sales/invoices/{inv['id']}/whatsapp", json={"force_resend": True})).json()
    assert later["status"] == "QUEUED" and "503" in later["last_error"]          # retried by the worker later


def _signed(body: dict, secret: str = "test-app-secret"):
    raw = json.dumps(body).encode()
    return raw, "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def _status_payload(msg_id: str, status: str, ts: int = 1790000000):
    return {"entry": [{"changes": [{"value": {"statuses": [{"id": msg_id, "status": status, "timestamp": str(ts)}]}}]}]}


@pytest.mark.asyncio
async def test_webhook_signature_and_forward_only_status(client: AsyncClient, cloud_api):
    data = await credit_setup(client)
    inv = await sell(client, rs100(data, "1"), [{"method": "CASH", "amount": "100.00"}], customer_id=data["customer"]["id"])
    msg = (await client.post(f"/api/v1/sales/invoices/{inv['id']}/whatsapp", json={})).json()

    challenge = await client.get("/api/v1/whatsapp/webhook", params={
        "hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "12345"})
    assert challenge.status_code == 200 and challenge.text == "12345"
    assert (await client.get("/api/v1/whatsapp/webhook", params={
        "hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"})).status_code == 403

    raw, sig = _signed(_status_payload("wamid.TEST1", "delivered"))
    forged = await client.post("/api/v1/whatsapp/webhook", content=raw,
                               headers={"X-Hub-Signature-256": "sha256=" + "0" * 64, "Content-Type": "application/json"})
    assert forged.status_code == 401
    ok = await client.post("/api/v1/whatsapp/webhook", content=raw,
                           headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"})
    assert ok.status_code == 200 and ok.json()["updated"] == 1

    for status in ("read", "sent", "failed"):        # read moves forward; late "sent"/"failed" are ignored
        raw, sig = _signed(_status_payload("wamid.TEST1", status))
        await client.post("/api/v1/whatsapp/webhook", content=raw,
                          headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"})
    msgs = (await client.get(f"/api/v1/sales/invoices/{inv['id']}/messages")).json()
    final = next(m for m in msgs if m["id"] == msg["id"])
    assert final["status"] == "READ" and final["delivered_at"] and final["read_at"]


# ── 23, 24: reminders ──────────────────────────────────────────────────────────

async def scan(client, on_date, dry_run=True):
    resp = await client.post("/api/v1/messaging/reminders/scan", json={"dry_run": dry_run, "on_date": str(on_date)})
    assert resp.status_code == 200, resp.text
    return resp.json()["results"]


@pytest.mark.asyncio
async def test_reminder_windows_eligibility_and_duplicates(client: AsyncClient):
    """Scenario E: due dates, test-mode scan, duplicate protection, paid invoices get nothing."""
    data = await credit_setup(client)
    cid = data["customer"]["id"]
    inv = await sell(client, rs100(data, "3"), [], customer_id=cid)
    due = date.fromisoformat(inv["due_date"])

    assert await scan(client, due - timedelta(days=5)) == []
    upcoming = await scan(client, due - timedelta(days=3))
    assert upcoming[0]["purpose"] == "REMINDER_UPCOMING" and upcoming[0]["eligible"]
    assert (await scan(client, due))[0]["purpose"] == "REMINDER_DUE_TODAY"

    overdue_day = due + timedelta(days=2)
    preview = await scan(client, overdue_day)
    assert preview[0]["purpose"] == "REMINDER_OVERDUE" and preview[0]["eligible"]
    outbox = (await client.get("/api/v1/messaging/outbox")).json()
    assert outbox["total"] == 0                                    # dry run wrote nothing

    created = await scan(client, overdue_day, dry_run=False)
    assert created[0]["message_id"] and created[0]["eligible"]
    pending = (await client.get("/api/v1/messaging/outbox?status=PENDING")).json()["items"]
    assert len(pending) == 1 and "remains outstanding" in pending[0]["body"]
    again = await scan(client, overdue_day + timedelta(days=1), dry_run=False)   # same 7-day window
    assert again[0]["eligible"] is False and "Already created" in again[0]["reason"]
    next_window = await scan(client, due + timedelta(days=9))
    assert next_window[0]["eligible"] is True                       # a new window may remind again

    await client.put(f"/api/v1/customers/{cid}", json={"reminders_opt_out": True})
    opted = await scan(client, due + timedelta(days=9))
    assert opted[0]["eligible"] is False and "opted out" in opted[0]["reason"]
    await client.put(f"/api/v1/customers/{cid}", json={"reminders_opt_out": False})

    await client.patch(f"/api/v1/sales/invoices/{inv['id']}/reminders", json={"is_disputed": True, "dispute_note": "Says paid by cheque"})
    disputed = await scan(client, due + timedelta(days=9))
    assert "disputed" in disputed[0]["reason"]
    await client.patch(f"/api/v1/sales/invoices/{inv['id']}/reminders", json={"is_disputed": False})

    paid = await collect(client, cid, "300.00")
    assert paid.status_code == 201
    assert await scan(client, due + timedelta(days=9), dry_run=False) == []  # fully paid: no reminder
    stale = (await client.get("/api/v1/messaging/outbox")).json()["items"][0]
    assert stale["status"] == "CANCELLED"                                    # pending one was withdrawn
    share = await client.post(f"/api/v1/messaging/{stale['id']}/share")
    assert share.status_code == 409


@pytest.mark.asyncio
async def test_manual_reminder_once_per_day(client: AsyncClient):
    data = await credit_setup(client)
    inv = await sell(client, rs100(data, "2"), [], customer_id=data["customer"]["id"])
    first = await client.post(f"/api/v1/sales/invoices/{inv['id']}/reminder", json={})
    assert first.status_code == 201 and first.json()["purpose"] == "REMINDER_UPCOMING"
    assert first.json()["share_url"].startswith("https://wa.me/919876543210?text=")
    assert (await client.post(f"/api/v1/sales/invoices/{inv['id']}/reminder", json={})).status_code == 409
