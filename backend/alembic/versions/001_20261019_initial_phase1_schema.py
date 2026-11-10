"""Phase 1 initial schema: pharmacies, roles, permissions, users, sessions, settings, audit logs

Revision ID: 001
Revises: 
Create Date: 2026-10-19 00:00:00.000000 UTC

This migration creates the complete Phase 1 database schema:
  - pharmacies
  - roles
  - permissions
  - role_permissions
  - users
  - user_sessions
  - settings
  - audit_logs

All tables use:
  - InnoDB storage engine
  - utf8mb4 character set
  - BIGINT UNSIGNED primary keys
  - UTC DATETIME timestamps
  - Explicit foreign key constraints
  - Named indexes and constraints matching SQLAlchemy naming conventions
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── pharmacies ────────────────────────────────────────────────────────────
    op.create_table(
        "pharmacies",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("legal_name", sa.String(255), nullable=True),
        sa.Column("gstin", sa.String(15), nullable=True),
        sa.Column("drug_license_number", sa.String(100), nullable=True),
        sa.Column("address_line1", sa.String(255), nullable=True),
        sa.Column("address_line2", sa.String(255), nullable=True),
        sa.Column("city", sa.String(100), nullable=True),
        sa.Column("state_code", sa.String(2), nullable=True),
        sa.Column("pincode", sa.String(6), nullable=True),
        sa.Column("phone", sa.String(15), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("invoice_prefix", sa.String(10), nullable=False, server_default="INV"),
        sa.Column("is_setup_complete", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_pharmacies"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_pharmacies_gstin", "pharmacies", ["gstin"])

    # ── roles ─────────────────────────────────────────────────────────────────
    op.create_table(
        "roles",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("pharmacy_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("is_system_role", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_roles"),
        sa.ForeignKeyConstraint(
            ["pharmacy_id"], ["pharmacies.id"],
            name="fk_roles_pharmacy_id_pharmacies",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("pharmacy_id", "name", name="uq_roles_pharmacy_name"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_roles_pharmacy_id", "roles", ["pharmacy_id"])

    # ── permissions ───────────────────────────────────────────────────────────
    op.create_table(
        "permissions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("module", sa.String(50), nullable=False, server_default="general"),
        sa.PrimaryKeyConstraint("id", name="pk_permissions"),
        sa.UniqueConstraint("code", name="uq_permissions_code"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_permissions_code", "permissions", ["code"])

    # ── role_permissions ──────────────────────────────────────────────────────
    op.create_table(
        "role_permissions",
        sa.Column("role_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("permission_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("role_id", "permission_id", name="pk_role_permissions"),
        sa.ForeignKeyConstraint(
            ["role_id"], ["roles.id"],
            name="fk_role_permissions_role_id_roles",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["permission_id"], ["permissions.id"],
            name="fk_role_permissions_permission_id_permissions",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("role_id", "permission_id", name="uq_role_permissions_pair"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )

    # ── users ─────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("pharmacy_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("username", sa.String(100), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False,
                  comment="Argon2id hash. Never store or log plaintext."),
        sa.Column("role_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", mysql.BIGINT(unsigned=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.ForeignKeyConstraint(
            ["pharmacy_id"], ["pharmacies.id"],
            name="fk_users_pharmacy_id_pharmacies",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["roles.id"],
            name="fk_users_role_id_roles",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"],
            name="fk_users_created_by_users",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("pharmacy_id", "username", name="uq_users_pharmacy_username"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_users_pharmacy_id", "users", ["pharmacy_id"])
    op.create_index("ix_users_role_id", "users", ["role_id"])

    # ── user_sessions ─────────────────────────────────────────────────────────
    op.create_table(
        "user_sessions",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("pharmacy_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("token", sa.String(128), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_user_sessions"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name="fk_user_sessions_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["pharmacy_id"], ["pharmacies.id"],
            name="fk_user_sessions_pharmacy_id_pharmacies",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("token", name="uq_user_sessions_token"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_user_sessions_token", "user_sessions", ["token"])
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_pharmacy_id", "user_sessions", ["pharmacy_id"])

    # ── settings ──────────────────────────────────────────────────────────────
    op.create_table(
        "settings",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("pharmacy_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("setting_key", sa.String(100), nullable=False),
        sa.Column("setting_value", sa.Text(), nullable=True),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("updated_by", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_settings"),
        sa.ForeignKeyConstraint(
            ["pharmacy_id"], ["pharmacies.id"],
            name="fk_settings_pharmacy_id_pharmacies",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"],
            name="fk_settings_updated_by_users",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("pharmacy_id", "setting_key", name="uq_settings_pharmacy_key"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_settings_pharmacy_id", "settings", ["pharmacy_id"])

    # ── audit_logs ────────────────────────────────────────────────────────────
    op.create_table(
        "audit_logs",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("pharmacy_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(100), nullable=True),
        sa.Column("entity_id", sa.String(50), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
        sa.ForeignKeyConstraint(
            ["pharmacy_id"], ["pharmacies.id"],
            name="fk_audit_logs_pharmacy_id_pharmacies",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name="fk_audit_logs_user_id_users",
            ondelete="SET NULL",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_audit_logs_pharmacy_id", "audit_logs", ["pharmacy_id"])
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_timestamp", "audit_logs", ["timestamp"])
    op.create_index("ix_audit_logs_entity_type", "audit_logs", ["entity_type"])


def downgrade() -> None:
    """
    Drop all Phase 1 tables in reverse dependency order.
    WARNING: This will delete all pharmacy data. Only use in development.
    """
    op.drop_table("audit_logs")
    op.drop_table("settings")
    op.drop_table("user_sessions")
    op.drop_table("users")
    op.drop_table("role_permissions")
    op.drop_table("permissions")
    op.drop_table("roles")
    op.drop_table("pharmacies")
