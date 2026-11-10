"""
Customers, credit (udhaar) and receivables.

Ledger invariant (checked by `reconcile_customer`):
    Σ ledger (debit - credit)
        = Σ balance_due of the customer's finalised invoices
        - Σ unapplied amount of the customer's payments / credit notes
Every money event writes a ledger row in the same transaction as the document
that causes it, so the balance can always be explained entry by entry.
"""
import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import List, Optional

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth import AuditLog, User
from app.models.sales import (
    Customer, CustomerLedgerEntry, CustomerPayment, PaymentAllocation, SalesInvoice,
)
from app.schemas.sales import (
    AdjustmentRequest, AllocationIn, CollectionRequest, CustomerCreate, CustomerResponse, CustomerUpdate,
    LedgerEntryResponse, PaymentResponse,
)
from app.services import app_settings
from app.services import gl_posting
from app.services.purchasing import resolve_supplier_state_code
from app.services.sales_common import (
    NON_CASH_METHODS, ZERO, allocate, customer_balance, day_bounds, ensure_amount, lock_customer, money,
    next_sales_number, normalize_mobile, overdue_days, payment_unapplied, post_ledger, refresh_settlement,
    request_fingerprint, require, shift_for_cash, today_local, utcnow,
)


# ── Customers ──────────────────────────────────────────────────────────────────

async def customer_figures(db: AsyncSession, customer: Customer) -> dict:
    balance = await customer_balance(db, customer.pharmacy_id, customer.id)
    overdue = (await db.execute(
        select(func.coalesce(func.sum(SalesInvoice.balance_due), 0)).where(
            SalesInvoice.customer_id == customer.id, SalesInvoice.status == "FINALIZED",
            SalesInvoice.balance_due > 0, SalesInvoice.due_date < today_local(),
        )
    )).scalar_one()
    return {
        "outstanding_balance": balance,
        "overdue_amount": money(overdue),
        "available_credit": money(max(ZERO, Decimal(str(customer.credit_limit)) - max(balance, ZERO))),
    }


async def customer_response(db: AsyncSession, customer: Customer) -> CustomerResponse:
    data = CustomerResponse.model_validate(customer).model_dump()
    data.update(await customer_figures(db, customer))
    return CustomerResponse(**data)


async def _check_mobile_unique(db, pharmacy_id: int, mobile: Optional[str], exclude_id: Optional[int] = None):
    if not mobile:
        return
    stmt = select(Customer.id).where(Customer.pharmacy_id == pharmacy_id, Customer.mobile == mobile)
    if exclude_id:
        stmt = stmt.where(Customer.id != exclude_id)
    if (await db.execute(stmt)).first():
        raise HTTPException(status_code=409, detail=f"Another customer already uses mobile {mobile}")


async def create_customer(db: AsyncSession, user: User, body: CustomerCreate) -> Customer:
    mobile = normalize_mobile(body.mobile)
    await _check_mobile_unique(db, user.pharmacy_id, mobile)
    if body.whatsapp_consent and not mobile:
        raise HTTPException(status_code=422, detail="WhatsApp consent needs a mobile number")
    customer = Customer(
        pharmacy_id=user.pharmacy_id,
        name=body.name.strip(),
        mobile=mobile,
        email=(body.email or "").strip().lower() or None,
        address=(body.address or "").strip() or None,
        gstin=body.gstin,
        state_code=resolve_supplier_state_code(body.gstin, None),
        business_name=(body.business_name or "").strip() or None,
        notes=body.notes,
        credit_limit=money(body.credit_limit),
        payment_terms_days=body.payment_terms_days,
        whatsapp_consent=body.whatsapp_consent,
        whatsapp_consent_at=utcnow() if body.whatsapp_consent else None,
        reminders_opt_out=body.reminders_opt_out,
        preferred_language=body.preferred_language,
        is_active=body.is_active,
        created_by=user.id,
    )
    db.add(customer)
    await db.flush()
    db.add(AuditLog(
        pharmacy_id=user.pharmacy_id, user_id=user.id, action="customer.create", entity_type="customer",
        entity_id=str(customer.id), metadata_json=json.dumps({"credit_limit": str(customer.credit_limit)}),
    ))
    return customer


