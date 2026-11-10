"""
Customer messaging over WhatsApp (provider-independent) with a durable outbox.

Modes
-----
CLOUD_API  Official WhatsApp Business Platform (Cloud API). Used only when
           WHATSAPP_PROVIDER=cloud_api and the phone-number ID and access token
           are set. Messages are queued in `message_outbox`, committed, and then
           sent outside any sale transaction; provider message IDs and webhook
           status updates (sent / delivered / read / failed) are recorded.
MANUAL     No credentials needed. The app builds a wa.me click-to-chat link with
           the message text; staff review and press Send in WhatsApp themselves.
           Such messages are recorded as MANUAL_SHARE_OPENED, never as delivered,
           because the app cannot know whether they were sent.

Safety
------
* Tokens are read from environment variables only and never returned to the browser.
* A message row is committed before the provider is called; a crash mid-send leaves
  it in SENDING, which the worker turns into FAILED ("outcome unknown") instead of
  re-sending, so customers do not get duplicates.
* Ordinary messages carry no medicine list or prescription details.
"""
import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable, Optional, Tuple
from urllib.parse import quote

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.auth import AuditLog, Pharmacy, User
from app.models.sales import Customer, MessageOutbox, SalesInvoice
from app.services import app_settings
from app.services.sales_common import (
    BUSINESS_TZ, ZERO, local_date, normalize_mobile, require, today_local, utcnow,
)

STATUS_LABELS = {
    "PENDING": "Pending — not sent yet",
    "QUEUED": "Queued for sending",
    "SENDING": "Sending",
    "SENT": "Sent to WhatsApp — delivery not yet confirmed",
    "DELIVERED": "Delivered (confirmed by WhatsApp)",
    "READ": "Read (confirmed by WhatsApp)",
    "FAILED": "Failed",
    "CANCELLED": "Cancelled",
    "MANUAL_SHARE_OPENED": "Opened in WhatsApp for staff to send — delivery not tracked",
}
STATUS_RANK = {"SENT": 1, "DELIVERED": 2, "READ": 3}
REMINDER_PURPOSES = ("REMINDER_UPCOMING", "REMINDER_DUE_TODAY", "REMINDER_OVERDUE")
TEMPLATE_KEYS = {
    "INVOICE": "messaging.template.invoice",
    "REMINDER_UPCOMING": "messaging.template.reminder_upcoming",
    "REMINDER_DUE_TODAY": "messaging.template.reminder_due_today",
    "REMINDER_OVERDUE": "messaging.template.reminder_overdue",
}
MAX_ATTEMPTS = 5


def cloud_configured() -> bool:
    return (settings.WHATSAPP_PROVIDER.strip().lower() == "cloud_api"
            and bool(settings.WHATSAPP_PHONE_NUMBER_ID) and bool(settings.WHATSAPP_ACCESS_TOKEN))


def default_mode() -> str:
    return "CLOUD_API" if cloud_configured() else "MANUAL"


def provider_status() -> dict:
    """Safe description of the configuration (no secrets)."""
    return {
        "provider": settings.WHATSAPP_PROVIDER,
        "cloud_api_configured": cloud_configured(),
        "phone_number_id_set": bool(settings.WHATSAPP_PHONE_NUMBER_ID),
        "access_token_set": bool(settings.WHATSAPP_ACCESS_TOKEN),
        "app_secret_set": bool(settings.WHATSAPP_APP_SECRET),
        "webhook_verify_token_set": bool(settings.WHATSAPP_WEBHOOK_VERIFY_TOKEN),
        "api_version": settings.WHATSAPP_API_VERSION,
        "templates": {
            "INVOICE": settings.WHATSAPP_TEMPLATE_INVOICE or None,
            "REMINDER_UPCOMING": settings.WHATSAPP_TEMPLATE_REMINDER_UPCOMING or None,
            "REMINDER_DUE_TODAY": settings.WHATSAPP_TEMPLATE_REMINDER_DUE_TODAY or None,
            "REMINDER_OVERDUE": settings.WHATSAPP_TEMPLATE_REMINDER_OVERDUE or None,
        },
        "attach_invoice_pdf": settings.WHATSAPP_SEND_INVOICE_PDF,
        "default_mode": default_mode(),
    }


