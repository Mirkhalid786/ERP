"""
Supplier invoice workflow: DRAFT -> POSTED (finalised) -> CANCELLED.

* A DRAFT can be edited (full replace, audited with before/after values) or cancelled.
* Finalising validates the bill against its lines, the supplier's printed total, the
  linked goods receipt and other finalised bills for the same GRN lines. Errors block;
  warnings must be acknowledged. A finalised bill is the point at which the amount is
  owed to the supplier (the Stage 5B posting point). It can no longer be edited.
* A finalised bill can be cancelled (separate permission + reason) only while no
  payment is allocated to it and no posted supplier return refers to it. The cancelled
  original is kept; the corrected bill is entered again and points at it through
  `replaces_invoice_id`. Cancelling frees the invoice number for that re-entry.
"""
import json
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import today_local
from app.models.auth import AuditLog, User
from app.models.purchasing import (
    GoodsReceipt, GoodsReceiptItem, SupplierInvoice, SupplierInvoiceItem, SupplierReturn,
)
from app.models.payables import SupplierPaymentAllocation
from app.schemas.purchasing import SupplierInvoiceCreate
from app.services import app_settings
from app.services import gl_posting
from app.services.purchasing import SupplierInvoiceService, SupplierService, invoice_number_key, round_cur
from app.services.sales_common import ZERO, money, require, utcnow


def _audit(db: AsyncSession, user: User, action: str, invoice: SupplierInvoice, details: dict, reason: str = None):
    db.add(AuditLog(pharmacy_id=user.pharmacy_id, user_id=user.id, action=action, entity_type="supplier_invoice",
                    entity_id=str(invoice.id), reason=reason, metadata_json=json.dumps(details, default=str)))


def _snapshot(invoice: SupplierInvoice) -> dict:
    return {
        "invoice_number": invoice.invoice_number, "supplier_id": invoice.supplier_id, "grn_id": invoice.grn_id,
        "invoice_date": invoice.invoice_date, "due_date": invoice.due_date,
        "calculated_total": invoice.calculated_grand_total, "declared_total": invoice.supplier_declared_total,
        "tax_total": invoice.tax_total, "lines": len(invoice.items or []),
    }


async def lock_invoice(db: AsyncSession, pharmacy_id: int, invoice_id: int) -> SupplierInvoice:
    found = (await db.execute(select(SupplierInvoice.id).where(
        SupplierInvoice.id == invoice_id, SupplierInvoice.pharmacy_id == pharmacy_id).with_for_update())).scalar_one_or_none()
    if found is None:
        raise HTTPException(status_code=404, detail="Supplier invoice not found")
    invoice = await SupplierInvoiceService.get_invoice(db, pharmacy_id, invoice_id)
    await db.refresh(invoice)
    await db.refresh(invoice, attribute_names=["items"])
    return invoice


async def update_draft(db: AsyncSession, user: User, invoice_id: int, payload: SupplierInvoiceCreate) -> SupplierInvoice:
    invoice = await lock_invoice(db, user.pharmacy_id, invoice_id)
    if invoice.status != "DRAFT":
        raise HTTPException(status_code=409, detail=f"Only draft invoices can be edited; this one is {invoice.status.lower()}")
    before = _snapshot(invoice)
    supplier = await SupplierService.get_supplier(db, user.pharmacy_id, payload.supplier_id)
    number = payload.invoice_number.strip().upper()
    key = invoice_number_key(number, payload.invoice_date)
    await SupplierInvoiceService.assert_number_free(db, user.pharmacy_id, supplier, key, exclude_id=invoice.id)
    await SupplierInvoiceService.validate_links(db, user.pharmacy_id, supplier, payload)

    for item in list(invoice.items):
        await db.delete(item)
    await db.flush()
    from datetime import timedelta
    invoice.supplier_id = supplier.id
    invoice.invoice_number, invoice.number_key = number, key
    invoice.grn_id, invoice.po_id = payload.grn_id, payload.po_id
    invoice.invoice_date, invoice.received_date = payload.invoice_date, payload.received_date
    invoice.due_date = payload.due_date or payload.invoice_date + timedelta(days=payload.payment_terms_days)
    invoice.payment_terms_days = payload.payment_terms_days
    invoice.is_tax_inclusive, invoice.currency = payload.is_tax_inclusive, payload.currency
    invoice.other_charges, invoice.round_off = payload.other_charges, payload.round_off
    invoice.supplier_declared_total = payload.supplier_declared_total
    invoice.discrepancy_notes, invoice.notes = payload.discrepancy_notes, payload.notes
    invoice.replaces_invoice_id = payload.replaces_invoice_id
    await SupplierInvoiceService.apply_lines(db, user.pharmacy_id, supplier, invoice, payload)
    await db.flush()
    invoice = await lock_invoice(db, user.pharmacy_id, invoice.id)
    _audit(db, user, "supplier_invoice.update", invoice, {"before": before, "after": _snapshot(invoice)})
    return invoice


