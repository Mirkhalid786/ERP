"""
General ledger engine (Phase 5A): chart of accounts, periods, journal posting,
reversals, integrity checks, trial balance and account ledger.

Posting rules enforced here, in one place (every future posting goes through `post_entry`):
  * at least two lines; each line is one-sided, positive and rounded to paise (Decimal only);
  * total debits == total credits, re-verified from the database before the entry is posted;
  * accounts must be active leaf accounts of the same pharmacy; lines on customer / supplier
    accounts must name that party; control accounts (receivables, payables, inventory,
    advances) only accept operational postings and opening balances, not manual journals;
  * the posting date must fall in an OPEN period (row-locked against a concurrent close);
  * `source_key` is unique per pharmacy, so one source event can post only once — a retry
    returns the original entry, and a different payload under the same key is refused;
  * a posted entry is never edited or deleted: it is reversed by a mirror entry, and
    `integrity_hash` lets `integrity_check` detect any later change made outside the app.
"""
import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, List, Optional, Sequence, Tuple

from fastapi import HTTPException, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import today_local, utcnow
from app.models.accounting import (
    AccountingPeriod, FiscalYear, JournalEntry, JournalLine, LedgerAccount,
)
from app.models.auth import AuditLog, User
from app.models.purchasing import Supplier
from app.models.sales import Customer
from app.services import app_settings
from app.services.numbering import next_document_number

ZERO = Decimal("0.00")
TWO = Decimal("0.01")
POSTED_STATES = ("POSTED", "REVERSED")       # Both count in balances: a reversal is its own posted entry
ACCOUNT_TYPES = ("ASSET", "LIABILITY", "EQUITY", "INCOME", "EXPENSE")
DEBIT_NATURE = ("ASSET", "EXPENSE")


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(TWO, rounding=ROUND_HALF_UP)


class DuplicateSource(Exception):
    """Another transaction posted the same source_key first."""


# ══════════════════════════════════════════════════════════════════════════════
# Chart of accounts
# ══════════════════════════════════════════════════════════════════════════════

