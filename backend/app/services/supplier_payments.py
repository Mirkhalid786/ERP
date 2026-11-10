"""
Supplier payments: dated, numbered, immutable records of money paid to suppliers,
allocated to finalised supplier invoices (any remainder is an advance).

Safety rules
* The supplier row is locked for the whole operation, so concurrent payments for one
  supplier are serialised and cannot over-allocate an invoice.
* `idempotency_key` (unique) makes a re-submitted form return the original payment;
  re-using a key with different data is refused.
* A bank / UPI / cheque reference can be used once per supplier and method while the
  payment is active (unique `reference_key`, cleared when the payment is reversed).
* A same-day payment of the same amount without a reference needs `confirm_duplicate`.
* Payments are never edited or deleted. Reversal (permission + reason) marks the
  payment and its allocations REVERSED and restores the invoices' outstanding amounts.
* Cash from the POS drawer is tied to the payer's open shift and lowers that shift's
  expected cash; office cash and bank methods never touch the drawer.
"""
import json
import re
from datetime import date
from decimal import Decimal
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.clock import today_local
from app.models.auth import AuditLog, User
from app.models.payables import CASH_SOURCES, SUPPLIER_PAYMENT_METHODS, SupplierPayment, SupplierPaymentAllocation
from app.models.purchasing import Supplier, SupplierInvoice, SupplierReturn
from app.services.numbering import next_document_number
from app.services import gl_posting
from app.services.sales_common import (
    ZERO, financial_year_label, money, open_shift_for, request_fingerprint, require, utcnow,
)
from app.services.supplier_invoices import outstanding, refresh_payment_cache

REFERENCE_REQUIRED = ("BANK_TRANSFER", "UPI", "CHEQUE")


def reference_key(method: str, reference: Optional[str]) -> Optional[str]:
    cleaned = re.sub(r"[^A-Z0-9]", "", (reference or "").upper())
    return f"{method}:{cleaned}" if cleaned else None


def _audit(db: AsyncSession, user: User, action: str, payment: SupplierPayment, details: dict, reason: str = None):
    db.add(AuditLog(pharmacy_id=user.pharmacy_id, user_id=user.id, action=action, entity_type="supplier_payment",
                    entity_id=str(payment.id), reason=reason, metadata_json=json.dumps(details, default=str)))


async def lock_supplier(db: AsyncSession, pharmacy_id: int, supplier_id: int) -> Supplier:
    supplier = (await db.execute(select(Supplier).where(
        Supplier.id == supplier_id, Supplier.pharmacy_id == pharmacy_id).with_for_update())).scalar_one_or_none()
    if supplier is None:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return supplier


async def get_payment(db: AsyncSession, pharmacy_id: int, payment_id: int, lock: bool = False) -> SupplierPayment:
    stmt = select(SupplierPayment).options(selectinload(SupplierPayment.allocations)).where(
        SupplierPayment.id == payment_id, SupplierPayment.pharmacy_id == pharmacy_id
    ).execution_options(populate_existing=True)
    if lock:
        stmt = stmt.with_for_update()
    payment = (await db.execute(stmt)).scalar_one_or_none()
    if payment is None:
        raise HTTPException(status_code=404, detail="Supplier payment not found")
    return payment


async def open_invoices(db: AsyncSession, pharmacy_id: int, supplier_id: int, lock: bool = False) -> List[SupplierInvoice]:
    stmt = select(SupplierInvoice).where(
        SupplierInvoice.pharmacy_id == pharmacy_id, SupplierInvoice.supplier_id == supplier_id,
        SupplierInvoice.status == "POSTED", SupplierInvoice.payment_status != "PAID",
    ).order_by(SupplierInvoice.due_date, SupplierInvoice.id)
    if lock:
        stmt = stmt.with_for_update()
    return list((await db.execute(stmt)).scalars().all())


async def unallocated(db: AsyncSession, payment: SupplierPayment) -> Decimal:
    used = money((await db.execute(select(func.coalesce(func.sum(SupplierPaymentAllocation.amount), 0)).where(
        SupplierPaymentAllocation.payment_id == payment.id, SupplierPaymentAllocation.status == "ACTIVE"))).scalar_one())
    return money(payment.amount - used) if payment.status == "POSTED" else ZERO