async def update_customer(db: AsyncSession, user: User, perms: set, customer_id: int, body: CustomerUpdate) -> Customer:
    customer = await lock_customer(db, user.pharmacy_id, customer_id)
    data = body.model_dump(exclude_unset=True)
    if "credit_limit" in data or "payment_terms_days" in data:
        require(perms, "sales.credit_override", "change credit limits or terms")
    if "mobile" in data:
        customer.mobile = normalize_mobile(data.pop("mobile"))
        await _check_mobile_unique(db, user.pharmacy_id, customer.mobile, exclude_id=customer.id)
    if "gstin" in data:
        customer.gstin = data.pop("gstin")
        customer.state_code = resolve_supplier_state_code(customer.gstin, None)
    if "whatsapp_consent" in data:
        consent = data.pop("whatsapp_consent")
        if consent and not customer.whatsapp_consent:
            customer.whatsapp_consent_at = utcnow()
        customer.whatsapp_consent = consent
    if customer.whatsapp_consent and not customer.mobile:
        raise HTTPException(status_code=422, detail="WhatsApp consent needs a mobile number")
    for key, value in data.items():
        if value is None and key in ("name", "credit_limit", "payment_terms_days", "is_active",
                                     "reminders_opt_out", "preferred_language"):
            continue
        setattr(customer, key, value.strip() if isinstance(value, str) else value)
    db.add(AuditLog(
        pharmacy_id=user.pharmacy_id, user_id=user.id, action="customer.update", entity_type="customer",
        entity_id=str(customer.id), metadata_json=json.dumps({"fields": sorted(body.model_dump(exclude_unset=True))}),
    ))
    await db.flush()
    return customer


async def list_customers(db: AsyncSession, pharmacy_id: int, q: Optional[str], active_only: bool,
                         with_balance_only: bool, limit: int, offset: int):
    balance_sq = (
        select(CustomerLedgerEntry.customer_id, func.sum(CustomerLedgerEntry.debit - CustomerLedgerEntry.credit).label("bal"))
        .where(CustomerLedgerEntry.pharmacy_id == pharmacy_id)
        .group_by(CustomerLedgerEntry.customer_id)
        .subquery()
    )
    stmt = select(Customer).outerjoin(balance_sq, balance_sq.c.customer_id == Customer.id).where(
        Customer.pharmacy_id == pharmacy_id
    )
    if q:
        like = f"%{q.strip()}%"
        digits = "".join(ch for ch in q if ch.isdigit())
        conds = [Customer.name.ilike(like), Customer.business_name.ilike(like), Customer.email.ilike(like)]
        if digits:
            conds.append(Customer.mobile.ilike(f"%{digits}%"))
        stmt = stmt.where(or_(*conds))
    if active_only:
        stmt = stmt.where(Customer.is_active.is_(True))
    if with_balance_only:
        stmt = stmt.where(balance_sq.c.bal != 0)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    customers = (await db.execute(stmt.order_by(Customer.name).offset(offset).limit(limit))).scalars().all()
    return [await customer_response(db, c) for c in customers], total


# ── Payments / collections ─────────────────────────────────────────────────────

async def payment_response(db: AsyncSession, payment: CustomerPayment, replayed: bool = False) -> PaymentResponse:
    allocs = (await db.execute(
        select(PaymentAllocation).where(PaymentAllocation.payment_id == payment.id, PaymentAllocation.is_reversed.is_(False))
    )).scalars().all()
    return PaymentResponse(
        id=payment.id, receipt_number=payment.receipt_number, customer_id=payment.customer_id,
        source=payment.source, method=payment.method, amount=payment.amount,
        amount_tendered=payment.amount_tendered, change_returned=payment.change_returned,
        refunded_amount=payment.refunded_amount, unapplied_amount=await payment_unapplied(db, payment),
        reference=payment.reference, verification_status=payment.verification_status, status=payment.status,
        received_at=payment.received_at, replayed=replayed,
        allocations=[AllocationIn(invoice_id=a.invoice_id, amount=a.amount) for a in allocs],
    )


async def _open_invoices(db: AsyncSession, pharmacy_id: int, customer_id: int, lock: bool) -> List[SalesInvoice]:
    stmt = (
        select(SalesInvoice)
        .where(SalesInvoice.pharmacy_id == pharmacy_id, SalesInvoice.customer_id == customer_id,
               SalesInvoice.status == "FINALIZED", SalesInvoice.balance_due > 0)
        .order_by(func.coalesce(SalesInvoice.due_date, func.date(SalesInvoice.invoice_date)), SalesInvoice.id)
    )
    if lock:
        stmt = stmt.with_for_update()
    return list((await db.execute(stmt)).scalars().all())


