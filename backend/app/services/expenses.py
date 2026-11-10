"""
Business expenses (rent, salaries, electricity, courier, ...), recorded operationally.

* Each expense has a category, date, amount (GST included, GST part shown separately),
  payee, payment method and reference. Bank / UPI / cheque need a reference.
* Approval: a user with `expenses.approve` records an expense as APPROVED; anyone else
  creates PENDING_APPROVAL, which an approver approves or rejects. Only APPROVED
  expenses count as money spent.
* Cash from the POS drawer leaves immediately, so it needs `expenses.approve` and the
  payer's open shift; it then lowers that shift's expected cash. Office cash and bank
  payments never touch the drawer.
* Expenses are never edited or deleted. An approved expense is corrected by reversing
  it (separate permission + reason) and recording it again.
* Approved expenses (and reversals) are posted to the GL by gl_posting, to the account
  whose code is the category's `account_code_hint` (Miscellaneous Expenses if none).
"""
import json
from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import false, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.clock import today_local
from app.models.auth import AuditLog, User
from app.models.payables import CASH_SOURCES, Expense, ExpenseCategory
from app.models.purchasing import Supplier
from app.services.numbering import next_document_number
from app.services import gl_posting
from app.services.sales_common import (
    ZERO, financial_year_label, money, open_shift_for, request_fingerprint, require, utcnow,
)

REFERENCE_REQUIRED = ("BANK_TRANSFER", "UPI", "CHEQUE")

DEFAULT_CATEGORIES = [
    # code, name, suggested GL account code (Stage 5A chart)
    ("RENT", "Rent", "5210"),
    ("SALARY", "Salaries & wages", "5220"),
    ("ELECTRICITY", "Electricity & water", "5230"),
    ("PHONE_INTERNET", "Telephone & internet", "5240"),
    ("TRANSPORT", "Courier, transport & delivery", "5250"),
    ("REPAIRS", "Repairs & maintenance", "5260"),
    ("STATIONERY", "Stationery & office supplies", "5270"),
    ("BANK_CHARGES", "Bank charges", "5280"),
    ("STAFF_WELFARE", "Staff tea, snacks & welfare", "5290"),
    ("CLEANING", "Cleaning & housekeeping", "5290"),
    ("LICENCE_FEES", "Licence & registration fees", "5290"),
    ("PROFESSIONAL_FEES", "Accountant & professional fees", "5290"),
    ("MISC", "Miscellaneous", "5290"),
]


def _audit(db: AsyncSession, user: User, action: str, entity_type: str, entity_id, details: dict, reason: str = None):
    db.add(AuditLog(pharmacy_id=user.pharmacy_id, user_id=user.id, action=action, entity_type=entity_type,
                    entity_id=str(entity_id), reason=reason, metadata_json=json.dumps(details, default=str)))


# ── Categories ─────────────────────────────────────────────────────────────────

async def ensure_categories(db: AsyncSession, pharmacy_id: int) -> None:
    """Seed the default categories once for a pharmacy (also done by migration 008)."""
    have = set((await db.execute(select(ExpenseCategory.code).where(ExpenseCategory.pharmacy_id == pharmacy_id))).scalars())
    if have:
        return
    try:
        async with db.begin_nested():
            db.add_all([ExpenseCategory(pharmacy_id=pharmacy_id, code=code, name=name, account_code_hint=hint)
                        for code, name, hint in DEFAULT_CATEGORIES])
            await db.flush()
    except IntegrityError:
        pass   # Seeded concurrently by another request


async def list_categories(db: AsyncSession, pharmacy_id: int) -> list:
    await ensure_categories(db, pharmacy_id)
    return list((await db.execute(select(ExpenseCategory).where(ExpenseCategory.pharmacy_id == pharmacy_id)
                                  .order_by(ExpenseCategory.name))).scalars().all())


async def save_category(db: AsyncSession, user: User, category_id: Optional[int], body) -> ExpenseCategory:
    pid = user.pharmacy_id
    await ensure_categories(db, pid)
    if category_id is None:
        code = body.code.strip().upper().replace(" ", "_")
        if (await db.execute(select(ExpenseCategory.id).where(ExpenseCategory.pharmacy_id == pid,
                                                             ExpenseCategory.code == code))).scalar_one_or_none():
            raise HTTPException(status_code=409, detail=f"Category code {code} already exists")
        cat = ExpenseCategory(pharmacy_id=pid, code=code, name=body.name.strip(), description=body.description,
                              account_code_hint=body.account_code_hint)
        db.add(cat)
        await db.flush()
        _audit(db, user, "expense_category.create", "expense_category", cat.id, {"code": code, "name": cat.name})
        return cat
    cat = (await db.execute(select(ExpenseCategory).where(ExpenseCategory.id == category_id,
                                                         ExpenseCategory.pharmacy_id == pid))).scalar_one_or_none()
    if cat is None:
        raise HTTPException(status_code=404, detail="Category not found")
    before = {"name": cat.name, "is_active": cat.is_active, "account_code_hint": cat.account_code_hint}
    cat.name = body.name.strip()
    cat.description = body.description
    cat.account_code_hint = body.account_code_hint
    if body.is_active is not None:
        cat.is_active = body.is_active
    _audit(db, user, "expense_category.update", "expense_category", cat.id, {
        "before": before, "after": {"name": cat.name, "is_active": cat.is_active, "account_code_hint": cat.account_code_hint}})
    await db.flush()
    return cat