async def _plan(db: AsyncSession, pharmacy_id: int, supplier_id: int, available: Decimal,
                requested: Optional[list], auto: bool) -> list:
    """[(invoice, amount)] after validating against each invoice's outstanding amount."""
    invoices = {i.id: i for i in await open_invoices(db, pharmacy_id, supplier_id, lock=True)}
    plan, total = [], ZERO
    if requested:
        seen = set()
        for row in requested:
            if row.invoice_id in seen:
                raise HTTPException(status_code=422, detail="Each invoice can appear only once in the allocations")
            seen.add(row.invoice_id)
            invoice = invoices.get(row.invoice_id)
            if invoice is None:
                raise HTTPException(status_code=422, detail=f"Invoice {row.invoice_id} is not an open finalised bill of this supplier")
            amount = money(row.amount)
            due = (await outstanding(db, invoice))["outstanding"]
            if amount <= ZERO or amount > due:
                raise HTTPException(status_code=422, detail=f"Invoice {invoice.invoice_number}: allocation ₹{amount} must be "
                                                            f"more than 0 and at most the outstanding ₹{due}")
            plan.append((invoice, amount))
            total += amount
    elif auto:
        remaining = available
        for invoice in invoices.values():
            if remaining <= ZERO:
                break
            due = (await outstanding(db, invoice))["outstanding"]
            if due > ZERO:
                take = min(due, remaining)
                plan.append((invoice, take))
                remaining -= take
                total += take
    if total > available:
        raise HTTPException(status_code=422, detail=f"Allocations (₹{money(total)}) exceed the amount available (₹{available})")
    return plan


async def _apply(db: AsyncSession, user: User, payment: SupplierPayment, plan: list) -> None:
    for invoice, amount in plan:
        db.add(SupplierPaymentAllocation(payment_id=payment.id, invoice_id=invoice.id, amount=amount, created_by=user.id))
    await db.flush()
    for invoice, _ in plan:
        await refresh_payment_cache(db, invoice)


async def record_payment(db: AsyncSession, user: User, perms: set, body) -> tuple:
    require(perms, "supplier_payments.create", "record supplier payments")
    pid = user.pharmacy_id
    fingerprint = request_fingerprint(body.model_dump(mode="json"))

    async def existing():
        return (await db.execute(select(SupplierPayment).where(
            SupplierPayment.pharmacy_id == pid, SupplierPayment.idempotency_key == body.idempotency_key))).scalar_one_or_none()

    async def replay(found: SupplierPayment):
        if found.request_hash != fingerprint:
            raise HTTPException(status_code=409, detail="This idempotency key was already used for a different payment")
        return await get_payment(db, pid, found.id), True

    if (found := await existing()):
        return await replay(found)
    supplier = await lock_supplier(db, pid, body.supplier_id)
    if (found := await existing()):          # A concurrent duplicate waited on the supplier lock
        return await replay(found)

    amount = money(body.amount)
    if amount <= ZERO:
        raise HTTPException(status_code=422, detail="Payment amount must be more than 0")
    if body.payment_date > today_local():
        raise HTTPException(status_code=422, detail="Payment date cannot be in the future")
    method = body.method
    shift_id = None
    if method == "CASH":
        if body.cash_source not in CASH_SOURCES:
            raise HTTPException(status_code=422, detail="Say whether the cash came from the POS drawer or office cash")
        if body.cash_source == "DRAWER":
            if body.payment_date != today_local():
                raise HTTPException(status_code=422, detail="Drawer cash can only be paid today, from an open shift")
            require(perms, "shifts.review", "pay suppliers from the cash drawer")
            shift = await open_shift_for(db, pid, user.id)
            if shift is None:
                raise HTTPException(status_code=409, detail="Open your cash shift before paying from the drawer")
            shift_id = shift.id
    elif body.cash_source:
        raise HTTPException(status_code=422, detail="Cash source applies to cash payments only")
    reference = (body.reference or "").strip() or None
    if method in REFERENCE_REQUIRED and not reference:
        raise HTTPException(status_code=422, detail=f"Enter the {'cheque number' if method == 'CHEQUE' else 'transaction reference (UTR)'}")
    ref_key = reference_key(method, reference)
    if ref_key:
        clash = (await db.execute(select(SupplierPayment.payment_number).where(
            SupplierPayment.pharmacy_id == pid, SupplierPayment.supplier_id == supplier.id,
            SupplierPayment.reference_key == ref_key))).scalar_one_or_none()
        if clash:
            raise HTTPException(status_code=409, detail=f"Reference '{reference}' is already recorded on payment {clash}")
    elif not body.confirm_duplicate:
        same = (await db.execute(select(SupplierPayment.payment_number).where(
            SupplierPayment.pharmacy_id == pid, SupplierPayment.supplier_id == supplier.id,
            SupplierPayment.payment_date == body.payment_date, SupplierPayment.amount == amount,
            SupplierPayment.status == "POSTED"))).scalars().first()
        if same:
            raise HTTPException(status_code=409, detail=f"Payment {same} of ₹{amount} to this supplier on {body.payment_date} "
                                                        "already exists; confirm if this is a separate payment")

    plan = await _plan(db, pid, supplier.id, amount, body.allocations, body.auto_allocate)
    fy = await financial_year_label(db, pid, body.payment_date)
    number = await next_document_number(db, pid, "SUPPLIER_PAYMENT", f"SP/{fy}", SupplierPayment.payment_number,
                                        SupplierPayment.pharmacy_id, separator="/", width=5)
    payment = SupplierPayment(
        pharmacy_id=pid, payment_number=number, idempotency_key=body.idempotency_key, request_hash=fingerprint,
        supplier_id=supplier.id, payment_date=body.payment_date, method=method, cash_source=body.cash_source,
        shift_id=shift_id, amount=amount, reference=reference, reference_key=ref_key,
        bank_account=(body.bank_account or "").strip() or None, notes=(body.notes or "").strip() or None,
        created_by=user.id,
    )
    db.add(payment)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail="This payment was already recorded (duplicate submission or reference)")
    await _apply(db, user, payment, plan)
    _audit(db, user, "supplier_payment.create", payment, {
        "number": number, "supplier_id": supplier.id, "amount": amount, "method": method,
        "cash_source": body.cash_source, "reference": reference,
        "allocations": [{"invoice": i.invoice_number, "amount": a} for i, a in plan],
    })
    await gl_posting.record(db, pid, user.id, "SUPPLIER_PAYMENT", payment.id)
    return await get_payment(db, pid, payment.id), False


