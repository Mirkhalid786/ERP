"""Stage 5C/5D and Phases 6-7: supplier credit notes, payment settlements (clearing -> bank), bank statement lines
for reconciliation, backup history, and the accounting.bank_reconcile / accounting.year_end
permissions (Owner / Admin). Adds tables only; no existing data is changed.

Revision ID: 010
Revises: 009
Create Date: 2026-10-23 00:00:00.000000 UTC
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PERMISSIONS = [
    ("accounting.bank_reconcile", "Record settlements and reconcile bank statements"),
    ("accounting.year_end", "Close and reopen financial years"),
]
TABLES = ["supplier_credit_notes", "bank_statement_lines", "payment_settlements", "backup_history"]


def upgrade() -> None:
    op.create_table('backup_history',
    sa.Column('id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), autoincrement=True, nullable=False),
    sa.Column('pharmacy_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('file_name', sa.String(length=200), nullable=False),
    sa.Column('trigger', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('size_bytes', sa.BigInteger(), nullable=True),
    sa.Column('sha256', sa.String(length=64), nullable=True),
    sa.Column('tables', sa.Integer(), nullable=True),
    sa.Column('total_rows', sa.BigInteger(), nullable=True),
    sa.Column('manifest_json', sa.Text(), nullable=True),
    sa.Column('alembic_revision', sa.String(length=32), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('verify_status', sa.String(length=20), nullable=True),
    sa.Column('verify_detail', sa.Text(), nullable=True),
    sa.CheckConstraint("status IN ('RUNNING','COMPLETED','FAILED')", name=op.f('ck_backup_history_status_valid')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_backup_history_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['pharmacy_id'], ['pharmacies.id'], name=op.f('fk_backup_history_pharmacy_id_pharmacies'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_backup_history')),
    sa.UniqueConstraint('file_name', name='uq_backup_history_file')
    )
    op.create_table('payment_settlements',
    sa.Column('id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), autoincrement=True, nullable=False),
    sa.Column('pharmacy_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('settlement_number', sa.String(length=30), nullable=False),
    sa.Column('idempotency_key', sa.String(length=64), nullable=True),
    sa.Column('clearing_key', sa.String(length=30), nullable=False),
    sa.Column('bank_account_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('settlement_date', sa.Date(), nullable=False),
    sa.Column('gross_amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('fee_amount', sa.Numeric(precision=14, scale=2), server_default='0.00', nullable=False),
    sa.Column('fee_gst_amount', sa.Numeric(precision=14, scale=2), server_default='0.00', nullable=False),
    sa.Column('net_amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('reference', sa.String(length=100), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_by', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('reversed_by', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('reversed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reversal_reason', sa.Text(), nullable=True),
    sa.CheckConstraint("status IN ('POSTED','REVERSED')", name=op.f('ck_payment_settlements_status_valid')),
    sa.CheckConstraint('gross_amount > 0 AND fee_amount >= 0 AND fee_gst_amount >= 0', name=op.f('ck_payment_settlements_amounts_valid')),
    sa.CheckConstraint('net_amount = gross_amount - fee_amount - fee_gst_amount AND net_amount >= 0', name=op.f('ck_payment_settlements_net_valid')),
    sa.ForeignKeyConstraint(['bank_account_id'], ['ledger_accounts.id'], name=op.f('fk_payment_settlements_bank_account_id_ledger_accounts'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_payment_settlements_created_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['pharmacy_id'], ['pharmacies.id'], name=op.f('fk_payment_settlements_pharmacy_id_pharmacies'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['reversed_by'], ['users.id'], name=op.f('fk_payment_settlements_reversed_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_payment_settlements')),
    sa.UniqueConstraint('pharmacy_id', 'idempotency_key', name='uq_payment_settlements_idem'),
    sa.UniqueConstraint('pharmacy_id', 'settlement_number', name='uq_payment_settlements_number')
    )
    op.create_table('bank_statement_lines',
    sa.Column('id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), autoincrement=True, nullable=False),
    sa.Column('pharmacy_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('account_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('import_batch', sa.String(length=40), nullable=False),
    sa.Column('line_hash', sa.String(length=64), nullable=False),
    sa.Column('statement_date', sa.Date(), nullable=False),
    sa.Column('description', sa.String(length=300), nullable=False),
    sa.Column('reference', sa.String(length=100), nullable=True),
    sa.Column('amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('matched_line_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('matched_by', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('matched_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status IN ('UNMATCHED','MATCHED')", name=op.f('ck_bank_statement_lines_status_valid')),
    sa.CheckConstraint('amount <> 0', name=op.f('ck_bank_statement_lines_amount_nonzero')),
    sa.ForeignKeyConstraint(['account_id'], ['ledger_accounts.id'], name=op.f('fk_bank_statement_lines_account_id_ledger_accounts'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_bank_statement_lines_created_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['matched_by'], ['users.id'], name=op.f('fk_bank_statement_lines_matched_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['matched_line_id'], ['journal_lines.id'], name=op.f('fk_bank_statement_lines_matched_line_id_journal_lines'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['pharmacy_id'], ['pharmacies.id'], name=op.f('fk_bank_statement_lines_pharmacy_id_pharmacies'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_bank_statement_lines')),
    sa.UniqueConstraint('matched_line_id', name='uq_bank_statement_lines_match'),
    sa.UniqueConstraint('pharmacy_id', 'account_id', 'line_hash', name='uq_bank_statement_lines_hash')
    )
    op.create_index('ix_bank_statement_lines_account', 'bank_statement_lines', ['pharmacy_id', 'account_id', 'status'], unique=False)
    op.create_table('supplier_credit_notes',
    sa.Column('id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), autoincrement=True, nullable=False),
    sa.Column('pharmacy_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('supplier_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('credit_note_number', sa.String(length=100), nullable=False),
    sa.Column('number_key', sa.String(length=120), nullable=True),
    sa.Column('idempotency_key', sa.String(length=64), nullable=True),
    sa.Column('note_date', sa.Date(), nullable=False),
    sa.Column('invoice_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('reason_code', sa.String(length=30), nullable=False),
    sa.Column('taxable_amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('cgst_amount', sa.Numeric(precision=12, scale=2), server_default='0.00', nullable=False),
    sa.Column('sgst_amount', sa.Numeric(precision=12, scale=2), server_default='0.00', nullable=False),
    sa.Column('igst_amount', sa.Numeric(precision=12, scale=2), server_default='0.00', nullable=False),
    sa.Column('total_amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_by', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('cancelled_by', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancel_reason', sa.Text(), nullable=True),
    sa.CheckConstraint("status IN ('POSTED','CANCELLED')", name=op.f('ck_supplier_credit_notes_status_valid')),
    sa.CheckConstraint('taxable_amount >= 0 AND cgst_amount >= 0 AND sgst_amount >= 0 AND igst_amount >= 0', name=op.f('ck_supplier_credit_notes_amounts_valid')),
    sa.CheckConstraint('total_amount > 0 AND total_amount = taxable_amount + cgst_amount + sgst_amount + igst_amount', name=op.f('ck_supplier_credit_notes_total_valid')),
    sa.ForeignKeyConstraint(['cancelled_by'], ['users.id'], name=op.f('fk_supplier_credit_notes_cancelled_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_supplier_credit_notes_created_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['invoice_id'], ['supplier_invoices.id'], name=op.f('fk_supplier_credit_notes_invoice_id_supplier_invoices'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['pharmacy_id'], ['pharmacies.id'], name=op.f('fk_supplier_credit_notes_pharmacy_id_pharmacies'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id'], name=op.f('fk_supplier_credit_notes_supplier_id_suppliers'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_supplier_credit_notes')),
    sa.UniqueConstraint('pharmacy_id', 'idempotency_key', name='uq_supplier_credit_notes_idem'),
    sa.UniqueConstraint('pharmacy_id', 'supplier_id', 'number_key', name='uq_supplier_credit_notes_number')
    )
    op.create_index(op.f('ix_supplier_credit_notes_invoice_id'), 'supplier_credit_notes', ['invoice_id'], unique=False)
    op.create_index(op.f('ix_supplier_credit_notes_supplier_id'), 'supplier_credit_notes', ['supplier_id'], unique=False)
    conn = op.get_bind()
    for code, description in PERMISSIONS:
        conn.execute(sa.text(
            "INSERT INTO permissions (code, description, module) SELECT :code, :description, 'accounting' FROM DUAL "
            "WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = :code)"), {"code": code, "description": description})
        conn.execute(sa.text(
            "INSERT INTO role_permissions (role_id, permission_id) "
            "SELECT r.id, p.id FROM roles r JOIN permissions p ON p.code = :code "
            "WHERE r.name = 'Owner / Admin' AND r.is_system_role = 1 "
            "AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)"),
            {"code": code})


def downgrade() -> None:
    conn = op.get_bind()
    codes = [c for c, _ in PERMISSIONS]
    conn.execute(sa.text("DELETE rp FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id WHERE p.code IN :codes")
                 .bindparams(sa.bindparam("codes", expanding=True)), {"codes": codes})
    conn.execute(sa.text("DELETE FROM permissions WHERE code IN :codes").bindparams(sa.bindparam("codes", expanding=True)),
                 {"codes": codes})
    for table in TABLES:
        op.drop_table(table)
