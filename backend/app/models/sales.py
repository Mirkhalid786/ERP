"""
SQLAlchemy ORM models for Phase 4: POS, Sales, Billing, Credit and Customer Communication.

Money model
-----------
* `sales_invoices` hold the immutable billed values plus running settlement fields
  (amount_paid, return_credit_applied, balance_due) that change only through
  payments, allocations, returns and voids — never by direct edit.
* `customer_payments` record money received (cash, UPI, card, ...). A payment is
  applied to invoices through `payment_allocations`; any unapplied part is the
  customer's advance credit.
* `customer_ledger_entries` is the append-only customer account. A customer's
  outstanding balance is SUM(debit - credit); it is never stored or overwritten.
* `customer_refunds` record money paid back (returns, voids).
* Stock leaves inventory only through `sales_invoice_items` (one row per batch
  allocation), each linked to its immutable StockMovement.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.inventory import FK_BIGINT, PK_BIGINT, utcnow

ZERO = Decimal("0.00")


def _money(default: Decimal = ZERO):
    return mapped_column(Numeric(12, 2), default=default, nullable=False)


# ── Customers ──────────────────────────────────────────────────────────────────

class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    mobile: Mapped[Optional[str]] = mapped_column(String(20))           # Normalised E.164 digits, e.g. "919876543210"
    email: Mapped[Optional[str]] = mapped_column(String(255))
    address: Mapped[Optional[str]] = mapped_column(Text)
    gstin: Mapped[Optional[str]] = mapped_column(String(15))
    state_code: Mapped[Optional[str]] = mapped_column(String(2))
    business_name: Mapped[Optional[str]] = mapped_column(String(255))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    credit_limit: Mapped[Decimal] = _money()                            # 0 = no credit allowed without override
    payment_terms_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    whatsapp_consent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    whatsapp_consent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reminders_opt_out: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    preferred_language: Mapped[str] = mapped_column(String(10), default="en", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("pharmacy_id", "mobile", name="uq_customers_pharmacy_mobile"),
        Index("ix_customers_pharmacy_name", "pharmacy_id", "name"),
    )


# ── Cash shifts (day-end closing) ──────────────────────────────────────────────

class CashShift(Base):
    __tablename__ = "cash_shifts"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False)
    user_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="OPEN", nullable=False)  # OPEN, CLOSED, REVIEWED
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    opening_cash: Mapped[Decimal] = _money()
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="SET NULL"))
    expected_cash: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    counted_cash: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    variance: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    variance_note: Mapped[Optional[str]] = mapped_column(Text)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reviewed_by: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (
        Index("ix_cash_shifts_user_status", "pharmacy_id", "user_id", "status"),
    )


class CashMovement(Base):
    """Cash paid out of / into the drawer that is not a sale, collection or refund."""
    __tablename__ = "cash_movements"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False)
    shift_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("cash_shifts.id", ondelete="RESTRICT"), nullable=False, index=True)
    movement_type: Mapped[str] = mapped_column(String(20), nullable=False)  # PAID_OUT, PAID_IN
    # See app.services.shifts.MOVEMENT_CATEGORIES; rows from before categories existed are UNCATEGORISED
    category: Mapped[str] = mapped_column(String(30), default="UNCATEGORISED", server_default="UNCATEGORISED", nullable=False)
    amount: Mapped[Decimal] = _money()
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    reference: Mapped[Optional[str]] = mapped_column(String(100))          # Deposit slip, handover note, ...
    authorised_by: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="SET NULL"))
    created_by: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


# ── Sales invoices ─────────────────────────────────────────────────────────────

class SalesInvoice(Base):
    __tablename__ = "sales_invoices"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False)
    invoice_number: Mapped[str] = mapped_column(String(30), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    invoice_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="FINALIZED", nullable=False)  # FINALIZED, VOIDED

    customer_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("customers.id", ondelete="RESTRICT"), index=True)
    customer_name: Mapped[str] = mapped_column(String(200), nullable=False)      # Snapshot at billing time
    customer_mobile: Mapped[Optional[str]] = mapped_column(String(20))
    customer_gstin: Mapped[Optional[str]] = mapped_column(String(15))
    is_inter_state: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cashier_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    shift_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("cash_shifts.id", ondelete="SET NULL"))

    gross_amount: Mapped[Decimal] = _money()        # Σ qty × selling rate (GST-inclusive, before discount)
    discount_total: Mapped[Decimal] = _money()
    taxable_total: Mapped[Decimal] = _money()
    cgst_total: Mapped[Decimal] = _money()
    sgst_total: Mapped[Decimal] = _money()
    igst_total: Mapped[Decimal] = _money()
    tax_total: Mapped[Decimal] = _money()
    round_off: Mapped[Decimal] = _money()
    grand_total: Mapped[Decimal] = _money()
    cost_total: Mapped[Decimal] = _money()          # Σ batch cost of goods sold (excl. GST)

    amount_paid: Mapped[Decimal] = _money()         # Σ active payment allocations
    paid_at_sale: Mapped[Decimal] = _money()        # Part of amount_paid received at the counter
    return_credit_applied: Mapped[Decimal] = _money()  # Returns that reduced the balance due
    returned_value: Mapped[Decimal] = _money()      # Total value of goods returned
    refunded_amount: Mapped[Decimal] = _money()     # Money paid back against this invoice
    written_off_amount: Mapped[Decimal] = _money()  # Authorised ledger adjustments / write-offs
    balance_due: Mapped[Decimal] = _money()         # grand_total - amount_paid - return_credit_applied - written_off_amount
    payment_status: Mapped[str] = mapped_column(String(20), default="UNPAID", nullable=False)  # PAID, PARTIALLY_PAID, UNPAID
    return_status: Mapped[str] = mapped_column(String(20), default="NONE", nullable=False)     # NONE, PARTIAL, FULL
    is_credit_sale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    due_date: Mapped[Optional[date]] = mapped_column(Date)
    credit_override_by: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="SET NULL"))
    credit_override_reason: Mapped[Optional[str]] = mapped_column(String(500))

    prescription_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reminders_paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_disputed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    dispute_note: Mapped[Optional[str]] = mapped_column(String(500))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    print_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    voided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    voided_by: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="SET NULL"))
    void_reason: Mapped[Optional[str]] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    items: Mapped[List["SalesInvoiceItem"]] = relationship(
        back_populates="invoice", order_by="SalesInvoiceItem.line_no", cascade="all, delete-orphan"
    )
    customer: Mapped[Optional["Customer"]] = relationship()
    prescription: Mapped[Optional["SalePrescription"]] = relationship(back_populates="invoice", uselist=False)

    __table_args__ = (
        UniqueConstraint("pharmacy_id", "invoice_number", name="uq_sales_invoices_number"),
        UniqueConstraint("pharmacy_id", "idempotency_key", name="uq_sales_invoices_idempotency"),
        Index("ix_sales_invoices_date", "pharmacy_id", "invoice_date"),
        Index("ix_sales_invoices_status", "pharmacy_id", "status", "payment_status"),
        Index("ix_sales_invoices_due", "pharmacy_id", "due_date"),
    )


class SalesInvoiceItem(Base):
    """One batch allocation of a billed line. A cart line split across batches (FEFO) becomes several items."""
    __tablename__ = "sales_invoice_items"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("sales_invoices.id", ondelete="RESTRICT"), nullable=False, index=True)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    medicine_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("medicines.id", ondelete="RESTRICT"), nullable=False, index=True)
    batch_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("medicine_batches.id", ondelete="RESTRICT"), nullable=False, index=True)
    stock_movement_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("stock_movements.id", ondelete="RESTRICT"))

    medicine_name: Mapped[str] = mapped_column(String(255), nullable=False)
    hsn_code: Mapped[Optional[str]] = mapped_column(String(20))
    schedule_type: Mapped[Optional[str]] = mapped_column(String(20))
    batch_number: Mapped[str] = mapped_column(String(100), nullable=False)
    expiry_date: Mapped[date] = mapped_column(Date, nullable=False)
    unit_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("units.id", ondelete="RESTRICT"), nullable=False)
    unit_name: Mapped[str] = mapped_column(String(50), nullable=False)
    conversion_factor: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)      # In the sale unit
    quantity_base: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    mrp: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)            # Per sale unit
    selling_rate: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)   # Per sale unit, GST-inclusive
    gross_amount: Mapped[Decimal] = _money()
    discount_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=ZERO, nullable=False)
    discount_amount: Mapped[Decimal] = _money()
    taxable_amount: Mapped[Decimal] = _money()
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=ZERO, nullable=False)
    cgst_amount: Mapped[Decimal] = _money()
    sgst_amount: Mapped[Decimal] = _money()
    igst_amount: Mapped[Decimal] = _money()
    tax_amount: Mapped[Decimal] = _money()
    line_total: Mapped[Decimal] = _money()
    cost_per_base_unit: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    cost_total: Mapped[Decimal] = _money()
    returned_quantity_base: Mapped[Decimal] = _money()

    invoice: Mapped["SalesInvoice"] = relationship(back_populates="items")


class SalePrescription(Base):
    """Prescription details for invoices containing prescription-only medicines. Access-controlled."""
    __tablename__ = "sale_prescriptions"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("sales_invoices.id", ondelete="RESTRICT"), nullable=False, unique=True)
    prescription_reference: Mapped[str] = mapped_column(String(100), nullable=False)
    prescription_date: Mapped[Optional[date]] = mapped_column(Date)
    prescriber_name: Mapped[str] = mapped_column(String(200), nullable=False)
    prescriber_registration_no: Mapped[Optional[str]] = mapped_column(String(100))
    prescriber_address: Mapped[Optional[str]] = mapped_column(Text)
    patient_name: Mapped[str] = mapped_column(String(200), nullable=False)
    patient_address: Mapped[Optional[str]] = mapped_column(Text)
    verified_by: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    invoice: Mapped["SalesInvoice"] = relationship(back_populates="prescription")


# ── Payments, allocations, refunds ─────────────────────────────────────────────

class CustomerPayment(Base):
    """Money received. `amount` is what was applied/kept (cash tendered minus change)."""
    __tablename__ = "customer_payments"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False)
    receipt_number: Mapped[str] = mapped_column(String(30), nullable=False)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(64))
    customer_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("customers.id", ondelete="RESTRICT"), index=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)    # SALE, COLLECTION, RETURN_CREDIT
    method: Mapped[str] = mapped_column(String(20), nullable=False)    # CASH, UPI, CARD, BANK_TRANSFER, OTHER, CREDIT_NOTE
    amount: Mapped[Decimal] = _money()
    amount_tendered: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))   # Cash handed over
    change_returned: Mapped[Decimal] = _money()
    refunded_amount: Mapped[Decimal] = _money()
    reference: Mapped[Optional[str]] = mapped_column(String(100))
    verification_status: Mapped[str] = mapped_column(String(20), default="RECORDED", nullable=False)  # RECORDED, VERIFIED, NOT_APPLICABLE
    verified_by: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="SET NULL"))
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="POSTED", nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(String(500))
    shift_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("cash_shifts.id", ondelete="SET NULL"), index=True)
    received_by: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    allocations: Mapped[List["PaymentAllocation"]] = relationship(back_populates="payment")

    __table_args__ = (
        UniqueConstraint("pharmacy_id", "receipt_number", name="uq_customer_payments_receipt"),
        UniqueConstraint("pharmacy_id", "idempotency_key", name="uq_customer_payments_idempotency"),
        Index("ix_customer_payments_date", "pharmacy_id", "received_at"),
    )


class PaymentAllocation(Base):
    __tablename__ = "payment_allocations"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    payment_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("customer_payments.id", ondelete="RESTRICT"), nullable=False, index=True)
    invoice_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("sales_invoices.id", ondelete="RESTRICT"), nullable=False, index=True)
    amount: Mapped[Decimal] = _money()
    is_reversed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reversed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    payment: Mapped["CustomerPayment"] = relationship(back_populates="allocations")


class CustomerRefund(Base):
    __tablename__ = "customer_refunds"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False)
    customer_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("customers.id", ondelete="RESTRICT"), index=True)
    invoice_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("sales_invoices.id", ondelete="RESTRICT"), index=True)
    return_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("sales_returns.id", ondelete="RESTRICT"))
    method: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = _money()
    reference: Mapped[Optional[str]] = mapped_column(String(100))
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    shift_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("cash_shifts.id", ondelete="SET NULL"), index=True)
    refunded_by: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    refunded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


# ── Customer ledger ────────────────────────────────────────────────────────────

class CustomerLedgerEntry(Base):
    """Append-only customer account. debit = customer owes more; credit = customer owes less."""
    __tablename__ = "customer_ledger_entries"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False)
    customer_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False)
    entry_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # INVOICE, PAYMENT, RETURN_CREDIT, REFUND, VOID_REVERSAL, ADJUSTMENT_DEBIT, ADJUSTMENT_CREDIT, WRITE_OFF
    debit: Mapped[Decimal] = _money()
    credit: Mapped[Decimal] = _money()
    invoice_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("sales_invoices.id", ondelete="RESTRICT"))
    payment_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("customer_payments.id", ondelete="RESTRICT"))
    return_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("sales_returns.id", ondelete="RESTRICT"))
    refund_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("customer_refunds.id", ondelete="RESTRICT"))
    reference: Mapped[str] = mapped_column(String(50), nullable=False)    # Human document number
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(String(500))
    created_by: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_customer_ledger_customer", "pharmacy_id", "customer_id", "created_at"),
    )


# ── Sales returns ──────────────────────────────────────────────────────────────

class SalesReturn(Base):
    __tablename__ = "sales_returns"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False)
    return_number: Mapped[str] = mapped_column(String(30), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    invoice_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("sales_invoices.id", ondelete="RESTRICT"), nullable=False, index=True)
    customer_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("customers.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(20), default="POSTED", nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    taxable_total: Mapped[Decimal] = _money()
    cgst_total: Mapped[Decimal] = _money()
    sgst_total: Mapped[Decimal] = _money()
    igst_total: Mapped[Decimal] = _money()
    tax_total: Mapped[Decimal] = _money()
    total_amount: Mapped[Decimal] = _money()
    applied_to_balance: Mapped[Decimal] = _money()   # Reduced the invoice's balance due
    refund_amount: Mapped[Decimal] = _money()        # Paid back in money
    credit_note_amount: Mapped[Decimal] = _money()   # Kept as customer credit
    refund_method: Mapped[Optional[str]] = mapped_column(String(20))
    refund_reference: Mapped[Optional[str]] = mapped_column(String(100))
    processed_by: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    items: Mapped[List["SalesReturnItem"]] = relationship(back_populates="sales_return", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("pharmacy_id", "return_number", name="uq_sales_returns_number"),
        UniqueConstraint("pharmacy_id", "idempotency_key", name="uq_sales_returns_idempotency"),
    )


class SalesReturnItem(Base):
    __tablename__ = "sales_return_items"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    return_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("sales_returns.id", ondelete="RESTRICT"), nullable=False, index=True)
    invoice_item_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("sales_invoice_items.id", ondelete="RESTRICT"), nullable=False, index=True)
    medicine_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("medicines.id", ondelete="RESTRICT"), nullable=False)
    batch_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("medicine_batches.id", ondelete="RESTRICT"), nullable=False)
    quantity_base: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    condition: Mapped[str] = mapped_column(String(20), nullable=False)      # SALEABLE, DAMAGED, EXPIRED, OPENED
    disposition: Mapped[str] = mapped_column(String(20), nullable=False)    # RESTOCKED, WRITTEN_OFF
    stock_movement_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("stock_movements.id", ondelete="RESTRICT"))
    taxable_amount: Mapped[Decimal] = _money()
    cgst_amount: Mapped[Decimal] = _money()
    sgst_amount: Mapped[Decimal] = _money()
    igst_amount: Mapped[Decimal] = _money()
    tax_amount: Mapped[Decimal] = _money()
    line_total: Mapped[Decimal] = _money()
    cost_total: Mapped[Decimal] = _money()

    sales_return: Mapped["SalesReturn"] = relationship(back_populates="items")


# ── Messaging outbox (WhatsApp) ────────────────────────────────────────────────

class MessageOutbox(Base):
    """
    Durable record of every customer message. Rows are committed before any provider
    call, so a sale is never affected by WhatsApp failures, and retries reuse the row.
    """
    __tablename__ = "message_outbox"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), default="WHATSAPP", nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)        # CLOUD_API, MANUAL
    purpose: Mapped[str] = mapped_column(String(30), nullable=False)     # INVOICE, REMINDER_UPCOMING, REMINDER_DUE_TODAY, REMINDER_OVERDUE
    customer_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("customers.id", ondelete="RESTRICT"), index=True)
    invoice_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("sales_invoices.id", ondelete="RESTRICT"), index=True)
    to_number: Mapped[str] = mapped_column(String(20), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    template_name: Mapped[Optional[str]] = mapped_column(String(100))
    template_params_json: Mapped[Optional[str]] = mapped_column(Text)
    attach_invoice_pdf: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="PENDING", nullable=False)
    # PENDING, QUEUED, SENDING, SENT, DELIVERED, READ, FAILED, CANCELLED, MANUAL_SHARE_OPENED
    dedupe_key: Mapped[Optional[str]] = mapped_column(String(150))
    provider_message_id: Mapped[Optional[str]] = mapped_column(String(150), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[Optional[str]] = mapped_column(String(500))
    scheduled_for: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    cancelled_reason: Mapped[Optional[str]] = mapped_column(String(200))
    created_by: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("pharmacy_id", "dedupe_key", name="uq_message_outbox_dedupe"),
        Index("ix_message_outbox_status", "status", "next_attempt_at"),
    )
