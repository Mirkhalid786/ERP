"""Supplier bill import: the purchase_bill_imports table (original document, extracted data, review draft and links
to the goods receipt / supplier bill created from it). Adds one empty table; no existing data is changed.

Revision ID: 012
Revises: 011
Create Date: 2026-10-26 00:00:00.000000 UTC
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BIGINT = sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql')
LONG_TEXT = sa.Text().with_variant(mysql.MEDIUMTEXT(), 'mysql')


def upgrade() -> None:
    op.create_table('purchase_bill_imports',
    sa.Column('id', BIGINT, autoincrement=True, nullable=False),
    sa.Column('pharmacy_id', BIGINT, nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('file_name', sa.String(length=255), nullable=False),
    sa.Column('content_type', sa.String(length=50), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('page_count', sa.Integer(), nullable=True),
    sa.Column('data', sa.LargeBinary().with_variant(mysql.MEDIUMBLOB(), 'mysql'), nullable=False),
    sa.Column('extraction_method', sa.String(length=30), nullable=True),
    sa.Column('extraction_json', LONG_TEXT, nullable=True),
    sa.Column('extraction_error', sa.Text(), nullable=True),
    sa.Column('review_json', LONG_TEXT, nullable=True),
    sa.Column('supplier_id', BIGINT, nullable=True),
    sa.Column('invoice_number', sa.String(length=100), nullable=True),
    sa.Column('invoice_date', sa.Date(), nullable=True),
    sa.Column('grand_total', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('grn_id', BIGINT, nullable=True),
    sa.Column('supplier_invoice_id', BIGINT, nullable=True),
    sa.Column('uploaded_by', BIGINT, nullable=True),
    sa.Column('uploaded_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('reviewed_by', BIGINT, nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('confirmed_by', BIGINT, nullable=True),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['pharmacy_id'], ['pharmacies.id'], name=op.f('fk_purchase_bill_imports_pharmacy_id_pharmacies'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id'], name=op.f('fk_purchase_bill_imports_supplier_id_suppliers'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['grn_id'], ['goods_receipts.id'], name=op.f('fk_purchase_bill_imports_grn_id_goods_receipts'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['supplier_invoice_id'], ['supplier_invoices.id'], name=op.f('fk_purchase_bill_imports_supplier_invoice_id_supplier_invoices'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['uploaded_by'], ['users.id'], name=op.f('fk_purchase_bill_imports_uploaded_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], name=op.f('fk_purchase_bill_imports_reviewed_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['confirmed_by'], ['users.id'], name=op.f('fk_purchase_bill_imports_confirmed_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_purchase_bill_imports'))
    )
    op.create_index('ix_purchase_bill_imports_pharmacy_status', 'purchase_bill_imports', ['pharmacy_id', 'status'], unique=False)
    op.create_index('ix_purchase_bill_imports_sha256', 'purchase_bill_imports', ['pharmacy_id', 'sha256'], unique=False)
    op.create_index('ix_purchase_bill_imports_invoice', 'purchase_bill_imports', ['pharmacy_id', 'supplier_id', 'invoice_number'], unique=False)


def downgrade() -> None:
    op.drop_table('purchase_bill_imports')