# ── Expenses ───────────────────────────────────────────────────────────────────

async def get_expense(db: AsyncSession, pharmacy_id: int, expense_id: int, lock: bool = False) -> Expense:
    stmt = select(Expense).options(selectinload(Expense.category)).where(
        Expense.id == expense_id, Expense.pharmacy_id == pharmacy_id)
    if lock:
        stmt = stmt.with_for_update()
    expense = (await db.execute(stmt)).scalar_one_or_none()
    if expense is None:
        raise HTTPException(status_code=404, detail="Expense not found")
    return expense


async def record_expense(db: AsyncSession, user: User, perms: set, body) -> tuple:
    require(perms, "expenses.create", "record expenses")
    pid = user.pharmacy_id
    fingerprint = request_fingerprint(body.model_dump(mode="json"))
    found = (await db.execute(select(Expense).where(Expense.pharmacy_id == pid,
                                                    Expense.idempotency_key == body.idempotency_key))).scalar_one_or_none()
    if found:
        if found.request_hash != fingerprint:
            raise HTTPException(status_code=409, detail="This idempotency key was already used for a different expense")
        return await get_expense(db, pid, found.id), True

    category = (await db.execute(select(ExpenseCategory).where(ExpenseCategory.id == body.category_id,
                                                              ExpenseCategory.pharmacy_id == pid))).scalar_one_or_none()
    if category is None or not category.is_active:
        raise HTTPException(status_code=422, detail="Choose an active expense category")
    amount, gst = money(body.amount), money(body.gst_amount or 0)
    if amount <= ZERO:
        raise HTTPException(status_code=422, detail="Amount must be more than 0")
    if gst < ZERO or gst > amount:
        raise HTTPException(status_code=422, detail="GST must be between 0 and the total amount")
    if body.expense_date > today_local():
        raise HTTPException(status_code=422, detail="Expense date cannot be in the future")
    payee = body.payee.strip()
    if body.supplier_id is not None and (await db.execute(select(Supplier.id).where(
            Supplier.id == body.supplier_id, Supplier.pharmacy_id == pid))).scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Supplier not found")

    can_approve = "expenses.approve" in perms
    method, shift_id = body.payment_method, None
    if method == "CASH":
        if body.cash_source not in CASH_SOURCES:
            raise HTTPException(status_code=422, detail="Say whether the cash came from the POS drawer or office cash")
        if body.cash_source == "DRAWER":
            if not can_approve:
                raise HTTPException(status_code=403, detail="Paying an expense from the cash drawer needs a manager "
                                                            "with 'expenses.approve'")
            if body.expense_date != today_local():
                raise HTTPException(status_code=422, detail="Drawer cash can only be paid today, from an open shift")
            shift = await open_shift_for(db, pid, user.id)
            if shift is None:
                raise HTTPException(status_code=409, detail="Open your cash shift before paying from the drawer")
            shift_id = shift.id
    elif body.cash_source:
        raise HTTPException(status_code=422, detail="Cash source applies to cash payments only")
    reference = (body.reference or "").strip() or None
    if method in REFERENCE_REQUIRED and not reference:
        raise HTTPException(status_code=422, detail="Enter the payment reference (UTR / cheque number)")
    bill = (body.bill_number or "").strip() or None

    if not body.confirm_duplicate:
        live = Expense.status.in_(("PENDING_APPROVAL", "APPROVED"))
        dup = (await db.execute(select(Expense.expense_number).where(
            Expense.pharmacy_id == pid, live, func.lower(Expense.payee) == payee.lower(),
            ((Expense.amount == amount) & (Expense.expense_date == body.expense_date))
            | ((Expense.bill_number == bill) if bill else false())))).scalars().first()
        if dup:
            raise HTTPException(status_code=409, detail=f"Expense {dup} for {payee} looks the same (same bill number, or same "
                                                        "amount and date); confirm if this is a separate expense")

    fy = await financial_year_label(db, pid, body.expense_date)
    number = await next_document_number(db, pid, "EXPENSE", f"EXP/{fy}", Expense.expense_number, Expense.pharmacy_id,
                                        separator="/", width=5)
    expense = Expense(
        pharmacy_id=pid, expense_number=number, idempotency_key=body.idempotency_key, request_hash=fingerprint,
        category_id=category.id, expense_date=body.expense_date, amount=amount, gst_amount=gst, payee=payee,
        payee_gstin=(body.payee_gstin or "").strip().upper() or None, supplier_id=body.supplier_id, bill_number=bill,
        payment_method=method, cash_source=body.cash_source, shift_id=shift_id, reference=reference,
        notes=(body.notes or "").strip() or None, status="APPROVED" if can_approve else "PENDING_APPROVAL",
        created_by=user.id,
    )
    if can_approve:
        expense.approved_by, expense.approved_at = user.id, utcnow()
    db.add(expense)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail="This expense was already recorded (duplicate submission)")
    _audit(db, user, "expense.create", "expense", expense.id, {
        "number": number, "category": category.code, "amount": amount, "gst": gst, "payee": payee, "method": method,
        "cash_source": body.cash_source, "status": expense.status})
    if expense.status == "APPROVED":
        await gl_posting.record(db, pid, user.id, "EXPENSE", expense.id)
    return await get_expense(db, pid, expense.id), False


