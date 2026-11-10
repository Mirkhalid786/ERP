"""
Medicine catalogue import (CSV / Excel): preview, column mapping, validation, duplicate detection and an
all-or-nothing commit.

A catalogue row defines a product (name, salt, strength, form, units, GST/HSN, schedule). Importing never creates
stock: no batches and no stock movements are written.

Duplicates: a row matches an existing medicine when its normalised name is the same, or when its barcode is already
used. Within one file the first occurrence wins. on_duplicate="skip" (default) leaves existing medicines unchanged;
"update" fills in blank details on the existing medicine and adds a missing pack conversion. It never changes the
stock (base) unit or an existing conversion factor, because that would change the meaning of stock already held.
"""
import csv
import io
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth import AuditLog, User
from app.models.inventory import (
    Category, Manufacturer, Medicine, MedicineBarcode, MedicineUnitConversion, TaxRate, Unit,
)

MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 50_000
PREVIEW_ROWS = 200

FIELDS: Dict[str, str] = {
    "name": "Product name", "generic_name": "Generic / salt", "strength": "Strength", "dosage_form": "Dosage form",
    "brand_name": "Brand", "manufacturer": "Manufacturer", "category": "Category", "hsn_code": "HSN code",
    "gst_rate": "GST %", "schedule_type": "Schedule", "requires_prescription": "Prescription required",
    "base_unit": "Stock unit", "pack_unit": "Pack unit", "pack_size": "Units per pack", "barcode": "Barcode",
    "reorder_level": "Reorder level",
}
ALIASES: Dict[str, List[str]] = {
    "name": ["name", "product", "product name", "item", "item name", "medicine", "medicine name", "drug name", "description"],
    "generic_name": ["generic", "generic name", "salt", "salt name", "composition", "molecule", "content", "generic_name"],
    "strength": ["strength", "potency", "dose"],
    "dosage_form": ["dosage form", "form", "dosage_form", "type", "formulation"],
    "brand_name": ["brand", "brand name", "brand_name"],
    "manufacturer": ["manufacturer", "company", "mfr", "mfg", "marketed by", "manufacturer name"],
    "category": ["category", "group", "therapeutic group", "group name", "class"],
    "hsn_code": ["hsn", "hsn code", "hsn_code", "hsn/sac"],
    "gst_rate": ["gst", "gst %", "gst rate", "gst_rate", "tax", "tax %", "tax rate"],
    "schedule_type": ["schedule", "schedule type", "schedule_type", "drug schedule"],
    "requires_prescription": ["prescription", "rx", "requires prescription", "requires_prescription", "prescription required"],
    "base_unit": ["unit", "base unit", "base_unit", "stock unit", "uom"],
    "pack_unit": ["pack unit", "pack_unit", "packing unit"],
    "pack_size": ["pack size", "pack_size", "packing", "pack", "units per pack", "unit size", "conversion"],
    "barcode": ["barcode", "ean", "gtin", "upc"],
    "reorder_level": ["reorder level", "reorder_level", "min stock", "minimum stock"],
}
SCHEDULES = {"NONE", "H", "H1", "X", "G", "NARCOTIC", "OTC"}
RX_SCHEDULES = {"H", "H1", "X", "NARCOTIC"}
TRUE = {"true", "yes", "y", "1", "rx"}
FALSE = {"false", "no", "n", "0", "otc", ""}
FORM_WORDS = [  # (pattern in the product name, dosage form)
    (r"\b(tab|tabs|tablet|tablets)\b", "Tablet"), (r"\b(cap|caps|capsule|capsules)\b", "Capsule"),
    (r"\b(syp|syr|syrup)\b", "Syrup"), (r"\b(susp|suspension)\b", "Suspension"), (r"\b(inj|injection)\b", "Injection"),
    (r"\b(drop|drops)\b", "Drops"), (r"\bcream\b", "Cream"), (r"\b(oint|ointment)\b", "Ointment"), (r"\bgel\b", "Gel"),
    (r"\blotion\b", "Lotion"), (r"\b(inhaler|rotacap|rotacaps)\b", "Inhaler"), (r"\brespules?\b", "Respules"),
    (r"\bsachet\b", "Sachet"), (r"\bpowder\b", "Powder"), (r"\bspray\b", "Spray"),
]
# Dosage form -> stock unit name (matched against the pharmacy's units)
FORM_BASE_UNIT = [
    ("tablet", "Tablet"), ("capsule", "Capsule"), ("vial", "Vial"), ("ampoule", "Ampoule"), ("respule", "Ampoule"),
    ("injection", "Vial"), ("syrup", "Bottle"), ("suspension", "Bottle"), ("solution", "Bottle"), ("drop", "Bottle"),
    ("infusion", "Bottle"), ("lotion", "Bottle"), ("spray", "Bottle"), ("cream", "Tube"), ("ointment", "Tube"),
    ("gel", "Tube"), ("powder", "Piece"), ("sachet", "Piece"), ("inhaler", "Piece"),
]
UNIT_ALIASES = {"tab": "tablet", "tabs": "tablet", "cap": "capsule", "caps": "capsule", "ml": "milliliter", "gm": "gram",
                "g": "gram", "nos": "piece", "no": "piece", "pcs": "piece", "pc": "piece", "btl": "bottle", "amp": "ampoule",
                "stp": "strip", "strips": "strip"}


