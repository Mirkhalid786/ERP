"""
Shared helpers for Phase 4 sales services: money rounding, phone numbers,
financial-year numbering, permissions, ledger posting and invoice settlement.
"""
import hashlib
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Optional

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.auth import Permission, RolePermission, User
from app.models.sales import (
    CashShift, Customer, CustomerLedgerEntry, CustomerPayment, PaymentAllocation, SalesInvoice,
)
from app.services import app_settings
from app.services.numbering import next_document_number

ZERO = Decimal("0.00")
TWO = Decimal("0.01")
FOUR = Decimal("0.0001")

PAYMENT_METHODS = ("CASH", "UPI", "CARD", "BANK_TRANSFER", "OTHER")
NON_CASH_METHODS = ("UPI", "CARD", "BANK_TRANSFER", "OTHER")


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(TWO, rounding=ROUND_HALF_UP)


def four(value) -> Decimal:
    return Decimal(str(value)).quantize(FOUR, rounding=ROUND_HALF_UP)


from app.core.clock import BUSINESS_TZ, day_bounds, local_date, today_local, utcnow  # noqa: E402,F401  (re-exported)


# ── Phone numbers ──────────────────────────────────────────────────────────────

def normalize_mobile(raw: Optional[str], default_country_code: str = "91") -> Optional[str]:
    """
    Normalise a phone number to international digits without '+', e.g. "919876543210".
    Ten-digit Indian mobiles (starting 6-9) get the country code added.
    Raises 422 for anything that is not a plausible E.164 number.
    """
    if raw is None:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    has_plus = cleaned.startswith("+")
    digits = re.sub(r"[\s\-().]", "", cleaned.lstrip("+"))
    if not digits.isdigit():
        raise HTTPException(status_code=422, detail="Mobile number may contain only digits, spaces, '+' and '-'")
    if digits.startswith("00"):
        digits, has_plus = digits[2:], True
    if not has_plus:
        if len(digits) == 11 and digits.startswith("0"):
            digits = digits[1:]
        if len(digits) == 10:
            if default_country_code == "91" and digits[0] not in "6789":
                raise HTTPException(status_code=422, detail="Indian mobile numbers start with 6, 7, 8 or 9")
            digits = default_country_code + digits
    if not 10 <= len(digits) <= 15 or digits.startswith("0"):
        raise HTTPException(status_code=422, detail="Enter a valid mobile number, e.g. 9876543210 or +91 98765 43210")
    if digits.startswith("91") and (len(digits) != 12 or digits[2] not in "6789"):
        raise HTTPException(status_code=422, detail="Indian mobile numbers have 10 digits starting with 6-9")
    return digits


# ── Numbering ──────────────────────────────────────────────────────────────────

async def financial_year_label(db: AsyncSession, pharmacy_id: int, on: date) -> str:
    start_month = await app_settings.get_setting(db, pharmacy_id, "invoice.financial_year_start_month", "4")
    try:
        start_month_int = int(start_month)
    except ValueError:
        start_month_int = 4
    start_year = on.year if on.month >= start_month_int else on.year - 1
    if start_month_int == 1:
        return f"{start_year}"
    return f"{start_year % 100:02d}-{(start_year + 1) % 100:02d}"


async def next_sales_number(
    db: AsyncSession, pharmacy_id: int, doc_type: str, prefix_key: str, default_prefix: str,
    number_column, pharmacy_column,
) -> str:
    """
    GST-style numbers such as INV/26-27/00001: unique, consecutive, reset each financial
    year when invoice.number_reset_on_fy is true. Max 16 characters (GST rule 46).
    """
    prefix = (await app_settings.get_setting(db, pharmacy_id, prefix_key, default_prefix)).strip().upper() or default_prefix
    reset = (await app_settings.get_setting(db, pharmacy_id, "invoice.number_reset_on_fy", "true")).lower() == "true"
    if reset:
        prefix = f"{prefix}/{await financial_year_label(db, pharmacy_id, today_local())}"
    number = await next_document_number(
        db, pharmacy_id, doc_type, prefix, number_column, pharmacy_column, separator="/", width=5,
    )
    if len(number) > 16:
        raise HTTPException(
            status_code=422,
            detail=f"Document number '{number}' is longer than the 16 characters GST allows; shorten the prefix in settings",
        )
    return number


# ── Idempotency ────────────────────────────────────────────────────────────────

def request_fingerprint(payload: dict) -> str:
    """Stable hash of a request body (minus its idempotency key) to detect key reuse with different data."""
    body = {k: v for k, v in payload.items() if k != "idempotency_key"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


# ── Permissions ────────────────────────────────────────────────────────────────

async def user_permissions(db: AsyncSession, user: User) -> set:
    rows = await db.execute(
        select(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id == user.role_id)
    )
    return {r[0] for r in rows.fetchall()}


def require(perms: set, code: str, action: str) -> None:
    if code not in perms:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permission '{code}' is required to {action}",
        )


# ── Shifts ─────────────────────────────────────────────────────────────────────

async def open_shift_for(db: AsyncSession, pharmacy_id: int, user_id: int) -> Optional[CashShift]:
    return (await db.execute(
        select(CashShift).where(
            CashShift.pharmacy_id == pharmacy_id,
            CashShift.user_id == user_id,
            CashShift.status == "OPEN",
        )
    )).scalar_one_or_none()


