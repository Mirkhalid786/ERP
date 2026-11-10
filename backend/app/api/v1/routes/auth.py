"""
Authentication, setup, users and settings API routes.
"""
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.config import settings as app_settings
from app.core.deps import get_current_user, get_current_session, require_permission
from app.core.rate_limit import login_rate_limiter
from app.core.security import csrf_token_for_session, verify_session_csrf_token
from app.models.auth import AuditLog, Pharmacy, User, Role, Permission, RolePermission, Setting, UserSession
from app.schemas.auth import (
    PharmacySetupRequest, PharmacyResponse,
    LoginRequest, LoginResponse, CurrentUserResponse,
    UserCreateRequest, UserResponse,
    RoleResponse, SettingResponse, SettingUpdateRequest,
    MessageResponse,
)
from app.services.auth import (
    is_setup_required, run_first_time_setup,
    authenticate_user, create_session, invalidate_session,
    get_user_permissions,
)

router = APIRouter()


def _set_session_cookie(response: Response, token: str) -> None:
    """Set the HTTP-only session cookie on the response."""
    response.set_cookie(
        key=app_settings.SESSION_COOKIE_NAME,
        value=token,
        httponly=app_settings.SESSION_COOKIE_HTTPONLY,
        secure=app_settings.SESSION_COOKIE_SECURE,
        samesite=app_settings.SESSION_COOKIE_SAMESITE,
        max_age=app_settings.SESSION_MAX_AGE_SECONDS,
        path="/",
    )


