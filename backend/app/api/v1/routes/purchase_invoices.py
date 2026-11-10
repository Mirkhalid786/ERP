"""
Supplier Purchase Invoices API routes.

Workflow: DRAFT (editable) -> POSTED (finalised; amount owed to the supplier) -> CANCELLED.
Payments are recorded as supplier payments (/supplier-payments); the old endpoint that
overwrote `paid_amount` is retired.
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import AuthContext, require_context
from app.schemas.payables import InvoiceFinalizeRequest, ReasonRequest
from app.schemas.purchasing import SupplierInvoiceCreate, SupplierInvoiceResponse
from app.services import supplier_invoices
from app.services.purchasing import SupplierInvoiceService
from app.services.sales import _user_names

router = APIRouter(prefix="/purchases/invoices", tags=["Supplier Invoices"])


@router.get("", response_model=List[SupplierInvoiceResponse])
async def list_supplier_invoices(
    supplier_id: Optional[int] = Query(None),
    payment_status: Optional[str] = Query(None, description="UNPAID, PARTIALLY_PAID, PAID"),
    status_filter: Optional[str] = Query(None, alias="status", description="DRAFT, POSTED, CANCELLED"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: AuthContext = Depends(require_context("purchases.view")),
    db: AsyncSession = Depends(get_db),
):
    """List supplier purchase invoices."""
    items, _ = await SupplierInvoiceService.list_invoices(
        db,
        pharmacy_id=ctx.pharmacy_id,
        supplier_id=supplier_id,
        payment_status=payment_status,
        limit=limit,
        offset=offset,
        status_filter=status_filter,
    )
    return [await _serialize_invoice(db, inv) for inv in items]


@router.get("/{invoice_id}", response_model=SupplierInvoiceResponse)
async def get_supplier_invoice(
    invoice_id: int,
    ctx: AuthContext = Depends(require_context("purchases.view")),
    db: AsyncSession = Depends(get_db),
):
    """Get single supplier invoice details with items."""
    invoice = await SupplierInvoiceService.get_invoice(db, ctx.pharmacy_id, invoice_id)
    return await _serialize_invoice(db, invoice)


@router.post("", response_model=SupplierInvoiceResponse, status_code=status.HTTP_201_CREATED)
async def create_supplier_invoice(
    body: SupplierInvoiceCreate,
    ctx: AuthContext = Depends(require_context("purchases.create")),
    db: AsyncSession = Depends(get_db),
):
    """
    Record a supplier bill as a DRAFT:
      - Refuses a number already used by this supplier in the same financial year
      - Computes line taxes & grand totals
      - Calculates discrepancy against supplier declared bill total
    """
    invoice = await SupplierInvoiceService.create_invoice(
        db,
        pharmacy_id=ctx.pharmacy_id,
        payload=body,
        user_id=ctx.user.id,
    )
    return await _serialize_invoice(db, invoice)


@router.put("/{invoice_id}", response_model=SupplierInvoiceResponse)
async def update_supplier_invoice(
    invoice_id: int,
    body: SupplierInvoiceCreate,
    ctx: AuthContext = Depends(require_context("purchases.create")),
    db: AsyncSession = Depends(get_db),
):
    """Replace a DRAFT invoice's header and lines (audited with before/after values)."""
    invoice = await supplier_invoices.update_draft(db, ctx.user, invoice_id, body)
    return await _serialize_invoice(db, invoice)


@router.get("/{invoice_id}/finalize-check")
async def finalize_check(
    invoice_id: int,
    ctx: AuthContext = Depends(require_context("purchases.view")),
    db: AsyncSession = Depends(get_db),
):
    """Errors that block finalising and warnings that must be acknowledged."""
    invoice = await SupplierInvoiceService.get_invoice(db, ctx.pharmacy_id, invoice_id)
    return await supplier_invoices.finalize_check(db, invoice)


@router.post("/{invoice_id}/finalize", response_model=SupplierInvoiceResponse)
async def finalize_supplier_invoice(
    invoice_id: int,
    body: InvoiceFinalizeRequest = InvoiceFinalizeRequest(),
    ctx: AuthContext = Depends(require_context("purchases.invoice_finalize")),
    db: AsyncSession = Depends(get_db),
):
    invoice = await supplier_invoices.finalize(db, ctx.user, ctx.permissions, invoice_id, body.acknowledge_warnings)
    return await _serialize_invoice(db, invoice)


