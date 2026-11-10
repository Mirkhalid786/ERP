"""
Stage 5B preparation: supplier payments and expenses (operational records only).

* `supplier_payments` — dated, numbered money paid to a supplier. A payment is
  never edited: a mistake is corrected by reversing it (reason + permission) and
  recording a new one. `idempotency_key` stops double submission; `reference_key`
  (method + normalised UTR / cheque number, cleared on reversal) stops the same
  bank reference being recorded twice for one supplier.
* `supplier_payment_allocations` — how much of a payment settles which finalised
  supplier invoice. Any unallocated part is an advance to the supplier.
  `supplier_invoices.paid_amount` / `payment_status` are only a cache recomputed
  from active allocations; they are never written directly any more.
* `expense_categories` / `expenses` — business expenses paid in cash or through
  the bank, with approval, audit and controlled reversal.

They are posted to the general ledger by app.services.gl_posting (Stage 5B).
"""
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.inventory import FK_BIGINT, PK_BIGINT, utcnow

ZERO = Decimal("0.00")

SUPPLIER_PAYMENT_METHODS = ("CASH", "BANK_TRANSFER", "UPI", "CHEQUE", "CARD")
EXPENSE_PAYMENT_METHODS = ("CASH", "BANK_TRANSFER", "UPI", "CHEQUE", "CARD")
CASH_SOURCES = ("DRAWER", "OFFICE_CASH")   # DRAWER = the POS cash drawer of an open shift


class SupplierPayment(Base):
    __tablename__ = "supplier_payments"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False)
    payment_number: Mapped[str] = mapped_column(String(30), nullable=False)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(64))
    request_hash: Mapped[Optional[str]] = mapped_column(String(64))
    supplier_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False, index=True)
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    method: Mapped[str] = mapped_column(String(20), nullable=False)          # SUPPLIER_PAYMENT_METHODS or LEGACY
    cash_source: Mapped[Optional[str]] = mapped_column(String(20))            # CASH only: DRAWER / OFFICE_CASH
    shift_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("cash_shifts.id", ondelete="RESTRICT"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    reference: Mapped[Optional[str]] = mapped_column(String(100))            # UTR, cheque number, ...
    reference_key: Mapped[Optional[str]] = mapped_column(String(130))        # Normalised; NULL once reversed
    bank_account: Mapped[Optional[str]] = mapped_column(String(100))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="POSTED", nullable=False)   # POSTED, REVERSED
    is_legacy: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)       # Migrated from paid_amount
    created_by: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    reversed_by: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="SET NULL"))
    reversed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reversal_reason: Mapped[Optional[str]] = mapped_column(Text)

    allocations: Mapped[List["SupplierPaymentAllocation"]] = relationship(back_populates="payment", order_by="SupplierPaymentAllocation.id")

    __table_args__ = (
        UniqueConstraint("pharmacy_id", "payment_number", name="uq_supplier_payments_number"),
        UniqueConstraint("pharmacy_id", "idempotency_key", name="uq_supplier_payments_idem"),
        UniqueConstraint("pharmacy_id", "supplier_id", "reference_key", name="uq_supplier_payments_reference"),
        Index("ix_supplier_payments_supplier_date", "pharmacy_id", "supplier_id", "payment_date"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("status IN ('POSTED','REVERSED')", name="status_valid"),
    )


class SupplierPaymentAllocation(Base):
    __tablename__ = "supplier_payment_allocations"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    payment_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("supplier_payments.id", ondelete="RESTRICT"), nullable=False, index=True)
    invoice_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("supplier_invoices.id", ondelete="RESTRICT"), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", nullable=False)   # ACTIVE, REVERSED
    created_by: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    reversed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    payment: Mapped["SupplierPayment"] = relationship(back_populates="allocations")

    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("status IN ('ACTIVE','REVERSED')", name="status_valid"),
    )


class ExpenseCategory(Base):
    __tablename__ = "expense_categories"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False)
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255))
    account_code_hint: Mapped[Optional[str]] = mapped_column(String(20))   # Suggested GL account for Stage 5B (not used yet)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("pharmacy_id", "code", name="uq_expense_categories_code"),)


