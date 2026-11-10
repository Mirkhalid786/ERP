"""
Supplier Purchase Returns (Debit Notes) API routes.
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_permission
from app.models.auth import User
from app.schemas.purchasing import (
    SupplierReturnCreate, SupplierReturnResponse,
)
from app.services.purchasing import SupplierReturnService

router = APIRouter(prefix="/purchases/returns", tags=["Supplier Returns"])


@router.get("", response_model=List[SupplierReturnResponse])
async def list_supplier_returns(
    supplier_id: Optional[int] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_permission("purchases.view")),
    db: AsyncSession = Depends(get_db),
):
    """List supplier purchase return vouchers."""
    items, _ = await SupplierReturnService.list_returns(
        db,
        pharmacy_id=current_user.pharmacy_id,
        supplier_id=supplier_id,
        limit=limit,
        offset=offset,
    )
    return [_serialize_return(ret) for ret in items]


@router.get("/{return_id}", response_model=SupplierReturnResponse)
async def get_supplier_return(
    return_id: int,
    current_user: User = Depends(require_permission("purchases.view")),
    db: AsyncSession = Depends(get_db),
):
    """Get single supplier return voucher details."""
    ret = await SupplierReturnService.get_return(db, current_user.pharmacy_id, return_id)
    return _serialize_return(ret)


@router.post("", response_model=SupplierReturnResponse, status_code=status.HTTP_201_CREATED)
async def create_supplier_return(
    body: SupplierReturnCreate,
    current_user: User = Depends(require_permission("purchases.return")),
    db: AsyncSession = Depends(get_db),
):
    """
    Create draft supplier return voucher:
      - Validates returnable quantity against GRN accepted quantity minus prior returns
      - Validates batch stock balance
    """
    ret = await SupplierReturnService.create_supplier_return(
        db,
        pharmacy_id=current_user.pharmacy_id,
        payload=body,
        user_id=current_user.id,
    )
    return _serialize_return(ret)


@router.post("/{return_id}/post", response_model=SupplierReturnResponse)
async def post_supplier_return(
    return_id: int,
    current_user: User = Depends(require_permission("purchases.return")),
    db: AsyncSession = Depends(get_db),
):
    """
    Atomically post supplier return:
      - Reduces saleable stock balance in MedicineBatch
      - Inserts immutable RETURN_OUTWARD stock movement ledger entry
      - Sets status to POSTED with generated debit note reference
    """
    ret = await SupplierReturnService.post_supplier_return(
        db,
        pharmacy_id=current_user.pharmacy_id,
        return_id=return_id,
        user_id=current_user.id,
    )
    return _serialize_return(ret)


def _serialize_return(ret) -> dict:
    return {
        "id": ret.id,
        "pharmacy_id": ret.pharmacy_id,
        "return_number": ret.return_number,
        "supplier_id": ret.supplier_id,
        "supplier_name": ret.supplier.name if ret.supplier else None,
        "grn_id": ret.grn_id,
        "grn_number": ret.goods_receipt.grn_number if ret.goods_receipt else None,
        "invoice_id": ret.invoice_id,
        "invoice_number": ret.invoice.invoice_number if ret.invoice else None,
        "return_date": ret.return_date,
        "status": ret.status,
        "reason_code": ret.reason_code,
        "subtotal": ret.subtotal,
        "tax_total": ret.tax_total,
        "total_return_amount": ret.total_return_amount,
        "debit_note_reference": ret.debit_note_reference,
        "notes": ret.notes,
        "created_by": ret.created_by,
        "posted_at": ret.posted_at,
        "created_at": ret.created_at,
        "updated_at": ret.updated_at,
        "items": [
            {
                "id": it.id,
                "return_id": it.return_id,
                "grn_item_id": it.grn_item_id,
                "invoice_item_id": it.invoice_item_id,
                "medicine_id": it.medicine_id,
                "medicine_name": it.medicine.name if it.medicine else None,
                "batch_id": it.batch_id,
                "batch_number": it.batch.batch_number if it.batch else None,
                "packaging_unit_id": it.packaging_unit_id,
                "packaging_unit_name": it.packaging_unit.name if it.packaging_unit else None,
                "conversion_factor": it.conversion_factor,
                "return_qty_pack": it.return_qty_pack,
                "return_qty_base": it.return_qty_base,
                "unit_cost_base": it.unit_cost_base,
                "refund_rate": it.refund_rate,
                "tax_rate_id": it.tax_rate_id,
                "tax_amount": it.tax_amount,
                "total_amount": it.total_amount,
                "reason": it.reason,
            }
            for it in (ret.items or [])
        ],
    }


# ── Supplier Credit Notes (Financial) ──────────────────────────────────────────

from app.models.payables import SupplierCreditNote
from app.schemas.purchasing import SupplierCreditNoteCreate, SupplierCreditNoteResponse
from app.services.purchasing import invoice_number_key
from app.services import gl_posting
from sqlalchemy import select, func


@router.get("/credit-notes", response_model=List[SupplierCreditNoteResponse])
async def list_supplier_credit_notes(
    supplier_id: Optional[int] = Query(None),
    invoice_id: Optional[int] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_permission("purchases.view")),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(SupplierCreditNote).where(SupplierCreditNote.pharmacy_id == current_user.pharmacy_id)
    if supplier_id:
        stmt = stmt.where(SupplierCreditNote.supplier_id == supplier_id)
    if invoice_id:
        stmt = stmt.where(SupplierCreditNote.invoice_id == invoice_id)
    stmt = stmt.order_by(SupplierCreditNote.created_at.desc()).offset(offset).limit(limit)
    items = (await db.execute(stmt)).scalars().all()
    return [_serialize_credit_note(cn) for cn in items]


@router.post("/credit-notes", response_model=SupplierCreditNoteResponse, status_code=status.HTTP_201_CREATED)
async def create_supplier_credit_note(
    body: SupplierCreditNoteCreate,
    current_user: User = Depends(require_permission("purchases.return")),
    db: AsyncSession = Depends(get_db),
):
    clean_num = body.credit_note_number.strip().upper()
    num_key = invoice_number_key(clean_num, body.note_date)
    existing = (await db.execute(select(SupplierCreditNote).where(
        SupplierCreditNote.pharmacy_id == current_user.pharmacy_id,
        SupplierCreditNote.supplier_id == body.supplier_id,
        SupplierCreditNote.number_key == num_key,
    ))).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Credit note '{clean_num}' has already been recorded for this supplier",
        )

    note = SupplierCreditNote(
        pharmacy_id=current_user.pharmacy_id,
        supplier_id=body.supplier_id,
        credit_note_number=clean_num,
        number_key=num_key,
        note_date=body.note_date,
        invoice_id=body.invoice_id,
        reason_code=body.reason_code,
        taxable_amount=body.taxable_amount,
        cgst_amount=body.cgst_amount,
        sgst_amount=body.sgst_amount,
        igst_amount=body.igst_amount,
        total_amount=body.total_amount,
        notes=body.notes,
        status="POSTED",
        created_by=current_user.id,
    )
    db.add(note)
    await db.flush()

    await gl_posting.record(db, current_user.pharmacy_id, current_user.id, "SUPPLIER_CREDIT_NOTE", note.id)
    return _serialize_credit_note(note)


@router.post("/credit-notes/{note_id}/cancel", response_model=SupplierCreditNoteResponse)
async def cancel_supplier_credit_note(
    note_id: int,
    body: dict,
    current_user: User = Depends(require_permission("purchases.return")),
    db: AsyncSession = Depends(get_db),
):
    note = (await db.execute(select(SupplierCreditNote).where(
        SupplierCreditNote.id == note_id,
        SupplierCreditNote.pharmacy_id == current_user.pharmacy_id,
    ))).scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=404, detail="Credit note not found")
    if note.status == "CANCELLED":
        raise HTTPException(status_code=409, detail="Credit note is already cancelled")

    reason = body.get("reason", "Cancelled by user")
    note.status = "CANCELLED"
    note.number_key = None
    note.cancelled_by = current_user.id
    note.cancelled_at = utcnow()
    note.cancel_reason = reason
    await db.flush()

    await gl_posting.record(db, current_user.pharmacy_id, current_user.id, "SUPPLIER_CREDIT_NOTE_CANCEL", note.id)
    return _serialize_credit_note(note)


def _serialize_credit_note(cn: SupplierCreditNote) -> dict:
    return {
        "id": cn.id,
        "pharmacy_id": cn.pharmacy_id,
        "supplier_id": cn.supplier_id,
        "supplier_name": cn.supplier.name if getattr(cn, "supplier", None) else None,
        "credit_note_number": cn.credit_note_number,
        "note_date": cn.note_date,
        "invoice_id": cn.invoice_id,
        "invoice_number": cn.invoice.invoice_number if getattr(cn, "invoice", None) else None,
        "reason_code": cn.reason_code,
        "taxable_amount": cn.taxable_amount,
        "cgst_amount": cn.cgst_amount,
        "sgst_amount": cn.sgst_amount,
        "igst_amount": cn.igst_amount,
        "total_amount": cn.total_amount,
        "notes": cn.notes,
        "status": cn.status,
        "created_by": cn.created_by,
        "created_at": cn.created_at,
        "cancelled_by": cn.cancelled_by,
        "cancelled_at": cn.cancelled_at,
        "cancel_reason": cn.cancel_reason,
    }
