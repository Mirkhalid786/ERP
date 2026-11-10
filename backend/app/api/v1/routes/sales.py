"""Sales invoices: finalise, search, view, print, void, prescription, WhatsApp."""
import json
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, get_session_factory
from app.core.deps import AuthContext, require_context
from app.models.auth import AuditLog, Pharmacy, User
from app.models.sales import Customer, MessageOutbox, SalesInvoice
from app.schemas.sales import (
    InvoiceListResponse, InvoiceResponse, MessageResponse, PrescriptionResponse, PrintRecordResponse,
    ReminderControlRequest, ReminderRequest, SaleFinalizeRequest, SendInvoiceMessageRequest, VoidRequest,
)
from app.services import app_settings, messaging, sales
from app.services.invoice_pdf import invoice_pdf_bytes
from app.services.sales_reports import to_csv

router = APIRouter(prefix="/sales/invoices", tags=["Sales"])


@router.post("", response_model=InvoiceResponse, status_code=status.HTTP_201_CREATED)
async def finalize_sale(
    body: SaleFinalizeRequest,
    response: Response,
    ctx: AuthContext = Depends(require_context("sales.create")),
    db: AsyncSession = Depends(get_db),
):
    """
    Finalise a counter sale in one transaction. Retrying with the same
    idempotency_key returns the original invoice (HTTP 200, replayed=true)
    instead of selling again.
    """
    pharmacy_id = ctx.pharmacy_id          # Read before a rollback expires the loaded user
    try:
        invoice, replayed, change = await sales.finalize_sale(db, ctx.user, ctx.permissions, body)
    except sales.DuplicateIdempotencyKey:
        # A concurrent request with the same key committed first: return its sale
        await db.rollback()
        invoice = await sales.invoice_by_key(db, pharmacy_id, body.idempotency_key)
        if invoice is None:
            raise HTTPException(status_code=409, detail="Duplicate submission; reload the sales history")
        replayed, change = True, 0
    if replayed:
        response.status_code = status.HTTP_200_OK
    return await sales.invoice_response(db, invoice, replayed=replayed, change_due=change)


def _filters(q, date_from, date_to, cashier_id, payment_method, payment_status, status_filter, customer_id,
             overdue_only, credit_only):
    return dict(q=q, date_from=date_from, date_to=date_to, cashier_id=cashier_id, payment_method=payment_method,
                payment_status=payment_status, status_filter=status_filter, customer_id=customer_id,
                overdue_only=overdue_only, credit_only=credit_only)


@router.get("", response_model=InvoiceListResponse)
async def list_invoices(
    q: Optional[str] = Query(None, max_length=100),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    cashier_id: Optional[int] = None,
    payment_method: Optional[str] = None,
    payment_status: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    customer_id: Optional[int] = None,
    overdue_only: bool = False,
    credit_only: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: AuthContext = Depends(require_context("sales.view")),
    db: AsyncSession = Depends(get_db),
):
    items, total = await sales.list_invoices(
        db, ctx.pharmacy_id, limit, offset,
        **_filters(q, date_from, date_to, cashier_id, payment_method, payment_status, status_filter,
                   customer_id, overdue_only, credit_only))
    return InvoiceListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/export.csv", response_class=PlainTextResponse)
