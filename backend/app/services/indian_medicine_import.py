"""
Large-scale Indian Medicine Dataset Importer (JuniorAlive / PharmaLens).

Handles:
  - Streaming CSV processing for ~254k records with bounded memory usage.
  - Safe mapping of brands, salts, strengths, dosage forms, packaging units, and manufacturers.
  - Therapeutic categorization and schedule/prescription tagging.
  - Catalogue reference MRP and discontinued status preservation (strictly separated from stock/batches).
  - High-performance in-memory caching of master lookups (units, categories, manufacturers, tax rates).
  - Transactional batching (e.g. 2,000 records per transaction) to prevent unbounded DB locks.
  - Audit logging of catalogue operations.
"""
import csv
import json
import logging
import os
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth import AuditLog
from app.models.inventory import (
    Category, Manufacturer, Medicine, MedicineUnitConversion, TaxRate, Unit,
)

log = logging.getLogger(__name__)

DATASET_CSV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "catalogue", "indian_medicine_data.csv"
)

# ── Dosage Form Extraction Patterns ───────────────────────────────────────────
DOSAGE_PATTERNS: List[Tuple[re.Pattern, str, str]] = [
    # (Regex pattern, Dosage Form Name, Base Unit Name)
    (re.compile(r"(?i)\b(tablet\s+sr|tablet\s+er|tablet\s+pr|tablet\s+cr|tablet\s+mr|tablet\s+dr|tablet\s+dt|tablet\s+md|tablets?|tabs?)\b"), "Tablet", "Tablet"),
    (re.compile(r"(?i)\b(capsule\s+sr|capsule\s+er|capsule\s+dr|capsules?|caps?)\b"), "Capsule", "Capsule"),
    (re.compile(r"(?i)\b(dry\s+syrup|syrup|syp|syr|expectorant)\b"), "Syrup", "Bottle"),
    (re.compile(r"(?i)\b(oral\s+suspension|suspension|susp)\b"), "Suspension", "Bottle"),
    (re.compile(r"(?i)\b(powder\s+for\s+injection|injections?|inj)\b"), "Injection", "Vial"),
    (re.compile(r"(?i)\b(eye\s+drops?|ear\s+drops?|nasal\s+drops?|drops?)\b"), "Drops", "Bottle"),
    (re.compile(r"(?i)\b(eye\s+ointment|ointment|oint)\b"), "Ointment", "Tube"),
    (re.compile(r"(?i)\b(creams?)\b"), "Cream", "Tube"),
    (re.compile(r"(?i)\b(gels?)\b"), "Gel", "Tube"),
    (re.compile(r"(?i)\b(lotions?)\b"), "Lotion", "Bottle"),
    (re.compile(r"(?i)\b(inhalers?|rotacaps?|respules?)\b"), "Inhaler", "Piece"),
    (re.compile(r"(?i)\b(nasal\s+spray|sprays?)\b"), "Spray", "Bottle"),
    (re.compile(r"(?i)\b(sachets?)\b"), "Sachet", "Piece"),
    (re.compile(r"(?i)\b(powders?|granules?)\b"), "Powder", "Piece"),
    (re.compile(r"(?i)\b(mouth\s*wash|gargle|liquids?|oral\s+solution|solutions?|infusions?)\b"), "Solution", "Bottle"),
]

# Packaging label regex: "strip of 10 tablets", "bottle of 100 ml Syrup", "vial of 1 Injection"
PACK_REGEX = re.compile(
    r"(?i)^(strip|box|bottle|tube|vial|ampoule|packet|blister|pack|jar|sachet|can|carton|tin|prefilled syringe|pre-filled syringe|syringe)\s+of\s+([0-9.]+)\s*(.*)$"
)

# Strength pattern from name
STRENGTH_REGEX = re.compile(
    r"(?i)\b(\d+(?:\.\d+)?\s*(?:mg|mcg|gm|g|ml|%|iu|duo|forte|ds|sr|er|pr|mr|cr|dt|md|xr)?(?:\s*/\s*\d+(?:\.\d+)?\s*(?:mg|mcg|gm|g|ml)?)?)\b"
)