def template_name_for(purpose: str) -> Optional[str]:
    return {
        "INVOICE": settings.WHATSAPP_TEMPLATE_INVOICE,
        "REMINDER_UPCOMING": settings.WHATSAPP_TEMPLATE_REMINDER_UPCOMING,
        "REMINDER_DUE_TODAY": settings.WHATSAPP_TEMPLATE_REMINDER_DUE_TODAY,
        "REMINDER_OVERDUE": settings.WHATSAPP_TEMPLATE_REMINDER_OVERDUE,
    }.get(purpose) or None


# Template parameters, in the order an approved WhatsApp template must declare {{1}}, {{2}}, ...
TEMPLATE_PARAM_ORDER = {
    "INVOICE": ["customer_name", "pharmacy_name", "invoice_number", "invoice_date", "invoice_total",
                "amount_paid", "balance_due"],
    "REMINDER_UPCOMING": ["customer_name", "balance_due", "invoice_number", "due_date"],
    "REMINDER_DUE_TODAY": ["customer_name", "balance_due", "invoice_number", "due_date"],
    "REMINDER_OVERDUE": ["customer_name", "balance_due", "invoice_number", "due_date", "pharmacy_name"],
}


def render(template: str, ctx: dict) -> str:
    """Replace {name} placeholders only (no attribute access or format specs)."""
    return re.sub(r"\{(\w+)\}", lambda m: str(ctx[m.group(1)]) if m.group(1) in ctx else m.group(0), template)


def _amount(value) -> str:
    return f"{Decimal(str(value)):,.2f}"


def share_url(to_number: str, body: str) -> str:
    """WhatsApp click-to-chat link: digits-only number and URL-encoded text."""
    return f"https://wa.me/{re.sub(r'[^0-9]', '', to_number)}?text={quote(body, safe='')}"


async def invoice_context(db: AsyncSession, invoice: SalesInvoice) -> dict:
    pharmacy = (await db.execute(select(Pharmacy).where(Pharmacy.id == invoice.pharmacy_id))).scalar_one()
    return {
        "customer_name": invoice.customer_name,
        "pharmacy_name": pharmacy.name,
        "invoice_number": invoice.invoice_number,
        "invoice_date": local_date(invoice.invoice_date).strftime("%d-%m-%Y"),
        "invoice_total": _amount(invoice.grand_total),
        "amount_paid": _amount(invoice.amount_paid),
        "balance_due": _amount(invoice.balance_due),
        "due_date": invoice.due_date.strftime("%d-%m-%Y") if invoice.due_date else "-",
    }


def message_view(msg: MessageOutbox, customer_name: Optional[str] = None,
                 invoice_number: Optional[str] = None) -> dict:
    return {
        "id": msg.id, "mode": msg.mode, "purpose": msg.purpose, "status": msg.status,
        "status_label": STATUS_LABELS.get(msg.status, msg.status), "customer_id": msg.customer_id,
        "customer_name": customer_name, "invoice_id": msg.invoice_id, "invoice_number": invoice_number,
        "to_number": msg.to_number, "body": msg.body, "attempts": msg.attempts, "last_error": msg.last_error,
        "provider_message_id": msg.provider_message_id,
        "share_url": share_url(msg.to_number, msg.body) if msg.mode == "MANUAL" else None,
        "created_at": msg.created_at, "sent_at": msg.sent_at, "delivered_at": msg.delivered_at,
        "read_at": msg.read_at, "failed_at": msg.failed_at,
    }


# ── Creating messages ──────────────────────────────────────────────────────────

