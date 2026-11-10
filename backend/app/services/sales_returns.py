"""
Sales returns against finalised invoices.

Value: each returned quantity is valued at its share of the original line
(the price actually charged after discount, GST included). When a line is
returned completely, the remaining value is used exactly, so repeated partial
returns never drift by a paisa. When the whole invoice has been returned, the
bill's round-off is returned too.

Money: the return value first reduces whatever is still due on the invoice;
the rest is refunded (cash / original method) or kept as customer credit.

Stock: only items returned in SALEABLE condition into an ACTIVE, unexpired batch
go back on the shelf (SALES_RETURN movement). Damaged, expired or opened items
are recorded as WRITTEN_OFF and never re-enter saleable stock.
"""
import json
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth import AuditLog, User
from app.models.inventory import Medicine, MedicineBatch, StockMovement, Unit
from app.models.sales import (
    CustomerPayment, CustomerRefund, PaymentAllocation, SalesInvoice, SalesInvoiceItem, SalesReturn,
    SalesReturnItem,
)
from app.schemas.sales import ReturnItemResponse, ReturnRequest, ReturnResponse
from app.services.sales import _user_names, get_invoice
from app.services.sales_common import (
    ZERO, four, lock_customer, money, next_sales_number, post_ledger, refresh_settlement, require,
    shift_for_cash, today_local, utcnow,
)
from app.services.units import UnitConversionService
from app.services import gl_posting


async def _previously_returned(db: AsyncSession, invoice_item_id: int) -> dict:
    row = (await db.execute(
        select(
            func.coalesce(func.sum(SalesReturnItem.taxable_amount), 0),
            func.coalesce(func.sum(SalesReturnItem.cgst_amount), 0),
            func.coalesce(func.sum(SalesReturnItem.sgst_amount), 0),
            func.coalesce(func.sum(SalesReturnItem.igst_amount), 0),
            func.coalesce(func.sum(SalesReturnItem.line_total), 0),
            func.coalesce(func.sum(SalesReturnItem.cost_total), 0),
        ).where(SalesReturnItem.invoice_item_id == invoice_item_id)
    )).one()
    keys = ("taxable", "cgst", "sgst", "igst", "total", "cost")
    return {k: money(v) for k, v in zip(keys, row)}


def _share(item: SalesInvoiceItem, qty: Decimal, prior: dict, closes_line: bool) -> dict:
    if closes_line:
        return {
            "taxable": money(item.taxable_amount - prior["taxable"]),
            "cgst": money(item.cgst_amount - prior["cgst"]),
            "sgst": money(item.sgst_amount - prior["sgst"]),
            "igst": money(item.igst_amount - prior["igst"]),
            "total": money(item.line_total - prior["total"]),
            "cost": money(item.cost_total - prior["cost"]),
        }
    ratio = qty / Decimal(str(item.quantity_base))
    total = money(item.line_total * ratio)
    cgst = money(item.cgst_amount * ratio)
    sgst = money(item.sgst_amount * ratio)
    igst = money(item.igst_amount * ratio)
    return {
        "taxable": money(total - cgst - sgst - igst), "cgst": cgst, "sgst": sgst, "igst": igst,
        "total": total, "cost": money(qty * item.cost_per_base_unit),
    }


async def return_by_key(db: AsyncSession, pharmacy_id: int, key: str, lock: bool = False) -> Optional[SalesReturn]:
    stmt = select(SalesReturn).where(SalesReturn.pharmacy_id == pharmacy_id, SalesReturn.idempotency_key == key)
    if lock:
        stmt = stmt.with_for_update(read=True)
    return (await db.execute(stmt)).scalar_one_or_none()


