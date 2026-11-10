"""
Sales invoices: finalisation, retrieval, search, voids and print tracking.

Finalisation runs in ONE database transaction (the request session): invoice,
lines, batch deductions, stock movements, payments, allocations, customer
ledger, prescription record and audit log either all commit or none do.

Idempotency: the client sends a key per checkout attempt. The invoice row that
carries the key is inserted before any stock is touched, so a retried or
double-clicked request waits on the unique index and then returns the original
sale instead of selling twice.
"""
import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.auth import AuditLog, Pharmacy, User
from app.models.inventory import MedicineBatch, StockMovement
from app.models.sales import (
    CustomerPayment, CustomerRefund, MessageOutbox, PaymentAllocation, SalePrescription,
    SalesInvoice, SalesInvoiceItem, SalesReturn,
)
from app.schemas.sales import (
    InvoiceItemResponse, InvoiceListItem, InvoiceResponse, PaymentSummary, RefundSummary,
    ReturnBrief, SaleFinalizeRequest, VoidRequest,
)
from app.services import app_settings
from app.services import gl_posting
from app.services.pos import plan_sale
from app.services.sales_common import (
    NON_CASH_METHODS, ZERO, allocate, day_bounds, customer_balance, default_due_date, lock_customer, money,
    next_sales_number, overdue_days, post_ledger, refresh_settlement, request_fingerprint, require,
    shift_for_cash, today_local, utcnow,
)


class DuplicateIdempotencyKey(Exception):
    """Another request with the same idempotency key committed first."""


async def invoice_by_key(db: AsyncSession, pharmacy_id: int, key: str) -> Optional[SalesInvoice]:
    return (await db.execute(
        select(SalesInvoice).where(SalesInvoice.pharmacy_id == pharmacy_id, SalesInvoice.idempotency_key == key)
    )).scalar_one_or_none()


def _resolve_payments(payments, grand_total: Decimal) -> List[dict]:
    """
    Validate payment lines. CASH may give only `tendered`: the amount applied is then
    whatever the other methods leave unpaid, and the rest is change.
    """
    resolved = []
    non_cash = ZERO
    for p in payments:
        if p.method == "CASH":
            continue
        if p.amount is None:
            raise HTTPException(status_code=422, detail=f"Enter the amount paid by {p.method}")
        if p.tendered is not None:
            raise HTTPException(status_code=422, detail="Only cash payments have a tendered amount")
        non_cash += money(p.amount)
        resolved.append({"method": p.method, "amount": money(p.amount), "tendered": None,
                         "change": ZERO, "reference": (p.reference or "").strip() or None})

    cash_lines = [p for p in payments if p.method == "CASH"]
    cash_applied_so_far = ZERO
    for p in cash_lines:
        tendered = money(p.tendered) if p.tendered is not None else None
        if p.amount is not None:
            amount = money(p.amount)
        elif tendered is not None:
            amount = min(tendered, money(grand_total - non_cash - cash_applied_so_far))
        else:
            raise HTTPException(status_code=422, detail="Enter the cash amount or the cash tendered")
        if amount <= ZERO:
            raise HTTPException(status_code=422, detail="Cash is not needed: other payments already cover the bill")
        if tendered is None:
            tendered = amount
        if tendered < amount:
            raise HTTPException(status_code=422, detail=f"Cash tendered ₹{tendered} is less than ₹{amount} applied")
        cash_applied_so_far += amount
        resolved.append({"method": "CASH", "amount": amount, "tendered": tendered,
                         "change": money(tendered - amount), "reference": None})

    total = sum((r["amount"] for r in resolved), ZERO)
    if total > grand_total:
        raise HTTPException(
            status_code=422,
            detail=f"Payments ₹{total} exceed the bill total ₹{grand_total}. Record extra cash as tendered (change).",
        )
    return resolved


