"""Data fix: store ADJUSTMENT_OUT stock movements as signed (negative) deltas

stock_movements.quantity_base is a signed delta (RETURN_OUTWARD already stores
negative values), but stock adjustments previously stored outflows as positive
numbers. This flips existing ADJUSTMENT_OUT rows so the ledger can be summed.

Revision ID: 004
Revises: 003
Create Date: 2026-10-21 00:00:00.000000 UTC
"""
from typing import Sequence, Union
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE stock_movements SET quantity_base = -quantity_base "
        "WHERE movement_type = 'ADJUSTMENT_OUT' AND quantity_base > 0"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE stock_movements SET quantity_base = -quantity_base "
        "WHERE movement_type = 'ADJUSTMENT_OUT' AND quantity_base < 0"
    )
