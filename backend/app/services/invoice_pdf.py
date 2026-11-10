"""
A4 PDF tax invoice (fpdf2). Used for "Download PDF" and optional WhatsApp attachments.

Built-in PDF fonts only cover Latin-1, so amounts use "Rs." and other characters
are replaced. No internal database IDs are printed.
"""
from decimal import Decimal

from fastapi import HTTPException
from fpdf import FPDF
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.auth import Pharmacy, User
from app.models.sales import CustomerPayment, PaymentAllocation, SalesInvoice
from app.services import app_settings
from app.services.sales_common import BUSINESS_TZ


def _t(value) -> str:
    text = "" if value is None else str(value)
    return text.replace("₹", "Rs.").encode("latin-1", "replace").decode("latin-1")


def _m(value) -> str:
    return f"{Decimal(str(value)):,.2f}"


async def invoice_pdf_bytes(db: AsyncSession, pharmacy_id: int, invoice_id: int, copy_label: str) -> tuple:
    invoice = (await db.execute(
        select(SalesInvoice).options(selectinload(SalesInvoice.items), selectinload(SalesInvoice.prescription))
        .where(SalesInvoice.id == invoice_id, SalesInvoice.pharmacy_id == pharmacy_id)
    )).scalar_one_or_none()
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    pharmacy = (await db.execute(select(Pharmacy).where(Pharmacy.id == pharmacy_id))).scalar_one()
    cashier = (await db.execute(select(User.name).where(User.id == invoice.cashier_id))).scalar_one_or_none()
    payments = (await db.execute(
        select(CustomerPayment.method, PaymentAllocation.amount, CustomerPayment.reference)
        .join(PaymentAllocation, PaymentAllocation.payment_id == CustomerPayment.id)
        .where(PaymentAllocation.invoice_id == invoice.id, PaymentAllocation.is_reversed.is_(False))
    )).all()
    footer = await app_settings.get_setting(db, pharmacy_id, "invoice.footer_note")

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(0, 8, _t(pharmacy.name), new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.set_font("Helvetica", "", 9)
    address = ", ".join(x for x in [getattr(pharmacy, "address_line1", None), pharmacy.city, pharmacy.pincode] if x)
    for line in [address, f"Phone: {pharmacy.phone}" if pharmacy.phone else "",
                 f"GSTIN: {pharmacy.gstin}" if pharmacy.gstin else "",
                 f"Drug Licence: {pharmacy.drug_license_number}" if pharmacy.drug_license_number else ""]:
        if line:
            pdf.cell(0, 4.5, _t(line), new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 12)
    title = "TAX INVOICE" if invoice.status == "FINALIZED" else "TAX INVOICE - VOIDED"
    pdf.cell(0, 7, _t(f"{title}   [{copy_label}]"), new_x="LMARGIN", new_y="NEXT", align="C")

    pdf.set_font("Helvetica", "", 9)
    when = invoice.invoice_date.astimezone(BUSINESS_TZ).strftime("%d-%m-%Y %I:%M %p")
    status = {"PAID": "PAID", "PARTIALLY_PAID": "PARTIALLY PAID", "UNPAID": "CREDIT - UNPAID"}[invoice.payment_status]
    if invoice.return_status != "NONE":
        status += " | " + ("FULLY RETURNED" if invoice.return_status == "FULL" else "PARTLY RETURNED")
    rows = [
        (f"Invoice No: {invoice.invoice_number}", f"Date: {when}"),
        (f"Customer: {invoice.customer_name}", f"Mobile: {invoice.customer_mobile or '-'}"),
        (f"Customer GSTIN: {invoice.customer_gstin or '-'}", f"Billed by: {cashier or '-'}"),
        (f"Status: {status}", f"Due date: {invoice.due_date.strftime('%d-%m-%Y') if invoice.due_date else '-'}"),
    ]
    if invoice.prescription is not None:
        rows.append((f"Rx ref: {invoice.prescription.prescription_reference}", ""))
    for left, right in rows:
        pdf.cell(110, 5, _t(left))
        pdf.cell(0, 5, _t(right), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    cols = [("#", 7), ("Medicine", 52), ("Batch", 20), ("Exp", 14), ("Qty", 18), ("MRP", 16),
            ("Rate", 16), ("Disc", 14), ("GST%", 11), ("Amount", 22)]
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(235, 235, 235)
    for name, width in cols:
        pdf.cell(width, 6, name, border=1, fill=True, align="C")
    pdf.ln()
    pdf.set_font("Helvetica", "", 8)
    for idx, it in enumerate(invoice.items, start=1):
        qty = f"{Decimal(str(it.quantity)).normalize():f} {it.unit_name}"
        values = [str(idx), it.medicine_name[:34], it.batch_number[:12], it.expiry_date.strftime("%m/%y"), qty[:14],
                  _m(it.mrp), _m(it.selling_rate), _m(it.discount_amount), f"{it.tax_rate:.0f}", _m(it.line_total)]
        for (name, width), value in zip(cols, values):
            pdf.cell(width, 5.5, _t(value), border=1, align="L" if name in ("Medicine", "Batch") else "R")
        pdf.ln()

    pdf.ln(2)
    totals = [("Gross (incl. GST)", invoice.gross_amount), ("Discount", -invoice.discount_total),
              ("Taxable value", invoice.taxable_total)]
    if invoice.is_inter_state:
        totals.append(("IGST", invoice.igst_total))
    else:
        totals += [("CGST", invoice.cgst_total), ("SGST", invoice.sgst_total)]
    totals += [("Round off", invoice.round_off), ("GRAND TOTAL", invoice.grand_total),
               ("Paid", invoice.amount_paid)]
    if invoice.returned_value:
        totals.append(("Returned", invoice.returned_value))
    totals.append(("Balance due", invoice.balance_due))
    for label, value in totals:
        pdf.set_font("Helvetica", "B" if label in ("GRAND TOTAL", "Balance due") else "", 9)
        pdf.cell(150, 5.5, _t(label), align="R")
        pdf.cell(0, 5.5, _t(f"Rs. {_m(value)}"), align="R", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 8)
    if payments:
        pdf.ln(1)
        pdf.cell(0, 4.5, _t("Payments: " + "; ".join(
            f"{m} Rs. {_m(a)}" + (f" (ref {r})" if r else "") for m, a, r in payments)), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.multi_cell(0, 4.5, _t(footer))
    pdf.cell(0, 4.5, _t("Computer generated invoice. Prices include GST as per MRP."), new_x="LMARGIN", new_y="NEXT")
    filename = f"{invoice.invoice_number.replace('/', '-')}.pdf"
    return filename, bytes(pdf.output())
