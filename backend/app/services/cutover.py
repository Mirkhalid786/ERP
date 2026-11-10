"""
Read-only inventory of the operational transactions that exist before accounting starts,
for planning the books start date and opening balances (nothing is changed or posted).

Records are flagged as probable test data when the party / document carries a "TEST"
marker (the convention used for demo scenarios); flags are advisory only.
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_date
from app.models.inventory import Medicine, StockMovement
from app.models.payables import Expense, SupplierPayment
from app.models.purchasing import GoodsReceipt, Supplier, SupplierInvoice, SupplierReturn
from app.models.sales import (
    CashMovement, CashShift, Customer, CustomerLedgerEntry, CustomerPayment, CustomerRefund, SalesInvoice, SalesReturn,
)
from app.services.sales_common import ZERO, money


def _day(value):
    if value is None:
        return None
    return local_date(value) if isinstance(value, datetime) else value


async def _summarise(db: AsyncSession, label: str, model, date_col, amount_col, pharmacy_id: int,
                     *, where=(), test_filter=None, group_col=None) -> list:
    base = select(model).where(model.pharmacy_id == pharmacy_id, *where).subquery()
    cols = [func.count(), func.min(base.c[date_col]), func.max(base.c[date_col]),
            func.coalesce(func.sum(base.c[amount_col]), 0) if amount_col else func.count()]
    if group_col:
        stmt = select(base.c[group_col], *cols).group_by(base.c[group_col])
    else:
        stmt = select(*cols)
    rows = []
    for row in (await db.execute(stmt)).all():
        group, (count, first, last, total) = (row[0], row[1:]) if group_col else (None, row)
        if not count:
            continue
        rows.append({"item": f"{label} ({group})" if group_col else label, "count": count, "first": _day(first),
                     "last": _day(last), "total": money(total) if amount_col else None})
    if test_filter is not None and rows:
        test_count = (await db.execute(select(func.count()).select_from(
            select(model.id).where(model.pharmacy_id == pharmacy_id, *where, test_filter).subquery()))).scalar_one()
        rows.append({"item": f"{label}: marked TEST", "count": test_count, "first": None, "last": None, "total": None})
    return rows


async def historical_summary(db: AsyncSession, pharmacy_id: int) -> dict:
    test_customer = SalesInvoice.customer_id.in_(select(Customer.id).where(Customer.name.like("TEST%")))
    test_supplier = lambda col: col.in_(select(Supplier.id).where(Supplier.name.like("TEST%")))  # noqa: E731
    sections = {
        "sales": (
            await _summarise(db, "Sales invoices", SalesInvoice, "invoice_date", "grand_total", pharmacy_id,
                             group_col="status", test_filter=or_(test_customer, SalesInvoice.notes.like("%TEST%")))
            + await _summarise(db, "Customer payments", CustomerPayment, "received_at", "amount", pharmacy_id,
                               group_col="method", where=(CustomerPayment.status == "POSTED",))
            + await _summarise(db, "Customer refunds", CustomerRefund, "refunded_at", "amount", pharmacy_id)
            + await _summarise(db, "Sales returns", SalesReturn, "created_at", "total_amount", pharmacy_id, group_col="status")
            + await _summarise(db, "Customer ledger (khata) entries", CustomerLedgerEntry, "created_at", None, pharmacy_id)
        ),
        "purchases": (
            await _summarise(db, "Goods receipts", GoodsReceipt, "receipt_date", "grand_total", pharmacy_id,
                             group_col="status", test_filter=or_(test_supplier(GoodsReceipt.supplier_id),
                                                                 GoodsReceipt.supplier_invoice_no.like("%TEST%")))
            + await _summarise(db, "Supplier invoices", SupplierInvoice, "invoice_date", "calculated_grand_total",
                               pharmacy_id, group_col="status")
            + await _summarise(db, "Supplier returns", SupplierReturn, "return_date", "total_return_amount", pharmacy_id,
                               group_col="status")
            + await _summarise(db, "Supplier payments", SupplierPayment, "payment_date", "amount", pharmacy_id,
                               group_col="status")
        ),
        "cash_and_expenses": (
            await _summarise(db, "Cash shifts", CashShift, "opened_at", None, pharmacy_id, group_col="status")
            + await _summarise(db, "Cash paid-out / paid-in", CashMovement, "created_at", "amount", pharmacy_id,
                               group_col="category")
            + await _summarise(db, "Expenses", Expense, "expense_date", "amount", pharmacy_id, group_col="status")
        ),
        "stock": await _summarise(db, "Stock movements (cost value)", StockMovement, "created_at", "total_cost", pharmacy_id,
                                  group_col="movement_type"),
    }
    khata = money((await db.execute(select(func.coalesce(func.sum(CustomerLedgerEntry.debit - CustomerLedgerEntry.credit), 0))
                                    .where(CustomerLedgerEntry.pharmacy_id == pharmacy_id))).scalar_one())
    # Signed quantity x unit cost (total_cost is stored unsigned), as in the opening-balance reconciliation
    stock_value = money((await db.execute(select(func.coalesce(func.sum(StockMovement.quantity_base * StockMovement.unit_cost), 0))
                                          .where(StockMovement.pharmacy_id == pharmacy_id))).scalar_one())
    test_masters = {
        "customers": (await db.execute(select(func.count(Customer.id)).where(
            Customer.pharmacy_id == pharmacy_id, Customer.name.like("TEST%")))).scalar_one(),
        "suppliers": (await db.execute(select(func.count(Supplier.id)).where(
            Supplier.pharmacy_id == pharmacy_id, Supplier.name.like("TEST%")))).scalar_one(),
        "medicines": (await db.execute(select(func.count(Medicine.id)).where(
            Medicine.pharmacy_id == pharmacy_id, Medicine.name.like("TEST%")))).scalar_one(),
    }
    dates = [r[k] for rows in sections.values() for r in rows for k in ("first", "last") if r.get(k)]
    return {
        "first_activity": min(dates) if dates else None,
        "last_activity": max(dates) if dates else None,
        "sections": sections,
        "balances_now": {"customer_khata_outstanding": khata, "stock_value_at_cost": stock_value},
        "test_masters": test_masters,
        "customer_balances": await _customer_balances(db, pharmacy_id),
        "supplier_balances": await _supplier_balances(db, pharmacy_id),
        "cash": await _cash_position(db, pharmacy_id),
        "bank": "Bank balances are not tracked operationally; take them from the bank statement on the start date.",
        "goods_received_not_billed": await _grni_estimate(db, pharmacy_id),
        "note": ("Read-only. Nothing here is posted to the general ledger. These are the ERP's current figures, "
                 "not proposed opening balances: the opening balances must come from your previous books / CA."),
    }


async def _customer_balances(db: AsyncSession, pharmacy_id: int) -> list:
    rows = (await db.execute(
        select(Customer.id, Customer.name, func.sum(CustomerLedgerEntry.debit - CustomerLedgerEntry.credit))
        .join(Customer, Customer.id == CustomerLedgerEntry.customer_id)
        .where(CustomerLedgerEntry.pharmacy_id == pharmacy_id).group_by(Customer.id, Customer.name))).all()
    return [{"customer_id": cid, "name": name, "balance": money(bal or 0)} for cid, name, bal in rows if money(bal or 0) != ZERO]


async def _supplier_balances(db: AsyncSession, pharmacy_id: int) -> list:
    from app.services.supplier_payments import supplier_balances
    return [{"supplier_id": r["supplier_id"], "name": r["supplier_name"], "balance": r["balance"]}
            for r in await supplier_balances(db, pharmacy_id)]


async def _cash_position(db: AsyncSession, pharmacy_id: int) -> dict:
    from app.services.shifts import shift_summary
    open_shifts = (await db.execute(select(CashShift).where(
        CashShift.pharmacy_id == pharmacy_id, CashShift.status == "OPEN"))).scalars().all()
    last_closed = (await db.execute(select(CashShift).where(
        CashShift.pharmacy_id == pharmacy_id, CashShift.status != "OPEN").order_by(CashShift.closed_at.desc()).limit(1))).scalars().first()
    return {
        "open_shifts": [{"shift_id": s.id, "opened_at": s.opened_at, "expected_cash": (await shift_summary(db, s)).expected_cash}
                        for s in open_shifts],
        "last_closed_shift": {"shift_id": last_closed.id, "closed_at": last_closed.closed_at, "counted_cash": last_closed.counted_cash}
        if last_closed else None,
        "note": "Close every shift and count the cash on the books start date.",
    }


async def _grni_estimate(db: AsyncSession, pharmacy_id: int) -> dict:
    """Stock value received on posted GRNs that no finalised supplier bill has matched yet."""
    from app.models.purchasing import GoodsReceiptItem, SupplierInvoiceItem
    received = money((await db.execute(select(func.coalesce(func.sum(StockMovement.total_cost), 0)).where(
        StockMovement.pharmacy_id == pharmacy_id, StockMovement.movement_type == "GOODS_RECEIPT"))).scalar_one())
    billed = ZERO
    for item, gi in (await db.execute(
            select(SupplierInvoiceItem, GoodsReceiptItem)
            .join(GoodsReceiptItem, GoodsReceiptItem.id == SupplierInvoiceItem.grn_item_id)
            .join(SupplierInvoice, SupplierInvoice.id == SupplierInvoiceItem.invoice_id)
            .where(SupplierInvoice.pharmacy_id == pharmacy_id, SupplierInvoice.status == "POSTED"))).all():
        paid_base = Decimal(str(gi.received_qty_pack)) * Decimal(str(gi.conversion_factor))
        value = money(gi.accepted_qty_base * gi.cost_per_base_unit)
        billed += min(value, money(value * Decimal(str(item.billed_qty_base)) / paid_base)) if paid_base else ZERO
    unbilled_grns = (await db.execute(select(GoodsReceipt.grn_number).where(
        GoodsReceipt.pharmacy_id == pharmacy_id, GoodsReceipt.status == "POSTED",
        ~GoodsReceipt.id.in_(select(SupplierInvoice.grn_id).where(SupplierInvoice.status == "POSTED",
                                                                  SupplierInvoice.grn_id.is_not(None)))))).scalars().all()
    return {"received_value": received, "matched_by_bills": money(billed), "unbilled_value": money(received - billed),
            "grns_without_finalised_bill": list(unbilled_grns)}
