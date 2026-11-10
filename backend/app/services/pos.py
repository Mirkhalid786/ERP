"""
POS engine: saleable-stock lookup, FEFO batch allocation and GST-inclusive pricing.

The same `plan_sale` runs for the live quote shown at the counter and for the
final sale (with row locks), so the cashier never sees totals that differ from
the invoice that is saved.

Pricing rules (MRP retail mode)
-------------------------------
* Batch rates are stored per `price_unit` (e.g. per Strip of 15); they are
  converted to the sale unit chosen at the counter: rate = round2(rate_per_base × factor).
* Selling rates are GST-inclusive (Legal Metrology: MRP includes all taxes).
  Line gross = round2(qty_base × rate / factor); discount = round2(gross × %);
  the GST inside (gross - discount) is split out with the inclusive formula.
* Intra-state sales split GST into CGST + SGST; a customer GSTIN from another
  state makes the sale inter-state (IGST).
* The bill total is rounded to the nearest rupee when invoice.round_to_rupee is on.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Optional, Sequence

from fastapi import HTTPException, status
from sqlalchemy import false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.auth import Pharmacy
from app.models.inventory import (
    Manufacturer, Medicine, MedicineBarcode, MedicineBatch, MedicineUnitConversion, Unit,
)
from app.models.sales import Customer
from app.services import app_settings
from app.services.purchasing import normalize_state_code
from app.services.sales_common import ZERO, four, money, today_local
from app.services.tax import GstTaxCalculationService
from app.services.units import UnitConversionService

BASE_RX_SCHEDULES = ("H", "H1", "X", "NARCOTIC")


def batch_rate_per_base(batch: MedicineBatch, rate: Decimal) -> Decimal:
    factor = Decimal(str(batch.price_conversion_factor or 1))
    return Decimal(str(rate)) / factor


def saleable_filter(pharmacy_id: int, cutoff: date):
    """Conditions for a batch that may be sold: active, released, unexpired, in stock."""
    return (
        MedicineBatch.pharmacy_id == pharmacy_id,
        MedicineBatch.is_active.is_(True),
        MedicineBatch.stock_status == "ACTIVE",
        MedicineBatch.expiry_date > cutoff,
        MedicineBatch.current_stock_base > 0,
    )


async def expiry_cutoff(db: AsyncSession, pharmacy_id: int) -> date:
    """Batches expiring on or before this date are not sold."""
    min_days = await app_settings.get_int(db, pharmacy_id, "pos.min_days_to_expiry")
    return today_local() + timedelta(days=max(0, min_days))


async def rx_schedules(db: AsyncSession, pharmacy_id: int) -> tuple:
    return tuple(await app_settings.get_list(db, pharmacy_id, "rx.prescription_schedules"))


def needs_prescription(medicine: Medicine, schedules: Sequence[str]) -> bool:
    return bool(medicine.requires_prescription) or (medicine.schedule_type or "").upper() in schedules


# ── Search ─────────────────────────────────────────────────────────────────────

def _unit_options(medicine: Medicine) -> List[dict]:
    options = [{
        "unit_id": medicine.base_unit_id,
        "unit_name": medicine.base_unit.name if medicine.base_unit else "Unit",
        "conversion_factor": Decimal("1.0000"),
        "is_base": True,
        "is_default": False,
    }]
    for conv in medicine.conversions:
        if conv.is_sales_unit and conv.packaging_unit_id != medicine.base_unit_id:
            options.append({
                "unit_id": conv.packaging_unit_id,
                "unit_name": conv.packaging_unit.name if conv.packaging_unit else "Pack",
                "conversion_factor": conv.conversion_factor,
                "is_base": False,
                "is_default": conv.is_default_dispensing_unit,
            })
    if not any(o["is_default"] for o in options):
        options[0]["is_default"] = True
    return options


def _batch_view(batch: MedicineBatch) -> dict:
    return {
        "batch_id": batch.id,
        "batch_number": batch.batch_number,
        "expiry_date": batch.expiry_date,
        "stock_base": batch.current_stock_base,
        "mrp_per_base": four(batch_rate_per_base(batch, batch.mrp)),
        "selling_rate_per_base": four(batch_rate_per_base(batch, batch.default_selling_rate)),
        "rack_location": batch.rack_location,
    }


async def search_saleable(db: AsyncSession, pharmacy_id: int, query: str, limit: int = 20) -> List[dict]:
    """
    Find medicines by name, brand, generic/composition, manufacturer or barcode.
    An exact barcode match is returned first (scanners type the code and press Enter).
    """
    term = query.strip()
    if not term:
        return []
    like = f"%{term}%"

    barcode_ids = set((await db.execute(
        select(MedicineBarcode.medicine_id).where(
            MedicineBarcode.pharmacy_id == pharmacy_id, MedicineBarcode.barcode == term,
        )
    )).scalars().all())
    barcode_ids |= set((await db.execute(
        select(Medicine.id).where(Medicine.pharmacy_id == pharmacy_id, Medicine.barcode == term)
    )).scalars().all())

    stmt = (
        select(Medicine)
        .outerjoin(Manufacturer, Manufacturer.id == Medicine.manufacturer_id)
        .options(
            selectinload(Medicine.base_unit),
            selectinload(Medicine.conversions).selectinload(MedicineUnitConversion.packaging_unit),
            selectinload(Medicine.tax_rate),
            selectinload(Medicine.manufacturer),
        )
        .where(
            Medicine.pharmacy_id == pharmacy_id,
            Medicine.is_active.is_(True),
            or_(
                Medicine.id.in_(barcode_ids) if barcode_ids else false(),
                Medicine.name.ilike(like),
                Medicine.brand_name.ilike(like),
                Medicine.generic_name.ilike(like),
                Manufacturer.name.ilike(like),
            ),
        )
        .order_by(Medicine.name)
        .limit(limit)
    )
    medicines = list((await db.execute(stmt)).scalars().unique().all())
    medicines.sort(key=lambda m: (m.id not in barcode_ids, m.name.lower()))

    cutoff = await expiry_cutoff(db, pharmacy_id)
    schedules = await rx_schedules(db, pharmacy_id)
    ids = [m.id for m in medicines]
    batches_by_med: Dict[int, List[MedicineBatch]] = {i: [] for i in ids}
    if ids:
        rows = (await db.execute(
            select(MedicineBatch)
            .where(MedicineBatch.medicine_id.in_(ids), *saleable_filter(pharmacy_id, cutoff))
            .order_by(MedicineBatch.expiry_date, MedicineBatch.id)
        )).scalars().all()
        for b in rows:
            batches_by_med[b.medicine_id].append(b)

    results = []
    for m in medicines:
        batches = batches_by_med[m.id]
        results.append({
            "medicine_id": m.id,
            "name": m.name,
            "brand_name": m.brand_name,
            "generic_name": m.generic_name,
            "strength": m.strength,
            "dosage_form": m.dosage_form,
            "manufacturer": m.manufacturer.name if m.manufacturer else None,
            "schedule_type": m.schedule_type,
            "requires_prescription": needs_prescription(m, schedules),
            "tax_rate": m.tax_rate.rate_percentage if m.tax_rate else ZERO,
            "base_unit_id": m.base_unit_id,
            "base_unit_name": m.base_unit.name if m.base_unit else "Unit",
            "fractional_allowed": bool(m.base_unit and m.base_unit.is_fractional_allowed),
            "units": _unit_options(m),
            "total_stock_base": sum((b.current_stock_base for b in batches), ZERO),
            "batches": [_batch_view(b) for b in batches],
            "matched_barcode": m.id in barcode_ids,
        })
    return results


# ── Sale planning ──────────────────────────────────────────────────────────────

@dataclass
class PlannedItem:
    line_no: int
    cart_line: int
    medicine: Medicine
    batch: MedicineBatch
    unit_id: int
    unit_name: str
    factor: Decimal
    quantity: Decimal
    quantity_base: Decimal
    mrp: Decimal
    selling_rate: Decimal
    gross: Decimal
    discount_percent: Decimal
    discount: Decimal
    taxable: Decimal
    tax_rate: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal
    tax: Decimal
    line_total: Decimal
    cost_per_base: Decimal
    cost_total: Decimal


@dataclass
class SalePlan:
    items: List[PlannedItem] = field(default_factory=list)
    is_inter_state: bool = False
    gross: Decimal = ZERO
    discount: Decimal = ZERO
    taxable: Decimal = ZERO
    cgst: Decimal = ZERO
    sgst: Decimal = ZERO
    igst: Decimal = ZERO
    tax: Decimal = ZERO
    round_off: Decimal = ZERO
    grand_total: Decimal = ZERO
    cost_total: Decimal = ZERO
    rx_medicines: List[Medicine] = field(default_factory=list)
    discount_used: bool = False
    batches: Dict[int, MedicineBatch] = field(default_factory=dict)


async def plan_sale(
    db: AsyncSession,
    pharmacy_id: int,
    lines: Sequence,
    customer: Optional[Customer],
    lock: bool,
) -> SalePlan:
    """
    Validate the cart and allocate stock. With lock=True the candidate batches are
    locked (SELECT ... FOR UPDATE, in id order to avoid deadlocks) so the stock
    figures used here cannot change before the sale commits.
    """
    pharmacy = (await db.execute(select(Pharmacy).where(Pharmacy.id == pharmacy_id))).scalar_one()
    home_state = normalize_state_code(pharmacy.state_code, pharmacy.gstin)
    customer_state = normalize_state_code(customer.state_code, customer.gstin) if customer else None
    plan = SalePlan(is_inter_state=bool(home_state and customer_state and home_state != customer_state))

    max_discount = await app_settings.get_decimal(db, pharmacy_id, "pos.max_discount_percent")
    schedules = await rx_schedules(db, pharmacy_id)
    cutoff = await expiry_cutoff(db, pharmacy_id)

    medicine_ids = sorted({line.medicine_id for line in lines})
    medicines = {
        m.id: m for m in (await db.execute(
            select(Medicine)
            .options(
                selectinload(Medicine.base_unit),
                selectinload(Medicine.tax_rate),
                selectinload(Medicine.conversions).selectinload(MedicineUnitConversion.packaging_unit),
            )
            .where(Medicine.pharmacy_id == pharmacy_id, Medicine.id.in_(medicine_ids))
        )).scalars().all()
    }
    for mid in medicine_ids:
        if mid not in medicines:
            raise HTTPException(status_code=404, detail=f"Medicine ID {mid} not found")
        if not medicines[mid].is_active:
            raise HTTPException(status_code=422, detail=f"'{medicines[mid].name}' is inactive and cannot be sold")

    batch_stmt = (
        select(MedicineBatch)
        .where(MedicineBatch.medicine_id.in_(medicine_ids), *saleable_filter(pharmacy_id, cutoff))
        .order_by(MedicineBatch.id)
    )
    if lock:
        batch_stmt = batch_stmt.with_for_update()
    eligible = list((await db.execute(batch_stmt)).scalars().all())
    available: Dict[int, Decimal] = {b.id: Decimal(str(b.current_stock_base)) for b in eligible}
    plan.batches = {b.id: b for b in eligible}
    fefo: Dict[int, List[MedicineBatch]] = {}
    for b in sorted(eligible, key=lambda x: (x.expiry_date, x.id)):
        fefo.setdefault(b.medicine_id, []).append(b)

    line_no = 0
    seen_rx = set()
    for cart_index, line in enumerate(lines):
        medicine = medicines[line.medicine_id]
        units = {u["unit_id"]: u for u in _unit_options(medicine)}
        if line.unit_id not in units:
            raise HTTPException(
                status_code=422,
                detail=f"Unit {line.unit_id} is not a configured sales unit for '{medicine.name}'",
            )
        unit = units[line.unit_id]
        factor = Decimal(str(unit["conversion_factor"]))
        qty = Decimal(str(line.quantity))
        qty_base = UnitConversionService.to_base_units(qty, factor)
        if qty_base <= ZERO or qty_base != (qty * factor):
            raise HTTPException(status_code=422, detail=f"Quantity {qty} {unit['unit_name']} cannot be converted exactly")
        fractional = bool(medicine.base_unit and medicine.base_unit.is_fractional_allowed)
        if not UnitConversionService.validate_whole_units(qty_base, fractional):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"'{medicine.name}': {qty} {unit['unit_name']} = {qty_base} "
                    f"{medicine.base_unit.name if medicine.base_unit else 'units'}, which cannot be split"
                ),
            )

        discount_pct = Decimal(str(line.discount_percent))
        if discount_pct > ZERO:
            plan.discount_used = True
            if discount_pct > max_discount:
                raise HTTPException(
                    status_code=422,
                    detail=f"Discount {discount_pct}% exceeds the {max_discount}% maximum allowed",
                )

        if needs_prescription(medicine, schedules) and medicine.id not in seen_rx:
            seen_rx.add(medicine.id)
            plan.rx_medicines.append(medicine)

        # Choose batches: the pinned one, or FEFO across eligible batches
        if line.batch_id:
            pinned = plan.batches.get(line.batch_id)
            if pinned is None or pinned.medicine_id != medicine.id:
                raise HTTPException(
                    status_code=422,
                    detail=f"Batch {line.batch_id} of '{medicine.name}' is not saleable (expired, quarantined, recalled or out of stock)",
                )
            candidates = [pinned]
        else:
            candidates = fefo.get(medicine.id, [])

        remaining = qty_base
        for batch in candidates:
            if remaining <= ZERO:
                break
            take = min(available[batch.id], remaining)
            if take <= ZERO:
                continue
            available[batch.id] -= take
            remaining -= take
            line_no += 1
            plan.items.append(_price_item(line_no, cart_index, medicine, batch, unit, factor, take, discount_pct,
                                          plan.is_inter_state))

        if remaining > ZERO:
            have = qty_base - remaining
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Insufficient saleable stock for '{medicine.name}'"
                    + (f" in batch {candidates[0].batch_number}" if line.batch_id else "")
                    + f": requested {qty_base}, available {have} "
                    f"{medicine.base_unit.name if medicine.base_unit else 'units'}"
                ),
            )

    for it in plan.items:
        plan.gross += it.gross
        plan.discount += it.discount
        plan.taxable += it.taxable
        plan.cgst += it.cgst
        plan.sgst += it.sgst
        plan.igst += it.igst
        plan.tax += it.tax
        plan.cost_total += it.cost_total
    net = sum((it.line_total for it in plan.items), ZERO)
    if await app_settings.get_bool(db, pharmacy_id, "invoice.round_to_rupee"):
        plan.grand_total = net.quantize(Decimal("1"), rounding=ROUND_HALF_UP).quantize(Decimal("0.01"))
    else:
        plan.grand_total = money(net)
    plan.round_off = money(plan.grand_total - net)
    return plan


def _price_item(
    line_no: int, cart_line: int, medicine: Medicine, batch: MedicineBatch, unit: dict, factor: Decimal,
    qty_base: Decimal, discount_pct: Decimal, inter_state: bool,
) -> PlannedItem:
    rate = money(batch_rate_per_base(batch, batch.default_selling_rate) * factor)
    mrp = money(batch_rate_per_base(batch, batch.mrp) * factor)
    gross = money(qty_base * rate / factor)
    discount = money(gross * discount_pct / Decimal("100"))
    net = gross - discount
    tax_rate = Decimal(str(medicine.tax_rate.rate_percentage)) if medicine.tax_rate else ZERO
    tax = GstTaxCalculationService.calculate_tax(
        amount=net, rate_percentage=tax_rate, is_tax_inclusive=True, is_inter_state=inter_state,
    )
    cost_per_base = Decimal(str(batch.cost_per_base_unit))
    return PlannedItem(
        line_no=line_no,
        cart_line=cart_line,
        medicine=medicine,
        batch=batch,
        unit_id=unit["unit_id"],
        unit_name=unit["unit_name"],
        factor=factor,
        quantity=four(qty_base / factor),
        quantity_base=money(qty_base),
        mrp=mrp,
        selling_rate=rate,
        gross=gross,
        discount_percent=discount_pct,
        discount=discount,
        taxable=tax["taxable_value"],
        tax_rate=tax_rate,
        cgst=tax["cgst_amount"],
        sgst=tax["sgst_amount"],
        igst=tax["igst_amount"],
        tax=tax["total_tax"],
        line_total=money(net),
        cost_per_base=cost_per_base,
        cost_total=money(qty_base * cost_per_base),
    )


def plan_to_quote(plan: SalePlan) -> dict:
    return {
        "is_inter_state": plan.is_inter_state,
        "items": [{
            "line_no": it.line_no,
            "cart_line": it.cart_line,
            "medicine_id": it.medicine.id,
            "medicine_name": it.medicine.name,
            "batch_id": it.batch.id,
            "batch_number": it.batch.batch_number,
            "expiry_date": it.batch.expiry_date,
            "unit_id": it.unit_id,
            "unit_name": it.unit_name,
            "quantity": it.quantity,
            "quantity_base": it.quantity_base,
            "mrp": it.mrp,
            "selling_rate": it.selling_rate,
            "gross_amount": it.gross,
            "discount_percent": it.discount_percent,
            "discount_amount": it.discount,
            "taxable_amount": it.taxable,
            "tax_rate": it.tax_rate,
            "cgst_amount": it.cgst,
            "sgst_amount": it.sgst,
            "igst_amount": it.igst,
            "tax_amount": it.tax,
            "line_total": it.line_total,
        } for it in plan.items],
        "gross_amount": plan.gross,
        "discount_total": plan.discount,
        "taxable_total": plan.taxable,
        "cgst_total": plan.cgst,
        "sgst_total": plan.sgst,
        "igst_total": plan.igst,
        "tax_total": plan.tax,
        "round_off": plan.round_off,
        "grand_total": plan.grand_total,
        "prescription_required": bool(plan.rx_medicines),
        "prescription_medicines": [m.name for m in plan.rx_medicines],
        "strict_prescription_medicines": [],
    }