def _plan_allocations(open_invoices: List[SalesInvoice], amount: Decimal, requested: Optional[List[AllocationIn]]):
    by_id = {i.id: i for i in open_invoices}
    plan = []
    if requested is None:
        remaining = amount
        for inv in open_invoices:            # Oldest due first
            if remaining <= ZERO:
                break
            take = min(remaining, Decimal(str(inv.balance_due)))
            plan.append((inv, take))
            remaining -= take
        return plan
    seen = set()
    for a in requested:
        if a.invoice_id in seen:
            raise HTTPException(status_code=422, detail="Each invoice may appear only once in allocations")
        seen.add(a.invoice_id)
        inv = by_id.get(a.invoice_id)
        if inv is None:
            raise HTTPException(status_code=422, detail=f"Invoice {a.invoice_id} is not an open invoice of this customer")
        plan.append((inv, money(a.amount)))
    if sum((amt for _, amt in plan), ZERO) > amount:
        raise HTTPException(status_code=422, detail="Allocations exceed the payment amount")
    return plan


async def record_collection(
    db: AsyncSession, user: User, perms: set, customer_id: int, body: CollectionRequest,
):
    """Payment received later against credit invoices (or as an advance)."""
    require(perms, "payments.receive", "record customer payments")
    pharmacy_id = user.pharmacy_id
    fingerprint = request_fingerprint({**body.model_dump(mode="json"), "customer_id": customer_id})
    async def find_existing():
        return (await db.execute(
            select(CustomerPayment).where(CustomerPayment.pharmacy_id == pharmacy_id,
                                          CustomerPayment.idempotency_key == body.idempotency_key)
        )).scalar_one_or_none()

    existing = await find_existing()
    if existing:
        return existing, True
    customer = await lock_customer(db, pharmacy_id, customer_id)
    # A concurrent duplicate waits on the customer lock; check again once we hold it
    existing = await find_existing()
    if existing:
        return existing, True
    amount = ensure_amount(body.amount, "Payment amount")
    tendered = None
    change = ZERO
    if body.method == "CASH":
        tendered = money(body.tendered) if body.tendered is not None else amount
        if tendered < amount:
            raise HTTPException(status_code=422, detail="Cash tendered is less than the amount received")
        change = money(tendered - amount)
    elif body.tendered is not None:
        raise HTTPException(status_code=422, detail="Only cash payments have a tendered amount")

    open_invoices = await _open_invoices(db, pharmacy_id, customer.id, lock=True)
    plan = _plan_allocations(open_invoices, amount, body.allocations)
    unapplied = money(amount - sum((amt for _, amt in plan), ZERO))
    if unapplied > ZERO and not await app_settings.get_bool(db, pharmacy_id, "credit.allow_advances"):
        raise HTTPException(status_code=422, detail=f"₹{unapplied} is more than the customer owes; advances are disabled")

    receipt = await next_sales_number(
        db, pharmacy_id, "RECEIPT", "invoice.receipt_prefix", "RCPT",
        CustomerPayment.receipt_number, CustomerPayment.pharmacy_id,
    )
    payment = CustomerPayment(
        pharmacy_id=pharmacy_id, receipt_number=receipt, idempotency_key=body.idempotency_key,
        customer_id=customer.id, source="COLLECTION", method=body.method, amount=amount,
        amount_tendered=tendered, change_returned=change, reference=(body.reference or "").strip() or None,
        verification_status="RECORDED" if body.method in NON_CASH_METHODS else "NOT_APPLICABLE",
        notes=body.notes, shift_id=await shift_for_cash(db, pharmacy_id, user.id, body.method == "CASH"),
        received_by=user.id,
    )
    db.add(payment)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail="This payment was already recorded (duplicate submission)")
    for inv, amt in plan:
        allocate(db, payment=payment, invoice=inv, amount=amt, user_id=user.id)
    post_ledger(
        db, pharmacy_id=pharmacy_id, customer_id=customer.id, entry_type="PAYMENT", user_id=user.id,
        reference=receipt, credit=amount, payment_id=payment.id,
        description=f"{body.method} payment received"
        + (f" for {', '.join(i.invoice_number for i, _ in plan)}" if plan else " (advance)"),
    )
    db.add(AuditLog(
        pharmacy_id=pharmacy_id, user_id=user.id, action="payment.receive", entity_type="customer_payment",
        entity_id=str(payment.id),
        metadata_json=json.dumps({"receipt": receipt, "amount": str(amount), "method": body.method,
                                  "unapplied": str(unapplied), "fingerprint": fingerprint[:16]}),
    ))
    await db.flush()
    await gl_posting.record(db, pharmacy_id, user.id, "CUSTOMER_RECEIPT", payment.id)
    return payment, False


