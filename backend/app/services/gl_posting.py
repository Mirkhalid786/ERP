"""
Stage 5B: automatic general-ledger postings for finalised business documents.

Every eligible event is posted once through `ledger.post_entry` with a source key such
as "SALE:12", inside the same database transaction as the business change. A posting
problem (closed period, missing account, inconsistent document) never blocks the sale or
payment: the journal part is rolled back to a savepoint and a `gl_posting_issues` row is
written instead, to be fixed and retried. Nothing is posted while the books are not set
up, or for events dated before the books start date (those belong in opening balances).

Account model
* Customers: everything goes through Trade Receivables per customer, exactly like the
  khata (customer ledger), so AR per customer = khata balance. Unapplied advances and
  credit notes are credit balances on that account; applying them moves nothing.
* Walk-in sales post their payments straight to the money accounts; walk-in voids and
  refunds clear through Refunds Payable.
* Suppliers: everything goes through Trade Payables per supplier, matching the supplier
  balance (bills - returns - payments; a debit balance is an advance).
* Stock: inventory moves at the stock-movement value (quantity x unit cost, the batch
  weighted-average cost), so GL inventory follows the ERP stock value. A goods receipt
  credits GRNI; the supplier bill clears GRNI, and any bill/receipt cost difference,
  freight and other charges go to Purchase Price Variance.
* Input GST on supplier bills and expenses waits in "Input GST - Pending Eligibility
  Review" until the CA confirms the credit.
"""
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_date
from app.models.accounting import GlPostingIssue, JournalEntry, LedgerAccount
from app.models.auth import AuditLog, User
from app.models.inventory import StockAdjustment, StockMovement
from app.models.payables import Expense, ExpenseCategory, SupplierPayment
from app.models.purchasing import GoodsReceipt, GoodsReceiptItem, SupplierInvoice, SupplierInvoiceItem, SupplierReturn
from app.models.sales import (
    CashMovement, CashShift, CustomerLedgerEntry, CustomerPayment, CustomerRefund, SalesInvoice, SalesReturn,
    SalesReturnItem,
)
from app.services import ledger
from app.services.ledger import LineIn, ZERO, money, utcnow

CUSTOMER_METHOD_ACCOUNT = {"CASH": "CASH", "UPI": "UPI_CLEARING", "CARD": "CARD_CLEARING",
                           "BANK_TRANSFER": "BANK", "OTHER": "OTHER_CLEARING"}
# Paying out: cash from the drawer or office cash is Cash in Hand; everything else leaves the bank
PAYOUT_METHOD_ACCOUNT = {"CASH": "CASH", "BANK_TRANSFER": "BANK", "UPI": "BANK", "CHEQUE": "BANK", "CARD": "BANK"}
WRITE_OFF_REASONS = {"DAMAGE", "BREAKAGE", "DAMAGE_BREAKAGE", "EXPIRY", "THEFT"}
# Cash-movement categories: (debit key, credit key); None = cash moves within Cash in Hand (no entry)
CASH_MOVEMENT_RULES = {
    "BANK_DEPOSIT": ("BANK", "CASH"), "BANK_WITHDRAWAL": ("CASH", "BANK"),
    "OWNER_WITHDRAWAL": ("DRAWINGS", "CASH"), "OWNER_INTRODUCED": ("CASH", "CAPITAL"),
    "CORRECTION_OUT": ("CASH_OVER_SHORT", "CASH"), "OTHER_OUT": ("CASH_OVER_SHORT", "CASH"),
    "CORRECTION_IN": ("CASH", "CASH_OVER_SHORT"), "OTHER_IN": ("CASH", "CASH_OVER_SHORT"),
    "PETTY_EXPENSE": ("MISC_EXPENSE", "CASH"),
    "TRANSFER_TO_SAFE": None, "TRANSFER_FROM_SAFE": None, "FLOAT_TRANSFER_OUT": None, "FLOAT_IN": None,
}
MISC_EXPENSE_CODE = "5290"


class PostingError(Exception):
    """The document cannot be posted as it stands; recorded as a posting issue."""


@dataclass
class Spec:
    entry_date: date
    narration: str
    reference: Optional[str]
    amounts: Dict[Tuple[str, Optional[str], Optional[int]], Decimal] = field(default_factory=dict)
    reversal_of_key: Optional[str] = None     # Mirror of an earlier posting (marks it REVERSED)

    def add(self, key: str, amount, party: Optional[Tuple[str, int]] = None):
        """Positive = debit, negative = credit. Lines on the same account / party are netted."""
        amount = money(amount)
        if amount == ZERO:
            return
        k = (key, party[0] if party else None, party[1] if party else None)
        self.amounts[k] = self.amounts.get(k, ZERO) + amount

    def dr(self, key, amount, party=None):
        self.add(key, amount, party)

    def cr(self, key, amount, party=None):
        self.add(key, -money(amount), party)


class Context:
    def __init__(self, db: AsyncSession, pharmacy_id: int, books_start: date):
        self.db, self.pharmacy_id, self.books_start = db, pharmacy_id, books_start
        self._accounts: Optional[dict] = None

    async def account(self, key: str) -> LedgerAccount:
        if self._accounts is None:
            self._accounts = await ledger.accounts_by_key(self.db, self.pharmacy_id)
        if key.startswith("CODE:"):
            code = key[5:]
            acc = (await self.db.execute(select(LedgerAccount).where(
                LedgerAccount.pharmacy_id == self.pharmacy_id, LedgerAccount.code == code))).scalar_one_or_none()
            if acc is None or acc.is_group or not acc.is_active or acc.account_type != "EXPENSE":
                raise PostingError(f"Account code {code} is not an active expense posting account")
            return acc
        if key.startswith("ID:"):
            acc = (await self.db.execute(select(LedgerAccount).where(
                LedgerAccount.pharmacy_id == self.pharmacy_id, LedgerAccount.id == int(key[3:])))).scalar_one_or_none()
            if acc is None:
                raise PostingError(f"Account {key[3:]} not found")
            return acc
        if key == "MISC_EXPENSE":
            return await self.account(f"CODE:{MISC_EXPENSE_CODE}")
        acc = self._accounts.get(key)
        if acc is None:
            raise PostingError(f"System account {key} is missing from the chart of accounts")
        return acc