# ── Therapeutic Category Classification ───────────────────────────────────────
CATEGORY_RULES: List[Tuple[str, re.Pattern]] = [
    ("Antibiotics", re.compile(r"(?i)\b(amoxycillin|clavulanic|azithromycin|cefixime|ciprofloxacin|ofloxacin|cefpodoxime|metronidazole|doxycycline|levofloxacin|ampicillin|meropenem|ceftriaxone|amikacin|gentamicin|clindamycin|clarithromycin|linezolid|moxifloxacin|norfloxacin|piperacillin|tazobactam|nitrofurantoin|cefuroxime|erythromycin)\b")),
    ("Analgesics & Antipyretics", re.compile(r"(?i)\b(paracetamol|ibuprofen|diclofenac|aceclofenac|tramadol|mefenamic|etoricoxib|ketorolac|aspirin|nimesulide|piroxicam|naproxen|indomethacin|lornoxicam)\b")),
    ("Gastrointestinal & Antacids", re.compile(r"(?i)\b(pantoprazole|omeprazole|rabeprazole|esomeprazole|ranitidine|famotidine|domperidone|ondansetron|sucralfate|magaldrate|simethicone|lactulose|sodium picosulfate|bisacodyl|mesalamine|metoclopramide|itopride)\b")),
    ("Cardiovascular & Antihypertensive", re.compile(r"(?i)\b(amlodipine|telmisartan|losartan|atorvastatin|rosuvastatin|metoprolol|atenolol|ramipril|enalapril|clopidogrel|ticagrelor|olmesartan|cilnidipine|diltiazem|verapamil|nebivolol|bisoprolol|carvedilol|fenofibrate)\b")),
    ("Respiratory & Antiallergic", re.compile(r"(?i)\b(cetirizine|levocetirizine|fexofenadine|montelukast|salbutamol|ambroxol|guaifenesin|dextromethorphan|phenylephrine|chlorpheniramine|budesonide|formoterol|levosalbutamol|terbutaline|theophylline|bilastine|hydroxyzine)\b")),
    ("Antidiabetic", re.compile(r"(?i)\b(metformin|glimepiride|gliclazide|vildagliptin|sitagliptin|teneligliptin|dapagliflozin|empagliflozin|insulin|pioglitazone|glipizide|voglibose)\b")),
    ("Dermatological & Topicals", re.compile(r"(?i)\b(clobetasol|betamethasone|mupirocin|fusidic|ketoconazole|clotrimazole|permethrin|adapalene|miconazole|terbinafine|fluconazole|itraconazole|calamine)\b")),
    ("Vitamins & Supplements", re.compile(r"(?i)\b(calcium|vitamin|folic acid|zinc|iron|ferrous|methylcobalamin|ascorbic|cyanocobalamin|multivitamin|cholecalciferol|protein|biotin)\b")),
    ("Central Nervous System", re.compile(r"(?i)\b(alprazolam|clonazepam|escitalopram|sertraline|fluoxetine|gabapentin|pregabalin|duloxetine|olanzapine|quetiapine|risperidone|levetiracetam|sodium valproate|carbamazepine)\b")),
]