async def finalize_sale(
    db: AsyncSession, user: User, perms: set, body: SaleFinalizeRequest,
) -> Tuple[SalesInvoice, bool, Decimal]:
    """Returns (invoice, replayed, change_due)."""
    pharmacy_id = user.pharmacy_id
    fingerprint = request_fingerprint(body.model_dump(mode="json"))
    existing = await invoice_by_key(db, pharmacy_id, body.idempotency_key)
    if existing:
        if existing.request_hash != fingerprint:
            raise HTTPException(status_code=409, detail="This checkout key was already used for a different bill")
        return existing, True, ZERO

    pharmacy = (await db.execute(select(Pharmacy).where(Pharmacy.id == pharmacy_id))).scalar_one()
    customer = None
    if body.customer_id:
        customer = await lock_customer(db, pharmacy_id, body.customer_id)
        if not customer.is_active:
            raise HTTPException(status_code=422, detail=f"Customer '{customer.name}' is inactive")

    # Claim the idempotency key and invoice number before touching stock
    invoice_number = await next_sales_number(
        db, pharmacy_id, "SALE", "invoice.sales_prefix", pharmacy.invoice_prefix or "INV",
        SalesInvoice.invoice_number, SalesInvoice.pharmacy_id,
    )
    walk_in = await app_settings.get_setting(db, pharmacy_id, "pos.walk_in_label")
    invoice = SalesInvoice(
        pharmacy_id=pharmacy_id,
        invoice_number=invoice_number,
        idempotency_key=body.idempotency_key,
        request_hash=fingerprint,
        invoice_date=utcnow(),
        status="FINALIZED",
        customer_id=customer.id if customer else None,
        customer_name=customer.name if customer else walk_in,
        customer_mobile=customer.mobile if customer else None,
        customer_gstin=customer.gstin if customer else None,
        cashier_id=user.id,
        notes=(body.notes or "").strip() or None,
    )
    db.add(invoice)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as exc:
        raise DuplicateIdempotencyKey() from exc

    plan = await plan_sale(db, pharmacy_id, body.items, customer, lock=True)
    if plan.discount_used:
        require(perms, "sales.discount", "give discounts")

    # Prescription controls
    if plan.rx_medicines:
        require(perms, "sales.prescription", "sell prescription medicines")
        names = ", ".join(m.name for m in plan.rx_medicines)
        if body.prescription is None:
            raise HTTPException(
                status_code=422,
                detail=f"Prescription details are required for: {names}",
            )
        strict = set(await app_settings.get_list(db, pharmacy_id, "rx.strict_schedules"))
        strict_meds = [m.name for m in plan.rx_medicines if (m.schedule_type or "").upper() in strict]
        if strict_meds and not ((body.prescription.patient_address or "").strip()
                                and (body.prescription.prescriber_address or "").strip()):
            raise HTTPException(
                status_code=422,
                detail=f"Patient and prescriber address are required for: {', '.join(strict_meds)}",
            )

    payments = _resolve_payments(body.payments, plan.grand_total)
    total_paid = sum((p["amount"] for p in payments), ZERO)
    unpaid = money(plan.grand_total - total_paid)

    if unpaid > ZERO:
        if customer is None:
            raise HTTPException(
                status_code=422,
                detail=f"₹{unpaid} is unpaid. Select a customer to make a credit (udhaar) sale.",
            )
        require(perms, "sales.credit", "make credit sales")
        outstanding = await customer_balance(db, pharmacy_id, customer.id)
        exposure = money(outstanding + unpaid)
        if exposure > customer.credit_limit:
            if "sales.credit_override" not in perms:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        f"Credit limit exceeded: outstanding ₹{outstanding} + ₹{unpaid} = ₹{exposure}, "
                        f"limit ₹{customer.credit_limit}. A manager with 'sales.credit_override' must approve."
                    ),
                )
            if not (body.credit_override_reason or "").strip():
                raise HTTPException(status_code=422, detail="Give a reason for exceeding the credit limit")
            invoice.credit_override_by = user.id
            invoice.credit_override_reason = body.credit_override_reason.strip()
        invoice.is_credit_sale = True
        invoice.due_date = body.due_date or default_due_date(customer.payment_terms_days)
        if invoice.due_date < today_local():
            raise HTTPException(status_code=422, detail="Due date cannot be in the past")

    involves_cash = any(p["method"] == "CASH" for p in payments)
    invoice.shift_id = await shift_for_cash(db, pharmacy_id, user.id, involves_cash)

    # Totals
    invoice.is_inter_state = plan.is_inter_state
    invoice.gross_amount = money(plan.gross)
    invoice.discount_total = money(plan.discount)
    invoice.taxable_total = money(plan.taxable)
    invoice.cgst_total = money(plan.cgst)
    invoice.sgst_total = money(plan.sgst)
    invoice.igst_total = money(plan.igst)
    invoice.tax_total = money(plan.tax)
    invoice.round_off = plan.round_off
    invoice.grand_total = plan.grand_total
    invoice.cost_total = money(plan.cost_total)
    invoice.prescription_required = bool(plan.rx_medicines)
    refresh_settlement(invoice)

    # Lines, stock deduction and immutable ledger movements
    for it in plan.items:
        batch: MedicineBatch = it.batch
        new_balance = money(Decimal(str(batch.current_stock_base)) - it.quantity_base)
        if new_balance < ZERO:  # Defensive: plan_sale already checked under the same lock
            raise HTTPException(status_code=409, detail=f"Stock changed for batch {batch.batch_number}; retry")
        batch.current_stock_base = new_balance
        movement = StockMovement(
            pharmacy_id=pharmacy_id,
            medicine_id=it.medicine.id,
            batch_id=batch.id,
            movement_type="SALE",
            reference_id=invoice_number,
            reference_type="sales_invoice",
            quantity_base=-it.quantity_base,
            balance_after_base=new_balance,
            unit_cost=it.cost_per_base,
            total_cost=it.cost_total,
            reason="Counter sale",
            user_id=user.id,
        )
        db.add(movement)
        await db.flush()
        db.add(SalesInvoiceItem(
            invoice_id=invoice.id,
            line_no=it.line_no,
            medicine_id=it.medicine.id,
            batch_id=batch.id,
            stock_movement_id=movement.id,
            medicine_name=it.medicine.name,
            hsn_code=it.medicine.hsn_code,
            schedule_type=it.medicine.schedule_type,
            batch_number=batch.batch_number,
            expiry_date=batch.expiry_date,
            unit_id=it.unit_id,
            unit_name=it.unit_name,
            conversion_factor=it.factor,
            quantity=it.quantity,
            quantity_base=it.quantity_base,
            mrp=it.mrp,
            selling_rate=it.selling_rate,
            gross_amount=it.gross,
            discount_percent=it.discount_percent,
            discount_amount=it.discount,
            taxable_amount=it.taxable,
            tax_rate=it.tax_rate,
            cgst_amount=it.cgst,
            sgst_amount=it.sgst,
            igst_amount=it.igst,
            tax_amount=it.tax,
            line_total=it.line_total,
            cost_per_base_unit=it.cost_per_base,
            cost_total=it.cost_total,
        ))

    # Customer ledger: the bill is a debit; each payment a credit
    if customer:
        post_ledger(
            db, pharmacy_id=pharmacy_id, customer_id=customer.id, entry_type="INVOICE", user_id=user.id,
            reference=invoice_number, description=f"Sale invoice {invoice_number}",
            debit=plan.grand_total, invoice_id=invoice.id,
        )

    change_due = ZERO
    for p in payments:
        receipt = await next_sales_number(
            db, pharmacy_id, "RECEIPT", "invoice.receipt_prefix", "RCPT",
            CustomerPayment.receipt_number, CustomerPayment.pharmacy_id,
        )
        payment = CustomerPayment(
            pharmacy_id=pharmacy_id,
            receipt_number=receipt,
            customer_id=customer.id if customer else None,
            source="SALE",
            method=p["method"],
            amount=p["amount"],
            amount_tendered=p["tendered"],
            change_returned=p["change"],
            reference=p["reference"],
            verification_status="RECORDED" if p["method"] in NON_CASH_METHODS else "NOT_APPLICABLE",
            shift_id=invoice.shift_id,
            received_by=user.id,
        )
        db.add(payment)
        await db.flush()
        allocate(db, payment=payment, invoice=invoice, amount=p["amount"], user_id=user.id)
        change_due += p["change"]
        if customer:
            post_ledger(
                db, pharmacy_id=pharmacy_id, customer_id=customer.id, entry_type="PAYMENT", user_id=user.id,
                reference=receipt, description=f"{p['method']} payment at sale {invoice_number}",
                credit=p["amount"], invoice_id=invoice.id, payment_id=payment.id,
            )
    invoice.paid_at_sale = total_paid

    if body.prescription is not None and plan.rx_medicines:
        rx = body.prescription
        db.add(SalePrescription(
            invoice_id=invoice.id,
            prescription_reference=rx.prescription_reference.strip(),
            prescription_date=rx.prescription_date,
            prescriber_name=rx.prescriber_name.strip(),
            prescriber_registration_no=(rx.prescriber_registration_no or "").strip() or None,
            prescriber_address=(rx.prescriber_address or "").strip() or None,
            patient_name=rx.patient_name.strip(),
            patient_address=(rx.patient_address or "").strip() or None,
            verified_by=user.id,
        ))

    db.add(AuditLog(
        pharmacy_id=pharmacy_id,
        user_id=user.id,
        action="sale.finalize",
        entity_type="sales_invoice",
        entity_id=str(invoice.id),
        metadata_json=json.dumps({
            "invoice_number": invoice_number,
            "grand_total": str(plan.grand_total),
            "paid": str(total_paid),
            "credit": str(unpaid),
            "lines": len(plan.items),
            "prescription_recorded": bool(plan.rx_medicines),   # details stay in sale_prescriptions
            "credit_override": invoice.credit_override_by is not None,
        }),
    ))
    await db.flush()
    await gl_posting.record(db, pharmacy_id, user.id, "SALE", invoice.id)
    return invoice, False, change_due