# (code, name, type, normal, parent_code, is_group, system_key, party_type, is_control, description)
DEFAULT_CHART = [
    ("1000", "Assets", "ASSET", "DEBIT", None, True, None, None, False, None),
    ("1100", "Current Assets", "ASSET", "DEBIT", "1000", True, None, None, False, None),
    ("1110", "Cash in Hand", "ASSET", "DEBIT", "1100", False, "CASH", None, False, "Shop cash drawer(s)"),
    ("1120", "Bank Accounts", "ASSET", "DEBIT", "1100", True, None, None, False, None),
    ("1121", "Bank - Current Account", "ASSET", "DEBIT", "1120", False, "BANK", None, False, "Main bank account; add more banks under 1120"),
    ("1130", "Payment Clearing", "ASSET", "DEBIT", "1100", True, None, None, False, "Money received but not yet settled into the bank"),
    ("1131", "UPI Settlement Clearing", "ASSET", "DEBIT", "1130", False, "UPI_CLEARING", None, False, None),
    ("1132", "Card Settlement Clearing", "ASSET", "DEBIT", "1130", False, "CARD_CLEARING", None, False, None),
    ("1133", "Other Payment Clearing", "ASSET", "DEBIT", "1130", False, "OTHER_CLEARING", None, False, None),
    ("1140", "Trade Receivables - Customers", "ASSET", "DEBIT", "1100", False, "AR", "CUSTOMER", True, "Udhaar; detail per customer"),
    ("1150", "Inventory - Medicines", "ASSET", "DEBIT", "1100", False, "INVENTORY", None, True, "Stock at batch weighted-average cost"),
    ("1160", "Input GST", "ASSET", "DEBIT", "1100", True, None, None, False, None),
    ("1161", "Input CGST", "ASSET", "DEBIT", "1160", False, "INPUT_CGST", None, False, None),
    ("1162", "Input SGST", "ASSET", "DEBIT", "1160", False, "INPUT_SGST", None, False, None),
    ("1163", "Input IGST", "ASSET", "DEBIT", "1160", False, "INPUT_IGST", None, False, None),
    ("1164", "Input GST - Pending Eligibility Review", "ASSET", "DEBIT", "1160", False, "INPUT_GST_PENDING", None, False,
     "Tax on supplier bills until the CA confirms input tax credit eligibility (GSTR-2B match)"),
    ("1170", "Supplier Advances", "ASSET", "DEBIT", "1100", False, "SUPPLIER_ADVANCES", "SUPPLIER", True, None),
    ("1180", "Prepaid Expenses & Deposits", "ASSET", "DEBIT", "1100", False, None, None, False, "e.g. rent deposit"),
    ("1200", "Fixed Assets", "ASSET", "DEBIT", "1000", True, None, None, False, None),
    ("1210", "Furniture & Fixtures", "ASSET", "DEBIT", "1200", False, None, None, False, None),
    ("1220", "Computers & Equipment", "ASSET", "DEBIT", "1200", False, None, None, False, None),
    ("1230", "Refrigeration & Cold-Chain Equipment", "ASSET", "DEBIT", "1200", False, None, None, False, None),
    ("2000", "Liabilities", "LIABILITY", "CREDIT", None, True, None, None, False, None),
    ("2100", "Current Liabilities", "LIABILITY", "CREDIT", "2000", True, None, None, False, None),
    ("2110", "Trade Payables - Suppliers", "LIABILITY", "CREDIT", "2100", False, "AP", "SUPPLIER", True, "Detail per supplier"),
    ("2115", "Goods Received Not Invoiced", "LIABILITY", "CREDIT", "2100", False, "GRNI", None, True,
     "Clears when the supplier bill for received goods is recorded; prevents double-counting inventory"),
    ("2120", "Output GST", "LIABILITY", "CREDIT", "2100", True, None, None, False, None),
    ("2121", "Output CGST", "LIABILITY", "CREDIT", "2120", False, "OUTPUT_CGST", None, False, None),
    ("2122", "Output SGST", "LIABILITY", "CREDIT", "2120", False, "OUTPUT_SGST", None, False, None),
    ("2123", "Output IGST", "LIABILITY", "CREDIT", "2120", False, "OUTPUT_IGST", None, False, None),
    ("2130", "Customer Advances & Credit Notes", "LIABILITY", "CREDIT", "2100", False, "CUSTOMER_ADVANCES", "CUSTOMER", True, None),
    ("2140", "Expenses Payable", "LIABILITY", "CREDIT", "2100", False, "EXPENSES_PAYABLE", None, False, None),
    ("2150", "TDS Payable", "LIABILITY", "CREDIT", "2100", False, "TDS_PAYABLE", None, False, "Only if TDS applies (CA to confirm)"),
    ("2160", "Refunds Payable", "LIABILITY", "CREDIT", "2100", False, "REFUNDS_PAYABLE", None, False, None),
    ("2200", "Loans", "LIABILITY", "CREDIT", "2000", True, None, None, False, None),
    ("2210", "Unsecured Loans", "LIABILITY", "CREDIT", "2200", False, None, None, False, None),
    ("3000", "Equity", "EQUITY", "CREDIT", None, True, None, None, False, None),
    ("3100", "Proprietor's Capital", "EQUITY", "CREDIT", "3000", False, "CAPITAL", None, False, None),
    ("3200", "Drawings", "EQUITY", "DEBIT", "3000", False, "DRAWINGS", None, False, "Contra-equity: owner withdrawals"),
    ("3300", "Retained Earnings", "EQUITY", "CREDIT", "3000", False, "RETAINED_EARNINGS", None, False, None),
    ("3900", "Opening Balance Difference (Suspense)", "EQUITY", "CREDIT", "3000", False, "OPENING_DIFFERENCE", None, False,
     "Unresolved difference in the opening trial balance; must be investigated and cleared"),
    ("4000", "Income", "INCOME", "CREDIT", None, True, None, None, False, None),
    ("4100", "Sales - Medicines", "INCOME", "CREDIT", "4000", False, "SALES", None, False, None),
    ("4110", "Discounts Allowed", "INCOME", "DEBIT", "4000", False, "DISCOUNT_ALLOWED", None, False,
     "Contra-income: goodwill adjustments on customer invoices"),
    ("4200", "Sales Returns", "INCOME", "DEBIT", "4000", False, "SALES_RETURNS", None, False, "Contra-income"),
    ("4300", "Other Income", "INCOME", "CREDIT", "4000", True, None, None, False, None),
    ("4310", "Purchase Discounts Received", "INCOME", "CREDIT", "4300", False, "PURCHASE_DISCOUNT", None, False, None),
    ("4320", "Rounding Off", "INCOME", "CREDIT", "4300", False, "ROUND_OFF", None, False, "Invoice round-off differences (both directions)"),
    ("4330", "Miscellaneous Income", "INCOME", "CREDIT", "4300", False, None, None, False, None),
    ("5000", "Expenses", "EXPENSE", "DEBIT", None, True, None, None, False, None),
    ("5100", "Cost of Goods Sold", "EXPENSE", "DEBIT", "5000", False, "COGS", None, False, None),
    ("5110", "Purchase Price Variance", "EXPENSE", "DEBIT", "5000", False, "PURCHASE_VARIANCE", None, False,
     "Supplier bill vs goods-receipt cost differences, freight and other bill charges"),
    ("5150", "Inventory Write-offs (Expired / Damaged)", "EXPENSE", "DEBIT", "5000", False, "INVENTORY_WRITE_OFF", None, False, None),
    ("5160", "Stock Adjustment Differences", "EXPENSE", "DEBIT", "5000", False, "STOCK_ADJUSTMENT", None, False,
     "Physical count gains / losses"),
    ("5170", "Cash Shortages & Excess", "EXPENSE", "DEBIT", "5000", False, "CASH_OVER_SHORT", None, False,
     "Drawer count differences and cash corrections"),
    ("5180", "Bad Debts Written Off", "EXPENSE", "DEBIT", "5000", False, "BAD_DEBTS", None, False, None),
    ("5200", "Operating Expenses", "EXPENSE", "DEBIT", "5000", True, None, None, False, None),
    ("5210", "Rent", "EXPENSE", "DEBIT", "5200", False, None, None, False, None),
    ("5220", "Salaries & Wages", "EXPENSE", "DEBIT", "5200", False, None, None, False, None),
    ("5230", "Electricity & Utilities", "EXPENSE", "DEBIT", "5200", False, None, None, False, None),
    ("5240", "Internet & Telephone", "EXPENSE", "DEBIT", "5200", False, None, None, False, None),
    ("5250", "Transport & Delivery", "EXPENSE", "DEBIT", "5200", False, None, None, False, None),
    ("5260", "Repairs & Maintenance", "EXPENSE", "DEBIT", "5200", False, None, None, False, None),
    ("5270", "Office Supplies & Stationery", "EXPENSE", "DEBIT", "5200", False, None, None, False, None),
    ("5280", "Bank Charges", "EXPENSE", "DEBIT", "5200", False, "BANK_CHARGES", None, False, None),
    ("5290", "Miscellaneous Expenses", "EXPENSE", "DEBIT", "5200", False, None, None, False, None),
]
REQUIRED_SYSTEM_KEYS = {row[6] for row in DEFAULT_CHART if row[6]}


