"""
Supplier bill import: upload → automatic reading → mandatory human review → confirm through the normal purchasing
workflow (goods receipt, then supplier bill).

Uploading, reading and saving a review draft never change stock, payables or the books. Confirming creates a goods
receipt draft and a supplier bill draft with the existing services; posting the goods receipt (stock in) and
finalising the bill (payable + automatic accounting) are separate, permission-checked options of the same confirm,
and everything happens in one database transaction. The original document is kept and linked to what it created.
"""
import asyncio
import difflib
import hashlib
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import today_local
from app.models.auth import AuditLog, Pharmacy, User
from app.models.bill_import import PurchaseBillImport
from app.models.inventory import Medicine, MedicineUnitConversion, TaxRate, Unit
from app.models.purchasing import Supplier, SupplierInvoice
from app.services import bill_extract, bill_parse
from app.services.catalogue_import import FORM_BASE_UNIT, norm_key

MAX_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 50
FORM_STOPWORDS = {"tab", "tabs", "tablet", "tablets", "cap", "caps", "capsule", "capsules", "syp", "syr", "syrup", "susp",
                  "suspension", "inj", "injection", "drop", "drops", "cream", "oint", "ointment", "gel", "lotion", "strip",
                  "strips", "bottle", "vial", "amp", "ampoule", "sachet", "powder", "spray", "and", "with", "plus"}
TYPES = {"application/pdf": "PDF", "image/png": "PNG image", "image/jpeg": "JPEG image", "image/webp": "WebP image"}
ZERO = Decimal("0")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sniff(data: bytes) -> Optional[str]:
    """Identify the document from its bytes (never from the file name or browser-supplied type)."""
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _pdf_pages(data: bytes) -> int:
    import pypdfium2 as pdfium
    try:
        doc = pdfium.PdfDocument(data)
    except Exception:
        raise HTTPException(status_code=422, detail="The PDF could not be opened (it may be damaged or password-protected)")
    try:
        return len(doc)
    finally:
        doc.close()


def D(value: Any, default: Optional[Decimal] = None) -> Optional[Decimal]:
    if value in (None, ""):
        return default
    try:
        return Decimal(str(value).replace(",", ""))
    except InvalidOperation:
        return default


# ── Matching ───────────────────────────────────────────────────────────────────

async def match_supplier(db: AsyncSession, pid: int, name: Optional[str], gstin: Optional[str]) -> Dict[str, Any]:
    if gstin:
        s = (await db.execute(select(Supplier).where(Supplier.pharmacy_id == pid, Supplier.gstin == gstin))).scalars().first()
        if s:
            return {"supplier_id": s.id, "name": s.name, "confidence": "high", "reason": "GSTIN matches", "candidates": []}
    candidates = []
    if name:
        words = [w for w in re.findall(r"[A-Za-z]{3,}", name)][:2]
        stmt = select(Supplier).where(Supplier.pharmacy_id == pid)
        if words:
            stmt = stmt.where(or_(*[Supplier.name.ilike(f"%{w}%") for w in words]))
        for s in (await db.execute(stmt.limit(20))).scalars():
            score = difflib.SequenceMatcher(None, norm_key(name), norm_key(s.name)).ratio()
            candidates.append({"supplier_id": s.id, "name": s.name, "gstin": s.gstin, "score": round(score, 2)})
        candidates.sort(key=lambda c: -c["score"])
    best = candidates[0] if candidates and candidates[0]["score"] >= 0.9 else None
    return {"supplier_id": best["supplier_id"] if best else None, "name": best["name"] if best else None,
            "confidence": "medium" if best else "low", "reason": "Name is similar" if best else "No matching supplier",
            "candidates": candidates[:5]}


