"""
Manufacturers API routes.
"""
import json
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_permission
from app.models.auth import User, AuditLog
from app.models.inventory import Manufacturer
from app.schemas.inventory import (
    ManufacturerCreate, ManufacturerUpdate, ManufacturerResponse,
)

router = APIRouter(prefix="/manufacturers", tags=["Manufacturers"])


@router.get("", response_model=List[ManufacturerResponse])
async def list_manufacturers(
    active_only: bool = Query(True),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List medicine manufacturers."""
    q = select(Manufacturer).where(Manufacturer.pharmacy_id == current_user.pharmacy_id)
    if active_only:
        q = q.where(Manufacturer.is_active == True)
    q = q.order_by(Manufacturer.name.asc())
    res = await db.execute(q)
    return res.scalars().all()


@router.post("", response_model=ManufacturerResponse, status_code=status.HTTP_201_CREATED)
async def create_manufacturer(
    body: ManufacturerCreate,
    current_user: User = Depends(require_permission("medicines.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new pharmaceutical manufacturer."""
    dup_q = select(Manufacturer).where(
        Manufacturer.pharmacy_id == current_user.pharmacy_id,
        Manufacturer.name == body.name.strip(),
    )
    dup_res = await db.execute(dup_q)
    if dup_res.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Manufacturer '{body.name}' already exists")

    mfg = Manufacturer(
        pharmacy_id=current_user.pharmacy_id,
        name=body.name.strip(),
        short_code=body.short_code.strip() if body.short_code else None,
        contact_email=body.contact_email.strip() if body.contact_email else None,
        contact_phone=body.contact_phone.strip() if body.contact_phone else None,
        is_active=body.is_active,
    )
    db.add(mfg)
    await db.flush()

    db.add(AuditLog(
        pharmacy_id=current_user.pharmacy_id,
        user_id=current_user.id,
        action="manufacturer.create",
        entity_type="manufacturer",
        entity_id=str(mfg.id),
        metadata_json=json.dumps({"name": mfg.name}),
    ))
    return mfg


@router.get("/{manufacturer_id}", response_model=ManufacturerResponse)
async def get_manufacturer(
    manufacturer_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get manufacturer details."""
    q = select(Manufacturer).where(
        Manufacturer.id == manufacturer_id,
        Manufacturer.pharmacy_id == current_user.pharmacy_id,
    )
    res = await db.execute(q)
    mfg = res.scalar_one_or_none()
    if not mfg:
        raise HTTPException(status_code=404, detail="Manufacturer not found")
    return mfg


@router.put("/{manufacturer_id}", response_model=ManufacturerResponse)
async def update_manufacturer(
    manufacturer_id: int,
    body: ManufacturerUpdate,
    current_user: User = Depends(require_permission("medicines.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Update manufacturer details."""
    q = select(Manufacturer).where(
        Manufacturer.id == manufacturer_id,
        Manufacturer.pharmacy_id == current_user.pharmacy_id,
    )
    res = await db.execute(q)
    mfg = res.scalar_one_or_none()
    if not mfg:
        raise HTTPException(status_code=404, detail="Manufacturer not found")

    old_vals = {"name": mfg.name, "is_active": mfg.is_active}
    if body.name is not None:
        mfg.name = body.name.strip()
    if body.short_code is not None:
        mfg.short_code = body.short_code.strip() if body.short_code else None
    if body.contact_email is not None:
        mfg.contact_email = body.contact_email.strip() if body.contact_email else None
    if body.contact_phone is not None:
        mfg.contact_phone = body.contact_phone.strip() if body.contact_phone else None
    if body.is_active is not None:
        mfg.is_active = body.is_active

    db.add(AuditLog(
        pharmacy_id=current_user.pharmacy_id,
        user_id=current_user.id,
        action="manufacturer.update",
        entity_type="manufacturer",
        entity_id=str(mfg.id),
        metadata_json=json.dumps({
            "old_values": old_vals,
            "new_values": {"name": mfg.name, "is_active": mfg.is_active},
        }),
    ))
    return mfg