async def apply_customer_credit(db: AsyncSession, user: User, perms: set, customer_id: int,
                                requested: Optional[List[AllocationIn]]) -> List[dict]:
    """Use advances / credit notes to settle open invoices. The ledger is unchanged (already credited)."""
    require(perms, "payments.receive", "apply customer credit")
    pharmacy_id = user.pharmacy_id
    customer = await lock_customer(db, pharmacy_id, customer_id)
    sources = (await db.execute(
        select(CustomerPayment).where(
            CustomerPayment.pharmacy_id == pharmacy_id, CustomerPayment.customer_id == customer.id,
            CustomerPayment.status == "POSTED",
        ).order_by(CustomerPayment.received_at, CustomerPayment.id).with_for_update()
    )).scalars().all()
    available = []
    for s in sources:
        left = await payment_unapplied(db, s)
        if left > ZERO:
            available.append([s, left])
    total_credit = sum((left for _, left in available), ZERO)
    if total_credit <= ZERO:
        raise HTTPException(status_code=422, detail="This customer has no unapplied credit")

    open_invoices = await _open_invoices(db, pharmacy_id, customer.id, lock=True)
    plan = _plan_allocations(open_invoices, total_credit, requested)
    applied = []
    for inv, amt in plan:
        remaining = amt
        for entry in available:
            if remaining <= ZERO:
                break
            take = min(entry[1], remaining)
            if take <= ZERO:
                continue
            allocate(db, payment=entry[0], invoice=inv, amount=take, user_id=user.id)
            entry[1] -= take
            remaining -= take
            applied.append({"invoice_id": inv.id, "invoice_number": inv.invoice_number,
                            "receipt_number": entry[0].receipt_number, "amount": take})
    db.add(AuditLog(
        pharmacy_id=pharmacy_id, user_id=user.id, action="credit.apply", entity_type="customer",
        entity_id=str(customer.id), metadata_json=json.dumps({"applied": [
            {**a, "amount": str(a["amount"])} for a in applied]}),
    ))
    await db.flush()
    return applied


async def get_payment(db: AsyncSession, pharmacy_id: int, payment_id: int, lock: bool = False) -> CustomerPayment:
    stmt = select(CustomerPayment).where(CustomerPayment.id == payment_id, CustomerPayment.pharmacy_id == pharmacy_id)
    if lock:
        stmt = stmt.with_for_update()
    payment = (await db.execute(stmt)).scalar_one_or_none()
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    return payment


async def verify_payment(db: AsyncSession, user: User, perms: set, payment_id: int, note: str) -> CustomerPayment:
    """Mark a UPI / card / bank payment as checked against the bank or terminal statement."""
    require(perms, "payments.receive", "verify payments")
    payment = await get_payment(db, user.pharmacy_id, payment_id, lock=True)
    if payment.method not in NON_CASH_METHODS:
        raise HTTPException(status_code=422, detail="Only UPI, card, bank and other payments need verification")
    if payment.verification_status == "VERIFIED":
        raise HTTPException(status_code=409, detail="Payment is already verified")
    payment.verification_status = "VERIFIED"
    payment.verified_by = user.id
    payment.verified_at = utcnow()
    db.add(AuditLog(
        pharmacy_id=user.pharmacy_id, user_id=user.id, action="payment.verify", entity_type="customer_payment",
        entity_id=str(payment.id), reason=note,
    ))
    await db.flush()
    return payment