# ── Retrieval ──────────────────────────────────────────────────────────────────

async def get_invoice(db: AsyncSession, pharmacy_id: int, invoice_id: int, lock: bool = False) -> SalesInvoice:
    stmt = (
        select(SalesInvoice)
        .options(selectinload(SalesInvoice.items), selectinload(SalesInvoice.prescription))
        .where(SalesInvoice.id == invoice_id, SalesInvoice.pharmacy_id == pharmacy_id)
    )
    if lock:
        stmt = stmt.with_for_update()
    invoice = (await db.execute(stmt)).scalar_one_or_none()
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return invoice


async def _user_names(db: AsyncSession, ids) -> dict:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {u.id: u.name for u in (await db.execute(select(User).where(User.id.in_(ids)))).scalars().all()}


async def invoice_response(
    db: AsyncSession, invoice: SalesInvoice, replayed: bool = False, change_due: Decimal = ZERO,
) -> InvoiceResponse:
    await db.refresh(invoice, attribute_names=["items", "prescription"])
    alloc_rows = (await db.execute(
        select(PaymentAllocation, CustomerPayment)
        .join(CustomerPayment, CustomerPayment.id == PaymentAllocation.payment_id)
        .where(PaymentAllocation.invoice_id == invoice.id)
        .order_by(CustomerPayment.received_at, PaymentAllocation.id)
    )).all()
    refunds = (await db.execute(
        select(CustomerRefund).where(CustomerRefund.invoice_id == invoice.id).order_by(CustomerRefund.refunded_at)
    )).scalars().all()
    returns = (await db.execute(
        select(SalesReturn).where(SalesReturn.invoice_id == invoice.id).order_by(SalesReturn.created_at)
    )).scalars().all()
    names = await _user_names(db, [invoice.cashier_id] + [p.received_by for _, p in alloc_rows])

    payments: List[PaymentSummary] = []
    for alloc, pay in alloc_rows:
        payments.append(PaymentSummary(
            id=pay.id, receipt_number=pay.receipt_number, method=pay.method, source=pay.source,
            amount=pay.amount, allocated_to_invoice=alloc.amount, amount_tendered=pay.amount_tendered,
            change_returned=pay.change_returned, reference=pay.reference,
            verification_status=pay.verification_status,
            status="REVERSED" if alloc.is_reversed else pay.status,
            received_by_name=names.get(pay.received_by), received_at=pay.received_at,
        ))

    return InvoiceResponse(
        id=invoice.id,
        invoice_number=invoice.invoice_number,
        invoice_date=invoice.invoice_date,
        status=invoice.status,
        customer_id=invoice.customer_id,
        customer_name=invoice.customer_name,
        customer_mobile=invoice.customer_mobile,
        customer_gstin=invoice.customer_gstin,
        is_inter_state=invoice.is_inter_state,
        cashier_id=invoice.cashier_id,
        cashier_name=names.get(invoice.cashier_id),
        gross_amount=invoice.gross_amount,
        discount_total=invoice.discount_total,
        taxable_total=invoice.taxable_total,
        cgst_total=invoice.cgst_total,
        sgst_total=invoice.sgst_total,
        igst_total=invoice.igst_total,
        tax_total=invoice.tax_total,
        round_off=invoice.round_off,
        grand_total=invoice.grand_total,
        amount_paid=invoice.amount_paid,
        paid_at_sale=invoice.paid_at_sale,
        return_credit_applied=invoice.return_credit_applied,
        returned_value=invoice.returned_value,
        refunded_amount=invoice.refunded_amount,
        written_off_amount=invoice.written_off_amount,
        balance_due=invoice.balance_due,
        payment_status=invoice.payment_status,
        return_status=invoice.return_status,
        is_credit_sale=invoice.is_credit_sale,
        due_date=invoice.due_date,
        overdue_days=overdue_days(invoice) if invoice.status == "FINALIZED" else 0,
        prescription_required=invoice.prescription_required,
        has_prescription=invoice.prescription is not None,
        reminders_paused=invoice.reminders_paused,
        is_disputed=invoice.is_disputed,
        dispute_note=invoice.dispute_note,
        notes=invoice.notes,
        print_count=invoice.print_count,
        voided_at=invoice.voided_at,
        void_reason=invoice.void_reason,
        replayed=replayed,
        change_due=change_due,
        items=[InvoiceItemResponse.model_validate(i) for i in invoice.items],
        payments=payments,
        refunds=[RefundSummary(
            id=r.id, method=r.method, amount=r.amount, reference=r.reference, reason=r.reason,
            return_id=r.return_id, refunded_at=r.refunded_at,
        ) for r in refunds],
        returns=[ReturnBrief(
            id=r.id, return_number=r.return_number, total_amount=r.total_amount,
            refund_amount=r.refund_amount, applied_to_balance=r.applied_to_balance,
            credit_note_amount=r.credit_note_amount, created_at=r.created_at,
        ) for r in returns],
    )


