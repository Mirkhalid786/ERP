"""
Purchase Orders API routes.
"""
from decimal import Decimal
from typing import List, Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_permission
from app.models.auth import User
from app.schemas.purchasing import (
    PurchaseOrderCreate, PurchaseOrderResponse, PurchaseOrderStatusUpdate,
)
from app.services.purchasing import PurchaseOrderService

router = APIRouter(prefix="/purchases/orders", tags=["Purchase Orders"])


@router.get("", response_model=List[PurchaseOrderResponse])
async def list_purchase_orders(
    supplier_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None, description="DRAFT, SUBMITTED, APPROVED, PARTIALLY_RECEIVED, RECEIVED, CANCELLED"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_permission("purchases.view")),
    db: AsyncSession = Depends(get_db),
):
    """List purchase orders with supplier and status filters."""
    items, _ = await PurchaseOrderService.list_purchase_orders(
        db,
        pharmacy_id=current_user.pharmacy_id,
        supplier_id=supplier_id,
        status_filter=status,
        limit=limit,
        offset=offset,
    )
    # Serialize with helper field population
    res = []
    for po in items:
        resp_dict = _serialize_po(po)
        res.append(resp_dict)
    return res


@router.get("/{po_id}", response_model=PurchaseOrderResponse)
async def get_purchase_order(
    po_id: int,
    current_user: User = Depends(require_permission("purchases.view")),
    db: AsyncSession = Depends(get_db),
):
    """Get single purchase order details."""
    po = await PurchaseOrderService.get_purchase_order(db, current_user.pharmacy_id, po_id)
    return _serialize_po(po)


@router.post("", response_model=PurchaseOrderResponse, status_code=status.HTTP_201_CREATED)
async def create_purchase_order(
    body: PurchaseOrderCreate,
    current_user: User = Depends(require_permission("purchases.create")),
    db: AsyncSession = Depends(get_db),
):
    """Create draft purchase order (does NOT mutate inventory)."""
    po = await PurchaseOrderService.create_purchase_order(
        db,
        pharmacy_id=current_user.pharmacy_id,
        payload=body,
        user_id=current_user.id,
    )
    return _serialize_po(po)


@router.put("/{po_id}", response_model=PurchaseOrderResponse)
async def update_purchase_order(
    po_id: int,
    body: PurchaseOrderCreate,
    current_user: User = Depends(require_permission("purchases.create")),
    db: AsyncSession = Depends(get_db),
):
    """Update draft purchase order."""
    po = await PurchaseOrderService.update_purchase_order(
        db,
        pharmacy_id=current_user.pharmacy_id,
        po_id=po_id,
        payload=body,
        user_id=current_user.id,
    )
    return _serialize_po(po)


@router.patch("/{po_id}/status", response_model=PurchaseOrderResponse)
async def update_purchase_order_status(
    po_id: int,
    body: PurchaseOrderStatusUpdate,
    current_user: User = Depends(require_permission("purchases.post")),
    db: AsyncSession = Depends(get_db),
):
    """Update purchase order status (SUBMITTED, APPROVED, DRAFT, CANCELLED)."""
    po = await PurchaseOrderService.update_po_status(
        db,
        pharmacy_id=current_user.pharmacy_id,
        po_id=po_id,
        new_status=body.status,
        user_id=current_user.id,
        reason=body.reason,
    )
    return _serialize_po(po)


@router.get("/{po_id}/lifecycle")
async def get_purchase_order_lifecycle(
    po_id: int,
    current_user: User = Depends(require_permission("purchases.view")),
    db: AsyncSession = Depends(get_db),
):
    """Get full unified procurement lifecycle for a purchase order (PO, GRNs, Invoices, Timeline)."""
    data = await PurchaseOrderService.get_purchase_order_lifecycle(
        db,
        pharmacy_id=current_user.pharmacy_id,
        po_id=po_id,
    )
    from app.api.v1.routes.goods_receipts import _serialize_grn
    return {
        "po": _serialize_po(data["po"]),
        "goods_receipts": [_serialize_grn(grn) for grn in data["goods_receipts"]],
        "invoices": [
            {
                "id": inv.id,
                "invoice_number": inv.invoice_number,
                "invoice_date": inv.invoice_date,
                "due_date": inv.due_date,
                "status": inv.status,
                "payment_status": inv.payment_status,
                "calculated_grand_total": inv.calculated_grand_total,
                "supplier_declared_total": inv.supplier_declared_total,
                "discrepancy_amount": inv.discrepancy_amount,
                "paid_amount": inv.paid_amount,
                "grn_id": inv.grn_id,
                "po_id": inv.po_id,
                "items_count": len(inv.items) if "items" in inv.__dict__ and inv.items is not None else 0,
            }
            for inv in data["invoices"]
        ],
        "lifecycle_stage": data["lifecycle_stage"],
        "total_ordered_base": data["total_ordered_base"],
        "total_received_base": data["total_received_base"],
        "fulfillment_percent": data["fulfillment_percent"],
        "posted_grns_count": data["posted_grns_count"],
        "posted_invs_count": data["posted_invs_count"],
    }


