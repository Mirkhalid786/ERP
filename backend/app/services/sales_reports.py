"""
Sales, receivables and tax reports.

Definitions used throughout (voided invoices are excluded and reported separately):
  gross_sales       Σ quantity × selling rate, GST-inclusive, before discounts
  discounts         Σ line discounts
  net_sales         Σ invoice grand totals (gross - discounts + round-off), GST-inclusive
  taxable_value     net sales excluding GST
  tax               CGST + SGST + IGST contained in net sales
  returns           value of goods returned (GST-inclusive) in the period
  net_after_returns net_sales - returns
  collections       money received in the period (at the counter + later payments), by method
  credit_given      part of the period's bills not paid at the counter
  outstanding       open balances of finalised invoices (as of now)
  gross_margin      (taxable value - returned taxable value) - (cost of goods sold - cost of
                    goods restocked by returns), using each batch's weighted-average cost
                    (excl. GST). Indicative only: it relies on purchase costs being complete.
"""
import csv
import io
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterable, List, Optional

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth import User
from app.models.sales import (
    CustomerPayment, CustomerRefund, SalesInvoice, SalesInvoiceItem, SalesReturn, SalesReturnItem,
)
from app.services.receivables import aging_report
from app.services.sales_common import ZERO, day_bounds, local_date, money, today_local


def _range(date_from: date, date_to: date):
    return day_bounds(date_from)[0], day_bounds(date_to)[1]


def _inv_conds(pharmacy_id: int, start, end, include_voided: bool = False):
    conds = [SalesInvoice.pharmacy_id == pharmacy_id, SalesInvoice.invoice_date >= start,
             SalesInvoice.invoice_date < end]
    if not include_voided:
        conds.append(SalesInvoice.status == "FINALIZED")
    return conds


async def summary(db: AsyncSession, pharmacy_id: int, date_from: date, date_to: date) -> dict:
    start, end = _range(date_from, date_to)
    inv = (await db.execute(
        select(
            func.count(SalesInvoice.id),
            func.coalesce(func.sum(SalesInvoice.gross_amount), 0),
            func.coalesce(func.sum(SalesInvoice.discount_total), 0),
            func.coalesce(func.sum(SalesInvoice.grand_total), 0),
            func.coalesce(func.sum(SalesInvoice.taxable_total), 0),
            func.coalesce(func.sum(SalesInvoice.tax_total), 0),
            func.coalesce(func.sum(SalesInvoice.round_off), 0),
            func.coalesce(func.sum(SalesInvoice.cost_total), 0),
            func.coalesce(func.sum(SalesInvoice.grand_total - SalesInvoice.paid_at_sale), 0),
        ).where(*_inv_conds(pharmacy_id, start, end))
    )).one()
    bills = inv[0]
    ret = (await db.execute(
        select(
            func.count(SalesReturn.id),
            func.coalesce(func.sum(SalesReturn.total_amount), 0),
            func.coalesce(func.sum(SalesReturn.taxable_total), 0),
            func.coalesce(func.sum(SalesReturn.refund_amount), 0),
        ).where(SalesReturn.pharmacy_id == pharmacy_id, SalesReturn.created_at >= start, SalesReturn.created_at < end)
    )).one()
    restocked_cost = money((await db.execute(
        select(func.coalesce(func.sum(SalesReturnItem.cost_total), 0))
        .join(SalesReturn, SalesReturn.id == SalesReturnItem.return_id)
        .where(SalesReturn.pharmacy_id == pharmacy_id, SalesReturn.created_at >= start,
               SalesReturn.created_at < end, SalesReturnItem.disposition == "RESTOCKED")
    )).scalar_one())
    voids = (await db.execute(
        select(func.count(SalesInvoice.id), func.coalesce(func.sum(SalesInvoice.grand_total), 0))
        .where(SalesInvoice.pharmacy_id == pharmacy_id, SalesInvoice.voided_at >= start,
               SalesInvoice.voided_at < end, SalesInvoice.status == "VOIDED")
    )).one()
    collections = await payment_totals(db, pharmacy_id, date_from, date_to)
    net_sales = money(inv[3])
    revenue_ex_tax = money(inv[4]) - money(ret[2])
    cogs = money(inv[7]) - restocked_cost
    return {
        "date_from": date_from, "date_to": date_to,
        "bills": bills,
        "gross_sales": money(inv[1]),
        "discounts": money(inv[2]),
        "round_off": money(inv[6]),
        "net_sales": net_sales,
        "taxable_value": money(inv[4]),
        "tax": money(inv[5]),
        "average_bill": money(net_sales / bills) if bills else ZERO,
        "credit_given": money(inv[8]),
        "returns_count": ret[0],
        "returns_value": money(ret[1]),
        "refunds_paid": money(ret[3]),
        "net_after_returns": money(net_sales - money(ret[1])),
        "voided_bills": voids[0],
        "voided_value": money(voids[1]),
        "collections_total": collections["total"],
        "collections_by_method": collections["by_method"],
        "cost_of_goods_sold": money(cogs),
        "gross_margin": money(revenue_ex_tax - cogs),
        "gross_margin_basis": "Taxable value after returns minus batch weighted-average cost; excludes GST",
    }