async def accounts_by_key(db: AsyncSession, pharmacy_id: int) -> dict:
    rows = (await db.execute(
        select(LedgerAccount).where(LedgerAccount.pharmacy_id == pharmacy_id, LedgerAccount.system_key.is_not(None))
    )).scalars().all()
    return {a.system_key: a for a in rows}


async def system_account(db: AsyncSession, pharmacy_id: int, key: str) -> LedgerAccount:
    account = (await accounts_by_key(db, pharmacy_id)).get(key)
    if account is None:
        raise HTTPException(status_code=409, detail=f"System account '{key}' is missing; complete accounting setup")
    return account


async def account_has_lines(db: AsyncSession, account_id: int) -> bool:
    return (await db.execute(select(JournalLine.id).where(JournalLine.account_id == account_id).limit(1))).first() is not None


def _audit(db: AsyncSession, user: User, action: str, entity_type: str, entity_id, details: dict, reason: str = None):
    db.add(AuditLog(
        pharmacy_id=user.pharmacy_id, user_id=user.id, action=action, entity_type=entity_type,
        entity_id=str(entity_id), reason=reason, metadata_json=json.dumps(details, default=str),
    ))


async def seed_chart(db: AsyncSession, user: User) -> int:
    """Create the starting chart (idempotent: existing codes are left alone)."""
    existing = {a.code: a for a in (await db.execute(
        select(LedgerAccount).where(LedgerAccount.pharmacy_id == user.pharmacy_id))).scalars().all()}
    created = 0
    for code, name, typ, normal, parent, group, key, party, control, desc in DEFAULT_CHART:
        if code in existing:
            continue
        account = LedgerAccount(
            pharmacy_id=user.pharmacy_id, code=code, name=name, account_type=typ, normal_balance=normal,
            parent_id=existing[parent].id if parent else None, is_group=group, system_key=key,
            party_type=party, is_control=control, description=desc, created_by=user.id,
        )
        db.add(account)
        await db.flush()
        existing[code] = account
        created += 1
    return created


async def get_account(db: AsyncSession, pharmacy_id: int, account_id: int, lock: bool = False) -> LedgerAccount:
    stmt = select(LedgerAccount).where(LedgerAccount.id == account_id, LedgerAccount.pharmacy_id == pharmacy_id)
    if lock:
        stmt = stmt.with_for_update()
    account = (await db.execute(stmt)).scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


async def create_account(db: AsyncSession, user: User, data: dict) -> LedgerAccount:
    pid = user.pharmacy_id
    code = data["code"].strip()
    if (await db.execute(select(LedgerAccount.id).where(
            LedgerAccount.pharmacy_id == pid, LedgerAccount.code == code))).first():
        raise HTTPException(status_code=409, detail=f"Account code {code} already exists")
    parent = None
    if data.get("parent_id"):
        parent = await get_account(db, pid, data["parent_id"])
        if not parent.is_group:
            raise HTTPException(status_code=422, detail=f"'{parent.name}' is a posting account, not a group")
        if parent.account_type != data["account_type"]:
            raise HTTPException(status_code=422, detail=f"A {data['account_type']} account cannot sit under {parent.account_type} group '{parent.name}'")
    normal = data.get("normal_balance") or ("DEBIT" if data["account_type"] in DEBIT_NATURE else "CREDIT")
    account = LedgerAccount(
        pharmacy_id=pid, code=code, name=data["name"].strip(), account_type=data["account_type"],
        normal_balance=normal, parent_id=parent.id if parent else None, is_group=bool(data.get("is_group")),
        description=(data.get("description") or "").strip() or None, created_by=user.id,
    )
    db.add(account)
    await db.flush()
    _audit(db, user, "account.create", "ledger_account", account.id, {"code": code, "name": account.name, "type": account.account_type})
    return account


async def update_account(db: AsyncSession, user: User, account_id: int, data: dict) -> LedgerAccount:
    account = await get_account(db, user.pharmacy_id, account_id, lock=True)
    used = await account_has_lines(db, account.id)
    changes = {}
    if data.get("name") and data["name"].strip() != account.name:
        changes["name"] = [account.name, data["name"].strip()]
        account.name = data["name"].strip()
    if "description" in data:
        account.description = (data["description"] or "").strip() or None
    if data.get("code") and data["code"].strip() != account.code:
        if used:
            raise HTTPException(status_code=409, detail="The code of an account with postings cannot change")
        if (await db.execute(select(LedgerAccount.id).where(
                LedgerAccount.pharmacy_id == user.pharmacy_id, LedgerAccount.code == data["code"].strip()))).first():
            raise HTTPException(status_code=409, detail=f"Account code {data['code']} already exists")
        changes["code"] = [account.code, data["code"].strip()]
        account.code = data["code"].strip()
    if "is_active" in data and data["is_active"] is not None and data["is_active"] != account.is_active:
        if not data["is_active"]:
            if account.system_key:
                raise HTTPException(status_code=409, detail=f"'{account.name}' is used by automatic postings and cannot be deactivated")
            if used:
                raise HTTPException(status_code=409, detail=f"'{account.name}' has postings and cannot be deactivated")
            if account.is_group and (await db.execute(select(LedgerAccount.id).where(
                    LedgerAccount.parent_id == account.id, LedgerAccount.is_active.is_(True)))).first():
                raise HTTPException(status_code=409, detail="Deactivate its active sub-accounts first")
        changes["is_active"] = [account.is_active, data["is_active"]]
        account.is_active = data["is_active"]
    if changes:
        _audit(db, user, "account.update", "ledger_account", account.id, changes)
    await db.flush()
    return account


