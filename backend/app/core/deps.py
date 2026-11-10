"""
Authentication dependencies for FastAPI route handlers.

Session model: HTTP-only signed cookie stores the session token.
The session token maps to a server-side session record in the database.
No JWT is issued to the browser — the token is opaque to the client.
"""
from typing import Optional
from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.config import settings
from app.models.auth import UserSession, User, Permission, RolePermission


class AuthenticationError(HTTPException):
    def __init__(self, detail: str = "Authentication required"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Cookie"},
        )


class PermissionDeniedError(HTTPException):
    def __init__(self, detail: str = "Insufficient permissions"):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
        )


async def get_current_session(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> UserSession:
    """
    Resolve the current session from the HTTP-only session cookie.
    Raises 401 if the session is missing, invalid or expired.
    """
    token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not token:
        raise AuthenticationError("No active session")

    result = await db.execute(
        select(UserSession)
        .where(UserSession.token == token)
        .where(UserSession.is_active == True)
    )
    session = result.scalar_one_or_none()

    if session is None:
        raise AuthenticationError("Session not found or expired")

    if session.is_expired:
        # Mark as inactive so it gets cleaned up
        session.is_active = False
        await db.commit()
        raise AuthenticationError("Session has expired")

    return session


async def get_current_user(
    session: UserSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Load the full User record for the current session.
    Raises 401 if the user account is inactive.
    """
    result = await db.execute(
        select(User).where(User.id == session.user_id)
    )
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise AuthenticationError("User account is not active")

    return user


async def get_user_permissions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> set[str]:
    """Return the set of permission codes granted to the current user's role."""
    result = await db.execute(
        select(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id == user.role_id)
    )
    return {row[0] for row in result.fetchall()}


def require_permission(permission_code: str):
    """
    FastAPI dependency factory that enforces a specific permission.

    Usage:
        @router.post("/sales", dependencies=[Depends(require_permission("sales.create"))])
    """
    async def _check(
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        permissions = await get_user_permissions(user=user, db=db)
        if permission_code not in permissions:
            raise PermissionDeniedError(
                f"Permission '{permission_code}' is required for this action"
            )
        return user

    return _check


class AuthContext:
    """The authenticated user plus their permission codes (for finer checks in services)."""

    def __init__(self, user: User, permissions: set[str]):
        self.user = user
        self.permissions = permissions

    @property
    def pharmacy_id(self) -> int:
        return self.user.pharmacy_id


def require_context(permission_code: str):
    """Like require_permission, but also hands the full permission set to the route."""
    async def _check(
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> AuthContext:
        permissions = await get_user_permissions(user=user, db=db)
        if permission_code not in permissions:
            raise PermissionDeniedError(f"Permission '{permission_code}' is required for this action")
        return AuthContext(user, permissions)

    return _check
