"""Indian medicine catalogue fields: add mrp, is_discontinued, source_name, source_id,
pack_size_label columns and search indexes to medicines table.

Revision ID: 013
Revises: 012
Create Date: 2026-10-29 00:00:00.000000 UTC
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("medicines", sa.Column("mrp", sa.Numeric(precision=12, scale=2), nullable=True))
    op.add_column("medicines", sa.Column("is_discontinued", sa.Boolean(), server_default=sa.text("0"), nullable=False))
    op.add_column("medicines", sa.Column("source_name", sa.String(length=100), nullable=True))
    op.add_column("medicines", sa.Column("source_id", sa.String(length=100), nullable=True))
    op.add_column("medicines", sa.Column("pack_size_label", sa.String(length=255), nullable=True))

    op.create_index("ix_medicines_pharmacy_brand", "medicines", ["pharmacy_id", "brand_name"], unique=False)
    op.create_index("ix_medicines_pharmacy_mfr", "medicines", ["pharmacy_id", "manufacturer_id"], unique=False)
    op.create_index("ix_medicines_pharmacy_source", "medicines", ["pharmacy_id", "source_name", "source_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_medicines_pharmacy_source", table_name="medicines")
    op.drop_index("ix_medicines_pharmacy_mfr", table_name="medicines")
    op.drop_index("ix_medicines_pharmacy_brand", table_name="medicines")

    op.drop_column("medicines", "pack_size_label")
    op.drop_column("medicines", "source_id")
    op.drop_column("medicines", "source_name")
    op.drop_column("medicines", "is_discontinued")
    op.drop_column("medicines", "mrp")