async def _movement_value(db: AsyncSession, pharmacy_id: int, reference_type: str, reference_id: str,
                          movement_type: Optional[str] = None) -> Decimal:
    """Signed stock value of a document's movements, each rounded to paise (as reconciled)."""
    stmt = select(StockMovement.quantity_base, StockMovement.unit_cost).where(
        StockMovement.pharmacy_id == pharmacy_id, StockMovement.reference_type == reference_type,
        StockMovement.reference_id == reference_id)
    if movement_type:
        stmt = stmt.where(StockMovement.movement_type == movement_type)
    return money(sum((money(q * c) for q, c in (await db.execute(stmt)).all()), ZERO))


def _customer_party(customer_id: Optional[int]):
    return ("AR", ("CUSTOMER", customer_id)) if customer_id else ("REFUNDS_PAYABLE", None)


async def _get(db: AsyncSession, model, pharmacy_id: int, source_id: int):
    row = (await db.execute(select(model).where(model.id == source_id, model.pharmacy_id == pharmacy_id))).scalar_one_or_none()
    if row is None:
        raise PostingError(f"{model.__tablename__} {source_id} not found")
    return row


# ── Sales side ─────────────────────────────────────────────────────────────────

async def _sale(ctx: Context, sid: int) -> Optional[Spec]:
    db = ctx.db
    inv = await _get(db, SalesInvoice, ctx.pharmacy_id, sid)
    if inv.status not in ("FINALIZED", "VOIDED"):
        return None
    spec = Spec(local_date(inv.invoice_date), f"Sale {inv.invoice_number} to {inv.customer_name}", inv.invoice_number)
    _sales_lines(spec, inv, sign=-1)
    spec.cr("ROUND_OFF", inv.round_off)
    # Money received at the sale (amounts are immutable; later reversals are their own events)
    from app.models.sales import PaymentAllocation
    paid = ZERO
    for method, amount in (await db.execute(
            select(CustomerPayment.method, CustomerPayment.amount)
            .where(CustomerPayment.source == "SALE", CustomerPayment.id.in_(
                select(PaymentAllocation.payment_id).where(PaymentAllocation.invoice_id == inv.id))))).all():
        if method not in CUSTOMER_METHOD_ACCOUNT:
            raise PostingError(f"Sale {inv.invoice_number} has a payment with unsupported method {method}")
        spec.dr(CUSTOMER_METHOD_ACCOUNT[method], amount)
        paid += money(amount)
    unpaid = money(inv.grand_total - paid)
    if unpaid < ZERO:
        raise PostingError(f"Payments at sale exceed the bill total on {inv.invoice_number}")
    if unpaid > ZERO:
        if not inv.customer_id:
            raise PostingError(f"{inv.invoice_number} has ₹{unpaid} unpaid but no customer")
        spec.dr("AR", unpaid, ("CUSTOMER", inv.customer_id))
    cost = await _movement_value(db, ctx.pharmacy_id, "sales_invoice", inv.invoice_number, "SALE")   # negative
    spec.dr("COGS", -cost)
    spec.cr("INVENTORY", -cost)
    return spec


def _sales_lines(spec: Spec, doc, sign: int, revenue_key: str = "SALES"):
    """Revenue, output GST and round-off of an invoice (sign -1 = credit as on a sale, +1 = debit)."""
    spec.add(revenue_key, sign * money(doc.taxable_total))
    spec.add("OUTPUT_CGST", sign * money(doc.cgst_total))
    spec.add("OUTPUT_SGST", sign * money(doc.sgst_total))
    spec.add("OUTPUT_IGST", sign * money(doc.igst_total))


async def _sale_void(ctx: Context, sid: int) -> Optional[Spec]:
    db = ctx.db
    inv = await _get(db, SalesInvoice, ctx.pharmacy_id, sid)
    if inv.status != "VOIDED" or inv.voided_at is None:
        return None
    spec = Spec(local_date(inv.voided_at), f"Void of sale {inv.invoice_number}: {inv.void_reason or ''}".strip(),
                inv.invoice_number)
    _sales_lines(spec, inv, sign=+1)
    spec.dr("ROUND_OFF", inv.round_off)
    account, party = _customer_party(inv.customer_id)
    spec.cr(account, inv.grand_total, party)
    back = await _movement_value(db, ctx.pharmacy_id, "sales_invoice", inv.invoice_number, "SALE_VOID")
    spec.dr("INVENTORY", back)
    spec.cr("COGS", back)
    return spec


async def _customer_receipt(ctx: Context, pid: int) -> Optional[Spec]:
    pay = await _get(ctx.db, CustomerPayment, ctx.pharmacy_id, pid)
    if pay.source != "COLLECTION" or pay.method not in CUSTOMER_METHOD_ACCOUNT:
        return None   # Sale receipts are part of the sale entry; credit notes move nothing
    spec = Spec(local_date(pay.received_at), f"{pay.method} received {pay.receipt_number}", pay.receipt_number)
    spec.dr(CUSTOMER_METHOD_ACCOUNT[pay.method], pay.amount)
    spec.cr("AR", pay.amount, ("CUSTOMER", pay.customer_id))
    return spec


async def _customer_receipt_reversal(ctx: Context, pid: int) -> Optional[Spec]:
    pay = await _get(ctx.db, CustomerPayment, ctx.pharmacy_id, pid)
    if pay.status != "REVERSED" or not pay.customer_id or pay.method not in CUSTOMER_METHOD_ACCOUNT:
        return None
    when = (await ctx.db.execute(select(func.max(CustomerLedgerEntry.created_at)).where(
        CustomerLedgerEntry.payment_id == pay.id, CustomerLedgerEntry.entry_type == "PAYMENT_REVERSAL"))).scalar_one()
    if when is None:
        raise PostingError(f"Reversal record for {pay.receipt_number} not found")
    original_date = local_date(pay.received_at)
    if original_date < ctx.books_start:
        return None       # The receipt itself is in the opening balances, not in the GL
    spec = Spec(local_date(when), f"Reversal of {pay.method} receipt {pay.receipt_number}", pay.receipt_number)
    spec.dr("AR", pay.amount, ("CUSTOMER", pay.customer_id))
    spec.cr(CUSTOMER_METHOD_ACCOUNT[pay.method], pay.amount)
    return spec