def invoice_filters(
    pharmacy_id: int, *, q: Optional[str] = None, date_from: Optional[date] = None, date_to: Optional[date] = None,
    cashier_id: Optional[int] = None, payment_method: Optional[str] = None, payment_status: Optional[str] = None,
    status_filter: Optional[str] = None, customer_id: Optional[int] = None, overdue_only: bool = False,
    credit_only: bool = False,
):
    conds = [SalesInvoice.pharmacy_id == pharmacy_id]
    if q:
        like = f"%{q.strip()}%"
        conds.append(or_(
            SalesInvoice.invoice_number.ilike(like),
            SalesInvoice.customer_name.ilike(like),
            SalesInvoice.customer_mobile.ilike(like),
            exists().where(and_(SalesInvoiceItem.invoice_id == SalesInvoice.id, SalesInvoiceItem.medicine_name.ilike(like))),
        ))
    if date_from:
        conds.append(SalesInvoice.invoice_date >= day_bounds(date_from)[0])
    if date_to:
        conds.append(SalesInvoice.invoice_date < day_bounds(date_to)[1])
    if cashier_id:
        conds.append(SalesInvoice.cashier_id == cashier_id)
    if payment_status:
        conds.append(SalesInvoice.payment_status == payment_status.upper())
    if status_filter:
        conds.append(SalesInvoice.status == status_filter.upper())
    if customer_id:
        conds.append(SalesInvoice.customer_id == customer_id)
    if credit_only:
        conds.append(SalesInvoice.is_credit_sale.is_(True))
    if overdue_only:
        conds += [SalesInvoice.status == "FINALIZED", SalesInvoice.balance_due > 0,
                  SalesInvoice.due_date < today_local()]
    if payment_method:
        conds.append(exists().where(and_(
            PaymentAllocation.invoice_id == SalesInvoice.id,
            PaymentAllocation.is_reversed.is_(False),
            CustomerPayment.id == PaymentAllocation.payment_id,
            CustomerPayment.method == payment_method.upper(),
        )))
    return conds