async def allocate(db: AsyncSession, user: User, perms: set, payment_id: int, allocations: list) -> SupplierPayment:
    """Apply the unallocated (advance) part of a payment to finalised invoices."""
    require(perms, "supplier_payments.create", "allocate supplier payments")
    pid = user.pharmacy_id
    payment = await get_payment(db, pid, payment_id)
    await lock_supplier(db, pid, payment.supplier_id)
    payment = await get_payment(db, pid, payment_id, lock=True)
    if payment.status != "POSTED":
        raise HTTPException(status_code=409, detail="Reversed payments cannot be allocated")
    available = await unallocated(db, payment)
    if available <= ZERO:
        raise HTTPException(status_code=409, detail="This payment is fully allocated")
    plan = await _plan(db, pid, payment.supplier_id, available, allocations, auto=not allocations)
    if not plan:
        raise HTTPException(status_code=422, detail="Nothing to allocate: the supplier has no open finalised invoices")
    await _apply(db, user, payment, plan)
    _audit(db, user, "supplier_payment.allocate", payment, {
        "allocations": [{"invoice": i.invoice_number, "amount": a} for i, a in plan]})
    return await get_payment(db, pid, payment_id)


async def reverse(db: AsyncSession, user: User, perms: set, payment_id: int, reason: str) -> SupplierPayment:
    require(perms, "supplier_payments.reverse", "reverse supplier payments")
    pid = user.pharmacy_id
    payment = await get_payment(db, pid, payment_id)
    await lock_supplier(db, pid, payment.supplier_id)
    payment = await get_payment(db, pid, payment_id, lock=True)
    if payment.status != "POSTED":
        raise HTTPException(status_code=409, detail=f"Payment {payment.payment_number} is already reversed")
    payment.status = "REVERSED"
    payment.reference_key = None
    payment.reversed_by, payment.reversed_at, payment.reversal_reason = user.id, utcnow(), reason.strip()
    invoice_ids = set()
    for alloc in payment.allocations:
        if alloc.status == "ACTIVE":
            alloc.status, alloc.reversed_at = "REVERSED", utcnow()
            invoice_ids.add(alloc.invoice_id)
    await db.flush()
    for invoice in (await db.execute(select(SupplierInvoice).where(SupplierInvoice.id.in_(invoice_ids)))).scalars().all() if invoice_ids else []:
        await refresh_payment_cache(db, invoice)
    _audit(db, user, "supplier_payment.reverse", payment, {
        "number": payment.payment_number, "amount": payment.amount, "invoices": sorted(invoice_ids)}, reason=reason)
    await db.flush()
    await gl_posting.record(db, pid, user.id, "SUPPLIER_PAYMENT_REVERSAL", payment.id)
    return await get_payment(db, pid, payment_id)


