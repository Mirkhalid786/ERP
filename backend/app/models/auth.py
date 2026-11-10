"""
SQLAlchemy ORM models for Phase 1:
  - Pharmacy profile
  - Roles and permissions
  - Users
  - User sessions
  - Settings

Design principles:
  - BIGINT UNSIGNED primary keys
  - DECIMAL(12,2) for monetary values
  - UTC timestamps stored as DATETIME
  - InnoDB with explicit foreign key constraints
  - Soft-delete only on master data, never on transactions
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    BIGINT, BigInteger, Boolean, DateTime, ForeignKey, Integer,
    String, Text, UniqueConstraint, func, event,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.mysql import BIGINT as MYSQL_BIGINT

from app.core.database import Base

# Cross-database BigInteger: BIGINT UNSIGNED on MySQL, INTEGER on SQLite for autoincrement support
PK_BIGINT = Integer().with_variant(MYSQL_BIGINT(unsigned=True), "mysql")
FK_BIGINT = Integer().with_variant(MYSQL_BIGINT(unsigned=True), "mysql")


# ── Helper ─────────────────────────────────────────────────────────────────────

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Pharmacies ─────────────────────────────────────────────────────────────────

class Pharmacy(Base):
    """
    The pharmacy business profile. Version 1 supports one pharmacy.
    pharmacy_id is carried on all transactional tables to support future
    multi-branch expansion without schema restructuring.
    """
    __tablename__ = "pharmacies"

    id: Mapped[int] = mapped_column(
        PK_BIGINT, primary_key=True, autoincrement=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    legal_name: Mapped[Optional[str]] = mapped_column(String(255))
    gstin: Mapped[Optional[str]] = mapped_column(String(15), index=True)
    drug_license_number: Mapped[Optional[str]] = mapped_column(String(100))
    address_line1: Mapped[Optional[str]] = mapped_column(String(255))
    address_line2: Mapped[Optional[str]] = mapped_column(String(255))
    city: Mapped[Optional[str]] = mapped_column(String(100))
    state_code: Mapped[Optional[str]] = mapped_column(String(2))  # ISO 3166-2 state code
    pincode: Mapped[Optional[str]] = mapped_column(String(6))
    phone: Mapped[Optional[str]] = mapped_column(String(15))
    email: Mapped[Optional[str]] = mapped_column(String(255))
    invoice_prefix: Mapped[str] = mapped_column(String(10), nullable=False, default="INV")
    is_setup_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    # Relationships
    users: Mapped[list["User"]] = relationship("User", back_populates="pharmacy")
    roles: Mapped[list["Role"]] = relationship("Role", back_populates="pharmacy")
    settings: Mapped[list["Setting"]] = relationship("Setting", back_populates="pharmacy")

    def __repr__(self) -> str:
        return f"<Pharmacy id={self.id} name={self.name!r}>"


# ── Roles ──────────────────────────────────────────────────────────────────────

class Role(Base):
    """
    User role within a pharmacy.
    Suggested roles: Owner/Admin, Pharmacist, Cashier, Inventory Manager.
    Roles are pharmacy-scoped so future pharmacies can define their own.
    """
    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("pharmacy_id", "name", name="uq_roles_pharmacy_name"),
    )

    id: Mapped[int] = mapped_column(
        PK_BIGINT, primary_key=True, autoincrement=True
    )
    pharmacy_id: Mapped[int] = mapped_column(
        FK_BIGINT,
        ForeignKey("pharmacies.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(500))
    is_system_role: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="System roles cannot be deleted"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    # Relationships
    pharmacy: Mapped["Pharmacy"] = relationship("Pharmacy", back_populates="roles")
    users: Mapped[list["User"]] = relationship("User", back_populates="role")
    role_permissions: Mapped[list["RolePermission"]] = relationship(
        "RolePermission", back_populates="role", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Role id={self.id} name={self.name!r}>"


# ── Permissions ────────────────────────────────────────────────────────────────

class Permission(Base):
    """
    Global permission codes. Permissions are not pharmacy-scoped because
    the permission vocabulary is defined by the application, not the business.

    Examples: sales.create, sales.refund, purchases.create,
              stock.adjust, reports.view, users.manage, backups.manage
    """
    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    description: Mapped[Optional[str]] = mapped_column(String(500))
    module: Mapped[str] = mapped_column(
        String(50), nullable=False, default="general",
        comment="Logical module this permission belongs to"
    )

    # Relationships
    role_permissions: Mapped[list["RolePermission"]] = relationship(
        "RolePermission", back_populates="permission"
    )

    def __repr__(self) -> str:
        return f"<Permission code={self.code!r}>"


class RolePermission(Base):
    """Junction table: many-to-many between roles and permissions."""
    __tablename__ = "role_permissions"
    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uq_role_permissions_pair"),
    )

    role_id: Mapped[int] = mapped_column(
        FK_BIGINT,
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    permission_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
    )

    # Relationships
    role: Mapped["Role"] = relationship("Role", back_populates="role_permissions")
    permission: Mapped["Permission"] = relationship(
        "Permission", back_populates="role_permissions"
    )


# ── Users ──────────────────────────────────────────────────────────────────────

class User(Base):
    """
    Pharmacy staff user account.
    Passwords are stored only as Argon2id hashes. Plain passwords are never
    written to the database or logged.
    """
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("pharmacy_id", "username", name="uq_users_pharmacy_username"),
    )

    id: Mapped[int] = mapped_column(
        PK_BIGINT, primary_key=True, autoincrement=True
    )
    pharmacy_id: Mapped[int] = mapped_column(
        FK_BIGINT,
        ForeignKey("pharmacies.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    password_hash: Mapped[str] = mapped_column(
        String(255), nullable=False,
        comment="Argon2id hash. Never store or log plaintext."
    )
    role_id: Mapped[int] = mapped_column(
        FK_BIGINT,
        ForeignKey("roles.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    created_by: Mapped[Optional[int]] = mapped_column(
        FK_BIGINT,
        ForeignKey("users.id", ondelete="SET NULL"),
    )

    # Relationships
    pharmacy: Mapped["Pharmacy"] = relationship("Pharmacy", back_populates="users")
    role: Mapped["Role"] = relationship("Role", back_populates="users")
    sessions: Mapped[list["UserSession"]] = relationship(
        "UserSession", back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username!r}>"


# ── User Sessions ──────────────────────────────────────────────────────────────

class UserSession(Base):
    """
    Server-side session record. The session token is stored in an HTTP-only
    cookie on the client. The browser never sees the user_id or any other
    session data directly.

    Sessions expire after SESSION_MAX_AGE_SECONDS.
    Inactive sessions should be cleaned up periodically.
    """
    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(
        PK_BIGINT, primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(
        FK_BIGINT,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    pharmacy_id: Mapped[int] = mapped_column(
        FK_BIGINT,
        ForeignKey("pharmacies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True, index=True,
        comment="Opaque random token stored in browser HTTP-only cookie"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    ip_address: Mapped[Optional[str]] = mapped_column(String(45))  # IPv4/IPv6
    user_agent: Mapped[Optional[str]] = mapped_column(String(500))

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="sessions")

    @property
    def is_expired(self) -> bool:
        now = datetime.now(timezone.utc)
        if self.expires_at.tzinfo is None:
            return now.replace(tzinfo=None) >= self.expires_at
        return now >= self.expires_at

    def __repr__(self) -> str:
        return f"<UserSession id={self.id} user_id={self.user_id} active={self.is_active}>"


# ── Settings ───────────────────────────────────────────────────────────────────

class Setting(Base):
    """
    Key-value application settings per pharmacy.
    Used for receipt layout, alert thresholds, tax configuration, etc.
    Do not store passwords, tokens or private keys in this table.
    """
    __tablename__ = "settings"
    __table_args__ = (
        UniqueConstraint("pharmacy_id", "setting_key", name="uq_settings_pharmacy_key"),
    )

    id: Mapped[int] = mapped_column(
        PK_BIGINT, primary_key=True, autoincrement=True
    )
    pharmacy_id: Mapped[int] = mapped_column(
        FK_BIGINT,
        ForeignKey("pharmacies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    setting_key: Mapped[str] = mapped_column(String(100), nullable=False)
    setting_value: Mapped[Optional[str]] = mapped_column(Text)
    description: Mapped[Optional[str]] = mapped_column(String(500))
    updated_by: Mapped[Optional[int]] = mapped_column(
        FK_BIGINT,
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    # Relationships
    pharmacy: Mapped["Pharmacy"] = relationship("Pharmacy", back_populates="settings")

    def __repr__(self) -> str:
        return f"<Setting key={self.setting_key!r} pharmacy_id={self.pharmacy_id}>"


# ── Audit Log ─────────────────────────────────────────────────────────────────

class AuditLog(Base):
    """
    Immutable audit trail. Records are appended only — never updated or deleted.
    Log important actions: login, logout, user creation, permission changes,
    pharmacy setup, settings updates.
    Do not log passwords, tokens or unnecessary sensitive data.
    """
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(
        PK_BIGINT, primary_key=True, autoincrement=True
    )
    pharmacy_id: Mapped[Optional[int]] = mapped_column(
        FK_BIGINT,
        ForeignKey("pharmacies.id", ondelete="SET NULL"),
        index=True,
    )
    user_id: Mapped[Optional[int]] = mapped_column(
        FK_BIGINT,
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    action: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True,
        comment="e.g. user.login, user.create, pharmacy.setup, settings.update"
    )
    entity_type: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    entity_id: Mapped[Optional[str]] = mapped_column(String(50))
    ip_address: Mapped[Optional[str]] = mapped_column(String(45))
    reason: Mapped[Optional[str]] = mapped_column(String(500))
    metadata_json: Mapped[Optional[str]] = mapped_column(
        Text, comment="Additional structured data. Never include passwords or tokens."
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )

    def __repr__(self) -> str:
        return f"<AuditLog action={self.action!r} user_id={self.user_id}>"