async def _customer_refund(ctx: Context, rid: int) -> Optional[Spec]:
    ref = await _get(ctx.db, CustomerRefund, ctx.pharmacy_id, rid)
    if ref.method not in CUSTOMER_METHOD_ACCOUNT:
        raise PostingError(f"Refund {ref.id} has unsupported method {ref.method}")
    spec = Spec(local_date(ref.refunded_at), f"{ref.method} refund: {ref.reason}"[:900], None)
    account, party = _customer_party(ref.customer_id)
    spec.dr(account, ref.amount, party)
    spec.cr(CUSTOMER_METHOD_ACCOUNT[ref.method], ref.amount)
    return spec


async def _customer_adjustment(ctx: Context, eid: int) -> Optional[Spec]:
    entry = await _get(ctx.db, CustomerLedgerEntry, ctx.pharmacy_id, eid)
    target = {"WRITE_OFF": "BAD_DEBTS", "ADJUSTMENT_CREDIT": "DISCOUNT_ALLOWED"}.get(entry.entry_type)
    if target is None:
        return None
    spec = Spec(local_date(entry.created_at), f"{entry.description}: {entry.reason or ''}".strip(), entry.reference)
    spec.dr(target, entry.credit)
    spec.cr("AR", entry.credit, ("CUSTOMER", entry.customer_id))
    return spec


async def _sales_return(ctx: Context, rid: int) -> Optional[Spec]:
    db = ctx.db
    sret = await _get(db, SalesReturn, ctx.pharmacy_id, rid)
    if sret.status != "POSTED":
        return None
    spec = Spec(local_date(sret.created_at), f"Sales return {sret.return_number}: {sret.reason}"[:900], sret.return_number)
    _sales_lines(spec, sret, sign=+1, revenue_key="SALES_RETURNS")
    spec.dr("ROUND_OFF", money(sret.total_amount - sret.taxable_total - sret.tax_total))
    account, party = _customer_party(sret.customer_id)
    spec.cr(account, sret.total_amount, party)
    restocked = await _movement_value(db, ctx.pharmacy_id, "sales_return", sret.return_number, "SALES_RETURN")
    written_off = money((await db.execute(select(func.coalesce(func.sum(SalesReturnItem.cost_total), 0)).where(
        SalesReturnItem.return_id == sret.id, SalesReturnItem.disposition == "WRITTEN_OFF"))).scalar_one())
    spec.dr("INVENTORY", restocked)
    spec.dr("INVENTORY_WRITE_OFF", written_off)
    spec.cr("COGS", restocked + written_off)
    return spec


# ── Purchase side ──────────────────────────────────────────────────────────────

async def _goods_receipt(ctx: Context, gid: int) -> Optional[Spec]:
    grn = await _get(ctx.db, GoodsReceipt, ctx.pharmacy_id, gid)
    if grn.status != "POSTED":
        return None
    value = await _movement_value(ctx.db, ctx.pharmacy_id, "goods_receipt", grn.grn_number, "GOODS_RECEIPT")
    spec = Spec(grn.receipt_date, f"Goods receipt {grn.grn_number}", grn.grn_number)
    spec.dr("INVENTORY", value)
    spec.cr("GRNI", value)
    return spec


async def _goods_receipt_cancel(ctx: Context, gid: int) -> Optional[Spec]:
    grn = await _get(ctx.db, GoodsReceipt, ctx.pharmacy_id, gid)
    if grn.status != "CANCELLED":
        return None
    if grn.receipt_date < ctx.books_start:
        return None
    return Spec(local_date(grn.updated_at), f"Cancellation of goods receipt {grn.grn_number}",
                grn.grn_number, reversal_of_key=f"GOODS_RECEIPT:{grn.id}")


async def _supplier_bill(ctx: Context, iid: int) -> Optional[Spec]:
    db = ctx.db
    bill = await _get(db, SupplierInvoice, ctx.pharmacy_id, iid)
    if bill.status not in ("POSTED", "CANCELLED") or bill.finalized_at is None:
        return None
    spec = Spec(bill.invoice_date, f"Supplier bill {bill.invoice_number}", bill.invoice_number)
    items = (await db.execute(select(SupplierInvoiceItem).where(SupplierInvoiceItem.invoice_id == bill.id))).scalars().all()
    grn_items = {gi.id: gi for gi in (await db.execute(select(GoodsReceiptItem).where(
        GoodsReceiptItem.id.in_([i.grn_item_id for i in items if i.grn_item_id])))).scalars().all()} if items else {}
    for item in items:
        taxable = money(item.taxable_amount)
        gi = grn_items.get(item.grn_item_id) if item.grn_item_id else None
        if gi is None:
            spec.dr("GRNI", taxable)        # Invoiced before / without a receipt: GRNI goes debit until received
            continue
        received_value = money(gi.accepted_qty_base * gi.cost_per_base_unit)
        paid_base = Decimal(str(gi.received_qty_pack)) * Decimal(str(gi.conversion_factor))
        share = money(received_value * Decimal(str(item.billed_qty_base)) / paid_base) if paid_base else ZERO
        share = min(share, received_value)
        spec.dr("GRNI", share)
        spec.dr("PURCHASE_VARIANCE", taxable - share)
    spec.dr("INPUT_GST_PENDING", bill.tax_total)
    spec.dr("PURCHASE_VARIANCE", bill.other_charges)
    spec.dr("ROUND_OFF", bill.round_off)
    spec.cr("AP", bill.calculated_grand_total, ("SUPPLIER", bill.supplier_id))
    return spec