async def payment_totals(db: AsyncSession, pharmacy_id: int, date_from: date, date_to: date) -> dict:
    start, end = _range(date_from, date_to)
    rows = (await db.execute(
        select(CustomerPayment.method, CustomerPayment.source, func.sum(CustomerPayment.amount), func.count())
        .where(CustomerPayment.pharmacy_id == pharmacy_id, CustomerPayment.received_at >= start,
               CustomerPayment.received_at < end, CustomerPayment.status == "POSTED",
               CustomerPayment.method != "CREDIT_NOTE")
        .group_by(CustomerPayment.method, CustomerPayment.source)
    )).all()
    refunds = (await db.execute(
        select(CustomerRefund.method, func.sum(CustomerRefund.amount))
        .where(CustomerRefund.pharmacy_id == pharmacy_id, CustomerRefund.refunded_at >= start,
               CustomerRefund.refunded_at < end)
        .group_by(CustomerRefund.method)
    )).all()
    by_method: dict = {}
    for method, source, amount, count in rows:
        row = by_method.setdefault(method, {"at_sale": ZERO, "collections": ZERO, "refunds": ZERO, "count": 0})
        row["at_sale" if source == "SALE" else "collections"] += money(amount or 0)
        row["count"] += count
    for method, amount in refunds:
        row = by_method.setdefault(method, {"at_sale": ZERO, "collections": ZERO, "refunds": ZERO, "count": 0})
        row["refunds"] += money(amount or 0)
    total = ZERO
    for row in by_method.values():
        row["received"] = money(row["at_sale"] + row["collections"])
        row["net"] = money(row["received"] - row["refunds"])
        total += row["received"]
    return {"by_method": by_method, "total": money(total)}


async def dashboard(db: AsyncSession, pharmacy_id: int) -> dict:
    today = today_local()
    month_start = today.replace(day=1)
    today_summary = await summary(db, pharmacy_id, today, today)
    month_summary = await summary(db, pharmacy_id, month_start, today)
    outstanding = (await db.execute(
        select(
            func.coalesce(func.sum(SalesInvoice.balance_due), 0),
            func.coalesce(func.sum(case((SalesInvoice.due_date < today, SalesInvoice.balance_due), else_=0)), 0),
            func.count(func.distinct(SalesInvoice.customer_id)),
        ).where(SalesInvoice.pharmacy_id == pharmacy_id, SalesInvoice.status == "FINALIZED",
                SalesInvoice.balance_due > 0)
    )).one()
    return {
        "today": today_summary,
        "month": month_summary,
        "outstanding_receivables": money(outstanding[0]),
        "overdue_receivables": money(outstanding[1]),
        "customers_with_balance": outstanding[2],
    }