class Expense(Base):
    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False)
    expense_number: Mapped[str] = mapped_column(String(30), nullable=False)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(64))
    request_hash: Mapped[Optional[str]] = mapped_column(String(64))
    category_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("expense_categories.id", ondelete="RESTRICT"), nullable=False, index=True)
    expense_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)            # Total paid, GST included
    gst_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=ZERO, nullable=False)
    payee: Mapped[str] = mapped_column(String(200), nullable=False)
    payee_gstin: Mapped[Optional[str]] = mapped_column(String(15))
    supplier_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("suppliers.id", ondelete="RESTRICT"))
    bill_number: Mapped[Optional[str]] = mapped_column(String(100))
    payment_method: Mapped[str] = mapped_column(String(20), nullable=False)
    cash_source: Mapped[Optional[str]] = mapped_column(String(20))
    shift_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("cash_shifts.id", ondelete="RESTRICT"), index=True)
    reference: Mapped[Optional[str]] = mapped_column(String(100))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False)   # PENDING_APPROVAL, APPROVED, REJECTED, REVERSED
    created_by: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    approved_by: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[Optional[str]] = mapped_column(Text)
    reversed_by: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="SET NULL"))
    reversed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reversal_reason: Mapped[Optional[str]] = mapped_column(Text)

    category: Mapped["ExpenseCategory"] = relationship()

    __table_args__ = (
        UniqueConstraint("pharmacy_id", "expense_number", name="uq_expenses_number"),
        UniqueConstraint("pharmacy_id", "idempotency_key", name="uq_expenses_idem"),
        Index("ix_expenses_pharmacy_date", "pharmacy_id", "expense_date"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("gst_amount >= 0 AND gst_amount <= amount", name="gst_within_amount"),
        CheckConstraint("status IN ('PENDING_APPROVAL','APPROVED','REJECTED','REVERSED')", name="status_valid"),
    )


CREDIT_NOTE_REASONS = ("RATE_DIFFERENCE", "SCHEME_DISCOUNT", "EXPIRY_CLAIM", "DAMAGE_CLAIM", "SHORT_SUPPLY", "OTHER")


class SupplierCreditNote(Base):
    """
    A credit note from a supplier that reduces what is owed without goods coming back
    (rate difference, scheme discount, expiry / damage claim). Stock returns stay on
    supplier_returns. Cancelled notes are kept; their number is freed for re-entry.
    """
    __tablename__ = "supplier_credit_notes"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False)
    supplier_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False, index=True)
    credit_note_number: Mapped[str] = mapped_column(String(100), nullable=False)      # As printed by the supplier
    number_key: Mapped[Optional[str]] = mapped_column(String(120))                    # FY + normalised number; NULL once cancelled
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(64))
    note_date: Mapped[date] = mapped_column(Date, nullable=False)
    invoice_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("supplier_invoices.id", ondelete="RESTRICT"), index=True)
    reason_code: Mapped[str] = mapped_column(String(30), nullable=False)
    taxable_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    cgst_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=ZERO, nullable=False)
    sgst_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=ZERO, nullable=False)
    igst_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=ZERO, nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="POSTED", nullable=False)  # POSTED, CANCELLED
    created_by: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    cancelled_by: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="SET NULL"))
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("pharmacy_id", "supplier_id", "number_key", name="uq_supplier_credit_notes_number"),
        UniqueConstraint("pharmacy_id", "idempotency_key", name="uq_supplier_credit_notes_idem"),
        CheckConstraint("taxable_amount >= 0 AND cgst_amount >= 0 AND sgst_amount >= 0 AND igst_amount >= 0", name="amounts_valid"),
        CheckConstraint("total_amount > 0 AND total_amount = taxable_amount + cgst_amount + sgst_amount + igst_amount", name="total_valid"),
        CheckConstraint("status IN ('POSTED','CANCELLED')", name="status_valid"),
    )