def _serialize_po(po) -> dict:
    grns = po.goods_receipts or []
    posted_grns = [g for g in grns if g.status == "POSTED"]

    # Invoices linked either directly to PO or through GRNs
    invs = list(getattr(po, "invoices", []) or [])
    for g in grns:
        for gi in (getattr(g, "invoices", []) or []):
            if gi not in invs:
                invs.append(gi)

    posted_invs = [inv for inv in invs if inv.status == "POSTED"]
    draft_invs = [inv for inv in invs if inv.status == "DRAFT"]

    total_ordered_base = sum(Decimal(str(it.ordered_qty_base or 0)) for it in (po.items or []))
    total_received_base = sum(Decimal(str(it.received_qty_base or 0)) for it in (po.items or []))

    if po.status == "CANCELLED":
        lifecycle_stage = "CANCELLED"
    elif po.status == "DRAFT":
        lifecycle_stage = "DRAFT"
    elif po.status == "SUBMITTED":
        lifecycle_stage = "SUBMITTED"
    elif po.status == "APPROVED":
        if not posted_grns and not any(g.status == "DRAFT" for g in grns):
            lifecycle_stage = "AWAITING_GOODS"
        elif draft_invs:
            lifecycle_stage = "PENDING_FINALIZATION"
        elif posted_invs:
            lifecycle_stage = "COMPLETED"
        else:
            lifecycle_stage = "AWAITING_INVOICE" if posted_grns else "AWAITING_GOODS"
    elif po.status == "PARTIALLY_RECEIVED":
        if draft_invs:
            lifecycle_stage = "PENDING_FINALIZATION"
        elif posted_invs and total_received_base >= total_ordered_base:
            lifecycle_stage = "COMPLETED"
        else:
            lifecycle_stage = "PARTIALLY_RECEIVED"
    elif po.status == "RECEIVED":
        if posted_invs:
            lifecycle_stage = "COMPLETED"
        elif draft_invs:
            lifecycle_stage = "PENDING_FINALIZATION"
        else:
            lifecycle_stage = "AWAITING_INVOICE"
    else:
        lifecycle_stage = po.status

    return {
        "id": po.id,
        "pharmacy_id": po.pharmacy_id,
        "po_number": po.po_number,
        "supplier_id": po.supplier_id,
        "supplier_name": po.supplier.name if po.supplier else None,
        "order_date": po.order_date,
        "expected_delivery_date": po.expected_delivery_date,
        "status": po.status,
        "subtotal": po.subtotal,
        "discount_total": po.discount_total,
        "tax_total": po.tax_total,
        "grand_total": po.grand_total,
        "notes": po.notes,
        "created_by": po.created_by,
        "created_by_name": getattr(po, "created_by_user", None),
        "approved_by": po.approved_by,
        "approved_at": po.approved_at,
        "cancelled_by": po.cancelled_by,
        "cancelled_at": po.cancelled_at,
        "created_at": po.created_at,
        "updated_at": po.updated_at,
        "lifecycle_stage": lifecycle_stage,
        "goods_receipts_count": len(grns),
        "invoices_count": len(invs),
        "total_ordered_base": total_ordered_base,
        "total_received_base": total_received_base,
        "items": [
            {
                "id": it.id,
                "po_id": it.po_id,
                "medicine_id": it.medicine_id,
                "medicine_name": it.medicine.name if it.medicine else None,
                "packaging_unit_id": it.packaging_unit_id,
                "packaging_unit_name": it.packaging_unit.name if it.packaging_unit else None,
                "conversion_factor": it.conversion_factor,
                "ordered_qty_pack": it.ordered_qty_pack,
                "ordered_qty_base": it.ordered_qty_base,
                "received_qty_base": it.received_qty_base,
                "purchase_rate": it.purchase_rate,
                "discount_percent": it.discount_percent,
                "tax_rate_id": it.tax_rate_id,
                "tax_rate_percentage": it.tax_rate.rate_percentage if it.tax_rate else None,
                "tax_amount": it.tax_amount,
                "line_total": it.line_total,
                "notes": it.notes,
            }
            for it in (po.items or [])
        ],
    }