async def daily(db: AsyncSession, pharmacy_id: int, date_from: date, date_to: date) -> List[dict]:
    start, end = _range(date_from, date_to)
    invoices = (await db.execute(select(SalesInvoice).where(*_inv_conds(pharmacy_id, start, end)))).scalars().all()
    returns = (await db.execute(
        select(SalesReturn).where(SalesReturn.pharmacy_id == pharmacy_id, SalesReturn.created_at >= start,
                                  SalesReturn.created_at < end)
    )).scalars().all()
    days: dict = {}
    d = date_from
    while d <= date_to:
        days[d] = {"date": d, "bills": 0, "gross_sales": ZERO, "discounts": ZERO, "net_sales": ZERO,
                   "taxable_value": ZERO, "tax": ZERO, "returns_value": ZERO, "credit_given": ZERO}
        d += timedelta(days=1)
    for inv in invoices:
        row = days.get(local_date(inv.invoice_date))
        if row is None:
            continue
        row["bills"] += 1
        row["gross_sales"] += inv.gross_amount
        row["discounts"] += inv.discount_total
        row["net_sales"] += inv.grand_total
        row["taxable_value"] += inv.taxable_total
        row["tax"] += inv.tax_total
        row["credit_given"] += inv.grand_total - inv.paid_at_sale
    for r in returns:
        row = days.get(local_date(r.created_at))
        if row is not None:
            row["returns_value"] += r.total_amount
    out = []
    for row in days.values():
        row["net_after_returns"] = money(row["net_sales"] - row["returns_value"])
        out.append({k: (money(v) if isinstance(v, Decimal) else v) for k, v in row.items()})
    return out


async def items(db: AsyncSession, pharmacy_id: int, date_from: date, date_to: date, by_batch: bool = False) -> List[dict]:
    start, end = _range(date_from, date_to)
    group = [SalesInvoiceItem.medicine_id, SalesInvoiceItem.medicine_name]
    if by_batch:
        group += [SalesInvoiceItem.batch_id, SalesInvoiceItem.batch_number, SalesInvoiceItem.expiry_date]
    rows = (await db.execute(
        select(
            *group,
            func.sum(SalesInvoiceItem.quantity_base), func.sum(SalesInvoiceItem.returned_quantity_base),
            func.sum(SalesInvoiceItem.gross_amount), func.sum(SalesInvoiceItem.discount_amount),
            func.sum(SalesInvoiceItem.line_total), func.sum(SalesInvoiceItem.taxable_amount),
            func.sum(SalesInvoiceItem.tax_amount), func.sum(SalesInvoiceItem.cost_total),
            func.count(func.distinct(SalesInvoiceItem.invoice_id)),
        )
        .join(SalesInvoice, SalesInvoice.id == SalesInvoiceItem.invoice_id)
        .where(*_inv_conds(pharmacy_id, start, end))
        .group_by(*group)
        .order_by(func.sum(SalesInvoiceItem.line_total).desc())
    )).all()
    out = []
    for r in rows:
        n = len(group)
        rec = {"medicine_id": r[0], "medicine_name": r[1]}
        if by_batch:
            rec.update({"batch_id": r[2], "batch_number": r[3], "expiry_date": r[4]})
        qty, returned, gross, disc, net, taxable, tax, cost, bills = r[n:]
        rec.update({
            "quantity_sold_base": money(qty or 0), "quantity_returned_base": money(returned or 0),
            "gross_sales": money(gross or 0), "discounts": money(disc or 0), "net_sales": money(net or 0),
            "taxable_value": money(taxable or 0), "tax": money(tax or 0), "cost": money(cost or 0),
            "margin_before_returns": money((taxable or 0) - (cost or 0)), "bills": bills,
        })
        out.append(rec)
    return out