async def _supplier_bill_cancel(ctx: Context, iid: int) -> Optional[Spec]:
    bill = await _get(ctx.db, SupplierInvoice, ctx.pharmacy_id, iid)
    if bill.status != "CANCELLED" or bill.finalized_at is None or bill.cancelled_at is None:
        return None
    if bill.invoice_date < ctx.books_start:
        return None
    return Spec(local_date(bill.cancelled_at), f"Cancellation of supplier bill {bill.invoice_number}: {bill.cancel_reason}"[:900],
                bill.invoice_number, reversal_of_key=f"SUPPLIER_BILL:{bill.id}")


async def _supplier_return(ctx: Context, rid: int) -> Optional[Spec]:
    ret = await _get(ctx.db, SupplierReturn, ctx.pharmacy_id, rid)
    if ret.status != "POSTED":
        return None
    out = await _movement_value(ctx.db, ctx.pharmacy_id, "purchase_return", ret.return_number, "RETURN_OUTWARD")  # negative
    spec = Spec(ret.return_date, f"Purchase return / debit note {ret.return_number}", ret.debit_note_reference or ret.return_number)
    spec.dr("AP", ret.total_return_amount, ("SUPPLIER", ret.supplier_id))
    spec.cr("INVENTORY", -out)
    spec.cr("INPUT_GST_PENDING", ret.tax_total)
    spec.cr("PURCHASE_VARIANCE", money(ret.total_return_amount) - money(-out) - money(ret.tax_total))
    return spec


async def _supplier_payment(ctx: Context, pid: int) -> Optional[Spec]:
    pay = await _get(ctx.db, SupplierPayment, ctx.pharmacy_id, pid)
    if pay.is_legacy or pay.method not in PAYOUT_METHOD_ACCOUNT:
        return None     # Migrated paid amounts belong in the opening balances
    spec = Spec(pay.payment_date, f"{pay.method.replace('_', ' ').title()} payment {pay.payment_number} to supplier",
                pay.reference or pay.payment_number)
    spec.dr("AP", pay.amount, ("SUPPLIER", pay.supplier_id))
    spec.cr(PAYOUT_METHOD_ACCOUNT[pay.method], pay.amount)
    return spec


async def _supplier_payment_reversal(ctx: Context, pid: int) -> Optional[Spec]:
    pay = await _get(ctx.db, SupplierPayment, ctx.pharmacy_id, pid)
    if pay.status != "REVERSED" or pay.is_legacy or pay.reversed_at is None or pay.payment_date < ctx.books_start:
        return None
    return Spec(local_date(pay.reversed_at), f"Reversal of supplier payment {pay.payment_number}: {pay.reversal_reason}"[:900],
                pay.payment_number, reversal_of_key=f"SUPPLIER_PAYMENT:{pay.id}")


async def _expense(ctx: Context, eid: int) -> Optional[Spec]:
    exp = await _get(ctx.db, Expense, ctx.pharmacy_id, eid)
    if exp.status not in ("APPROVED", "REVERSED") or exp.approved_at is None:
        return None
    category = (await ctx.db.execute(select(ExpenseCategory).where(ExpenseCategory.id == exp.category_id))).scalar_one()
    target = f"CODE:{category.account_code_hint}" if category.account_code_hint else "MISC_EXPENSE"
    if exp.payment_method not in PAYOUT_METHOD_ACCOUNT:
        raise PostingError(f"Expense {exp.expense_number} has unsupported method {exp.payment_method}")
    spec = Spec(exp.expense_date, f"{category.name}: {exp.payee} ({exp.expense_number})", exp.bill_number or exp.expense_number)
    spec.dr(target, money(exp.amount) - money(exp.gst_amount))
    spec.dr("INPUT_GST_PENDING", exp.gst_amount)
    spec.cr(PAYOUT_METHOD_ACCOUNT[exp.payment_method], exp.amount)
    return spec


async def _expense_reversal(ctx: Context, eid: int) -> Optional[Spec]:
    exp = await _get(ctx.db, Expense, ctx.pharmacy_id, eid)
    if exp.status != "REVERSED" or exp.reversed_at is None or exp.expense_date < ctx.books_start:
        return None
    return Spec(local_date(exp.reversed_at), f"Reversal of expense {exp.expense_number}: {exp.reversal_reason}"[:900],
                exp.expense_number, reversal_of_key=f"EXPENSE:{exp.id}")


# ── Cash and stock ─────────────────────────────────────────────────────────────

async def _cash_movement(ctx: Context, mid: int) -> Optional[Spec]:
    move = await _get(ctx.db, CashMovement, ctx.pharmacy_id, mid)
    if move.category == "UNCATEGORISED":
        if local_date(move.created_at) < ctx.books_start:
            return None
        raise PostingError("Classify this cash movement before it can be posted")
    if move.category not in CASH_MOVEMENT_RULES:
        raise PostingError(f"No posting rule for cash movement category {move.category}")
    rule = CASH_MOVEMENT_RULES[move.category]
    if rule is None:
        return None          # Cash moved between the drawer and the safe stays in Cash in Hand
    spec = Spec(local_date(move.created_at), f"Cash {move.movement_type.replace('_', ' ').lower()}: {move.reason}"[:900],
                move.reference)
    spec.dr(rule[0], move.amount)
    spec.cr(rule[1], move.amount)
    return spec


async def _shift_variance(ctx: Context, sid: int) -> Optional[Spec]:
    shift = await _get(ctx.db, CashShift, ctx.pharmacy_id, sid)
    if shift.status == "OPEN" or shift.closed_at is None or not shift.variance:
        return None
    spec = Spec(local_date(shift.closed_at), f"Cash shift {shift.id} count difference: {shift.variance_note or ''}".strip(), None)
    spec.dr("CASH", shift.variance)              # Excess (+) debits cash; shortage (-) credits it
    spec.cr("CASH_OVER_SHORT", shift.variance)
    return spec


async def _stock_adjustment(ctx: Context, aid: int) -> Optional[Spec]:
    adj = await _get(ctx.db, StockAdjustment, ctx.pharmacy_id, aid)
    if adj.status != "POSTED":
        return None
    value = await _movement_value(ctx.db, ctx.pharmacy_id, "stock_adjustment", adj.adjustment_number)
    target = "INVENTORY_WRITE_OFF" if adj.reason_code in WRITE_OFF_REASONS else "STOCK_ADJUSTMENT"
    spec = Spec(local_date(adj.created_at), f"Stock adjustment {adj.adjustment_number} ({adj.reason_code})", adj.adjustment_number)
    spec.dr("INVENTORY", value)
    spec.cr(target, value)
    return spec