async def create_invoice_message(
    db: AsyncSession, user: User, perms: set, invoice: SalesInvoice, to_number: Optional[str],
    mode: Optional[str], consent_confirmed: bool, force_resend: bool,
) -> MessageOutbox:
    require(perms, "messaging.send", "send invoices by WhatsApp")
    if invoice.status != "FINALIZED":
        raise HTTPException(status_code=409, detail="Only finalised invoices can be sent")
    mode = mode or default_mode()
    if mode == "CLOUD_API" and not cloud_configured():
        raise HTTPException(status_code=422, detail="The WhatsApp Cloud API is not configured; use manual sharing")

    customer = None
    if invoice.customer_id:
        customer = (await db.execute(select(Customer).where(Customer.id == invoice.customer_id))).scalar_one()
    number = normalize_mobile(to_number) if to_number else (customer.mobile if customer else None)
    if not number:
        raise HTTPException(status_code=422, detail="Enter the customer's WhatsApp number")

    if customer and not customer.whatsapp_consent:
        if not consent_confirmed:
            raise HTTPException(
                status_code=422,
                detail="This customer has not agreed to WhatsApp messages. Confirm they asked for the invoice on WhatsApp.",
            )
        customer.whatsapp_consent = True
        customer.whatsapp_consent_at = utcnow()
    elif customer is None and not consent_confirmed:
        raise HTTPException(status_code=422, detail="Confirm the customer asked for the invoice on WhatsApp")

    if not force_resend:
        recent = (await db.execute(
            select(MessageOutbox).where(
                MessageOutbox.invoice_id == invoice.id, MessageOutbox.purpose == "INVOICE",
                MessageOutbox.to_number == number,
                MessageOutbox.status.in_(("QUEUED", "SENDING", "SENT", "DELIVERED", "READ", "MANUAL_SHARE_OPENED")),
                MessageOutbox.created_at >= utcnow() - timedelta(minutes=10),
            )
        )).scalars().first()
        if recent:
            raise HTTPException(
                status_code=409,
                detail=f"This invoice was already sent to {number} a few minutes ago ({STATUS_LABELS[recent.status]}). "
                       f"Use 'Send again' to resend.",
            )

    ctx = await invoice_context(db, invoice)
    body = render(await app_settings.get_setting(db, invoice.pharmacy_id, TEMPLATE_KEYS["INVOICE"]), ctx)
    msg = MessageOutbox(
        pharmacy_id=invoice.pharmacy_id, mode=mode, purpose="INVOICE", customer_id=invoice.customer_id,
        invoice_id=invoice.id, to_number=number, body=body,
        template_name=template_name_for("INVOICE") if mode == "CLOUD_API" else None,
        template_params_json=json.dumps([ctx[k] for k in TEMPLATE_PARAM_ORDER["INVOICE"]]),
        attach_invoice_pdf=mode == "CLOUD_API" and settings.WHATSAPP_SEND_INVOICE_PDF,
        status="QUEUED" if mode == "CLOUD_API" else "MANUAL_SHARE_OPENED",
        sent_at=None, created_by=user.id,
    )
    db.add(msg)
    await db.flush()
    db.add(AuditLog(
        pharmacy_id=invoice.pharmacy_id, user_id=user.id, action="message.invoice", entity_type="message_outbox",
        entity_id=str(msg.id), metadata_json=json.dumps({"invoice_number": invoice.invoice_number, "mode": mode}),
    ))
    return msg


# ── Cloud API client ───────────────────────────────────────────────────────────

class ProviderError(Exception):
    def __init__(self, message: str, retryable: bool):
        super().__init__(message)
        self.message = message[:400]
        self.retryable = retryable


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=20.0)


# Tests replace this factory with one that uses httpx.MockTransport.
http_client_factory: Callable[[], httpx.AsyncClient] = _http_client