async def list_invoices(db: AsyncSession, pharmacy_id: int, limit: int, offset: int, **filters):
    conds = invoice_filters(pharmacy_id, **filters)
    total = (await db.execute(select(func.count(SalesInvoice.id)).where(*conds))).scalar_one()
    invoices = (await db.execute(
        select(SalesInvoice).where(*conds)
        .order_by(SalesInvoice.invoice_date.desc(), SalesInvoice.id.desc())
        .offset(offset).limit(limit)
    )).scalars().all()
    return await list_items(db, invoices), total


async def list_items(db: AsyncSession, invoices) -> List[InvoiceListItem]:
    ids = [i.id for i in invoices]
    methods: dict = {}
    if ids:
        for inv_id, method in (await db.execute(
            select(PaymentAllocation.invoice_id, CustomerPayment.method)
            .join(CustomerPayment, CustomerPayment.id == PaymentAllocation.payment_id)
            .where(PaymentAllocation.invoice_id.in_(ids), PaymentAllocation.is_reversed.is_(False))
        )).all():
            methods.setdefault(inv_id, set()).add(method)
    names = await _user_names(db, [i.cashier_id for i in invoices])
    return [InvoiceListItem(
        id=i.id, invoice_number=i.invoice_number, invoice_date=i.invoice_date, status=i.status,
        customer_id=i.customer_id, customer_name=i.customer_name, customer_mobile=i.customer_mobile,
        cashier_name=names.get(i.cashier_id), grand_total=i.grand_total, amount_paid=i.amount_paid,
        balance_due=i.balance_due, payment_status=i.payment_status, return_status=i.return_status,
        is_credit_sale=i.is_credit_sale, due_date=i.due_date,
        overdue_days=overdue_days(i) if i.status == "FINALIZED" else 0,
        payment_methods=", ".join(sorted(methods.get(i.id, set()))) or ("CREDIT" if i.is_credit_sale else ""),
    ) for i in invoices]