async def _supplier_credit_note(ctx: Context, nid: int) -> Optional[Spec]:
    from app.models.payables import SupplierCreditNote
    note = await _get(ctx.db, SupplierCreditNote, ctx.pharmacy_id, nid)
    spec = Spec(note.note_date, f"Supplier credit note {note.credit_note_number} ({note.reason_code.replace('_', ' ').lower()})",
                note.credit_note_number)
    spec.dr("AP", note.total_amount, ("SUPPLIER", note.supplier_id))
    spec.cr("PURCHASE_VARIANCE", note.taxable_amount)
    spec.cr("INPUT_GST_PENDING", money(note.cgst_amount) + money(note.sgst_amount) + money(note.igst_amount))
    return spec


async def _supplier_credit_note_cancel(ctx: Context, nid: int) -> Optional[Spec]:
    from app.models.payables import SupplierCreditNote
    note = await _get(ctx.db, SupplierCreditNote, ctx.pharmacy_id, nid)
    if note.status != "CANCELLED" or note.cancelled_at is None or note.note_date < ctx.books_start:
        return None
    return Spec(local_date(note.cancelled_at), f"Cancellation of supplier credit note {note.credit_note_number}: {note.cancel_reason}"[:900],
                note.credit_note_number, reversal_of_key=f"SUPPLIER_CREDIT_NOTE:{note.id}")


async def _settlement(ctx: Context, sid: int) -> Optional[Spec]:
    from app.models.accounting import PaymentSettlement
    st = await _get(ctx.db, PaymentSettlement, ctx.pharmacy_id, sid)
    spec = Spec(st.settlement_date, f"Settlement {st.settlement_number} from {st.clearing_key.replace('_', ' ').lower()}",
                st.reference or st.settlement_number)
    spec.dr(f"ID:{st.bank_account_id}", st.net_amount)
    spec.dr("BANK_CHARGES", st.fee_amount)
    spec.dr("INPUT_GST_PENDING", st.fee_gst_amount)
    spec.cr(st.clearing_key, st.gross_amount)
    return spec


async def _settlement_reversal(ctx: Context, sid: int) -> Optional[Spec]:
    from app.models.accounting import PaymentSettlement
    st = await _get(ctx.db, PaymentSettlement, ctx.pharmacy_id, sid)
    if st.status != "REVERSED" or st.reversed_at is None or st.settlement_date < ctx.books_start:
        return None
    return Spec(local_date(st.reversed_at), f"Reversal of settlement {st.settlement_number}: {st.reversal_reason}"[:900],
                st.settlement_number, reversal_of_key=f"SETTLEMENT:{st.id}")


BUILDERS: Dict[str, Callable[[Context, int], Awaitable[Optional[Spec]]]] = {
    "SALE": _sale, "SALE_VOID": _sale_void, "SALES_RETURN": _sales_return,
    "CUSTOMER_RECEIPT": _customer_receipt, "CUSTOMER_RECEIPT_REVERSAL": _customer_receipt_reversal,
    "CUSTOMER_REFUND": _customer_refund, "CUSTOMER_ADJUSTMENT": _customer_adjustment,
    "GOODS_RECEIPT": _goods_receipt, "GOODS_RECEIPT_CANCEL": _goods_receipt_cancel,
    "SUPPLIER_BILL": _supplier_bill, "SUPPLIER_BILL_CANCEL": _supplier_bill_cancel,
    "SUPPLIER_RETURN": _supplier_return, "SUPPLIER_PAYMENT": _supplier_payment,
    "SUPPLIER_PAYMENT_REVERSAL": _supplier_payment_reversal, "EXPENSE": _expense, "EXPENSE_REVERSAL": _expense_reversal,
    "CASH_MOVEMENT": _cash_movement, "SHIFT_VARIANCE": _shift_variance, "STOCK_ADJUSTMENT": _stock_adjustment,
    "SETTLEMENT": _settlement, "SETTLEMENT_REVERSAL": _settlement_reversal,
    "SUPPLIER_CREDIT_NOTE": _supplier_credit_note, "SUPPLIER_CREDIT_NOTE_CANCEL": _supplier_credit_note_cancel,
}


# ── Posting ────────────────────────────────────────────────────────────────────

async def _lines(ctx: Context, spec: Spec) -> List[LineIn]:
    lines = []
    for (key, party_type, party_id), amount in spec.amounts.items():
        amount = money(amount)
        if amount == ZERO:
            continue
        acc = await ctx.account(key)
        lines.append(LineIn(acc.id, debit=amount if amount > 0 else ZERO, credit=-amount if amount < 0 else ZERO,
                            party_type=party_type, party_id=party_id))
    debit = sum((l.debit for l in lines), ZERO)
    credit = sum((l.credit for l in lines), ZERO)
    if debit != credit:
        raise PostingError(f"Posting rule produced debits ₹{debit} and credits ₹{credit}; the document totals are inconsistent")
    return lines


async def build(db: AsyncSession, pharmacy_id: int, books_start: date, source_type: str, source_id: int):
    """(spec, lines) for a source event, or (None, None) when nothing is to be posted."""
    ctx = Context(db, pharmacy_id, books_start)
    spec = await BUILDERS[source_type](ctx, source_id)
    if spec is None or spec.entry_date < books_start:
        return None, None
    if spec.reversal_of_key:
        original = await ledger.entry_by_source(db, pharmacy_id, spec.reversal_of_key)
        if original is None:
            raise PostingError(f"The original posting {spec.reversal_of_key} is missing; retry it first")
        await db.refresh(original, attribute_names=["lines"])
        return spec, [LineIn(l.account_id, l.credit, l.debit, l.party_type, l.party_id, "Reversal") for l in original.lines]
    lines = await _lines(ctx, spec)
    return (spec, lines) if lines else (None, None)