def norm_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


# ── Parsing ────────────────────────────────────────────────────────────────────

def parse_file(filename: str, data: bytes) -> Tuple[List[str], List[Dict[str, str]]]:
    if not data:
        raise HTTPException(status_code=422, detail="The file is empty")
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB; split it into parts")
    lower = (filename or "").lower()
    if data[:4] == b"PK\x03\x04" or lower.endswith(".xlsx"):
        return _parse_xlsx(data)
    if lower.endswith((".xls",)):
        raise HTTPException(status_code=415, detail="Old .xls files are not supported; save the sheet as .xlsx or CSV")
    return _parse_csv(data)


def _parse_csv(data: bytes) -> Tuple[List[str], List[Dict[str, str]]]:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise HTTPException(status_code=422, detail="The file is not readable text (save it as CSV UTF-8)")
    if "\x00" in text:
        raise HTTPException(status_code=415, detail="This does not look like a CSV file")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    rows = list(reader)
    return _table(rows)


def _parse_xlsx(data: bytes) -> Tuple[List[str], List[Dict[str, str]]]:
    try:
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        raise HTTPException(status_code=415, detail="The Excel file could not be opened (use .xlsx)")
    ws = wb.worksheets[0]
    rows = []
    for r in ws.iter_rows(values_only=True):
        rows.append(["" if v is None else (str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)) for v in r])
        if len(rows) > MAX_ROWS + 1:
            break
    wb.close()
    return _table(rows)


ROW_NO = "\x00row"      # original spreadsheet row number, so messages point at the right line


def _table(rows: List[List[str]]) -> Tuple[List[str], List[Dict[str, str]]]:
    numbered = [(n, r) for n, r in enumerate(rows, start=1) if any(str(c).strip() for c in r)]
    if not numbered:
        raise HTTPException(status_code=422, detail="The file has no rows")
    headers = [str(h).strip() or f"Column {i + 1}" for i, h in enumerate(numbered[0][1])]
    if len(numbered) - 1 > MAX_ROWS:
        raise HTTPException(status_code=413, detail=f"The file has more than {MAX_ROWS} rows; split it into parts")
    out = []
    for n, r in numbered[1:]:
        d = {headers[i]: str(v).strip() for i, v in enumerate(r[:len(headers)])}
        d[ROW_NO] = n
        out.append(d)
    return headers, out


