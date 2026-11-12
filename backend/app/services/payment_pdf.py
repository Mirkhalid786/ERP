"""
A4 PDF rendering for payment documents (fpdf2), built from the same document
dicts as the print pages (`payment_docs`), so screen, print and PDF never
disagree. Built-in fonts cover Latin-1 only: amounts use "Rs.".
"""
from datetime import date, datetime
from decimal import Decimal

from fpdf import FPDF
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import payment_docs
from app.services.sales_common import BUSINESS_TZ


def _t(value) -> str:
    text = "" if value is None else str(value)
    return text.replace("₹", "Rs.").encode("latin-1", "replace").decode("latin-1")


def _m(value) -> str:
    return f"{Decimal(str(value)):,.2f}"


def _when(value) -> str:
    if isinstance(value, datetime):
        return value.astimezone(BUSINESS_TZ).strftime("%d-%m-%Y %I:%M %p")
    if isinstance(value, date):
        return value.strftime("%d-%m-%Y")
    return "-"


def _header(pdf: FPDF, doc: dict, title: str) -> None:
    ph = doc["pharmacy"]
    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(0, 8, _t(ph["name"]), new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.set_font("Helvetica", "", 9)
    address = ", ".join(x for x in [ph.get("address_line1"), ph.get("city"), ph.get("pincode")] if x)
    for line in [address, f"Phone: {ph['phone']}" if ph.get("phone") else "",
                 f"GSTIN: {ph['gstin']}" if ph.get("gstin") else "",
                 f"Drug Licence: {ph['drug_license_number']}" if ph.get("drug_license_number") else ""]:
        if line:
            pdf.cell(0, 4.5, _t(line), new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 12)
    label = title + (" — REVERSED" if doc["is_reversed"] else "")
    pdf.cell(0, 7, _t(label), new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(1)


def _rows(pdf: FPDF, rows) -> None:
    pdf.set_font("Helvetica", "", 9)
    for left, right in rows:
        pdf.cell(110, 5, _t(left))
        pdf.cell(0, 5, _t(right), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)


def _alloc_table(pdf: FPDF, allocations, number_key: str, date_key: str = None) -> None:
    cols = [("#", 10), ("Document", 60), ("Date", 30), ("Amount Rs.", 40)]
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(235, 235, 235)
    for name, width in cols:
        pdf.cell(width, 6, name, border=1, fill=True, align="C")
    pdf.ln()
    pdf.set_font("Helvetica", "", 8)
    if not allocations:
        pdf.cell(140, 5.5, _t("No invoice allocation — amount held as advance"), border=1)
        pdf.ln()
    for idx, a in enumerate(allocations, start=1):
        values = [str(idx), str(a.get(number_key) or "-"),
                  _when(a.get(date_key)) if date_key else "-", _m(a["amount"])]
        for (name, width), value in zip(cols, values):
            pdf.cell(width, 5.5, _t(value), border=1, align="L" if name == "Document" else "R")
        pdf.ln()
    pdf.ln(2)


def _totals(pdf: FPDF, rows) -> None:
    for label, value in rows:
        pdf.set_font("Helvetica", "B" if label.isupper() else "", 9)
        pdf.cell(110, 5.5, _t(label))
        pdf.cell(30, 5.5, _t(_m(value)), align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(0, 5, _t("Computer-generated document. This is not a tax invoice."), align="C")


def _new_pdf() -> FPDF:
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    return pdf


async def customer_receipt_pdf_bytes(db: AsyncSession, pharmacy_id: int, payment_id: int) -> tuple:
    doc = await payment_docs.customer_receipt_document(db, pharmacy_id, payment_id)
    pdf = _new_pdf()
    _header(pdf, doc, "PAYMENT RECEIPT")
    cust = doc["customer"] or {}
    _rows(pdf, [
        (f"Receipt No: {doc['receipt_number']}", f"Date: {_when(doc['received_at'])}"),
        (f"Customer: {cust.get('name') or 'Walk-in'}", f"Mobile: {cust.get('mobile') or '-'}"),
        (f"Method: {doc['method']}", f"Reference: {doc['reference'] or '-'}"),
        (f"Received by: {doc['received_by'] or '-'}", f"Status: {doc['status']}"),
    ])
    _alloc_table(pdf, doc["allocations"], "invoice_number", "invoice_date")
    totals = [("Allocated to invoices", doc["allocated_total"]), ("AMOUNT RECEIVED", doc["amount"])]
    if doc["amount_tendered"]:
        totals.append(("Tendered", doc["amount_tendered"]))
    if Decimal(str(doc["change_returned"])) > 0:
        totals.append(("Change returned", doc["change_returned"]))
    if Decimal(str(doc["advance_unapplied"])) > 0:
        totals.append(("Advance (unapplied)", doc["advance_unapplied"]))
    if doc["balance_after"] is not None:
        totals.append(("BALANCE OUTSTANDING AFTER THIS PAYMENT", doc["balance_after"]))
    _totals(pdf, totals)
    return bytes(pdf.output()), f"receipt-{doc['receipt_number'].replace('/', '-')}.pdf"


async def supplier_voucher_pdf_bytes(db: AsyncSession, pharmacy_id: int, payment_id: int) -> tuple:
    doc = await payment_docs.supplier_voucher_document(db, pharmacy_id, payment_id)
    pdf = _new_pdf()
    _header(pdf, doc, "PAYMENT VOUCHER")
    sup = doc["supplier"] or {}
    _rows(pdf, [
        (f"Voucher No: {doc['payment_number']}", f"Date: {_when(doc['payment_date'])}"),
        (f"Supplier: {sup.get('name') or '-'}", f"GSTIN: {sup.get('gstin') or '-'}"),
        (f"Method: {doc['method']}", f"Reference: {doc['reference'] or '-'}"),
        (f"Recorded by: {doc['recorded_by'] or '-'}", f"Status: {doc['status']}"),
    ])
    _alloc_table(pdf, doc["allocations"], "invoice_number")
    totals = [("Allocated to bills", doc["allocated_total"]), ("AMOUNT PAID", doc["amount"])]
    if Decimal(str(doc["advance_unallocated"])) > 0:
        totals.append(("Advance (unallocated)", doc["advance_unallocated"]))
    totals.append(("PAYABLE OUTSTANDING AFTER THIS PAYMENT", doc["payable_after"]))
    _totals(pdf, totals)
    if doc["is_reversed"]:
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(0, 5, _t(f"Reversed: {doc['reversal_reason'] or '-'}"), new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output()), f"voucher-{doc['payment_number'].replace('/', '-')}.pdf"
