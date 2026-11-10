"""
Cashier shifts and day-end cash reconciliation.

Expected drawer cash =
    opening cash
  + cash received for sales      (amount applied, i.e. tendered minus change)
  + cash collections             (later udhaar payments)
  - cash refunds
  - cash paid out + cash paid in   (categorised cash_movements)
  - supplier payments and expenses paid from the drawer during the shift
Only CASH counts. UPI, card, bank transfer and credit are listed separately and
never added to the physical cash figure.
"""
import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import List

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth import AuditLog, User
from app.models.payables import Expense, SupplierPayment
from app.models.sales import CashMovement, CashShift, CustomerPayment, CustomerRefund, SalesInvoice
from app.schemas.sales import ShiftSummary
from app.services import app_settings
from app.services import gl_posting
from app.services.sales import _user_names
from app.services.sales_common import ZERO, day_bounds, money, open_shift_for, require, utcnow


# Drawer cash that is not a sale, collection, refund, supplier payment or expense.
# Expenses and supplier payments have their own records (and appear in the shift
# from there), so they are deliberately not cash-movement categories.
MOVEMENT_CATEGORIES = {
    "PAID_OUT": {
        "BANK_DEPOSIT": "Cash deposited into the bank",
        "OWNER_WITHDRAWAL": "Cash taken by the owner",
        "TRANSFER_TO_SAFE": "Cash moved to the safe / office cash",
        "FLOAT_TRANSFER_OUT": "Change float moved to another drawer",
        "CORRECTION_OUT": "Correction of a counting or entry error",
        "OTHER_OUT": "Other (explain in the reason)",
    },
    "PAID_IN": {
        "FLOAT_IN": "Change float added",
        "OWNER_INTRODUCED": "Cash brought in by the owner",
        "TRANSFER_FROM_SAFE": "Cash brought from the safe / office cash",
        "BANK_WITHDRAWAL": "Cash withdrawn from the bank",
        "CORRECTION_IN": "Correction of a counting or entry error",
        "OTHER_IN": "Other (explain in the reason)",
    },
}
REFERENCE_REQUIRED = ("BANK_DEPOSIT", "BANK_WITHDRAWAL")
# Only for classifying movements recorded before categories existed (no new record is created)
LEGACY_ONLY = {"PAID_OUT": {"PETTY_EXPENSE": "Petty expense paid before the Expenses module existed"}}


def _drawer_rows_counted(model, shift: CashShift, active_status: str):
    """Rows paid from this shift's drawer that still count: active ones, plus ones reversed after the shift closed."""
    counted = model.status == active_status
    if shift.closed_at is not None:
        counted = counted | ((model.status == "REVERSED") & (model.reversed_at > shift.closed_at))
    return (model.shift_id == shift.id) & (model.cash_source == "DRAWER") & counted


async def open_shift(db: AsyncSession, user: User, perms: set, opening_cash: Decimal) -> CashShift:
    require(perms, "shifts.operate", "open a cash shift")
    if await open_shift_for(db, user.pharmacy_id, user.id):
        raise HTTPException(status_code=409, detail="You already have an open shift; close it first")
    shift = CashShift(pharmacy_id=user.pharmacy_id, user_id=user.id, opening_cash=money(opening_cash))
    db.add(shift)
    await db.flush()
    db.add(AuditLog(pharmacy_id=user.pharmacy_id, user_id=user.id, action="shift.open", entity_type="cash_shift",
                    entity_id=str(shift.id), metadata_json=json.dumps({"opening_cash": str(shift.opening_cash)})))
    return shift


async def get_shift(db: AsyncSession, pharmacy_id: int, shift_id: int, lock: bool = False) -> CashShift:
    stmt = select(CashShift).where(CashShift.id == shift_id, CashShift.pharmacy_id == pharmacy_id)
    if lock:
        stmt = stmt.with_for_update()
    shift = (await db.execute(stmt)).scalar_one_or_none()
    if shift is None:
        raise HTTPException(status_code=404, detail="Shift not found")
    return shift


