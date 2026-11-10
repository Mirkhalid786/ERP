"""
User management: create, edit, deactivate and reset passwords for staff accounts.

"Administrator" means any user whose role grants `users.manage` — decided by the
role's permissions, never by its display name. The system always keeps at least one
active administrator: changes that would remove the last one are refused, and the
check runs while the administrators' rows are locked so two simultaneous requests
cannot both remove "the other" admin.

Passwords are hashed with Argon2id (app.core.security) and are never returned,
logged or written to the audit trail.
"""
import json
from typing import List, Optional

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.security import hash_password
from app.models.auth import AuditLog, Permission, Role, RolePermission, User, UserSession

ADMIN_PERMISSION = "users.manage"


async def _role(db: AsyncSession, pharmacy_id: int, role_id: int) -> Role:
    role = (await db.execute(
        select(Role).where(Role.id == role_id, Role.pharmacy_id == pharmacy_id)
    )).scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    return role


async def admin_role_ids(db: AsyncSession, pharmacy_id: int) -> set:
    rows = await db.execute(
        select(Role.id)
        .join(RolePermission, RolePermission.role_id == Role.id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .where(Role.pharmacy_id == pharmacy_id, Permission.code == ADMIN_PERMISSION)
    )
    return {r[0] for r in rows.fetchall()}


async def _lock_active_admins(db: AsyncSession, pharmacy_id: int) -> List[User]:
    """Row-lock every active administrator (in id order) and return them."""
    role_ids = await admin_role_ids(db, pharmacy_id)
    if not role_ids:
        return []
    return list((await db.execute(
        select(User)
        .where(User.pharmacy_id == pharmacy_id, User.is_active.is_(True), User.role_id.in_(role_ids))
        .order_by(User.id)
        .with_for_update()
    )).scalars().all())


async def _check_username_free(db: AsyncSession, pharmacy_id: int, username: str, exclude_id: Optional[int] = None):
    stmt = select(User.id).where(User.pharmacy_id == pharmacy_id, func.lower(User.username) == username.lower())
    if exclude_id:
        stmt = stmt.where(User.id != exclude_id)
    if (await db.execute(stmt)).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Username '{username}' is already taken")


async def end_sessions(db: AsyncSession, user_id: int, keep_token: Optional[str] = None) -> int:
    """Invalidate the user's server-side sessions (optionally keeping the caller's own)."""
    stmt = update(UserSession).where(UserSession.user_id == user_id, UserSession.is_active.is_(True))
    if keep_token:
        stmt = stmt.where(UserSession.token != keep_token)
    result = await db.execute(stmt.values(is_active=False))
    return result.rowcount or 0


def _audit(db: AsyncSession, actor: User, action: str, target: User, details: dict, reason: Optional[str] = None):
    db.add(AuditLog(
        pharmacy_id=actor.pharmacy_id, user_id=actor.id, action=action, entity_type="user",
        entity_id=str(target.id), reason=reason,
        metadata_json=json.dumps({"username": target.username, **details}),
    ))


async def list_users(db: AsyncSession, pharmacy_id: int, q: Optional[str] = None,
                     include_inactive: bool = True) -> list:
    stmt = (
        select(User, Role.name, func.count(UserSession.id))
        .join(Role, Role.id == User.role_id)
        .outerjoin(UserSession, (UserSession.user_id == User.id) & (UserSession.is_active.is_(True))
                   & (UserSession.expires_at > utcnow()))
        .where(User.pharmacy_id == pharmacy_id)
        .group_by(User.id, Role.name)
        .order_by(User.is_active.desc(), User.name)
    )
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(User.name.ilike(like), User.username.ilike(like), Role.name.ilike(like)))
    if not include_inactive:
        stmt = stmt.where(User.is_active.is_(True))
    rows = (await db.execute(stmt)).all()
    admins = await admin_role_ids(db, pharmacy_id)
    creators = {u.id: u.name for u in (await db.execute(
        select(User).where(User.id.in_({u.created_by for u, _, _ in rows if u.created_by} or {0}))
    )).scalars().all()}
    return [{
        "id": u.id, "name": u.name, "username": u.username, "role_id": u.role_id, "role_name": role_name,
        "is_active": u.is_active, "is_admin": u.role_id in admins, "last_login_at": u.last_login_at,
        "created_at": u.created_at, "updated_at": u.updated_at,
        "created_by_name": creators.get(u.created_by), "active_sessions": sessions,
    } for u, role_name, sessions in rows]