# ── Void ───────────────────────────────────────────────────────────────────────

async def void_invoice(db: AsyncSession, user: User, perms: set, invoice_id: int, body: VoidRequest) -> SalesInvoice:
    """
    Cancel a finalised sale with reversal entries: stock goes back to its batches
    (SALE_VOID movements), payment allocations are reversed and the money is either
    refunded or kept as customer credit, and the ledger gets a VOID_REVERSAL.
    Nothing is deleted. Invoices with returns or write-offs must be corrected by
    returns instead, so the books stay consistent.
    """
    require(perms, "sales.cancel", "void invoices")
    pharmacy_id = user.pharmacy_id
    invoice = await get_invoice(db, pharmacy_id, invoice_id, lock=True)
    if invoice.status == "VOIDED":
        raise HTTPException(status_code=409, detail=f"Invoice {invoice.invoice_number} is already voided")
    if invoice.return_status != "NONE" or invoice.returned_value > ZERO:
        raise HTTPException(status_code=409, detail="Invoices with returns cannot be voided; return the remaining items instead")
    if invoice.written_off_amount > ZERO:
        raise HTTPException(status_code=409, detail="Invoices with ledger adjustments cannot be voided")
    if body.settlement == "CUSTOMER_CREDIT" and invoice.customer_id is None:
        raise HTTPException(status_code=422, detail="Walk-in sales can only be voided with a refund")

    # Stock back to the same batches
    for item in invoice.items:
        batch = (await db.execute(
            select(MedicineBatch).where(MedicineBatch.id == item.batch_id).with_for_update()
        )).scalar_one()
        batch.current_stock_base = money(Decimal(str(batch.current_stock_base)) + item.quantity_base)
        db.add(StockMovement(
            pharmacy_id=pharmacy_id, medicine_id=item.medicine_id, batch_id=batch.id,
            movement_type="SALE_VOID", reference_id=invoice.invoice_number, reference_type="sales_invoice",
            quantity_base=item.quantity_base, balance_after_base=batch.current_stock_base,
            unit_cost=item.cost_per_base_unit, total_cost=item.cost_total,
            reason=f"Void: {body.reason}"[:255], user_id=user.id,
        ))

    if invoice.customer_id:
        await lock_customer(db, pharmacy_id, invoice.customer_id)
        post_ledger(
            db, pharmacy_id=pharmacy_id, customer_id=invoice.customer_id, entry_type="VOID_REVERSAL",
            user_id=user.id, reference=invoice.invoice_number,
            description=f"Invoice {invoice.invoice_number} voided", credit=invoice.grand_total,
            invoice_id=invoice.id, reason=body.reason,
        )

    allocations = (await db.execute(
        select(PaymentAllocation, CustomerPayment)
        .join(CustomerPayment, CustomerPayment.id == PaymentAllocation.payment_id)
        .where(PaymentAllocation.invoice_id == invoice.id, PaymentAllocation.is_reversed.is_(False))
        .with_for_update()
    )).all()
    refund_ids = []
    for alloc, payment in allocations:
        alloc.is_reversed = True
        alloc.reversed_at = utcnow()
        if body.settlement == "REFUND" and payment.source != "RETURN_CREDIT":
            shift_id = await shift_for_cash(db, pharmacy_id, user.id, payment.method == "CASH")
            refund = CustomerRefund(
                pharmacy_id=pharmacy_id, customer_id=invoice.customer_id, invoice_id=invoice.id,
                method=payment.method, amount=alloc.amount, reference=payment.reference,
                reason=f"Void {invoice.invoice_number}: {body.reason}"[:500],
                shift_id=shift_id, refunded_by=user.id,
            )
            db.add(refund)
            await db.flush()
            refund_ids.append(refund.id)
            payment.refunded_amount = money(Decimal(str(payment.refunded_amount)) + alloc.amount)
            invoice.refunded_amount = money(Decimal(str(invoice.refunded_amount)) + alloc.amount)
            if invoice.customer_id:
                post_ledger(
                    db, pharmacy_id=pharmacy_id, customer_id=invoice.customer_id, entry_type="REFUND",
                    user_id=user.id, reference=invoice.invoice_number,
                    description=f"{payment.method} refund for voided {invoice.invoice_number}",
                    debit=alloc.amount, invoice_id=invoice.id, refund_id=refund.id, reason=body.reason,
                )

    invoice.amount_paid = ZERO
    invoice.status = "VOIDED"
    invoice.voided_at = utcnow()
    invoice.voided_by = user.id
    invoice.void_reason = body.reason
    refresh_settlement(invoice)

    # Pending messages about this invoice are no longer valid
    for msg in (await db.execute(
        select(MessageOutbox).where(
            MessageOutbox.invoice_id == invoice.id, MessageOutbox.status.in_(("PENDING", "QUEUED")),
        )
    )).scalars().all():
        msg.status = "CANCELLED"
        msg.cancelled_reason = "Invoice voided"

    db.add(AuditLog(
        pharmacy_id=pharmacy_id, user_id=user.id, action="sale.void", entity_type="sales_invoice",
        entity_id=str(invoice.id), reason=body.reason,
        metadata_json=json.dumps({"invoice_number": invoice.invoice_number, "settlement": body.settlement,
                                  "grand_total": str(invoice.grand_total)}),
    ))
    await db.flush()
    await gl_posting.record(db, pharmacy_id, user.id, "SALE_VOID", invoice.id)
    for refund_id in refund_ids:
        await gl_posting.record(db, pharmacy_id, user.id, "CUSTOMER_REFUND", refund_id)
    return invoice


async def record_print(db: AsyncSession, user: User, invoice_id: int, layout: str) -> Tuple[str, int]:
    """Printing never touches stock or money; it only counts copies for the ORIGINAL/DUPLICATE label."""
    invoice = await get_invoice(db, user.pharmacy_id, invoice_id, lock=True)
    invoice.print_count += 1
    label = "ORIGINAL" if invoice.print_count == 1 else "DUPLICATE"
    db.add(AuditLog(
        pharmacy_id=user.pharmacy_id, user_id=user.id, action="sale.print", entity_type="sales_invoice",
        entity_id=str(invoice.id),
        metadata_json=json.dumps({"invoice_number": invoice.invoice_number, "copy": label, "layout": layout}),
    ))
    await db.flush()
    return label, invoice.print_count
