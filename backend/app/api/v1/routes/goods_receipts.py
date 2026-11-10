"""
Goods Receipt Inward (GRN) API routes.
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_permission
from app.models.auth import User
from app.schemas.purchasing import (
    GoodsReceiptCreate, GoodsReceiptResponse,
)
from app.services.purchasing import GoodsReceiptService

router = APIRouter(prefix="/purchases/receipts", tags=["Goods Receipts"])


@router.get("", response_model=List[GoodsReceiptResponse])
async def list_goods_receipts(
    supplier_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None, description="DRAFT, POSTED, CANCELLED"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_permission("purchases.view")),
    db: AsyncSession = Depends(get_db),
):
    """List goods receipt notes."""
    items, _ = await GoodsReceiptService.list_goods_receipts(
        db,
        pharmacy_id=current_user.pharmacy_id,
        supplier_id=supplier_id,
        status_filter=status,
        limit=limit,
        offset=offset,
    )
    return [_serialize_grn(grn) for grn in items]


@router.get("/{grn_id}", response_model=GoodsReceiptResponse)
async def get_goods_receipt(
    grn_id: int,
    current_user: User = Depends(require_permission("purchases.view")),
    db: AsyncSession = Depends(get_db),
):
    """Get single goods receipt note."""
    grn = await GoodsReceiptService.get_goods_receipt(db, current_user.pharmacy_id, grn_id)
    return _serialize_grn(grn)


@router.post("", response_model=GoodsReceiptResponse, status_code=status.HTTP_201_CREATED)
async def create_goods_receipt(
    body: GoodsReceiptCreate,
    current_user: User = Depends(require_permission("purchases.create")),
    db: AsyncSession = Depends(get_db),
):
    """Create draft goods receipt inward (does NOT mutate inventory until posted)."""
    grn = await GoodsReceiptService.create_goods_receipt(
        db,
        pharmacy_id=current_user.pharmacy_id,
        payload=body,
        user_id=current_user.id,
    )
    return _serialize_grn(grn)


@router.post("/{grn_id}/post", response_model=GoodsReceiptResponse)
async def post_goods_receipt(
    grn_id: int,
    current_user: User = Depends(require_permission("purchases.post")),
    db: AsyncSession = Depends(get_db),
):
    """
    Atomically post goods receipt to inventory:
      - Validates and creates/updates batches
      - Adds accepted stock to saleable inventory
      - Inserts immutable GOODS_RECEIPT stock ledger movement
      - Excludes rejected items from saleable inventory
      - Updates linked purchase order fulfillment
    """
    grn = await GoodsReceiptService.post_goods_receipt(
        db,
        pharmacy_id=current_user.pharmacy_id,
        grn_id=grn_id,
        user_id=current_user.id,
    )
    return _serialize_grn(grn)


def _serialize_grn(grn) -> dict:
    return {
        "id": grn.id,
        "pharmacy_id": grn.pharmacy_id,
        "grn_number": grn.grn_number,
        "supplier_id": grn.supplier_id,
        "supplier_name": grn.supplier.name if grn.supplier else None,
        "po_id": grn.po_id,
        "po_number": grn.purchase_order.po_number if grn.purchase_order else None,
        "receipt_date": grn.receipt_date,
        "supplier_challan_no": grn.supplier_challan_no,
        "supplier_invoice_no": grn.supplier_invoice_no,
        "status": grn.status,
        "total_received_base": grn.total_received_base,
        "total_accepted_base": grn.total_accepted_base,
        "total_rejected_base": grn.total_rejected_base,
        "total_free_base": grn.total_free_base,
        "subtotal": grn.subtotal,
        "tax_total": grn.tax_total,
        "grand_total": grn.grand_total,
        "notes": grn.notes,
        "received_by": grn.received_by,
        "posted_at": grn.posted_at,
        "created_at": grn.created_at,
        "updated_at": grn.updated_at,
        "items": [
            {
                "id": it.id,
                "grn_id": it.grn_id,
                "po_item_id": it.po_item_id,
                "medicine_id": it.medicine_id,
                "medicine_name": it.medicine.name if it.medicine else None,
                "batch_id": it.batch_id,
                "batch_number": it.batch_number,
                "manufacturing_date": it.manufacturing_date,
                "expiry_date": it.expiry_date,
                "packaging_unit_id": it.packaging_unit_id,
                "packaging_unit_name": it.packaging_unit.name if it.packaging_unit else None,
                "conversion_factor": it.conversion_factor,
                "received_qty_pack": it.received_qty_pack,
                "free_qty_pack": it.free_qty_pack,
                "rejected_qty_pack": it.rejected_qty_pack,
                "accepted_qty_base": it.accepted_qty_base,
                "rejected_qty_base": it.rejected_qty_base,
                "rejection_reason": it.rejection_reason,
                "purchase_rate": it.purchase_rate,
                "discount_percent": it.discount_percent,
                "mrp": it.mrp,
                "selling_rate": it.selling_rate,
                "cost_per_base_unit": it.cost_per_base_unit,
                "tax_rate_id": it.tax_rate_id,
                "cgst_amount": it.cgst_amount,
                "sgst_amount": it.sgst_amount,
                "igst_amount": it.igst_amount,
                "tax_amount": it.tax_amount,
                "line_total": it.line_total,
                "rack_location": it.rack_location,
            }
            for it in (grn.items or [])
        ],
    }
