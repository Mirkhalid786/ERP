"""
Payment documents: customer payment receipts and supplier payment vouchers.

These builders only *read* committed financial records — a document can never
be produced for a payment that failed to commit, and reprints show the same
stored facts (document number, method, allocations). Balance figures are
derived deterministically from the same records the ledgers use:

* Customer receipt — "balance after this payment" is the customer-ledger
  running balance at the payment's own ledger entry (the ledger is
  append-only, so later payments/reversals do not change it).
* Supplier voucher — "payable after this payment" is rebuilt as of the
  payment's creation time from posted bills, payment allocations and credit
  notes created up to that moment (later reversals/cancellations excluded).

Nothing here posts, allocates or mutates money.
"""
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth import Pharmacy, User
from app.models.payables import SupplierCreditNote, SupplierPayment, SupplierPaymentAllocation
from app.models.purchasing import Supplier, SupplierInvoice
from app.models.sales import Customer, CustomerLedgerEntry, CustomerPayment, PaymentAllocation, SalesInvoice
from app.services import receivables
from app.services.sales_common import ZERO, money
from app.services.supplier_payments import get_payment as get_supplier_payment


async def pharmacy_header(db: AsyncSession, pharmacy_id: int) -> dict:
    ph = (await db.execute(select(Pharmacy).where(Pharmacy.id == pharmacy_id))).scalar_one()
    return {
        "name": ph.name, "legal_name": ph.legal_name, "address_line1": ph.address_line1,
        "city": ph.city, "pincode": ph.pincode, "phone": ph.phone, "email": ph.email,
        "gstin": ph.gstin, "drug_license_number": ph.drug_license_number,
    }


async def _user_name(db: AsyncSession, user_id: Optional[int]) -> Optional[str]:
    if not user_id:
        return None
    return (await db.execute(select(User.name).where(User.id == user_id))).scalar_one_or_none()


# ── Customer payment receipt ──────────────────────────────────────────────────

async def customer_receipt_document(db: AsyncSession, pharmacy_id: int, payment_id: int) -> dict:
    payment = await receivables.get_payment(db, pharmacy_id, payment_id)

    customer = None
    if payment.customer_id:
        c = (await db.execute(select(Customer).where(
            Customer.id == payment.customer_id, Customer.pharmacy_id == pharmacy_id))).scalar_one_or_none()
        if c:
            customer = {"id": c.id, "name": c.name, "mobile": c.mobile, "gstin": c.gstin,
                        "address": c.address}

    alloc_rows = (await db.execute(
        select(PaymentAllocation, SalesInvoice.invoice_number, SalesInvoice.invoice_date)
        .join(SalesInvoice, SalesInvoice.id == PaymentAllocation.invoice_id)
        .where(PaymentAllocation.payment_id == payment.id, PaymentAllocation.is_reversed.is_(False))
        .order_by(PaymentAllocation.id)
    )).all()
    allocations = [{"invoice_id": a.invoice_id, "invoice_number": number, "invoice_date": inv_date,
                    "amount": a.amount} for a, number, inv_date in alloc_rows]

    # Balance after this payment: running customer-ledger balance at the
    # payment's original ledger entry (earliest entry carrying this payment).
    balance_after = None
    advance_after = None
    if payment.customer_id:
        entry = (await db.execute(
            select(CustomerLedgerEntry).where(
                CustomerLedgerEntry.pharmacy_id == pharmacy_id,
                CustomerLedgerEntry.payment_id == payment.id,
                CustomerLedgerEntry.entry_type == "PAYMENT",
            ).order_by(CustomerLedgerEntry.id).limit(1)
        )).scalar_one_or_none()
        if entry is not None:
            balance_after = money((await db.execute(
                select(func.coalesce(func.sum(CustomerLedgerEntry.debit - CustomerLedgerEntry.credit), 0)).where(
                    CustomerLedgerEntry.pharmacy_id == pharmacy_id,
                    CustomerLedgerEntry.customer_id == payment.customer_id,
                    (CustomerLedgerEntry.created_at < entry.created_at) |
                    ((CustomerLedgerEntry.created_at == entry.created_at) & (CustomerLedgerEntry.id <= entry.id)),
                )
            )).scalar_one())
            advance_after = money(-balance_after) if balance_after < 0 else ZERO

    return {
        "kind": "CUSTOMER_PAYMENT_RECEIPT",
        "title": "Payment Receipt",
        "pharmacy": await pharmacy_header(db, pharmacy_id),
        "id": payment.id,
        "receipt_number": payment.receipt_number,
        "status": payment.status,
        "is_reversed": payment.status != "POSTED",
        "source": payment.source,
        "received_at": payment.received_at,
        "method": payment.method,
        "reference": payment.reference,
        "amount": payment.amount,
        "amount_tendered": payment.amount_tendered,
        "change_returned": payment.change_returned,
        "refunded_amount": payment.refunded_amount,
        "customer": customer,
        "allocations": allocations,
        "allocated_total": money(sum((a["amount"] for a in allocations), ZERO)),
        "advance_unapplied": await receivables.payment_unapplied(db, payment),
        "balance_after": balance_after,
        "advance_after": advance_after,
        "received_by": await _user_name(db, payment.received_by),
        "notes": payment.notes,
    }