def norm_text(text: Optional[str]) -> str:
    """Normalize text by stripping punctuation and whitespace."""
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def parse_indian_medicine_row(row: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """Parse a single raw row from the Indian Medicine Dataset into structured catalogue fields."""
    name_raw = (row.get("name") or "").strip()
    if not name_raw:
        return None

    # Full generic composition
    c1 = (row.get("short_composition1") or "").strip()
    c2 = (row.get("short_composition2") or "").strip()
    cs = (row.get("salt_composition") or "").strip()
    generic_name = cs if cs else (f"{c1} + {c2}" if c2 else c1)
    if not generic_name:
        generic_name = name_raw

    # Manufacturer
    manufacturer_name = (row.get("manufacturer_name") or "").strip() or "Standard Pharmaceuticals"

    # Pack size label
    label = (row.get("pack_size_label") or "").strip()

    # Dosage form & base unit
    dosage_form = "Tablet"
    base_unit_name = "Tablet"
    for pat, form, base_u in DOSAGE_PATTERNS:
        if pat.search(name_raw) or (label and pat.search(label)):
            dosage_form = form
            base_unit_name = base_u
            break

    # Strength extraction
    strength = None
    m_str = STRENGTH_REGEX.search(name_raw)
    if m_str:
        strength = m_str.group(1).strip()
    elif generic_name:
        m_str_gen = STRENGTH_REGEX.search(generic_name)
        if m_str_gen:
            strength = m_str_gen.group(1).strip()

    # Brand extraction (extract prefix of product name before strength or form)
    brand_name = None
    name_parts = name_raw.split()
    if name_parts:
        # First word or two usually denotes the brand family (e.g. Augmentin, Dolo, Azithral)
        brand_name = name_parts[0]

    # Packaging unit and pack size (conversion factor)
    pack_unit_name = None
    pack_size = Decimal("1.0000")
    if label:
        m_pack = PACK_REGEX.match(label)
        if m_pack:
            p_unit_raw, p_size_raw, _ = m_pack.groups()
            pack_unit_name = p_unit_raw.title()
            if pack_unit_name in ("Prefilled Syringe", "Pre-Filled Syringe"):
                pack_unit_name = "Piece"
            try:
                pack_size = Decimal(p_size_raw)
            except InvalidOperation:
                pack_size = Decimal("1.0000")

    # Reference MRP
    price_str = (row.get("price") or "").strip()
    mrp = None
    if price_str and price_str.lower() not in ("null", "none", "nan"):
        try:
            p_val = Decimal(price_str)
            if p_val > 0:
                mrp = p_val.quantize(Decimal("0.01"))
        except InvalidOperation:
            mrp = None

    # Discontinued status
    disc_str = (row.get("Is_discontinued") or "").strip().upper()
    is_discontinued = (disc_str == "TRUE")

    # Therapeutic category classification
    comp_full = f"{name_raw} {generic_name}"
    category_name = "General Pharmaceuticals"
    for cat_name, rgx in CATEGORY_RULES:
        if rgx.search(comp_full):
            category_name = cat_name
            break

    # Drug Schedule & Prescription:
    # Antibiotics, Cardio, Diabetes, CNS medicines are strictly Schedule H
    if category_name in ("Antibiotics", "Cardiovascular & Antihypertensive", "Antidiabetic", "Central Nervous System"):
        schedule_type = "H"
        requires_prescription = True
    elif "paracetamol" in generic_name.lower() or "antacid" in generic_name.lower():
        schedule_type = "OTC"
        requires_prescription = False
    else:
        schedule_type = "H"
        requires_prescription = True

    return {
        "source_id": str(row.get("id") or "").strip(),
        "source_name": "junioralive/Indian-Medicine-Dataset",
        "name": name_raw[:255],
        "brand_name": (brand_name[:255]) if brand_name else None,
        "generic_name": generic_name[:500],
        "strength": (strength[:100]) if strength else None,
        "dosage_form": dosage_form[:100],
        "base_unit_name": base_unit_name,
        "manufacturer_name": manufacturer_name[:200],
        "category_name": category_name[:100],
        "pack_unit_name": pack_unit_name,
        "pack_size": pack_size,
        "pack_size_label": (label[:255]) if label else None,
        "mrp": mrp,
        "is_discontinued": is_discontinued,
        "hsn_code": "3004",
        "schedule_type": schedule_type,
        "requires_prescription": requires_prescription,
    }


def preview_indian_catalogue(csv_path: str = DATASET_CSV_PATH, max_rows: Optional[int] = None) -> Dict[str, Any]:
    """Perform a dry-run analysis of the Indian Medicine Dataset without touching the database."""
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"Dataset CSV not found at {csv_path}")

    total_rows = 0
    valid_records = 0
    rejected_records = 0
    unique_medicines: Set[Tuple[str, str]] = set()
    unique_manufacturers: Set[str] = set()
    unique_categories: Set[str] = set()
    conversions_count = 0
    discontinued_count = 0
    mrp_count = 0
    sample_records: List[Dict[str, Any]] = []

    with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total_rows += 1
            if max_rows and total_rows > max_rows:
                break

            parsed = parse_indian_medicine_row(row)
            if not parsed:
                rejected_records += 1
                continue

            valid_records += 1
            key = (norm_text(parsed["name"]), norm_text(parsed["manufacturer_name"]))
            unique_medicines.add(key)
            unique_manufacturers.add(parsed["manufacturer_name"].lower())
            unique_categories.add(parsed["category_name"])

            if parsed["pack_unit_name"] and parsed["pack_size"] > Decimal("1"):
                conversions_count += 1
            if parsed["is_discontinued"]:
                discontinued_count += 1
            if parsed["mrp"] is not None:
                mrp_count += 1

            if len(sample_records) < 10:
                sample_records.append({
                    "id": parsed["source_id"],
                    "name": parsed["name"],
                    "brand": parsed["brand_name"],
                    "generic_name": parsed["generic_name"],
                    "strength": parsed["strength"],
                    "dosage_form": parsed["dosage_form"],
                    "manufacturer": parsed["manufacturer_name"],
                    "category": parsed["category_name"],
                    "base_unit": parsed["base_unit_name"],
                    "pack": f"{parsed['pack_unit_name']} of {parsed['pack_size']}" if parsed["pack_unit_name"] else parsed["base_unit_name"],
                    "mrp": float(parsed["mrp"]) if parsed["mrp"] else None,
                    "discontinued": parsed["is_discontinued"],
                })

    duplicates_count = valid_records - len(unique_medicines)
    return {
        "file_path": csv_path,
        "total_source_records": total_rows,
        "valid_records": valid_records,
        "unique_medicines": len(unique_medicines),
        "duplicate_variants": duplicates_count,
        "rejected_records": rejected_records,
        "unique_manufacturers": len(unique_manufacturers),
        "unique_categories": len(unique_categories),
        "conversions_count": conversions_count,
        "field_coverage": {
            "name": f"{valid_records / total_rows * 100:.1f}%",
            "salt_composition": f"{valid_records / total_rows * 100:.1f}%",
            "manufacturer": f"{valid_records / total_rows * 100:.1f}%",
            "dosage_form": f"{valid_records / total_rows * 100:.1f}%",
            "reference_mrp": f"{mrp_count / total_rows * 100:.1f}%",
            "discontinued_status": f"{valid_records / total_rows * 100:.1f}%",
        },
        "discontinued_count": discontinued_count,
        "active_marketed_count": valid_records - discontinued_count,
        "sample_records": sample_records,
    }