async def decide(db: AsyncSession, user: User, perms: set, expense_id: int, approve: bool, note: Optional[str]) -> Expense:
    require(perms, "expenses.approve", "approve or reject expenses")
    expense = await get_expense(db, user.pharmacy_id, expense_id, lock=True)
    if expense.status != "PENDING_APPROVAL":
        raise HTTPException(status_code=409, detail=f"Expense {expense.expense_number} is {expense.status.lower()}")
    if not approve and not (note or "").strip():
        raise HTTPException(status_code=422, detail="Give a reason for rejecting the expense")
    expense.status = "APPROVED" if approve else "REJECTED"
    expense.approved_by, expense.approved_at = user.id, utcnow()
    expense.decision_note = (note or "").strip() or None
    _audit(db, user, "expense.approve" if approve else "expense.reject", "expense", expense.id,
           {"number": expense.expense_number, "amount": expense.amount}, reason=note)
    await db.flush()
    if approve:
        await gl_posting.record(db, user.pharmacy_id, user.id, "EXPENSE", expense.id)
    return expense


async def reverse(db: AsyncSession, user: User, perms: set, expense_id: int, reason: str) -> Expense:
    require(perms, "expenses.reverse", "reverse expenses")
    expense = await get_expense(db, user.pharmacy_id, expense_id, lock=True)
    if expense.status != "APPROVED":
        raise HTTPException(status_code=409, detail=f"Only approved expenses can be reversed; this one is {expense.status.lower()}")
    expense.status = "REVERSED"
    expense.reversed_by, expense.reversed_at, expense.reversal_reason = user.id, utcnow(), reason.strip()
    _audit(db, user, "expense.reverse", "expense", expense.id,
           {"number": expense.expense_number, "amount": expense.amount}, reason=reason)
    await db.flush()
    await gl_posting.record(db, user.pharmacy_id, user.id, "EXPENSE_REVERSAL", expense.id)
    return expense


async def list_expenses(db: AsyncSession, pharmacy_id: int, *, date_from: Optional[date], date_to: Optional[date],
                        category_id: Optional[int], status: Optional[str], limit: int, offset: int) -> dict:
    stmt = select(Expense).options(selectinload(Expense.category)).where(Expense.pharmacy_id == pharmacy_id)
    if date_from:
        stmt = stmt.where(Expense.expense_date >= date_from)
    if date_to:
        stmt = stmt.where(Expense.expense_date <= date_to)
    if category_id:
        stmt = stmt.where(Expense.category_id == category_id)
    if status:
        stmt = stmt.where(Expense.status == status.upper())
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    approved = stmt.where(Expense.status == "APPROVED").subquery()
    by_category = [{"category": name, "amount": money(amount), "count": count} for name, amount, count in (await db.execute(
        select(ExpenseCategory.name, func.sum(approved.c.amount), func.count(approved.c.id))
        .join(approved, approved.c.category_id == ExpenseCategory.id).group_by(ExpenseCategory.name)
        .order_by(ExpenseCategory.name))).all()]
    rows = (await db.execute(stmt.order_by(Expense.expense_date.desc(), Expense.id.desc()).limit(limit).offset(offset))).scalars().all()
    return {"total": total, "items": list(rows), "approved_by_category": by_category,
            "approved_total": money(sum((r["amount"] for r in by_category), ZERO))}