async def _figures(db: AsyncSession, shift: CashShift) -> dict:
    payments = (await db.execute(
        select(CustomerPayment.method, CustomerPayment.source, func.sum(CustomerPayment.amount))
        .where(CustomerPayment.shift_id == shift.id, CustomerPayment.method != "CREDIT_NOTE",
               CustomerPayment.status == "POSTED")
        .group_by(CustomerPayment.method, CustomerPayment.source)
    )).all()
    cash_sales = cash_collections = ZERO
    non_cash: dict = {}
    for method, source, amount in payments:
        amount = money(amount or 0)
        if method == "CASH":
            if source == "SALE":
                cash_sales += amount
            else:
                cash_collections += amount
        else:
            non_cash[method] = money(non_cash.get(method, ZERO) + amount)
    refunds = (await db.execute(
        select(CustomerRefund.method, func.sum(CustomerRefund.amount))
        .where(CustomerRefund.shift_id == shift.id).group_by(CustomerRefund.method)
    )).all()
    cash_refunds = ZERO
    for method, amount in refunds:
        if method == "CASH":
            cash_refunds += money(amount or 0)
        else:
            non_cash[f"{method}_REFUNDS"] = money(amount or 0)
    movements = (await db.execute(
        select(CashMovement).where(CashMovement.shift_id == shift.id).order_by(CashMovement.created_at)
    )).scalars().all()
    paid_out = money(sum((m.amount for m in movements if m.movement_type == "PAID_OUT"), ZERO))
    paid_in = money(sum((m.amount for m in movements if m.movement_type == "PAID_IN"), ZERO))
    supplier_paid = money((await db.execute(select(func.coalesce(func.sum(SupplierPayment.amount), 0)).where(
        _drawer_rows_counted(SupplierPayment, shift, "POSTED")))).scalar_one())
    expenses_paid = money((await db.execute(select(func.coalesce(func.sum(Expense.amount), 0)).where(
        _drawer_rows_counted(Expense, shift, "APPROVED")))).scalar_one())
    credit_sales = money((await db.execute(
        select(func.coalesce(func.sum(SalesInvoice.grand_total - SalesInvoice.paid_at_sale), 0))
        .where(SalesInvoice.shift_id == shift.id, SalesInvoice.status == "FINALIZED")
    )).scalar_one())
    if credit_sales > ZERO:
        non_cash["CREDIT_SALES"] = credit_sales
    expected = money(shift.opening_cash + cash_sales + cash_collections - cash_refunds - paid_out + paid_in
                     - supplier_paid - expenses_paid)
    return {
        "cash_sales": money(cash_sales), "cash_collections": money(cash_collections),
        "cash_refunds": money(cash_refunds), "paid_out": paid_out, "paid_in": paid_in,
        "supplier_payments_paid": supplier_paid, "expenses_paid": expenses_paid,
        "expected_cash": expected, "non_cash_totals": non_cash,
        "cash_movements": [{
            "id": m.id, "movement_type": m.movement_type, "category": m.category, "amount": m.amount,
            "reason": m.reason, "reference": m.reference, "created_at": m.created_at,
        } for m in movements],
    }


async def shift_summary(db: AsyncSession, shift: CashShift) -> ShiftSummary:
    figures = await _figures(db, shift)
    names = await _user_names(db, [shift.user_id, shift.reviewed_by])
    return ShiftSummary(
        id=shift.id, user_id=shift.user_id, user_name=names.get(shift.user_id), status=shift.status,
        opened_at=shift.opened_at, closed_at=shift.closed_at, opening_cash=shift.opening_cash,
        counted_cash=shift.counted_cash, variance=shift.variance, variance_note=shift.variance_note,
        needs_review=shift.needs_review, reviewed_by_name=names.get(shift.reviewed_by),
        review_note=shift.review_note,
        **{**figures, "expected_cash": shift.expected_cash if shift.status != "OPEN" else figures["expected_cash"]},
    )