async def get_user(db: AsyncSession, pharmacy_id: int, user_id: int, lock: bool = False) -> User:
    stmt = select(User).where(User.id == user_id, User.pharmacy_id == pharmacy_id)
    if lock:
        stmt = stmt.with_for_update()
    user = (await db.execute(stmt)).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


async def create_user(db: AsyncSession, actor: User, name: str, username: str, password: str, role_id: int) -> User:
    role = await _role(db, actor.pharmacy_id, role_id)
    await _check_username_free(db, actor.pharmacy_id, username)
    user = User(
        pharmacy_id=actor.pharmacy_id, name=name.strip(), username=username,
        password_hash=hash_password(password), role_id=role.id, is_active=True, created_by=actor.id,
    )
    db.add(user)
    await db.flush()
    _audit(db, actor, "user.create", user, {"role": role.name})
    return user


async def update_user(db: AsyncSession, actor: User, user_id: int, changes: dict) -> User:
    pharmacy_id = actor.pharmacy_id
    admins = await _lock_active_admins(db, pharmacy_id)        # Taken first: serialises admin changes
    target = await get_user(db, pharmacy_id, user_id, lock=True)
    admin_roles = await admin_role_ids(db, pharmacy_id)
    details: dict = {}

    new_role_id = changes.get("role_id")
    new_active = changes.get("is_active")
    if new_role_id is not None and new_role_id != target.role_id:
        old_role, new_role = await _role(db, pharmacy_id, target.role_id), await _role(db, pharmacy_id, new_role_id)
        details["role"] = {"from": old_role.name, "to": new_role.name}
    if new_active is not None and new_active != target.is_active:
        details["is_active"] = {"from": target.is_active, "to": new_active}

    # Would this change leave no active administrator?
    loses_admin = target.is_active and target.role_id in admin_roles and (
        new_active is False or (new_role_id is not None and new_role_id not in admin_roles)
    )
    if loses_admin:
        if target.id == actor.id and new_active is False:
            raise HTTPException(status_code=409, detail="You cannot deactivate your own account")
        if len([a for a in admins if a.id != target.id]) == 0:
            raise HTTPException(
                status_code=409,
                detail="This is the last active administrator. Make another user an administrator first.",
            )

    if "username" in changes and changes["username"] and changes["username"] != target.username:
        await _check_username_free(db, pharmacy_id, changes["username"], exclude_id=target.id)
        details["username"] = {"from": target.username, "to": changes["username"]}
        target.username = changes["username"]
    if "name" in changes and changes["name"] and changes["name"].strip() != target.name:
        details["name"] = True
        target.name = changes["name"].strip()
    if "role" in details:
        target.role_id = new_role_id
    if "is_active" in details:
        target.is_active = new_active

    if not details:
        return target
    action = "user.update"
    if details.get("is_active", {}).get("to") is False:
        action = "user.deactivate"
        details["sessions_ended"] = await end_sessions(db, target.id)
    elif details.get("is_active", {}).get("to") is True:
        action = "user.activate"
    elif "role" in details:
        action = "user.role_change"
    _audit(db, actor, action, target, details)
    await db.flush()
    return target


async def reset_password(db: AsyncSession, actor: User, user_id: int, new_password: str,
                         actor_session_token: Optional[str]) -> int:
    """Set a new password and end the user's sessions (the caller keeps theirs when resetting their own)."""
    target = await get_user(db, actor.pharmacy_id, user_id, lock=True)
    target.password_hash = hash_password(new_password)
    keep = actor_session_token if target.id == actor.id else None
    ended = await end_sessions(db, target.id, keep_token=keep)
    _audit(db, actor, "user.password_reset", target, {"sessions_ended": ended})
    await db.flush()
    return ended


async def user_audit(db: AsyncSession, pharmacy_id: int, user_id: int, limit: int = 50) -> list:
    """Administrative actions on this account (no passwords are ever stored)."""
    rows = (await db.execute(
        select(AuditLog, User.name)
        .outerjoin(User, User.id == AuditLog.user_id)
        .where(AuditLog.pharmacy_id == pharmacy_id, AuditLog.entity_type == "user",
               AuditLog.entity_id == str(user_id), AuditLog.action.like("user.%"),
               AuditLog.action.not_in(("user.login", "user.logout")))
        .order_by(AuditLog.id.desc()).limit(limit)
    )).all()
    return [{"action": a.action, "by": name, "at": a.timestamp,
             "details": json.loads(a.metadata_json) if a.metadata_json else {}} for a, name in rows]