async def finalize_check(db: AsyncSession, invoice: SupplierInvoice) -> dict:
    """Validation for finalising a draft. Errors block; warnings need acknowledgement."""
    errors, warnings = [], []
    pid = invoice.pharmacy_id
    if invoice.status != "DRAFT":
        errors.append(f"Invoice is {invoice.status.lower()}; only drafts can be finalised")
    items = list(invoice.items or [])
    if not items:
        errors.append("The invoice has no lines")
    if invoice.invoice_date > today_local():
        errors.append(f"Invoice date {invoice.invoice_date} is in the future")
    if invoice.due_date < invoice.invoice_date:
        errors.append("Due date is before the invoice date")
    if invoice.received_date < invoice.invoice_date:
        warnings.append("Received date is before the invoice date")

    # Totals must still agree with the lines (guards against edited or inconsistent rows)
    lines_total = sum((money(i.line_total) for i in items), ZERO)
    expected_total = round_cur(lines_total + money(invoice.other_charges) + money(invoice.round_off))
    if expected_total != money(invoice.calculated_grand_total):
        errors.append(f"Stored total ₹{invoice.calculated_grand_total} does not match its lines (₹{expected_total}); edit and save the draft again")
    for i in items:
        if money(i.taxable_amount) + money(i.tax_amount) != money(i.line_total):
            errors.append(f"Line for batch {i.batch_number}: taxable + tax does not equal the line total")
        if money(i.cgst_amount) + money(i.sgst_amount) + money(i.igst_amount) != money(i.tax_amount):
            errors.append(f"Line for batch {i.batch_number}: CGST + SGST + IGST does not equal the line tax")
        if money(i.igst_amount) > ZERO and (money(i.cgst_amount) > ZERO or money(i.sgst_amount) > ZERO):
            errors.append(f"Line for batch {i.batch_number}: has both IGST and CGST/SGST")
    if abs(money(invoice.round_off)) > Decimal("1.00"):
        warnings.append(f"Round-off of ₹{invoice.round_off} is more than ₹1")

    tolerance = await app_settings.get_decimal(db, pid, "purchases.invoice_discrepancy_tolerance")
    diff = money(invoice.discrepancy_amount)
    if diff != ZERO:
        explained = bool((invoice.discrepancy_notes or "").strip())
        if abs(diff) > tolerance and not explained:
            errors.append(f"Calculated total differs from the supplier's bill by ₹{diff} (tolerance ₹{tolerance}); "
                          "correct the lines or explain the difference in the discrepancy notes")
        else:
            warnings.append(f"Calculated total differs from the supplier's bill by ₹{diff}; the ERP-calculated "
                            f"₹{invoice.calculated_grand_total} will be the amount payable")

    if invoice.grn_id is None:
        warnings.append("No goods receipt is linked: the bill cannot be matched to stock received")
    else:
        grn = (await db.execute(select(GoodsReceipt).where(GoodsReceipt.id == invoice.grn_id))).scalar_one()
        if grn.status != "POSTED":
            errors.append(f"Goods receipt {grn.grn_number} is {grn.status.lower()}; post it before finalising the bill")
        grn_items = {gi.id: gi for gi in (await db.execute(
            select(GoodsReceiptItem).where(GoodsReceiptItem.grn_id == grn.id))).scalars().all()}
        other_bills = (await db.execute(select(SupplierInvoice.invoice_number).where(
            SupplierInvoice.grn_id == grn.id, SupplierInvoice.status == "POSTED",
            SupplierInvoice.id != invoice.id))).scalars().all()
        if other_bills:
            warnings.append(f"Goods receipt {grn.grn_number} is already billed on {', '.join(other_bills)}")
        unlinked = [i.batch_number for i in items if not i.grn_item_id]
        if unlinked:
            warnings.append(f"Lines not matched to a GRN line: {', '.join(unlinked)}")
        billed_here: dict = {}
        for i in items:
            if i.grn_item_id:
                billed_here[i.grn_item_id] = billed_here.get(i.grn_item_id, ZERO) + money(i.billed_qty_base)
                gi = grn_items.get(i.grn_item_id)
                if gi and money(i.purchase_rate) != money(gi.purchase_rate):
                    warnings.append(f"Batch {i.batch_number}: billed rate ₹{i.purchase_rate} differs from GRN rate ₹{gi.purchase_rate}")
        for grn_item_id, qty in billed_here.items():
            gi = grn_items[grn_item_id]
            received = money(gi.received_qty_pack * gi.conversion_factor)
            already = money((await db.execute(
                select(func.coalesce(func.sum(SupplierInvoiceItem.billed_qty_base), 0))
                .join(SupplierInvoice, SupplierInvoice.id == SupplierInvoiceItem.invoice_id)
                .where(SupplierInvoiceItem.grn_item_id == grn_item_id, SupplierInvoice.status == "POSTED",
                       SupplierInvoice.id != invoice.id))).scalar_one())
            if qty + already > received:
                errors.append(f"Batch {gi.batch_number}: billed {qty + already} units in total but only {received} "
                              "paid units were received on the GRN")
            elif qty + already < received:
                warnings.append(f"Batch {gi.batch_number}: {received - qty - already} received units are not billed yet")
    return {"errors": errors, "warnings": warnings, "can_finalize": not errors,
            "calculated_total": invoice.calculated_grand_total, "declared_total": invoice.supplier_declared_total,
            "discrepancy": invoice.discrepancy_amount}


