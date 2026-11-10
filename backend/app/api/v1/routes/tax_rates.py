"""
Tax Rates API routes — GST slabs and calculation utilities.
"""
import json
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_permission
from app.models.auth import User, AuditLog
from app.models.inventory import TaxRate
from app.schemas.inventory import (
    TaxRateCreate, TaxRateUpdate, TaxRateResponse,
)
from app.services.tax import GstTaxCalculationService

router = APIRouter(prefix="/tax-rates", tags=["Tax Rates"])


@router.get("", response_model=List[TaxRateResponse])
async def list_tax_rates(
    active_only: bool = Query(True),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all configured GST tax rates."""
    query = select(TaxRate).where(TaxRate.pharmacy_id == current_user.pharmacy_id)
    if active_only:
        query = query.where(TaxRate.is_active == True)
    query = query.order_by(TaxRate.rate_percentage.asc())
    result = await db.execute(query)
    return result.scalars().all()


@router.post("", response_model=TaxRateResponse, status_code=status.HTTP_201_CREATED)
async def create_tax_rate(
    body: TaxRateCreate,
    current_user: User = Depends(require_permission("settings.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new GST tax rate slab."""
    # Check duplicate
    dup_q = select(TaxRate).where(
        TaxRate.pharmacy_id == current_user.pharmacy_id,
        TaxRate.name == body.name.strip(),
    )
    dup_res = await db.execute(dup_q)
    if dup_res.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Tax rate '{body.name}' already exists")

    cgst = body.cgst_percentage if body.cgst_percentage > Decimal("0") else (body.rate_percentage / Decimal("2"))
    sgst = body.sgst_percentage if body.sgst_percentage > Decimal("0") else (body.rate_percentage / Decimal("2"))
    igst = body.igst_percentage if body.igst_percentage > Decimal("0") else body.rate_percentage

    tax_rate = TaxRate(
        pharmacy_id=current_user.pharmacy_id,
        name=body.name.strip(),
        rate_percentage=body.rate_percentage,
        cgst_percentage=cgst,
        sgst_percentage=sgst,
        igst_percentage=igst,
        cess_percentage=body.cess_percentage,
        is_active=body.is_active,
    )
    db.add(tax_rate)
    await db.flush()

    db.add(AuditLog(
        pharmacy_id=current_user.pharmacy_id,
        user_id=current_user.id,
        action="tax_rate.create",
        entity_type="tax_rate",
        entity_id=str(tax_rate.id),
        metadata_json=json.dumps({"name": tax_rate.name, "rate": str(tax_rate.rate_percentage)}),
    ))
    return tax_rate


@router.get("/{tax_rate_id}", response_model=TaxRateResponse)
async def get_tax_rate(
    tax_rate_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve details of a specific tax rate."""
    q = select(TaxRate).where(
        TaxRate.id == tax_rate_id,
        TaxRate.pharmacy_id == current_user.pharmacy_id,
    )
    res = await db.execute(q)
    item = res.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Tax rate not found")
    return item


@router.put("/{tax_rate_id}", response_model=TaxRateResponse)
async def update_tax_rate(
    tax_rate_id: int,
    body: TaxRateUpdate,
    current_user: User = Depends(require_permission("settings.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Update a tax rate."""
    q = select(TaxRate).where(
        TaxRate.id == tax_rate_id,
        TaxRate.pharmacy_id == current_user.pharmacy_id,
    )
    res = await db.execute(q)
    tax_rate = res.scalar_one_or_none()
    if not tax_rate:
        raise HTTPException(status_code=404, detail="Tax rate not found")

    old_vals = {"name": tax_rate.name, "rate": str(tax_rate.rate_percentage)}

    if body.name is not None:
        tax_rate.name = body.name.strip()
    if body.rate_percentage is not None:
        tax_rate.rate_percentage = body.rate_percentage
        tax_rate.cgst_percentage = body.cgst_percentage or (body.rate_percentage / Decimal("2"))
        tax_rate.sgst_percentage = body.sgst_percentage or (body.rate_percentage / Decimal("2"))
        tax_rate.igst_percentage = body.igst_percentage or body.rate_percentage
    if body.cess_percentage is not None:
        tax_rate.cess_percentage = body.cess_percentage
    if body.is_active is not None:
        tax_rate.is_active = body.is_active

    db.add(AuditLog(
        pharmacy_id=current_user.pharmacy_id,
        user_id=current_user.id,
        action="tax_rate.update",
        entity_type="tax_rate",
        entity_id=str(tax_rate.id),
        metadata_json=json.dumps({
            "old_values": old_vals,
            "new_values": {"name": tax_rate.name, "rate": str(tax_rate.rate_percentage)},
        }),
    ))
    return tax_rate


@router.post("/calculate", response_model=dict)
async def calculate_tax(
    amount: Decimal = Query(..., gt=0),
    rate_percentage: Decimal = Query(..., ge=0),
    is_tax_inclusive: bool = Query(True),
    is_inter_state: bool = Query(False),
    current_user: User = Depends(get_current_user),
):
    """
    Test/utility endpoint for verifying GST calculation breakdowns.
    """
    calc = GstTaxCalculationService.calculate_tax(
        amount=amount,
        rate_percentage=rate_percentage,
        is_tax_inclusive=is_tax_inclusive,
        is_inter_state=is_inter_state,
    )
    return {k: str(v) if isinstance(v, Decimal) else v for k, v in calc.items()}
