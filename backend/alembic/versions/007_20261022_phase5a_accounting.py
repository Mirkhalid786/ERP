"""Phase 5A: double-entry general ledger — chart of accounts, fiscal years and periods,
journal entries and lines, opening balances. Seeds the accounting permissions and grants
them to the Owner / Admin role only. No existing data is changed.

Revision ID: 007
Revises: 006
Create Date: 2026-10-22 00:00:00.000000 UTC
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PERMISSIONS = [
    ("accounting.view", "View chart of accounts, journals and financial reports"),
    ("accounting.accounts_manage", "Set up accounting, manage accounts and fiscal years"),
    ("accounting.journal_create", "Prepare draft manual journals"),
    ("accounting.journal_post", "Post journal entries"),
    ("accounting.journal_reverse", "Reverse posted journal entries"),
    ("accounting.opening_balances", "Prepare and post opening balances"),
    ("accounting.period_close", "Close accounting periods"),
    ("accounting.period_reopen", "Reopen closed accounting periods"),
    ("accounting.export", "Export financial data"),
]
TABLES = ["opening_balance_lines", "opening_balance_batches", "journal_lines", "journal_entries",
          "accounting_periods", "ledger_accounts", "fiscal_years"]


def upgrade() -> None:
    op.create_table('fiscal_years',
    sa.Column('id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), autoincrement=True, nullable=False),
    sa.Column('pharmacy_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('name', sa.String(length=20), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('end_date', sa.Date(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('created_by', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('end_date > start_date', name=op.f('ck_fiscal_years_fy_dates_valid')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_fiscal_years_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['pharmacy_id'], ['pharmacies.id'], name=op.f('fk_fiscal_years_pharmacy_id_pharmacies'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_fiscal_years')),
    sa.UniqueConstraint('pharmacy_id', 'start_date', name='uq_fiscal_years_start')
    )
    op.create_table('ledger_accounts',
    sa.Column('id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), autoincrement=True, nullable=False),
    sa.Column('pharmacy_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('code', sa.String(length=20), nullable=False),
    sa.Column('name', sa.String(length=150), nullable=False),
    sa.Column('account_type', sa.String(length=20), nullable=False),
    sa.Column('normal_balance', sa.String(length=6), nullable=False),
    sa.Column('parent_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('is_group', sa.Boolean(), nullable=False),
    sa.Column('system_key', sa.String(length=40), nullable=True),
    sa.Column('party_type', sa.String(length=10), nullable=True),
    sa.Column('is_control', sa.Boolean(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('description', sa.String(length=500), nullable=True),
    sa.Column('created_by', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("account_type IN ('ASSET','LIABILITY','EQUITY','INCOME','EXPENSE')", name=op.f('ck_ledger_accounts_account_type_valid')),
    sa.CheckConstraint("normal_balance IN ('DEBIT','CREDIT')", name=op.f('ck_ledger_accounts_normal_balance_valid')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_ledger_accounts_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['parent_id'], ['ledger_accounts.id'], name=op.f('fk_ledger_accounts_parent_id_ledger_accounts'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['pharmacy_id'], ['pharmacies.id'], name=op.f('fk_ledger_accounts_pharmacy_id_pharmacies'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ledger_accounts')),
    sa.UniqueConstraint('pharmacy_id', 'code', name='uq_ledger_accounts_code'),
    sa.UniqueConstraint('pharmacy_id', 'system_key', name='uq_ledger_accounts_system_key')
    )
    op.create_table('accounting_periods',
    sa.Column('id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), autoincrement=True, nullable=False),
    sa.Column('pharmacy_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('fiscal_year_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('name', sa.String(length=20), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('end_date', sa.Date(), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('closed_by', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reopened_by', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('reopened_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reopen_reason', sa.String(length=500), nullable=True),
    sa.CheckConstraint('end_date >= start_date', name=op.f('ck_accounting_periods_period_dates_valid')),
    sa.ForeignKeyConstraint(['closed_by'], ['users.id'], name=op.f('fk_accounting_periods_closed_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['fiscal_year_id'], ['fiscal_years.id'], name=op.f('fk_accounting_periods_fiscal_year_id_fiscal_years'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['pharmacy_id'], ['pharmacies.id'], name=op.f('fk_accounting_periods_pharmacy_id_pharmacies'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['reopened_by'], ['users.id'], name=op.f('fk_accounting_periods_reopened_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_accounting_periods')),
    sa.UniqueConstraint('pharmacy_id', 'start_date', name='uq_accounting_periods_start')
    )
    op.create_index('ix_accounting_periods_dates', 'accounting_periods', ['pharmacy_id', 'start_date', 'end_date'], unique=False)
    op.create_table('journal_entries',
    sa.Column('id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), autoincrement=True, nullable=False),
    sa.Column('pharmacy_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('entry_number', sa.String(length=30), nullable=True),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('entry_date', sa.Date(), nullable=False),
    sa.Column('document_date', sa.Date(), nullable=True),
    sa.Column('period_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('source_type', sa.String(length=30), nullable=False),
    sa.Column('source_id', sa.String(length=64), nullable=True),
    sa.Column('source_key', sa.String(length=150), nullable=True),
    sa.Column('request_hash', sa.String(length=64), nullable=True),
    sa.Column('reference', sa.String(length=100), nullable=True),
    sa.Column('narration', sa.String(length=1000), nullable=False),
    sa.Column('total_debit', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('total_credit', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('integrity_hash', sa.String(length=64), nullable=True),
    sa.Column('created_by', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('posted_by', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('posted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reversal_of_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('reversed_by_entry_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('reversed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reversal_reason', sa.String(length=500), nullable=True),
    sa.CheckConstraint("status IN ('DRAFT','POSTED','REVERSED')", name=op.f('ck_journal_entries_status_valid')),
    sa.CheckConstraint('total_debit = total_credit', name=op.f('ck_journal_entries_balanced')),
    sa.CheckConstraint('total_debit >= 0', name=op.f('ck_journal_entries_totals_non_negative')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_journal_entries_created_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['period_id'], ['accounting_periods.id'], name=op.f('fk_journal_entries_period_id_accounting_periods'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['pharmacy_id'], ['pharmacies.id'], name=op.f('fk_journal_entries_pharmacy_id_pharmacies'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['posted_by'], ['users.id'], name=op.f('fk_journal_entries_posted_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['reversal_of_id'], ['journal_entries.id'], name=op.f('fk_journal_entries_reversal_of_id_journal_entries'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['reversed_by_entry_id'], ['journal_entries.id'], name=op.f('fk_journal_entries_reversed_by_entry_id_journal_entries'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_journal_entries')),
    sa.UniqueConstraint('pharmacy_id', 'entry_number', name='uq_journal_entries_number'),
    sa.UniqueConstraint('pharmacy_id', 'source_key', name='uq_journal_entries_source_key')
    )
    op.create_index('ix_journal_entries_date', 'journal_entries', ['pharmacy_id', 'entry_date', 'status'], unique=False)
    op.create_index('ix_journal_entries_source', 'journal_entries', ['pharmacy_id', 'source_type', 'source_id'], unique=False)
    op.create_table('journal_lines',
    sa.Column('id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), autoincrement=True, nullable=False),
    sa.Column('entry_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('line_no', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('debit', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('credit', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('party_type', sa.String(length=10), nullable=True),
    sa.Column('party_id', sa.Integer(), nullable=True),
    sa.Column('description', sa.String(length=500), nullable=True),
    sa.CheckConstraint('(debit = 0 AND credit > 0) OR (credit = 0 AND debit > 0)', name=op.f('ck_journal_lines_one_sided')),
    sa.CheckConstraint('debit >= 0 AND credit >= 0', name=op.f('ck_journal_lines_non_negative')),
    sa.ForeignKeyConstraint(['account_id'], ['ledger_accounts.id'], name=op.f('fk_journal_lines_account_id_ledger_accounts'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['entry_id'], ['journal_entries.id'], name=op.f('fk_journal_lines_entry_id_journal_entries'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_journal_lines')),
    sa.UniqueConstraint('entry_id', 'line_no', name='uq_journal_lines_line_no')
    )
    op.create_index('ix_journal_lines_account', 'journal_lines', ['account_id', 'entry_id'], unique=False)
    op.create_index(op.f('ix_journal_lines_entry_id'), 'journal_lines', ['entry_id'], unique=False)
    op.create_index('ix_journal_lines_party', 'journal_lines', ['party_type', 'party_id'], unique=False)
    op.create_table('opening_balance_batches',
    sa.Column('id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), autoincrement=True, nullable=False),
    sa.Column('pharmacy_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('opening_date', sa.Date(), nullable=False),
    sa.Column('source_description', sa.String(length=500), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('post_difference_to_suspense', sa.Boolean(), nullable=False),
    sa.Column('journal_entry_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('prepared_by', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('posted_by', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('posted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['journal_entry_id'], ['journal_entries.id'], name=op.f('fk_opening_balance_batches_journal_entry_id_journal_entries'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['pharmacy_id'], ['pharmacies.id'], name=op.f('fk_opening_balance_batches_pharmacy_id_pharmacies'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['posted_by'], ['users.id'], name=op.f('fk_opening_balance_batches_posted_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['prepared_by'], ['users.id'], name=op.f('fk_opening_balance_batches_prepared_by_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_opening_balance_batches'))
    )
    op.create_table('opening_balance_lines',
    sa.Column('id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), autoincrement=True, nullable=False),
    sa.Column('batch_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('account_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('debit', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('credit', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('party_type', sa.String(length=10), nullable=True),
    sa.Column('party_id', sa.Integer(), nullable=True),
    sa.Column('document_number', sa.String(length=60), nullable=True),
    sa.Column('document_date', sa.Date(), nullable=True),
    sa.Column('due_date', sa.Date(), nullable=True),
    sa.Column('outstanding_amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('notes', sa.String(length=500), nullable=True),
    sa.CheckConstraint('(debit = 0 AND credit > 0) OR (credit = 0 AND debit > 0)', name=op.f('ck_opening_balance_lines_one_sided')),
    sa.CheckConstraint('debit >= 0 AND credit >= 0', name=op.f('ck_opening_balance_lines_non_negative')),
    sa.ForeignKeyConstraint(['account_id'], ['ledger_accounts.id'], name=op.f('fk_opening_balance_lines_account_id_ledger_accounts'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['batch_id'], ['opening_balance_batches.id'], name=op.f('fk_opening_balance_lines_batch_id_opening_balance_batches'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_opening_balance_lines'))
    )
    op.create_index(op.f('ix_opening_balance_lines_batch_id'), 'opening_balance_lines', ['batch_id'], unique=False)

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
    conn.execute(sa.text("DELETE FROM permissions WHERE code IN :codes").bindparams(sa.bindparam("codes", expanding=True)), {"codes": codes})
    for table in TABLES:
        op.drop_table(table)
