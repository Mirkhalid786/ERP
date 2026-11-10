"""Supplier bill import: upload validation, text-PDF reading, OCR of scanned PDFs and images, review checks,
duplicate detection, drafts changing nothing, confirm through GRN + supplier bill, posting/finalising, permissions,
OCR failure fallback and all-or-nothing rollback."""
import io
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.auth import AuditLog
from app.models.inventory import MedicineBatch, StockMovement
from app.models.purchasing import GoodsReceipt, SupplierInvoice
from tests.sales_helpers import add_user, login_as
from tests.test_purchasing import setup_pharmacy_and_catalogue

SUPPLIER_GSTIN = "27AAACC1234D1Z8"          # the catalogue helper's supplier (Cipla Distribution Hub)


def bill_lines(invoice_no="CDH/26/0457", qty=10, free=1, rate="24.15", disc="5.00", gst=12, total=None):
    taxable = (Decimal(qty) * Decimal(rate) * (1 - Decimal(disc) / 100)).quantize(Decimal("0.01"))
    tax = (taxable * gst / 100).quantize(Decimal("0.01"))
    grand = total if total is not None else (taxable + tax).quantize(Decimal("1"))
    round_off = (Decimal(grand) - taxable - tax).quantize(Decimal("0.01"))
    return [
        "CIPLA DISTRIBUTION HUB", f"GSTIN: {SUPPLIER_GSTIN}   DL No: MH-20B-998877", "TAX INVOICE",
        f"Invoice No: {invoice_no}     Invoice Date: 12/10/2026",
        "HSN    Product              Pack  Batch    Exp    Qty Free  MRP     Rate    Disc%  GST%  Amount",
        f"3004   DOLO 650 TABLET      15S   DL2405   08/29  {qty}  {free}     45.00   {rate}   {disc}   {gst}     {taxable}",
        f"Taxable Amount: {taxable}   CGST: {tax / 2:.2f}   SGST: {tax / 2:.2f}", f"Round Off: {round_off}", f"Grand Total: {grand}.00",
    ], taxable, tax, Decimal(grand)