@router.post("/{invoice_id}/cancel", response_model=SupplierInvoiceResponse)
async def cancel_supplier_invoice(
    invoice_id: int,
    body: ReasonRequest,
    ctx: AuthContext = Depends(require_context("purchases.view")),
    db: AsyncSession = Depends(get_db),
):
    """Cancel a draft (purchases.create) or an unpaid finalised bill (purchases.invoice_cancel). Kept for audit."""
    invoice = await supplier_invoices.cancel(db, ctx.user, ctx.permissions, invoice_id, body.reason)
    return await _serialize_invoice(db, invoice)


@router.patch("/{invoice_id}/payment", include_in_schema=False)
async def retired_payment_update(invoice_id: int, ctx: AuthContext = Depends(require_context("purchases.view"))):
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Overwriting the paid amount is no longer supported; record a supplier payment instead "
               "(POST /api/v1/supplier-payments)",
    )


async def _serialize_invoice(db: AsyncSession, inv) -> dict:
    figures = await supplier_invoices.outstanding(db, inv)
    names = await _user_names(db, [inv.created_by, inv.finalized_by, inv.cancelled_by])
    return {
        "id": inv.id,
        "pharmacy_id": inv.pharmacy_id,
        "invoice_number": inv.invoice_number,
        "supplier_id": inv.supplier_id,
        "supplier_name": inv.supplier.name if inv.supplier else None,
        "grn_id": inv.grn_id,
        "grn_number": inv.goods_receipt.grn_number if inv.goods_receipt else None,
        "po_id": inv.po_id,
        "invoice_date": inv.invoice_date,
        "received_date": inv.received_date,
        "due_date": inv.due_date,
        "payment_terms_days": inv.payment_terms_days,
        "is_tax_inclusive": inv.is_tax_inclusive,
        "currency": inv.currency,
        "subtotal": inv.subtotal,
        "discount_total": inv.discount_total,
        "cgst_total": inv.cgst_total,
        "sgst_total": inv.sgst_total,
        "igst_total": inv.igst_total,
        "tax_total": inv.tax_total,
        "other_charges": inv.other_charges,
        "round_off": inv.round_off,
        "calculated_grand_total": inv.calculated_grand_total,
        "supplier_declared_total": inv.supplier_declared_total,
        "discrepancy_amount": inv.discrepancy_amount,
        "discrepancy_notes": inv.discrepancy_notes,
        "discrepancy_accepted": inv.discrepancy_accepted,
        "status": inv.status,
        "payment_status": inv.payment_status,
        "paid_amount": inv.paid_amount,
        "returned_amount": figures["returned"],
        "outstanding_amount": figures["outstanding"] if inv.status == "POSTED" else 0,
        "notes": inv.notes,
        "finalized_by": inv.finalized_by,
        "finalized_by_name": names.get(inv.finalized_by),
        "finalized_at": inv.finalized_at,
        "cancelled_by_name": names.get(inv.cancelled_by),
        "cancelled_at": inv.cancelled_at,
        "cancel_reason": inv.cancel_reason,
        "replaces_invoice_id": inv.replaces_invoice_id,
        "created_by": inv.created_by,
        "created_by_name": names.get(inv.created_by),
        "created_at": inv.created_at,
        "updated_at": inv.updated_at,
        "items": [
            {
                "id": it.id,
                "invoice_id": it.invoice_id,
                "grn_item_id": it.grn_item_id,
                "medicine_id": it.medicine_id,
                "medicine_name": it.medicine.name if it.medicine else None,
                "batch_id": it.batch_id,
                "batch_number": it.batch_number,
                "packaging_unit_id": it.packaging_unit_id,
                "packaging_unit_name": it.packaging_unit.name if it.packaging_unit else None,
                "conversion_factor": it.conversion_factor,
                "billed_qty_pack": it.billed_qty_pack,
                "billed_qty_base": it.billed_qty_base,
                "free_qty_pack": it.free_qty_pack,
                "purchase_rate": it.purchase_rate,
                "discount_percent": it.discount_percent,
                "discount_amount": it.discount_amount,
                "taxable_amount": it.taxable_amount,
                "tax_rate_id": it.tax_rate_id,
                "cgst_amount": it.cgst_amount,
                "sgst_amount": it.sgst_amount,
                "igst_amount": it.igst_amount,
                "tax_amount": it.tax_amount,
                "line_total": it.line_total,
            }
            for it in (inv.items or [])
        ],
    }