async def delete_account(db: AsyncSession, user: User, account_id: int) -> None:
    account = await get_account(db, user.pharmacy_id, account_id, lock=True)
    if account.system_key:
        raise HTTPException(status_code=409, detail="System accounts cannot be deleted")
    if await account_has_lines(db, account.id):
        raise HTTPException(status_code=409, detail="Accounts with postings cannot be deleted; deactivate is also blocked")
    if (await db.execute(select(LedgerAccount.id).where(LedgerAccount.parent_id == account.id))).first():
        raise HTTPException(status_code=409, detail="Delete or move its sub-accounts first")
    from app.models.accounting import OpeningBalanceLine
    if (await db.execute(select(OpeningBalanceLine.id).where(OpeningBalanceLine.account_id == account.id))).first():
        raise HTTPException(status_code=409, detail="The account is used in an opening-balance draft")
    _audit(db, user, "account.delete", "ledger_account", account.id, {"code": account.code, "name": account.name})
    await db.delete(account)
    await db.flush()


# ══════════════════════════════════════════════════════════════════════════════
# Fiscal years and periods
# ══════════════════════════════════════════════════════════════════════════════

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def fy_bounds(on: date, start_month: int) -> Tuple[date, date]:
    start_year = on.year if on.month >= start_month else on.year - 1
    start = date(start_year, start_month, 1)
    end = date(start_year + 1, start_month, 1) - timedelta(days=1)
    return start, end


def fy_name(start: date, end: date) -> str:
    return f"{start.year % 100:02d}-{end.year % 100:02d}" if start.year != end.year else str(start.year)