async def match_medicine(db: AsyncSession, pid: int, description: Optional[str]) -> Dict[str, Any]:
    if not description:
        return {"medicine_id": None, "confidence": "low", "candidates": []}
    # Search on the distinctive words only: "DOLO 650 TABLET" must not pull in every tablet
    words = [w for w in re.findall(r"[A-Za-z]{3,}", description) if w.lower() not in FORM_STOPWORDS][:2]
    if not words:
        return {"medicine_id": None, "confidence": "low", "candidates": []}
    stmt = select(Medicine).where(Medicine.pharmacy_id == pid, Medicine.is_active == True)  # noqa: E712
    if words:
        stmt = stmt.where(or_(*[or_(Medicine.name.ilike(f"%{w}%"), Medicine.brand_name.ilike(f"%{w}%"),
                                    Medicine.generic_name.ilike(f"%{w}%")) for w in words]))
    key = norm_key(description)
    nums = set(re.findall(r"\d+(?:\.\d+)?", description))
    out = []
    for m in (await db.execute(stmt.limit(60))).scalars():
        label = " ".join(x for x in (m.name, m.strength or "") if x)
        score = difflib.SequenceMatcher(None, key, norm_key(m.name)).ratio()
        m_nums = set(re.findall(r"\d+(?:\.\d+)?", label))
        if nums and m_nums and not (nums & m_nums):
            score -= 0.25                                    # "Dolo 650" must not match "Dolo 500"
        out.append({"medicine_id": m.id, "name": m.name, "strength": m.strength, "dosage_form": m.dosage_form,
                    "base_unit_id": m.base_unit_id, "score": round(max(score, 0), 2)})
    out.sort(key=lambda c: -c["score"])
    best = out[0] if out and out[0]["score"] >= 0.8 else None
    return {"medicine_id": best["medicine_id"] if best else None,
            "confidence": ("high" if best and best["score"] >= 0.95 else "medium") if best else "low",
            "candidates": out[:5]}


async def _units(db: AsyncSession, pid: int) -> Dict[str, Unit]:
    out = {}
    for u in (await db.execute(select(Unit).where(Unit.pharmacy_id == pid, Unit.is_active == True))).scalars():  # noqa: E712
        out[u.name.lower()] = u
        out[u.abbreviation.lower()] = u
    return out


async def _tax_by_rate(db: AsyncSession, pid: int) -> Dict[Decimal, int]:
    out: Dict[Decimal, int] = {}
    for t in (await db.execute(select(TaxRate).where(TaxRate.pharmacy_id == pid, TaxRate.is_active == True))).scalars():  # noqa: E712
        if t.rate_percentage not in out or t.name.upper().startswith("GST"):
            out[t.rate_percentage] = t.id
    return out


async def suggest_review(db: AsyncSession, pid: int, parsed: Dict[str, Any]) -> Dict[str, Any]:
    """First review draft from what was read: matched supplier/medicines and units; unknown values stay blank."""
    sup, inv, tot = parsed["supplier"], parsed["invoice"], parsed["totals"]
    supplier = await match_supplier(db, pid, sup["name"]["value"], sup["gstin"]["value"])
    tax = await _tax_by_rate(db, pid)
    units = await _units(db, pid)
    lines = []
    for idx, it in enumerate(parsed["items"]):
        desc = it["description"]["value"]
        med = await match_medicine(db, pid, desc)
        pack_size = it["pack_size"]["value"]
        unit_id, new_pack = None, None
        if med["medicine_id"]:
            m = await db.get(Medicine, med["medicine_id"])
            convs = (await db.execute(select(MedicineUnitConversion).where(MedicineUnitConversion.medicine_id == m.id))).scalars().all()
            hit = next((c for c in convs if pack_size and c.conversion_factor == Decimal(pack_size)), None)
            if hit:
                unit_id = hit.packaging_unit_id
            elif pack_size and pack_size > 1:
                pack_unit = units.get("strip" if (m.dosage_form or "").lower() in ("tablet", "capsule") else "box")
                new_pack = {"unit_id": pack_unit.id if pack_unit else None, "size": str(pack_size)}
            else:
                unit_id = m.base_unit_id
        gst = D(it["gst"]["value"])
        mrp = it["mrp"]["value"]
        lines.append({
            "source_index": idx, "description": desc, "medicine_id": med["medicine_id"], "medicine_match": med,
            "new_medicine": None, "packaging_unit_id": unit_id, "new_pack": new_pack, "pack_size_on_bill": pack_size,
            "hsn": it["hsn"]["value"], "batch_number": it["batch"]["value"], "expiry_date": it["expiry"]["value"],
            "qty": it["qty"]["value"], "free_qty": it["free"]["value"] or "0", "purchase_rate": it["rate"]["value"],
            "discount_percent": it["discount"]["value"] or "0", "mrp": mrp, "selling_rate": mrp,
            "gst_percent": it["gst"]["value"], "tax_rate_id": tax.get(gst.quantize(Decimal("0.01"))) if gst is not None else None,
            "amount_on_bill": it["amount"]["value"],
            "confidence": {k: it[k]["confidence"] for k in ("description", "batch", "expiry", "qty", "free", "rate", "discount", "mrp", "gst", "amount")},
            "issues": it["issues"],
        })
    return {
        "supplier_id": supplier["supplier_id"], "supplier_match": supplier,
        "new_supplier": None if supplier["supplier_id"] else (
            {"name": sup["name"]["value"] or "", "gstin": sup["gstin"]["value"] or "", "phone": ""} if sup["name"]["value"] else None),
        "invoice_number": inv["number"]["value"], "invoice_date": inv["date"]["value"], "due_date": inv["due_date"]["value"],
        "received_date": today_local().isoformat(), "po_reference": inv["po_reference"]["value"],
        "declared": {k: tot[k]["value"] for k in ("taxable", "cgst", "sgst", "igst", "round_off", "grand_total")},
        "lines": lines, "discrepancy_notes": "", "acknowledge_discrepancy": False,
    }