# ── Balances and reconciliation ────────────────────────────────────────────────

async def supplier_balances(db: AsyncSession, pharmacy_id: int, supplier_id: Optional[int] = None) -> list:
    """Per supplier: finalised bills - posted returns - active payments = amount owed (negative = advance)."""
    invoiced = dict((await db.execute(select(SupplierInvoice.supplier_id, func.sum(SupplierInvoice.calculated_grand_total))
                                       .where(SupplierInvoice.pharmacy_id == pharmacy_id, SupplierInvoice.status == "POSTED")
                                       .group_by(SupplierInvoice.supplier_id))).all())
    returned = dict((await db.execute(select(SupplierReturn.supplier_id, func.sum(SupplierReturn.total_return_amount))
                                       .where(SupplierReturn.pharmacy_id == pharmacy_id, SupplierReturn.status == "POSTED")
                                       .group_by(SupplierReturn.supplier_id))).all())
    from app.models.payables import SupplierCreditNote
    for sid, amount in (await db.execute(select(SupplierCreditNote.supplier_id, func.sum(SupplierCreditNote.total_amount))
                                         .where(SupplierCreditNote.pharmacy_id == pharmacy_id, SupplierCreditNote.status == "POSTED")
                                         .group_by(SupplierCreditNote.supplier_id))).all():
        returned[sid] = money(returned.get(sid) or 0) + money(amount or 0)     # "Returns" include credit notes
    paid = dict((await db.execute(select(SupplierPayment.supplier_id, func.sum(SupplierPayment.amount))
                                   .where(SupplierPayment.pharmacy_id == pharmacy_id, SupplierPayment.status == "POSTED")
                                   .group_by(SupplierPayment.supplier_id))).all())
    allocated_by_payment = dict((await db.execute(
        select(SupplierPaymentAllocation.payment_id, func.sum(SupplierPaymentAllocation.amount))
        .join(SupplierPayment, SupplierPayment.id == SupplierPaymentAllocation.payment_id)
        .where(SupplierPayment.pharmacy_id == pharmacy_id, SupplierPaymentAllocation.status == "ACTIVE")
        .group_by(SupplierPaymentAllocation.payment_id))).all())
    unapplied: dict = {}
    for pay_id, sid, amount in (await db.execute(select(SupplierPayment.id, SupplierPayment.supplier_id, SupplierPayment.amount)
                                                 .where(SupplierPayment.pharmacy_id == pharmacy_id,
                                                        SupplierPayment.status == "POSTED"))).all():
        unapplied[sid] = unapplied.get(sid, ZERO) + money(amount) - money(allocated_by_payment.get(pay_id) or 0)
    today = today_local()
    overdue = dict((await db.execute(select(SupplierInvoice.supplier_id, func.count(SupplierInvoice.id))
                                      .where(SupplierInvoice.pharmacy_id == pharmacy_id, SupplierInvoice.status == "POSTED",
                                             SupplierInvoice.payment_status != "PAID", SupplierInvoice.due_date < today)
                                      .group_by(SupplierInvoice.supplier_id))).all())
    ids = set(invoiced) | set(returned) | set(paid)
    if supplier_id is not None:
        ids &= {supplier_id}
    names = dict((await db.execute(select(Supplier.id, Supplier.name).where(Supplier.id.in_(ids)))).all()) if ids else {}
    rows = []
    for sid in sorted(ids, key=lambda s: names.get(s, "")):
        inv, ret, pay = money(invoiced.get(sid) or 0), money(returned.get(sid) or 0), money(paid.get(sid) or 0)
        rows.append({"supplier_id": sid, "supplier_name": names.get(sid), "invoiced": inv, "returns": ret, "paid": pay,
                     "unallocated_payments": money(unapplied.get(sid) or 0), "balance": money(inv - ret - pay),
                     "overdue_invoices": overdue.get(sid, 0)})
    return rows


