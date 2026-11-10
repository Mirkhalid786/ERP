"""
Phase 5A: double-entry general ledger.

* `ledger_accounts` — chart of accounts (tree via parent_id). Group accounts only
  organise; postings go to leaf accounts. Accounts with a `system_key` are used by
  automatic posting rules and cannot be deactivated or deleted.
* `fiscal_years` / `accounting_periods` — Indian FY (April–March by default) with
  monthly periods that can be closed and (with permission + reason) reopened.
* `journal_entries` / `journal_lines` — every financial event. A POSTED entry is
  immutable: corrections are made with a reversal entry plus a new entry.
  `source_key` (unique per pharmacy) makes each source event post at most once.
  `integrity_hash` lets the integrity check detect any later change to a posted entry.
* `opening_balance_batches` / `opening_balance_lines` — the one-time opening entry,
  prepared as a draft, previewed and reconciled, then posted.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.inventory import FK_BIGINT, PK_BIGINT, utcnow

ZERO = Decimal("0.00")


class LedgerAccount(Base):
    __tablename__ = "ledger_accounts"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    account_type: Mapped[str] = mapped_column(String(20), nullable=False)   # ASSET, LIABILITY, EQUITY, INCOME, EXPENSE
    normal_balance: Mapped[str] = mapped_column(String(6), nullable=False)  # DEBIT or CREDIT (contra accounts differ from type)
    parent_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("ledger_accounts.id", ondelete="RESTRICT"))
    is_group: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    system_key: Mapped[Optional[str]] = mapped_column(String(40))          # e.g. CASH, AR, OUTPUT_CGST
    party_type: Mapped[Optional[str]] = mapped_column(String(10))          # CUSTOMER / SUPPLIER: lines must name the party
    is_control: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # Fed only by operational postings
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(500))
    created_by: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("pharmacy_id", "code", name="uq_ledger_accounts_code"),
        UniqueConstraint("pharmacy_id", "system_key", name="uq_ledger_accounts_system_key"),
        CheckConstraint("account_type IN ('ASSET','LIABILITY','EQUITY','INCOME','EXPENSE')", name="account_type_valid"),
        CheckConstraint("normal_balance IN ('DEBIT','CREDIT')", name="normal_balance_valid"),
    )


class FiscalYear(Base):
    __tablename__ = "fiscal_years"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False)
    name: Mapped[str] = mapped_column(String(20), nullable=False)          # "2026-27"
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="OPEN", nullable=False)  # OPEN, CLOSED
    created_by: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    periods: Mapped[List["AccountingPeriod"]] = relationship(back_populates="fiscal_year", order_by="AccountingPeriod.start_date")

    __table_args__ = (
        UniqueConstraint("pharmacy_id", "start_date", name="uq_fiscal_years_start"),
        CheckConstraint("end_date > start_date", name="fy_dates_valid"),
    )


class AccountingPeriod(Base):
    __tablename__ = "accounting_periods"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False)
    fiscal_year_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("fiscal_years.id", ondelete="RESTRICT"), nullable=False)
    name: Mapped[str] = mapped_column(String(20), nullable=False)          # "Oct 2026"
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    # OPEN: postings allowed · CLOSED: blocked until an authorised reopen ·
    # PRE_OPENING: before the books' opening date, never posted to
    status: Mapped[str] = mapped_column(String(12), default="OPEN", nullable=False)
    closed_by: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="SET NULL"))
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reopened_by: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="SET NULL"))
    reopened_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reopen_reason: Mapped[Optional[str]] = mapped_column(String(500))

    fiscal_year: Mapped["FiscalYear"] = relationship(back_populates="periods")

    __table_args__ = (
        UniqueConstraint("pharmacy_id", "start_date", name="uq_accounting_periods_start"),
        Index("ix_accounting_periods_dates", "pharmacy_id", "start_date", "end_date"),
        CheckConstraint("end_date >= start_date", name="period_dates_valid"),
    )


class JournalEntry(Base):
    __tablename__ = "journal_entries"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False)
    entry_number: Mapped[Optional[str]] = mapped_column(String(30))        # Assigned when posted: JV/26-27/00001
    status: Mapped[str] = mapped_column(String(10), default="DRAFT", nullable=False)  # DRAFT, POSTED, REVERSED
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)          # Posting (accounting) date
    document_date: Mapped[Optional[date]] = mapped_column(Date)             # Date on the source document
    period_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("accounting_periods.id", ondelete="RESTRICT"))
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)    # MANUAL, OPENING, REVERSAL, SALE, ...
    source_id: Mapped[Optional[str]] = mapped_column(String(64))
    source_key: Mapped[Optional[str]] = mapped_column(String(150))          # Unique per pharmacy: one posting per source event
    request_hash: Mapped[Optional[str]] = mapped_column(String(64))
    reference: Mapped[Optional[str]] = mapped_column(String(100))
    narration: Mapped[str] = mapped_column(String(1000), nullable=False)
    total_debit: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=ZERO, nullable=False)
    total_credit: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=ZERO, nullable=False)
    integrity_hash: Mapped[Optional[str]] = mapped_column(String(64))
    created_by: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    posted_by: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="SET NULL"))
    posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reversal_of_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("journal_entries.id", ondelete="RESTRICT"))
    reversed_by_entry_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("journal_entries.id", ondelete="RESTRICT"))
    reversed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reversal_reason: Mapped[Optional[str]] = mapped_column(String(500))

    lines: Mapped[List["JournalLine"]] = relationship(
        back_populates="entry", order_by="JournalLine.line_no", cascade="all, delete-orphan",
        foreign_keys="JournalLine.entry_id",
    )

    __table_args__ = (
        UniqueConstraint("pharmacy_id", "entry_number", name="uq_journal_entries_number"),
        UniqueConstraint("pharmacy_id", "source_key", name="uq_journal_entries_source_key"),
        Index("ix_journal_entries_date", "pharmacy_id", "entry_date", "status"),
        Index("ix_journal_entries_source", "pharmacy_id", "source_type", "source_id"),
        CheckConstraint("status IN ('DRAFT','POSTED','REVERSED')", name="status_valid"),
        CheckConstraint("total_debit = total_credit", name="balanced"),
        CheckConstraint("total_debit >= 0", name="totals_non_negative"),
    )


class JournalLine(Base):
    __tablename__ = "journal_lines"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    entry_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("journal_entries.id", ondelete="RESTRICT"), nullable=False, index=True)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    account_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("ledger_accounts.id", ondelete="RESTRICT"), nullable=False)
    debit: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=ZERO, nullable=False)
    credit: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=ZERO, nullable=False)
    party_type: Mapped[Optional[str]] = mapped_column(String(10))           # CUSTOMER / SUPPLIER
    party_id: Mapped[Optional[int]] = mapped_column(Integer)
    description: Mapped[Optional[str]] = mapped_column(String(500))

    entry: Mapped["JournalEntry"] = relationship(back_populates="lines", foreign_keys=[entry_id])

    __table_args__ = (
        UniqueConstraint("entry_id", "line_no", name="uq_journal_lines_line_no"),
        Index("ix_journal_lines_account", "account_id", "entry_id"),
        Index("ix_journal_lines_party", "party_type", "party_id"),
        # Exactly one side per line, never negative, never zero
        CheckConstraint("debit >= 0 AND credit >= 0", name="non_negative"),
        CheckConstraint("(debit = 0 AND credit > 0) OR (credit = 0 AND debit > 0)", name="one_sided"),
    )


class OpeningBalanceBatch(Base):
    __tablename__ = "opening_balance_batches"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="DRAFT", nullable=False)  # DRAFT, POSTED
    opening_date: Mapped[date] = mapped_column(Date, nullable=False)
    source_description: Mapped[str] = mapped_column(String(500), nullable=False)    # e.g. "Tally trial balance 30-09-2026, signed by CA"
    notes: Mapped[Optional[str]] = mapped_column(Text)
    post_difference_to_suspense: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    journal_entry_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("journal_entries.id", ondelete="RESTRICT"))
    prepared_by: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    posted_by: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="SET NULL"))
    posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    lines: Mapped[List["OpeningBalanceLine"]] = relationship(
        back_populates="batch", order_by="OpeningBalanceLine.id", cascade="all, delete-orphan")


class OpeningBalanceLine(Base):
    """
    One opening balance. Customer / supplier balances are entered per outstanding
    document (number, date, due date), so later collections and payments can be
    allocated against them instead of against one lump sum.
    """
    __tablename__ = "opening_balance_lines"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("opening_balance_batches.id", ondelete="CASCADE"), nullable=False, index=True)
    account_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("ledger_accounts.id", ondelete="RESTRICT"), nullable=False)
    debit: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=ZERO, nullable=False)
    credit: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=ZERO, nullable=False)
    party_type: Mapped[Optional[str]] = mapped_column(String(10))
    party_id: Mapped[Optional[int]] = mapped_column(Integer)
    document_number: Mapped[Optional[str]] = mapped_column(String(60))
    document_date: Mapped[Optional[date]] = mapped_column(Date)
    due_date: Mapped[Optional[date]] = mapped_column(Date)
    outstanding_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=ZERO, nullable=False)  # Reduced by Stage 5B allocations
    notes: Mapped[Optional[str]] = mapped_column(String(500))

    batch: Mapped["OpeningBalanceBatch"] = relationship(back_populates="lines")

    __table_args__ = (
        CheckConstraint("debit >= 0 AND credit >= 0", name="non_negative"),
        CheckConstraint("(debit = 0 AND credit > 0) OR (credit = 0 AND debit > 0)", name="one_sided"),
    )


class GlPostingIssue(Base):
    """
    A business event whose automatic journal could not be posted (closed period, missing
    account, inconsistent document ...). Kept until a retry posts it; never silently dropped.
    """
    __tablename__ = "gl_posting_issues"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_id: Mapped[str] = mapped_column(String(40), nullable=False)
    source_key: Mapped[str] = mapped_column(String(120), nullable=False)
    event_date: Mapped[Optional[date]] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="OPEN", nullable=False)   # OPEN, RESOLVED
    error: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    journal_entry_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("journal_entries.id", ondelete="SET NULL"))

    __table_args__ = (
        UniqueConstraint("pharmacy_id", "source_key", name="uq_gl_posting_issues_key"),
        Index("ix_gl_posting_issues_status", "pharmacy_id", "status"),
        CheckConstraint("status IN ('OPEN','RESOLVED')", name="status_valid"),
    )


class PaymentSettlement(Base):
    """
    UPI / card / other-gateway money moving from its clearing account into the bank,
    net of the provider's fees. Posted to the GL; corrected only by reversal.
    """
    __tablename__ = "payment_settlements"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False)
    settlement_number: Mapped[str] = mapped_column(String(30), nullable=False)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(64))
    clearing_key: Mapped[str] = mapped_column(String(30), nullable=False)        # UPI_CLEARING, CARD_CLEARING, OTHER_CLEARING
    bank_account_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("ledger_accounts.id", ondelete="RESTRICT"), nullable=False)
    settlement_date: Mapped[date] = mapped_column(Date, nullable=False)
    gross_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    fee_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=ZERO, nullable=False)
    fee_gst_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=ZERO, nullable=False)
    net_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    reference: Mapped[Optional[str]] = mapped_column(String(100))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="POSTED", nullable=False)
    created_by: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    reversed_by: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="SET NULL"))
    reversed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reversal_reason: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("pharmacy_id", "settlement_number", name="uq_payment_settlements_number"),
        UniqueConstraint("pharmacy_id", "idempotency_key", name="uq_payment_settlements_idem"),
        CheckConstraint("gross_amount > 0 AND fee_amount >= 0 AND fee_gst_amount >= 0", name="amounts_valid"),
        CheckConstraint("net_amount = gross_amount - fee_amount - fee_gst_amount AND net_amount >= 0", name="net_valid"),
        CheckConstraint("status IN ('POSTED','REVERSED')", name="status_valid"),
    )


class BankStatementLine(Base):
    """One line of an imported bank statement, matched to at most one bank journal line."""
    __tablename__ = "bank_statement_lines"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False)
    account_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("ledger_accounts.id", ondelete="RESTRICT"), nullable=False)
    import_batch: Mapped[str] = mapped_column(String(40), nullable=False)
    line_hash: Mapped[str] = mapped_column(String(64), nullable=False)       # Duplicate-import guard
    statement_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    reference: Mapped[Optional[str]] = mapped_column(String(100))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)  # + money in, - money out
    status: Mapped[str] = mapped_column(String(20), default="UNMATCHED", nullable=False)   # UNMATCHED, MATCHED
    matched_line_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("journal_lines.id", ondelete="RESTRICT"))
    matched_by: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="SET NULL"))
    matched_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("pharmacy_id", "account_id", "line_hash", name="uq_bank_statement_lines_hash"),
        UniqueConstraint("matched_line_id", name="uq_bank_statement_lines_match"),
        Index("ix_bank_statement_lines_account", "pharmacy_id", "account_id", "status"),
        CheckConstraint("amount <> 0", name="amount_nonzero"),
        CheckConstraint("status IN ('UNMATCHED','MATCHED')", name="status_valid"),
    )