# ── Upload / read ──────────────────────────────────────────────────────────────

def _audit(db, user: User, action: str, rec: PurchaseBillImport, meta: Dict[str, Any]):
    db.add(AuditLog(pharmacy_id=user.pharmacy_id, user_id=user.id, action=action, entity_type="purchase_bill_import",
                    entity_id=str(rec.id), metadata_json=json.dumps(meta, default=str)[:20000]))


async def upload(db: AsyncSession, user: User, filename: str, data: bytes) -> PurchaseBillImport:
    if not data:
        raise HTTPException(status_code=422, detail="The file is empty")
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="The file is larger than 10 MB")
    ctype = sniff(data)
    if ctype is None:
        raise HTTPException(status_code=415, detail="Upload a PDF, JPG/JPEG, PNG or WebP file (phone photos: set the camera to JPEG)")
    pages = _pdf_pages(data) if ctype == "application/pdf" else 1
    if pages > MAX_PDF_PAGES:
        raise HTTPException(status_code=422, detail=f"The PDF has {pages} pages; upload one bill (up to {MAX_PDF_PAGES} pages) at a time")
    digest = hashlib.sha256(data).hexdigest()
    rec = PurchaseBillImport(pharmacy_id=user.pharmacy_id, status="EXTRACTED", file_name=(filename or "bill")[:255],
                             content_type=ctype, size_bytes=len(data), sha256=digest, page_count=pages, data=data,
                             uploaded_by=user.id, uploaded_at=utcnow())
    pharmacy = (await db.execute(select(Pharmacy).where(Pharmacy.id == user.pharmacy_id))).scalar_one()
    try:
        extracted = await asyncio.to_thread(bill_extract.extract_document, data, ctype)
        parsed = bill_parse.parse_bill(extracted.lines, own_gstin=pharmacy.gstin)
        parsed["extraction"] = {"method": extracted.method, "pages": extracted.pages, "warnings": extracted.warnings,
                                "confidence": extracted.confidence, "lines": extracted.lines[:1500]}
        rec.extraction_method = extracted.method
        rec.extraction_json = json.dumps(parsed, default=str)
        review = await suggest_review(db, user.pharmacy_id, parsed)
        if not extracted.lines:
            rec.extraction_error = "; ".join(extracted.warnings) or "No text could be read"
    except HTTPException:
        raise
    except Exception as exc:                      # A reading failure must not lose the upload: fall back to manual entry
        rec.status, rec.extraction_method = "FAILED", "none"
        rec.extraction_error = f"Automatic reading failed: {type(exc).__name__}"
        rec.extraction_json = json.dumps({"supplier": {}, "invoice": {}, "totals": {}, "items": [], "warnings": [rec.extraction_error]})
        review = {"supplier_id": None, "supplier_match": None, "new_supplier": None, "invoice_number": None, "invoice_date": None,
                  "due_date": None, "received_date": today_local().isoformat(), "po_reference": None,
                  "declared": {}, "lines": [], "discrepancy_notes": "", "acknowledge_discrepancy": False}
    rec.review_json = json.dumps(review, default=str)
    rec.supplier_id = review.get("supplier_id")
    rec.invoice_number = (review.get("invoice_number") or None)
    rec.invoice_date = date.fromisoformat(review["invoice_date"]) if review.get("invoice_date") else None
    rec.grand_total = D((review.get("declared") or {}).get("grand_total"))
    db.add(rec)
    await db.flush()
    _audit(db, user, "purchase.bill_upload", rec, {"file": rec.file_name, "type": ctype, "bytes": len(data), "sha256": digest,
                                                   "method": rec.extraction_method, "status": rec.status})
    await db.flush()
    return rec