async def reverse_payment(db: AsyncSession, user: User, perms: set, payment_id: int, reason: str) -> CustomerPayment:
    """
    Undo a payment recorded in error (e.g. a UPI transfer that never arrived): its
    allocations are reversed so the invoices become due again, and the ledger gets a
    PAYMENT_REVERSAL debit. Walk-in sale payments must be corrected by voiding the sale.
    """
    require(perms, "customers.adjust", "reverse payments")
    payment = await get_payment(db, user.pharmacy_id, payment_id, lock=True)
    if payment.status == "REVERSED":
        raise HTTPException(status_code=409, detail="Payment is already reversed")
    if payment.customer_id is None:
        raise HTTPException(status_code=422, detail="Walk-in sale payments are corrected by voiding the sale")
    if payment.source == "RETURN_CREDIT":
        raise HTTPException(status_code=422, detail="Credit notes from returns cannot be reversed")
    if payment.refunded_amount > ZERO:
        raise HTTPException(status_code=409, detail="Part of this payment was refunded; it cannot be reversed")
    await lock_customer(db, user.pharmacy_id, payment.customer_id)
    rows = (await db.execute(
        select(PaymentAllocation, SalesInvoice)
        .join(SalesInvoice, SalesInvoice.id == PaymentAllocation.invoice_id)
        .where(PaymentAllocation.payment_id == payment.id, PaymentAllocation.is_reversed.is_(False))
        .with_for_update()
    )).all()
    for alloc, invoice in rows:
        alloc.is_reversed = True
        alloc.reversed_at = utcnow()
        invoice.amount_paid = money(Decimal(str(invoice.amount_paid)) - alloc.amount)
        if payment.source == "SALE":
            invoice.paid_at_sale = money(Decimal(str(invoice.paid_at_sale)) - alloc.amount)
        refresh_settlement(invoice)
    payment.status = "REVERSED"
    post_ledger(
        db, pharmacy_id=user.pharmacy_id, customer_id=payment.customer_id, entry_type="PAYMENT_REVERSAL",
        user_id=user.id, reference=payment.receipt_number, debit=payment.amount, payment_id=payment.id,
        description=f"Reversal of {payment.method} payment {payment.receipt_number}", reason=reason,
    )
    db.add(AuditLog(
        pharmacy_id=user.pharmacy_id, user_id=user.id, action="payment.reverse", entity_type="customer_payment",
        entity_id=str(payment.id), reason=reason,
        metadata_json=json.dumps({"receipt": payment.receipt_number, "amount": str(payment.amount)}),
    ))
    await db.flush()
    await gl_posting.record(db, user.pharmacy_id, user.id, "CUSTOMER_RECEIPT_REVERSAL", payment.id)
    return payment


async def post_adjustment(db: AsyncSession, user: User, perms: set, customer_id: int, body: AdjustmentRequest):
    """Authorised reduction of an invoice balance (goodwill adjustment or bad-debt write-off)."""
    require(perms, "customers.adjust", "post ledger adjustments")
    pharmacy_id = user.pharmacy_id
    await lock_customer(db, pharmacy_id, customer_id)
    invoice = (await db.execute(
        select(SalesInvoice).where(
            SalesInvoice.id == body.invoice_id, SalesInvoice.pharmacy_id == pharmacy_id,
            SalesInvoice.customer_id == customer_id,
        ).with_for_update()
    )).scalar_one_or_none()
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found for this customer")
    if invoice.status != "FINALIZED":
        raise HTTPException(status_code=409, detail="Only finalised invoices can be adjusted")
    amount = money(body.amount)
    if amount > invoice.balance_due:
        raise HTTPException(status_code=422, detail=f"Adjustment exceeds the ₹{invoice.balance_due} due")
    invoice.written_off_amount = money(Decimal(str(invoice.written_off_amount)) + amount)
    refresh_settlement(invoice)
    entry = post_ledger(
        db, pharmacy_id=pharmacy_id, customer_id=customer_id, entry_type=body.adjustment_type,
        user_id=user.id, reference=invoice.invoice_number, credit=amount, invoice_id=invoice.id,
        description=("Write-off" if body.adjustment_type == "WRITE_OFF" else "Adjustment") + f" on {invoice.invoice_number}",
        reason=body.reason,
    )
    db.add(AuditLog(
        pharmacy_id=pharmacy_id, user_id=user.id, action=f"ledger.{body.adjustment_type.lower()}",
        entity_type="sales_invoice", entity_id=str(invoice.id), reason=body.reason,
        metadata_json=json.dumps({"amount": str(amount), "invoice_number": invoice.invoice_number}),
    ))
    await db.flush()
    await gl_posting.record(db, pharmacy_id, user.id, "CUSTOMER_ADJUSTMENT", entry.id)
    return entry


# ── Statements, reconciliation, aging ──────────────────────────────────────────