async def record(db: AsyncSession, pharmacy_id: int, user_id: int, source_type: str, source_id: int) -> str:
    """
    Post one business event. Returns "posted", "already_posted", "skipped" (books not set up,
    before the books start, or nothing to post) or "failed" (an issue row was written).
    """
    books_start = await ledger.books_start_date(db, pharmacy_id)
    if books_start is None:
        return "skipped"
    key = f"{source_type}:{source_id}"
    await db.flush()     # Business changes are written before the journal's savepoint
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
    event_date = None
    try:
        async with db.begin_nested():
            spec, lines = await build(db, pharmacy_id, books_start, source_type, source_id)
            if spec is None:
                await _resolve(db, pharmacy_id, key, None)
                return "skipped"
            event_date = spec.entry_date
            original = await ledger.entry_by_source(db, pharmacy_id, spec.reversal_of_key) if spec.reversal_of_key else None
            entry, replayed = await ledger.post_entry(
                db, user, entry_date=spec.entry_date, narration=spec.narration[:1000], lines=lines,
                source_type=source_type, source_id=str(source_id), source_key=key, reference=spec.reference,
                allow_control=True, reversal_of_id=original.id if original else None,
            )
            if original is not None and not replayed:
                original.status = "REVERSED"
                original.reversed_by_entry_id, original.reversed_at = entry.id, utcnow()
                original.reversal_reason = spec.narration[:1000]
    except ledger.DuplicateSource:
        return "already_posted"
    except (PostingError, HTTPException) as exc:
        message = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
        await _fail(db, pharmacy_id, user_id, source_type, source_id, key, event_date, message)
        return "failed"
    await _resolve(db, pharmacy_id, key, entry.id)
    return "already_posted" if replayed else "posted"


async def _fail(db, pharmacy_id, user_id, source_type, source_id, key, event_date, message):
    issue = (await db.execute(select(GlPostingIssue).where(
        GlPostingIssue.pharmacy_id == pharmacy_id, GlPostingIssue.source_key == key))).scalar_one_or_none()
    if issue is None:
        db.add(GlPostingIssue(pharmacy_id=pharmacy_id, source_type=source_type, source_id=str(source_id), source_key=key,
                              event_date=event_date, error=message[:2000]))
    else:
        issue.status, issue.error, issue.resolved_at = "OPEN", message[:2000], None
        issue.attempts += 1
        issue.last_attempt_at = utcnow()
        issue.event_date = event_date or issue.event_date
    db.add(AuditLog(pharmacy_id=pharmacy_id, user_id=user_id, action="gl.posting_failed", entity_type="gl_posting",
                    entity_id=key, metadata_json=json.dumps({"error": message[:500]})))
    await db.flush()


async def _resolve(db, pharmacy_id, key, entry_id):
    issue = (await db.execute(select(GlPostingIssue).where(
        GlPostingIssue.pharmacy_id == pharmacy_id, GlPostingIssue.source_key == key,
        GlPostingIssue.status == "OPEN"))).scalar_one_or_none()
    if issue is not None:
        issue.status, issue.resolved_at, issue.journal_entry_id = "RESOLVED", utcnow(), entry_id
        issue.last_attempt_at = utcnow()


# ── Source enumeration, reconciliation, retry ──────────────────────────────────

