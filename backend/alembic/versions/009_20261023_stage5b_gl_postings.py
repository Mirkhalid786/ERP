"""Stage 5B: automatic GL postings — the posting-issues table (failed postings awaiting
retry) and the 'accounting.postings_manage' permission (Owner / Admin). No existing data
is changed. The extra system accounts used by the posting rules (Discounts Allowed,
Purchase Price Variance, Cash Shortages & Excess, Bad Debts) are created by the chart
seeding when accounting is set up; live accounting is not set up by this migration.

Revision ID: 009
Revises: 008
Create Date: 2026-10-23 00:00:00.000000 UTC
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PERMISSION = ("accounting.postings_manage", "Retry failed or missing automatic postings")


def upgrade() -> None:
    bigint = sa.Integer().with_variant(mysql.BIGINT(unsigned=True), "mysql")
    op.create_table(
        "gl_posting_issues",
        sa.Column("id", bigint, autoincrement=True, nullable=False),
        sa.Column("pharmacy_id", bigint, nullable=False),
        sa.Column("source_type", sa.String(length=40), nullable=False),
        sa.Column("source_id", sa.String(length=40), nullable=False),
        sa.Column("source_key", sa.String(length=120), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("journal_entry_id", bigint, nullable=True),
        sa.CheckConstraint("status IN ('OPEN','RESOLVED')", name=op.f("ck_gl_posting_issues_status_valid")),
        sa.ForeignKeyConstraint(["pharmacy_id"], ["pharmacies.id"], name=op.f("fk_gl_posting_issues_pharmacy_id_pharmacies"),
                                ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["journal_entry_id"], ["journal_entries.id"],
                                name=op.f("fk_gl_posting_issues_journal_entry_id_journal_entries"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_gl_posting_issues")),
        sa.UniqueConstraint("pharmacy_id", "source_key", name="uq_gl_posting_issues_key"),
    )
    op.create_index("ix_gl_posting_issues_status", "gl_posting_issues", ["pharmacy_id", "status"], unique=False)
    conn = op.get_bind()
    conn.execute(sa.text(
        "INSERT INTO permissions (code, description, module) SELECT :code, :description, 'accounting' FROM DUAL "
        "WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = :code)"), {"code": PERMISSION[0], "description": PERMISSION[1]})
    conn.execute(sa.text(
        "INSERT INTO role_permissions (role_id, permission_id) "
        "SELECT r.id, p.id FROM roles r JOIN permissions p ON p.code = :code "
        "WHERE r.name = 'Owner / Admin' AND r.is_system_role = 1 "
        "AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)"),
        {"code": PERMISSION[0]})


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DELETE rp FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id WHERE p.code = :c"),
                 {"c": PERMISSION[0]})
    conn.execute(sa.text("DELETE FROM permissions WHERE code = :c"), {"c": PERMISSION[0]})
    op.drop_index("ix_gl_posting_issues_status", table_name="gl_posting_issues")
    op.drop_table("gl_posting_issues")
