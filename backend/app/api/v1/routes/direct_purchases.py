"""
Direct Supplier Invoice Purchase API routes.

Provides unified direct purchase entry:
  - Enter supplier invoice & medicines in ONE step
  - "Save Draft" or "Save & Post Purchase"
  - Controlled cancellation with stock consumption guard
  - Deletion of draft purchases
  - Optional advance PO conversion to direct purchase
"""
from decimal import Decimal
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import AuthContext, require_context
from app.schemas.purchasing import (
    DirectPurchaseCreatePayload,
    DirectPurchaseCancelPayload,
    SupplierInvoiceResponse,
)
from app.services.direct_purchasing import DirectPurchaseService
from app.services.purchasing import PurchaseOrderService
from app.api.v1.routes.purchase_invoices import _serialize_invoice

router = APIRouter(prefix="/purchases/direct", tags=["Direct Purchasing"])


@router.post("", response_model=SupplierInvoiceResponse, status_code=status.HTTP_201_CREATED)
async def create_or_post_purchase(
    body: DirectPurchaseCreatePayload,
    ctx: AuthContext = Depends(require_context("purchases.create")),
    db: AsyncSession = Depends(get_db),
):
    """
    Direct Purchase Entry:
      - If post_now=True: atomically records invoice, internal GRN, batches, stock, payable & GL.
      - If post_now=False: saves as DRAFT without stock or accounting impact.
    """
    if body.post_now and "purchases.invoice_finalize" not in ctx.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to post purchases (purchases.invoice_finalize required)",
        )

    invoice = await DirectPurchaseService.create_or_post_purchase(
        db,
        pharmacy_id=ctx.pharmacy_id,
        payload=body,
        user=ctx.user,
        perms=ctx.permissions,
    )
    return await _serialize_invoice(db, invoice)


@router.post("/{invoice_id}/post", response_model=SupplierInvoiceResponse)
async def post_draft_purchase(
    invoice_id: int,
    ctx: AuthContext = Depends(require_context("purchases.invoice_finalize")),
    db: AsyncSession = Depends(get_db),
):
    """Post an existing draft direct purchase into inventory and supplier payables."""
    invoice = await DirectPurchaseService.post_draft_purchase(
        db,
        pharmacy_id=ctx.pharmacy_id,
        invoice_id=invoice_id,
        user=ctx.user,
        perms=ctx.permissions,
    )
    return await _serialize_invoice(db, invoice)


@router.delete("/{invoice_id}")
async def delete_draft_purchase(
    invoice_id: int,
    ctx: AuthContext = Depends(require_context("purchases.create")),
    db: AsyncSession = Depends(get_db),
):
    """Delete a draft purchase and its draft goods receipt. Blocked if already posted."""
    return await DirectPurchaseService.delete_draft_purchase(
        db,
        pharmacy_id=ctx.pharmacy_id,
        invoice_id=invoice_id,
        user=ctx.user,
    )


@router.post("/{invoice_id}/cancel", response_model=SupplierInvoiceResponse)
async def cancel_posted_purchase(
    invoice_id: int,
    body: DirectPurchaseCancelPayload,
    ctx: AuthContext = Depends(require_context("purchases.invoice_cancel")),
    db: AsyncSession = Depends(get_db),
):
    """
    Controlled reversal of a posted purchase:
      - BLOCKED if any stock has been sold or consumed!
      - Reverses stock, creates PURCHASE_CANCEL movement, cancels bill & GRN, reverses GL entries.
    """
    invoice = await DirectPurchaseService.cancel_posted_purchase(
        db,
        pharmacy_id=ctx.pharmacy_id,
        invoice_id=invoice_id,
        payload=body,
        user=ctx.user,
        perms=ctx.permissions,
    )
    return await _serialize_invoice(db, invoice)


@router.get("/from-po/{po_id}")
async def get_direct_purchase_prefill_from_po(
    po_id: int,
    ctx: AuthContext = Depends(require_context("purchases.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Pre-fill direct purchase form data from an advance Purchase Order.
    Carries forward supplier, medicines, packaging units, quantities, and rates.
    """
    po = await PurchaseOrderService.get_purchase_order(db, ctx.pharmacy_id, po_id)
    items = []
    for it in (po.items or []):
        rem_pack = it.ordered_qty_pack
        items.append({
            "medicine_id": it.medicine_id,
            "medicine_name": it.medicine.name if it.medicine else "",
            "brand_name": it.medicine.brand_name if it.medicine else "",
            "packaging_unit_id": it.packaging_unit_id,
            "packaging_unit_name": it.packaging_unit.name if getattr(it, "packaging_unit", None) else "",
            "quantity": float(rem_pack),
            "purchase_qty_pack": float(rem_pack),
            "free_quantity": 0,
            "purchase_rate": float(it.purchase_rate),
            "discount_percent": float(it.discount_percent or 0),
            "tax_rate_id": it.tax_rate_id,
            "batch_number": "",
            "manufacturing_date": None,
            "expiry_date": None,
            "mrp": float(Decimal(str(it.purchase_rate)) * Decimal("1.30")),
            "selling_rate": float(Decimal(str(it.purchase_rate)) * Decimal("1.25")),
            "rack_location": "RACK-A1",
        })

    return {
        "supplier_id": po.supplier_id,
        "po_id": po.id,
        "po_number": po.po_number,
        "order_date": str(po.order_date),
        "notes": po.notes or "",
        "items": items,
    }