async def export_invoices(
    q: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    cashier_id: Optional[int] = None,
    payment_method: Optional[str] = None,
    payment_status: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    customer_id: Optional[int] = None,
    overdue_only: bool = False,
    credit_only: bool = False,
    ctx: AuthContext = Depends(require_context("reports.export")),
    db: AsyncSession = Depends(get_db),
):
    items, _ = await sales.list_invoices(
        db, ctx.pharmacy_id, 5000, 0,
        **_filters(q, date_from, date_to, cashier_id, payment_method, payment_status, status_filter,
                   customer_id, overdue_only, credit_only))
    rows = [i.model_dump(exclude={"id", "customer_id"}) for i in items]
    return PlainTextResponse(to_csv(rows), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="invoices.csv"'})


@router.get("/{invoice_id}", response_model=InvoiceResponse)
async def get_invoice(
    invoice_id: int,
    ctx: AuthContext = Depends(require_context("sales.view")),
    db: AsyncSession = Depends(get_db),
):
    return await sales.invoice_response(db, await sales.get_invoice(db, ctx.pharmacy_id, invoice_id))


@router.post("/{invoice_id}/print", response_model=PrintRecordResponse)
async def record_print(
    invoice_id: int,
    layout: str = Query("thermal", pattern="^(thermal|a4|pdf)$"),
    ctx: AuthContext = Depends(require_context("sales.view")),
    db: AsyncSession = Depends(get_db),
):
    """Count a (re)print. First print is ORIGINAL, later ones DUPLICATE. No stock or money changes."""
    label, count = await sales.record_print(db, ctx.user, invoice_id, layout)
    pharmacy = (await db.execute(select(Pharmacy).where(Pharmacy.id == ctx.pharmacy_id))).scalar_one()
    return PrintRecordResponse(
        copy_label=label, print_count=count,
        pharmacy={
            "name": pharmacy.name, "legal_name": pharmacy.legal_name, "address_line1": pharmacy.address_line1,
            "city": pharmacy.city, "pincode": pharmacy.pincode, "phone": pharmacy.phone, "email": pharmacy.email,
            "gstin": pharmacy.gstin, "drug_license_number": pharmacy.drug_license_number,
        },
        footer_note=await app_settings.get_setting(db, ctx.pharmacy_id, "invoice.footer_note"),
        receipt_footer=await app_settings.get_setting(db, ctx.pharmacy_id, "branding.receipt_footer", default=""),
        website=await app_settings.get_setting(db, ctx.pharmacy_id, "branding.website", default=""),
    )


@router.get("/{invoice_id}/pdf")
async def invoice_pdf(
    invoice_id: int,
    ctx: AuthContext = Depends(require_context("sales.view")),
    db: AsyncSession = Depends(get_db),
):
    label, _ = await sales.record_print(db, ctx.user, invoice_id, "pdf")
    filename, content = await invoice_pdf_bytes(db, ctx.pharmacy_id, invoice_id, label)
    return Response(content=content, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/{invoice_id}/void", response_model=InvoiceResponse)
async def void_invoice(
    invoice_id: int,
    body: VoidRequest,
    ctx: AuthContext = Depends(require_context("sales.cancel")),
    db: AsyncSession = Depends(get_db),
):
    invoice = await sales.void_invoice(db, ctx.user, ctx.permissions, invoice_id, body)
    return await sales.invoice_response(db, invoice)


@router.get("/{invoice_id}/prescription", response_model=PrescriptionResponse)
async def get_prescription(
    invoice_id: int,
    ctx: AuthContext = Depends(require_context("prescriptions.view")),
    db: AsyncSession = Depends(get_db),
):
    invoice = await sales.get_invoice(db, ctx.pharmacy_id, invoice_id)
    rx = invoice.prescription
    if rx is None:
        raise HTTPException(status_code=404, detail="No prescription recorded for this invoice")
    verifier = (await db.execute(select(User.name).where(User.id == rx.verified_by))).scalar_one_or_none()
    db.add(AuditLog(pharmacy_id=ctx.pharmacy_id, user_id=ctx.user.id, action="prescription.view",
                    entity_type="sales_invoice", entity_id=str(invoice.id)))
    return PrescriptionResponse(
        prescription_reference=rx.prescription_reference, prescription_date=rx.prescription_date,
        prescriber_name=rx.prescriber_name, prescriber_registration_no=rx.prescriber_registration_no,
        prescriber_address=rx.prescriber_address, patient_name=rx.patient_name,
        patient_address=rx.patient_address, verified_by_name=verifier, created_at=rx.created_at,
    )


@router.patch("/{invoice_id}/reminders", response_model=InvoiceResponse)
async def control_reminders(
    invoice_id: int,
    body: ReminderControlRequest,
    ctx: AuthContext = Depends(require_context("payments.receive")),
    db: AsyncSession = Depends(get_db),
):
    """Pause / resume reminders or mark a dispute (reminders stop while disputed)."""
    invoice = await sales.get_invoice(db, ctx.pharmacy_id, invoice_id, lock=True)
    changes = body.model_dump(exclude_unset=True)
    if "reminders_paused" in changes and changes["reminders_paused"] is not None:
        invoice.reminders_paused = changes["reminders_paused"]
    if "is_disputed" in changes and changes["is_disputed"] is not None:
        if changes["is_disputed"] and not (body.dispute_note or "").strip():
            raise HTTPException(status_code=422, detail="Describe the dispute")
        invoice.is_disputed = changes["is_disputed"]
        invoice.dispute_note = (body.dispute_note or "").strip() or None if invoice.is_disputed else None
    db.add(AuditLog(pharmacy_id=ctx.pharmacy_id, user_id=ctx.user.id, action="sale.reminder_control",
                    entity_type="sales_invoice", entity_id=str(invoice.id),
                    metadata_json=json.dumps({k: v for k, v in changes.items() if k != "dispute_note"})))
    await db.flush()
    return await sales.invoice_response(db, invoice)


async def _message_response(db: AsyncSession, msg: MessageOutbox) -> MessageResponse:
    customer_name = invoice_number = None
    if msg.customer_id:
        customer_name = (await db.execute(select(Customer.name).where(Customer.id == msg.customer_id))).scalar_one_or_none()
    if msg.invoice_id:
        invoice_number = (await db.execute(
            select(SalesInvoice.invoice_number).where(SalesInvoice.id == msg.invoice_id))).scalar_one_or_none()
    return MessageResponse(**messaging.message_view(msg, customer_name, invoice_number))


@router.post("/{invoice_id}/whatsapp", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
async def send_invoice_whatsapp(
    invoice_id: int,
    body: SendInvoiceMessageRequest,
    ctx: AuthContext = Depends(require_context("messaging.send")),
    db: AsyncSession = Depends(get_db),
    session_factory=Depends(get_session_factory),
):
    """
    MANUAL mode returns a wa.me link for staff to open and send (status: opened, not delivered).
    CLOUD_API mode queues the message, commits, then sends it; the response shows the real status.
    """
    invoice = await sales.get_invoice(db, ctx.pharmacy_id, invoice_id)
    msg = await messaging.create_invoice_message(
        db, ctx.user, ctx.permissions, invoice, body.to_number, body.mode, body.consent_confirmed, body.force_resend)
    if msg.mode == "CLOUD_API":
        await db.commit()                      # The message row is durable before WhatsApp is called
        await messaging.dispatch_message(session_factory, msg.id)
        await db.refresh(msg)
    return await _message_response(db, msg)


@router.post("/{invoice_id}/reminder", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
async def send_reminder(
    invoice_id: int,
    body: ReminderRequest,
    ctx: AuthContext = Depends(require_context("messaging.send")),
    db: AsyncSession = Depends(get_db),
    session_factory=Depends(get_session_factory),
):
    invoice = await sales.get_invoice(db, ctx.pharmacy_id, invoice_id)
    msg = await messaging.create_manual_reminder(db, ctx.user, ctx.permissions, invoice, body.mode)
    if msg.mode == "CLOUD_API":
        await db.commit()
        await messaging.dispatch_message(session_factory, msg.id)
        await db.refresh(msg)
    return await _message_response(db, msg)


@router.get("/{invoice_id}/messages", response_model=list[MessageResponse])
async def invoice_messages(
    invoice_id: int,
    ctx: AuthContext = Depends(require_context("sales.view")),
    db: AsyncSession = Depends(get_db),
):
    await sales.get_invoice(db, ctx.pharmacy_id, invoice_id)
    rows = (await db.execute(
        select(MessageOutbox).where(MessageOutbox.invoice_id == invoice_id).order_by(MessageOutbox.created_at.desc())
    )).scalars().all()
    return [await _message_response(db, m) for m in rows]
