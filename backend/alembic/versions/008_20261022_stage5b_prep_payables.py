"""Stage 5B preparation: supplier invoice workflow, supplier payments, expenses and
categorised cash-shift movements. Operational records only; nothing is posted to the GL.

* supplier_invoices: finalise / cancel audit columns, `replaces_invoice_id`, and a
  financial-year-scoped duplicate key `number_key` replacing the old unique
  (pharmacy, supplier, invoice_number) index. Existing rows get their key backfilled.
* supplier_payments / supplier_payment_allocations (new). Every existing non-zero
  `supplier_invoices.paid_amount` is preserved as a LEGACY payment allocated to its
  invoice (is_legacy = 1, method LEGACY) so no historical value is discarded; the
  invoice columns themselves are not changed.
* expense_categories / expenses (new); default categories seeded per pharmacy.
* cash_movements: category (existing rows: UNCATEGORISED), reference, authorised_by
  (existing rows: the user who recorded them, who needed 'shifts.review').
* Permissions for the new workflows, granted to the default roles.

Revision ID: 008
Revises: 007
Create Date: 2026-10-22 00:00:00.000000 UTC
"""
import re
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PERMISSIONS = [
    ("purchases.invoice_finalize", "Finalise supplier invoices (amount becomes payable)", "purchases"),
    ("purchases.invoice_cancel", "Cancel finalised supplier invoices", "purchases"),
    ("supplier_payments.view", "View supplier payments and balances", "payables"),
    ("supplier_payments.create", "Record and allocate supplier payments", "payables"),
    ("supplier_payments.reverse", "Reverse supplier payments", "payables"),
    ("expenses.view", "View expenses", "expenses"),
    ("expenses.create", "Record expenses (pending approval unless also an approver)", "expenses"),
    ("expenses.approve", "Approve or reject expenses; pay expenses from the drawer", "expenses"),
    ("expenses.reverse", "Reverse approved expenses", "expenses"),
    ("expenses.configure", "Manage expense categories", "expenses"),
]
ROLE_GRANTS = {
    "Owner / Admin": [code for code, _, _ in PERMISSIONS],
    "Pharmacist": ["expenses.view", "expenses.create"],
    "Cashier": ["expenses.create"],
    "Inventory Manager": ["purchases.invoice_finalize", "supplier_payments.view", "expenses.view", "expenses.create"],
}
DEFAULT_CATEGORIES = [
    ("RENT", "Rent", "5210"), ("SALARY", "Salaries & wages", "5220"), ("ELECTRICITY", "Electricity & water", "5230"),
    ("PHONE_INTERNET", "Telephone & internet", "5240"), ("TRANSPORT", "Courier, transport & delivery", "5250"),
    ("REPAIRS", "Repairs & maintenance", "5260"), ("STATIONERY", "Stationery & office supplies", "5270"),
    ("BANK_CHARGES", "Bank charges", "5280"), ("STAFF_WELFARE", "Staff tea, snacks & welfare", "5290"),
    ("CLEANING", "Cleaning & housekeeping", "5290"), ("LICENCE_FEES", "Licence & registration fees", "5290"),
    ("PROFESSIONAL_FEES", "Accountant & professional fees", "5290"), ("MISC", "Miscellaneous", "5290"),
]
NEW_TABLES = ["supplier_payment_allocations", "supplier_payments", "expenses", "expense_categories"]


def _number_key(number: str, invoice_date) -> str:
    cleaned = re.sub(r"[^A-Z0-9]", "", (number or "").upper()) or f"BLANK{id(number)}"
    fy = invoice_date.year if invoice_date.month >= 4 else invoice_date.year - 1
    return f"{fy}:{cleaned}"


