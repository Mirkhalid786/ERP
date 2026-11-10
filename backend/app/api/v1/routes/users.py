"""Staff user management (permission: users.manage)."""
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import require_permission
from app.core.json import decimal_json
from app.models.auth import User
from app.schemas.auth import ManagedUserResponse, PasswordResetRequest, UserCreateRequest, UserUpdateRequest
from app.services import users as user_service

router = APIRouter(prefix="/users", tags=["Users"])


async def _one(db: AsyncSession, pharmacy_id: int, user_id: int) -> ManagedUserResponse:
    rows = [u for u in await user_service.list_users(db, pharmacy_id) if u["id"] == user_id]
    return ManagedUserResponse(**rows[0])


@router.get("", response_model=list[ManagedUserResponse])
async def list_users(
    q: Optional[str] = Query(None, max_length=100),
    include_inactive: bool = True,
    current_user: User = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    return await user_service.list_users(db, current_user.pharmacy_id, q, include_inactive)


@router.post("", response_model=ManagedUserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreateRequest,
    current_user: User = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    user = await user_service.create_user(db, current_user, body.name, body.username, body.password, body.role_id)
    return await _one(db, current_user.pharmacy_id, user.id)


@router.get("/{user_id}", response_model=ManagedUserResponse)
async def get_user(
    user_id: int,
    current_user: User = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    await user_service.get_user(db, current_user.pharmacy_id, user_id)
    return await _one(db, current_user.pharmacy_id, user_id)


@router.put("/{user_id}", response_model=ManagedUserResponse)
async def update_user(
    user_id: int,
    body: UserUpdateRequest,
    current_user: User = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Edit name, username, role or active status. The last active administrator is protected."""
    pharmacy_id = current_user.pharmacy_id
    await user_service.update_user(db, current_user, user_id, body.model_dump(exclude_unset=True))
    return await _one(db, pharmacy_id, user_id)


@router.post("/{user_id}/reset-password")
async def reset_password(
    user_id: int,
    body: PasswordResetRequest,
    request: Request,
    current_user: User = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Set a new password and sign the user out everywhere (your own current session is kept)."""
    ended = await user_service.reset_password(
        db, current_user, user_id, body.new_password, request.cookies.get(settings.SESSION_COOKIE_NAME))
    return {"message": "Password changed", "sessions_ended": ended}


@router.get("/{user_id}/audit")
async def user_audit(
    user_id: int,
    current_user: User = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    await user_service.get_user(db, current_user.pharmacy_id, user_id)
    return decimal_json(await user_service.user_audit(db, current_user.pharmacy_id, user_id))
