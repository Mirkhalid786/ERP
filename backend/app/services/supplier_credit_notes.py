"""
Supplier credit notes (no goods returned): reduce the supplier balance and, when linked,
the outstanding amount of one finalised bill. Posted to the GL as
Dr Trade Payables / Cr Purchase Price Variance (taxable) / Cr Input GST pending (tax).
Cancelling keeps the note, frees its number and reverses the GL entry.
"""
import json
from typing import List

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import today_local
from app.models.auth import AuditLog, User
from app.models.payables import CREDIT_NOTE_REASONS, SupplierCreditNote
from app.models.purchasing import SupplierInvoice
from app.services import gl_posting
from app.services.purchasing import invoice_number_key
from app.services.sales_common import ZERO, money, require, utcnow
from app.services.supplier_invoices import outstanding, refresh_payment_cache
from app.services.supplier_payments import lock_supplier


def _audit(db, user: User, action: str, note: SupplierCreditNote, details: dict, reason: str = None):
    db.add(AuditLog(pharmacy_id=user.pharmacy_id, user_id=user.id, action=action, entity_type="supplier_credit_note",
                    entity_id=str(note.id), reason=reason, metadata_json=json.dumps(details, default=str)))


async def create(db: AsyncSession, user: User, perms: set, body) -> tuple:
    require(perms, "supplier_payments.create", "record supplier credit notes")
    pid = user.pharmacy_id
    found = (await db.execute(select(SupplierCreditNote).where(
        SupplierCreditNote.pharmacy_id == pid, SupplierCreditNote.idempotency_key == body.idempotency_key))).scalar_one_or_none()
    if found:
        return found, True
    supplier = await lock_supplier(db, pid, body.supplier_id)
    if body.reason_code not in CREDIT_NOTE_REASONS:
        raise HTTPException(status_code=422, detail=f"Reason must be one of {', '.join(CREDIT_NOTE_REASONS)}")
    if body.note_date > today_local():
        raise HTTPException(status_code=422, detail="Credit note date cannot be in the future")
    taxable, cgst, sgst, igst = (money(body.taxable_amount), money(body.cgst_amount or 0), money(body.sgst_amount or 0),
                                 money(body.igst_amount or 0))
    if min(taxable, cgst, sgst, igst) < ZERO or (igst > ZERO and (cgst > ZERO or sgst > ZERO)) or cgst != sgst:
        raise HTTPException(status_code=422, detail="Enter CGST = SGST (intra-state) or IGST only (inter-state); no negatives")
    total = money(taxable + cgst + sgst + igst)
    if total <= ZERO:
        raise HTTPException(status_code=422, detail="Credit note total must be more than 0")
    number = body.credit_note_number.strip().upper()
    key = invoice_number_key(number, body.note_date)
    clash = (await db.execute(select(SupplierCreditNote.credit_note_number).where(
        SupplierCreditNote.pharmacy_id == pid, SupplierCreditNote.supplier_id == supplier.id,
        SupplierCreditNote.number_key == key))).scalar_one_or_none()
    if clash:
        raise HTTPException(status_code=409, detail=f"Credit note {clash} from {supplier.name} is already recorded this financial year")
    invoice = None
    if body.invoice_id:
        invoice = (await db.execute(select(SupplierInvoice).where(
            SupplierInvoice.id == body.invoice_id, SupplierInvoice.pharmacy_id == pid).with_for_update())).scalar_one_or_none()
        if invoice is None or invoice.supplier_id != supplier.id or invoice.status != "POSTED":
            raise HTTPException(status_code=422, detail="Link the credit note to a finalised bill of the same supplier")
        due = (await outstanding(db, invoice))["outstanding"]
        if total > due:
            raise HTTPException(status_code=422, detail=f"Credit note ₹{total} is more than the ₹{due} outstanding on {invoice.invoice_number}")
    note = SupplierCreditNote(pharmacy_id=pid, supplier_id=supplier.id, credit_note_number=number, number_key=key,
                              idempotency_key=body.idempotency_key, note_date=body.note_date, invoice_id=invoice.id if invoice else None,
                              reason_code=body.reason_code, taxable_amount=taxable, cgst_amount=cgst, sgst_amount=sgst,
                              igst_amount=igst, total_amount=total, notes=(body.notes or "").strip() or None, created_by=user.id)
    db.add(note)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail="This credit note was already recorded")
    if invoice is not None:
        await refresh_payment_cache(db, invoice)
    _audit(db, user, "supplier_credit_note.create", note, {"number": number, "supplier": supplier.id, "total": total,
                                                            "invoice_id": note.invoice_id, "reason": body.reason_code})
    await db.flush()
    await gl_posting.record(db, pid, user.id, "SUPPLIER_CREDIT_NOTE", note.id)
    return note, False


async def cancel(db: AsyncSession, user: User, perms: set, note_id: int, reason: str) -> SupplierCreditNote:
    require(perms, "purchases.invoice_cancel", "cancel supplier credit notes")
    note = (await db.execute(select(SupplierCreditNote).where(
        SupplierCreditNote.id == note_id, SupplierCreditNote.pharmacy_id == user.pharmacy_id))).scalar_one_or_none()
    if note is None:
        raise HTTPException(status_code=404, detail="Credit note not found")
    await lock_supplier(db, user.pharmacy_id, note.supplier_id)
    if note.status != "POSTED":
        raise HTTPException(status_code=409, detail="Credit note is already cancelled")
    note.status, note.number_key = "CANCELLED", None
    note.cancelled_by, note.cancelled_at, note.cancel_reason = user.id, utcnow(), reason.strip()
    if note.invoice_id:
        invoice = (await db.execute(select(SupplierInvoice).where(SupplierInvoice.id == note.invoice_id))).scalar_one()
        await refresh_payment_cache(db, invoice)
    _audit(db, user, "supplier_credit_note.cancel", note, {"number": note.credit_note_number, "total": note.total_amount}, reason=reason)
    await db.flush()
    await gl_posting.record(db, user.pharmacy_id, user.id, "SUPPLIER_CREDIT_NOTE_CANCEL", note.id)
    return note


async def list_notes(db: AsyncSession, pharmacy_id: int, supplier_id=None) -> List[SupplierCreditNote]:
    stmt = select(SupplierCreditNote).where(SupplierCreditNote.pharmacy_id == pharmacy_id)
    if supplier_id:
        stmt = stmt.where(SupplierCreditNote.supplier_id == supplier_id)
    return list((await db.execute(stmt.order_by(SupplierCreditNote.note_date.desc(), SupplierCreditNote.id.desc()).limit(200))).scalars().all())