async def add_cash_movement(db: AsyncSession, user: User, perms: set, shift_id: int,
                            movement_type: str, category: str, amount: Decimal, reason: str,
                            reference: str | None = None) -> CashMovement:
    require(perms, "shifts.review", "authorise cash paid out / paid in")
    categories = MOVEMENT_CATEGORIES[movement_type]
    if category not in categories:
        raise HTTPException(status_code=422, detail=(
            f"Category must be one of: {', '.join(categories)}. Record expenses and supplier payments "
            "on their own screens (choose 'cash from drawer') instead of as a paid-out"))
    reference = (reference or "").strip() or None
    if category in REFERENCE_REQUIRED and not reference:
        raise HTTPException(status_code=422, detail="Enter the bank deposit / withdrawal slip reference")
    if category.startswith("OTHER") and len(reason.strip()) < 10:
        raise HTTPException(status_code=422, detail="Explain an 'Other' cash movement in at least 10 characters")
    shift = await get_shift(db, user.pharmacy_id, shift_id, lock=True)
    if shift.status != "OPEN":
        raise HTTPException(status_code=409, detail="Shift is closed")
    move = CashMovement(pharmacy_id=user.pharmacy_id, shift_id=shift.id, movement_type=movement_type,
                        category=category, amount=money(amount), reason=reason.strip(), reference=reference,
                        authorised_by=user.id, created_by=user.id)
    db.add(move)
    await db.flush()
    db.add(AuditLog(pharmacy_id=user.pharmacy_id, user_id=user.id, action=f"shift.{movement_type.lower()}",
                    entity_type="cash_shift", entity_id=str(shift.id), reason=reason,
                    metadata_json=json.dumps({"amount": str(move.amount), "category": category,
                                              "reference": reference, "movement_id": move.id})))
    await gl_posting.record(db, user.pharmacy_id, user.id, "CASH_MOVEMENT", move.id)
    return move


async def classify_legacy_movement(db: AsyncSession, user: User, perms: set, movement_id: int,
                                   category: str, note: str) -> CashMovement:
    """Give an UNCATEGORISED (pre-categories) movement a category. Amount, shift and reason are untouched."""
    require(perms, "shifts.review", "classify cash movements")
    move = (await db.execute(select(CashMovement).where(
        CashMovement.id == movement_id, CashMovement.pharmacy_id == user.pharmacy_id).with_for_update())).scalar_one_or_none()
    if move is None:
        raise HTTPException(status_code=404, detail="Cash movement not found")
    if move.category != "UNCATEGORISED":
        raise HTTPException(status_code=409, detail="Only movements recorded before categories existed can be classified")
    allowed = {**MOVEMENT_CATEGORIES[move.movement_type], **LEGACY_ONLY.get(move.movement_type, {})}
    if category not in allowed:
        raise HTTPException(status_code=422, detail=f"Category must be one of: {', '.join(allowed)}")
    move.category = category
    db.add(AuditLog(pharmacy_id=user.pharmacy_id, user_id=user.id, action="shift.movement_classify",
                    entity_type="cash_movement", entity_id=str(move.id), reason=note,
                    metadata_json=json.dumps({"from": "UNCATEGORISED", "to": category, "amount": str(move.amount)})))
    await db.flush()
    await gl_posting.record(db, user.pharmacy_id, user.id, "CASH_MOVEMENT", move.id)
    return move


async def close_shift(db: AsyncSession, user: User, perms: set, shift_id: int,
                      counted: Decimal, note: str | None) -> CashShift:
    require(perms, "shifts.operate", "close a cash shift")
    shift = await get_shift(db, user.pharmacy_id, shift_id, lock=True)
    if shift.status != "OPEN":
        raise HTTPException(status_code=409, detail="Shift is already closed")
    if shift.user_id != user.id and "shifts.review" not in perms:
        raise HTTPException(status_code=403, detail="Only the shift owner or a manager can close this shift")
    figures = await _figures(db, shift)
    shift.expected_cash = figures["expected_cash"]
    shift.counted_cash = money(counted)
    shift.variance = money(shift.counted_cash - shift.expected_cash)
    threshold = await app_settings.get_decimal(db, user.pharmacy_id, "shift.variance_review_threshold")
    shift.needs_review = abs(shift.variance) > threshold
    if shift.variance != ZERO and not (note or "").strip():
        raise HTTPException(status_code=422, detail=f"Explain the ₹{shift.variance} cash variance")
    shift.variance_note = (note or "").strip() or None
    shift.status = "CLOSED"
    shift.closed_at = utcnow()
    shift.closed_by = user.id
    db.add(AuditLog(pharmacy_id=user.pharmacy_id, user_id=user.id, action="shift.close", entity_type="cash_shift",
                    entity_id=str(shift.id), metadata_json=json.dumps({
                        "expected": str(shift.expected_cash), "counted": str(shift.counted_cash),
                        "variance": str(shift.variance), "needs_review": shift.needs_review})))
    await db.flush()
    await gl_posting.record(db, user.pharmacy_id, user.id, "SHIFT_VARIANCE", shift.id)
    return shift