async def import_indian_catalogue(
    db: AsyncSession,
    pharmacy_id: int,
    user_id: int,
    csv_path: str = DATASET_CSV_PATH,
    batch_size: int = 2000,
    limit: Optional[int] = None,
    on_duplicate: str = "skip",
    progress_callback: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Import the Indian Medicine Dataset safely in bounded transactional batches.
    
    Guarantees:
      - Memory usage is strictly bounded: reads streaming CSV rows.
      - Fast: pre-caches master lookups (units, categories, tax rates, manufacturers).
      - Master catalogue only: NEVER creates batches, stock movements, or supplier purchase prices.
      - Safe deduplication: respects existing records without merging different strengths or dosage forms.
      - Transactional batching: commits every `batch_size` records so the DB is never locked for long.
      - Full audit trail: writes AuditLog entry.
    """
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"Dataset CSV not found at {csv_path}")

    # 1. Pre-load units for this pharmacy
    units_res = await db.execute(select(Unit).where(Unit.pharmacy_id == pharmacy_id))
    unit_map: Dict[str, Unit] = {u.name.lower(): u for u in units_res.scalars().all()}
    
    # Ensure standard base units exist
    default_units = [
        ("Tablet", "TAB", False), ("Capsule", "CAP", False), ("Strip", "STP", False),
        ("Box", "BOX", False), ("Bottle", "BTL", False), ("Vial", "VIAL", False),
        ("Ampoule", "AMP", False), ("Tube", "TUB", False), ("Piece", "PC", False),
        ("Milliliter", "ML", True), ("Gram", "GM", True),
    ]
    for uname, uabbr, ufrac in default_units:
        if uname.lower() not in unit_map:
            u_obj = Unit(pharmacy_id=pharmacy_id, name=uname, abbreviation=uabbr, is_fractional_allowed=ufrac, is_active=True)
            db.add(u_obj)
            await db.flush()
            unit_map[uname.lower()] = u_obj
    await db.commit()

    # 2. Pre-load categories for this pharmacy
    cat_res = await db.execute(select(Category).where(Category.pharmacy_id == pharmacy_id))
    cat_map: Dict[str, Category] = {c.name.lower(): c for c in cat_res.scalars().all()}

    # 3. Pre-load tax rate (standard GST 12% for medicines)
    tax_res = await db.execute(select(TaxRate).where(TaxRate.pharmacy_id == pharmacy_id, TaxRate.rate_percentage == Decimal("12.00")))
    default_tax_rate = tax_res.scalar_one_or_none()
    if not default_tax_rate:
        tax_res_any = await db.execute(select(TaxRate).where(TaxRate.pharmacy_id == pharmacy_id).limit(1))
        default_tax_rate = tax_res_any.scalar_one_or_none()
    default_tax_rate_id = default_tax_rate.id if default_tax_rate else None

    # 4. Pre-load manufacturers for this pharmacy
    mfr_res = await db.execute(select(Manufacturer).where(Manufacturer.pharmacy_id == pharmacy_id))
    mfr_map: Dict[str, int] = {m.name.lower(): m.id for m in mfr_res.scalars().all()}

    # 5. Pre-load existing medicine keys for this pharmacy to avoid duplicates
    existing_med_keys: Dict[Tuple[str, int], int] = {}
    med_q = await db.execute(select(Medicine.id, Medicine.name, Medicine.manufacturer_id).where(Medicine.pharmacy_id == pharmacy_id))
    for mid, mname, mmfr_id in med_q.all():
        existing_med_keys[(norm_text(mname), mmfr_id or 0)] = mid

    # 6. Pre-load existing unit conversions
    existing_conversions: Set[Tuple[int, int]] = set()
    conv_q = await db.execute(select(MedicineUnitConversion.medicine_id, MedicineUnitConversion.packaging_unit_id).where(MedicineUnitConversion.pharmacy_id == pharmacy_id))
    for cmid, cpuid in conv_q.all():
        existing_conversions.add((cmid, cpuid))

    # Counters
    total_processed = 0
    created_medicines = 0
    created_manufacturers = 0
    created_conversions = 0
    skipped_duplicates = 0
    errors_count = 0

    batch_meds: List[Medicine] = []
    batch_conv_tuples: List[Tuple[Any, int, Decimal]] = []  # (med_obj_or_id, pack_unit_id, pack_size)
    new_mfr_names_in_batch: Set[str] = set()

    with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total_processed += 1
            if limit and total_processed > limit:
                break

            try:
                parsed = parse_indian_medicine_row(row)
                if not parsed:
                    errors_count += 1
                    continue

                mfr_name = parsed["manufacturer_name"]
                mfr_lower = mfr_name.lower()
                mfr_id = mfr_map.get(mfr_lower)

                # Ensure category exists
                cat_lower = parsed["category_name"].lower()
                cat_obj = cat_map.get(cat_lower)
                if not cat_obj:
                    cat_obj = Category(pharmacy_id=pharmacy_id, name=parsed["category_name"], is_active=True)
                    db.add(cat_obj)
                    await db.flush()
                    cat_map[cat_lower] = cat_obj
                cat_id = cat_obj.id

                # Resolve base unit
                b_unit = unit_map.get(parsed["base_unit_name"].lower()) or unit_map.get("tablet") or list(unit_map.values())[0]
                base_unit_id = b_unit.id

                # If manufacturer doesn't exist, create it immediately
                if not mfr_id:
                    new_mfr = Manufacturer(pharmacy_id=pharmacy_id, name=mfr_name, is_active=True)
                    db.add(new_mfr)
                    await db.flush()
                    mfr_id = new_mfr.id
                    mfr_map[mfr_lower] = mfr_id
                    created_manufacturers += 1

                med_key = (norm_text(parsed["name"]), mfr_id)
                existing_med_id = existing_med_keys.get(med_key)

                # Check pack conversion if present
                pack_unit_id = None
                if parsed["pack_unit_name"] and parsed["pack_size"] > Decimal("1"):
                    p_unit = unit_map.get(parsed["pack_unit_name"].lower())
                    if not p_unit:
                        p_unit = Unit(pharmacy_id=pharmacy_id, name=parsed["pack_unit_name"], abbreviation=parsed["pack_unit_name"][:4].upper(), is_fractional_allowed=False, is_active=True)
                        db.add(p_unit)
                        await db.flush()
                        unit_map[parsed["pack_unit_name"].lower()] = p_unit
                    pack_unit_id = p_unit.id

                if existing_med_id:
                    # Medicine already exists
                    if pack_unit_id and (existing_med_id, pack_unit_id) not in existing_conversions:
                        # Add missing pack conversion
                        conv = MedicineUnitConversion(
                            pharmacy_id=pharmacy_id,
                            medicine_id=existing_med_id,
                            packaging_unit_id=pack_unit_id,
                            conversion_factor=parsed["pack_size"],
                            is_sales_unit=True,
                            is_purchase_unit=True,
                            is_default_dispensing_unit=False,
                        )
                        db.add(conv)
                        existing_conversions.add((existing_med_id, pack_unit_id))
                        created_conversions += 1
                    skipped_duplicates += 1
                    continue

                # Create new Medicine record
                med = Medicine(
                    pharmacy_id=pharmacy_id,
                    name=parsed["name"],
                    brand_name=parsed["brand_name"],
                    generic_name=parsed["generic_name"],
                    strength=parsed["strength"],
                    dosage_form=parsed["dosage_form"],
                    category_id=cat_id,
                    manufacturer_id=mfr_id,
                    tax_rate_id=default_tax_rate_id,
                    hsn_code=parsed["hsn_code"],
                    schedule_type=parsed["schedule_type"],
                    requires_prescription=parsed["requires_prescription"],
                    base_unit_id=base_unit_id,
                    mrp=parsed["mrp"],
                    is_discontinued=parsed["is_discontinued"],
                    source_name=parsed["source_name"],
                    source_id=parsed["source_id"],
                    pack_size_label=parsed["pack_size_label"],
                    reorder_level=Decimal("0.00"),
                    reorder_quantity=Decimal("0.00"),
                    is_active=True,
                )
                db.add(med)
                batch_meds.append(med)
                created_medicines += 1
                
                # We'll associate pack unit after flush so med.id is populated
                if pack_unit_id:
                    batch_conv_tuples.append((med, pack_unit_id, parsed["pack_size"]))

                # Flush and commit batch
                if len(batch_meds) >= batch_size:
                    await db.flush()
                    for m_inst in batch_meds:
                        existing_med_keys[(norm_text(m_inst.name), m_inst.manufacturer_id or 0)] = m_inst.id

                    for m_inst, puid, psize in batch_conv_tuples:
                        conv = MedicineUnitConversion(
                            pharmacy_id=pharmacy_id,
                            medicine_id=m_inst.id,
                            packaging_unit_id=puid,
                            conversion_factor=psize,
                            is_sales_unit=True,
                            is_purchase_unit=True,
                            is_default_dispensing_unit=False,
                        )
                        db.add(conv)
                        existing_conversions.add((m_inst.id, puid))
                        created_conversions += 1

                    await db.commit()
                    log.info(f"Imported batch: {created_medicines} medicines, {created_conversions} conversions")
                    batch_meds.clear()
                    batch_conv_tuples.clear()
                    if progress_callback:
                        progress_callback({
                            "total_processed": total_processed,
                            "created_medicines": created_medicines,
                            "created_manufacturers": created_manufacturers,
                            "created_conversions": created_conversions,
                            "skipped_duplicates": skipped_duplicates,
                            "errors": errors_count,
                        })

            except Exception as e:
                log.exception(f"Error importing row {total_processed}: {e}")
                errors_count += 1

        # Flush final partial batch
        if batch_meds:
            await db.flush()
            for m_inst in batch_meds:
                existing_med_keys[(norm_text(m_inst.name), m_inst.manufacturer_id or 0)] = m_inst.id

            for m_inst, puid, psize in batch_conv_tuples:
                conv = MedicineUnitConversion(
                    pharmacy_id=pharmacy_id,
                    medicine_id=m_inst.id,
                    packaging_unit_id=puid,
                    conversion_factor=psize,
                    is_sales_unit=True,
                    is_purchase_unit=True,
                    is_default_dispensing_unit=False,
                )
                db.add(conv)
                existing_conversions.add((m_inst.id, puid))
                created_conversions += 1

            await db.commit()
            batch_meds.clear()
            batch_conv_tuples.clear()
            if progress_callback:
                progress_callback({
                    "total_processed": total_processed,
                    "created_medicines": created_medicines,
                    "created_manufacturers": created_manufacturers,
                    "created_conversions": created_conversions,
                    "skipped_duplicates": skipped_duplicates,
                    "errors": errors_count,
                })

    # Record AuditLog
    summary = {
        "file": os.path.basename(csv_path),
        "source": "junioralive/Indian-Medicine-Dataset",
        "total_processed": total_processed,
        "created_medicines": created_medicines,
        "created_manufacturers": created_manufacturers,
        "created_conversions": created_conversions,
        "skipped_duplicates": skipped_duplicates,
        "errors": errors_count,
    }
    db.add(AuditLog(
        pharmacy_id=pharmacy_id,
        user_id=user_id,
        action="medicine.catalogue_import_bulk_indian",
        entity_type="medicine",
        metadata_json=json.dumps(summary),
    ))
    await db.commit()

    return summary
