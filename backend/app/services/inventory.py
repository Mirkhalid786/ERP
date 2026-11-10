"""
Inventory & Batch Management Service.

Enforces:
  - Atomic stock balance updates + immutable movement ledger writes.
  - Row-level locking on batches to prevent race conditions during concurrent adjustments.
  - Expiry validation (expired batches prohibited from active dispensing).
  - Deletion protection on batches with movement history.
  - Low-stock and expiry alert calculations.
"""
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional, Tuple, Dict, Any

from fastapi import HTTPException, status
from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from app.services import gl_posting
from sqlalchemy.orm import selectinload

from app.models.inventory import (
    Medicine, MedicineBatch, StockMovement, StockAdjustment, StockAdjustmentItem,
    MedicineUnitConversion, Unit, TaxRate, Category, Manufacturer,
)
from app.models.auth import AuditLog
from app.core.clock import business_now
from app.services.numbering import next_document_number
from app.services.units import UnitConversionService

TWO_PLACES = Decimal("0.01")
FOUR_PLACES = Decimal("0.0001")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class InventoryService:
    @staticmethod
    async def seed_default_inventory_masters(db: AsyncSession, pharmacy_id: int) -> None:
        """Seed standard tax rates, units, categories, and manufacturers for a pharmacy."""
        # 1. Tax Rates
        tax_slabs = [
            ("GST 0%", Decimal("0.00"), Decimal("0.00"), Decimal("0.00"), Decimal("0.00")),
            ("GST 5%", Decimal("5.00"), Decimal("2.50"), Decimal("2.50"), Decimal("5.00")),
            ("GST 12%", Decimal("12.00"), Decimal("6.00"), Decimal("6.00"), Decimal("12.00")),
            ("GST 18%", Decimal("18.00"), Decimal("9.00"), Decimal("9.00"), Decimal("18.00")),
            ("GST 28%", Decimal("28.00"), Decimal("14.00"), Decimal("14.00"), Decimal("28.00")),
            ("Exempt", Decimal("0.00"), Decimal("0.00"), Decimal("0.00"), Decimal("0.00")),
        ]
        for name, rate, cgst, sgst, igst in tax_slabs:
            q = select(TaxRate).where(TaxRate.pharmacy_id == pharmacy_id, TaxRate.name == name)
            if not (await db.execute(q)).scalar_one_or_none():
                db.add(TaxRate(
                    pharmacy_id=pharmacy_id,
                    name=name,
                    rate_percentage=rate,
                    cgst_percentage=cgst,
                    sgst_percentage=sgst,
                    igst_percentage=igst,
                    is_active=True,
                ))

        # 2. Units
        default_units = [
            ("Tablet", "TAB", False),
            ("Capsule", "CAP", False),
            ("Strip", "STP", False),
            ("Box", "BOX", False),
            ("Bottle", "BTL", False),
            ("Vial", "VIAL", False),
            ("Ampoule", "AMP", False),
            ("Tube", "TUB", False),
            ("Milliliter", "ML", True),
            ("Gram", "GM", True),
            ("Piece", "PC", False),
        ]
        for name, abbr, fractional in default_units:
            q = select(Unit).where(Unit.pharmacy_id == pharmacy_id, Unit.name == name)
            if not (await db.execute(q)).scalar_one_or_none():
                db.add(Unit(
                    pharmacy_id=pharmacy_id,
                    name=name,
                    abbreviation=abbr,
                    is_fractional_allowed=fractional,
                    is_active=True,
                ))

        # 3. Categories
        default_cats = [
            ("Antibiotics", "Antimicrobial pharmaceuticals"),
            ("Analgesics & Antipyretics", "Pain relief and fever reduction"),
            ("Gastrointestinal & Antacids", "Digestive and acid reflux care"),
            ("Cardiovascular & Antihypertensive", "Heart and blood pressure care"),
            ("Antidiabetic", "Blood sugar management"),
            ("Respiratory & Antiallergic", "Cold, cough and allergy care"),
            ("Dermatological & Topicals", "Skin care ointments and creams"),
            ("Vitamins & Supplements", "Nutritional supplements"),
            ("Surgicals & Consumables", "Cotton, syringes, bandages"),
            ("Over-The-Counter (OTC)", "General wellness products"),
        ]
        for name, desc in default_cats:
            q = select(Category).where(Category.pharmacy_id == pharmacy_id, Category.name == name)
            if not (await db.execute(q)).scalar_one_or_none():
                db.add(Category(
                    pharmacy_id=pharmacy_id,
                    name=name,
                    description=desc,
                    is_active=True,
                ))

        # 4. Manufacturers
        default_mfgs = [
            ("Cipla Ltd", "CIPLA"),
            ("Sun Pharmaceutical Industries Ltd", "SUN"),
            ("Dr. Reddy's Laboratories", "DRREDDY"),
            ("Abbott Healthcare", "ABBOTT"),
            ("Mankind Pharma", "MANKIND"),
            ("Alkem Laboratories", "ALKEM"),
            ("Torrent Pharmaceuticals", "TORRENT"),
            ("Lupin Ltd", "LUPIN"),
        ]
        for name, code in default_mfgs:
            q = select(Manufacturer).where(Manufacturer.pharmacy_id == pharmacy_id, Manufacturer.name == name)
            if not (await db.execute(q)).scalar_one_or_none():
                db.add(Manufacturer(
                    pharmacy_id=pharmacy_id,
                    name=name,
                    short_code=code,
                    is_active=True,
                ))
        await db.flush()

    @staticmethod
    async def create_batch(
        db: AsyncSession,
        pharmacy_id: int,
        medicine_id: int,
        batch_number: str,
        expiry_date: date,
        mrp: Decimal,
        purchase_rate: Decimal,
        default_selling_rate: Decimal,
        cost_per_base_unit: Optional[Decimal] = None,
        manufacturing_date: Optional[date] = None,
        rack_location: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> MedicineBatch:
        """Create a new batch for a medicine."""
        # Verify medicine exists and belongs to pharmacy
        med_query = select(Medicine).where(
            Medicine.id == medicine_id,
            Medicine.pharmacy_id == pharmacy_id,
        )
        med_result = await db.execute(med_query)
        medicine = med_result.scalar_one_or_none()
        if not medicine:
            raise HTTPException(status_code=404, detail="Medicine not found")

        # Check duplicate batch number for this medicine
        dup_query = select(MedicineBatch).where(
            MedicineBatch.pharmacy_id == pharmacy_id,
            MedicineBatch.medicine_id == medicine_id,
            MedicineBatch.batch_number == batch_number.strip().upper(),
        )
        dup_result = await db.execute(dup_query)
        if dup_result.scalar_one_or_none():
            raise HTTPException(
                status_code=409,
                detail=f"Batch '{batch_number}' already exists for this medicine",
            )

        # Validate rates
        if default_selling_rate is None:
            default_selling_rate = mrp

        if mrp < Decimal("0") or purchase_rate < Decimal("0") or default_selling_rate < Decimal("0"):
            raise HTTPException(status_code=422, detail="Rates cannot be negative")

        if default_selling_rate > mrp:
            raise HTTPException(
                status_code=422,
                detail="Default selling rate cannot exceed MRP under Legal Metrology Act / DPCO",
            )

        if cost_per_base_unit is None:
            # Default to purchase_rate if not provided
            cost_per_base_unit = purchase_rate.quantize(FOUR_PLACES)

        batch = MedicineBatch(
            pharmacy_id=pharmacy_id,
            medicine_id=medicine_id,
            batch_number=batch_number.strip().upper(),
            manufacturing_date=manufacturing_date,
            expiry_date=expiry_date,
            mrp=mrp.quantize(TWO_PLACES),
            purchase_rate=purchase_rate.quantize(TWO_PLACES),
            cost_per_base_unit=cost_per_base_unit.quantize(FOUR_PLACES),
            default_selling_rate=default_selling_rate.quantize(TWO_PLACES),
            rack_location=rack_location.strip() if rack_location else None,
            current_stock_base=Decimal("0.00"),
            price_unit_id=medicine.base_unit_id,  # Manually created batches are priced per base unit
            price_conversion_factor=Decimal("1.0000"),
            is_active=True,
        )
        db.add(batch)
        await db.flush()

        # Audit log
        db.add(AuditLog(
            pharmacy_id=pharmacy_id,
            user_id=user_id,
            action="batch.create",
            entity_type="medicine_batch",
            entity_id=str(batch.id),
            metadata_json=json.dumps({
                "batch_number": batch.batch_number,
                "medicine_id": medicine_id,
                "expiry_date": str(expiry_date),
                "mrp": str(batch.mrp),
            }),
        ))
        return batch

    @staticmethod
    async def record_opening_stock(
        db: AsyncSession,
        pharmacy_id: int,
        medicine_id: int,
        batch_number: str,
        expiry_date: date,
        quantity_in_pack: Decimal,
        mrp: Decimal,
        purchase_rate: Decimal,
        default_selling_rate: Decimal,
        packaging_unit_id: Optional[int] = None,
        manufacturing_date: Optional[date] = None,
        rack_location: Optional[str] = None,
        notes: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> Tuple[MedicineBatch, StockMovement]:
        """
        Record opening stock for a medicine batch.
        Calculates base unit quantities and atomically records an OPENING_STOCK movement.
        """
        if quantity_in_pack <= Decimal("0"):
            raise HTTPException(status_code=422, detail="Opening stock quantity must be positive")

        # Load medicine and base unit
        med_query = select(Medicine).options(selectinload(Medicine.base_unit)).where(
            Medicine.id == medicine_id,
            Medicine.pharmacy_id == pharmacy_id,
        )
        med_res = await db.execute(med_query)
        medicine = med_res.scalar_one_or_none()
        if not medicine:
            raise HTTPException(status_code=404, detail="Medicine not found")

        if default_selling_rate is None:
            default_selling_rate = mrp

        # Determine conversion factor to base units
        conversion_factor = Decimal("1.0000")
        if packaging_unit_id and packaging_unit_id != medicine.base_unit_id:
            conv_query = select(MedicineUnitConversion).where(
                MedicineUnitConversion.medicine_id == medicine_id,
                MedicineUnitConversion.packaging_unit_id == packaging_unit_id,
            )
            conv_res = await db.execute(conv_query)
            conv = conv_res.scalar_one_or_none()
            if not conv:
                raise HTTPException(
                    status_code=422,
                    detail=f"Unit conversion not configured for packaging unit ID {packaging_unit_id}",
                )
            conversion_factor = conv.conversion_factor

        qty_base = UnitConversionService.to_base_units(quantity_in_pack, conversion_factor)
        if not UnitConversionService.validate_whole_units(qty_base, medicine.base_unit.is_fractional_allowed):
            raise HTTPException(
                status_code=422,
                detail=f"Base unit '{medicine.base_unit.name}' does not allow fractional quantities ({qty_base})",
            )

        cost_per_base = (purchase_rate / conversion_factor).quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)
        total_cost = (qty_base * cost_per_base).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        # Find or create batch with row lock
        batch_query = select(MedicineBatch).where(
            MedicineBatch.pharmacy_id == pharmacy_id,
            MedicineBatch.medicine_id == medicine_id,
            MedicineBatch.batch_number == batch_number.strip().upper(),
        ).with_for_update()
        batch_res = await db.execute(batch_query)
        batch = batch_res.scalar_one_or_none()

        if batch is None:
            batch = MedicineBatch(
                pharmacy_id=pharmacy_id,
                medicine_id=medicine_id,
                batch_number=batch_number.strip().upper(),
                manufacturing_date=manufacturing_date,
                expiry_date=expiry_date,
                mrp=mrp.quantize(TWO_PLACES),
                purchase_rate=purchase_rate.quantize(TWO_PLACES),
                cost_per_base_unit=cost_per_base,
                default_selling_rate=default_selling_rate.quantize(TWO_PLACES),
                rack_location=rack_location.strip() if rack_location else None,
                current_stock_base=Decimal("0.00"),
                # Rates were entered per the packaging unit of the opening quantity
                price_unit_id=packaging_unit_id or medicine.base_unit_id,
                price_conversion_factor=conversion_factor,
                is_active=True,
            )
            db.add(batch)
            await db.flush()

        # Update batch balance
        batch.current_stock_base = (batch.current_stock_base + qty_base).quantize(TWO_PLACES)

        # Record immutable stock movement
        movement = StockMovement(
            pharmacy_id=pharmacy_id,
            medicine_id=medicine_id,
            batch_id=batch.id,
            movement_type="OPENING_STOCK",
            reference_id=f"OPN-{batch.id}",
            reference_type="opening_stock",
            quantity_base=qty_base,
            balance_after_base=batch.current_stock_base,
            unit_cost=cost_per_base,
            total_cost=total_cost,
            reason="Opening Stock Intake",
            notes=notes,
            user_id=user_id,
        )
        db.add(movement)

        # Audit log
        db.add(AuditLog(
            pharmacy_id=pharmacy_id,
            user_id=user_id,
            action="stock.opening_stock",
            entity_type="stock_movement",
            entity_id=str(batch.id),
            metadata_json=json.dumps({
                "batch_number": batch.batch_number,
                "medicine_id": medicine_id,
                "quantity_base": str(qty_base),
                "balance_after": str(batch.current_stock_base),
            }),
        ))
        await db.flush()
        return batch, movement

    @staticmethod
    async def record_stock_adjustment(
        db: AsyncSession,
        pharmacy_id: int,
        reason_code: str,
        items_data: List[Dict[str, Any]],
        notes: Optional[str] = None,
        user_id: int = 1,
    ) -> StockAdjustment:
        """
        Record a physical stock adjustment with atomic balance updates and immutable movement logs.
        Row locks are held on all touched batches.
        """
        valid_reasons = {
            "DAMAGE", "BREAKAGE", "DAMAGE_BREAKAGE", "EXPIRY", "THEFT",
            "AUDIT_DISCREPANCY", "PHYSICAL_AUDIT_SURPLUS", "PHYSICAL_AUDIT_SHORTAGE",
            "FOUND", "CORRECTION", "OTHER",
        }
        if reason_code.upper() not in valid_reasons:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid reason_code. Allowed: {', '.join(sorted(valid_reasons))}",
            )

        if not items_data:
            raise HTTPException(status_code=422, detail="Adjustment must contain at least one item")

        # Generate unique adjustment number
        today_str = business_now().strftime("%Y%m%d")
        adj_number = await next_document_number(
            db, pharmacy_id, "ADJ", f"ADJ-{today_str}",
            StockAdjustment.adjustment_number, StockAdjustment.pharmacy_id,
        )

        adjustment = StockAdjustment(
            pharmacy_id=pharmacy_id,
            adjustment_number=adj_number,
            reason_code=reason_code.upper(),
            status="POSTED",
            notes=notes,
            adjusted_by=user_id,
        )
        db.add(adjustment)
        await db.flush()

        for item_data in items_data:
            batch_id = item_data["batch_id"]
            physical_stock = Decimal(str(item_data["physical_stock_base"]))
            item_notes = item_data.get("notes")

            if physical_stock < Decimal("0"):
                raise HTTPException(status_code=422, detail="Physical stock cannot be negative")

            # Lock batch row
            batch_q = select(MedicineBatch).where(
                MedicineBatch.id == batch_id,
                MedicineBatch.pharmacy_id == pharmacy_id,
            ).with_for_update()
            batch_res = await db.execute(batch_q)
            batch = batch_res.scalar_one_or_none()
            if not batch:
                raise HTTPException(status_code=404, detail=f"Batch ID {batch_id} not found")

            system_stock = batch.current_stock_base
            diff_qty = (physical_stock - system_stock).quantize(TWO_PLACES)

            unit_cost = batch.cost_per_base_unit
            total_cost = (abs(diff_qty) * unit_cost).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

            # Create adjustment line item
            adj_item = StockAdjustmentItem(
                adjustment_id=adjustment.id,
                medicine_id=batch.medicine_id,
                batch_id=batch.id,
                system_stock_base=system_stock,
                physical_stock_base=physical_stock,
                adjustment_qty_base=diff_qty,
                unit_cost=unit_cost,
                total_cost=total_cost,
                notes=item_notes,
            )
            db.add(adj_item)

            if diff_qty != Decimal("0"):
                movement_type = "ADJUSTMENT_IN" if diff_qty > Decimal("0") else "ADJUSTMENT_OUT"
                batch.current_stock_base = physical_stock

                # Record movement
                movement = StockMovement(
                    pharmacy_id=pharmacy_id,
                    medicine_id=batch.medicine_id,
                    batch_id=batch.id,
                    movement_type=movement_type,
                    reference_id=adj_number,
                    reference_type="stock_adjustment",
                    quantity_base=diff_qty,  # Signed delta: negative for ADJUSTMENT_OUT
                    balance_after_base=batch.current_stock_base,
                    unit_cost=unit_cost,
                    total_cost=total_cost,
                    reason=f"Stock Adjustment ({reason_code.upper()}): {item_notes or ''}".strip(),
                    notes=item_notes,
                    user_id=user_id,
                )
                db.add(movement)

        # Audit log
        db.add(AuditLog(
            pharmacy_id=pharmacy_id,
            user_id=user_id,
            action="stock.adjustment",
            entity_type="stock_adjustment",
            entity_id=str(adjustment.id),
            metadata_json=json.dumps({
                "adjustment_number": adj_number,
                "reason_code": reason_code,
                "items_count": len(items_data),
            }),
        ))
        await db.flush()
        await gl_posting.record(db, pharmacy_id, user_id, "STOCK_ADJUSTMENT", adjustment.id)
        return adjustment

    @staticmethod
    async def delete_batch(
        db: AsyncSession,
        pharmacy_id: int,
        batch_id: int,
        user_id: int = 1,
    ) -> None:
        """
        Delete a batch only if no historical stock movements reference it.
        """
        batch_q = select(MedicineBatch).where(
            MedicineBatch.id == batch_id,
            MedicineBatch.pharmacy_id == pharmacy_id,
        )
        batch_res = await db.execute(batch_q)
        batch = batch_res.scalar_one_or_none()
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found")

        # Check if movements exist
        mov_q = select(func.count(StockMovement.id)).where(StockMovement.batch_id == batch_id)
        mov_res = await db.execute(mov_q)
        if (mov_res.scalar() or 0) > 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot delete batch with movement history. Historical ledger integrity requires preserving this record.",
            )

        db.add(AuditLog(
            pharmacy_id=pharmacy_id,
            user_id=user_id,
            action="batch.delete",
            entity_type="medicine_batch",
            entity_id=str(batch_id),
            metadata_json=json.dumps({"batch_number": batch.batch_number}),
        ))
        await db.delete(batch)
        await db.flush()