async def unapplied_credit(db: AsyncSession, pharmacy_id: int, customer_id: int) -> Decimal:
    payments = (await db.execute(
        select(CustomerPayment).where(
            CustomerPayment.pharmacy_id == pharmacy_id, CustomerPayment.customer_id == customer_id,
            CustomerPayment.status == "POSTED",
        )
    )).scalars().all()
    total = ZERO
    for p in payments:
        total += await payment_unapplied(db, p)
    return money(total)


async def statement(db: AsyncSession, pharmacy_id: int, customer_id: int,
                    date_from: Optional[date], date_to: Optional[date]):
    base = [CustomerLedgerEntry.pharmacy_id == pharmacy_id, CustomerLedgerEntry.customer_id == customer_id]
    opening = ZERO
    if date_from:
        opening = money((await db.execute(
            select(func.coalesce(func.sum(CustomerLedgerEntry.debit - CustomerLedgerEntry.credit), 0))
            .where(*base, CustomerLedgerEntry.created_at < day_bounds(date_from)[0])
        )).scalar_one())
        base.append(CustomerLedgerEntry.created_at >= day_bounds(date_from)[0])
    if date_to:
        base.append(CustomerLedgerEntry.created_at < day_bounds(date_to)[1])
    rows = (await db.execute(
        select(CustomerLedgerEntry).where(*base).order_by(CustomerLedgerEntry.created_at, CustomerLedgerEntry.id)
    )).scalars().all()
    running = opening
    entries = []
    debits = credits = ZERO
    for r in rows:
        running = money(running + r.debit - r.credit)
        debits += r.debit
        credits += r.credit
        entries.append(LedgerEntryResponse(
            id=r.id, created_at=r.created_at, entry_type=r.entry_type, reference=r.reference,
            description=r.description, debit=r.debit, credit=r.credit, balance=running,
            invoice_id=r.invoice_id, reason=r.reason,
        ))
    return opening, running, money(debits), money(credits), entries


async def reconcile_customer(db: AsyncSession, pharmacy_id: int, customer_id: int) -> dict:
    ledger = await customer_balance(db, pharmacy_id, customer_id)
    open_total = money((await db.execute(
        select(func.coalesce(func.sum(SalesInvoice.balance_due), 0)).where(
            SalesInvoice.pharmacy_id == pharmacy_id, SalesInvoice.customer_id == customer_id,
            SalesInvoice.status == "FINALIZED",
        )
    )).scalar_one())
    unapplied = await unapplied_credit(db, pharmacy_id, customer_id)
    expected = money(open_total - unapplied)
    return {
        "ledger_balance": ledger,
        "open_invoice_balance": open_total,
        "unapplied_credit": unapplied,
        "expected_ledger_balance": expected,
        "difference": money(ledger - expected),
        "is_reconciled": ledger == expected,
    }


AGING_BUCKETS = (("not_due", None, 0), ("days_1_30", 1, 30), ("days_31_60", 31, 60),
                 ("days_61_90", 61, 90), ("days_over_90", 91, None))


def bucket_for(days: int, due: Optional[date]) -> str:
    if due is None or days <= 0:
        return "not_due"
    for name, low, high in AGING_BUCKETS[1:]:
        if days >= low and (high is None or days <= high):
            return name
    return "days_over_90"


async def aging_report(db: AsyncSession, pharmacy_id: int, on: Optional[date] = None) -> dict:
    on = on or today_local()
    invoices = (await db.execute(
        select(SalesInvoice).where(
            SalesInvoice.pharmacy_id == pharmacy_id, SalesInvoice.status == "FINALIZED",
            SalesInvoice.balance_due > 0, SalesInvoice.customer_id.is_not(None),
        )
    )).scalars().all()
    totals = {name: ZERO for name, _, _ in AGING_BUCKETS}
    customers: dict = {}
    for inv in invoices:
        days = overdue_days(inv, on)
        bucket = bucket_for(days, inv.due_date)
        totals[bucket] += inv.balance_due
        row = customers.setdefault(inv.customer_id, {
            "customer_id": inv.customer_id, "customer_name": inv.customer_name,
            "customer_mobile": inv.customer_mobile, "invoices": 0, "total": ZERO,
            **{name: ZERO for name, _, _ in AGING_BUCKETS},
        })
        row["invoices"] += 1
        row["total"] += inv.balance_due
        row[bucket] += inv.balance_due
    return {
        "as_of": on,
        "totals": {k: money(v) for k, v in totals.items()},
        "grand_total": money(sum(totals.values(), ZERO)),
        "customers": sorted(customers.values(), key=lambda r: -r["total"]),
    }