# ── Supplier payment voucher ──────────────────────────────────────────────────

async def supplier_voucher_document(db: AsyncSession, pharmacy_id: int, payment_id: int) -> dict:
    payment = await get_supplier_payment(db, pharmacy_id, payment_id)
    supplier = (await db.execute(select(Supplier).where(
        Supplier.id == payment.supplier_id, Supplier.pharmacy_id == pharmacy_id))).scalar_one_or_none()

    numbers = {}
    if payment.allocations:
        numbers = {inv.id: inv.invoice_number for inv in (await db.execute(select(SupplierInvoice).where(
            SupplierInvoice.id.in_([a.invoice_id for a in payment.allocations])))).scalars().all()}
    allocations = [{"invoice_id": a.invoice_id, "invoice_number": numbers.get(a.invoice_id),
                    "amount": a.amount, "status": a.status}
                   for a in payment.allocations if a.status == "ACTIVE"]
    allocated = money(sum((a["amount"] for a in allocations), ZERO))
    advance = money(payment.amount - allocated) if payment.status == "POSTED" else ZERO

    # Payable after this payment, rebuilt as of the payment's creation:
    # bills posted by then − allocations made by then − credit notes by then.
    bills = (await db.execute(
        select(func.coalesce(func.sum(SupplierInvoice.calculated_grand_total), 0)).where(
            SupplierInvoice.pharmacy_id == pharmacy_id, SupplierInvoice.supplier_id == payment.supplier_id,
            SupplierInvoice.created_at <= payment.created_at,
            (SupplierInvoice.status == "POSTED") |
            ((SupplierInvoice.status == "CANCELLED") & (SupplierInvoice.cancelled_at > payment.created_at)),
        )
    )).scalar_one()
    # Allocations count from their *payment's* position in time (allocation
    # rows are written a moment after their payment row in the same commit).
    allocs = (await db.execute(
        select(func.coalesce(func.sum(SupplierPaymentAllocation.amount), 0))
        .join(SupplierPayment, SupplierPayment.id == SupplierPaymentAllocation.payment_id)
        .where(
            SupplierPayment.pharmacy_id == pharmacy_id, SupplierPayment.supplier_id == payment.supplier_id,
            (SupplierPayment.created_at < payment.created_at) |
            ((SupplierPayment.created_at == payment.created_at) & (SupplierPayment.id <= payment.id)),
            (SupplierPayment.reversed_at.is_(None)) | (SupplierPayment.reversed_at > payment.created_at),
        )
    )).scalar_one()
    notes = (await db.execute(
        select(func.coalesce(func.sum(SupplierCreditNote.total_amount), 0)).where(
            SupplierCreditNote.pharmacy_id == pharmacy_id, SupplierCreditNote.supplier_id == payment.supplier_id,
            SupplierCreditNote.created_at <= payment.created_at,
            (SupplierCreditNote.status == "POSTED") |
            ((SupplierCreditNote.status == "CANCELLED") & (SupplierCreditNote.cancelled_at > payment.created_at)),
        )
    )).scalar_one()
    payable_after = money(Decimal(str(bills)) - Decimal(str(allocs)) - Decimal(str(notes)))

    return {
        "kind": "SUPPLIER_PAYMENT_VOUCHER",
        "title": "Payment Voucher",
        "pharmacy": await pharmacy_header(db, pharmacy_id),
        "id": payment.id,
        "payment_number": payment.payment_number,
        "status": payment.status,
        "is_reversed": payment.status != "POSTED",
        "payment_date": payment.payment_date,
        "created_at": payment.created_at,
        "method": payment.method,
        "reference": payment.reference,
        "bank_account": payment.bank_account,
        "amount": payment.amount,
        "supplier": ({"id": supplier.id, "name": supplier.name, "legal_name": supplier.legal_name,
                      "phone": getattr(supplier, "phone", None), "gstin": supplier.gstin,
                      "address": getattr(supplier, "address", None)} if supplier else None),
        "allocations": allocations,
        "allocated_total": allocated,
        "advance_unallocated": advance,
        "payable_after": payable_after,
        "recorded_by": await _user_name(db, payment.created_by),
        "reversed_by": await _user_name(db, payment.reversed_by) if payment.reversed_by else None,
        "reversal_reason": payment.reversal_reason,
        "notes": payment.notes,
    }