def _set_csrf_cookie(response: Response, session_token: str) -> None:
    """Set the CSRF cookie. Not HTTP-only: the frontend reads it to send X-CSRF-Token."""
    response.set_cookie(
        key=app_settings.CSRF_COOKIE_NAME,
        value=csrf_token_for_session(session_token),
        httponly=False,
        secure=app_settings.SESSION_COOKIE_SECURE,
        samesite=app_settings.SESSION_COOKIE_SAMESITE,
        max_age=app_settings.SESSION_MAX_AGE_SECONDS,
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    """Clear the session and CSRF cookies."""
    response.delete_cookie(
        key=app_settings.SESSION_COOKIE_NAME,
        path="/",
    )
    response.delete_cookie(
        key=app_settings.CSRF_COOKIE_NAME,
        path="/",
    )


# ── Setup ──────────────────────────────────────────────────────────────────────

@router.get("/setup/status", response_model=dict, tags=["Setup"])
async def get_setup_status(db: AsyncSession = Depends(get_db)):
    """
    Returns whether first-run pharmacy setup is required.
    The frontend uses this to redirect to the setup wizard on first visit.
    """
    required = await is_setup_required(db)
    return {"setup_required": required}


@router.post("/setup", response_model=PharmacyResponse, status_code=201, tags=["Setup"])
async def first_run_setup(
    request: Request,
    body: PharmacySetupRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    One-time pharmacy and administrator setup.
    Can only be called once. Returns 409 if setup is already complete.
    """
    ip = request.client.host if request.client else None
    pharmacy = await run_first_time_setup(db, body, ip_address=ip)
    return PharmacyResponse(
        id=pharmacy.id,
        name=pharmacy.name,
        legal_name=pharmacy.legal_name,
        gstin=pharmacy.gstin,
        drug_license_number=pharmacy.drug_license_number,
        address_line1=pharmacy.address_line1,
        city=pharmacy.city,
        state_code=pharmacy.state_code,
        pincode=pharmacy.pincode,
        phone=pharmacy.phone,
        invoice_prefix=pharmacy.invoice_prefix,
        is_setup_complete=pharmacy.is_setup_complete,
        created_at=pharmacy.created_at,
    )


# ── Authentication ─────────────────────────────────────────────────────────────

@router.post("/auth/login", response_model=LoginResponse, tags=["Auth"])
async def login(
    request: Request,
    response: Response,
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Authenticate a user and issue a server-side session.
    The session token is stored in an HTTP-only cookie.
    The pharmacy_id is derived from the only configured pharmacy in V1.
    """
    # In V1, there is exactly one pharmacy
    result = await db.execute(select(Pharmacy).where(Pharmacy.is_setup_complete == True))
    pharmacy = result.scalar_one_or_none()
    if pharmacy is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Pharmacy setup is not complete. Run first-time setup first.",
        )

    client_ip = request.client.host if request.client else None
    login_rate_limiter.check(client_ip, body.username)
    try:
        user = await authenticate_user(db, pharmacy.id, body.username, body.password)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            login_rate_limiter.record_failure(client_ip, body.username)
        raise
    login_rate_limiter.record_success(client_ip, body.username)
    session = await create_session(db, user, request)

    # Load role name
    role_result = await db.execute(select(Role).where(Role.id == user.role_id))
    role = role_result.scalar_one()

    _set_session_cookie(response, session.token)
    _set_csrf_cookie(response, session.token)

    return LoginResponse(
        message="Login successful",
        user_id=user.id,
        username=user.username,
        name=user.name,
        role=role.name,
        pharmacy_id=pharmacy.id,
    )


@router.post("/auth/logout", response_model=MessageResponse, tags=["Auth"])
async def logout(
    response: Response,
    session: UserSession = Depends(get_current_session),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Invalidate the current session and clear the session cookie."""
    await invalidate_session(db, session, user)
    _clear_session_cookie(response)
    return MessageResponse(message="Logged out successfully")


@router.get("/auth/me", response_model=CurrentUserResponse, tags=["Auth"])
async def get_me(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the current authenticated user's profile and permissions."""
    # (Re)issue the CSRF cookie, e.g. for sessions created before it existed
    session_token = request.cookies.get(app_settings.SESSION_COOKIE_NAME)
    if session_token and not verify_session_csrf_token(
        session_token, request.cookies.get(app_settings.CSRF_COOKIE_NAME)
    ):
        _set_csrf_cookie(response, session_token)

    role_result = await db.execute(select(Role).where(Role.id == user.role_id))
    role = role_result.scalar_one()
    permissions = await get_user_permissions(db, user)

    return CurrentUserResponse(
        user_id=user.id,
        username=user.username,
        name=user.name,
        role_id=user.role_id,
        role=role.name,
        pharmacy_id=user.pharmacy_id,
        permissions=permissions,
        last_login_at=user.last_login_at,
    )


# ── Users: see app/api/v1/routes/users.py ─────────────────────────────────────


# ── Roles ──────────────────────────────────────────────────────────────────────

async def _require_roles_view(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    from app.core.deps import get_user_permissions, PermissionDeniedError
    permissions = await get_user_permissions(user=current_user, db=db)
    if not any(p in permissions for p in ["roles.manage", "users.manage", "settings.manage"]):
        raise PermissionDeniedError("Permission 'roles.manage', 'users.manage' or 'settings.manage' is required to view roles")
    return current_user


@router.get("/roles", response_model=list[RoleResponse], tags=["Roles"])
async def list_roles(
    user: User = Depends(_require_roles_view),
    db: AsyncSession = Depends(get_db),
):
    """List all roles for the current pharmacy."""
    result = await db.execute(
        select(Role).where(Role.pharmacy_id == user.pharmacy_id).order_by(Role.name)
    )
    roles = result.scalars().all()

    response_roles = []
    for role in roles:
        perms_result = await db.execute(
            select(Permission.code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == role.id)
        )
        perms = [row[0] for row in perms_result.fetchall()]
        response_roles.append(RoleResponse(
            id=role.id,
            name=role.name,
            description=role.description,
            is_system_role=role.is_system_role,
            permissions=perms,
        ))
    return response_roles


# ── Settings ───────────────────────────────────────────────────────────────────

@router.get("/settings", response_model=list[SettingResponse], tags=["Settings"])
async def list_settings(
    user: User = Depends(require_permission("settings.manage")),
    db: AsyncSession = Depends(get_db),
):
    """List all pharmacy settings (requires settings.manage permission)."""
    result = await db.execute(
        select(Setting)
        .where(Setting.pharmacy_id == user.pharmacy_id)
        .order_by(Setting.setting_key)
    )
    return [
        SettingResponse(
            setting_key=s.setting_key,
            setting_value=s.setting_value,
            description=s.description,
            updated_at=s.updated_at,
        )
        for s in result.scalars().all()
    ]


@router.put("/settings/{key}", response_model=SettingResponse, tags=["Settings"])
async def update_setting(
    key: str,
    body: SettingUpdateRequest,
    user: User = Depends(require_permission("settings.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Update a pharmacy setting by key (requires settings.manage permission)."""
    result = await db.execute(
        select(Setting)
        .where(Setting.pharmacy_id == user.pharmacy_id)
        .where(Setting.setting_key == key)
    )
    setting = result.scalar_one_or_none()
    if not setting:
        from app.services import app_settings
        if key in app_settings.DEFAULTS:
            default_val, desc = app_settings.DEFAULTS[key]
            setting = Setting(
                pharmacy_id=user.pharmacy_id,
                setting_key=key,
                setting_value=body.setting_value,
                description=desc,
                updated_by=user.id,
            )
            db.add(setting)
            await db.flush()
        else:
            raise HTTPException(status_code=404, detail=f"Setting '{key}' not found")
    else:
        setting.setting_value = body.setting_value
        setting.updated_by = user.id
        await db.flush()

    return SettingResponse(
        setting_key=setting.setting_key,
        setting_value=setting.setting_value,
        description=setting.description,
        updated_at=setting.updated_at,
    )


@router.get("/settings/general", tags=["Settings"])
async def general_settings(user: User = Depends(require_permission("settings.manage")), db: AsyncSession = Depends(get_db)):
    """Every editable application setting with its current value (default when never changed)."""
    from app.services import app_settings
    return [{"key": k, "value": await app_settings.get_setting(db, user.pharmacy_id, k),
             "default": app_settings.DEFAULTS[k][0], "description": app_settings.DEFAULTS[k][1]}
            for k in app_settings.general_keys()]


@router.put("/settings/general/{key}", tags=["Settings"])
async def update_general_setting(key: str, body: SettingUpdateRequest,
                                 user: User = Depends(require_permission("settings.manage")), db: AsyncSession = Depends(get_db)):
    from app.services import app_settings
    value = app_settings.validate_general(key, body.setting_value or "")
    before = await app_settings.get_setting(db, user.pharmacy_id, key)
    await app_settings.set_setting(db, user.pharmacy_id, key, value, user.id)
    db.add(AuditLog(pharmacy_id=user.pharmacy_id, user_id=user.id, action="settings.update", entity_type="setting",
                    entity_id=key, metadata_json=json.dumps({"from": before, "to": value})))
    await db.flush()
    return {"key": key, "value": value}
