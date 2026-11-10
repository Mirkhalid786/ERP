"""WhatsApp configuration, message outbox, reminder scans and the provider webhook."""
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db, get_session_factory
from app.core.json import decimal_json
from app.core.deps import AuthContext, require_context
from app.models.auth import AuditLog
from app.models.sales import MessageOutbox
from app.schemas.sales import CommunicationSettingsUpdate, MessageResponse, ReminderScanRequest
from app.services import app_settings, messaging
from app.api.v1.routes.sales import _message_response

router = APIRouter(tags=["Messaging"])


@router.get("/messaging/config")
async def get_config(
    ctx: AuthContext = Depends(require_context("messaging.configure")),
    db: AsyncSession = Depends(get_db),
):
    """Templates, reminder settings and provider status. Secrets are never returned."""
    values = {key: await app_settings.get_setting(db, ctx.pharmacy_id, key) for key in app_settings.COMMUNICATION_KEYS}
    return {
        "provider": messaging.provider_status(),
        "settings": values,
        "descriptions": {k: app_settings.DEFAULTS[k][1] for k in app_settings.COMMUNICATION_KEYS},
        "placeholders": ["customer_name", "pharmacy_name", "invoice_number", "invoice_date", "invoice_total",
                         "amount_paid", "balance_due", "due_date"],
        "template_param_order": messaging.TEMPLATE_PARAM_ORDER,
    }


@router.put("/messaging/config")
async def update_config(
    body: CommunicationSettingsUpdate,
    ctx: AuthContext = Depends(require_context("messaging.configure")),
    db: AsyncSession = Depends(get_db),
):
    for key, value in body.values.items():
        if key not in app_settings.COMMUNICATION_KEYS:
            raise HTTPException(status_code=422, detail=f"'{key}' is not a communication setting")
        value = str(value).strip()
        if key.startswith("messaging.template.") and not (10 <= len(value) <= 1000):
            raise HTTPException(status_code=422, detail="Templates must be 10-1000 characters")
        if key.startswith("reminders.") and key != "reminders.auto_send_enabled" and not value.isdigit():
            raise HTTPException(status_code=422, detail=f"'{key}' must be a whole number")
        if key == "reminders.auto_send_enabled":
            value = "true" if value.lower() in ("1", "true", "yes", "on") else "false"
            if value == "true" and not messaging.cloud_configured():
                raise HTTPException(status_code=422, detail="Automatic sending needs the WhatsApp Cloud API configured")
        await app_settings.set_setting(db, ctx.pharmacy_id, key, value, ctx.user.id)
    db.add(AuditLog(pharmacy_id=ctx.pharmacy_id, user_id=ctx.user.id, action="messaging.configure",
                    entity_type="settings", entity_id="communications",
                    metadata_json=json.dumps({"keys": sorted(body.values)})))
    return await get_config(ctx=ctx, db=db)


@router.get("/messaging/outbox")
async def outbox(
    status_filter: Optional[str] = Query(None, alias="status"),
    purpose: Optional[str] = None,
    customer_id: Optional[int] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: AuthContext = Depends(require_context("messaging.send")),
    db: AsyncSession = Depends(get_db),
):
    conds = [MessageOutbox.pharmacy_id == ctx.pharmacy_id]
    if status_filter:
        conds.append(MessageOutbox.status == status_filter.upper())
    if purpose:
        conds.append(MessageOutbox.purpose == purpose.upper())
    if customer_id:
        conds.append(MessageOutbox.customer_id == customer_id)
    total = (await db.execute(select(func.count(MessageOutbox.id)).where(*conds))).scalar_one()
    rows = (await db.execute(
        select(MessageOutbox).where(*conds).order_by(MessageOutbox.created_at.desc()).offset(offset).limit(limit)
    )).scalars().all()
    return {"items": [await _message_response(db, m) for m in rows], "total": total}