async def finalize(db: AsyncSession, user: User, perms: set, invoice_id: int, acknowledge_warnings: bool) -> SupplierInvoice:
    require(perms, "purchases.invoice_finalize", "finalise supplier invoices")
    invoice = await lock_invoice(db, user.pharmacy_id, invoice_id)
    if invoice.status == "POSTED":
        raise HTTPException(status_code=409, detail="Invoice is already finalised")
    if invoice.grn_id:
        # Serialise finalising bills for the same GRN (billed-quantity check)
        await db.execute(select(GoodsReceipt.id).where(GoodsReceipt.id == invoice.grn_id).with_for_update())
    check = await finalize_check(db, invoice)
    if check["errors"]:
        raise HTTPException(status_code=422, detail={"message": "The invoice cannot be finalised", "errors": check["errors"]})
    if check["warnings"] and not acknowledge_warnings:
        raise HTTPException(status_code=409, detail={"message": "Review and acknowledge the warnings", "warnings": check["warnings"]})
    invoice.status = "POSTED"
    invoice.discrepancy_accepted = money(invoice.discrepancy_amount) != ZERO
    invoice.finalized_by, invoice.finalized_at = user.id, utcnow()
    _audit(db, user, "supplier_invoice.finalize", invoice, {**_snapshot(invoice), "warnings_acknowledged": check["warnings"]})
    await db.flush()
    await gl_posting.record(db, user.pharmacy_id, user.id, "SUPPLIER_BILL", invoice.id)
    return invoice