async def cashiers(db: AsyncSession, pharmacy_id: int, date_from: date, date_to: date) -> List[dict]:
    start, end = _range(date_from, date_to)
    rows = (await db.execute(
        select(SalesInvoice.cashier_id, User.name, func.count(SalesInvoice.id),
               func.sum(SalesInvoice.grand_total), func.sum(SalesInvoice.discount_total),
               func.sum(SalesInvoice.grand_total - SalesInvoice.paid_at_sale))
        .join(User, User.id == SalesInvoice.cashier_id)
        .where(*_inv_conds(pharmacy_id, start, end))
        .group_by(SalesInvoice.cashier_id, User.name)
    )).all()
    return [{"cashier_id": r[0], "cashier_name": r[1], "bills": r[2], "net_sales": money(r[3] or 0),
             "discounts": money(r[4] or 0), "credit_given": money(r[5] or 0),
             "average_bill": money((r[3] or 0) / r[2]) if r[2] else ZERO} for r in rows]


async def credit_and_collections(db: AsyncSession, pharmacy_id: int, date_from: date, date_to: date) -> dict:
    start, end = _range(date_from, date_to)
    credit = (await db.execute(
        select(SalesInvoice).where(*_inv_conds(pharmacy_id, start, end), SalesInvoice.is_credit_sale.is_(True))
        .order_by(SalesInvoice.invoice_date)
    )).scalars().all()
    collections = (await db.execute(
        select(CustomerPayment).where(
            CustomerPayment.pharmacy_id == pharmacy_id, CustomerPayment.source == "COLLECTION",
            CustomerPayment.received_at >= start, CustomerPayment.received_at < end,
            CustomerPayment.status == "POSTED",
        ).order_by(CustomerPayment.received_at)
    )).scalars().all()
    return {
        "credit_sales": [{
            "invoice_id": i.id, "invoice_number": i.invoice_number, "invoice_date": i.invoice_date,
            "customer_name": i.customer_name, "grand_total": i.grand_total, "paid_at_sale": i.paid_at_sale,
            "credit_given": money(i.grand_total - i.paid_at_sale), "balance_due": i.balance_due,
            "due_date": i.due_date,
        } for i in credit],
        "credit_total": money(sum((i.grand_total - i.paid_at_sale for i in credit), ZERO)),
        "collections": [{
            "payment_id": p.id, "receipt_number": p.receipt_number, "received_at": p.received_at,
            "customer_id": p.customer_id, "method": p.method, "amount": p.amount, "reference": p.reference,
            "verification_status": p.verification_status,
        } for p in collections],
        "collections_total": money(sum((p.amount for p in collections), ZERO)),
    }


async def returns_and_voids(db: AsyncSession, pharmacy_id: int, date_from: date, date_to: date) -> dict:
    start, end = _range(date_from, date_to)
    rets = (await db.execute(
        select(SalesReturn, SalesInvoice.invoice_number, SalesInvoice.customer_name)
        .join(SalesInvoice, SalesInvoice.id == SalesReturn.invoice_id)
        .where(SalesReturn.pharmacy_id == pharmacy_id, SalesReturn.created_at >= start, SalesReturn.created_at < end)
        .order_by(SalesReturn.created_at)
    )).all()
    voids = (await db.execute(
        select(SalesInvoice).where(SalesInvoice.pharmacy_id == pharmacy_id, SalesInvoice.status == "VOIDED",
                                   SalesInvoice.voided_at >= start, SalesInvoice.voided_at < end)
    )).scalars().all()
    return {
        "returns": [{
            "return_id": r.id, "return_number": r.return_number, "created_at": r.created_at,
            "invoice_number": num, "customer_name": cust, "reason": r.reason, "total_amount": r.total_amount,
            "applied_to_balance": r.applied_to_balance, "refund_amount": r.refund_amount,
            "refund_method": r.refund_method, "credit_note_amount": r.credit_note_amount,
        } for r, num, cust in rets],
        "voids": [{
            "invoice_id": v.id, "invoice_number": v.invoice_number, "voided_at": v.voided_at,
            "customer_name": v.customer_name, "grand_total": v.grand_total, "reason": v.void_reason,
            "refunded_amount": v.refunded_amount,
        } for v in voids],
    }