async def eligible_sources(db: AsyncSession, pharmacy_id: int, since: date) -> List[Tuple[str, int]]:
    """Every business event that may need a journal, from documents on or after `since` (approximate filter)."""
    from app.core.clock import day_bounds
    start, _ = day_bounds(since)
    q = []

    async def ids(stmt):
        return list((await db.execute(stmt)).scalars().all())

    pid = pharmacy_id
    q += [("SALE", i) for i in await ids(select(SalesInvoice.id).where(
        SalesInvoice.pharmacy_id == pid, SalesInvoice.invoice_date >= start, SalesInvoice.status.in_(("FINALIZED", "VOIDED"))))]
    q += [("SALE_VOID", i) for i in await ids(select(SalesInvoice.id).where(
        SalesInvoice.pharmacy_id == pid, SalesInvoice.status == "VOIDED", SalesInvoice.voided_at >= start))]
    q += [("SALES_RETURN", i) for i in await ids(select(SalesReturn.id).where(
        SalesReturn.pharmacy_id == pid, SalesReturn.created_at >= start))]
    q += [("CUSTOMER_RECEIPT", i) for i in await ids(select(CustomerPayment.id).where(
        CustomerPayment.pharmacy_id == pid, CustomerPayment.source == "COLLECTION", CustomerPayment.received_at >= start))]
    q += [("CUSTOMER_RECEIPT_REVERSAL", i) for i in await ids(select(CustomerPayment.id).where(
        CustomerPayment.pharmacy_id == pid, CustomerPayment.status == "REVERSED", CustomerPayment.received_at >= start))]
    q += [("CUSTOMER_REFUND", i) for i in await ids(select(CustomerRefund.id).where(
        CustomerRefund.pharmacy_id == pid, CustomerRefund.refunded_at >= start))]
    q += [("CUSTOMER_ADJUSTMENT", i) for i in await ids(select(CustomerLedgerEntry.id).where(
        CustomerLedgerEntry.pharmacy_id == pid, CustomerLedgerEntry.created_at >= start,
        CustomerLedgerEntry.entry_type.in_(("WRITE_OFF", "ADJUSTMENT_CREDIT"))))]
    q += [("GOODS_RECEIPT", i) for i in await ids(select(GoodsReceipt.id).where(
        GoodsReceipt.pharmacy_id == pid, GoodsReceipt.status == "POSTED", GoodsReceipt.receipt_date >= since))]
    q += [("SUPPLIER_BILL", i) for i in await ids(select(SupplierInvoice.id).where(
        SupplierInvoice.pharmacy_id == pid, SupplierInvoice.finalized_at.is_not(None), SupplierInvoice.invoice_date >= since))]
    q += [("SUPPLIER_BILL_CANCEL", i) for i in await ids(select(SupplierInvoice.id).where(
        SupplierInvoice.pharmacy_id == pid, SupplierInvoice.finalized_at.is_not(None), SupplierInvoice.status == "CANCELLED",
        SupplierInvoice.invoice_date >= since))]
    q += [("SUPPLIER_RETURN", i) for i in await ids(select(SupplierReturn.id).where(
        SupplierReturn.pharmacy_id == pid, SupplierReturn.status == "POSTED", SupplierReturn.return_date >= since))]
    q += [("SUPPLIER_PAYMENT", i) for i in await ids(select(SupplierPayment.id).where(
        SupplierPayment.pharmacy_id == pid, SupplierPayment.payment_date >= since))]
    q += [("SUPPLIER_PAYMENT_REVERSAL", i) for i in await ids(select(SupplierPayment.id).where(
        SupplierPayment.pharmacy_id == pid, SupplierPayment.status == "REVERSED", SupplierPayment.payment_date >= since))]
    q += [("EXPENSE", i) for i in await ids(select(Expense.id).where(
        Expense.pharmacy_id == pid, Expense.status.in_(("APPROVED", "REVERSED")), Expense.expense_date >= since))]
    q += [("EXPENSE_REVERSAL", i) for i in await ids(select(Expense.id).where(
        Expense.pharmacy_id == pid, Expense.status == "REVERSED", Expense.expense_date >= since))]
    q += [("CASH_MOVEMENT", i) for i in await ids(select(CashMovement.id).where(
        CashMovement.pharmacy_id == pid, CashMovement.created_at >= start))]
    q += [("SHIFT_VARIANCE", i) for i in await ids(select(CashShift.id).where(
        CashShift.pharmacy_id == pid, CashShift.closed_at >= start))]
    q += [("STOCK_ADJUSTMENT", i) for i in await ids(select(StockAdjustment.id).where(
        StockAdjustment.pharmacy_id == pid, StockAdjustment.created_at >= start))]
    from app.models.payables import SupplierCreditNote
    q += [("SUPPLIER_CREDIT_NOTE", i) for i in await ids(select(SupplierCreditNote.id).where(
        SupplierCreditNote.pharmacy_id == pid, SupplierCreditNote.note_date >= since))]
    q += [("SUPPLIER_CREDIT_NOTE_CANCEL", i) for i in await ids(select(SupplierCreditNote.id).where(
        SupplierCreditNote.pharmacy_id == pid, SupplierCreditNote.status == "CANCELLED", SupplierCreditNote.note_date >= since))]
    from app.models.accounting import PaymentSettlement
    q += [("SETTLEMENT", i) for i in await ids(select(PaymentSettlement.id).where(
        PaymentSettlement.pharmacy_id == pid, PaymentSettlement.settlement_date >= since))]
    q += [("SETTLEMENT_REVERSAL", i) for i in await ids(select(PaymentSettlement.id).where(
        PaymentSettlement.pharmacy_id == pid, PaymentSettlement.status == "REVERSED", PaymentSettlement.settlement_date >= since))]
    return q


async def reconciliation(db: AsyncSession, pharmacy_id: int) -> dict:
    """
    Source documents without their journal, journals that no longer match their document,
    open posting issues, and control accounts compared with the operational sub-ledgers.
    """
    books_start = await ledger.books_start_date(db, pharmacy_id)
    if books_start is None:
        return {"set_up": False, "ok": True, "missing": [], "mismatched": [], "unbuildable": [], "open_issues": [],
                "controls": [], "checked": 0}
    missing, mismatched, unbuildable = [], [], []
    checked = 0
    for source_type, source_id in await eligible_sources(db, pharmacy_id, books_start):
        key = f"{source_type}:{source_id}"
        try:
            spec, lines = await build(db, pharmacy_id, books_start, source_type, source_id)
        except PostingError as exc:
            unbuildable.append({"source_key": key, "error": str(exc)})
            continue
        if spec is None:
            continue
        checked += 1
        entry = await ledger.entry_by_source(db, pharmacy_id, key)
        if entry is None:
            missing.append({"source_key": key, "date": spec.entry_date, "narration": spec.narration})
        elif entry.request_hash != ledger.lines_fingerprint(spec.entry_date, lines):
            mismatched.append({"source_key": key, "entry_number": entry.entry_number, "narration": spec.narration})
    issues = (await db.execute(select(GlPostingIssue).where(
        GlPostingIssue.pharmacy_id == pharmacy_id, GlPostingIssue.status == "OPEN").order_by(GlPostingIssue.id))).scalars().all()
    controls = await control_comparison(db, pharmacy_id)
    ok = not (missing or mismatched or unbuildable or issues) and all(c["ok"] for c in controls)
    return {"set_up": True, "books_start_date": books_start, "ok": ok, "checked": checked, "missing": missing,
            "mismatched": mismatched, "unbuildable": unbuildable,
            "open_issues": [_issue_out(i) for i in issues], "controls": controls}


async def issue_for(db: AsyncSession, pharmacy_id: int, key: str) -> Optional[GlPostingIssue]:
    return (await db.execute(select(GlPostingIssue).where(
        GlPostingIssue.pharmacy_id == pharmacy_id, GlPostingIssue.source_key == key))).scalar_one_or_none()


async def open_issue_count(db: AsyncSession, pharmacy_id: int) -> int:
    return (await db.execute(select(func.count(GlPostingIssue.id)).where(
        GlPostingIssue.pharmacy_id == pharmacy_id, GlPostingIssue.status == "OPEN"))).scalar_one()


def _issue_out(i: GlPostingIssue) -> dict:
    return {"id": i.id, "source_key": i.source_key, "source_type": i.source_type, "source_id": i.source_id,
            "event_date": i.event_date, "status": i.status, "error": i.error, "attempts": i.attempts,
            "first_seen_at": i.first_seen_at, "last_attempt_at": i.last_attempt_at, "resolved_at": i.resolved_at}