async def _original_method(db: AsyncSession, invoice_id: int) -> str:
    row = (await db.execute(
        select(CustomerPayment.method)
        .join(PaymentAllocation, PaymentAllocation.payment_id == CustomerPayment.id)
        .where(PaymentAllocation.invoice_id == invoice_id, CustomerPayment.method != "CREDIT_NOTE")
        .order_by(PaymentAllocation.amount.desc())
        .limit(1)
    )).scalar_one_or_none()
    return row or "CASH"


async def create_return(db: AsyncSession, user: User, perms: set, body: ReturnRequest):
    require(perms, "sales.refund", "process returns and refunds")
    pharmacy_id = user.pharmacy_id
    existing = await return_by_key(db, pharmacy_id, body.idempotency_key)
    if existing:
        return existing, True

    invoice = await get_invoice(db, pharmacy_id, body.invoice_id, lock=True)
    # Duplicate submissions wait on the invoice lock; check again with a locking read
    existing = await return_by_key(db, pharmacy_id, body.idempotency_key, lock=True)
    if existing:
        return existing, True
    if invoice.status != "FINALIZED":
        raise HTTPException(status_code=409, detail=f"Invoice {invoice.invoice_number} is {invoice.status.lower()}")
    if invoice.customer_id:
        await lock_customer(db, pharmacy_id, invoice.customer_id)
    if body.settlement == "CUSTOMER_CREDIT" and not invoice.customer_id:
        raise HTTPException(status_code=422, detail="Walk-in returns can only be refunded")

    items = {i.id: i for i in invoice.items}
    return_number = await next_sales_number(
        db, pharmacy_id, "SALES_RETURN", "invoice.return_prefix", "SR",
        SalesReturn.return_number, SalesReturn.pharmacy_id,
    )
    sret = SalesReturn(
        pharmacy_id=pharmacy_id, return_number=return_number, idempotency_key=body.idempotency_key,
        invoice_id=invoice.id, customer_id=invoice.customer_id, status="POSTED", reason=body.reason.strip(),
        processed_by=user.id,
    )
    db.add(sret)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail="This return was already submitted")

    seen = set()
    totals = {"taxable": ZERO, "cgst": ZERO, "sgst": ZERO, "igst": ZERO, "total": ZERO}
    today = today_local()
    for line in body.items:
        if line.invoice_item_id in seen:
            raise HTTPException(status_code=422, detail="Each invoice line may appear only once")
        seen.add(line.invoice_item_id)
        item = items.get(line.invoice_item_id)
        if item is None:
            raise HTTPException(status_code=422, detail=f"Line {line.invoice_item_id} is not on invoice {invoice.invoice_number}")
        qty = money(line.quantity_base)
        returnable = money(item.quantity_base - item.returned_quantity_base)
        if qty > returnable:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"'{item.medicine_name}' batch {item.batch_number}: only {returnable} of "
                    f"{item.quantity_base} sold can still be returned"
                ),
            )
        batch = (await db.execute(
            select(MedicineBatch).where(MedicineBatch.id == item.batch_id).with_for_update()
        )).scalar_one()
        fractional = (await db.execute(
            select(Unit.is_fractional_allowed).join(Medicine, Medicine.base_unit_id == Unit.id)
            .where(Medicine.id == item.medicine_id)
        )).scalar_one()
        if not UnitConversionService.validate_whole_units(qty, fractional):
            raise HTTPException(status_code=422, detail=f"'{item.medicine_name}' must be returned in whole units")

        prior = await _previously_returned(db, item.id)
        closes_line = qty == returnable
        share = _share(item, qty, prior, closes_line)
        item.returned_quantity_base = money(item.returned_quantity_base + qty)

        restock = (line.condition == "SALEABLE" and batch.stock_status == "ACTIVE"
                   and batch.is_active and batch.expiry_date > today)
        movement_id = None
        if restock:
            old_qty = Decimal(str(batch.current_stock_base))
            new_qty = money(old_qty + qty)
            if new_qty > ZERO:
                # Keep the batch's weighted average cost consistent with the returned units
                batch.cost_per_base_unit = four(
                    (old_qty * batch.cost_per_base_unit + qty * item.cost_per_base_unit) / new_qty
                )
            batch.current_stock_base = new_qty
            movement = StockMovement(
                pharmacy_id=pharmacy_id, medicine_id=item.medicine_id, batch_id=batch.id,
                movement_type="SALES_RETURN", reference_id=return_number, reference_type="sales_return",
                quantity_base=qty, balance_after_base=new_qty, unit_cost=item.cost_per_base_unit,
                total_cost=share["cost"], reason=f"Return against {invoice.invoice_number}",
                notes=body.reason[:255], user_id=user.id,
            )
            db.add(movement)
            await db.flush()
            movement_id = movement.id

        db.add(SalesReturnItem(
            return_id=sret.id, invoice_item_id=item.id, medicine_id=item.medicine_id, batch_id=item.batch_id,
            quantity_base=qty, condition=line.condition,
            disposition="RESTOCKED" if restock else "WRITTEN_OFF", stock_movement_id=movement_id,
            taxable_amount=share["taxable"], cgst_amount=share["cgst"], sgst_amount=share["sgst"],
            igst_amount=share["igst"], tax_amount=money(share["cgst"] + share["sgst"] + share["igst"]),
            line_total=share["total"], cost_total=share["cost"],
        ))
        for k in totals:
            totals[k] += share[k]

    refund_id = None
    fully_returned = all(i.returned_quantity_base >= i.quantity_base for i in invoice.items)
    total = money(totals["total"])
    if fully_returned:
        total = money(total + invoice.round_off)   # The bill's rounding goes back with the last item
    sret.taxable_total = money(totals["taxable"])
    sret.cgst_total = money(totals["cgst"])
    sret.sgst_total = money(totals["sgst"])
    sret.igst_total = money(totals["igst"])
    sret.tax_total = money(totals["cgst"] + totals["sgst"] + totals["igst"])
    sret.total_amount = total

    # Settle the value: reduce what is still due, then refund or keep as credit
    applied = min(total, Decimal(str(invoice.balance_due)))
    remainder = money(total - applied)
    invoice.return_credit_applied = money(invoice.return_credit_applied + applied)
    invoice.returned_value = money(invoice.returned_value + total)
    invoice.return_status = "FULL" if fully_returned else "PARTIAL"
    sret.applied_to_balance = money(applied)
    refresh_settlement(invoice)

    if invoice.customer_id:
        post_ledger(
            db, pharmacy_id=pharmacy_id, customer_id=invoice.customer_id, entry_type="RETURN_CREDIT",
            user_id=user.id, reference=return_number, credit=total, invoice_id=invoice.id, return_id=sret.id,
            description=f"Return {return_number} against {invoice.invoice_number}", reason=body.reason,
        )

    if remainder > ZERO:
        if body.settlement == "REFUND":
            method = body.refund_method or await _original_method(db, invoice.id)
            refundable = money(invoice.amount_paid - invoice.refunded_amount)
            if remainder > refundable:
                raise HTTPException(status_code=409, detail=f"Refund ₹{remainder} exceeds the ₹{refundable} paid on this invoice")
            refund = CustomerRefund(
                pharmacy_id=pharmacy_id, customer_id=invoice.customer_id, invoice_id=invoice.id,
                return_id=sret.id, method=method, amount=remainder,
                reference=(body.refund_reference or "").strip() or None,
                reason=f"Return {return_number}: {body.reason}"[:500],
                shift_id=await shift_for_cash(db, pharmacy_id, user.id, method == "CASH"),
                refunded_by=user.id,
            )
            db.add(refund)
            await db.flush()
            refund_id = refund.id
            invoice.refunded_amount = money(invoice.refunded_amount + remainder)
            sret.refund_amount = remainder
            sret.refund_method = method
            sret.refund_reference = refund.reference
            if invoice.customer_id:
                post_ledger(
                    db, pharmacy_id=pharmacy_id, customer_id=invoice.customer_id, entry_type="REFUND",
                    user_id=user.id, reference=return_number, debit=remainder, invoice_id=invoice.id,
                    return_id=sret.id, refund_id=refund.id,
                    description=f"{method} refund for return {return_number}",
                )
        else:
            # Customer credit note: an unapplied credit usable against future invoices.
            # The ledger was already credited by RETURN_CREDIT above.
            receipt = await next_sales_number(
                db, pharmacy_id, "RECEIPT", "invoice.receipt_prefix", "RCPT",
                CustomerPayment.receipt_number, CustomerPayment.pharmacy_id,
            )
            db.add(CustomerPayment(
                pharmacy_id=pharmacy_id, receipt_number=receipt, customer_id=invoice.customer_id,
                source="RETURN_CREDIT", method="CREDIT_NOTE", amount=remainder,
                verification_status="NOT_APPLICABLE", notes=f"Credit note from {return_number}",
                received_by=user.id,
            ))
            sret.credit_note_amount = remainder

    db.add(AuditLog(
        pharmacy_id=pharmacy_id, user_id=user.id, action="sale.return", entity_type="sales_return",
        entity_id=str(sret.id), reason=body.reason,
        metadata_json=json.dumps({
            "return_number": return_number, "invoice_number": invoice.invoice_number,
            "total": str(total), "applied_to_balance": str(applied), "refund": str(sret.refund_amount),
            "credit_note": str(sret.credit_note_amount),
        }),
    ))
    await db.flush()
    await gl_posting.record(db, pharmacy_id, user.id, "SALES_RETURN", sret.id)
    if refund_id:
        await gl_posting.record(db, pharmacy_id, user.id, "CUSTOMER_REFUND", refund_id)
    return sret, False