async def reconciliation(db: AsyncSession, pharmacy_id: int) -> dict:
    """Cross-checks that payment records, allocations and invoice caches agree."""
    problems = []
    invoices = (await db.execute(select(SupplierInvoice).where(SupplierInvoice.pharmacy_id == pharmacy_id))).scalars().all()
    for inv in invoices:
        figures = await outstanding(db, inv)
        if money(inv.paid_amount) != figures["paid"]:
            problems.append(f"Invoice {inv.invoice_number}: paid_amount ₹{inv.paid_amount} ≠ active allocations ₹{figures['paid']}")
        if inv.status == "POSTED" and figures["outstanding"] < ZERO:
            problems.append(f"Invoice {inv.invoice_number}: over-settled by ₹{-figures['outstanding']}")
        if inv.status != "POSTED" and figures["paid"] > ZERO:
            legacy = (await db.execute(select(func.count(SupplierPaymentAllocation.id))
                                       .join(SupplierPayment, SupplierPayment.id == SupplierPaymentAllocation.payment_id)
                                       .where(SupplierPaymentAllocation.invoice_id == inv.id,
                                              SupplierPaymentAllocation.status == "ACTIVE",
                                              SupplierPayment.is_legacy.is_(False)))).scalar_one()
            if legacy:
                problems.append(f"Invoice {inv.invoice_number} is {inv.status.lower()} but has active payment allocations")
    payments = (await db.execute(select(SupplierPayment).options(selectinload(SupplierPayment.allocations))
                                 .where(SupplierPayment.pharmacy_id == pharmacy_id))).scalars().all()
    for pay in payments:
        active = money(sum((a.amount for a in pay.allocations if a.status == "ACTIVE"), ZERO))
        if pay.status == "REVERSED" and active > ZERO:
            problems.append(f"Payment {pay.payment_number} is reversed but still has active allocations")
        if active > money(pay.amount):
            problems.append(f"Payment {pay.payment_number}: allocations ₹{active} exceed the payment ₹{pay.amount}")
    balances = await supplier_balances(db, pharmacy_id)
    for row in balances:
        open_total = ZERO
        for inv in invoices:
            if inv.supplier_id == row["supplier_id"] and inv.status == "POSTED":
                open_total += (await outstanding(db, inv))["outstanding"]
        other_returns = money((await db.execute(select(func.coalesce(func.sum(SupplierReturn.total_return_amount), 0))
                               .outerjoin(SupplierInvoice, SupplierInvoice.id == SupplierReturn.invoice_id)
                               .where(SupplierReturn.pharmacy_id == pharmacy_id, SupplierReturn.supplier_id == row["supplier_id"],
                                      SupplierReturn.status == "POSTED",
                                      (SupplierReturn.invoice_id.is_(None)) | (SupplierInvoice.status != "POSTED")))).scalar_one())
        on_other_bills = money((await db.execute(select(func.coalesce(func.sum(SupplierPaymentAllocation.amount), 0))
                                .join(SupplierInvoice, SupplierInvoice.id == SupplierPaymentAllocation.invoice_id)
                                .join(SupplierPayment, SupplierPayment.id == SupplierPaymentAllocation.payment_id)
                                .where(SupplierInvoice.supplier_id == row["supplier_id"], SupplierInvoice.status != "POSTED",
                                       SupplierPayment.status == "POSTED",
                                       SupplierPaymentAllocation.status == "ACTIVE"))).scalar_one())
        from app.models.payables import SupplierCreditNote
        unlinked_notes = money((await db.execute(select(func.coalesce(func.sum(SupplierCreditNote.total_amount), 0))
                                .where(SupplierCreditNote.pharmacy_id == pharmacy_id,
                                       SupplierCreditNote.supplier_id == row["supplier_id"], SupplierCreditNote.status == "POSTED",
                                       SupplierCreditNote.invoice_id.is_(None)))).scalar_one())
        expected = money(open_total - row["unallocated_payments"] - other_returns - on_other_bills - unlinked_notes)
        if expected != row["balance"]:
            problems.append(f"{row['supplier_name']}: open invoices less advances ₹{expected} ≠ supplier balance ₹{row['balance']}")
    return {"ok": not problems, "problems": problems, "invoices_checked": len(invoices), "payments_checked": len(payments),
            "suppliers": balances}
