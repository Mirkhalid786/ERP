"""
Units API routes — measurement and dispensing units.
"""
import json
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_permission
from app.models.auth import User, AuditLog
from app.models.inventory import Unit
from app.schemas.inventory import (
    UnitCreate, UnitUpdate, UnitResponse,
)

router = APIRouter(prefix="/units", tags=["Units"])


@router.get("", response_model=List[UnitResponse])
async def list_units(
    active_only: bool = Query(True),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all units of measurement."""
    q = select(Unit).where(Unit.pharmacy_id == current_user.pharmacy_id)
    if active_only:
        q = q.where(Unit.is_active == True)
    q = q.order_by(Unit.name.asc())
    res = await db.execute(q)
    return res.scalars().all()


@router.post("", response_model=UnitResponse, status_code=status.HTTP_201_CREATED)
async def create_unit(
    body: UnitCreate,
    current_user: User = Depends(require_permission("medicines.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new unit of measurement."""
    # Check duplicate name or abbreviation
    dup_q = select(Unit).where(
        Unit.pharmacy_id == current_user.pharmacy_id,
        (Unit.name == body.name.strip()) | (Unit.abbreviation == body.abbreviation.strip().upper()),
    )
    dup_res = await db.execute(dup_q)
    if dup_res.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"Unit with name '{body.name}' or abbreviation '{body.abbreviation}' already exists",
        )

    unit = Unit(
        pharmacy_id=current_user.pharmacy_id,
        name=body.name.strip(),
        abbreviation=body.abbreviation.strip().upper(),
        is_fractional_allowed=body.is_fractional_allowed,
        is_active=body.is_active,
    )
    db.add(unit)
    await db.flush()

    db.add(AuditLog(
        pharmacy_id=current_user.pharmacy_id,
        user_id=current_user.id,
        action="unit.create",
        entity_type="unit",
        entity_id=str(unit.id),
        metadata_json=json.dumps({"name": unit.name, "abbr": unit.abbreviation}),
    ))
    return unit


@router.get("/{unit_id}", response_model=UnitResponse)
async def get_unit(
    unit_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get unit details."""
    q = select(Unit).where(
        Unit.id == unit_id,
        Unit.pharmacy_id == current_user.pharmacy_id,
    )
    res = await db.execute(q)
    unit = res.scalar_one_or_none()
    if not unit:
        raise HTTPException(status_code=404, detail="Unit not found")
    return unit


@router.put("/{unit_id}", response_model=UnitResponse)
async def update_unit(
    unit_id: int,
    body: UnitUpdate,
    current_user: User = Depends(require_permission("medicines.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Update unit details."""
    q = select(Unit).where(
        Unit.id == unit_id,
        Unit.pharmacy_id == current_user.pharmacy_id,
    )
    res = await db.execute(q)
    unit = res.scalar_one_or_none()
    if not unit:
        raise HTTPException(status_code=404, detail="Unit not found")

    old_vals = {"name": unit.name, "abbr": unit.abbreviation}
    if body.name is not None:
        unit.name = body.name.strip()
    if body.abbreviation is not None:
        unit.abbreviation = body.abbreviation.strip().upper()
    if body.is_fractional_allowed is not None:
        unit.is_fractional_allowed = body.is_fractional_allowed
    if body.is_active is not None:
        unit.is_active = body.is_active

    db.add(AuditLog(
        pharmacy_id=current_user.pharmacy_id,
        user_id=current_user.id,
        action="unit.update",
        entity_type="unit",
        entity_id=str(unit.id),
        metadata_json=json.dumps({
            "old_values": old_vals,
            "new_values": {"name": unit.name, "abbr": unit.abbreviation},
        }),
    ))
    return unit