async def create_fiscal_year(db: AsyncSession, user: User, start: date, end: date,
                             pre_opening_before: Optional[date] = None) -> FiscalYear:
    pid = user.pharmacy_id
    overlap = (await db.execute(select(FiscalYear.id).where(
        FiscalYear.pharmacy_id == pid, FiscalYear.start_date <= end, FiscalYear.end_date >= start))).first()
    if overlap:
        raise HTTPException(status_code=409, detail="This fiscal year overlaps an existing one")
    fy = FiscalYear(pharmacy_id=pid, name=fy_name(start, end), start_date=start, end_date=end, created_by=user.id)
    db.add(fy)
    await db.flush()
    cursor = start
    while cursor <= end:
        nxt = date(cursor.year + (cursor.month // 12), cursor.month % 12 + 1, 1)
        p_end = min(nxt - timedelta(days=1), end)
        db.add(AccountingPeriod(
            pharmacy_id=pid, fiscal_year_id=fy.id, name=f"{MONTHS[cursor.month - 1]} {cursor.year}",
            start_date=cursor, end_date=p_end,
            status="PRE_OPENING" if pre_opening_before and p_end < pre_opening_before else "OPEN",
        ))
        cursor = nxt
    await db.flush()
    _audit(db, user, "fiscal_year.create", "fiscal_year", fy.id, {"name": fy.name, "start": start, "end": end})
    return fy


async def books_start_date(db: AsyncSession, pharmacy_id: int) -> Optional[date]:
    value = await app_settings.get_setting(db, pharmacy_id, "accounting.books_start_date", "")
    return date.fromisoformat(value) if value else None


async def setup_accounting(db: AsyncSession, user: User, books_start: date) -> dict:
    """One-time setup: starting chart, the fiscal year containing the books' start date, settings."""
    pid = user.pharmacy_id
    if await books_start_date(db, pid):
        raise HTTPException(status_code=409, detail="Accounting is already set up")
    start_month = int(await app_settings.get_setting(db, pid, "invoice.financial_year_start_month", "4") or 4)
    created = await seed_chart(db, user)
    fy_start, fy_end = fy_bounds(books_start, start_month)
    existing_fy = (await db.execute(select(FiscalYear).where(
        FiscalYear.pharmacy_id == pid, FiscalYear.start_date == fy_start))).scalar_one_or_none()
    if existing_fy is None:
        await create_fiscal_year(db, user, fy_start, fy_end, pre_opening_before=books_start)
    await app_settings.set_setting(db, pid, "accounting.books_start_date", books_start.isoformat(), user.id)
    _audit(db, user, "accounting.setup", "pharmacy", pid, {"books_start_date": books_start, "accounts_created": created})
    return {"accounts_created": created, "books_start_date": books_start, "fiscal_year": fy_name(fy_start, fy_end)}


async def period_for(db: AsyncSession, pharmacy_id: int, on: date, lock: str = "") -> AccountingPeriod:
    stmt = select(AccountingPeriod).where(
        AccountingPeriod.pharmacy_id == pharmacy_id, AccountingPeriod.start_date <= on, AccountingPeriod.end_date >= on)
    if lock == "share":
        stmt = stmt.with_for_update(read=True)
    elif lock == "update":
        stmt = stmt.with_for_update()
    period = (await db.execute(stmt)).scalar_one_or_none()
    if period is None:
        raise HTTPException(status_code=422, detail=f"No accounting period covers {on}; create the fiscal year first")
    return period


async def assert_postable(db: AsyncSession, pharmacy_id: int, on: date) -> AccountingPeriod:
    """The date must be in an OPEN period. Shared lock: a concurrent close waits for this posting."""
    period = await period_for(db, pharmacy_id, on, lock="share")
    if period.status == "PRE_OPENING":
        raise HTTPException(status_code=409, detail=f"{on} is before the books' opening date; nothing can be posted there")
    if period.status != "OPEN":
        raise HTTPException(status_code=409, detail=f"Period {period.name} is {period.status.lower()}; reopening needs authorisation")
    return period


async def close_period(db: AsyncSession, user: User, period_id: int) -> AccountingPeriod:
    period = (await db.execute(select(AccountingPeriod).where(
        AccountingPeriod.id == period_id, AccountingPeriod.pharmacy_id == user.pharmacy_id).with_for_update())).scalar_one_or_none()
    if period is None:
        raise HTTPException(status_code=404, detail="Period not found")
    if period.status != "OPEN":
        raise HTTPException(status_code=409, detail=f"Period {period.name} is {period.status.lower()}")
    drafts = (await db.execute(select(func.count(JournalEntry.id)).where(
        JournalEntry.pharmacy_id == user.pharmacy_id, JournalEntry.status == "DRAFT",
        JournalEntry.entry_date >= period.start_date, JournalEntry.entry_date <= period.end_date))).scalar_one()
    if drafts:
        raise HTTPException(status_code=409, detail=f"{drafts} draft journal(s) are dated in {period.name}; post or delete them first")
    period.status = "CLOSED"
    period.closed_by, period.closed_at = user.id, utcnow()
    _audit(db, user, "period.close", "accounting_period", period.id, {"period": period.name})
    await db.flush()
    return period


async def reopen_period(db: AsyncSession, user: User, period_id: int, reason: str) -> AccountingPeriod:
    period = (await db.execute(select(AccountingPeriod).where(
        AccountingPeriod.id == period_id, AccountingPeriod.pharmacy_id == user.pharmacy_id).with_for_update())).scalar_one_or_none()
    if period is None:
        raise HTTPException(status_code=404, detail="Period not found")
    if period.status != "CLOSED":
        raise HTTPException(status_code=409, detail=f"Only closed periods can be reopened ({period.name} is {period.status.lower()})")
    period.status = "OPEN"
    period.reopened_by, period.reopened_at, period.reopen_reason = user.id, utcnow(), reason.strip()
    _audit(db, user, "period.reopen", "accounting_period", period.id, {"period": period.name}, reason=reason)
    await db.flush()
    return period


# ══════════════════════════════════════════════════════════════════════════════
# Journal posting
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class LineIn:
    account_id: int
    debit: Decimal = ZERO
    credit: Decimal = ZERO
    party_type: Optional[str] = None
    party_id: Optional[int] = None
    description: Optional[str] = None


def lines_fingerprint(entry_date: date, lines: Sequence[LineIn]) -> str:
    body = [entry_date.isoformat()] + [
        [l.account_id, str(money(l.debit)), str(money(l.credit)), l.party_type, l.party_id] for l in lines]
    return hashlib.sha256(json.dumps(body).encode()).hexdigest()


def integrity_digest(entry: JournalEntry, lines: Iterable[JournalLine]) -> str:
    body = {
        "number": entry.entry_number, "date": entry.entry_date.isoformat(), "source_key": entry.source_key,
        "debit": str(money(entry.total_debit)), "credit": str(money(entry.total_credit)),
        "lines": [[l.line_no, l.account_id, str(money(l.debit)), str(money(l.credit)), l.party_type, l.party_id]
                  for l in sorted(lines, key=lambda x: x.line_no)],
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


async def _validate_lines(db: AsyncSession, pharmacy_id: int, lines: Sequence[LineIn], allow_control: bool) -> Tuple[Decimal, Decimal]:
    if len(lines) < 2:
        raise HTTPException(status_code=422, detail="A journal entry needs at least two lines")
    account_ids = {l.account_id for l in lines}
    accounts = {a.id: a for a in (await db.execute(select(LedgerAccount).where(
        LedgerAccount.pharmacy_id == pharmacy_id, LedgerAccount.id.in_(account_ids)))).scalars().all()}
    debit = credit = ZERO
    for i, line in enumerate(lines, start=1):
        d, c = money(line.debit), money(line.credit)
        if d < ZERO or c < ZERO:
            raise HTTPException(status_code=422, detail=f"Line {i}: amounts cannot be negative")
        if (d > ZERO) == (c > ZERO):
            raise HTTPException(status_code=422, detail=f"Line {i}: enter either a debit or a credit (not both, not zero)")
        account = accounts.get(line.account_id)
        if account is None:
            raise HTTPException(status_code=422, detail=f"Line {i}: account {line.account_id} not found")
        if account.is_group:
            raise HTTPException(status_code=422, detail=f"Line {i}: '{account.name}' is a group; post to one of its sub-accounts")
        if not account.is_active:
            raise HTTPException(status_code=422, detail=f"Line {i}: '{account.name}' is inactive")
        if account.is_control and not allow_control:
            raise HTTPException(
                status_code=422,
                detail=f"Line {i}: '{account.name}' is a control account fed by sales, purchases and stock; "
                       f"it cannot be used in a manual journal",
            )
        if account.party_type:
            if line.party_type != account.party_type or not line.party_id:
                raise HTTPException(status_code=422, detail=f"Line {i}: '{account.name}' needs a {account.party_type.lower()}")
            model = Customer if account.party_type == "CUSTOMER" else Supplier
            if not (await db.execute(select(model.id).where(model.id == line.party_id, model.pharmacy_id == pharmacy_id))).first():
                raise HTTPException(status_code=422, detail=f"Line {i}: {account.party_type.lower()} {line.party_id} not found")
        elif line.party_type or line.party_id:
            raise HTTPException(status_code=422, detail=f"Line {i}: '{account.name}' does not take a customer / supplier")
        debit += d
        credit += c
    if debit != credit:
        raise HTTPException(
            status_code=422,
            detail=f"Debits ₹{debit} and credits ₹{credit} differ by ₹{abs(debit - credit)}; a journal must balance",
        )
    return debit, credit


async def _journal_number(db: AsyncSession, pharmacy_id: int, on: date) -> str:
    period = await period_for(db, pharmacy_id, on)
    fy = (await db.execute(select(FiscalYear).where(FiscalYear.id == period.fiscal_year_id))).scalar_one()
    return await next_document_number(
        db, pharmacy_id, "JOURNAL", f"JV/{fy.name}", JournalEntry.entry_number, JournalEntry.pharmacy_id,
        separator="/", width=5,
    )


async def _finalise(db: AsyncSession, user: User, entry: JournalEntry, expected: Decimal) -> JournalEntry:
    """Re-check the stored lines, number the entry and mark it POSTED (same transaction)."""
    stored = (await db.execute(select(
        func.coalesce(func.sum(JournalLine.debit), 0), func.coalesce(func.sum(JournalLine.credit), 0), func.count(JournalLine.id),
    ).where(JournalLine.entry_id == entry.id))).one()
    if money(stored[0]) != expected or money(stored[1]) != expected or stored[2] < 2:
        raise HTTPException(status_code=500, detail="Journal verification failed; nothing was posted")
    entry.entry_number = await _journal_number(db, entry.pharmacy_id, entry.entry_date)
    entry.total_debit = entry.total_credit = expected
    entry.status = "POSTED"
    entry.posted_by, entry.posted_at = user.id, utcnow()
    lines = (await db.execute(select(JournalLine).where(JournalLine.entry_id == entry.id))).scalars().all()
    entry.integrity_hash = integrity_digest(entry, lines)
    _audit(db, user, "journal.post", "journal_entry", entry.id, {
        "number": entry.entry_number, "source": entry.source_type, "source_id": entry.source_id, "amount": expected})
    await db.flush()
    return entry


async def entry_by_source(db: AsyncSession, pharmacy_id: int, source_key: str) -> Optional[JournalEntry]:
    return (await db.execute(select(JournalEntry).where(
        JournalEntry.pharmacy_id == pharmacy_id, JournalEntry.source_key == source_key))).scalar_one_or_none()


async def post_entry(
    db: AsyncSession, user: User, *, entry_date: date, narration: str, lines: Sequence[LineIn],
    source_type: str, source_id: Optional[str], source_key: str, reference: Optional[str] = None,
    document_date: Optional[date] = None, allow_control: bool = False, reversal_of_id: Optional[int] = None,
) -> Tuple[JournalEntry, bool]:
    """
    The single posting path. Returns (entry, replayed). Everything happens in the
    caller's transaction: if anything later fails, the journal rolls back with it.
    """
    pid = user.pharmacy_id
    fingerprint = lines_fingerprint(entry_date, lines)
    existing = await entry_by_source(db, pid, source_key)
    if existing:
        if existing.request_hash != fingerprint:
            raise HTTPException(status_code=409, detail=f"Source {source_key} was already posted with different amounts")
        return existing, True
    total, _ = await _validate_lines(db, pid, lines, allow_control)
    period = await assert_postable(db, pid, entry_date)

    entry = JournalEntry(
        pharmacy_id=pid, status="DRAFT", entry_date=entry_date, document_date=document_date, period_id=period.id,
        source_type=source_type, source_id=source_id, source_key=source_key, request_hash=fingerprint,
        reference=(reference or "").strip() or None, narration=narration.strip(), created_by=user.id,
        reversal_of_id=reversal_of_id,
    )
    db.add(entry)
    try:
        async with db.begin_nested():
            await db.flush()             # Claims source_key; a concurrent duplicate waits here, then fails
    except IntegrityError as exc:
        raise DuplicateSource() from exc
    for i, line in enumerate(lines, start=1):
        db.add(JournalLine(
            entry_id=entry.id, line_no=i, account_id=line.account_id, debit=money(line.debit), credit=money(line.credit),
            party_type=line.party_type, party_id=line.party_id, description=(line.description or "").strip() or None,
        ))
    await db.flush()
    return await _finalise(db, user, entry, total), False


# ── Manual journals (draft → post) ─────────────────────────────────────────────

async def save_draft(db: AsyncSession, user: User, entry_id: Optional[int], *, entry_date: date, narration: str,
                     reference: Optional[str], lines: Sequence[LineIn], idempotency_key: Optional[str]) -> JournalEntry:
    pid = user.pharmacy_id
    await assert_postable(db, pid, entry_date)   # No drafts in closed periods (and a concurrent close waits)
    if entry_id is None:
        key = f"MANUAL:{idempotency_key}" if idempotency_key else None
        if key:
            existing = await entry_by_source(db, pid, key)
            if existing:
                return existing
        entry = JournalEntry(pharmacy_id=pid, status="DRAFT", entry_date=entry_date, source_type="MANUAL",
                             source_key=key, narration=narration.strip(), reference=(reference or "").strip() or None,
                             created_by=user.id)
        db.add(entry)
        try:
            async with db.begin_nested():
                await db.flush()
        except IntegrityError as exc:
            raise DuplicateSource() from exc
        entry.source_id = str(entry.id)
    else:
        entry = await get_entry(db, pid, entry_id, lock=True)
        if entry.status != "DRAFT" or entry.source_type != "MANUAL":
            raise HTTPException(status_code=409, detail="Only manual draft journals can be edited")
        entry.entry_date, entry.narration = entry_date, narration.strip()
        entry.reference = (reference or "").strip() or None
        for old in list(entry.lines):
            await db.delete(old)
        await db.flush()
    for i, line in enumerate(lines, start=1):
        d, c = money(line.debit), money(line.credit)
        if (d > ZERO) == (c > ZERO) or d < ZERO or c < ZERO:
            raise HTTPException(status_code=422, detail=f"Line {i}: enter either a debit or a credit (not both, not zero)")
        db.add(JournalLine(entry_id=entry.id, line_no=i, account_id=line.account_id, debit=d, credit=c,
                           party_type=line.party_type, party_id=line.party_id, description=line.description))
    await db.flush()
    _audit(db, user, "journal.draft_save", "journal_entry", entry.id, {"lines": len(lines)})
    return entry


async def post_draft(db: AsyncSession, user: User, entry_id: int) -> JournalEntry:
    entry = await get_entry(db, user.pharmacy_id, entry_id, lock=True)
    if entry.status != "DRAFT":
        raise HTTPException(status_code=409, detail=f"Journal is already {entry.status.lower()}")
    lines = [LineIn(l.account_id, l.debit, l.credit, l.party_type, l.party_id, l.description) for l in entry.lines]
    total, _ = await _validate_lines(db, user.pharmacy_id, lines, allow_control=False)
    period = await assert_postable(db, user.pharmacy_id, entry.entry_date)
    entry.period_id = period.id
    entry.request_hash = lines_fingerprint(entry.entry_date, lines)
    return await _finalise(db, user, entry, total)


async def delete_draft(db: AsyncSession, user: User, entry_id: int) -> None:
    entry = await get_entry(db, user.pharmacy_id, entry_id, lock=True)
    if entry.status != "DRAFT":
        raise HTTPException(status_code=409, detail="Posted journals are never deleted; reverse them instead")
    _audit(db, user, "journal.draft_delete", "journal_entry", entry.id, {"narration": entry.narration[:80]})
    await db.delete(entry)
    await db.flush()


async def get_entry(db: AsyncSession, pharmacy_id: int, entry_id: int, lock: bool = False) -> JournalEntry:
    from sqlalchemy.orm import selectinload
    stmt = select(JournalEntry).options(selectinload(JournalEntry.lines)).where(
        JournalEntry.id == entry_id, JournalEntry.pharmacy_id == pharmacy_id)
    if lock:
        stmt = stmt.with_for_update()
    entry = (await db.execute(stmt)).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    return entry


REVERSIBLE_HERE = ("MANUAL", "OPENING")


async def reverse_entry(db: AsyncSession, user: User, entry_id: int, reason: str,
                        reversal_date: Optional[date] = None) -> JournalEntry:
    """
    Post a mirror entry and mark the original REVERSED. The original is kept unchanged.
    Operational postings (sales, purchases…) are corrected through their own documents.
    """
    original = await get_entry(db, user.pharmacy_id, entry_id, lock=True)
    if original.status == "REVERSED":
        raise HTTPException(status_code=409, detail=f"{original.entry_number} is already reversed")
    if original.status != "POSTED":
        raise HTTPException(status_code=409, detail="Only posted entries can be reversed")
    if original.reversal_of_id:
        raise HTTPException(status_code=409, detail="A reversal entry cannot itself be reversed; post a new entry instead")
    if original.source_type not in REVERSIBLE_HERE:
        raise HTTPException(status_code=409, detail=f"{original.source_type} entries are corrected through their source document")
    on = reversal_date or today_local()
    if on < original.entry_date:
        raise HTTPException(status_code=422, detail="A reversal cannot be dated before the original entry")
    mirror = [LineIn(l.account_id, l.credit, l.debit, l.party_type, l.party_id, f"Reversal: {l.description or ''}".strip())
              for l in original.lines]
    reversal, _ = await post_entry(
        db, user, entry_date=on, narration=f"Reversal of {original.entry_number}: {reason.strip()}", lines=mirror,
        source_type="REVERSAL", source_id=str(original.id), source_key=f"REVERSAL:{original.id}",
        reference=original.entry_number, allow_control=True, reversal_of_id=original.id,
    )
    original.status = "REVERSED"
    original.reversed_by_entry_id, original.reversed_at, original.reversal_reason = reversal.id, utcnow(), reason.strip()
    _audit(db, user, "journal.reverse", "journal_entry", original.id,
           {"number": original.entry_number, "reversal": reversal.entry_number}, reason=reason)
    await db.flush()
    return reversal


# ══════════════════════════════════════════════════════════════════════════════
# Balances, trial balance, ledger, integrity
# ══════════════════════════════════════════════════════════════════════════════

def _posted_line_query(pharmacy_id: int):
    return (select(JournalLine, JournalEntry)
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .where(JournalEntry.pharmacy_id == pharmacy_id, JournalEntry.status.in_(POSTED_STATES)))


async def trial_balance(db: AsyncSession, pharmacy_id: int, date_from: Optional[date], date_to: date) -> dict:
    """Opening (before date_from), period movement and closing per posting account."""
    accounts = (await db.execute(select(LedgerAccount).where(
        LedgerAccount.pharmacy_id == pharmacy_id).order_by(LedgerAccount.code))).scalars().all()
    base = [JournalEntry.pharmacy_id == pharmacy_id, JournalEntry.status.in_(POSTED_STATES), JournalEntry.entry_date <= date_to]
    async def sums(conds):
        return {r[0]: (money(r[1] or 0), money(r[2] or 0)) for r in (await db.execute(
            select(JournalLine.account_id, func.sum(JournalLine.debit), func.sum(JournalLine.credit))
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id).where(*conds).group_by(JournalLine.account_id)
        )).all()}
    opening = await sums(base + [JournalEntry.entry_date < date_from]) if date_from else {}
    movement = await sums(base + ([JournalEntry.entry_date >= date_from] if date_from else []))
    out, totals = [], {k: ZERO for k in ("opening_debit", "opening_credit", "debit", "credit", "closing_debit", "closing_credit")}
    for a in accounts:
        if a.is_group:
            continue
        od, oc = opening.get(a.id, (ZERO, ZERO))
        md, mc = movement.get(a.id, (ZERO, ZERO))
        if not any((od, oc, md, mc)):
            continue
        o_net, c_net = od - oc, od - oc + md - mc
        row = {
            "account_id": a.id, "code": a.code, "name": a.name, "account_type": a.account_type,
            "opening_debit": max(o_net, ZERO), "opening_credit": max(-o_net, ZERO),
            "debit": md, "credit": mc,
            "closing_debit": max(c_net, ZERO), "closing_credit": max(-c_net, ZERO),
        }
        for k in totals:
            totals[k] += row[k]
        out.append(row)
    return {
        "date_from": date_from, "date_to": date_to, "rows": out, "totals": {k: money(v) for k, v in totals.items()},
        "is_balanced": totals["closing_debit"] == totals["closing_credit"] and totals["debit"] == totals["credit"],
    }


async def account_ledger(db: AsyncSession, pharmacy_id: int, account_id: int, date_from: Optional[date], date_to: date,
                         party_type: Optional[str] = None, party_id: Optional[int] = None) -> dict:
    account = await get_account(db, pharmacy_id, account_id)
    conds = [JournalEntry.pharmacy_id == pharmacy_id, JournalEntry.status.in_(POSTED_STATES),
             JournalLine.account_id == account_id, JournalEntry.entry_date <= date_to]
    if party_type:
        conds += [JournalLine.party_type == party_type, JournalLine.party_id == party_id]
    opening = ZERO
    if date_from:
        o = (await db.execute(select(func.coalesce(func.sum(JournalLine.debit - JournalLine.credit), 0))
                              .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
                              .where(*conds, JournalEntry.entry_date < date_from))).scalar_one()
        opening = money(o)
        conds.append(JournalEntry.entry_date >= date_from)
    rows = (await db.execute(select(JournalLine, JournalEntry).join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
                             .where(*conds).order_by(JournalEntry.entry_date, JournalEntry.id, JournalLine.line_no))).all()
    running, entries = opening, []
    for line, entry in rows:
        running = money(running + line.debit - line.credit)
        entries.append({
            "entry_id": entry.id, "entry_number": entry.entry_number, "entry_date": entry.entry_date,
            "status": entry.status, "source_type": entry.source_type, "narration": entry.narration,
            "description": line.description, "party_type": line.party_type, "party_id": line.party_id,
            "debit": line.debit, "credit": line.credit, "balance": running,
        })
    return {"account": {"id": account.id, "code": account.code, "name": account.name,
                        "normal_balance": account.normal_balance},
            "date_from": date_from, "date_to": date_to, "opening_balance": opening, "closing_balance": running,
            "entries": entries}


async def integrity_check(db: AsyncSession, pharmacy_id: int) -> dict:
    """Recompute every posted entry: balance, stored totals, hash, reversal links, global balance."""
    entries = (await db.execute(select(JournalEntry).where(
        JournalEntry.pharmacy_id == pharmacy_id, JournalEntry.status.in_(POSTED_STATES)))).scalars().all()
    lines = (await db.execute(select(JournalLine).join(JournalEntry, JournalEntry.id == JournalLine.entry_id).where(
        JournalEntry.pharmacy_id == pharmacy_id, JournalEntry.status.in_(POSTED_STATES)))).scalars().all()
    by_entry: dict = {}
    for l in lines:
        by_entry.setdefault(l.entry_id, []).append(l)
    problems = []
    grand_d = grand_c = ZERO
    for e in entries:
        ls = by_entry.get(e.id, [])
        d = money(sum((l.debit for l in ls), ZERO))
        c = money(sum((l.credit for l in ls), ZERO))
        grand_d += d
        grand_c += c
        if d != c:
            problems.append({"entry": e.entry_number, "problem": f"Unbalanced: debits {d}, credits {c}"})
        if d != money(e.total_debit) or c != money(e.total_credit):
            problems.append({"entry": e.entry_number, "problem": "Stored totals do not match its lines"})
        if e.integrity_hash != integrity_digest(e, ls):
            problems.append({"entry": e.entry_number, "problem": "Changed after posting (integrity hash mismatch)"})
        if e.status == "REVERSED" and not e.reversed_by_entry_id:
            problems.append({"entry": e.entry_number, "problem": "Marked reversed but has no reversal entry"})
    return {"entries_checked": len(entries), "lines_checked": len(lines), "total_debit": money(grand_d),
            "total_credit": money(grand_c), "ledger_balanced": grand_d == grand_c, "problems": problems,
            "ok": not problems and grand_d == grand_c}
