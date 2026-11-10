"""
Categories API routes.
"""
import json
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_permission
from app.models.auth import User, AuditLog
from app.models.inventory import Category
from app.schemas.inventory import (
    CategoryCreate, CategoryUpdate, CategoryResponse,
)

router = APIRouter(prefix="/categories", tags=["Categories"])


@router.get("", response_model=List[CategoryResponse])
async def list_categories(
    active_only: bool = Query(True),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List medicine categories."""
    q = select(Category).where(Category.pharmacy_id == current_user.pharmacy_id)
    if active_only:
        q = q.where(Category.is_active == True)
    q = q.order_by(Category.name.asc())
    res = await db.execute(q)
    return res.scalars().all()


@router.post("", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED)
async def create_category(
    body: CategoryCreate,
    current_user: User = Depends(require_permission("medicines.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new medicine category."""
    dup_q = select(Category).where(
        Category.pharmacy_id == current_user.pharmacy_id,
        Category.name == body.name.strip(),
    )
    dup_res = await db.execute(dup_q)
    if dup_res.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Category '{body.name}' already exists")

    cat = Category(
        pharmacy_id=current_user.pharmacy_id,
        name=body.name.strip(),
        description=body.description.strip() if body.description else None,
        is_active=body.is_active,
    )
    db.add(cat)
    await db.flush()

    db.add(AuditLog(
        pharmacy_id=current_user.pharmacy_id,
        user_id=current_user.id,
        action="category.create",
        entity_type="category",
        entity_id=str(cat.id),
        metadata_json=json.dumps({"name": cat.name}),
    ))
    return cat


@router.get("/{category_id}", response_model=CategoryResponse)
async def get_category(
    category_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get category details."""
    q = select(Category).where(
        Category.id == category_id,
        Category.pharmacy_id == current_user.pharmacy_id,
    )
    res = await db.execute(q)
    cat = res.scalar_one_or_none()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    return cat


@router.put("/{category_id}", response_model=CategoryResponse)
async def update_category(
    category_id: int,
    body: CategoryUpdate,
    current_user: User = Depends(require_permission("medicines.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing category."""
    q = select(Category).where(
        Category.id == category_id,
        Category.pharmacy_id == current_user.pharmacy_id,
    )
    res = await db.execute(q)
    cat = res.scalar_one_or_none()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")

    old_vals = {"name": cat.name, "is_active": cat.is_active}
    if body.name is not None:
        cat.name = body.name.strip()
    if body.description is not None:
        cat.description = body.description.strip() if body.description else None
    if body.is_active is not None:
        cat.is_active = body.is_active

    db.add(AuditLog(
        pharmacy_id=current_user.pharmacy_id,
        user_id=current_user.id,
        action="category.update",
        entity_type="category",
        entity_id=str(cat.id),
        metadata_json=json.dumps({
            "old_values": old_vals,
            "new_values": {"name": cat.name, "is_active": cat.is_active},
        }),
    ))
    return cat