async def review_shift(db: AsyncSession, user: User, perms: set, shift_id: int, note: str) -> CashShift:
    require(perms, "shifts.review", "review cash shifts")
    shift = await get_shift(db, user.pharmacy_id, shift_id, lock=True)
    if shift.status != "CLOSED":
        raise HTTPException(status_code=409, detail="Only closed shifts can be reviewed")
    shift.status = "REVIEWED"
    shift.reviewed_by = user.id
    shift.reviewed_at = utcnow()
    shift.review_note = note.strip()
    db.add(AuditLog(pharmacy_id=user.pharmacy_id, user_id=user.id, action="shift.review", entity_type="cash_shift",
                    entity_id=str(shift.id), reason=note))
    await db.flush()
    return shift


async def list_shifts(db: AsyncSession, pharmacy_id: int, day: date | None, limit: int) -> List[CashShift]:
    stmt = select(CashShift).where(CashShift.pharmacy_id == pharmacy_id)
    if day:
        start, end = day_bounds(day)
        stmt = stmt.where(CashShift.opened_at >= start, CashShift.opened_at < end)
    return list((await db.execute(stmt.order_by(CashShift.opened_at.desc()).limit(limit))).scalars().all())


async def day_end(db: AsyncSession, pharmacy_id: int, day: date) -> dict:
    """Whole-day money picture by method, plus each shift's cash reconciliation."""
    start, end = day_bounds(day)
    by_method = {}
    for method, source, amount, count in (await db.execute(
        select(CustomerPayment.method, CustomerPayment.source, func.sum(CustomerPayment.amount), func.count())
        .where(CustomerPayment.pharmacy_id == pharmacy_id, CustomerPayment.received_at >= start,
               CustomerPayment.received_at < end, CustomerPayment.method != "CREDIT_NOTE",
               CustomerPayment.status == "POSTED")
        .group_by(CustomerPayment.method, CustomerPayment.source)
    )).all():
        row = by_method.setdefault(method, {"sales": ZERO, "collections": ZERO, "refunds": ZERO, "count": 0})
        row["sales" if source == "SALE" else "collections"] += money(amount or 0)
        row["count"] += count
    for method, amount in (await db.execute(
        select(CustomerRefund.method, func.sum(CustomerRefund.amount))
        .where(CustomerRefund.pharmacy_id == pharmacy_id, CustomerRefund.refunded_at >= start,
               CustomerRefund.refunded_at < end)
        .group_by(CustomerRefund.method)
    )).all():
        row = by_method.setdefault(method, {"sales": ZERO, "collections": ZERO, "refunds": ZERO, "count": 0})
        row["refunds"] += money(amount or 0)
    for row in by_method.values():
        row["net"] = money(row["sales"] + row["collections"] - row["refunds"])

    inv = (await db.execute(
        select(func.count(SalesInvoice.id), func.coalesce(func.sum(SalesInvoice.grand_total), 0),
               func.coalesce(func.sum(SalesInvoice.grand_total - SalesInvoice.paid_at_sale), 0))
        .where(SalesInvoice.pharmacy_id == pharmacy_id, SalesInvoice.invoice_date >= start,
               SalesInvoice.invoice_date < end, SalesInvoice.status == "FINALIZED")
    )).one()
    unshifted_cash = money((await db.execute(
        select(func.coalesce(func.sum(CustomerPayment.amount), 0)).where(
            CustomerPayment.pharmacy_id == pharmacy_id, CustomerPayment.received_at >= start,
            CustomerPayment.received_at < end, CustomerPayment.method == "CASH",
            CustomerPayment.shift_id.is_(None),
        )
    )).scalar_one())
    shifts = await list_shifts(db, pharmacy_id, day, 100)
    return {
        "date": day,
        "bills": inv[0],
        "net_sales": money(inv[1]),
        "credit_given": money(inv[2]),
        "by_method": by_method,
        "cash_outside_shifts": unshifted_cash,
        "shifts": [await shift_summary(db, s) for s in shifts],
    }