def _safe_error(resp: httpx.Response) -> str:
    try:
        err = resp.json().get("error", {})
        return f"HTTP {resp.status_code}: {err.get('message') or err.get('type') or 'error'} (code {err.get('code')})"
    except Exception:
        return f"HTTP {resp.status_code}"


async def send_via_cloud_api(msg: MessageOutbox, pdf: Optional[Tuple[str, bytes]] = None) -> str:
    """Send one message; returns the provider message ID or raises ProviderError."""
    base = f"{settings.WHATSAPP_GRAPH_BASE_URL.rstrip('/')}/{settings.WHATSAPP_API_VERSION}/{settings.WHATSAPP_PHONE_NUMBER_ID}"
    headers = {"Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}"}
    async with http_client_factory() as client:
        try:
            if pdf is not None:
                filename, content = pdf
                up = await client.post(
                    f"{base}/media", headers=headers,
                    data={"messaging_product": "whatsapp", "type": "application/pdf"},
                    files={"file": (filename, content, "application/pdf")},
                )
                if up.status_code != 200:
                    raise ProviderError(f"Media upload failed: {_safe_error(up)}", up.status_code >= 500 or up.status_code == 429)
                payload = {"messaging_product": "whatsapp", "to": msg.to_number, "type": "document",
                           "document": {"id": up.json()["id"], "filename": filename, "caption": msg.body[:1024]}}
            elif msg.template_name:
                params = json.loads(msg.template_params_json or "[]")
                payload = {"messaging_product": "whatsapp", "to": msg.to_number, "type": "template",
                           "template": {"name": msg.template_name,
                                        "language": {"code": settings.WHATSAPP_TEMPLATE_LANGUAGE},
                                        "components": [{"type": "body", "parameters": [
                                            {"type": "text", "text": str(p)} for p in params]}]}}
            else:
                payload = {"messaging_product": "whatsapp", "to": msg.to_number, "type": "text",
                           "text": {"body": msg.body, "preview_url": False}}
            resp = await client.post(f"{base}/messages", headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise ProviderError(f"Network error contacting WhatsApp: {type(exc).__name__}", True)
    if resp.status_code == 200:
        try:
            return resp.json()["messages"][0]["id"]
        except Exception:
            raise ProviderError("WhatsApp accepted the request but returned no message ID", False)
    raise ProviderError(_safe_error(resp), resp.status_code == 429 or resp.status_code >= 500)


# ── Dispatch (separate transactions, never inside a sale) ──────────────────────

async def reminder_still_eligible(db: AsyncSession, msg: MessageOutbox) -> Optional[str]:
    """None if the reminder may still be sent, otherwise the reason it may not."""
    if msg.purpose not in REMINDER_PURPOSES:
        return None
    invoice = (await db.execute(select(SalesInvoice).where(SalesInvoice.id == msg.invoice_id))).scalar_one_or_none()
    if invoice is None or invoice.status != "FINALIZED":
        return "Invoice is no longer active"
    if invoice.balance_due <= ZERO:
        return "Invoice is fully paid"
    if invoice.reminders_paused:
        return "Reminders are paused for this invoice"
    if invoice.is_disputed:
        return "Invoice is disputed"
    customer = (await db.execute(select(Customer).where(Customer.id == msg.customer_id))).scalar_one_or_none()
    if customer is None or not customer.is_active:
        return "Customer is inactive"
    if customer.reminders_opt_out:
        return "Customer opted out of reminders"
    if not customer.whatsapp_consent:
        return "Customer has not consented to WhatsApp"
    return None


async def dispatch_message(session_factory, message_id: int) -> Optional[str]:
    """Send one QUEUED Cloud API message. Returns the resulting status."""
    from app.services.invoice_pdf import invoice_pdf_bytes

    async with session_factory() as db:
        msg = (await db.execute(
            select(MessageOutbox).where(MessageOutbox.id == message_id).with_for_update(skip_locked=True)
        )).scalar_one_or_none()
        if msg is None or msg.status != "QUEUED" or msg.mode != "CLOUD_API":
            return msg.status if msg else None
        if msg.next_attempt_at and msg.next_attempt_at > utcnow():
            return msg.status
        reason = await reminder_still_eligible(db, msg)
        if reason:
            msg.status, msg.cancelled_reason = "CANCELLED", reason
            await db.commit()
            return msg.status
        if not cloud_configured():
            msg.status, msg.last_error, msg.failed_at = "FAILED", "WhatsApp Cloud API is not configured", utcnow()
            await db.commit()
            return msg.status
        pdf = None
        if msg.attach_invoice_pdf and msg.invoice_id:
            pdf = await invoice_pdf_bytes(db, msg.pharmacy_id, msg.invoice_id, copy_label="COPY")
        msg.status = "SENDING"
        msg.attempts += 1
        await db.commit()
        snapshot = MessageOutbox(
            to_number=msg.to_number, body=msg.body, template_name=msg.template_name,
            template_params_json=msg.template_params_json,
        )

    try:
        provider_id = await send_via_cloud_api(snapshot, pdf)
        error = None
    except ProviderError as exc:
        provider_id, error = None, exc

    async with session_factory() as db:
        msg = (await db.execute(select(MessageOutbox).where(MessageOutbox.id == message_id).with_for_update())).scalar_one()
        if provider_id:
            msg.status, msg.provider_message_id, msg.sent_at, msg.last_error = "SENT", provider_id, utcnow(), None
        elif error.retryable and msg.attempts < MAX_ATTEMPTS:
            msg.status, msg.last_error = "QUEUED", error.message
            msg.next_attempt_at = utcnow() + timedelta(minutes=2 ** msg.attempts)
        else:
            msg.status, msg.last_error, msg.failed_at = "FAILED", error.message, utcnow()
        await db.commit()
        return msg.status


async def process_outbox(session_factory, limit: int = 20) -> int:
    """Worker step: recover stuck sends, then send due QUEUED messages. Returns messages attempted."""
    async with session_factory() as db:
        stuck = (await db.execute(
            select(MessageOutbox).where(
                MessageOutbox.status == "SENDING", MessageOutbox.updated_at < utcnow() - timedelta(minutes=10),
            ).with_for_update(skip_locked=True)
        )).scalars().all()
        for msg in stuck:
            msg.status, msg.failed_at = "FAILED", utcnow()
            msg.last_error = "Send outcome unknown (interrupted). Check WhatsApp before retrying."
        due = (await db.execute(
            select(MessageOutbox.id).where(
                MessageOutbox.status == "QUEUED", MessageOutbox.mode == "CLOUD_API",
                (MessageOutbox.next_attempt_at.is_(None)) | (MessageOutbox.next_attempt_at <= utcnow()),
            ).order_by(MessageOutbox.id).limit(limit)
        )).scalars().all()
        await db.commit()
    for message_id in due:
        await dispatch_message(session_factory, message_id)
    return len(due)


async def retry_message(db: AsyncSession, user: User, perms: set, msg: MessageOutbox) -> MessageOutbox:
    """Re-queue a FAILED message (same row: no new invoice, no duplicate record)."""
    require(perms, "messaging.send", "retry messages")
    if msg.status != "FAILED":
        raise HTTPException(status_code=409, detail=f"Only failed messages can be retried (this one is {msg.status})")
    if msg.mode == "CLOUD_API" and not cloud_configured():
        raise HTTPException(status_code=422, detail="The WhatsApp Cloud API is not configured")
    reason = await reminder_still_eligible(db, msg)
    if reason:
        raise HTTPException(status_code=409, detail=f"Reminder no longer applies: {reason}")
    msg.status, msg.next_attempt_at, msg.failed_at = "QUEUED", None, None
    if msg.attempts >= MAX_ATTEMPTS:
        msg.attempts = MAX_ATTEMPTS - 1
    db.add(AuditLog(pharmacy_id=msg.pharmacy_id, user_id=user.id, action="message.retry",
                    entity_type="message_outbox", entity_id=str(msg.id)))
    await db.flush()
    return msg


async def share_pending_manually(db: AsyncSession, user: User, perms: set, msg: MessageOutbox) -> MessageOutbox:
    """Staff open a PENDING (manual) reminder in WhatsApp. Recorded honestly: not delivered."""
    require(perms, "messaging.send", "send reminders")
    if msg.status not in ("PENDING", "FAILED"):
        raise HTTPException(status_code=409, detail=f"Message is {msg.status}")
    reason = await reminder_still_eligible(db, msg)
    if reason:
        msg.status, msg.cancelled_reason = "CANCELLED", reason
        await db.flush()
        raise HTTPException(status_code=409, detail=f"Reminder no longer applies: {reason}")
    msg.mode = "MANUAL"
    msg.status = "MANUAL_SHARE_OPENED"
    db.add(AuditLog(pharmacy_id=msg.pharmacy_id, user_id=user.id, action="message.manual_share",
                    entity_type="message_outbox", entity_id=str(msg.id)))
    await db.flush()
    return msg


# ── Webhook ────────────────────────────────────────────────────────────────────

def verify_webhook_signature(raw_body: bytes, signature_header: Optional[str]) -> bool:
    """Meta signs webhook bodies with the app secret: X-Hub-Signature-256: sha256=<hex>."""
    if not settings.WHATSAPP_APP_SECRET or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(settings.WHATSAPP_APP_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header[len("sha256="):])


async def apply_webhook(db: AsyncSession, payload: dict) -> int:
    """Apply status callbacks. Statuses only move forward (sent → delivered → read); returns rows updated."""
    updated = 0
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            for st in (change.get("value", {}) or {}).get("statuses", []) or []:
                provider_id, status_value = st.get("id"), (st.get("status") or "").upper()
                if not provider_id or status_value not in ("SENT", "DELIVERED", "READ", "FAILED"):
                    continue
                msg = (await db.execute(
                    select(MessageOutbox).where(MessageOutbox.provider_message_id == provider_id).with_for_update()
                )).scalar_one_or_none()
                if msg is None:
                    continue
                try:
                    when = datetime.fromtimestamp(int(st.get("timestamp")), tz=timezone.utc)
                except (TypeError, ValueError):
                    when = utcnow()
                if status_value == "FAILED":
                    if msg.status in ("DELIVERED", "READ"):
                        continue
                    errors = st.get("errors") or [{}]
                    msg.status, msg.failed_at = "FAILED", when
                    msg.last_error = f"{errors[0].get('code', '')} {errors[0].get('title', 'Delivery failed')}".strip()[:400]
                elif STATUS_RANK[status_value] > STATUS_RANK.get(msg.status, 0):
                    msg.status = status_value
                    if status_value == "DELIVERED":
                        msg.delivered_at = when
                    elif status_value == "READ":
                        msg.read_at = when
                        msg.delivered_at = msg.delivered_at or when
                    elif status_value == "SENT":
                        msg.sent_at = msg.sent_at or when
                else:
                    continue
                updated += 1
    await db.flush()
    return updated


# ── Reminders ──────────────────────────────────────────────────────────────────

async def _customer_block(customer: Optional[Customer]) -> Optional[str]:
    if customer is None or not customer.is_active:
        return "Customer is inactive"
    if customer.reminders_opt_out:
        return "Customer opted out of reminders"
    if not customer.whatsapp_consent:
        return "Customer has not consented to WhatsApp"
    if not customer.mobile:
        return "Customer has no mobile number"
    return None


async def scan_reminders(db: AsyncSession, pharmacy_id: int, on_date=None, dry_run: bool = True,
                         created_by: Optional[int] = None) -> list:
    """
    Decide which invoices need a reminder today. One reminder per invoice per window:
      upcoming  - within `upcoming_days_before` days of the due date (key: due date)
      due today - on the due date
      overdue   - one per `overdue_interval_days` window, at most `max_overdue_reminders`
    With dry_run nothing is written. Otherwise rows are created (QUEUED when automatic
    Cloud API sending is enabled, else PENDING for staff to send manually) and stale
    pending reminders for settled invoices are cancelled.
    """
    on = on_date or today_local()
    upcoming_days = await app_settings.get_int(db, pharmacy_id, "reminders.upcoming_days_before")
    interval = max(1, await app_settings.get_int(db, pharmacy_id, "reminders.overdue_interval_days"))
    max_overdue = await app_settings.get_int(db, pharmacy_id, "reminders.max_overdue_reminders")
    auto = await app_settings.get_bool(db, pharmacy_id, "reminders.auto_send_enabled") and cloud_configured()
    start_hour = await app_settings.get_int(db, pharmacy_id, "reminders.send_window_start_hour")
    end_hour = await app_settings.get_int(db, pharmacy_id, "reminders.send_window_end_hour")

    results = []
    invoices = (await db.execute(
        select(SalesInvoice).where(
            SalesInvoice.pharmacy_id == pharmacy_id, SalesInvoice.status == "FINALIZED",
            SalesInvoice.balance_due > 0, SalesInvoice.customer_id.is_not(None), SalesInvoice.due_date.is_not(None),
        ).order_by(SalesInvoice.due_date, SalesInvoice.id)
    )).scalars().all()
    customers = {c.id: c for c in (await db.execute(
        select(Customer).where(Customer.id.in_({i.customer_id for i in invoices} or {0}))
    )).scalars().all()}

    for inv in invoices:
        days_to_due = (inv.due_date - on).days
        purpose = key = None
        if 0 < days_to_due <= upcoming_days:
            purpose, key = "REMINDER_UPCOMING", f"reminder:{inv.id}:UPCOMING:{inv.due_date}"
        elif days_to_due == 0:
            purpose, key = "REMINDER_DUE_TODAY", f"reminder:{inv.id}:DUE_TODAY:{inv.due_date}"
        elif days_to_due < 0:
            window = (-days_to_due - 1) // interval
            if window < max_overdue:
                purpose, key = "REMINDER_OVERDUE", f"reminder:{inv.id}:OVERDUE:{window}"
        if purpose is None:
            continue
        customer = customers.get(inv.customer_id)
        block = await _customer_block(customer)
        if not block and inv.reminders_paused:
            block = "Reminders are paused for this invoice"
        if not block and inv.is_disputed:
            block = "Invoice is disputed"
        existing = (await db.execute(
            select(MessageOutbox.id, MessageOutbox.status).where(
                MessageOutbox.pharmacy_id == pharmacy_id, MessageOutbox.dedupe_key == key)
        )).first()
        result = {"invoice_id": inv.id, "invoice_number": inv.invoice_number, "customer_id": inv.customer_id,
                  "customer_name": inv.customer_name, "purpose": purpose, "due_date": inv.due_date,
                  "balance_due": inv.balance_due, "eligible": block is None and existing is None,
                  "reason": block or (f"Already created (#{existing[0]}, {existing[1]})" if existing else None),
                  "message_id": existing[0] if existing else None}
        if result["eligible"] and not dry_run:
            ctx = await invoice_context(db, inv)
            body = render(await app_settings.get_setting(db, pharmacy_id, TEMPLATE_KEYS[purpose]), ctx)
            next_at = None
            if auto:
                now_local = datetime.now(BUSINESS_TZ)
                if not start_hour <= now_local.hour < end_hour:
                    start = now_local.replace(hour=start_hour, minute=0, second=0, microsecond=0)
                    if now_local.hour >= end_hour:
                        start += timedelta(days=1)
                    next_at = start.astimezone(timezone.utc)
            msg = MessageOutbox(
                pharmacy_id=pharmacy_id, mode="CLOUD_API" if auto else "MANUAL", purpose=purpose,
                customer_id=inv.customer_id, invoice_id=inv.id, to_number=customer.mobile, body=body,
                template_name=template_name_for(purpose) if auto else None,
                template_params_json=json.dumps([ctx[k] for k in TEMPLATE_PARAM_ORDER[purpose]]),
                status="QUEUED" if auto else "PENDING", dedupe_key=key, next_attempt_at=next_at,
                created_by=created_by,
            )
            try:
                async with db.begin_nested():
                    db.add(msg)
                    await db.flush()
                result["message_id"] = msg.id
            except IntegrityError:
                result.update(eligible=False, reason="Created by a concurrent run")
        results.append(result)

    if not dry_run:
        stale = (await db.execute(
            select(MessageOutbox).where(
                MessageOutbox.pharmacy_id == pharmacy_id, MessageOutbox.purpose.in_(REMINDER_PURPOSES),
                MessageOutbox.status.in_(("PENDING", "QUEUED")),
            )
        )).scalars().all()
        for msg in stale:
            reason = await reminder_still_eligible(db, msg)
            if reason:
                msg.status, msg.cancelled_reason = "CANCELLED", reason
        await db.flush()
    return results


async def create_manual_reminder(db: AsyncSession, user: User, perms: set, invoice: SalesInvoice,
                                 mode: Optional[str]) -> MessageOutbox:
    """Reminder requested from the invoice / ledger screen. At most one per invoice per day."""
    require(perms, "messaging.send", "send payment reminders")
    if invoice.status != "FINALIZED" or invoice.balance_due <= ZERO:
        raise HTTPException(status_code=409, detail="Nothing is due on this invoice")
    customer = (await db.execute(select(Customer).where(Customer.id == invoice.customer_id))).scalar_one_or_none()
    block = await _customer_block(customer)
    if block:
        raise HTTPException(status_code=422, detail=block)
    if invoice.reminders_paused or invoice.is_disputed:
        raise HTTPException(status_code=409, detail="Reminders are paused or the invoice is disputed")
    mode = mode or default_mode()
    if mode == "CLOUD_API" and not cloud_configured():
        raise HTTPException(status_code=422, detail="The WhatsApp Cloud API is not configured; use manual sharing")
    today = today_local()
    if invoice.due_date and invoice.due_date > today:
        purpose = "REMINDER_UPCOMING"
    elif invoice.due_date == today:
        purpose = "REMINDER_DUE_TODAY"
    else:
        purpose = "REMINDER_OVERDUE"
    key = f"manual-reminder:{invoice.id}:{today}"
    if (await db.execute(select(MessageOutbox.id).where(
            MessageOutbox.pharmacy_id == invoice.pharmacy_id, MessageOutbox.dedupe_key == key))).first():
        raise HTTPException(status_code=409, detail="A reminder for this invoice was already sent today")
    ctx = await invoice_context(db, invoice)
    msg = MessageOutbox(
        pharmacy_id=invoice.pharmacy_id, mode=mode, purpose=purpose, customer_id=invoice.customer_id,
        invoice_id=invoice.id, to_number=customer.mobile,
        body=render(await app_settings.get_setting(db, invoice.pharmacy_id, TEMPLATE_KEYS[purpose]), ctx),
        template_name=template_name_for(purpose) if mode == "CLOUD_API" else None,
        template_params_json=json.dumps([ctx[k] for k in TEMPLATE_PARAM_ORDER[purpose]]),
        status="QUEUED" if mode == "CLOUD_API" else "MANUAL_SHARE_OPENED", dedupe_key=key, created_by=user.id,
    )
    db.add(msg)
    await db.flush()
    db.add(AuditLog(pharmacy_id=invoice.pharmacy_id, user_id=user.id, action="message.reminder",
                    entity_type="message_outbox", entity_id=str(msg.id),
                    metadata_json=json.dumps({"invoice_number": invoice.invoice_number, "mode": mode})))
    return msg
