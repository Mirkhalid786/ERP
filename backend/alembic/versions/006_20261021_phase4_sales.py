"""Phase 4: POS, sales invoices, payments, customer credit ledger, returns, cash shifts, messaging outbox

Also:
* medicine_batches gains price_unit_id / price_conversion_factor (the pack size its
  MRP and rates are quoted in) and stock_status (ACTIVE / QUARANTINED / RECALLED / REJECTED).
  Existing batches are backfilled from their latest goods-receipt line, else the base unit.
* Seeds the Phase 4 permission codes and grants them to the default roles.

Revision ID: 006
Revises: 005
Create Date: 2026-10-21 00:00:00.000000 UTC
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PHASE4_PERMISSIONS = [
    ("pos.access", "Open the POS and look up saleable stock", "sales"),
    ("sales.discount", "Give discounts on sales", "sales"),
    ("sales.prescription", "Finalise sales of prescription medicines", "sales"),
    ("prescriptions.view", "View prescription and patient details", "sales"),
    ("sales.credit", "Make credit / part-paid sales", "credit"),
    ("sales.credit_override", "Exceed a customer's credit limit", "credit"),
    ("payments.receive", "Record and allocate customer payments", "credit"),
    ("customers.view", "View customers and their ledgers", "customers"),
    ("customers.adjust", "Post ledger adjustments and write-offs", "credit"),
    ("messaging.send", "Send invoices and reminders by WhatsApp", "messaging"),
    ("messaging.configure", "Configure WhatsApp templates and reminders", "messaging"),
    ("reports.export", "Export reports", "reports"),
    ("shifts.operate", "Open and close own cash shift", "shifts"),
    ("shifts.review", "Review cash shifts and approve paid-outs", "shifts"),
]
ROLE_GRANTS = {
    "Owner / Admin": [p[0] for p in PHASE4_PERMISSIONS],
    "Pharmacist": ["pos.access", "sales.discount", "sales.prescription", "prescriptions.view", "sales.credit",
                   "payments.receive", "customers.view", "messaging.send", "shifts.operate"],
    "Cashier": ["pos.access", "payments.receive", "customers.view", "messaging.send", "shifts.operate"],
}
NEW_TABLES = [  # reverse dependency order for downgrade
    "customer_ledger_entries", "sales_return_items", "customer_refunds", "sales_returns",
    "sale_prescriptions", "sales_invoice_items", "payment_allocations", "message_outbox",
    "sales_invoices", "customer_payments", "cash_movements", "customers", "cash_shifts",
]


def upgrade() -> None:
    op.create_table('cash_shifts',
    sa.Column('id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), autoincrement=True, nullable=False),
    sa.Column('pharmacy_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('user_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('opened_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('opening_cash', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('closed_by', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('expected_cash', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('counted_cash', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('variance', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('variance_note', sa.Text(), nullable=True),
    sa.Column('needs_review', sa.Boolean(), nullable=False),
    sa.Column('reviewed_by', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('review_note', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['closed_by'], ['users.id'], name=op.f('fk_cash_shifts_closed_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['pharmacy_id'], ['pharmacies.id'], name=op.f('fk_cash_shifts_pharmacy_id_pharmacies'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], name=op.f('fk_cash_shifts_reviewed_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_cash_shifts_user_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_cash_shifts'))
    )
    op.create_index('ix_cash_shifts_user_status', 'cash_shifts', ['pharmacy_id', 'user_id', 'status'], unique=False)
    op.create_table('customers',
    sa.Column('id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), autoincrement=True, nullable=False),
    sa.Column('pharmacy_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('mobile', sa.String(length=20), nullable=True),
    sa.Column('email', sa.String(length=255), nullable=True),
    sa.Column('address', sa.Text(), nullable=True),
    sa.Column('gstin', sa.String(length=15), nullable=True),
    sa.Column('state_code', sa.String(length=2), nullable=True),
    sa.Column('business_name', sa.String(length=255), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('credit_limit', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('payment_terms_days', sa.Integer(), nullable=False),
    sa.Column('whatsapp_consent', sa.Boolean(), nullable=False),
    sa.Column('whatsapp_consent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reminders_opt_out', sa.Boolean(), nullable=False),
    sa.Column('preferred_language', sa.String(length=10), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_customers_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['pharmacy_id'], ['pharmacies.id'], name=op.f('fk_customers_pharmacy_id_pharmacies'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_customers')),
    sa.UniqueConstraint('pharmacy_id', 'mobile', name='uq_customers_pharmacy_mobile')
    )
    op.create_index('ix_customers_pharmacy_name', 'customers', ['pharmacy_id', 'name'], unique=False)
    op.create_table('cash_movements',
    sa.Column('id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), autoincrement=True, nullable=False),
    sa.Column('pharmacy_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('shift_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('movement_type', sa.String(length=20), nullable=False),
    sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('reason', sa.String(length=500), nullable=False),
    sa.Column('created_by', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_cash_movements_created_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['pharmacy_id'], ['pharmacies.id'], name=op.f('fk_cash_movements_pharmacy_id_pharmacies'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['shift_id'], ['cash_shifts.id'], name=op.f('fk_cash_movements_shift_id_cash_shifts'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_cash_movements'))
    )
    op.create_index(op.f('ix_cash_movements_shift_id'), 'cash_movements', ['shift_id'], unique=False)
    op.create_table('customer_payments',
    sa.Column('id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), autoincrement=True, nullable=False),
    sa.Column('pharmacy_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('receipt_number', sa.String(length=30), nullable=False),
    sa.Column('idempotency_key', sa.String(length=64), nullable=True),
    sa.Column('customer_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('method', sa.String(length=20), nullable=False),
    sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('amount_tendered', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('change_returned', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('refunded_amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('reference', sa.String(length=100), nullable=True),
    sa.Column('verification_status', sa.String(length=20), nullable=False),
    sa.Column('verified_by', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('notes', sa.String(length=500), nullable=True),
    sa.Column('shift_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('received_by', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], name=op.f('fk_customer_payments_customer_id_customers'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['pharmacy_id'], ['pharmacies.id'], name=op.f('fk_customer_payments_pharmacy_id_pharmacies'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['received_by'], ['users.id'], name=op.f('fk_customer_payments_received_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['shift_id'], ['cash_shifts.id'], name=op.f('fk_customer_payments_shift_id_cash_shifts'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['verified_by'], ['users.id'], name=op.f('fk_customer_payments_verified_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_customer_payments')),
    sa.UniqueConstraint('pharmacy_id', 'idempotency_key', name='uq_customer_payments_idempotency'),
    sa.UniqueConstraint('pharmacy_id', 'receipt_number', name='uq_customer_payments_receipt')
    )
    op.create_index(op.f('ix_customer_payments_customer_id'), 'customer_payments', ['customer_id'], unique=False)
    op.create_index('ix_customer_payments_date', 'customer_payments', ['pharmacy_id', 'received_at'], unique=False)
    op.create_index(op.f('ix_customer_payments_shift_id'), 'customer_payments', ['shift_id'], unique=False)
    op.create_table('sales_invoices',
    sa.Column('id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), autoincrement=True, nullable=False),
    sa.Column('pharmacy_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('invoice_number', sa.String(length=30), nullable=False),
    sa.Column('idempotency_key', sa.String(length=64), nullable=False),
    sa.Column('request_hash', sa.String(length=64), nullable=False),
    sa.Column('invoice_date', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('customer_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('customer_name', sa.String(length=200), nullable=False),
    sa.Column('customer_mobile', sa.String(length=20), nullable=True),
    sa.Column('customer_gstin', sa.String(length=15), nullable=True),
    sa.Column('is_inter_state', sa.Boolean(), nullable=False),
    sa.Column('cashier_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('shift_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('gross_amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('discount_total', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('taxable_total', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('cgst_total', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('sgst_total', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('igst_total', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('tax_total', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('round_off', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('grand_total', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('cost_total', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('amount_paid', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('paid_at_sale', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('return_credit_applied', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('returned_value', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('refunded_amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('written_off_amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('balance_due', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('payment_status', sa.String(length=20), nullable=False),
    sa.Column('return_status', sa.String(length=20), nullable=False),
    sa.Column('is_credit_sale', sa.Boolean(), nullable=False),
    sa.Column('due_date', sa.Date(), nullable=True),
    sa.Column('credit_override_by', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('credit_override_reason', sa.String(length=500), nullable=True),
    sa.Column('prescription_required', sa.Boolean(), nullable=False),
    sa.Column('reminders_paused', sa.Boolean(), nullable=False),
    sa.Column('is_disputed', sa.Boolean(), nullable=False),
    sa.Column('dispute_note', sa.String(length=500), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('print_count', sa.Integer(), nullable=False),
    sa.Column('voided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('voided_by', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('void_reason', sa.String(length=500), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['cashier_id'], ['users.id'], name=op.f('fk_sales_invoices_cashier_id_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['credit_override_by'], ['users.id'], name=op.f('fk_sales_invoices_credit_override_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], name=op.f('fk_sales_invoices_customer_id_customers'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['pharmacy_id'], ['pharmacies.id'], name=op.f('fk_sales_invoices_pharmacy_id_pharmacies'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['shift_id'], ['cash_shifts.id'], name=op.f('fk_sales_invoices_shift_id_cash_shifts'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['voided_by'], ['users.id'], name=op.f('fk_sales_invoices_voided_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sales_invoices')),
    sa.UniqueConstraint('pharmacy_id', 'idempotency_key', name='uq_sales_invoices_idempotency'),
    sa.UniqueConstraint('pharmacy_id', 'invoice_number', name='uq_sales_invoices_number')
    )
    op.create_index(op.f('ix_sales_invoices_customer_id'), 'sales_invoices', ['customer_id'], unique=False)
    op.create_index('ix_sales_invoices_date', 'sales_invoices', ['pharmacy_id', 'invoice_date'], unique=False)
    op.create_index('ix_sales_invoices_due', 'sales_invoices', ['pharmacy_id', 'due_date'], unique=False)
    op.create_index('ix_sales_invoices_status', 'sales_invoices', ['pharmacy_id', 'status', 'payment_status'], unique=False)
    op.create_table('message_outbox',
    sa.Column('id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), autoincrement=True, nullable=False),
    sa.Column('pharmacy_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('channel', sa.String(length=20), nullable=False),
    sa.Column('mode', sa.String(length=20), nullable=False),
    sa.Column('purpose', sa.String(length=30), nullable=False),
    sa.Column('customer_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('invoice_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('to_number', sa.String(length=20), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('template_name', sa.String(length=100), nullable=True),
    sa.Column('template_params_json', sa.Text(), nullable=True),
    sa.Column('attach_invoice_pdf', sa.Boolean(), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('dedupe_key', sa.String(length=150), nullable=True),
    sa.Column('provider_message_id', sa.String(length=150), nullable=True),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('last_error', sa.String(length=500), nullable=True),
    sa.Column('scheduled_for', sa.DateTime(timezone=True), nullable=True),
    sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('failed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancelled_reason', sa.String(length=200), nullable=True),
    sa.Column('created_by', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_message_outbox_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], name=op.f('fk_message_outbox_customer_id_customers'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['invoice_id'], ['sales_invoices.id'], name=op.f('fk_message_outbox_invoice_id_sales_invoices'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['pharmacy_id'], ['pharmacies.id'], name=op.f('fk_message_outbox_pharmacy_id_pharmacies'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_message_outbox')),
    sa.UniqueConstraint('pharmacy_id', 'dedupe_key', name='uq_message_outbox_dedupe')
    )
    op.create_index(op.f('ix_message_outbox_customer_id'), 'message_outbox', ['customer_id'], unique=False)
    op.create_index(op.f('ix_message_outbox_invoice_id'), 'message_outbox', ['invoice_id'], unique=False)
    op.create_index(op.f('ix_message_outbox_provider_message_id'), 'message_outbox', ['provider_message_id'], unique=False)
    op.create_index('ix_message_outbox_status', 'message_outbox', ['status', 'next_attempt_at'], unique=False)
    op.create_table('payment_allocations',
    sa.Column('id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), autoincrement=True, nullable=False),
    sa.Column('payment_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('invoice_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('is_reversed', sa.Boolean(), nullable=False),
    sa.Column('reversed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_payment_allocations_created_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['invoice_id'], ['sales_invoices.id'], name=op.f('fk_payment_allocations_invoice_id_sales_invoices'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['payment_id'], ['customer_payments.id'], name=op.f('fk_payment_allocations_payment_id_customer_payments'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_payment_allocations'))
    )
    op.create_index(op.f('ix_payment_allocations_invoice_id'), 'payment_allocations', ['invoice_id'], unique=False)
    op.create_index(op.f('ix_payment_allocations_payment_id'), 'payment_allocations', ['payment_id'], unique=False)
    op.create_table('sale_prescriptions',
    sa.Column('id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), autoincrement=True, nullable=False),
    sa.Column('invoice_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('prescription_reference', sa.String(length=100), nullable=False),
    sa.Column('prescription_date', sa.Date(), nullable=True),
    sa.Column('prescriber_name', sa.String(length=200), nullable=False),
    sa.Column('prescriber_registration_no', sa.String(length=100), nullable=True),
    sa.Column('prescriber_address', sa.Text(), nullable=True),
    sa.Column('patient_name', sa.String(length=200), nullable=False),
    sa.Column('patient_address', sa.Text(), nullable=True),
    sa.Column('verified_by', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['invoice_id'], ['sales_invoices.id'], name=op.f('fk_sale_prescriptions_invoice_id_sales_invoices'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['verified_by'], ['users.id'], name=op.f('fk_sale_prescriptions_verified_by_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sale_prescriptions')),
    sa.UniqueConstraint('invoice_id', name=op.f('uq_sale_prescriptions_invoice_id'))
    )
    op.create_table('sales_invoice_items',
    sa.Column('id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), autoincrement=True, nullable=False),
    sa.Column('invoice_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('line_no', sa.Integer(), nullable=False),
    sa.Column('medicine_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('batch_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('stock_movement_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('medicine_name', sa.String(length=255), nullable=False),
    sa.Column('hsn_code', sa.String(length=20), nullable=True),
    sa.Column('schedule_type', sa.String(length=20), nullable=True),
    sa.Column('batch_number', sa.String(length=100), nullable=False),
    sa.Column('expiry_date', sa.Date(), nullable=False),
    sa.Column('unit_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('unit_name', sa.String(length=50), nullable=False),
    sa.Column('conversion_factor', sa.Numeric(precision=12, scale=4), nullable=False),
    sa.Column('quantity', sa.Numeric(precision=12, scale=4), nullable=False),
    sa.Column('quantity_base', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('mrp', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('selling_rate', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('gross_amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('discount_percent', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('discount_amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('taxable_amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('tax_rate', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('cgst_amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('sgst_amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('igst_amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('tax_amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('line_total', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('cost_per_base_unit', sa.Numeric(precision=12, scale=4), nullable=False),
    sa.Column('cost_total', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('returned_quantity_base', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.ForeignKeyConstraint(['batch_id'], ['medicine_batches.id'], name=op.f('fk_sales_invoice_items_batch_id_medicine_batches'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['invoice_id'], ['sales_invoices.id'], name=op.f('fk_sales_invoice_items_invoice_id_sales_invoices'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['medicine_id'], ['medicines.id'], name=op.f('fk_sales_invoice_items_medicine_id_medicines'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['stock_movement_id'], ['stock_movements.id'], name=op.f('fk_sales_invoice_items_stock_movement_id_stock_movements'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['unit_id'], ['units.id'], name=op.f('fk_sales_invoice_items_unit_id_units'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sales_invoice_items'))
    )
    op.create_index(op.f('ix_sales_invoice_items_batch_id'), 'sales_invoice_items', ['batch_id'], unique=False)
    op.create_index(op.f('ix_sales_invoice_items_invoice_id'), 'sales_invoice_items', ['invoice_id'], unique=False)
    op.create_index(op.f('ix_sales_invoice_items_medicine_id'), 'sales_invoice_items', ['medicine_id'], unique=False)
    op.create_table('sales_returns',
    sa.Column('id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), autoincrement=True, nullable=False),
    sa.Column('pharmacy_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('return_number', sa.String(length=30), nullable=False),
    sa.Column('idempotency_key', sa.String(length=64), nullable=False),
    sa.Column('invoice_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('customer_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('reason', sa.String(length=500), nullable=False),
    sa.Column('taxable_total', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('cgst_total', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('sgst_total', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('igst_total', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('tax_total', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('total_amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('applied_to_balance', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('refund_amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('credit_note_amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('refund_method', sa.String(length=20), nullable=True),
    sa.Column('refund_reference', sa.String(length=100), nullable=True),
    sa.Column('processed_by', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], name=op.f('fk_sales_returns_customer_id_customers'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['invoice_id'], ['sales_invoices.id'], name=op.f('fk_sales_returns_invoice_id_sales_invoices'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['pharmacy_id'], ['pharmacies.id'], name=op.f('fk_sales_returns_pharmacy_id_pharmacies'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['processed_by'], ['users.id'], name=op.f('fk_sales_returns_processed_by_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sales_returns')),
    sa.UniqueConstraint('pharmacy_id', 'idempotency_key', name='uq_sales_returns_idempotency'),
    sa.UniqueConstraint('pharmacy_id', 'return_number', name='uq_sales_returns_number')
    )
    op.create_index(op.f('ix_sales_returns_invoice_id'), 'sales_returns', ['invoice_id'], unique=False)
    op.create_table('customer_refunds',
    sa.Column('id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), autoincrement=True, nullable=False),
    sa.Column('pharmacy_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('customer_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('invoice_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('return_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('method', sa.String(length=20), nullable=False),
    sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('reference', sa.String(length=100), nullable=True),
    sa.Column('reason', sa.String(length=500), nullable=False),
    sa.Column('shift_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('refunded_by', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('refunded_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], name=op.f('fk_customer_refunds_customer_id_customers'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['invoice_id'], ['sales_invoices.id'], name=op.f('fk_customer_refunds_invoice_id_sales_invoices'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['pharmacy_id'], ['pharmacies.id'], name=op.f('fk_customer_refunds_pharmacy_id_pharmacies'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['refunded_by'], ['users.id'], name=op.f('fk_customer_refunds_refunded_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['return_id'], ['sales_returns.id'], name=op.f('fk_customer_refunds_return_id_sales_returns'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['shift_id'], ['cash_shifts.id'], name=op.f('fk_customer_refunds_shift_id_cash_shifts'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_customer_refunds'))
    )
    op.create_index(op.f('ix_customer_refunds_customer_id'), 'customer_refunds', ['customer_id'], unique=False)
    op.create_index(op.f('ix_customer_refunds_invoice_id'), 'customer_refunds', ['invoice_id'], unique=False)
    op.create_index(op.f('ix_customer_refunds_shift_id'), 'customer_refunds', ['shift_id'], unique=False)
    op.create_table('sales_return_items',
    sa.Column('id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), autoincrement=True, nullable=False),
    sa.Column('return_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('invoice_item_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('medicine_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('batch_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('quantity_base', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('condition', sa.String(length=20), nullable=False),
    sa.Column('disposition', sa.String(length=20), nullable=False),
    sa.Column('stock_movement_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('taxable_amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('cgst_amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('sgst_amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('igst_amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('tax_amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('line_total', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('cost_total', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.ForeignKeyConstraint(['batch_id'], ['medicine_batches.id'], name=op.f('fk_sales_return_items_batch_id_medicine_batches'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['invoice_item_id'], ['sales_invoice_items.id'], name=op.f('fk_sales_return_items_invoice_item_id_sales_invoice_items'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['medicine_id'], ['medicines.id'], name=op.f('fk_sales_return_items_medicine_id_medicines'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['return_id'], ['sales_returns.id'], name=op.f('fk_sales_return_items_return_id_sales_returns'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['stock_movement_id'], ['stock_movements.id'], name=op.f('fk_sales_return_items_stock_movement_id_stock_movements'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sales_return_items'))
    )
    op.create_index(op.f('ix_sales_return_items_invoice_item_id'), 'sales_return_items', ['invoice_item_id'], unique=False)
    op.create_index(op.f('ix_sales_return_items_return_id'), 'sales_return_items', ['return_id'], unique=False)
    op.create_table('customer_ledger_entries',
    sa.Column('id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), autoincrement=True, nullable=False),
    sa.Column('pharmacy_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('customer_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('entry_type', sa.String(length=30), nullable=False),
    sa.Column('debit', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('credit', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('invoice_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('payment_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('return_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('refund_id', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=True),
    sa.Column('reference', sa.String(length=50), nullable=False),
    sa.Column('description', sa.String(length=500), nullable=False),
    sa.Column('reason', sa.String(length=500), nullable=True),
    sa.Column('created_by', sa.Integer().with_variant(mysql.BIGINT(unsigned=True), 'mysql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_customer_ledger_entries_created_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], name=op.f('fk_customer_ledger_entries_customer_id_customers'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['invoice_id'], ['sales_invoices.id'], name=op.f('fk_customer_ledger_entries_invoice_id_sales_invoices'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['payment_id'], ['customer_payments.id'], name=op.f('fk_customer_ledger_entries_payment_id_customer_payments'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['pharmacy_id'], ['pharmacies.id'], name=op.f('fk_customer_ledger_entries_pharmacy_id_pharmacies'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['refund_id'], ['customer_refunds.id'], name=op.f('fk_customer_ledger_entries_refund_id_customer_refunds'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['return_id'], ['sales_returns.id'], name=op.f('fk_customer_ledger_entries_return_id_sales_returns'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_customer_ledger_entries'))
    )
    op.create_index('ix_customer_ledger_customer', 'customer_ledger_entries', ['pharmacy_id', 'customer_id', 'created_at'], unique=False)

    # ── medicine_batches: pricing unit and saleability status ─────────────────
    op.add_column("medicine_batches", sa.Column(
        "price_unit_id", sa.Integer().with_variant(mysql.BIGINT(unsigned=True), "mysql"), nullable=True))
    op.add_column("medicine_batches", sa.Column(
        "price_conversion_factor", sa.Numeric(12, 4), nullable=False, server_default="1.0000"))
    op.add_column("medicine_batches", sa.Column(
        "stock_status", sa.String(20), nullable=False, server_default="ACTIVE"))
    op.create_foreign_key("fk_medicine_batches_price_unit_id_units", "medicine_batches", "units",
                          ["price_unit_id"], ["id"], ondelete="RESTRICT")
    # Rates of received batches were entered per the goods-receipt pack unit
    op.execute("""
        UPDATE medicine_batches b
        JOIN (
            SELECT gri.batch_id, gri.packaging_unit_id, gri.conversion_factor
            FROM goods_receipt_items gri
            JOIN (SELECT batch_id, MAX(id) AS last_id FROM goods_receipt_items
                  WHERE batch_id IS NOT NULL GROUP BY batch_id) latest ON latest.last_id = gri.id
        ) g ON g.batch_id = b.id
        SET b.price_unit_id = g.packaging_unit_id, b.price_conversion_factor = g.conversion_factor
    """)
    op.execute("""
        UPDATE medicine_batches b JOIN medicines m ON m.id = b.medicine_id
        SET b.price_unit_id = m.base_unit_id
        WHERE b.price_unit_id IS NULL
    """)

    # ── Permissions for existing pharmacies ───────────────────────────────────
    conn = op.get_bind()
    for code, description, module in PHASE4_PERMISSIONS:
        conn.execute(sa.text(
            "INSERT INTO permissions (code, description, module) "
            "SELECT :code, :description, :module FROM DUAL "
            "WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = :code)"
        ), {"code": code, "description": description, "module": module})
    for role_name, codes in ROLE_GRANTS.items():
        for code in codes:
            conn.execute(sa.text(
                "INSERT INTO role_permissions (role_id, permission_id) "
                "SELECT r.id, p.id FROM roles r JOIN permissions p ON p.code = :code "
                "WHERE r.name = :role AND r.is_system_role = 1 "
                "AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)"
            ), {"code": code, "role": role_name})


def downgrade() -> None:
    conn = op.get_bind()
    codes = [p[0] for p in PHASE4_PERMISSIONS]
    conn.execute(sa.text(
        "DELETE rp FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id WHERE p.code IN :codes"
    ).bindparams(sa.bindparam("codes", expanding=True)), {"codes": codes})
    conn.execute(sa.text("DELETE FROM permissions WHERE code IN :codes")
                 .bindparams(sa.bindparam("codes", expanding=True)), {"codes": codes})
    op.drop_constraint("fk_medicine_batches_price_unit_id_units", "medicine_batches", type_="foreignkey")
    op.drop_column("medicine_batches", "stock_status")
    op.drop_column("medicine_batches", "price_conversion_factor")
    op.drop_column("medicine_batches", "price_unit_id")
    for table in NEW_TABLES:
        op.drop_table(table)
