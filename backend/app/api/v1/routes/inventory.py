"""
Inventory Ledger, Adjustments, and Stock Alerts API routes.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.deps import get_current_user, require_permission
from app.models.auth import User
from app.models.inventory import (
    Medicine, MedicineBatch, StockMovement, StockAdjustment, StockAdjustmentItem,
    Unit,
)
from app.schemas.inventory import (
    OpeningStockCreate, StockMovementResponse,
    StockAdjustmentCreate, StockAdjustmentResponse, StockAdjustmentItemResponse,
    LowStockAlert, ExpiryAlert, MedicineBatchResponse,
)
from app.services.inventory import InventoryService
from app.core.clock import today_local

router = APIRouter(prefix="/inventory", tags=["Inventory"])


# ── Opening Stock ──────────────────────────────────────────────────────────────

@router.post("/opening-stock", response_model=StockMovementResponse, status_code=status.HTTP_201_CREATED)
async def create_opening_stock(
    body: OpeningStockCreate,
    current_user: User = Depends(require_permission("stock.adjust")),
    db: AsyncSession = Depends(get_db),
):
    """
    Record opening stock balance for a medicine batch.
    Creates or updates batch and posts an immutable OPENING_STOCK movement.
    """
    batch, movement = await InventoryService.record_opening_stock(
        db=db,
        pharmacy_id=current_user.pharmacy_id,
        medicine_id=body.medicine_id,
        batch_number=body.batch_number,
        expiry_date=body.expiry_date,
        quantity_in_pack=body.quantity_in_pack,
        mrp=body.mrp,
        purchase_rate=body.purchase_rate,
        default_selling_rate=body.default_selling_rate,
        packaging_unit_id=body.packaging_unit_id,
        manufacturing_date=body.manufacturing_date,
        rack_location=body.rack_location,
        notes=body.notes,
        user_id=current_user.id,
    )

    # Load medicine for response
    m_q = select(Medicine).where(Medicine.id == body.medicine_id)
    m_res = await db.execute(m_q)
    med = m_res.scalar_one()

    return StockMovementResponse(
        id=movement.id,
        pharmacy_id=movement.pharmacy_id,
        medicine_id=movement.medicine_id,
        medicine_name=med.name,
        batch_id=movement.batch_id,
        batch_number=batch.batch_number,
        movement_type=movement.movement_type,
        reference_id=movement.reference_id,
        reference_type=movement.reference_type,
        quantity_base=movement.quantity_base,
        balance_after_base=movement.balance_after_base,
        unit_cost=movement.unit_cost,
        total_cost=movement.total_cost,
        reason=movement.reason,
        notes=movement.notes,
        user_id=movement.user_id,
        created_at=movement.created_at,
    )


# ── Stock Adjustments ──────────────────────────────────────────────────────────

@router.post("/adjustments", response_model=StockAdjustmentResponse, status_code=status.HTTP_201_CREATED)
async def create_stock_adjustment(
    body: StockAdjustmentCreate,
    current_user: User = Depends(require_permission("stock.adjust")),
    db: AsyncSession = Depends(get_db),
):
    """
    Post a physical stock audit adjustment voucher.
    Updates batch balances atomically with row-locking and writes ledger movements.
    """
    items_payload = [item.model_dump() for item in body.items]
    adj = await InventoryService.record_stock_adjustment(
        db=db,
        pharmacy_id=current_user.pharmacy_id,
        reason_code=body.reason_code,
        items_data=items_payload,
        notes=body.notes,
        user_id=current_user.id,
    )

    # Reload adjustment with items and names
    q = select(StockAdjustment).options(
        selectinload(StockAdjustment.items).selectinload(StockAdjustmentItem.medicine),
        selectinload(StockAdjustment.items).selectinload(StockAdjustmentItem.batch),
    ).where(StockAdjustment.id == adj.id)
    res = await db.execute(q)
    loaded = res.scalar_one()

    items_resp = [
        StockAdjustmentItemResponse(
            id=it.id,
            medicine_id=it.medicine_id,
            medicine_name=it.medicine.name if it.medicine else None,
            batch_id=it.batch_id,
            batch_number=it.batch.batch_number if it.batch else None,
            system_stock_base=it.system_stock_base,
            physical_stock_base=it.physical_stock_base,
            adjustment_qty_base=it.adjustment_qty_base,
            unit_cost=it.unit_cost,
            total_cost=it.total_cost,
            notes=it.notes,
        )
        for it in loaded.items
    ]

    return StockAdjustmentResponse(
        id=loaded.id,
        pharmacy_id=loaded.pharmacy_id,
        adjustment_number=loaded.adjustment_number,
        reason_code=loaded.reason_code,
        status=loaded.status,
        notes=loaded.notes,
        adjusted_by=loaded.adjusted_by,
        created_at=loaded.created_at,
        items=items_resp,
    )


@router.get("/adjustments", response_model=List[StockAdjustmentResponse])
async def list_stock_adjustments(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_permission("stock.view")),
    db: AsyncSession = Depends(get_db),
):
    """List stock adjustment vouchers."""
    q = select(StockAdjustment).options(
        selectinload(StockAdjustment.items).selectinload(StockAdjustmentItem.medicine),
        selectinload(StockAdjustment.items).selectinload(StockAdjustmentItem.batch),
    ).where(
        StockAdjustment.pharmacy_id == current_user.pharmacy_id
    ).order_by(StockAdjustment.created_at.desc()).offset(offset).limit(limit)

    res = await db.execute(q)
    adjustments = res.scalars().all()

    output = []
    for adj in adjustments:
        items_resp = [
            StockAdjustmentItemResponse(
                id=it.id,
                medicine_id=it.medicine_id,
                medicine_name=it.medicine.name if it.medicine else None,
                batch_id=it.batch_id,
                batch_number=it.batch.batch_number if it.batch else None,
                system_stock_base=it.system_stock_base,
                physical_stock_base=it.physical_stock_base,
                adjustment_qty_base=it.adjustment_qty_base,
                unit_cost=it.unit_cost,
                total_cost=it.total_cost,
                notes=it.notes,
            )
            for it in adj.items
        ]
        output.append(StockAdjustmentResponse(
            id=adj.id,
            pharmacy_id=adj.pharmacy_id,
            adjustment_number=adj.adjustment_number,
            reason_code=adj.reason_code,
            status=adj.status,
            notes=adj.notes,
            adjusted_by=adj.adjusted_by,
            created_at=adj.created_at,
            items=items_resp,
        ))
    return output


# ── Immutable Movement Ledger ──────────────────────────────────────────────────

@router.get("/movements", response_model=List[StockMovementResponse])
async def list_stock_movements(
    medicine_id: Optional[int] = Query(None),
    batch_id: Optional[int] = Query(None),
    movement_type: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_permission("stock.view")),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve immutable stock ledger movements."""
    q = select(StockMovement).options(
        selectinload(StockMovement.medicine),
        selectinload(StockMovement.batch),
    ).where(StockMovement.pharmacy_id == current_user.pharmacy_id)

    if medicine_id:
        q = q.where(StockMovement.medicine_id == medicine_id)
    if batch_id:
        q = q.where(StockMovement.batch_id == batch_id)
    if movement_type:
        q = q.where(StockMovement.movement_type == movement_type.upper())

    q = q.order_by(StockMovement.created_at.desc()).offset(offset).limit(limit)
    res = await db.execute(q)
    movements = res.scalars().all()

    return [
        StockMovementResponse(
            id=m.id,
            pharmacy_id=m.pharmacy_id,
            medicine_id=m.medicine_id,
            medicine_name=m.medicine.name if m.medicine else None,
            batch_id=m.batch_id,
            batch_number=m.batch.batch_number if m.batch else None,
            movement_type=m.movement_type,
            reference_id=m.reference_id,
            reference_type=m.reference_type,
            quantity_base=m.quantity_base,
            balance_after_base=m.balance_after_base,
            unit_cost=m.unit_cost,
            total_cost=m.total_cost,
            reason=m.reason,
            notes=m.notes,
            user_id=m.user_id,
            created_at=m.created_at,
        )
        for m in movements
    ]