async def get(db: AsyncSession, pid: int, import_id: int, lock: bool = False) -> PurchaseBillImport:
    stmt = select(PurchaseBillImport).where(PurchaseBillImport.id == import_id, PurchaseBillImport.pharmacy_id == pid)
    if lock:
        stmt = stmt.with_for_update()
    rec = (await db.execute(stmt)).scalar_one_or_none()
    if rec is None:
        raise HTTPException(status_code=404, detail="Bill import not found")
    return rec


async def duplicates(db: AsyncSession, rec: PurchaseBillImport, review: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    same_file = (await db.execute(select(PurchaseBillImport).where(
        PurchaseBillImport.pharmacy_id == rec.pharmacy_id, PurchaseBillImport.sha256 == rec.sha256,
        PurchaseBillImport.id != rec.id, PurchaseBillImport.status != "DISCARDED"))).scalars().all()
    for o in same_file:
        out.append({"kind": "same_file", "import_id": o.id, "status": o.status,
                    "message": f"The same file was uploaded before (import #{o.id}, {o.status.lower()})"})
    if review.get("supplier_id") and review.get("invoice_number"):
        from app.services.purchasing import invoice_number_key
        try:
            inv_date = date.fromisoformat(review["invoice_date"]) if review.get("invoice_date") else today_local()
        except ValueError:
            inv_date = today_local()
        key = invoice_number_key(review["invoice_number"].strip(), inv_date)
        clash = (await db.execute(select(SupplierInvoice).where(
            SupplierInvoice.pharmacy_id == rec.pharmacy_id, SupplierInvoice.supplier_id == review["supplier_id"],
            SupplierInvoice.number_key == key))).scalars().first()
        if clash:
            out.append({"kind": "invoice_recorded", "supplier_invoice_id": clash.id, "status": clash.status,
                        "message": f"Bill {clash.invoice_number} dated {clash.invoice_date} is already recorded for this supplier "
                                   f"({clash.status.lower()})"})
        others = (await db.execute(select(PurchaseBillImport).where(
            PurchaseBillImport.pharmacy_id == rec.pharmacy_id, PurchaseBillImport.id != rec.id,
            PurchaseBillImport.supplier_id == review["supplier_id"], PurchaseBillImport.invoice_number == review["invoice_number"].strip(),
            PurchaseBillImport.status.in_(["EXTRACTED", "FAILED", "CONFIRMED"])))).scalars().all()
        for o in others:
            out.append({"kind": "same_invoice_import", "import_id": o.id, "status": o.status,
                        "message": f"Another import (#{o.id}, {o.status.lower()}) has the same supplier and bill number"})
    return out


def serialize(rec: PurchaseBillImport, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out = {
        "id": rec.id, "status": rec.status, "file_name": rec.file_name, "content_type": rec.content_type,
        "size_bytes": rec.size_bytes, "page_count": rec.page_count, "extraction_method": rec.extraction_method,
        "extraction_error": rec.extraction_error, "supplier_id": rec.supplier_id, "invoice_number": rec.invoice_number,
        "invoice_date": rec.invoice_date, "grand_total": rec.grand_total, "grn_id": rec.grn_id,
        "supplier_invoice_id": rec.supplier_invoice_id, "uploaded_by": rec.uploaded_by, "uploaded_at": rec.uploaded_at,
        "reviewed_by": rec.reviewed_by, "reviewed_at": rec.reviewed_at, "confirmed_by": rec.confirmed_by,
        "confirmed_at": rec.confirmed_at,
    }
    if extra:
        out.update(extra)
    return out


async def detail(db: AsyncSession, pid: int, import_id: int) -> Dict[str, Any]:
    rec = await get(db, pid, import_id)
    review = json.loads(rec.review_json or "{}")
    return serialize(rec, {"extraction": json.loads(rec.extraction_json or "{}"), "review": review,
                           "duplicates": await duplicates(db, rec, review), "check": await check(db, rec, review)})


# ── Review validation ──────────────────────────────────────────────────────────

def _line_totals(line: Dict[str, Any], gst_by_tax: Dict[int, Decimal]) -> Dict[str, Decimal]:
    qty, rate, disc = D(line.get("qty"), ZERO), D(line.get("purchase_rate"), ZERO), D(line.get("discount_percent"), ZERO)
    taxable = (qty * rate * (1 - disc / 100)).quantize(Decimal("0.01"))
    pct = gst_by_tax.get(line.get("tax_rate_id"), ZERO)
    tax = (taxable * pct / 100).quantize(Decimal("0.01"))
    return {"taxable": taxable, "tax": tax, "total": taxable + tax}


async def check(db: AsyncSession, rec: PurchaseBillImport, review: Dict[str, Any]) -> Dict[str, Any]:
    """Errors block confirmation; warnings are shown. Totals are recalculated and compared with the bill."""
    pid = rec.pharmacy_id
    errors: List[str] = []
    warnings: List[str] = []
    if rec.status in ("CONFIRMED", "DISCARDED"):
        errors.append(f"This import is already {rec.status.lower()}")
    if not review.get("supplier_id") and not (review.get("new_supplier") or {}).get("name"):
        errors.append("Choose the supplier, or enter a name to create a new one")
    if review.get("supplier_id"):
        s = await db.get(Supplier, review["supplier_id"])
        if s is None or s.pharmacy_id != pid:
            errors.append("The chosen supplier does not exist")
    if not (review.get("invoice_number") or "").strip():
        errors.append("Enter the supplier's bill number")
    inv_date = None
    try:
        inv_date = date.fromisoformat(review["invoice_date"]) if review.get("invoice_date") else None
    except ValueError:
        errors.append("The bill date is not a valid date")
    if inv_date is None:
        errors.append("Enter the bill date")
    elif inv_date > today_local():
        errors.append("The bill date is in the future")
    tax_rows = (await db.execute(select(TaxRate.id, TaxRate.rate_percentage).where(TaxRate.pharmacy_id == pid))).all()
    gst_by_tax = {i: r for i, r in tax_rows}
    lines = review.get("lines") or []
    if not lines:
        errors.append("Add at least one line")
    taxable = tax = ZERO
    for n, ln in enumerate(lines, start=1):
        tag = f"Line {n}"
        if not ln.get("medicine_id") and not (ln.get("new_medicine") or {}).get("name"):
            errors.append(f"{tag}: match a medicine or create a new one")
        if not ln.get("packaging_unit_id") and not ((ln.get("new_pack") or {}).get("unit_id") and D((ln.get("new_pack") or {}).get("size"))):
            errors.append(f"{tag}: choose the purchase unit")
        if not (ln.get("batch_number") or "").strip():
            errors.append(f"{tag}: enter the batch number")
        try:
            exp = date.fromisoformat(ln["expiry_date"]) if ln.get("expiry_date") else None
        except ValueError:
            exp = None
        if exp is None:
            errors.append(f"{tag}: enter a valid expiry date")
        elif exp <= today_local():
            errors.append(f"{tag}: the batch has already expired ({exp})")
        for key, label, minimum in (("qty", "quantity", "pos"), ("purchase_rate", "rate", "nonneg"), ("mrp", "MRP", "pos"),
                                    ("selling_rate", "selling rate", "pos"), ("free_qty", "free quantity", "nonneg"),
                                    ("discount_percent", "discount %", "nonneg")):
            val = D(ln.get(key))
            if val is None and key in ("free_qty", "discount_percent"):
                continue
            if val is None or (minimum == "pos" and val <= 0) or val < 0:
                errors.append(f"{tag}: {label} must be {'more than 0' if minimum == 'pos' else '0 or more'}")
        if D(ln.get("discount_percent"), ZERO) > 100:
            errors.append(f"{tag}: discount cannot exceed 100%")
        if ln.get("tax_rate_id") is None:
            errors.append(f"{tag}: choose the GST rate")
        elif ln["tax_rate_id"] not in gst_by_tax:
            errors.append(f"{tag}: unknown GST rate")
        bill_pack = D(ln.get("pack_size_on_bill"))
        if ln.get("medicine_id") and ln.get("packaging_unit_id") and bill_pack and bill_pack > 1:
            m = await db.get(Medicine, ln["medicine_id"])
            if m is not None and m.base_unit_id == ln["packaging_unit_id"]:
                warnings.append(f"{tag}: the bill shows packs of {bill_pack}, but the quantity will be counted in single units — "
                                "choose (or add) the pack unit if the quantity is in packs")
        if D(ln.get("mrp")) and D(ln.get("purchase_rate")) and D(ln["purchase_rate"]) > D(ln["mrp"]):
            warnings.append(f"{tag}: the rate is higher than the MRP")
        t = _line_totals(ln, gst_by_tax)
        taxable += t["taxable"]
        tax += t["tax"]
        on_bill = D(ln.get("amount_on_bill"))
        if on_bill is not None and abs(on_bill - t["taxable"]) > Decimal("1") and abs(on_bill - t["total"]) > Decimal("1"):
            warnings.append(f"{tag}: calculated ₹{t['taxable']} (₹{t['total']} with GST) differs from ₹{on_bill} on the bill")
    round_off = D((review.get("declared") or {}).get("round_off"), ZERO)
    calculated = (taxable + tax + round_off).quantize(Decimal("0.01"))
    declared = D((review.get("declared") or {}).get("grand_total"))
    difference = (calculated - declared).quantize(Decimal("0.01")) if declared is not None else None
    if declared is None:
        errors.append("Enter the bill's grand total (as printed) so it can be compared")
    elif abs(difference) > Decimal("1"):
        msg = f"Calculated total ₹{calculated} differs from the bill's total ₹{declared} by ₹{difference}"
        if review.get("acknowledge_discrepancy") and (review.get("discrepancy_notes") or "").strip():
            warnings.append(msg + " (acknowledged)")
        else:
            errors.append(msg + ". Correct the lines, or tick 'acknowledge' and explain the difference")
    dups = await duplicates(db, rec, review)
    for d in dups:
        if d["kind"] == "invoice_recorded":
            errors.append(d["message"])
        else:
            warnings.append(d["message"])
    return {"errors": errors, "warnings": warnings, "duplicates": dups,
            "totals": {"taxable": str(taxable), "tax": str(tax), "round_off": str(round_off), "calculated": str(calculated),
                       "declared": str(declared) if declared is not None else None,
                       "difference": str(difference) if difference is not None else None}}


async def save_draft(db: AsyncSession, user: User, import_id: int, review: Dict[str, Any]) -> Dict[str, Any]:
    rec = await get(db, user.pharmacy_id, import_id, lock=True)
    if rec.status in ("CONFIRMED", "DISCARDED"):
        raise HTTPException(status_code=409, detail=f"This import is already {rec.status.lower()}")
    if not isinstance(review, dict) or not isinstance(review.get("lines", []), list) or len(review.get("lines", [])) > 500:
        raise HTTPException(status_code=422, detail="The review data is not valid")
    rec.review_json = json.dumps(review, default=str)[:4_000_000]
    rec.supplier_id = review.get("supplier_id") if isinstance(review.get("supplier_id"), int) else None
    rec.invoice_number = (str(review.get("invoice_number") or "").strip() or None)
    try:
        rec.invoice_date = date.fromisoformat(review["invoice_date"]) if review.get("invoice_date") else None
    except ValueError:
        rec.invoice_date = None
    rec.grand_total = D((review.get("declared") or {}).get("grand_total"))
    rec.reviewed_by, rec.reviewed_at = user.id, utcnow()
    await db.flush()
    return await detail(db, user.pharmacy_id, import_id)


async def discard(db: AsyncSession, user: User, import_id: int) -> None:
    rec = await get(db, user.pharmacy_id, import_id, lock=True)
    if rec.status == "CONFIRMED":
        raise HTTPException(status_code=409, detail="A confirmed import cannot be discarded; cancel the supplier bill instead")
    rec.status = "DISCARDED"
    _audit(db, user, "purchase.bill_discard", rec, {})
    await db.flush()


# ── Confirm ────────────────────────────────────────────────────────────────────

def _require(perms: set, code: str, what: str):
    if code not in perms:
        raise HTTPException(status_code=403, detail=f"Permission '{code}' is required to {what}")


async def confirm(db: AsyncSession, user: User, perms: set, import_id: int, review: Dict[str, Any],
                  post_grn: bool, finalize: bool, acknowledge_warnings: bool) -> Dict[str, Any]:
    from app.schemas.purchasing import (GoodsReceiptCreate, GoodsReceiptItemCreate, SupplierCreate,
                                        SupplierInvoiceCreate, SupplierInvoiceItemCreate)
    from app.services import supplier_invoices
    from app.services.purchasing import GoodsReceiptService, SupplierInvoiceService, SupplierService

    pid = user.pharmacy_id
    rec = await get(db, pid, import_id, lock=True)          # one confirmation at a time per import
    if rec.status == "CONFIRMED":
        raise HTTPException(status_code=409, detail="This bill was already imported")
    _require(perms, "purchases.create", "import supplier bills")
    if finalize and not post_grn:
        raise HTTPException(status_code=422, detail="Post the goods receipt to finalise the bill in the same step")
    if post_grn:
        _require(perms, "purchases.post", "post goods receipts to stock")
    if finalize:
        _require(perms, "purchases.invoice_finalize", "finalise supplier bills")
    result = await check(db, rec, review)
    if result["errors"]:
        raise HTTPException(status_code=422, detail={"message": "The bill has problems to fix before importing", "errors": result["errors"]})

    supplier_id = review.get("supplier_id")
    if not supplier_id:
        _require(perms, "suppliers.manage", "create suppliers")
        ns = review["new_supplier"]
        supplier = await SupplierService.create_supplier(db, pid, SupplierCreate(
            name=ns["name"], gstin=ns.get("gstin") or None, phone=ns.get("phone") or None), user.id)
        supplier_id = supplier.id

    units = {u.id: u for u in (await db.execute(select(Unit).where(Unit.pharmacy_id == pid))).scalars()}
    grn_items: List[GoodsReceiptItemCreate] = []
    created_medicines, created_packs = [], []
    for ln in review["lines"]:
        medicine_id = ln.get("medicine_id")
        if not medicine_id:
            _require(perms, "medicines.manage", "create medicines")
            nm = ln["new_medicine"]
            form = (nm.get("dosage_form") or "Tablet").strip()
            base_unit_id = nm.get("base_unit_id")
            if not base_unit_id:
                guess = next((u for w, u in FORM_BASE_UNIT if w in form.lower()), "Piece").lower()
                base_unit_id = next((u.id for u in units.values() if u.name.lower() == guess), None)
            if base_unit_id not in units:
                raise HTTPException(status_code=422, detail=f"Choose a stock unit for the new medicine '{nm['name']}'")
            med = Medicine(pharmacy_id=pid, name=nm["name"].strip()[:255], generic_name=(nm.get("generic_name") or nm["name"]).strip()[:500],
                           strength=(nm.get("strength") or None), dosage_form=form[:100], hsn_code=(ln.get("hsn") or "3004")[:20],
                           base_unit_id=base_unit_id, tax_rate_id=ln.get("tax_rate_id"),
                           requires_prescription=bool(nm.get("requires_prescription", False)), is_active=True)
            db.add(med)
            await db.flush()
            medicine_id = med.id
            created_medicines.append(med.name)
        unit_id = ln.get("packaging_unit_id")
        if not unit_id:
            _require(perms, "medicines.manage", "add pack sizes to medicines")
            np_ = ln["new_pack"]
            unit_id = int(np_["unit_id"])
            exists = (await db.execute(select(MedicineUnitConversion).where(
                MedicineUnitConversion.medicine_id == medicine_id, MedicineUnitConversion.packaging_unit_id == unit_id))).scalar_one_or_none()
            if exists is None:
                db.add(MedicineUnitConversion(pharmacy_id=pid, medicine_id=medicine_id, packaging_unit_id=unit_id,
                                              conversion_factor=D(np_["size"]), is_sales_unit=True, is_purchase_unit=True,
                                              is_default_dispensing_unit=False))
                created_packs.append(f"{units[unit_id].name} of {np_['size']}" if unit_id in units else str(unit_id))
                await db.flush()
            elif exists.conversion_factor != D(np_["size"]):
                raise HTTPException(status_code=422, detail=(
                    f"This medicine's {units[unit_id].name if unit_id in units else 'pack'} is already {exists.conversion_factor} units, "
                    f"not {np_['size']}; choose that unit or a different pack unit"))
        grn_items.append(GoodsReceiptItemCreate(
            medicine_id=medicine_id, batch_number=ln["batch_number"].strip(), expiry_date=date.fromisoformat(ln["expiry_date"]),
            packaging_unit_id=unit_id, received_qty_pack=D(ln["qty"]), free_qty_pack=D(ln.get("free_qty"), ZERO),
            purchase_rate=D(ln["purchase_rate"]), discount_percent=D(ln.get("discount_percent"), ZERO),
            mrp=D(ln["mrp"]), selling_rate=D(ln["selling_rate"]), tax_rate_id=ln["tax_rate_id"]))

    received = date.fromisoformat(review["received_date"]) if review.get("received_date") else today_local()
    inv_no = review["invoice_number"].strip()
    note = f"Imported from supplier bill document #{rec.id} ({rec.file_name})"
    grn = await GoodsReceiptService.create_goods_receipt(db, pid, GoodsReceiptCreate(
        supplier_id=supplier_id, receipt_date=received, supplier_invoice_no=inv_no, notes=note, items=grn_items), user.id)
    if post_grn:
        grn = await GoodsReceiptService.post_goods_receipt(db, pid, grn.id, user.id)
    grn = await GoodsReceiptService.get_goods_receipt(db, pid, grn.id)
    grn_lines = sorted(grn.items, key=lambda gi: gi.id)
    declared = review.get("declared") or {}
    invoice = await SupplierInvoiceService.create_invoice(db, pid, SupplierInvoiceCreate(
        supplier_id=supplier_id, invoice_number=inv_no, grn_id=grn.id, invoice_date=date.fromisoformat(review["invoice_date"]),
        received_date=received, due_date=date.fromisoformat(review["due_date"]) if review.get("due_date") else None,
        round_off=D(declared.get("round_off"), ZERO), supplier_declared_total=D(declared.get("grand_total")),
        discrepancy_notes=(review.get("discrepancy_notes") or None), notes=note,
        items=[SupplierInvoiceItemCreate(grn_item_id=gi.id, medicine_id=gi.medicine_id, batch_number=gi.batch_number,
                                         packaging_unit_id=gi.packaging_unit_id, billed_qty_pack=gi.received_qty_pack,
                                         free_qty_pack=gi.free_qty_pack, purchase_rate=gi.purchase_rate,
                                         discount_percent=gi.discount_percent, tax_rate_id=gi.tax_rate_id)
               for gi in grn_lines]), user.id)
    if finalize:
        invoice = await supplier_invoices.finalize(db, user, perms, invoice.id, acknowledge_warnings)

    rec.status, rec.grn_id, rec.supplier_invoice_id = "CONFIRMED", grn.id, invoice.id
    rec.supplier_id, rec.invoice_number = supplier_id, inv_no
    rec.invoice_date, rec.grand_total = date.fromisoformat(review["invoice_date"]), D(declared.get("grand_total"))
    rec.review_json = json.dumps(review, default=str)
    rec.confirmed_by, rec.confirmed_at = user.id, utcnow()
    if rec.reviewed_by is None:
        rec.reviewed_by, rec.reviewed_at = user.id, utcnow()
    _audit(db, user, "purchase.bill_confirm", rec, {"grn": grn.grn_number, "supplier_invoice_id": invoice.id,
                                                    "invoice_number": inv_no, "posted_grn": post_grn, "finalized": finalize,
                                                    "created_medicines": created_medicines, "created_packs": created_packs,
                                                    "warnings": result["warnings"]})
    await db.flush()
    return serialize(rec, {"grn_number": grn.grn_number, "grn_status": grn.status, "supplier_invoice_status": invoice.status,
                           "created_medicines": created_medicines, "created_packs": created_packs,
                           "warnings": result["warnings"]})


async def list_imports(db: AsyncSession, pid: int, limit: int = 50) -> List[Dict[str, Any]]:
    rows = (await db.execute(select(PurchaseBillImport).where(PurchaseBillImport.pharmacy_id == pid)
                             .order_by(PurchaseBillImport.id.desc()).limit(limit))).scalars().all()
    names = {}
    ids = {r.supplier_id for r in rows if r.supplier_id}
    if ids:
        names = dict((await db.execute(select(Supplier.id, Supplier.name).where(Supplier.id.in_(ids)))).all())
    return [serialize(r, {"supplier_name": names.get(r.supplier_id)}) for r in rows]