async def _gl_balance(db: AsyncSession, pharmacy_id: int, key: str, party_type: Optional[str] = None) -> dict:
    from app.models.accounting import JournalLine
    acc = (await ledger.accounts_by_key(db, pharmacy_id)).get(key)
    if acc is None:
        return {}
    stmt = (select(JournalLine.party_id, func.sum(JournalLine.debit - JournalLine.credit))
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .where(JournalEntry.pharmacy_id == pharmacy_id, JournalEntry.status.in_(ledger.POSTED_STATES),
                   JournalLine.account_id == acc.id).group_by(JournalLine.party_id))
    return {pid: money(v or 0) for pid, v in (await db.execute(stmt)).all()}


async def control_comparison(db: AsyncSession, pharmacy_id: int) -> List[dict]:
    """GL control accounts against the operational figures (meaningful once opening balances are posted)."""
    from app.services.supplier_payments import supplier_balances
    rows = []
    # Receivables: GL AR per customer vs khata balance
    ar = await _gl_balance(db, pharmacy_id, "AR")
    khata = {cid: money(v or 0) for cid, v in (await db.execute(
        select(CustomerLedgerEntry.customer_id, func.sum(CustomerLedgerEntry.debit - CustomerLedgerEntry.credit))
        .where(CustomerLedgerEntry.pharmacy_id == pharmacy_id).group_by(CustomerLedgerEntry.customer_id))).all()}
    diffs = [{"party_id": c, "gl": ar.get(c, ZERO), "operational": khata.get(c, ZERO)}
             for c in sorted(set(ar) | set(khata), key=lambda x: x or 0) if ar.get(c, ZERO) != khata.get(c, ZERO)]
    rows.append({"control": "Trade Receivables vs customer khata", "gl": money(sum(ar.values(), ZERO)),
                 "operational": money(sum(khata.values(), ZERO)), "ok": not diffs, "differences": diffs})
    # Payables: GL AP (credit balance) per supplier vs supplier balance
    ap = await _gl_balance(db, pharmacy_id, "AP")
    sup = {r["supplier_id"]: r["balance"] for r in await supplier_balances(db, pharmacy_id)}
    diffs = [{"party_id": s, "gl": -ap.get(s, ZERO), "operational": sup.get(s, ZERO)}
             for s in sorted(set(ap) | set(sup)) if -ap.get(s, ZERO) != sup.get(s, ZERO)]
    rows.append({"control": "Trade Payables vs supplier balances", "gl": money(-sum(ap.values(), ZERO)),
                 "operational": money(sum(sup.values(), ZERO)), "ok": not diffs, "differences": diffs})
    # Inventory: GL vs ERP stock value at movement cost
    inv = money(sum((await _gl_balance(db, pharmacy_id, "INVENTORY")).values(), ZERO))
    erp = money(sum((money(q * c) for q, c in (await db.execute(select(StockMovement.quantity_base, StockMovement.unit_cost)
                                                                 .where(StockMovement.pharmacy_id == pharmacy_id))).all()), ZERO))
    rows.append({"control": "Inventory vs ERP stock value", "gl": inv, "operational": erp, "ok": inv == erp, "differences": []})
    return rows


async def retry(db: AsyncSession, user: User, include_missing: bool = True, source_key: Optional[str] = None) -> dict:
    """Post open issues (and, optionally, documents found without a journal). Idempotent."""
    pid = user.pharmacy_id
    books_start = await ledger.books_start_date(db, pid)
    if books_start is None:
        raise HTTPException(status_code=409, detail="Accounting has not been set up yet")
    keys = []
    if source_key:
        keys = [source_key]
    else:
        keys = [i.source_key for i in (await db.execute(select(GlPostingIssue).where(
            GlPostingIssue.pharmacy_id == pid, GlPostingIssue.status == "OPEN").order_by(GlPostingIssue.id))).scalars().all()]
        if include_missing:
            for source_type, source_id in await eligible_sources(db, pid, books_start):
                key = f"{source_type}:{source_id}"
                if key not in keys and await ledger.entry_by_source(db, pid, key) is None:
                    keys.append(key)
    # Originals before their reversals
    keys.sort(key=lambda k: ("REVERSAL" in k or "CANCEL" in k or "VOID" in k))
    results: Dict[str, str] = {}
    for key in keys:
        source_type, _, source_id = key.rpartition(":")
        if source_type not in BUILDERS or not source_id.isdigit():
            results[key] = "unknown source"
            continue
        results[key] = await record(db, pid, user.id, source_type, int(source_id))
    db.add(AuditLog(pharmacy_id=pid, user_id=user.id, action="gl.posting_retry", entity_type="gl_posting",
                    entity_id=source_key or "all", metadata_json=json.dumps({k: v for k, v in list(results.items())[:200]})))
    await db.flush()
    summary = {s: sum(1 for v in results.values() if v == s) for s in ("posted", "already_posted", "skipped", "failed")}
    return {"results": results, "summary": summary}


async def statuses(db: AsyncSession, pharmacy_id: int, keys: List[str]) -> Dict[str, dict]:
    """
    Ledger status of documents: POSTED (with entry number), FAILED (open issue), PENDING (eligible
    but no journal yet), NOT_APPLICABLE (nothing to post / before the books start), NOT_SET_UP.
    """
    books_start = await ledger.books_start_date(db, pharmacy_id)
    out: Dict[str, dict] = {}
    for key in keys[:200]:
        source_type, _, sid = key.rpartition(":")
        if source_type not in BUILDERS or not sid.isdigit():
            out[key] = {"status": "UNKNOWN"}
            continue
        if books_start is None:
            out[key] = {"status": "NOT_SET_UP"}
            continue
        entry = await ledger.entry_by_source(db, pharmacy_id, key)
        if entry is not None:
            out[key] = {"status": "POSTED" if entry.status == "POSTED" else entry.status, "entry_number": entry.entry_number,
                        "entry_id": entry.id}
            continue
        issue = await issue_for(db, pharmacy_id, key)
        if issue is not None and issue.status == "OPEN":
            out[key] = {"status": "FAILED", "error": issue.error}
            continue
        try:
            spec, _ = await build(db, pharmacy_id, books_start, source_type, int(sid))
        except PostingError as exc:
            out[key] = {"status": "FAILED", "error": str(exc)}
            continue
        out[key] = {"status": "PENDING" if spec is not None else "NOT_APPLICABLE"}
    return out
