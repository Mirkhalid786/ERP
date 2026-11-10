"""
Suppliers API routes.
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_permission
from app.models.auth import User
from app.schemas.purchasing import (
    SupplierCreate, SupplierUpdate, SupplierResponse,
)
from app.services.purchasing import SupplierService

router = APIRouter(prefix="/suppliers", tags=["Suppliers"])


async def _require_supplier_view(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    from app.core.deps import get_user_permissions, PermissionDeniedError
    permissions = await get_user_permissions(user=current_user, db=db)
    if "suppliers.manage" not in permissions and "purchases.view" not in permissions:
        raise PermissionDeniedError("Permission 'suppliers.manage' or 'purchases.view' is required to view suppliers")
    return current_user


@router.get("", response_model=List[SupplierResponse])
async def list_suppliers(
    search: Optional[str] = Query(None, description="Search by name, legal name, GSTIN, phone, city"),
    active_only: bool = Query(False),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(_require_supplier_view),
    db: AsyncSession = Depends(get_db),
):
    """List suppliers with optional search and active filtering."""
    items, _ = await SupplierService.list_suppliers(
        db,
        pharmacy_id=current_user.pharmacy_id,
        search=search,
        active_only=active_only,
        limit=limit,
        offset=offset,
    )
    return items


@router.get("/{supplier_id}", response_model=SupplierResponse)
async def get_supplier(
    supplier_id: int,
    current_user: User = Depends(_require_supplier_view),
    db: AsyncSession = Depends(get_db),
):
    """Get single supplier detail."""
    return await SupplierService.get_supplier(db, current_user.pharmacy_id, supplier_id)


@router.post("", response_model=SupplierResponse, status_code=status.HTTP_201_CREATED)
async def create_supplier(
    body: SupplierCreate,
    current_user: User = Depends(require_permission("suppliers.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new supplier with GSTIN and Drug License details."""
    return await SupplierService.create_supplier(
        db,
        pharmacy_id=current_user.pharmacy_id,
        payload=body,
        user_id=current_user.id,
    )


@router.put("/{supplier_id}", response_model=SupplierResponse)
async def update_supplier(
    supplier_id: int,
    body: SupplierUpdate,
    current_user: User = Depends(require_permission("suppliers.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Update supplier master details."""
    return await SupplierService.update_supplier(
        db,
        pharmacy_id=current_user.pharmacy_id,
        supplier_id=supplier_id,
        payload=body,
        user_id=current_user.id,
    )


@router.delete("/{supplier_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_supplier(
    supplier_id: int,
    current_user: User = Depends(require_permission("suppliers.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Delete an unreferenced supplier."""
    await SupplierService.delete_supplier(
        db,
        pharmacy_id=current_user.pharmacy_id,
        supplier_id=supplier_id,
        user_id=current_user.id,
    )