def suggest_mapping(headers: List[str]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    used = set()
    for target, names in ALIASES.items():
        for h in headers:
            if h in used:
                continue
            if h.strip().lower() in names:
                mapping[target] = h
                used.add(h)
                break
    return mapping


# ── Row validation ─────────────────────────────────────────────────────────────

@dataclass
class Lookups:
    units: Dict[str, Unit]
    tax_by_rate: Dict[Decimal, TaxRate]
    manufacturers: Dict[str, Manufacturer]
    categories: Dict[str, Category]
    med_by_key: Dict[str, int]
    med_by_barcode: Dict[str, int]


async def load_lookups(db: AsyncSession, pid: int) -> Lookups:
    units = {}
    for u in (await db.execute(select(Unit).where(Unit.pharmacy_id == pid, Unit.is_active == True))).scalars():  # noqa: E712
        units[u.name.lower()] = u
        units[u.abbreviation.lower()] = u
    tax = {}
    for t in (await db.execute(select(TaxRate).where(TaxRate.pharmacy_id == pid, TaxRate.is_active == True))).scalars():  # noqa: E712
        if t.rate_percentage not in tax or t.name.upper().startswith("GST"):
            tax[t.rate_percentage] = t
    mfrs = {m.name.lower(): m for m in (await db.execute(select(Manufacturer).where(Manufacturer.pharmacy_id == pid))).scalars()}
    cats = {c.name.lower(): c for c in (await db.execute(select(Category).where(Category.pharmacy_id == pid))).scalars()}
    by_key, by_bc = {}, {}
    for mid, name, bc in (await db.execute(select(Medicine.id, Medicine.name, Medicine.barcode).where(Medicine.pharmacy_id == pid))).all():
        by_key.setdefault(norm_key(name), mid)
        if bc:
            by_bc[bc.strip()] = mid
    for mid, bc in (await db.execute(select(MedicineBarcode.medicine_id, MedicineBarcode.barcode)
                                     .join(Medicine, Medicine.id == MedicineBarcode.medicine_id)
                                     .where(Medicine.pharmacy_id == pid))).all():
        by_bc[bc.strip()] = mid
    return Lookups(units, tax, mfrs, cats, by_key, by_bc)


@dataclass
class RowResult:
    row: int
    values: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    action: str = "create"             # create | update | skip | error
    match_id: Optional[int] = None


def _unit(lk: Lookups, text: str) -> Optional[Unit]:
    t = (text or "").strip().lower().rstrip(".")
    return lk.units.get(t) or lk.units.get(UNIT_ALIASES.get(t, ""))


def _pack_size(text: str) -> Optional[Decimal]:
    t = (text or "").strip().lower().replace("’", "'")
    if not t:
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[x\*]\s*(\d+(?:\.\d+)?)", t)        # "10x10" = 100 per pack
    if m:
        return Decimal(m.group(1)) * Decimal(m.group(2))
    m = re.match(r"(\d+(?:\.\d+)?)", t)                                         # "10", "10's", "10 tab", "15 tablets"
    return Decimal(m.group(1)) if m else Decimal("-1")


def validate_row(n: int, raw: Dict[str, str], mapping: Dict[str, str], lk: Lookups) -> RowResult:
    r = RowResult(row=n)
    get = lambda f: (raw.get(mapping[f], "") if f in mapping else "").strip()
    v = r.values
    name, generic, strength, form = get("name"), get("generic_name"), get("strength"), get("dosage_form")
    if not form and name:
        for pat, guess in FORM_WORDS:
            if re.search(pat, name.lower()):
                form = guess
                r.warnings.append(f"Dosage form taken from the name: {guess}")
                break
    if not name and generic:
        name = " ".join(x for x in (generic, strength, form) if x)
    if not name:
        r.errors.append("Product name is missing")
    if not generic and name:
        generic = name
        r.warnings.append("No generic/salt given; the product name is used")
    if not form:
        r.errors.append("Dosage form is missing (e.g. Tablet, Capsule, Syrup)")
    for key, val, limit in (("name", name, 255), ("generic_name", generic, 500), ("strength", strength, 100),
                            ("dosage_form", form, 100), ("brand_name", get("brand_name"), 255)):
        if len(val) > limit:
            r.errors.append(f"{FIELDS[key]} is longer than {limit} characters")
        v[key] = val or None
    v["manufacturer"] = get("manufacturer")[:200] or None
    v["category"] = get("category")[:100] or None

    hsn = re.sub(r"\s", "", get("hsn_code"))
    if hsn and not re.fullmatch(r"\d{4,8}", hsn):
        r.errors.append(f"HSN code '{hsn}' must be 4 to 8 digits")
    v["hsn_code"] = hsn or "3004"

    gst = get("gst_rate").upper().replace("GST", "").replace("%", "").strip()
    v["tax_rate_id"] = None
    if gst:
        try:
            rate = Decimal(gst).quantize(Decimal("0.01"))
            t = lk.tax_by_rate.get(rate)
            if t is None:
                r.errors.append(f"GST rate {gst}% is not set up in Masters & Tax")
            else:
                v["tax_rate_id"] = t.id
                v["gst_rate"] = str(rate)
        except InvalidOperation:
            r.errors.append(f"GST rate '{get('gst_rate')}' is not a number")

    sched = get("schedule_type").upper().replace("SCHEDULE", "").strip() or "NONE"
    if sched not in SCHEDULES:
        r.errors.append(f"Schedule '{get('schedule_type')}' must be one of {', '.join(sorted(SCHEDULES))}")
        sched = "NONE"
    v["schedule_type"] = sched
    rx_text = get("requires_prescription").lower()
    if rx_text in TRUE:
        v["requires_prescription"] = True
    elif rx_text in FALSE:
        v["requires_prescription"] = sched in RX_SCHEDULES
    else:
        r.errors.append(f"Prescription required '{get('requires_prescription')}' must be yes or no")
        v["requires_prescription"] = sched in RX_SCHEDULES

    base_text = get("base_unit")
    base = _unit(lk, base_text) if base_text else None
    if base_text and base is None:
        r.errors.append(f"Stock unit '{base_text}' is not one of the units in Masters & Tax")
    if base is None and not base_text and form:
        guess = next((unit for word, unit in FORM_BASE_UNIT if word in form.lower()), "Piece")
        base = _unit(lk, guess)
        if base is not None:
            r.warnings.append(f"Stock unit set to {base.name} from the dosage form")
    if base is None and not r.errors:
        r.errors.append("No stock unit could be determined")
    v["base_unit_id"] = base.id if base else None
    v["base_unit"] = base.name if base else None

    size = _pack_size(get("pack_size"))
    v["pack_unit_id"] = None
    if size is not None:
        if size <= 0 or size > 100000:
            r.errors.append(f"Units per pack '{get('pack_size')}' is not a positive number")
        elif size != 1 or get("pack_unit"):
            pack_text = get("pack_unit") or ("Strip" if form and form.lower() in ("tablet", "capsule") else "Box")
            pack = _unit(lk, pack_text)
            if pack is None:
                r.errors.append(f"Pack unit '{pack_text}' is not one of the units in Masters & Tax")
            elif base is not None and pack.id == base.id:
                r.errors.append("Pack unit cannot be the same as the stock unit")
            else:
                v["pack_unit_id"], v["pack_unit"], v["pack_size"] = pack.id, pack.name, str(size)

    bc = get("barcode")
    if len(bc) > 100:
        r.errors.append("Barcode is longer than 100 characters")
    v["barcode"] = bc or None
    rl = get("reorder_level")
    v["reorder_level"] = "0"
    if rl:
        try:
            if Decimal(rl) < 0:
                raise InvalidOperation
            v["reorder_level"] = rl
        except InvalidOperation:
            r.errors.append(f"Reorder level '{rl}' is not a non-negative number")
    return r


def analyse(rows: List[Dict[str, str]], mapping: Dict[str, str], lk: Lookups, on_duplicate: str) -> List[RowResult]:
    seen_keys, seen_bc = {}, {}
    results = []
    for raw in rows:
        i = raw.get(ROW_NO, 0)
        r = validate_row(i, raw, mapping, lk)
        if r.errors:
            r.action = "error"
        else:
            key = norm_key(r.values["name"])
            bc = r.values["barcode"]
            if key in seen_keys:
                r.action, r.warnings = "skip", r.warnings + [f"Same product as row {seen_keys[key]} in this file"]
            elif bc and bc in seen_bc:
                r.action, r.warnings = "skip", r.warnings + [f"Same barcode as row {seen_bc[bc]} in this file"]
            else:
                match = lk.med_by_key.get(key) or (lk.med_by_barcode.get(bc) if bc else None)
                if match:
                    r.match_id = match
                    r.action = "update" if on_duplicate == "update" else "skip"
                    if r.action == "skip":
                        r.warnings.append("Already in the catalogue")
                seen_keys[key] = i
                if bc:
                    seen_bc[bc] = i
        results.append(r)
    return results


def summary(results: List[RowResult]) -> Dict[str, int]:
    out = {"create": 0, "update": 0, "skip": 0, "error": 0}
    for r in results:
        out[r.action] += 1
    return out


def _check_mapping(mapping: Dict[str, str], headers: List[str]) -> Dict[str, str]:
    clean = {k: v for k, v in (mapping or {}).items() if v}
    unknown = set(clean) - set(FIELDS)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown target field(s): {', '.join(sorted(unknown))}")
    missing = [v for v in clean.values() if v not in headers]
    if missing:
        raise HTTPException(status_code=422, detail=f"Column(s) not in the file: {', '.join(missing)}")
    if "name" not in clean and "generic_name" not in clean:
        raise HTTPException(status_code=422, detail="Map at least the product name or the generic name")
    return clean


async def preview(db: AsyncSession, user: User, filename: str, data: bytes, mapping: Optional[Dict[str, str]],
                  on_duplicate: str) -> Dict[str, Any]:
    headers, rows = parse_file(filename, data)
    mapping = _check_mapping(mapping if mapping else suggest_mapping(headers), headers)
    lk = await load_lookups(db, user.pharmacy_id)
    results = analyse(rows, mapping, lk, on_duplicate)
    return {
        "file_name": filename, "headers": headers, "mapping": mapping, "fields": FIELDS, "total_rows": len(rows),
        "summary": summary(results), "on_duplicate": on_duplicate,
        "rows": [r.__dict__ for r in results[:PREVIEW_ROWS]],
        "problems": [r.__dict__ for r in results if r.action == "error"][:500],
    }


async def commit(db: AsyncSession, user: User, filename: str, data: bytes, mapping: Dict[str, str],
                 on_duplicate: str) -> Dict[str, Any]:
    """Validates everything again and applies it in the caller's transaction (all rows or none)."""
    headers, rows = parse_file(filename, data)
    mapping = _check_mapping(mapping if mapping else suggest_mapping(headers), headers)
    pid = user.pharmacy_id
    lk = await load_lookups(db, pid)
    results = analyse(rows, mapping, lk, on_duplicate)
    created_masters = {"manufacturers": 0, "categories": 0}

    async def master(kind: str, name: Optional[str]):
        if not name:
            return None
        store, model = (lk.manufacturers, Manufacturer) if kind == "manufacturers" else (lk.categories, Category)
        obj = store.get(name.lower())
        if obj is None:
            obj = model(pharmacy_id=pid, name=name, is_active=True)
            db.add(obj)
            await db.flush()
            store[name.lower()] = obj
            created_masters[kind] += 1
        return obj.id

    created_ids: List[int] = []
    for i, r in enumerate(results):
        v = r.values
        if r.action == "create":
            med = Medicine(
                pharmacy_id=pid, name=v["name"], brand_name=v["brand_name"], generic_name=v["generic_name"],
                strength=v["strength"], dosage_form=v["dosage_form"], hsn_code=v["hsn_code"],
                schedule_type=v["schedule_type"], requires_prescription=v["requires_prescription"],
                base_unit_id=v["base_unit_id"], barcode=v["barcode"], tax_rate_id=v["tax_rate_id"],
                manufacturer_id=await master("manufacturers", v["manufacturer"]),
                category_id=await master("categories", v["category"]),
                reorder_level=Decimal(v["reorder_level"]), is_active=True,
            )
            db.add(med)
            await db.flush()
            created_ids.append(med.id)
            if v["pack_unit_id"]:
                db.add(MedicineUnitConversion(pharmacy_id=pid, medicine_id=med.id, packaging_unit_id=v["pack_unit_id"],
                                              conversion_factor=Decimal(v["pack_size"]), is_sales_unit=True,
                                              is_purchase_unit=True, is_default_dispensing_unit=False))
        elif r.action == "update":
            med = await db.get(Medicine, r.match_id)
            for attr in ("brand_name", "strength", "barcode", "tax_rate_id"):
                if v.get(attr) and not getattr(med, attr):
                    setattr(med, attr, v[attr])
            if v["manufacturer"] and not med.manufacturer_id:
                med.manufacturer_id = await master("manufacturers", v["manufacturer"])
            if v["category"] and not med.category_id:
                med.category_id = await master("categories", v["category"])
            if v["pack_unit_id"]:
                exists = (await db.execute(select(MedicineUnitConversion.id).where(
                    MedicineUnitConversion.medicine_id == med.id,
                    MedicineUnitConversion.packaging_unit_id == v["pack_unit_id"]))).scalar_one_or_none()
                if exists is None:
                    db.add(MedicineUnitConversion(pharmacy_id=pid, medicine_id=med.id, packaging_unit_id=v["pack_unit_id"],
                                                  conversion_factor=Decimal(v["pack_size"]), is_sales_unit=True,
                                                  is_purchase_unit=True, is_default_dispensing_unit=False))
        if i % 500 == 499:
            await db.flush()
    await db.flush()
    s = summary(results)
    db.add(AuditLog(pharmacy_id=pid, user_id=user.id, action="medicine.catalogue_import", entity_type="medicine",
                    metadata_json=json.dumps({"file": filename[:200], "rows": len(rows), "summary": s,
                                              "on_duplicate": on_duplicate, "created_masters": created_masters,
                                              "mapping": mapping})))
    await db.flush()
    return {"summary": s, "total_rows": len(rows), "created_masters": created_masters,
            "problems": [r.__dict__ for r in results if r.action == "error"][:500]}
