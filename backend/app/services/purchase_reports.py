"""Phase 6: purchase summary (supplier-wise) and input GST by rate, from finalised supplier bills."""
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inventory import TaxRate
from app.models.purchasing import Supplier, SupplierInvoice, SupplierInvoiceItem, SupplierReturn
from app.services.sales_common import ZERO, money


async def purchase_summary(db: AsyncSession, pharmacy_id: int, date_from: date, date_to: date) -> dict:
    conds = [SupplierInvoice.pharmacy_id == pharmacy_id, SupplierInvoice.status == "POSTED",
             SupplierInvoice.invoice_date >= date_from, SupplierInvoice.invoice_date <= date_to]
    rows = (await db.execute(
        select(Supplier.id, Supplier.name, func.count(SupplierInvoice.id), func.sum(SupplierInvoice.subtotal),
               func.sum(SupplierInvoice.discount_total), func.sum(SupplierInvoice.cgst_total), func.sum(SupplierInvoice.sgst_total),
               func.sum(SupplierInvoice.igst_total), func.sum(SupplierInvoice.other_charges),
               func.sum(SupplierInvoice.calculated_grand_total), func.sum(SupplierInvoice.paid_amount))
        .join(Supplier, Supplier.id == SupplierInvoice.supplier_id).where(*conds)
        .group_by(Supplier.id, Supplier.name).order_by(Supplier.name))).all()
    returns = dict((await db.execute(select(SupplierReturn.supplier_id, func.sum(SupplierReturn.total_return_amount)).where(
        SupplierReturn.pharmacy_id == pharmacy_id, SupplierReturn.status == "POSTED",
        SupplierReturn.return_date >= date_from, SupplierReturn.return_date <= date_to).group_by(SupplierReturn.supplier_id))).all())
    from app.models.payables import SupplierCreditNote
    for sid, amount in (await db.execute(select(SupplierCreditNote.supplier_id, func.sum(SupplierCreditNote.total_amount)).where(
            SupplierCreditNote.pharmacy_id == pharmacy_id, SupplierCreditNote.status == "POSTED",
            SupplierCreditNote.note_date >= date_from, SupplierCreditNote.note_date <= date_to)
            .group_by(SupplierCreditNote.supplier_id))).all():
        returns[sid] = money(returns.get(sid) or 0) + money(amount or 0)     # Returns column includes credit notes
    suppliers = []
    for sid, name, bills, gross, disc, cgst, sgst, igst, other, total, paid in rows:
        suppliers.append({"supplier_id": sid, "supplier": name, "bills": bills, "gross": money(gross or 0),
                          "discount": money(disc or 0), "cgst": money(cgst or 0), "sgst": money(sgst or 0),
                          "igst": money(igst or 0), "other_charges": money(other or 0), "total": money(total or 0),
                          "paid": money(paid or 0), "returns": money(returns.get(sid) or 0)})
    by_rate = [{"rate": str(rate) if rate is not None else "No GST", "taxable": money(taxable or 0), "cgst": money(c or 0),
                "sgst": money(s or 0), "igst": money(i or 0), "tax": money(t or 0)}
               for rate, taxable, c, s, i, t in (await db.execute(
                   select(TaxRate.rate_percentage, func.sum(SupplierInvoiceItem.taxable_amount), func.sum(SupplierInvoiceItem.cgst_amount),
                          func.sum(SupplierInvoiceItem.sgst_amount), func.sum(SupplierInvoiceItem.igst_amount),
                          func.sum(SupplierInvoiceItem.tax_amount))
                   .join(SupplierInvoice, SupplierInvoice.id == SupplierInvoiceItem.invoice_id)
                   .outerjoin(TaxRate, TaxRate.id == SupplierInvoiceItem.tax_rate_id)
                   .where(*conds).group_by(TaxRate.rate_percentage).order_by(TaxRate.rate_percentage))).all()]
    keys = ("bills", "gross", "discount", "cgst", "sgst", "igst", "other_charges", "total", "paid", "returns")
    totals = {k: (sum(r[k] for r in suppliers) if k == "bills" else money(sum((r[k] for r in suppliers), ZERO))) for k in keys}
    return {"date_from": date_from, "date_to": date_to, "suppliers": suppliers, "input_gst_by_rate": by_rate, "totals": totals,
            "note": "Finalised supplier bills only (drafts and cancelled bills excluded). Input GST shown here is as billed; "
                    "eligibility for credit is for your CA to confirm (GSTR-2B)."}