def make_pdf(lines) -> bytes:
    from fpdf import FPDF
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=10)
    for ln in lines:
        pdf.cell(0, 7, ln, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def page_image(pdf: bytes):
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(pdf)
    img = doc[0].render(scale=200 / 72).to_pil().convert("RGB")
    doc.close()
    return img


def as_png(pdf: bytes) -> bytes:
    buf = io.BytesIO()
    page_image(pdf).save(buf, format="PNG")
    return buf.getvalue()


def as_scanned_pdf(pdf: bytes) -> bytes:
    buf = io.BytesIO()
    page_image(pdf).save(buf, format="PDF", resolution=200)        # image only, no text layer
    return buf.getvalue()


async def count(engine, model, *where):
    async with async_sessionmaker(engine)() as s:
        return (await s.execute(select(func.count()).select_from(model).where(*where))).scalar()


async def upload(client, data: bytes, name="bill.pdf", ctype="application/pdf"):
    return await client.post("/api/v1/purchases/bill-imports", files={"file": (name, data, ctype)})


@pytest.mark.asyncio
async def test_text_pdf_is_read_and_matched_without_side_effects(client: AsyncClient, test_engine):
    await setup_pharmacy_and_catalogue(client)
    lines, taxable, tax, grand = bill_lines()
    r = await upload(client, make_pdf(lines))
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["status"] == "EXTRACTED" and b["extraction_method"] == "pdf-text"
    rev = b["review"]
    assert rev["supplier_match"]["confidence"] == "high" and rev["supplier_id"]
    assert rev["invoice_number"] == "CDH/26/0457" and rev["invoice_date"] == "2026-10-12"
    assert Decimal(rev["declared"]["grand_total"]) == grand
    ln = rev["lines"][0]
    assert ln["medicine_id"] and ln["batch_number"] == "DL2405" and ln["expiry_date"] == "2029-08-31"
    assert ln["qty"] == "10" and ln["free_qty"] == "1" and ln["purchase_rate"] == "24.15" and ln["mrp"] == "45.00"
    assert ln["packaging_unit_id"] and ln["tax_rate_id"]                 # strip of 15 and GST 12% found
    assert b["check"]["errors"] == [], b["check"]
    # Uploading and reading changed nothing
    assert await count(test_engine, GoodsReceipt) == 0 and await count(test_engine, SupplierInvoice) == 0
    assert await count(test_engine, MedicineBatch) == 0 and await count(test_engine, StockMovement) == 0
    assert await count(test_engine, AuditLog, AuditLog.action == "purchase.bill_upload") == 1
    f = await client.get(f"/api/v1/purchases/bill-imports/{b['id']}/file")
    assert f.status_code == 200 and f.content[:5] == b"%PDF-" and f.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_review_draft_and_discrepancy_rules(client: AsyncClient, test_engine):
    await setup_pharmacy_and_catalogue(client)
    lines, *_ = bill_lines()
    b = (await upload(client, make_pdf(lines))).json()
    review = b["review"]
    review["declared"]["grand_total"] = "999.00"                           # does not match the lines
    chk = (await client.post(f"/api/v1/purchases/bill-imports/{b['id']}/check", json=review)).json()
    assert any("differs from the bill's total" in e for e in chk["errors"])
    review["acknowledge_discrepancy"], review["discrepancy_notes"] = True, "Supplier added freight on the printed total"
    chk = (await client.post(f"/api/v1/purchases/bill-imports/{b['id']}/check", json=review)).json()
    assert not any("differs" in e for e in chk["errors"]) and any("acknowledged" in w for w in chk["warnings"])
    review["lines"][0]["batch_number"] = ""
    review["lines"][0]["qty"] = "0"
    saved = await client.put(f"/api/v1/purchases/bill-imports/{b['id']}", json=review)
    assert saved.status_code == 200
    errors = saved.json()["check"]["errors"]
    assert any("batch number" in e for e in errors) and any("quantity" in e for e in errors)
    assert await count(test_engine, MedicineBatch) == 0 and await count(test_engine, SupplierInvoice) == 0


@pytest.mark.asyncio
async def test_confirm_creates_drafts_only_then_post_and_finalise(client: AsyncClient, test_engine):
    data = await setup_pharmacy_and_catalogue(client)
    lines, taxable, tax, grand = bill_lines()
    b = (await upload(client, make_pdf(lines))).json()
    r = await client.post(f"/api/v1/purchases/bill-imports/{b['id']}/confirm", json={"review": b["review"]})
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["status"] == "CONFIRMED" and done["grn_status"] == "DRAFT" and done["supplier_invoice_status"] == "DRAFT"
    assert await count(test_engine, MedicineBatch) == 0                      # a draft GRN does not touch stock
    again = await client.post(f"/api/v1/purchases/bill-imports/{b['id']}/confirm", json={"review": b["review"]})
    assert again.status_code == 409

    # A second bill: confirm, post to stock and finalise in one step
    lines2, taxable2, tax2, grand2 = bill_lines(invoice_no="CDH/26/0458", qty=4, free=0, disc="0.00")
    b2 = (await upload(client, make_pdf(lines2))).json()
    r = await client.post(f"/api/v1/purchases/bill-imports/{b2['id']}/confirm",
                          json={"review": b2["review"], "post_grn": True, "finalize": True, "acknowledge_warnings": True})
    assert r.status_code == 200, r.text
    d2 = r.json()
    assert d2["grn_status"] == "POSTED" and d2["supplier_invoice_status"] == "POSTED"
    batch = (await client.get("/api/v1/batches", params={"medicine_id": data["medicine"]["id"]})).json()
    dl = next(x for x in batch if x["batch_number"] == "DL2405")
    assert Decimal(dl["current_stock_base"]) == Decimal("60") and dl["expiry_date"] == "2029-08-31"    # 4 strips x 15
    inv = (await client.get(f"/api/v1/purchases/invoices/{d2['supplier_invoice_id']}")).json()
    assert Decimal(inv["calculated_grand_total"]) == grand2 or abs(Decimal(inv["calculated_grand_total"]) - grand2) <= 1
    assert await count(test_engine, AuditLog, AuditLog.action == "purchase.bill_confirm") == 2


@pytest.mark.asyncio
async def test_duplicates_are_flagged_and_recorded_bills_blocked(client: AsyncClient):
    await setup_pharmacy_and_catalogue(client)
    pdf = make_pdf(bill_lines()[0])
    first = (await upload(client, pdf)).json()
    await client.post(f"/api/v1/purchases/bill-imports/{first['id']}/confirm", json={"review": first["review"]})
    second = (await upload(client, pdf)).json()
    kinds = {d["kind"] for d in second["duplicates"]}
    assert {"same_file", "invoice_recorded"} <= kinds
    assert any("already recorded" in e for e in second["check"]["errors"])
    r = await client.post(f"/api/v1/purchases/bill-imports/{second['id']}/confirm", json={"review": second["review"]})
    assert r.status_code == 422
    assert (await client.post(f"/api/v1/purchases/bill-imports/{second['id']}/discard")).status_code == 204


@pytest.mark.asyncio
async def test_failed_finalise_rolls_everything_back(client: AsyncClient, test_engine):
    await setup_pharmacy_and_catalogue(client)
    lines, *_ = bill_lines(total=Decimal("400"))                            # printed total far from the lines
    b = (await upload(client, make_pdf(lines))).json()
    review = b["review"]
    review["acknowledge_discrepancy"], review["discrepancy_notes"] = True, "Checking with supplier"
    r = await client.post(f"/api/v1/purchases/bill-imports/{b['id']}/confirm",
                          json={"review": review, "post_grn": True, "finalize": True, "acknowledge_warnings": False})
    assert r.status_code == 409, r.text                                      # finalise refuses unacknowledged warnings
    assert await count(test_engine, GoodsReceipt) == 0 and await count(test_engine, SupplierInvoice) == 0
    assert await count(test_engine, MedicineBatch) == 0 and await count(test_engine, StockMovement) == 0
    assert (await client.get(f"/api/v1/purchases/bill-imports/{b['id']}")).json()["status"] == "EXTRACTED"


@pytest.mark.asyncio
async def test_scanned_pdf_and_photo_are_read_by_ocr(client: AsyncClient):
    await setup_pharmacy_and_catalogue(client)
    pdf = make_pdf(bill_lines()[0])
    for data, name, ctype in ((as_scanned_pdf(pdf), "scan.pdf", "application/pdf"), (as_png(pdf), "photo.png", "image/png")):
        r = await upload(client, data, name, ctype)
        assert r.status_code == 201, r.text
        b = r.json()
        assert b["extraction_method"] == "ocr-local", b["extraction_method"]
        rev = b["review"]
        assert rev["supplier_match"]["confidence"] == "high"
        assert rev["invoice_number"] == "CDH/26/0457"
        ln = rev["lines"][0]
        assert ln["batch_number"] == "DL2405" and ln["qty"] == "10" and ln["purchase_rate"] == "24.15"
        await client.post(f"/api/v1/purchases/bill-imports/{b['id']}/discard")


@pytest.mark.asyncio
@pytest.mark.parametrize("name,data,status", [
    ("bill.exe", b"MZ\x90\x00 executable", 415),
    ("bill.pdf", b"<html><script>alert(1)</script></html>", 415),
    ("bill.pdf", b"", 422),
    ("bill.pdf", b"%PDF-1.7 this is not really a pdf", 422),
    ("bill.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * (10 * 1024 * 1024), 413),
], ids=["exe", "html", "empty", "broken-pdf", "too-large"])
async def test_invalid_uploads(client: AsyncClient, test_engine, name, data, status):
    await setup_pharmacy_and_catalogue(client)
    r = await upload(client, data, name, "application/octet-stream")
    assert r.status_code == status, r.text


@pytest.mark.asyncio
async def test_reading_failure_falls_back_to_manual_entry(client: AsyncClient, monkeypatch):
    from app.services import bill_extract
    data = await setup_pharmacy_and_catalogue(client)

    def broken(*a, **k):
        raise RuntimeError("OCR engine crashed")
    monkeypatch.setattr(bill_extract, "extract_document", broken)
    b = (await upload(client, make_pdf(bill_lines()[0]))).json()
    assert b["status"] == "FAILED" and "failed" in b["extraction_error"]
    review = b["review"]
    units = {u["name"]: u for u in (await client.get("/api/v1/units")).json()}
    taxes = {t["name"]: t for t in (await client.get("/api/v1/tax-rates")).json()}
    review.update({"supplier_id": data["supplier"]["id"], "invoice_number": "MANUAL-1", "invoice_date": "2026-10-12",
                   "declared": {"grand_total": "117.00", "round_off": "0.52"},       # 5 x 20.80 = 104.00 + 12% = 116.48
                   "lines": [{"medicine_id": data["medicine"]["id"], "packaging_unit_id": units["Strip"]["id"], "batch_number": "MAN1",
                              "expiry_date": "2029-01-31", "qty": "5", "free_qty": "0", "purchase_rate": "20.80", "discount_percent": "0",
                              "mrp": "30", "selling_rate": "30", "tax_rate_id": taxes["GST 12%"]["id"]}]})
    r = await client.post(f"/api/v1/purchases/bill-imports/{b['id']}/confirm", json={"review": review})
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_permissions(client: AsyncClient):
    await setup_pharmacy_and_catalogue(client)
    b = (await upload(client, make_pdf(bill_lines()[0]))).json()
    await add_user(client, "Pharmacist", "pharm1")            # can view purchases, cannot create them
    await client.post("/api/v1/auth/logout")
    await login_as(client, "pharm1", "Staff1234")
    assert (await upload(client, make_pdf(bill_lines(invoice_no="X1")[0]))).status_code == 403
    assert (await client.post(f"/api/v1/purchases/bill-imports/{b['id']}/confirm", json={"review": b["review"]})).status_code == 403
    assert (await client.get(f"/api/v1/purchases/bill-imports/{b['id']}/file")).status_code == 200
    await client.post("/api/v1/auth/logout")
    assert (await client.get(f"/api/v1/purchases/bill-imports/{b['id']}/file")).status_code == 401