# ── Inventory Alerts & Reports ─────────────────────────────────────────────────

@router.get("/alerts/low-stock", response_model=List[LowStockAlert])
async def get_low_stock_alerts(
    current_user: User = Depends(require_permission("stock.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    List all active, non-discontinued medicines with configured reorder thresholds
    where total active stock in base units is <= reorder_level.
    Catalogue-only medicines without reorder configuration are excluded.
    """
    today = today_local()
    # Aggregate current usable stock across active, non-expired batches in a subquery
    batch_stock_subq = (
        select(
            MedicineBatch.medicine_id.label("med_id"),
            func.coalesce(func.sum(MedicineBatch.current_stock_base), Decimal("0.00")).label("stock_sum")
        )
        .where(
            MedicineBatch.pharmacy_id == current_user.pharmacy_id,
            MedicineBatch.is_active == True,
            MedicineBatch.expiry_date >= today,
        )
        .group_by(MedicineBatch.medicine_id)
        .subquery()
    )

    stock_val = func.coalesce(batch_stock_subq.c.stock_sum, Decimal("0.00"))

    q = (
        select(Medicine, stock_val.label("curr_stock"))
        .outerjoin(batch_stock_subq, Medicine.id == batch_stock_subq.c.med_id)
        .options(selectinload(Medicine.base_unit))
        .where(
            Medicine.pharmacy_id == current_user.pharmacy_id,
            Medicine.is_active == True,
            Medicine.is_discontinued == False,
            Medicine.reorder_level > Decimal("0.00"),
            stock_val <= Medicine.reorder_level,
        )
        .order_by(Medicine.name.asc())
    )
    res = await db.execute(q)
    rows = res.all()

    alerts = []
    for med, curr_stock in rows:
        alerts.append(LowStockAlert(
            medicine_id=med.id,
            medicine_name=med.name,
            generic_name=med.generic_name,
            dosage_form=med.dosage_form,
            reorder_level=med.reorder_level,
            current_stock_base=curr_stock,
            base_unit_name=med.base_unit.name if med.base_unit else "",
            base_unit_abbr=med.base_unit.abbreviation if med.base_unit else "",
        ))
    return alerts


@router.get("/alerts/near-expiry", response_model=List[ExpiryAlert])
async def get_near_expiry_alerts(
    days: int = Query(90, ge=1, le=365),
    current_user: User = Depends(require_permission("stock.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    List batches expiring within specified days (default 90 days) with remaining stock.
    """
    today = today_local()
    cutoff = today + timedelta(days=days)

    q = select(MedicineBatch).options(
        selectinload(MedicineBatch.medicine).selectinload(Medicine.base_unit)
    ).where(
        MedicineBatch.pharmacy_id == current_user.pharmacy_id,
        MedicineBatch.is_active == True,
        MedicineBatch.current_stock_base > Decimal("0.00"),
        MedicineBatch.expiry_date >= today,
        MedicineBatch.expiry_date <= cutoff,
    ).order_by(MedicineBatch.expiry_date.asc())

    res = await db.execute(q)
    batches = res.scalars().all()

    alerts = []
    for b in batches:
        days_left = (b.expiry_date - today).days
        alerts.append(ExpiryAlert(
            batch_id=b.id,
            medicine_id=b.medicine_id,
            medicine_name=b.medicine.name,
            batch_number=b.batch_number,
            expiry_date=b.expiry_date,
            days_to_expiry=days_left,
            current_stock_base=b.current_stock_base,
            base_unit_abbr=b.medicine.base_unit.abbreviation,
            rack_location=b.rack_location,
            cost_per_base_unit=b.cost_per_base_unit,
            stock_value=(Decimal(str(b.current_stock_base)) * Decimal(str(b.cost_per_base_unit))).quantize(Decimal("0.01")),
            is_expired=False,
        ))
    return alerts


@router.get("/alerts/expired", response_model=List[ExpiryAlert])
async def get_expired_batches(
    current_user: User = Depends(require_permission("stock.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    List batches that have passed expiry date but still have non-zero stock.
    """
    today = today_local()

    q = select(MedicineBatch).options(
        selectinload(MedicineBatch.medicine).selectinload(Medicine.base_unit)
    ).where(
        MedicineBatch.pharmacy_id == current_user.pharmacy_id,
        MedicineBatch.is_active == True,
        MedicineBatch.current_stock_base > Decimal("0.00"),
        MedicineBatch.expiry_date < today,
    ).order_by(MedicineBatch.expiry_date.asc())

    res = await db.execute(q)
    batches = res.scalars().all()

    alerts = []
    for b in batches:
        days_past = (today - b.expiry_date).days
        alerts.append(ExpiryAlert(
            batch_id=b.id,
            medicine_id=b.medicine_id,
            medicine_name=b.medicine.name,
            batch_number=b.batch_number,
            expiry_date=b.expiry_date,
            days_to_expiry=-days_past,
            current_stock_base=b.current_stock_base,
            base_unit_abbr=b.medicine.base_unit.abbreviation,
            rack_location=b.rack_location,
            cost_per_base_unit=b.cost_per_base_unit,
            stock_value=(Decimal(str(b.current_stock_base)) * Decimal(str(b.cost_per_base_unit))).quantize(Decimal("0.01")),
            is_expired=True,
        ))
    return alerts


@router.get("/valuation", response_model=dict)
async def get_inventory_valuation(
    current_user: User = Depends(require_permission("reports.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Calculate total inventory valuation (cost value and retail MRP value).
    """
    q = select(
        func.count(MedicineBatch.id).label("total_batches"),
        func.sum(MedicineBatch.current_stock_base).label("total_base_units"),
        func.sum(MedicineBatch.current_stock_base * MedicineBatch.cost_per_base_unit).label("total_cost_valuation"),
    ).where(
        MedicineBatch.pharmacy_id == current_user.pharmacy_id,
        MedicineBatch.is_active == True,
        MedicineBatch.current_stock_base > Decimal("0.00"),
    )
    res = await db.execute(q)
    row = res.one()

    return {
        "active_batches_count": row.total_batches or 0,
        "total_base_units": str(row.total_base_units or Decimal("0.00")),
        "total_cost_valuation": str(round(Decimal(str(row.total_cost_valuation or 0)), 2)),
    }
