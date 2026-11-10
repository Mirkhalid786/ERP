"""
Batches API routes.
"""
import json
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.deps import get_current_user, require_permission
from app.models.auth import User, AuditLog
from app.models.inventory import MedicineBatch, Medicine
from app.schemas.inventory import (
    MedicineBatchCreate, MedicineBatchUpdate, MedicineBatchResponse, BatchStockStatusUpdate,
)
from app.services.inventory import InventoryService
from app.core.clock import today_local

router = APIRouter(prefix="/batches", tags=["Batches"])


@router.get("", response_model=List[MedicineBatchResponse])
async def list_batches(
    medicine_id: Optional[int] = Query(None),
    active_only: bool = Query(True),
    non_expired_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List medicine batches with filtering."""
    q = select(MedicineBatch).options(
        selectinload(MedicineBatch.medicine)
    ).where(MedicineBatch.pharmacy_id == current_user.pharmacy_id)

    if medicine_id:
        q = q.where(MedicineBatch.medicine_id == medicine_id)
    if active_only:
        q = q.where(MedicineBatch.is_active == True)
    if non_expired_only:
        q = q.where(MedicineBatch.expiry_date >= today_local())

    q = q.order_by(MedicineBatch.expiry_date.asc()).offset(offset).limit(limit)
    res = await db.execute(q)
    batches = res.scalars().all()

    return [
        MedicineBatchResponse(
            id=b.id,
            pharmacy_id=b.pharmacy_id,
            medicine_id=b.medicine_id,
            medicine_name=b.medicine.name if b.medicine else None,
            batch_number=b.batch_number,
            manufacturing_date=b.manufacturing_date,
            expiry_date=b.expiry_date,
            mrp=b.mrp,
            purchase_rate=b.purchase_rate,
            cost_per_base_unit=b.cost_per_base_unit,
            default_selling_rate=b.default_selling_rate,
            rack_location=b.rack_location,
            current_stock_base=b.current_stock_base,
            is_expired=b.is_expired,
            stock_status=b.stock_status,
            price_unit_id=b.price_unit_id,
            price_conversion_factor=b.price_conversion_factor,
            is_active=b.is_active,
            created_at=b.created_at,
            updated_at=b.updated_at,
        )
        for b in batches
    ]


@router.post("", response_model=MedicineBatchResponse, status_code=status.HTTP_201_CREATED)
async def create_batch(
    body: MedicineBatchCreate,
    current_user: User = Depends(require_permission("medicines.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new batch for a medicine."""
    batch = await InventoryService.create_batch(
        db=db,
        pharmacy_id=current_user.pharmacy_id,
        medicine_id=body.medicine_id,
        batch_number=body.batch_number,
        expiry_date=body.expiry_date,
        mrp=body.mrp,
        purchase_rate=body.purchase_rate,
        default_selling_rate=body.default_selling_rate,
        cost_per_base_unit=body.cost_per_base_unit,
        manufacturing_date=body.manufacturing_date,
        rack_location=body.rack_location,
        user_id=current_user.id,
    )

    # Load medicine relationship
    q = select(MedicineBatch).options(selectinload(MedicineBatch.medicine)).where(MedicineBatch.id == batch.id)
    r = await db.execute(q)
    b = r.scalar_one()

    return MedicineBatchResponse(
        id=b.id,
        pharmacy_id=b.pharmacy_id,
        medicine_id=b.medicine_id,
        medicine_name=b.medicine.name if b.medicine else None,
        batch_number=b.batch_number,
        manufacturing_date=b.manufacturing_date,
        expiry_date=b.expiry_date,
        mrp=b.mrp,
        purchase_rate=b.purchase_rate,
        cost_per_base_unit=b.cost_per_base_unit,
        default_selling_rate=b.default_selling_rate,
        rack_location=b.rack_location,
        current_stock_base=b.current_stock_base,
        is_expired=b.is_expired,
        stock_status=b.stock_status,
        price_unit_id=b.price_unit_id,
        price_conversion_factor=b.price_conversion_factor,
        is_active=b.is_active,
        created_at=b.created_at,
        updated_at=b.updated_at,
    )


@router.get("/{batch_id}", response_model=MedicineBatchResponse)
async def get_batch(
    batch_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve details of a specific batch."""
    q = select(MedicineBatch).options(
        selectinload(MedicineBatch.medicine)
    ).where(
        MedicineBatch.id == batch_id,
        MedicineBatch.pharmacy_id == current_user.pharmacy_id,
    )
    res = await db.execute(q)
    b = res.scalar_one_or_none()
    if not b:
        raise HTTPException(status_code=404, detail="Batch not found")

    return MedicineBatchResponse(
        id=b.id,
        pharmacy_id=b.pharmacy_id,
        medicine_id=b.medicine_id,
        medicine_name=b.medicine.name if b.medicine else None,
        batch_number=b.batch_number,
        manufacturing_date=b.manufacturing_date,
        expiry_date=b.expiry_date,
        mrp=b.mrp,
        purchase_rate=b.purchase_rate,
        cost_per_base_unit=b.cost_per_base_unit,
        default_selling_rate=b.default_selling_rate,
        rack_location=b.rack_location,
        current_stock_base=b.current_stock_base,
        is_expired=b.is_expired,
        stock_status=b.stock_status,
        price_unit_id=b.price_unit_id,
        price_conversion_factor=b.price_conversion_factor,
        is_active=b.is_active,
        created_at=b.created_at,
        updated_at=b.updated_at,
    )


@router.put("/{batch_id}", response_model=MedicineBatchResponse)
async def update_batch(
    batch_id: int,
    body: MedicineBatchUpdate,
    current_user: User = Depends(require_permission("medicines.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Update batch pricing or shelf location."""
    q = select(MedicineBatch).options(
        selectinload(MedicineBatch.medicine)
    ).where(
        MedicineBatch.id == batch_id,
        MedicineBatch.pharmacy_id == current_user.pharmacy_id,
    )
    res = await db.execute(q)
    b = res.scalar_one_or_none()
    if not b:
        raise HTTPException(status_code=404, detail="Batch not found")

    old_vals = {"mrp": str(b.mrp), "selling_rate": str(b.default_selling_rate), "rack": b.rack_location}

    if body.manufacturing_date is not None:
        b.manufacturing_date = body.manufacturing_date
    if body.expiry_date is not None:
        b.expiry_date = body.expiry_date
    if body.mrp is not None:
        b.mrp = body.mrp
    if body.purchase_rate is not None:
        b.purchase_rate = body.purchase_rate
    if body.cost_per_base_unit is not None:
        b.cost_per_base_unit = body.cost_per_base_unit
    if body.default_selling_rate is not None:
        if body.default_selling_rate > b.mrp:
            raise HTTPException(status_code=422, detail="Selling rate cannot exceed MRP")
        b.default_selling_rate = body.default_selling_rate
    if body.rack_location is not None:
        b.rack_location = body.rack_location.strip() if body.rack_location else None
    if body.is_active is not None:
        b.is_active = body.is_active

    db.add(AuditLog(
        pharmacy_id=current_user.pharmacy_id,
        user_id=current_user.id,
        action="batch.update",
        entity_type="medicine_batch",
        entity_id=str(b.id),
        metadata_json=json.dumps({
            "old_values": old_vals,
            "new_values": {"mrp": str(b.mrp), "selling_rate": str(b.default_selling_rate), "rack": b.rack_location},
        }),
    ))
    return MedicineBatchResponse(
        id=b.id,
        pharmacy_id=b.pharmacy_id,
        medicine_id=b.medicine_id,
        medicine_name=b.medicine.name if b.medicine else None,
        batch_number=b.batch_number,
        manufacturing_date=b.manufacturing_date,
        expiry_date=b.expiry_date,
        mrp=b.mrp,
        purchase_rate=b.purchase_rate,
        cost_per_base_unit=b.cost_per_base_unit,
        default_selling_rate=b.default_selling_rate,
        rack_location=b.rack_location,
        current_stock_base=b.current_stock_base,
        is_expired=b.is_expired,
        stock_status=b.stock_status,
        price_unit_id=b.price_unit_id,
        price_conversion_factor=b.price_conversion_factor,
        is_active=b.is_active,
        created_at=b.created_at,
        updated_at=b.updated_at,
    )


@router.delete("/{batch_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_batch(
    batch_id: int,
    current_user: User = Depends(require_permission("medicines.manage")),
    db: AsyncSession = Depends(get_db),
):
    """
    Delete a batch if and only if no historical stock movements reference it.
    """
    await InventoryService.delete_batch(
        db=db,
        pharmacy_id=current_user.pharmacy_id,
        batch_id=batch_id,
        user_id=current_user.id,
    )


@router.patch("/{batch_id}/stock-status", response_model=MedicineBatchResponse)
async def update_batch_stock_status(
    batch_id: int,
    body: BatchStockStatusUpdate,
    current_user: User = Depends(require_permission("stock.adjust")),
    db: AsyncSession = Depends(get_db),
):
    """
    Quarantine, recall or reject a batch (or release it back to ACTIVE).
    Only ACTIVE, unexpired batches can be sold at the POS.
    """
    q = select(MedicineBatch).options(selectinload(MedicineBatch.medicine)).where(
        MedicineBatch.id == batch_id,
        MedicineBatch.pharmacy_id == current_user.pharmacy_id,
    ).with_for_update()
    b = (await db.execute(q)).scalar_one_or_none()
    if not b:
        raise HTTPException(status_code=404, detail="Batch not found")

    previous = b.stock_status
    b.stock_status = body.stock_status
    db.add(AuditLog(
        pharmacy_id=current_user.pharmacy_id,
        user_id=current_user.id,
        action="batch.stock_status",
        entity_type="medicine_batch",
        entity_id=str(b.id),
        reason=body.reason,
        metadata_json=json.dumps({"from": previous, "to": body.stock_status, "batch_number": b.batch_number}),
    ))
    await db.flush()
    return await get_batch(batch_id=batch_id, current_user=current_user, db=db)