async def cancel(db: AsyncSession, user: User, perms: set, invoice_id: int, reason: str) -> SupplierInvoice:
    invoice = await lock_invoice(db, user.pharmacy_id, invoice_id)
    if invoice.status == "CANCELLED":
        raise HTTPException(status_code=409, detail="Invoice is already cancelled")
    if invoice.status == "POSTED":
        require(perms, "purchases.invoice_cancel", "cancel finalised supplier invoices")
    else:
        require(perms, "purchases.create", "cancel draft supplier invoices")
    allocated = money((await db.execute(select(func.coalesce(func.sum(SupplierPaymentAllocation.amount), 0)).where(
        SupplierPaymentAllocation.invoice_id == invoice.id, SupplierPaymentAllocation.status == "ACTIVE"))).scalar_one())
    if allocated > ZERO:
        raise HTTPException(status_code=409, detail=f"₹{allocated} of supplier payments is allocated to this invoice; "
                                                    "reverse those payments first")
    returns = (await db.execute(select(SupplierReturn.return_number).where(
        SupplierReturn.invoice_id == invoice.id, SupplierReturn.status == "POSTED"))).scalars().all()
    if returns:
        raise HTTPException(status_code=409, detail=f"Posted supplier returns refer to this invoice: {', '.join(returns)}")
    from app.models.payables import SupplierCreditNote
    notes = (await db.execute(select(SupplierCreditNote.credit_note_number).where(
        SupplierCreditNote.invoice_id == invoice.id, SupplierCreditNote.status == "POSTED"))).scalars().all()
    if notes:
        raise HTTPException(status_code=409, detail=f"Supplier credit notes refer to this invoice: {', '.join(notes)}; cancel them first")
    was = invoice.status
    invoice.status = "CANCELLED"
    invoice.number_key = None
    invoice.cancelled_by, invoice.cancelled_at, invoice.cancel_reason = user.id, utcnow(), reason.strip()
    _audit(db, user, "supplier_invoice.cancel", invoice, {**_snapshot(invoice), "previous_status": was}, reason=reason)
    await db.flush()
    if was == "POSTED":
        await gl_posting.record(db, user.pharmacy_id, user.id, "SUPPLIER_BILL_CANCEL", invoice.id)
    return invoice


async def outstanding(db: AsyncSession, invoice: SupplierInvoice) -> dict:
    """Amount still owed on one invoice: total - active allocations - posted returns and credit notes against it."""
    paid = money((await db.execute(select(func.coalesce(func.sum(SupplierPaymentAllocation.amount), 0)).where(
        SupplierPaymentAllocation.invoice_id == invoice.id, SupplierPaymentAllocation.status == "ACTIVE"))).scalar_one())
    returned = money((await db.execute(select(func.coalesce(func.sum(SupplierReturn.total_return_amount), 0)).where(
        SupplierReturn.invoice_id == invoice.id, SupplierReturn.status == "POSTED"))).scalar_one())
    from app.models.payables import SupplierCreditNote
    credited = money((await db.execute(select(func.coalesce(func.sum(SupplierCreditNote.total_amount), 0)).where(
        SupplierCreditNote.invoice_id == invoice.id, SupplierCreditNote.status == "POSTED"))).scalar_one())
    total = money(invoice.calculated_grand_total) if invoice.status != "CANCELLED" else ZERO
    return {"paid": paid, "returned": money(returned + credited), "credit_notes": credited,
            "outstanding": money(total - paid - returned - credited)}


async def refresh_payment_cache(db: AsyncSession, invoice: SupplierInvoice) -> None:
    """paid_amount / payment_status are derived from active allocations; recompute after any change."""
    figures = await outstanding(db, invoice)
    invoice.paid_amount = figures["paid"]
    if figures["outstanding"] <= ZERO and invoice.status == "POSTED":
        invoice.payment_status = "PAID"
    elif figures["paid"] > ZERO:
        invoice.payment_status = "PARTIALLY_PAID"
    else:
        invoice.payment_status = "UNPAID"