def upgrade() -> None:
    conn = op.get_bind()

    op.create_table('expense_categories',
    sa.Column('id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), autoincrement=True, nullable=False),
    sa.Column('pharmacy_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('code', sa.String(length=30), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('description', sa.String(length=255), nullable=True),
    sa.Column('account_code_hint', sa.String(length=20), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default=sa.true(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['pharmacy_id'], ['pharmacies.id'], name=op.f('fk_expense_categories_pharmacy_id_pharmacies'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_expense_categories')),
    sa.UniqueConstraint('pharmacy_id', 'code', name='uq_expense_categories_code')
    )
    op.create_table('expenses',
    sa.Column('id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), autoincrement=True, nullable=False),
    sa.Column('pharmacy_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('expense_number', sa.String(length=30), nullable=False),
    sa.Column('idempotency_key', sa.String(length=64), nullable=True),
    sa.Column('request_hash', sa.String(length=64), nullable=True),
    sa.Column('category_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('expense_date', sa.Date(), nullable=False),
    sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('gst_amount', sa.Numeric(precision=12, scale=2), server_default='0.00', nullable=False),
    sa.Column('payee', sa.String(length=200), nullable=False),
    sa.Column('payee_gstin', sa.String(length=15), nullable=True),
    sa.Column('supplier_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('bill_number', sa.String(length=100), nullable=True),
    sa.Column('payment_method', sa.String(length=20), nullable=False),
    sa.Column('cash_source', sa.String(length=20), nullable=True),
    sa.Column('shift_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('reference', sa.String(length=100), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_by', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('approved_by', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decision_note', sa.Text(), nullable=True),
    sa.Column('reversed_by', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('reversed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reversal_reason', sa.Text(), nullable=True),
    sa.CheckConstraint("status IN ('PENDING_APPROVAL','APPROVED','REJECTED','REVERSED')", name=op.f('ck_expenses_status_valid')),
    sa.CheckConstraint('amount > 0', name=op.f('ck_expenses_amount_positive')),
    sa.CheckConstraint('gst_amount >= 0 AND gst_amount <= amount', name=op.f('ck_expenses_gst_within_amount')),
    sa.ForeignKeyConstraint(['approved_by'], ['users.id'], name=op.f('fk_expenses_approved_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['category_id'], ['expense_categories.id'], name=op.f('fk_expenses_category_id_expense_categories'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_expenses_created_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['pharmacy_id'], ['pharmacies.id'], name=op.f('fk_expenses_pharmacy_id_pharmacies'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['reversed_by'], ['users.id'], name=op.f('fk_expenses_reversed_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['shift_id'], ['cash_shifts.id'], name=op.f('fk_expenses_shift_id_cash_shifts'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id'], name=op.f('fk_expenses_supplier_id_suppliers'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_expenses')),
    sa.UniqueConstraint('pharmacy_id', 'expense_number', name='uq_expenses_number'),
    sa.UniqueConstraint('pharmacy_id', 'idempotency_key', name='uq_expenses_idem')
    )
    op.create_index(op.f('ix_expenses_category_id'), 'expenses', ['category_id'], unique=False)
    op.create_index('ix_expenses_pharmacy_date', 'expenses', ['pharmacy_id', 'expense_date'], unique=False)
    op.create_index(op.f('ix_expenses_shift_id'), 'expenses', ['shift_id'], unique=False)
    op.create_table('supplier_payments',
    sa.Column('id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), autoincrement=True, nullable=False),
    sa.Column('pharmacy_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('payment_number', sa.String(length=30), nullable=False),
    sa.Column('idempotency_key', sa.String(length=64), nullable=True),
    sa.Column('request_hash', sa.String(length=64), nullable=True),
    sa.Column('supplier_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('payment_date', sa.Date(), nullable=False),
    sa.Column('method', sa.String(length=20), nullable=False),
    sa.Column('cash_source', sa.String(length=20), nullable=True),
    sa.Column('shift_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('reference', sa.String(length=100), nullable=True),
    sa.Column('reference_key', sa.String(length=130), nullable=True),
    sa.Column('bank_account', sa.String(length=100), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('is_legacy', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('created_by', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('reversed_by', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('reversed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reversal_reason', sa.Text(), nullable=True),
    sa.CheckConstraint("status IN ('POSTED','REVERSED')", name=op.f('ck_supplier_payments_status_valid')),
    sa.CheckConstraint('amount > 0', name=op.f('ck_supplier_payments_amount_positive')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_supplier_payments_created_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['pharmacy_id'], ['pharmacies.id'], name=op.f('fk_supplier_payments_pharmacy_id_pharmacies'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['reversed_by'], ['users.id'], name=op.f('fk_supplier_payments_reversed_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['shift_id'], ['cash_shifts.id'], name=op.f('fk_supplier_payments_shift_id_cash_shifts'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id'], name=op.f('fk_supplier_payments_supplier_id_suppliers'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_supplier_payments')),
    sa.UniqueConstraint('pharmacy_id', 'idempotency_key', name='uq_supplier_payments_idem'),
    sa.UniqueConstraint('pharmacy_id', 'payment_number', name='uq_supplier_payments_number'),
    sa.UniqueConstraint('pharmacy_id', 'supplier_id', 'reference_key', name='uq_supplier_payments_reference')
    )
    op.create_index(op.f('ix_supplier_payments_shift_id'), 'supplier_payments', ['shift_id'], unique=False)
    op.create_index('ix_supplier_payments_supplier_date', 'supplier_payments', ['pharmacy_id', 'supplier_id', 'payment_date'], unique=False)
    op.create_index(op.f('ix_supplier_payments_supplier_id'), 'supplier_payments', ['supplier_id'], unique=False)
    op.create_table('supplier_payment_allocations',
    sa.Column('id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), autoincrement=True, nullable=False),
    sa.Column('payment_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('invoice_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_by', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('reversed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('ACTIVE','REVERSED')", name=op.f('ck_supplier_payment_allocations_status_valid')),
    sa.CheckConstraint('amount > 0', name=op.f('ck_supplier_payment_allocations_amount_positive')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_supplier_payment_allocations_created_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['invoice_id'], ['supplier_invoices.id'], name=op.f('fk_supplier_payment_allocations_invoice_id_supplier_invoices'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['payment_id'], ['supplier_payments.id'], name=op.f('fk_supplier_payment_allocations_payment_id_supplier_payments'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_supplier_payment_allocations'))
    )
    op.create_index(op.f('ix_supplier_payment_allocations_invoice_id'), 'supplier_payment_allocations', ['invoice_id'], unique=False)
    op.create_index(op.f('ix_supplier_payment_allocations_payment_id'), 'supplier_payment_allocations', ['payment_id'], unique=False)

    # ── supplier_invoices ──────────────────────────────────────────────────────
    bigint = sa.Integer().with_variant(mysql.BIGINT(unsigned=True), "mysql")
    op.add_column("supplier_invoices", sa.Column("number_key", sa.String(length=120), nullable=True))
    op.add_column("supplier_invoices", sa.Column("discrepancy_accepted", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("supplier_invoices", sa.Column("finalized_by", bigint, nullable=True))
    op.add_column("supplier_invoices", sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("supplier_invoices", sa.Column("cancelled_by", bigint, nullable=True))
    op.add_column("supplier_invoices", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("supplier_invoices", sa.Column("cancel_reason", sa.Text(), nullable=True))
    op.add_column("supplier_invoices", sa.Column("replaces_invoice_id", bigint, nullable=True))
    op.create_foreign_key(op.f("fk_supplier_invoices_finalized_by_users"), "supplier_invoices", "users",
                          ["finalized_by"], ["id"], ondelete="SET NULL")
    op.create_foreign_key(op.f("fk_supplier_invoices_cancelled_by_users"), "supplier_invoices", "users",
                          ["cancelled_by"], ["id"], ondelete="SET NULL")
    op.create_foreign_key(op.f("fk_supplier_invoices_replaces_invoice_id_supplier_invoices"), "supplier_invoices",
                          "supplier_invoices", ["replaces_invoice_id"], ["id"], ondelete="SET NULL")
    # Backfill the duplicate key (cancelled bills keep NULL). Two existing rows that would now
    # clash keep their original numbers; the later one gets a "#<id>" suffix and is listed below.
    seen = set()
    for row in conn.execute(sa.text(
            "SELECT id, pharmacy_id, supplier_id, invoice_number, invoice_date FROM supplier_invoices "
            "WHERE status <> 'CANCELLED' ORDER BY id")).all():
        key = _number_key(row.invoice_number, row.invoice_date)
        if (row.pharmacy_id, row.supplier_id, key) in seen:
            print(f"008: supplier invoice {row.id} ({row.invoice_number}) duplicates an earlier bill; key suffixed")
            key = f"{key}#{row.id}"
        seen.add((row.pharmacy_id, row.supplier_id, key))
        conn.execute(sa.text("UPDATE supplier_invoices SET number_key = :k WHERE id = :i"), {"k": key, "i": row.id})
    op.create_index("ix_supplier_invoices_sup_num", "supplier_invoices", ["pharmacy_id", "supplier_id", "invoice_number"], unique=False)
    op.create_unique_constraint("uq_supplier_invoices_number_key", "supplier_invoices", ["pharmacy_id", "supplier_id", "number_key"])
    op.drop_index("uq_supplier_invoices_pharmacy_sup_num", table_name="supplier_invoices")

    # Preserve every recorded paid_amount as a legacy payment + allocation (nothing is discarded)
    for row in conn.execute(sa.text(
            "SELECT id, pharmacy_id, supplier_id, invoice_number, paid_amount, updated_at, created_by "
            "FROM supplier_invoices WHERE paid_amount > 0 ORDER BY id")).all():
        conn.execute(sa.text(
            "INSERT INTO supplier_payments (pharmacy_id, payment_number, supplier_id, payment_date, method, amount, notes, "
            "status, is_legacy, created_by, created_at) VALUES (:p, :n, :s, :d, 'LEGACY', :a, :notes, 'POSTED', 1, :u, :t)"),
            {"p": row.pharmacy_id, "n": f"LEGACY/{row.id}", "s": row.supplier_id, "d": row.updated_at.date(),
             "a": row.paid_amount, "u": row.created_by, "t": row.updated_at,
             "notes": f"Migrated from the paid amount recorded on bill {row.invoice_number} before payment records "
                      "existed; actual date, method and reference were not captured - verify with the supplier"})
        payment_id = conn.execute(sa.text("SELECT id FROM supplier_payments WHERE pharmacy_id = :p AND payment_number = :n"),
                                  {"p": row.pharmacy_id, "n": f"LEGACY/{row.id}"}).scalar_one()
        conn.execute(sa.text(
            "INSERT INTO supplier_payment_allocations (payment_id, invoice_id, amount, status, created_by, created_at) "
            "VALUES (:pay, :inv, :a, 'ACTIVE', :u, :t)"),
            {"pay": payment_id, "inv": row.id, "a": row.paid_amount, "u": row.created_by, "t": row.updated_at})

    # ── cash_movements ─────────────────────────────────────────────────────────
    op.add_column("cash_movements", sa.Column("category", sa.String(length=30), server_default="UNCATEGORISED", nullable=False))
    op.add_column("cash_movements", sa.Column("reference", sa.String(length=100), nullable=True))
    op.add_column("cash_movements", sa.Column("authorised_by", bigint, nullable=True))
    op.create_foreign_key(op.f("fk_cash_movements_authorised_by_users"), "cash_movements", "users",
                          ["authorised_by"], ["id"], ondelete="SET NULL")
    conn.execute(sa.text("UPDATE cash_movements SET authorised_by = created_by WHERE authorised_by IS NULL"))

    # ── Seeds ──────────────────────────────────────────────────────────────────
    for pharmacy_id in conn.execute(sa.text("SELECT id FROM pharmacies")).scalars().all():
        for code, name, hint in DEFAULT_CATEGORIES:
            conn.execute(sa.text(
                "INSERT INTO expense_categories (pharmacy_id, code, name, account_code_hint, is_active, created_at) "
                "SELECT :p, :c, :n, :h, 1, UTC_TIMESTAMP() FROM DUAL WHERE NOT EXISTS "
                "(SELECT 1 FROM expense_categories WHERE pharmacy_id = :p AND code = :c)"),
                {"p": pharmacy_id, "c": code, "n": name, "h": hint})
    for code, description, module in PERMISSIONS:
        conn.execute(sa.text(
            "INSERT INTO permissions (code, description, module) SELECT :code, :description, :module FROM DUAL "
            "WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = :code)"),
            {"code": code, "description": description, "module": module})
    for role, codes in ROLE_GRANTS.items():
        for code in codes:
            conn.execute(sa.text(
                "INSERT INTO role_permissions (role_id, permission_id) "
                "SELECT r.id, p.id FROM roles r JOIN permissions p ON p.code = :code "
                "WHERE r.name = :role AND r.is_system_role = 1 "
                "AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)"),
                {"code": code, "role": role})


def downgrade() -> None:
    """Drops the new tables (legacy payments are only a copy: supplier_invoices.paid_amount is untouched)."""
    conn = op.get_bind()
    codes = [c for c, _, _ in PERMISSIONS]
    conn.execute(sa.text("DELETE rp FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id WHERE p.code IN :codes")
                 .bindparams(sa.bindparam("codes", expanding=True)), {"codes": codes})
    conn.execute(sa.text("DELETE FROM permissions WHERE code IN :codes").bindparams(sa.bindparam("codes", expanding=True)),
                 {"codes": codes})
    op.drop_constraint(op.f("fk_cash_movements_authorised_by_users"), "cash_movements", type_="foreignkey")
    op.drop_column("cash_movements", "authorised_by")
    op.drop_column("cash_movements", "reference")
    op.drop_column("cash_movements", "category")
    # Fails (deliberately) if a cancelled bill was re-entered under the same number
    op.create_index("uq_supplier_invoices_pharmacy_sup_num", "supplier_invoices", ["pharmacy_id", "supplier_id", "invoice_number"], unique=True)
    op.drop_constraint("uq_supplier_invoices_number_key", "supplier_invoices", type_="unique")
    op.drop_index("ix_supplier_invoices_sup_num", table_name="supplier_invoices")
    for fk in ("fk_supplier_invoices_replaces_invoice_id_supplier_invoices", "fk_supplier_invoices_cancelled_by_users",
               "fk_supplier_invoices_finalized_by_users"):
        op.drop_constraint(op.f(fk), "supplier_invoices", type_="foreignkey")
    for col in ("replaces_invoice_id", "cancel_reason", "cancelled_at", "cancelled_by", "finalized_at", "finalized_by",
                "discrepancy_accepted", "number_key"):
        op.drop_column("supplier_invoices", col)
    for table in NEW_TABLES:
        op.drop_table(table)