async def shift_for_cash(db: AsyncSession, pharmacy_id: int, user_id: int, involves_cash: bool) -> Optional[int]:
    shift = await open_shift_for(db, pharmacy_id, user_id)
    if involves_cash and shift is None and await app_settings.get_bool(db, pharmacy_id, "pos.require_open_shift"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Open a cash shift (Day-end page) before taking or returning cash",
        )
    return shift.id if shift else None


# ── Customers & ledger ─────────────────────────────────────────────────────────

async def lock_customer(db: AsyncSession, pharmacy_id: int, customer_id: int) -> Customer:
    """Row-lock the customer: serialises credit checks and payments for one account."""
    customer = (await db.execute(
        select(Customer).where(Customer.id == customer_id, Customer.pharmacy_id == pharmacy_id).with_for_update()
    )).scalar_one_or_none()
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


async def get_customer(db: AsyncSession, pharmacy_id: int, customer_id: int) -> Customer:
    """Plain (non-locking) lookup for reads."""
    customer = (await db.execute(
        select(Customer).where(Customer.id == customer_id, Customer.pharmacy_id == pharmacy_id)
    )).scalar_one_or_none()
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


async def customer_balance(db: AsyncSession, pharmacy_id: int, customer_id: int) -> Decimal:
    """Outstanding = Σ(debit - credit). Positive: customer owes; negative: advance credit."""
    value = (await db.execute(
        select(func.coalesce(func.sum(CustomerLedgerEntry.debit - CustomerLedgerEntry.credit), 0))
        .where(CustomerLedgerEntry.pharmacy_id == pharmacy_id, CustomerLedgerEntry.customer_id == customer_id)
    )).scalar_one()
    return money(value)


def post_ledger(
    db: AsyncSession, *, pharmacy_id: int, customer_id: int, entry_type: str, user_id: int,
    reference: str, description: str, debit: Decimal = ZERO, credit: Decimal = ZERO,
    invoice_id: Optional[int] = None, payment_id: Optional[int] = None, return_id: Optional[int] = None,
    refund_id: Optional[int] = None, reason: Optional[str] = None,
) -> CustomerLedgerEntry:
    entry = CustomerLedgerEntry(
        pharmacy_id=pharmacy_id, customer_id=customer_id, entry_type=entry_type,
        debit=money(debit), credit=money(credit), invoice_id=invoice_id, payment_id=payment_id,
        return_id=return_id, refund_id=refund_id, reference=reference, description=description,
        reason=reason, created_by=user_id,
    )
    db.add(entry)
    return entry


async def payment_unapplied(db: AsyncSession, payment: CustomerPayment) -> Decimal:
    allocated = (await db.execute(
        select(func.coalesce(func.sum(PaymentAllocation.amount), 0)).where(
            PaymentAllocation.payment_id == payment.id, PaymentAllocation.is_reversed.is_(False),
        )
    )).scalar_one()
    return money(Decimal(str(payment.amount)) - Decimal(str(allocated)) - Decimal(str(payment.refunded_amount)))


# ── Invoice settlement ─────────────────────────────────────────────────────────

def refresh_settlement(invoice: SalesInvoice) -> None:
    """Recompute balance and payment status from the settlement fields."""
    balance = money(
        Decimal(str(invoice.grand_total)) - Decimal(str(invoice.amount_paid))
        - Decimal(str(invoice.return_credit_applied)) - Decimal(str(invoice.written_off_amount))
    )
    if balance < ZERO:
        raise HTTPException(status_code=409, detail=f"Invoice {invoice.invoice_number} would be over-settled")
    invoice.balance_due = balance
    if invoice.status == "VOIDED":
        invoice.balance_due = ZERO
    if invoice.balance_due == ZERO:
        invoice.payment_status = "PAID"
    elif invoice.amount_paid > ZERO or invoice.return_credit_applied > ZERO or invoice.written_off_amount > ZERO:
        invoice.payment_status = "PARTIALLY_PAID"
    else:
        invoice.payment_status = "UNPAID"


def allocate(
    db: AsyncSession, *, payment: CustomerPayment, invoice: SalesInvoice, amount: Decimal, user_id: int,
) -> PaymentAllocation:
    amount = money(amount)
    if amount <= ZERO:
        raise HTTPException(status_code=422, detail="Allocation amount must be positive")
    if invoice.status != "FINALIZED":
        raise HTTPException(status_code=409, detail=f"Invoice {invoice.invoice_number} is {invoice.status.lower()}")
    if amount > invoice.balance_due:
        raise HTTPException(
            status_code=422,
            detail=f"₹{amount} exceeds the ₹{invoice.balance_due} still due on {invoice.invoice_number}",
        )
    row = PaymentAllocation(payment_id=payment.id, invoice_id=invoice.id, amount=amount, created_by=user_id)
    db.add(row)
    invoice.amount_paid = money(Decimal(str(invoice.amount_paid)) + amount)
    refresh_settlement(invoice)
    return row


def overdue_days(invoice: SalesInvoice, on: Optional[date] = None) -> int:
    if not invoice.due_date or invoice.balance_due <= ZERO:
        return 0
    return max(0, ((on or today_local()) - invoice.due_date).days)


def default_due_date(terms_days: int, on: Optional[date] = None) -> date:
    return (on or today_local()) + timedelta(days=terms_days)


def ensure_amount(value, field: str) -> Decimal:
    amount = money(value)
    if amount <= ZERO:
        raise HTTPException(status_code=422, detail=f"{field} must be greater than zero")
    return amount


def methods_label(methods: Iterable[str]) -> str:
    return ", ".join(sorted(set(methods)))