async def discounts(db: AsyncSession, pharmacy_id: int, date_from: date, date_to: date) -> List[dict]:
    start, end = _range(date_from, date_to)
    rows = (await db.execute(
        select(SalesInvoice, User.name)
        .join(User, User.id == SalesInvoice.cashier_id)
        .where(*_inv_conds(pharmacy_id, start, end), SalesInvoice.discount_total > 0)
        .order_by(SalesInvoice.invoice_date)
    )).all()
    return [{"invoice_id": i.id, "invoice_number": i.invoice_number, "invoice_date": i.invoice_date,
             "cashier_name": name, "customer_name": i.customer_name, "gross_amount": i.gross_amount,
             "discount_total": i.discount_total,
             "discount_percent": money(i.discount_total * 100 / i.gross_amount) if i.gross_amount else ZERO}
            for i, name in rows]


async def tax_summary(db: AsyncSession, pharmacy_id: int, date_from: date, date_to: date) -> dict:
    start, end = _range(date_from, date_to)
    sales = (await db.execute(
        select(SalesInvoiceItem.tax_rate, func.sum(SalesInvoiceItem.taxable_amount),
               func.sum(SalesInvoiceItem.cgst_amount), func.sum(SalesInvoiceItem.sgst_amount),
               func.sum(SalesInvoiceItem.igst_amount), func.sum(SalesInvoiceItem.line_total))
        .join(SalesInvoice, SalesInvoice.id == SalesInvoiceItem.invoice_id)
        .where(*_inv_conds(pharmacy_id, start, end))
        .group_by(SalesInvoiceItem.tax_rate).order_by(SalesInvoiceItem.tax_rate)
    )).all()
    returns = (await db.execute(
        select(SalesInvoiceItem.tax_rate, func.sum(SalesReturnItem.taxable_amount),
               func.sum(SalesReturnItem.cgst_amount), func.sum(SalesReturnItem.sgst_amount),
               func.sum(SalesReturnItem.igst_amount), func.sum(SalesReturnItem.line_total))
        .join(SalesInvoiceItem, SalesInvoiceItem.id == SalesReturnItem.invoice_item_id)
        .join(SalesReturn, SalesReturn.id == SalesReturnItem.return_id)
        .where(SalesReturn.pharmacy_id == pharmacy_id, SalesReturn.created_at >= start, SalesReturn.created_at < end)
        .group_by(SalesInvoiceItem.tax_rate)
    )).all()

    def rows(data):
        return [{"tax_rate": r[0], "taxable_value": money(r[1] or 0), "cgst": money(r[2] or 0),
                 "sgst": money(r[3] or 0), "igst": money(r[4] or 0),
                 "total_tax": money((r[2] or 0) + (r[3] or 0) + (r[4] or 0)), "value_incl_tax": money(r[5] or 0)}
                for r in data]
    return {
        "sales": rows(sales),
        "returns": rows(returns),
        "note": "Line-level tax as billed; invoice round-off is not part of taxable value. "
                "Have a CA confirm GSTR-1 treatment (B2C small, B2B, credit notes) before filing.",
    }


async def register(db: AsyncSession, pharmacy_id: int, date_from: date, date_to: date) -> List[dict]:
    start, end = _range(date_from, date_to)
    invoices = (await db.execute(
        select(SalesInvoice).where(*_inv_conds(pharmacy_id, start, end, include_voided=True))
        .order_by(SalesInvoice.invoice_date, SalesInvoice.id)
    )).scalars().all()
    return [{
        "invoice_number": i.invoice_number, "invoice_date": i.invoice_date, "status": i.status,
        "customer_name": i.customer_name, "customer_gstin": i.customer_gstin, "taxable_value": i.taxable_total,
        "cgst": i.cgst_total, "sgst": i.sgst_total, "igst": i.igst_total, "round_off": i.round_off,
        "grand_total": i.grand_total, "payment_status": i.payment_status, "balance_due": i.balance_due,
    } for i in invoices]


async def aging(db: AsyncSession, pharmacy_id: int) -> dict:
    return await aging_report(db, pharmacy_id)


def to_csv(rows: Iterable[dict]) -> str:
    rows = list(rows)
    buf = io.StringIO()
    if not rows:
        return ""
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: ("" if v is None else v) for k, v in r.items()})
    return buf.getvalue()