async def _get_message(db: AsyncSession, pharmacy_id: int, message_id: int) -> MessageOutbox:
    msg = (await db.execute(
        select(MessageOutbox).where(MessageOutbox.id == message_id, MessageOutbox.pharmacy_id == pharmacy_id)
        .with_for_update()
    )).scalar_one_or_none()
    if msg is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return msg


@router.post("/messaging/{message_id}/retry", response_model=MessageResponse)
async def retry(
    message_id: int,
    ctx: AuthContext = Depends(require_context("messaging.send")),
    db: AsyncSession = Depends(get_db),
    session_factory=Depends(get_session_factory),
):
    msg = await _get_message(db, ctx.pharmacy_id, message_id)
    if msg.mode == "MANUAL":
        msg = await messaging.share_pending_manually(db, ctx.user, ctx.permissions, msg)
    else:
        msg = await messaging.retry_message(db, ctx.user, ctx.permissions, msg)
        await db.commit()
        await messaging.dispatch_message(session_factory, msg.id)
        await db.refresh(msg)
    return await _message_response(db, msg)


@router.post("/messaging/{message_id}/share", response_model=MessageResponse)
async def share(
    message_id: int,
    ctx: AuthContext = Depends(require_context("messaging.send")),
    db: AsyncSession = Depends(get_db),
):
    """Open a pending reminder in WhatsApp (manual mode). Returns the wa.me link."""
    msg = await _get_message(db, ctx.pharmacy_id, message_id)
    msg = await messaging.share_pending_manually(db, ctx.user, ctx.permissions, msg)
    return await _message_response(db, msg)


@router.post("/messaging/{message_id}/cancel", response_model=MessageResponse)
async def cancel(
    message_id: int,
    ctx: AuthContext = Depends(require_context("messaging.send")),
    db: AsyncSession = Depends(get_db),
):
    msg = await _get_message(db, ctx.pharmacy_id, message_id)
    if msg.status not in ("PENDING", "QUEUED", "FAILED"):
        raise HTTPException(status_code=409, detail=f"A {msg.status} message cannot be cancelled")
    msg.status, msg.cancelled_reason = "CANCELLED", f"Cancelled by {ctx.user.name}"
    await db.flush()
    return await _message_response(db, msg)


@router.post("/messaging/reminders/scan")
async def scan(
    body: ReminderScanRequest,
    ctx: AuthContext = Depends(require_context("messaging.send")),
    db: AsyncSession = Depends(get_db),
):
    """Run the reminder scan now. dry_run=true only reports what would be created (test mode)."""
    if not body.dry_run and "messaging.configure" not in ctx.permissions:
        raise HTTPException(status_code=403, detail="Creating reminders in bulk needs 'messaging.configure'")
    results = await messaging.scan_reminders(db, ctx.pharmacy_id, on_date=body.on_date, dry_run=body.dry_run,
                                             created_by=ctx.user.id)
    return decimal_json({"dry_run": body.dry_run, "results": results})


# ── Provider webhook (public; authenticated by signature) ──────────────────────

@router.get("/whatsapp/webhook", response_class=PlainTextResponse)
async def webhook_verify(
    hub_mode: str = Query("", alias="hub.mode"),
    hub_verify_token: str = Query("", alias="hub.verify_token"),
    hub_challenge: str = Query("", alias="hub.challenge"),
):
    """Meta's subscription handshake: echo the challenge when the verify token matches."""
    import hmac as _hmac
    if (hub_mode == "subscribe" and settings.WHATSAPP_WEBHOOK_VERIFY_TOKEN
            and _hmac.compare_digest(hub_verify_token, settings.WHATSAPP_WEBHOOK_VERIFY_TOKEN)):
        return PlainTextResponse(hub_challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/whatsapp/webhook")
async def webhook_receive(request: Request, db: AsyncSession = Depends(get_db)):
    raw = await request.body()
    if not messaging.verify_webhook_signature(raw, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=401, detail="Invalid signature")
    try:
        payload = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    updated = await messaging.apply_webhook(db, payload)
    return {"updated": updated}
