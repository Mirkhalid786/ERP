"""Document number sequences; stock ledger total_cost at inventory cost

1. Adds document_sequences: a row-locked counter per (pharmacy, document type,
   period) so concurrent saves never generate the same PO/GRN/return/adjustment number.
2. Data fix: GOODS_RECEIPT and RETURN_OUTWARD movements stored the invoice line
   total (incl. GST and rejected units) or refund value in total_cost. Every other
   movement stores quantity x unit cost, so recompute these rows the same way.

Revision ID: 005
Revises: 004
Create Date: 2026-10-21 00:00:00.000000 UTC
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_sequences",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("pharmacy_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("doc_type", sa.String(30), nullable=False),
        sa.Column("period_key", sa.String(50), nullable=False),
        sa.Column("last_value", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_document_sequences"),
        sa.ForeignKeyConstraint(
            ["pharmacy_id"], ["pharmacies.id"],
            name="fk_document_sequences_pharmacy_id_pharmacies",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("pharmacy_id", "doc_type", "period_key", name="uq_document_sequences_key"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )

    op.execute(
        "UPDATE stock_movements SET total_cost = ROUND(ABS(quantity_base) * unit_cost, 2) "
        "WHERE movement_type IN ('GOODS_RECEIPT', 'RETURN_OUTWARD')"
    )


def downgrade() -> None:
    # The previous total_cost values (line totals / refund values) are not restored;
    # they remain available on the goods receipt and return documents.
    op.drop_table("document_sequences")