async def return_response(db: AsyncSession, sret: SalesReturn, replayed: bool = False) -> ReturnResponse:
    await db.refresh(sret, attribute_names=["items"])
    invoice = (await db.execute(select(SalesInvoice).where(SalesInvoice.id == sret.invoice_id))).scalar_one()
    item_rows = {
        i.id: i for i in (await db.execute(
            select(SalesInvoiceItem).where(SalesInvoiceItem.id.in_([ri.invoice_item_id for ri in sret.items] or [0]))
        )).scalars().all()
    }
    names = await _user_names(db, [sret.processed_by])
    return ReturnResponse(
        id=sret.id, return_number=sret.return_number, invoice_id=sret.invoice_id,
        invoice_number=invoice.invoice_number, customer_id=sret.customer_id, customer_name=invoice.customer_name,
        reason=sret.reason, taxable_total=sret.taxable_total, cgst_total=sret.cgst_total,
        sgst_total=sret.sgst_total, igst_total=sret.igst_total, tax_total=sret.tax_total,
        total_amount=sret.total_amount, applied_to_balance=sret.applied_to_balance,
        refund_amount=sret.refund_amount, credit_note_amount=sret.credit_note_amount,
        refund_method=sret.refund_method, refund_reference=sret.refund_reference,
        processed_by_name=names.get(sret.processed_by), created_at=sret.created_at, replayed=replayed,
        items=[ReturnItemResponse(
            id=ri.id, invoice_item_id=ri.invoice_item_id, medicine_id=ri.medicine_id,
            medicine_name=item_rows[ri.invoice_item_id].medicine_name if ri.invoice_item_id in item_rows else None,
            batch_id=ri.batch_id,
            batch_number=item_rows[ri.invoice_item_id].batch_number if ri.invoice_item_id in item_rows else None,
            quantity_base=ri.quantity_base, condition=ri.condition, disposition=ri.disposition,
            taxable_amount=ri.taxable_amount, tax_amount=ri.tax_amount, line_total=ri.line_total,
        ) for ri in sret.items],
    )
