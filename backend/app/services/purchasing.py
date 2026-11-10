"""
Purchasing, Goods Receipt, Supplier Invoices & Returns Service Layer.

Implements all core business logic for Phase 3:
  - Supplier Management & GSTIN / state-code validation
  - Purchase Orders (Draft -> Submitted -> Approved -> Cancelled; zero inventory mutation)
  - Goods Receipt Inward (GRN) with:
      - Unit conversion & divisibility check
      - Free goods allocated acquisition costing
      - Expiry validation (expired stock rejected from saleable inventory)
      - MRP compliance (selling price cannot exceed MRP)
      - Rejected / damaged goods isolated without increasing saleable stock
      - Idempotent row-locked posting to MedicineBatch and immutable StockMovement
      - PO fulfillment tracking
  - Supplier Purchase Invoices with:
      - Duplicate invoice number validation per supplier
      - Dual tracking: supplier declared bill value vs calculated value discrepancy
      - Intra-state (CGST+SGST) vs Inter-state (IGST) tax computation
  - Supplier Purchase Returns (Debit Notes) with:
      - Returnable quantity validation (<= received accepted - previously returned)
      - Current batch stock availability validation
      - Idempotent row-locked posting reducing stock and writing RETURN_OUTWARD ledger entry
"""
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional, Tuple, Dict, Any

from fastapi import HTTPException, status
from sqlalchemy import select, func, and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.auth import Pharmacy, AuditLog, User
from app.models.inventory import (
    Medicine, MedicineBatch, StockMovement, MedicineUnitConversion,
    Unit, TaxRate, utcnow,
)
from app.models.purchasing import (
    Supplier,
    PurchaseOrder, PurchaseOrderItem,
    GoodsReceipt, GoodsReceiptItem,
    SupplierInvoice, SupplierInvoiceItem,
    SupplierReturn, SupplierReturnItem,
)
from app.schemas.purchasing import (
    SupplierCreate, SupplierUpdate,
    PurchaseOrderCreate, PurchaseOrderUpdate,
    GoodsReceiptCreate,
    SupplierInvoiceCreate, SupplierInvoiceUpdate,
    SupplierReturnCreate,
)
from app.core.clock import business_now
from app.services.numbering import next_document_number
from app.services import gl_posting
from app.services.units import UnitConversionService
from app.services.tax import GstTaxCalculationService
from app.core.clock import today_local

TWO_PLACES = Decimal("0.01")
FOUR_PLACES = Decimal("0.0001")


def round_cur(val: Decimal) -> Decimal:
    return val.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def round_four(val: Decimal) -> Decimal:
    return val.quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)


INDIAN_STATE_CODE_MAP = {
    "JK": "01", "HP": "02", "PB": "03", "CH": "04", "UT": "05", "HR": "06", "DL": "07",
    "RJ": "08", "UP": "09", "BR": "10", "SK": "11", "AR": "12", "NL": "13", "MN": "14",
    "MZ": "15", "TR": "16", "ML": "17", "AS": "18", "WB": "19", "JH": "20", "OR": "21",
    "OD": "21", "CT": "22", "CG": "22", "MP": "23", "GJ": "24", "DD": "26", "DN": "26",
    "MH": "27", "KA": "29", "GA": "30", "LD": "31", "KL": "32", "TN": "33", "PY": "34",
    "AN": "35", "TS": "36", "TG": "36", "AD": "37", "AP": "37", "LA": "38",
}


def normalize_state_code(code: Optional[str], gstin: Optional[str] = None) -> Optional[str]:
    """Normalize Indian GST state code from 2-letter postal code or GSTIN."""
    if gstin and len(gstin) >= 2 and gstin[:2].isdigit():
        return gstin[:2]
    if not code:
        return None
    code_up = code.strip().upper()
    if code_up.isdigit() and len(code_up) == 2:
        return code_up
    return INDIAN_STATE_CODE_MAP.get(code_up, code_up)


def resolve_supplier_state_code(
    gstin: Optional[str], state_code: Optional[str], fallback: Optional[str] = None
) -> Optional[str]:
    """
    Return the supplier's GST state code. With a GSTIN, the state is its first two
    digits and an explicitly given state code must match it (otherwise CGST/SGST vs
    IGST would be decided from conflicting data). Without a GSTIN, the given code
    (or `fallback`) is used as is.
    """
    if gstin and gstin[:2].isdigit():
        gstin_state = gstin[:2]
        if state_code and state_code != gstin_state:
            # Same shape as a request-validation error, so the form shows it on the State code field
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=[{"loc": ["body", "state_code"], "type": "value_error",
                         "msg": f"State code {state_code} does not match the GSTIN, which is registered in state {gstin_state}"}],
            )
        return gstin_state
    return state_code or fallback


# ══════════════════════════════════════════════════════════════════════════════
# 1. Supplier Service
# ══════════════════════════════════════════════════════════════════════════════

class SupplierService:
    @staticmethod
    async def create_supplier(
        db: AsyncSession, pharmacy_id: int, payload: SupplierCreate, user_id: int
    ) -> Supplier:
        name_clean = payload.name.strip()
        # Check duplicate name
        existing = await db.execute(
            select(Supplier).where(
                Supplier.pharmacy_id == pharmacy_id,
                Supplier.name == name_clean,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Supplier with name '{name_clean}' already exists in this pharmacy",
            )

        # The first two GSTIN digits are the registered state; infer or cross-check it
        state_code = resolve_supplier_state_code(payload.gstin, payload.state_code)

        supplier = Supplier(
            pharmacy_id=pharmacy_id,
            name=name_clean,
            legal_name=payload.legal_name.strip() if payload.legal_name else None,
            gstin=payload.gstin,
            state_code=state_code,
            drug_license_no=payload.drug_license_no.strip() if payload.drug_license_no else None,
            contact_person=payload.contact_person.strip() if payload.contact_person else None,
            phone=payload.phone.strip() if payload.phone else None,
            email=payload.email.strip().lower() if payload.email else None,
            address=payload.address.strip() if payload.address else None,
            city=payload.city.strip() if payload.city else None,
            state=payload.state.strip() if payload.state else None,
            pincode=payload.pincode.strip() if payload.pincode else None,
            payment_terms_days=payload.payment_terms_days,
            credit_limit=payload.credit_limit,
            notes=payload.notes,
            is_active=payload.is_active,
        )
        db.add(supplier)
        await db.flush()

        db.add(AuditLog(
            pharmacy_id=pharmacy_id,
            user_id=user_id,
            action="supplier.create",
            entity_type="supplier",
            entity_id=str(supplier.id),
            metadata_json=json.dumps({
                "name": supplier.name,
                "gstin": supplier.gstin,
                "drug_license_no": supplier.drug_license_no,
            }),
        ))
        await db.flush()
        return supplier

    @staticmethod
    async def update_supplier(
        db: AsyncSession, pharmacy_id: int, supplier_id: int, payload: SupplierUpdate, user_id: int
    ) -> Supplier:
        supplier = await SupplierService.get_supplier(db, pharmacy_id, supplier_id)

        update_data = payload.model_dump(exclude_unset=True)
        if "name" in update_data and update_data["name"]:
            name_clean = update_data["name"].strip()
            if name_clean.lower() != supplier.name.lower():
                existing = await db.execute(
                    select(Supplier).where(
                        Supplier.pharmacy_id == pharmacy_id,
                        Supplier.name == name_clean,
                        Supplier.id != supplier_id,
                    )
                )
                if existing.scalar_one_or_none():
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Supplier with name '{name_clean}' already exists",
                    )
            supplier.name = name_clean

        for field in [
            "legal_name", "drug_license_no", "contact_person", "phone",
            "email", "address", "city", "state", "pincode", "notes",
        ]:
            if field in update_data:
                val = update_data[field]
                setattr(supplier, field, val.strip() if isinstance(val, str) else val)

        new_gstin = update_data["gstin"] if "gstin" in update_data else supplier.gstin
        requested_state = update_data.get("state_code") or None
        if "state_code" in update_data and requested_state is None and not new_gstin:
            supplier.state_code = None          # Explicitly cleared; allowed when there is no GSTIN
        if "gstin" in update_data or requested_state:
            # A changed GSTIN re-derives the state unless one is given explicitly;
            # an explicit state must agree with the (new or existing) GSTIN.
            supplier.state_code = resolve_supplier_state_code(
                new_gstin, requested_state.strip() if requested_state else None,
                fallback=None if "gstin" in update_data and new_gstin else supplier.state_code,
            )
            supplier.gstin = new_gstin

        if "payment_terms_days" in update_data and update_data["payment_terms_days"] is not None:
            supplier.payment_terms_days = update_data["payment_terms_days"]

        if "credit_limit" in update_data and update_data["credit_limit"] is not None:
            supplier.credit_limit = update_data["credit_limit"]

        if "is_active" in update_data and update_data["is_active"] is not None:
            supplier.is_active = update_data["is_active"]

        supplier.updated_at = utcnow()

        db.add(AuditLog(
            pharmacy_id=pharmacy_id,
            user_id=user_id,
            action="supplier.update",
            entity_type="supplier",
            entity_id=str(supplier.id),
            metadata_json=json.dumps({"updated_fields": list(update_data.keys())}),
        ))
        await db.flush()
        return supplier

    @staticmethod
    async def get_supplier(db: AsyncSession, pharmacy_id: int, supplier_id: int) -> Supplier:
        res = await db.execute(
            select(Supplier).where(
                Supplier.id == supplier_id,
                Supplier.pharmacy_id == pharmacy_id,
            )
        )
        supplier = res.scalar_one_or_none()
        if not supplier:
            raise HTTPException(status_code=404, detail="Supplier not found")
        return supplier

    @staticmethod
    async def list_suppliers(
        db: AsyncSession,
        pharmacy_id: int,
        search: Optional[str] = None,
        active_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[Supplier], int]:
        stmt = select(Supplier).where(Supplier.pharmacy_id == pharmacy_id)
        if active_only:
            stmt = stmt.where(Supplier.is_active.is_(True))
        if search:
            term = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(
                    Supplier.name.ilike(term),
                    Supplier.legal_name.ilike(term),
                    Supplier.gstin.ilike(term),
                    Supplier.phone.ilike(term),
                    Supplier.city.ilike(term),
                )
            )

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await db.execute(count_stmt)).scalar_one()

        stmt = stmt.order_by(Supplier.name.asc()).offset(offset).limit(limit)
        items = (await db.execute(stmt)).scalars().all()
        return list(items), total

    @staticmethod
    async def delete_supplier(db: AsyncSession, pharmacy_id: int, supplier_id: int, user_id: int) -> None:
        supplier = await SupplierService.get_supplier(db, pharmacy_id, supplier_id)

        # Check references
        for model, label in [
            (PurchaseOrder, "purchase orders"),
            (GoodsReceipt, "goods receipts"),
            (SupplierInvoice, "purchase invoices"),
            (SupplierReturn, "returns"),
        ]:
            ref = (await db.execute(
                select(func.count(model.id)).where(model.supplier_id == supplier_id)
            )).scalar_one()
            if ref > 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Cannot delete supplier referenced by {ref} {label}. Deactivate the supplier instead.",
                )

        db.add(AuditLog(
            pharmacy_id=pharmacy_id,
            user_id=user_id,
            action="supplier.delete",
            entity_type="supplier",
            entity_id=str(supplier.id),
            metadata_json=json.dumps({"name": supplier.name}),
        ))
        await db.delete(supplier)
        await db.flush()


# ══════════════════════════════════════════════════════════════════════════════
# 2. Purchase Order Service
# ══════════════════════════════════════════════════════════════════════════════

class PurchaseOrderService:
    @staticmethod
    async def generate_po_number(db: AsyncSession, pharmacy_id: int) -> str:
        prefix = f"PO-{business_now().strftime('%Y%m')}"
        return await next_document_number(
            db, pharmacy_id, "PO", prefix, PurchaseOrder.po_number, PurchaseOrder.pharmacy_id,
        )

    @staticmethod
    async def create_purchase_order(
        db: AsyncSession, pharmacy_id: int, payload: PurchaseOrderCreate, user_id: int
    ) -> PurchaseOrder:
        # Validate supplier
        supplier = await SupplierService.get_supplier(db, pharmacy_id, payload.supplier_id)
        if not supplier.is_active:
            raise HTTPException(status_code=400, detail="Cannot create purchase order for inactive supplier")

        po_number = await PurchaseOrderService.generate_po_number(db, pharmacy_id)

        po = PurchaseOrder(
            pharmacy_id=pharmacy_id,
            po_number=po_number,
            supplier_id=supplier.id,
            order_date=payload.order_date,
            expected_delivery_date=payload.expected_delivery_date,
            status="DRAFT",
            notes=payload.notes,
            created_by=user_id,
        )
        db.add(po)
        await db.flush()

        subtotal = Decimal("0.00")
        discount_total = Decimal("0.00")
        tax_total = Decimal("0.00")

        for item_data in payload.items:
            # Validate medicine
            med_res = await db.execute(
                select(Medicine).where(
                    Medicine.id == item_data.medicine_id,
                    Medicine.pharmacy_id == pharmacy_id,
                )
            )
            medicine = med_res.scalar_one_or_none()
            if not medicine:
                raise HTTPException(status_code=404, detail=f"Medicine ID {item_data.medicine_id} not found")

            # Validate packaging unit conversion
            conversion_factor = Decimal("1.0000")
            if item_data.packaging_unit_id != medicine.base_unit_id:
                conv_res = await db.execute(
                    select(MedicineUnitConversion).where(
                        MedicineUnitConversion.medicine_id == medicine.id,
                        MedicineUnitConversion.packaging_unit_id == item_data.packaging_unit_id,
                    )
                )
                conv = conv_res.scalar_one_or_none()
                if not conv:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Unit conversion not configured for packaging unit ID {item_data.packaging_unit_id} on medicine '{medicine.name}'",
                    )
                conversion_factor = conv.conversion_factor

            ordered_qty_base = UnitConversionService.to_base_units(item_data.ordered_qty_pack, conversion_factor)

            # Compute line finance
            line_gross = round_cur(item_data.ordered_qty_pack * item_data.purchase_rate)
            line_disc = round_cur(line_gross * (item_data.discount_percent / Decimal("100.00")))
            line_taxable = line_gross - line_disc

            tax_amount = Decimal("0.00")
            if item_data.tax_rate_id:
                tr_res = await db.execute(
                    select(TaxRate).where(
                        TaxRate.id == item_data.tax_rate_id,
                        TaxRate.pharmacy_id == pharmacy_id,
                    )
                )
                tax_rate = tr_res.scalar_one_or_none()
                if tax_rate:
                    tax_calc = GstTaxCalculationService.calculate_tax(
                        amount=line_taxable,
                        rate_percentage=tax_rate.rate_percentage,
                        is_tax_inclusive=False,
                    )
                    tax_amount = tax_calc["total_tax"]

            line_total = line_taxable + tax_amount

            subtotal += line_gross
            discount_total += line_disc
            tax_total += tax_amount

            item = PurchaseOrderItem(
                po_id=po.id,
                medicine_id=medicine.id,
                packaging_unit_id=item_data.packaging_unit_id,
                conversion_factor=conversion_factor,
                ordered_qty_pack=item_data.ordered_qty_pack,
                ordered_qty_base=ordered_qty_base,
                received_qty_base=Decimal("0.00"),
                purchase_rate=item_data.purchase_rate,
                discount_percent=item_data.discount_percent,
                tax_rate_id=item_data.tax_rate_id,
                tax_amount=tax_amount,
                line_total=line_total,
                notes=item_data.notes,
            )
            db.add(item)

        po.subtotal = round_cur(subtotal)
        po.discount_total = round_cur(discount_total)
        po.tax_total = round_cur(tax_total)
        po.grand_total = round_cur(subtotal - discount_total + tax_total)

        db.add(AuditLog(
            pharmacy_id=pharmacy_id,
            user_id=user_id,
            action="purchase_order.create",
            entity_type="purchase_order",
            entity_id=str(po.id),
            metadata_json=json.dumps({"po_number": po.po_number, "grand_total": str(po.grand_total)}),
        ))
        await db.flush()
        return await PurchaseOrderService.get_purchase_order(db, pharmacy_id, po.id)

    @staticmethod
    async def get_purchase_order(db: AsyncSession, pharmacy_id: int, po_id: int) -> PurchaseOrder:
        stmt = (
            select(PurchaseOrder)
            .options(
                selectinload(PurchaseOrder.supplier),
                selectinload(PurchaseOrder.items).selectinload(PurchaseOrderItem.medicine),
                selectinload(PurchaseOrder.items).selectinload(PurchaseOrderItem.packaging_unit),
                selectinload(PurchaseOrder.items).selectinload(PurchaseOrderItem.tax_rate),
                selectinload(PurchaseOrder.goods_receipts).selectinload(GoodsReceipt.items).selectinload(GoodsReceiptItem.medicine),
                selectinload(PurchaseOrder.goods_receipts).selectinload(GoodsReceipt.items).selectinload(GoodsReceiptItem.packaging_unit),
                selectinload(PurchaseOrder.goods_receipts).selectinload(GoodsReceipt.items).selectinload(GoodsReceiptItem.batch),
                selectinload(PurchaseOrder.goods_receipts).selectinload(GoodsReceipt.invoices).selectinload(SupplierInvoice.items),
                selectinload(PurchaseOrder.invoices).selectinload(SupplierInvoice.items),
            )
            .where(PurchaseOrder.id == po_id, PurchaseOrder.pharmacy_id == pharmacy_id)
        )
        po = (await db.execute(stmt)).scalar_one_or_none()
        if not po:
            raise HTTPException(status_code=404, detail="Purchase order not found")
        return po

    @staticmethod
    async def list_purchase_orders(
        db: AsyncSession,
        pharmacy_id: int,
        supplier_id: Optional[int] = None,
        status_filter: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[PurchaseOrder], int]:
        stmt = (
            select(PurchaseOrder)
            .options(
                selectinload(PurchaseOrder.supplier),
                selectinload(PurchaseOrder.items).selectinload(PurchaseOrderItem.medicine),
                selectinload(PurchaseOrder.items).selectinload(PurchaseOrderItem.packaging_unit),
                selectinload(PurchaseOrder.items).selectinload(PurchaseOrderItem.tax_rate),
                selectinload(PurchaseOrder.goods_receipts).selectinload(GoodsReceipt.invoices),
                selectinload(PurchaseOrder.invoices),
            )
            .where(PurchaseOrder.pharmacy_id == pharmacy_id)
        )
        if supplier_id:
            stmt = stmt.where(PurchaseOrder.supplier_id == supplier_id)
        if status_filter:
            stmt = stmt.where(PurchaseOrder.status == status_filter.upper())

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await db.execute(count_stmt)).scalar_one()

        stmt = stmt.order_by(PurchaseOrder.created_at.desc()).offset(offset).limit(limit)
        items = (await db.execute(stmt)).scalars().all()
        return list(items), total

    @staticmethod
    async def update_purchase_order(
        db: AsyncSession, pharmacy_id: int, po_id: int, payload: PurchaseOrderCreate, user_id: int
    ) -> PurchaseOrder:
        po = await PurchaseOrderService.get_purchase_order(db, pharmacy_id, po_id)
        if po.status not in ["DRAFT", "SUBMITTED"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Only draft or submitted purchase orders can be edited; this one is '{po.status}'",
            )

        supplier = await SupplierService.get_supplier(db, pharmacy_id, payload.supplier_id)
        po.supplier_id = supplier.id
        po.order_date = payload.order_date
        po.expected_delivery_date = payload.expected_delivery_date
        po.notes = payload.notes

        for old_item in list(po.items):
            await db.delete(old_item)
        await db.flush()

        subtotal = Decimal("0.00")
        discount_total = Decimal("0.00")
        tax_total = Decimal("0.00")

        for item_data in payload.items:
            med_res = await db.execute(
                select(Medicine).where(
                    Medicine.id == item_data.medicine_id,
                    Medicine.pharmacy_id == pharmacy_id,
                )
            )
            medicine = med_res.scalar_one_or_none()
            if not medicine:
                raise HTTPException(status_code=404, detail=f"Medicine ID {item_data.medicine_id} not found")

            conversion_factor = Decimal("1.0000")
            if item_data.packaging_unit_id != medicine.base_unit_id:
                conv_res = await db.execute(
                    select(MedicineUnitConversion).where(
                        MedicineUnitConversion.medicine_id == medicine.id,
                        MedicineUnitConversion.packaging_unit_id == item_data.packaging_unit_id,
                    )
                )
                conv = conv_res.scalar_one_or_none()
                if not conv:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Unit conversion not configured for packaging unit ID {item_data.packaging_unit_id} on medicine '{medicine.name}'",
                    )
                conversion_factor = conv.conversion_factor

            ordered_qty_base = UnitConversionService.to_base_units(item_data.ordered_qty_pack, conversion_factor)
            line_gross = round_cur(item_data.ordered_qty_pack * item_data.purchase_rate)
            line_disc = round_cur(line_gross * (item_data.discount_percent / Decimal("100.00")))
            line_taxable = line_gross - line_disc

            tax_amount = Decimal("0.00")
            if item_data.tax_rate_id:
                tr_res = await db.execute(
                    select(TaxRate).where(
                        TaxRate.id == item_data.tax_rate_id,
                        TaxRate.pharmacy_id == pharmacy_id,
                    )
                )
                tax_rate = tr_res.scalar_one_or_none()
                if tax_rate:
                    tax_calc = GstTaxCalculationService.calculate_tax(
                        amount=line_taxable,
                        rate_percentage=tax_rate.rate_percentage,
                        is_tax_inclusive=False,
                    )
                    tax_amount = tax_calc["total_tax"]

            line_total = line_taxable + tax_amount
            subtotal += line_gross
            discount_total += line_disc
            tax_total += tax_amount

            item = PurchaseOrderItem(
                po_id=po.id,
                medicine_id=medicine.id,
                packaging_unit_id=item_data.packaging_unit_id,
                conversion_factor=conversion_factor,
                ordered_qty_pack=item_data.ordered_qty_pack,
                ordered_qty_base=ordered_qty_base,
                received_qty_base=Decimal("0.00"),
                purchase_rate=item_data.purchase_rate,
                discount_percent=item_data.discount_percent,
                tax_rate_id=item_data.tax_rate_id,
                tax_amount=tax_amount,
                line_total=line_total,
                notes=item_data.notes,
            )
            db.add(item)

        po.subtotal = round_cur(subtotal)
        po.discount_total = round_cur(discount_total)
        po.tax_total = round_cur(tax_total)
        po.grand_total = round_cur(subtotal - discount_total + tax_total)
        po.updated_at = utcnow()

        db.add(AuditLog(
            pharmacy_id=pharmacy_id,
            user_id=user_id,
            action="purchase_order.update",
            entity_type="purchase_order",
            entity_id=str(po.id),
            metadata_json=json.dumps({"po_number": po.po_number, "grand_total": str(po.grand_total)}),
        ))
        await db.flush()
        return await PurchaseOrderService.get_purchase_order(db, pharmacy_id, po.id)

    @staticmethod
    async def update_po_status(
        db: AsyncSession, pharmacy_id: int, po_id: int, new_status: str, user_id: int, reason: Optional[str] = None
    ) -> PurchaseOrder:
        po = await PurchaseOrderService.get_purchase_order(db, pharmacy_id, po_id)
        target = new_status.upper()

        valid_transitions = {
            "DRAFT": ["SUBMITTED", "CANCELLED"],
            "SUBMITTED": ["APPROVED", "DRAFT", "CANCELLED"],
            "APPROVED": ["CANCELLED"],
            "PARTIALLY_RECEIVED": [],
            "RECEIVED": [],
            "CANCELLED": [],
        }

        if target not in valid_transitions.get(po.status, []):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot transition purchase order from '{po.status}' to '{target}'",
            )

        if target == "APPROVED":
            from app.services import app_settings
            prohibit_self = await app_settings.get_bool(db, pharmacy_id, "purchases.prohibit_self_approval")
            if prohibit_self and po.created_by == user_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Self-approval of purchase orders is prohibited by pharmacy policy",
                )
            po.approved_by = user_id
            po.approved_at = utcnow()
        elif target == "DRAFT":
            po.approved_by = None
            po.approved_at = None
        elif target == "CANCELLED":
            grn_count = (await db.execute(
                select(func.count(GoodsReceipt.id)).where(
                    GoodsReceipt.po_id == po.id,
                    GoodsReceipt.status != "CANCELLED",
                )
            )).scalar_one()
            if grn_count > 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Cannot cancel purchase order with {grn_count} active goods receipts",
                )
            po.cancelled_by = user_id
            po.cancelled_at = utcnow()

        po.status = target
        po.updated_at = utcnow()

        db.add(AuditLog(
            pharmacy_id=pharmacy_id,
            user_id=user_id,
            action=f"purchase_order.{target.lower()}",
            entity_type="purchase_order",
            entity_id=str(po.id),
            reason=reason,
            metadata_json=json.dumps({"po_number": po.po_number, "new_status": target, "reason": reason}),
        ))
        await db.flush()
        return po

    @staticmethod
    async def get_purchase_order_lifecycle(
        db: AsyncSession, pharmacy_id: int, po_id: int
    ) -> dict:
        po = await PurchaseOrderService.get_purchase_order(db, pharmacy_id, po_id)
        grns = po.goods_receipts or []

        invoices_map = {}
        for inv in (getattr(po, "invoices", []) or []):
            invoices_map[inv.id] = inv
        for g in grns:
            for gi in (g.invoices or []):
                invoices_map[gi.id] = gi
        invoices = list(invoices_map.values())

        total_ordered_base = sum(Decimal(str(it.ordered_qty_base or 0)) for it in (po.items or []))
        total_received_base = sum(Decimal(str(it.received_qty_base or 0)) for it in (po.items or []))

        posted_grns = [g for g in grns if g.status == "POSTED"]
        draft_grns = [g for g in grns if g.status == "DRAFT"]
        posted_invs = [inv for inv in invoices if inv.status == "POSTED"]
        draft_invs = [inv for inv in invoices if inv.status == "DRAFT"]

        if po.status == "CANCELLED":
            lifecycle_stage = "CANCELLED"
        elif po.status == "DRAFT":
            lifecycle_stage = "DRAFT"
        elif po.status == "SUBMITTED":
            lifecycle_stage = "SUBMITTED"
        elif po.status == "APPROVED":
            if not posted_grns and not draft_grns:
                lifecycle_stage = "AWAITING_GOODS"
            elif draft_invs:
                lifecycle_stage = "PENDING_FINALIZATION"
            elif posted_invs:
                lifecycle_stage = "COMPLETED"
            else:
                lifecycle_stage = "AWAITING_INVOICE" if posted_grns else "AWAITING_GOODS"
        elif po.status == "PARTIALLY_RECEIVED":
            if draft_invs:
                lifecycle_stage = "PENDING_FINALIZATION"
            elif posted_invs and total_received_base >= total_ordered_base:
                lifecycle_stage = "COMPLETED"
            else:
                lifecycle_stage = "PARTIALLY_RECEIVED"
        elif po.status == "RECEIVED":
            if posted_invs:
                lifecycle_stage = "COMPLETED"
            elif draft_invs:
                lifecycle_stage = "PENDING_FINALIZATION"
            else:
                lifecycle_stage = "AWAITING_INVOICE"
        else:
            lifecycle_stage = po.status

        return {
            "po": po,
            "goods_receipts": grns,
            "invoices": invoices,
            "lifecycle_stage": lifecycle_stage,
            "total_ordered_base": total_ordered_base,
            "total_received_base": total_received_base,
            "fulfillment_percent": float(round((total_received_base / total_ordered_base * 100) if total_ordered_base > 0 else 0, 1)),
            "posted_grns_count": len(posted_grns),
            "posted_invs_count": len(posted_invs),
        }


# ══════════════════════════════════════════════════════════════════════════════
# 3. Goods Receipt Inward (GRN) Service
# ══════════════════════════════════════════════════════════════════════════════

class GoodsReceiptService:
    @staticmethod
    async def generate_grn_number(db: AsyncSession, pharmacy_id: int) -> str:
        prefix = f"GRN-{business_now().strftime('%Y%m')}"
        return await next_document_number(
            db, pharmacy_id, "GRN", prefix, GoodsReceipt.grn_number, GoodsReceipt.pharmacy_id,
        )

    @staticmethod
    async def create_goods_receipt(
        db: AsyncSession, pharmacy_id: int, payload: GoodsReceiptCreate, user_id: int
    ) -> GoodsReceipt:
        supplier = await SupplierService.get_supplier(db, pharmacy_id, payload.supplier_id)

        # Validate PO if linked
        po: Optional[PurchaseOrder] = None
        if payload.po_id:
            po = await PurchaseOrderService.get_purchase_order(db, pharmacy_id, payload.po_id)
            if po.supplier_id != supplier.id:
                raise HTTPException(status_code=400, detail="Purchase order does not belong to this supplier")
            if po.status not in ["APPROVED", "PARTIALLY_RECEIVED"]:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot receive goods against purchase order with status '{po.status}' (must be APPROVED or PARTIALLY_RECEIVED)",
                )

        # Check pharmacy tax state code for intra vs inter state calculation
        pharmacy_res = await db.execute(select(Pharmacy).where(Pharmacy.id == pharmacy_id))
        pharmacy = pharmacy_res.scalar_one()
        pharm_state = normalize_state_code(pharmacy.state_code, pharmacy.gstin)
        supp_state = normalize_state_code(supplier.state_code, supplier.gstin)
        is_inter_state = bool(pharm_state and supp_state and pharm_state != supp_state)

        grn_number = await GoodsReceiptService.generate_grn_number(db, pharmacy_id)

        grn = GoodsReceipt(
            pharmacy_id=pharmacy_id,
            grn_number=grn_number,
            supplier_id=supplier.id,
            po_id=po.id if po else None,
            receipt_date=payload.receipt_date,
            supplier_challan_no=payload.supplier_challan_no,
            supplier_invoice_no=payload.supplier_invoice_no,
            status="DRAFT",
            notes=payload.notes,
            received_by=user_id,
        )
        db.add(grn)
        await db.flush()

        total_received_base = Decimal("0.00")
        total_accepted_base = Decimal("0.00")
        total_rejected_base = Decimal("0.00")
        total_free_base = Decimal("0.00")
        subtotal = Decimal("0.00")
        tax_total = Decimal("0.00")

        today = today_local()

        for item_data in payload.items:
            # 1. Validate Medicine
            med_res = await db.execute(
                select(Medicine).options(selectinload(Medicine.base_unit)).where(
                    Medicine.id == item_data.medicine_id,
                    Medicine.pharmacy_id == pharmacy_id,
                )
            )
            medicine = med_res.scalar_one_or_none()
            if not medicine:
                raise HTTPException(status_code=404, detail=f"Medicine ID {item_data.medicine_id} not found")

            # PO line must belong to the linked PO and be for the same medicine,
            # otherwise posting would credit received quantity to an unrelated order.
            if item_data.po_item_id:
                if not po:
                    raise HTTPException(
                        status_code=400,
                        detail="po_item_id can only be used when the goods receipt is linked to a purchase order",
                    )
                po_item_match = next((p for p in po.items if p.id == item_data.po_item_id), None)
                if po_item_match is None:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Purchase order line {item_data.po_item_id} does not belong to purchase order '{po.po_number}'",
                    )
                if po_item_match.medicine_id != medicine.id:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Purchase order line {item_data.po_item_id} is for a different medicine than '{medicine.name}'",
                    )

            # 2. Expiry validation: expired goods cannot enter saleable inventory!
            if item_data.expiry_date <= today:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Medicine '{medicine.name}' batch '{item_data.batch_number}' has expired ({item_data.expiry_date}). Expired stock cannot be received into saleable inventory.",
                )

            # 3. DPCO / Selling price validation: selling_rate <= mrp
            if item_data.selling_rate > item_data.mrp:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Selling rate ({item_data.selling_rate}) cannot exceed Maximum Retail Price (MRP {item_data.mrp}) for '{medicine.name}'.",
                )

            # 4. Packaging unit conversion
            conversion_factor = Decimal("1.0000")
            if item_data.packaging_unit_id != medicine.base_unit_id:
                conv_res = await db.execute(
                    select(MedicineUnitConversion).where(
                        MedicineUnitConversion.medicine_id == medicine.id,
                        MedicineUnitConversion.packaging_unit_id == item_data.packaging_unit_id,
                    )
                )
                conv = conv_res.scalar_one_or_none()
                if not conv:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Unit conversion not configured for packaging unit ID {item_data.packaging_unit_id} on '{medicine.name}'",
                    )
                conversion_factor = conv.conversion_factor

            # Quantities in pack
            rec_pack = item_data.received_qty_pack
            free_pack = item_data.free_qty_pack
            rej_pack = item_data.rejected_qty_pack

            if (rec_pack + free_pack) <= Decimal("0.00"):
                raise HTTPException(status_code=422, detail="Received plus free quantity must be greater than zero")
            if rej_pack > (rec_pack + free_pack):
                raise HTTPException(status_code=422, detail="Rejected quantity cannot exceed total received quantity")

            # Net accepted packs into saleable stock
            accepted_pack = (rec_pack + free_pack) - rej_pack
            accepted_qty_base = UnitConversionService.to_base_units(accepted_pack, conversion_factor)
            rejected_qty_base = UnitConversionService.to_base_units(rej_pack, conversion_factor)
            free_qty_base = UnitConversionService.to_base_units(free_pack, conversion_factor)
            received_qty_base = UnitConversionService.to_base_units(rec_pack, conversion_factor)

            # Validate indivisible whole units
            if not UnitConversionService.validate_whole_units(accepted_qty_base, medicine.base_unit.is_fractional_allowed):
                raise HTTPException(
                    status_code=422,
                    detail=f"Base unit '{medicine.base_unit.name}' does not allow fractional quantities ({accepted_qty_base})",
                )

            # 5. Allocated acquisition cost per base unit accounting for free goods
            allocated = UnitConversionService.calculate_allocated_cost(
                paid_quantity=rec_pack,
                free_quantity=free_pack,
                unit_purchase_rate=item_data.purchase_rate,
                conversion_factor=conversion_factor,
                discount_percent=item_data.discount_percent,
            )
            cost_per_base_unit = allocated["cost_per_base_unit"]

            # Line financial amounts
            gross_line = round_cur(rec_pack * item_data.purchase_rate)
            disc_line = round_cur(gross_line * (item_data.discount_percent / Decimal("100.00")))
            taxable_line = gross_line - disc_line

            cgst_amt = Decimal("0.00")
            sgst_amt = Decimal("0.00")
            igst_amt = Decimal("0.00")
            tax_amount = Decimal("0.00")

            if item_data.tax_rate_id:
                tr_res = await db.execute(
                    select(TaxRate).where(
                        TaxRate.id == item_data.tax_rate_id,
                        TaxRate.pharmacy_id == pharmacy_id,
                    )
                )
                tax_rate = tr_res.scalar_one_or_none()
                if tax_rate:
                    tax_calc = GstTaxCalculationService.calculate_tax(
                        amount=taxable_line,
                        rate_percentage=tax_rate.rate_percentage,
                        is_tax_inclusive=False,
                        is_inter_state=is_inter_state,
                    )
                    cgst_amt = tax_calc["cgst_amount"]
                    sgst_amt = tax_calc["sgst_amount"]
                    igst_amt = tax_calc["igst_amount"]
                    tax_amount = tax_calc["total_tax"]

            line_total = taxable_line + tax_amount

            total_received_base += received_qty_base
            total_accepted_base += accepted_qty_base
            total_rejected_base += rejected_qty_base
            total_free_base += free_qty_base
            subtotal += taxable_line
            tax_total += tax_amount

            grn_item = GoodsReceiptItem(
                grn_id=grn.id,
                po_item_id=item_data.po_item_id,
                medicine_id=medicine.id,
                batch_number=item_data.batch_number.strip().upper(),
                manufacturing_date=item_data.manufacturing_date,
                expiry_date=item_data.expiry_date,
                packaging_unit_id=item_data.packaging_unit_id,
                conversion_factor=conversion_factor,
                received_qty_pack=rec_pack,
                free_qty_pack=free_pack,
                rejected_qty_pack=rej_pack,
                accepted_qty_base=accepted_qty_base,
                rejected_qty_base=rejected_qty_base,
                rejection_reason=item_data.rejection_reason,
                purchase_rate=item_data.purchase_rate,
                discount_percent=item_data.discount_percent,
                mrp=item_data.mrp,
                selling_rate=item_data.selling_rate,
                cost_per_base_unit=cost_per_base_unit,
                tax_rate_id=item_data.tax_rate_id,
                cgst_amount=cgst_amt,
                sgst_amount=sgst_amt,
                igst_amount=igst_amt,
                tax_amount=tax_amount,
                line_total=line_total,
                rack_location=item_data.rack_location.strip() if item_data.rack_location else None,
            )
            db.add(grn_item)

        grn.total_received_base = round_cur(total_received_base)
        grn.total_accepted_base = round_cur(total_accepted_base)
        grn.total_rejected_base = round_cur(total_rejected_base)
        grn.total_free_base = round_cur(total_free_base)
        grn.subtotal = round_cur(subtotal)
        grn.tax_total = round_cur(tax_total)
        grn.grand_total = round_cur(subtotal + tax_total)

        db.add(AuditLog(
            pharmacy_id=pharmacy_id,
            user_id=user_id,
            action="goods_receipt.create",
            entity_type="goods_receipt",
            entity_id=str(grn.id),
            metadata_json=json.dumps({"grn_number": grn.grn_number, "accepted_base": str(grn.total_accepted_base)}),
        ))
        await db.flush()
        return await GoodsReceiptService.get_goods_receipt(db, pharmacy_id, grn.id)

    @staticmethod
    async def get_goods_receipt(db: AsyncSession, pharmacy_id: int, grn_id: int) -> GoodsReceipt:
        stmt = (
            select(GoodsReceipt)
            .options(
                selectinload(GoodsReceipt.supplier),
                selectinload(GoodsReceipt.purchase_order),
                selectinload(GoodsReceipt.items).selectinload(GoodsReceiptItem.medicine),
                selectinload(GoodsReceipt.items).selectinload(GoodsReceiptItem.packaging_unit),
                selectinload(GoodsReceipt.items).selectinload(GoodsReceiptItem.batch),
                selectinload(GoodsReceipt.items).selectinload(GoodsReceiptItem.tax_rate),
            )
            .where(GoodsReceipt.id == grn_id, GoodsReceipt.pharmacy_id == pharmacy_id)
        )
        grn = (await db.execute(stmt)).scalar_one_or_none()
        if not grn:
            raise HTTPException(status_code=404, detail="Goods receipt not found")
        return grn

    @staticmethod
    async def list_goods_receipts(
        db: AsyncSession,
        pharmacy_id: int,
        supplier_id: Optional[int] = None,
        status_filter: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[GoodsReceipt], int]:
        stmt = (
            select(GoodsReceipt)
            .options(
                selectinload(GoodsReceipt.supplier),
                selectinload(GoodsReceipt.purchase_order),
                selectinload(GoodsReceipt.items).selectinload(GoodsReceiptItem.medicine),
                selectinload(GoodsReceipt.items).selectinload(GoodsReceiptItem.packaging_unit),
            )
            .where(GoodsReceipt.pharmacy_id == pharmacy_id)
        )
        if supplier_id:
            stmt = stmt.where(GoodsReceipt.supplier_id == supplier_id)
        if status_filter:
            stmt = stmt.where(GoodsReceipt.status == status_filter.upper())

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await db.execute(count_stmt)).scalar_one()

        stmt = stmt.order_by(GoodsReceipt.created_at.desc()).offset(offset).limit(limit)
        items = (await db.execute(stmt)).scalars().all()
        return list(items), total

    @staticmethod
    async def post_goods_receipt(
        db: AsyncSession, pharmacy_id: int, grn_id: int, user_id: int
    ) -> GoodsReceipt:
        """
        Atomically posts goods receipt into stock:
          - Validates idempotency (if already POSTED, raises 409 Conflict)
          - Locks batches with `with_for_update()`
          - Updates `MedicineBatch.current_stock_base`
          - Records immutable `StockMovement` (type `GOODS_RECEIPT`)
          - Updates PO item received quantities & PO status if applicable
        """
        # Lock GRN header
        grn_q = select(GoodsReceipt).where(
            GoodsReceipt.id == grn_id,
            GoodsReceipt.pharmacy_id == pharmacy_id,
        ).with_for_update()
        grn = (await db.execute(grn_q)).scalar_one_or_none()
        if not grn:
            raise HTTPException(status_code=404, detail="Goods receipt not found")

        if grn.status == "POSTED":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Goods receipt '{grn.grn_number}' is already posted",
            )
        if grn.status == "CANCELLED":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot post cancelled goods receipt '{grn.grn_number}'",
            )

        # Load items
        items_q = select(GoodsReceiptItem).where(GoodsReceiptItem.grn_id == grn.id)
        items = (await db.execute(items_q)).scalars().all()

        for item in items:
            if item.accepted_qty_base <= Decimal("0.00"):
                # If everything was rejected, no saleable stock is added
                continue

            # Row lock existing batch or create a new batch
            batch_q = select(MedicineBatch).where(
                MedicineBatch.pharmacy_id == pharmacy_id,
                MedicineBatch.medicine_id == item.medicine_id,
                MedicineBatch.batch_number == item.batch_number,
            ).with_for_update()
            batch = (await db.execute(batch_q)).scalar_one_or_none()

            if batch is None:
                batch = MedicineBatch(
                    pharmacy_id=pharmacy_id,
                    medicine_id=item.medicine_id,
                    batch_number=item.batch_number,
                    manufacturing_date=item.manufacturing_date,
                    expiry_date=item.expiry_date,
                    mrp=item.mrp,
                    purchase_rate=item.purchase_rate,
                    cost_per_base_unit=item.cost_per_base_unit,
                    default_selling_rate=item.selling_rate,
                    rack_location=item.rack_location,
                    current_stock_base=Decimal("0.00"),
                    price_unit_id=item.packaging_unit_id,
                    price_conversion_factor=item.conversion_factor,
                    is_active=True,
                )
                db.add(batch)
                await db.flush()
            else:
                existing_stock = batch.current_stock_base
                # Rates are compared per base unit: the batch and the receipt may quote
                # them for different pack sizes (e.g. per Strip vs per Box).
                batch_factor = Decimal(str(batch.price_conversion_factor or 1))
                item_factor = Decimal(str(item.conversion_factor))
                mrp_per_base_batch = Decimal(str(batch.mrp)) / batch_factor
                mrp_per_base_item = Decimal(str(item.mrp)) / item_factor
                if existing_stock > Decimal("0.00"):
                    # A batch number identifies one manufacturing lot, so its printed MRP
                    # and expiry are fixed. A mismatch means a data-entry error; silently
                    # overwriting would re-price stock already on the shelf.
                    if round_four(mrp_per_base_batch) != round_four(mrp_per_base_item):
                        raise HTTPException(
                            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=(
                                f"Batch '{batch.batch_number}' is already in stock with MRP {batch.mrp}, "
                                f"but this receipt has MRP {item.mrp}. Correct the receipt or use a different batch number."
                            ),
                        )
                    if batch.expiry_date != item.expiry_date:
                        raise HTTPException(
                            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=(
                                f"Batch '{batch.batch_number}' is already in stock with expiry {batch.expiry_date}, "
                                f"but this receipt has expiry {item.expiry_date}. Correct the receipt or use a different batch number."
                            ),
                        )
                    # Weighted average cost (AS 2) across stock on hand and the new intake
                    total_qty = existing_stock + item.accepted_qty_base
                    batch.cost_per_base_unit = round_four(
                        (existing_stock * batch.cost_per_base_unit
                         + item.accepted_qty_base * item.cost_per_base_unit) / total_qty
                    )
                    # Keep the batch's pricing unit; restate the new rates in it
                    batch.purchase_rate = round_cur(item.purchase_rate / item_factor * batch_factor)
                    batch.default_selling_rate = round_cur(item.selling_rate / item_factor * batch_factor)
                else:
                    # No stock left in this batch: the new intake defines its cost, MRP and expiry
                    batch.cost_per_base_unit = item.cost_per_base_unit
                    batch.mrp = item.mrp
                    batch.expiry_date = item.expiry_date
                    if item.manufacturing_date:
                        batch.manufacturing_date = item.manufacturing_date
                    batch.price_unit_id = item.packaging_unit_id
                    batch.price_conversion_factor = item_factor
                    batch.purchase_rate = item.purchase_rate
                    batch.default_selling_rate = item.selling_rate
                if item.rack_location:
                    batch.rack_location = item.rack_location

            # Increment stock balance atomically
            batch.current_stock_base = (batch.current_stock_base + item.accepted_qty_base).quantize(TWO_PLACES)
            item.batch_id = batch.id

            # Write immutable stock ledger movement
            movement = StockMovement(
                pharmacy_id=pharmacy_id,
                medicine_id=item.medicine_id,
                batch_id=batch.id,
                movement_type="GOODS_RECEIPT",
                reference_id=grn.grn_number,
                reference_type="goods_receipt",
                quantity_base=item.accepted_qty_base,
                balance_after_base=batch.current_stock_base,
                unit_cost=item.cost_per_base_unit,
                # Inventory value added: accepted units at cost (net of discount, excl. GST)
                total_cost=round_cur(item.accepted_qty_base * item.cost_per_base_unit),
                reason=f"Goods Receipt from Supplier",
                notes=f"Accepted {item.accepted_qty_base} units (Rejected {item.rejected_qty_base})",
                user_id=user_id,
            )
            db.add(movement)

            # Update PO item received quantity if linked
            if item.po_item_id:
                po_item_q = select(PurchaseOrderItem).where(
                    PurchaseOrderItem.id == item.po_item_id
                ).with_for_update()
                po_item = (await db.execute(po_item_q)).scalar_one_or_none()
                if po_item:
                    po_item.received_qty_base = (po_item.received_qty_base + item.accepted_qty_base).quantize(TWO_PLACES)

        # If linked to a PO, update overall PO status
        if grn.po_id:
            po_q = select(PurchaseOrder).options(selectinload(PurchaseOrder.items)).where(
                PurchaseOrder.id == grn.po_id
            ).with_for_update()
            po = (await db.execute(po_q)).scalar_one()

            all_fulfilled = True
            any_received = False
            for p_item in po.items:
                if p_item.received_qty_base > Decimal("0.00"):
                    any_received = True
                if p_item.received_qty_base < p_item.ordered_qty_base:
                    all_fulfilled = False

            if all_fulfilled:
                po.status = "RECEIVED"
            elif any_received:
                po.status = "PARTIALLY_RECEIVED"
            po.updated_at = utcnow()

        grn.status = "POSTED"
        grn.posted_at = utcnow()
        grn.updated_at = utcnow()

        db.add(AuditLog(
            pharmacy_id=pharmacy_id,
            user_id=user_id,
            action="goods_receipt.post",
            entity_type="goods_receipt",
            entity_id=str(grn.id),
            metadata_json=json.dumps({
                "grn_number": grn.grn_number,
                "total_accepted": str(grn.total_accepted_base),
            }),
        ))
        await db.flush()
        await gl_posting.record(db, pharmacy_id, user_id, "GOODS_RECEIPT", grn.id)
        return await GoodsReceiptService.get_goods_receipt(db, pharmacy_id, grn.id)


# ══════════════════════════════════════════════════════════════════════════════
# 4. Supplier Purchase Invoices Service
# ══════════════════════════════════════════════════════════════════════════════

def invoice_number_key(invoice_number: str, invoice_date: date) -> str:
    """
    Duplicate-detection key for a supplier bill: the financial year (suppliers restart
    their GST invoice series every April) plus the number without spaces or punctuation,
    so "INV-001", "inv 001" and "INV/001" are treated as the same bill.
    """
    cleaned = re.sub(r"[^A-Z0-9]", "", invoice_number.upper())
    if not cleaned:
        raise HTTPException(status_code=422, detail="Invoice number must contain letters or digits")
    fy = invoice_date.year if invoice_date.month >= 4 else invoice_date.year - 1
    return f"{fy}:{cleaned}"


class SupplierInvoiceService:
    @staticmethod
    async def assert_number_free(db: AsyncSession, pharmacy_id: int, supplier: Supplier, number_key: str,
                                 exclude_id: Optional[int] = None) -> None:
        stmt = select(SupplierInvoice).where(
            SupplierInvoice.pharmacy_id == pharmacy_id,
            SupplierInvoice.supplier_id == supplier.id,
            SupplierInvoice.number_key == number_key,
        )
        if exclude_id:
            stmt = stmt.where(SupplierInvoice.id != exclude_id)
        clash = (await db.execute(stmt)).scalars().first()
        if clash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(f"Invoice number '{clash.invoice_number}' (dated {clash.invoice_date}) has already been recorded "
                        f"for supplier '{supplier.name}' in this financial year ({clash.status.lower()})"),
            )

    @staticmethod
    async def validate_links(db: AsyncSession, pharmacy_id: int, supplier: Supplier, payload) -> None:
        """Linked GRN / PO and GRN lines must belong to this pharmacy and supplier."""
        grn_items = {}
        if payload.grn_id:
            ref_grn = await GoodsReceiptService.get_goods_receipt(db, pharmacy_id, payload.grn_id)
            if ref_grn.supplier_id != supplier.id:
                raise HTTPException(status_code=400, detail="Goods receipt does not belong to this supplier")
            if ref_grn.status == "CANCELLED":
                raise HTTPException(status_code=422, detail=f"Goods receipt {ref_grn.grn_number} is cancelled")
            grn_items = {gi.id: gi for gi in ref_grn.items}
        if payload.po_id:
            ref_po = await PurchaseOrderService.get_purchase_order(db, pharmacy_id, payload.po_id)
            if ref_po.supplier_id != supplier.id:
                raise HTTPException(status_code=400, detail="Purchase order does not belong to this supplier")
        if payload.replaces_invoice_id:
            original = (await db.execute(select(SupplierInvoice).where(
                SupplierInvoice.id == payload.replaces_invoice_id, SupplierInvoice.pharmacy_id == pharmacy_id,
            ))).scalar_one_or_none()
            if original is None or original.supplier_id != supplier.id or original.status != "CANCELLED":
                raise HTTPException(status_code=422, detail="A replacement must point at a cancelled bill of the same supplier")
        for item_data in payload.items:
            if item_data.grn_item_id:
                gi = grn_items.get(item_data.grn_item_id)
                if gi is None:
                    raise HTTPException(
                        status_code=400,
                        detail=f"GRN item {item_data.grn_item_id} does not belong to the invoice's goods receipt",
                    )
                if gi.medicine_id != item_data.medicine_id:
                    raise HTTPException(
                        status_code=422,
                        detail=f"GRN item {item_data.grn_item_id} is a different medicine from the invoice line",
                    )

    @staticmethod
    async def apply_lines(db: AsyncSession, pharmacy_id: int, supplier: Supplier, invoice: SupplierInvoice, payload) -> None:
        """(Re)build the invoice lines and totals from the payload (draft invoices only)."""
        pharmacy_res = await db.execute(select(Pharmacy).where(Pharmacy.id == pharmacy_id))
        pharmacy = pharmacy_res.scalar_one()
        pharm_state = normalize_state_code(pharmacy.state_code, pharmacy.gstin)
        supp_state = normalize_state_code(supplier.state_code, supplier.gstin)
        is_inter_state = bool(pharm_state and supp_state and pharm_state != supp_state)

        subtotal = Decimal("0.00")
        discount_total = Decimal("0.00")
        cgst_total = Decimal("0.00")
        sgst_total = Decimal("0.00")
        igst_total = Decimal("0.00")
        tax_total = Decimal("0.00")
        # Sum of line totals (taxable + tax). For tax-inclusive bills the gross
        # already contains GST, so the grand total must be built from line totals
        # rather than subtotal - discount + tax (which would count GST twice).
        lines_total = Decimal("0.00")

        for item_data in payload.items:
            med_res = await db.execute(
                select(Medicine).where(
                    Medicine.id == item_data.medicine_id,
                    Medicine.pharmacy_id == pharmacy_id,
                )
            )
            medicine = med_res.scalar_one_or_none()
            if not medicine:
                raise HTTPException(status_code=404, detail=f"Medicine ID {item_data.medicine_id} not found")

            conversion_factor = Decimal("1.0000")
            if item_data.packaging_unit_id != medicine.base_unit_id:
                conv_res = await db.execute(
                    select(MedicineUnitConversion).where(
                        MedicineUnitConversion.medicine_id == medicine.id,
                        MedicineUnitConversion.packaging_unit_id == item_data.packaging_unit_id,
                    )
                )
                conv = conv_res.scalar_one_or_none()
                if not conv:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Unit conversion not configured for packaging unit ID {item_data.packaging_unit_id}",
                    )
                conversion_factor = conv.conversion_factor

            billed_qty_base = UnitConversionService.to_base_units(item_data.billed_qty_pack, conversion_factor)

            line_gross = round_cur(item_data.billed_qty_pack * item_data.purchase_rate)
            line_disc = round_cur(line_gross * (item_data.discount_percent / Decimal("100.00")))
            line_taxable = line_gross - line_disc

            cgst_amt = Decimal("0.00")
            sgst_amt = Decimal("0.00")
            igst_amt = Decimal("0.00")
            item_tax = Decimal("0.00")

            if item_data.tax_rate_id:
                tr_res = await db.execute(
                    select(TaxRate).where(
                        TaxRate.id == item_data.tax_rate_id,
                        TaxRate.pharmacy_id == pharmacy_id,
                    )
                )
                tax_rate = tr_res.scalar_one_or_none()
                if not tax_rate:
                    raise HTTPException(status_code=422, detail=f"Tax rate ID {item_data.tax_rate_id} not found")
                if tax_rate:
                    tax_calc = GstTaxCalculationService.calculate_tax(
                        amount=line_taxable,
                        rate_percentage=tax_rate.rate_percentage,
                        is_tax_inclusive=payload.is_tax_inclusive,
                        is_inter_state=is_inter_state,
                    )
                    cgst_amt = tax_calc["cgst_amount"]
                    sgst_amt = tax_calc["sgst_amount"]
                    igst_amt = tax_calc["igst_amount"]
                    item_tax = tax_calc["total_tax"]
                    if payload.is_tax_inclusive:
                        line_taxable = tax_calc["taxable_value"]

            line_total = line_taxable + item_tax

            subtotal += line_gross
            discount_total += line_disc
            cgst_total += cgst_amt
            sgst_total += sgst_amt
            igst_total += igst_amt
            tax_total += item_tax
            lines_total += line_total

            inv_item = SupplierInvoiceItem(
                invoice_id=invoice.id,
                grn_item_id=item_data.grn_item_id,
                medicine_id=medicine.id,
                batch_number=item_data.batch_number.strip().upper(),
                packaging_unit_id=item_data.packaging_unit_id,
                conversion_factor=conversion_factor,
                billed_qty_pack=item_data.billed_qty_pack,
                billed_qty_base=billed_qty_base,
                free_qty_pack=item_data.free_qty_pack,
                purchase_rate=item_data.purchase_rate,
                discount_percent=item_data.discount_percent,
                discount_amount=line_disc,
                taxable_amount=line_taxable,
                tax_rate_id=item_data.tax_rate_id,
                cgst_amount=cgst_amt,
                sgst_amount=sgst_amt,
                igst_amount=igst_amt,
                tax_amount=item_tax,
                line_total=line_total,
            )
            db.add(inv_item)

        invoice.subtotal = round_cur(subtotal)
        invoice.discount_total = round_cur(discount_total)
        invoice.cgst_total = round_cur(cgst_total)
        invoice.sgst_total = round_cur(sgst_total)
        invoice.igst_total = round_cur(igst_total)
        invoice.tax_total = round_cur(tax_total)

        calc_grand = lines_total + payload.other_charges + payload.round_off
        invoice.calculated_grand_total = round_cur(calc_grand)
        invoice.discrepancy_amount = round_cur(invoice.calculated_grand_total - payload.supplier_declared_total)


    @staticmethod
    async def create_invoice(
        db: AsyncSession, pharmacy_id: int, payload: SupplierInvoiceCreate, user_id: int
    ) -> SupplierInvoice:
        supplier = await SupplierService.get_supplier(db, pharmacy_id, payload.supplier_id)

        # Duplicate invoice number per supplier and financial year
        inv_num_clean = payload.invoice_number.strip().upper()
        number_key = invoice_number_key(inv_num_clean, payload.invoice_date)
        await SupplierInvoiceService.assert_number_free(db, pharmacy_id, supplier, number_key)
        await SupplierInvoiceService.validate_links(db, pharmacy_id, supplier, payload)

        due_date = payload.due_date
        if not due_date:
            from datetime import timedelta
            due_date = payload.invoice_date + timedelta(days=payload.payment_terms_days)

        invoice = SupplierInvoice(
            pharmacy_id=pharmacy_id,
            invoice_number=inv_num_clean,
            number_key=number_key,
            supplier_id=supplier.id,
            grn_id=payload.grn_id,
            po_id=payload.po_id,
            invoice_date=payload.invoice_date,
            received_date=payload.received_date,
            due_date=due_date,
            payment_terms_days=payload.payment_terms_days,
            is_tax_inclusive=payload.is_tax_inclusive,
            currency=payload.currency,
            other_charges=payload.other_charges,
            round_off=payload.round_off,
            supplier_declared_total=payload.supplier_declared_total,
            discrepancy_notes=payload.discrepancy_notes,
            notes=payload.notes,
            status="DRAFT",
            payment_status="UNPAID",
            paid_amount=Decimal("0.00"),
            replaces_invoice_id=payload.replaces_invoice_id,
            created_by=user_id,
        )
        supplier_name = supplier.name   # Objects are expired if the savepoint below rolls back
        db.add(invoice)
        try:
            # Savepoint: a concurrent request saving the same bill hits the unique key
            async with db.begin_nested():
                await db.flush()
        except IntegrityError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Invoice number '{inv_num_clean}' has already been recorded for supplier '{supplier_name}'",
            )

        await SupplierInvoiceService.apply_lines(db, pharmacy_id, supplier, invoice, payload)

        db.add(AuditLog(
            pharmacy_id=pharmacy_id,
            user_id=user_id,
            action="supplier_invoice.create",
            entity_type="supplier_invoice",
            entity_id=str(invoice.id),
            metadata_json=json.dumps({
                "invoice_number": invoice.invoice_number,
                "calculated_total": str(invoice.calculated_grand_total),
                "declared_total": str(invoice.supplier_declared_total),
                "discrepancy": str(invoice.discrepancy_amount),
                "replaces_invoice_id": invoice.replaces_invoice_id,
            }),
        ))
        await db.flush()
        return await SupplierInvoiceService.get_invoice(db, pharmacy_id, invoice.id)

    @staticmethod
    async def get_invoice(db: AsyncSession, pharmacy_id: int, invoice_id: int) -> SupplierInvoice:
        stmt = (
            select(SupplierInvoice)
            .options(
                selectinload(SupplierInvoice.supplier),
                selectinload(SupplierInvoice.goods_receipt),
                selectinload(SupplierInvoice.items).selectinload(SupplierInvoiceItem.medicine),
                selectinload(SupplierInvoice.items).selectinload(SupplierInvoiceItem.packaging_unit),
                selectinload(SupplierInvoice.items).selectinload(SupplierInvoiceItem.tax_rate),
            )
            .where(SupplierInvoice.id == invoice_id, SupplierInvoice.pharmacy_id == pharmacy_id)
        )
        invoice = (await db.execute(stmt)).scalar_one_or_none()
        if not invoice:
            raise HTTPException(status_code=404, detail="Supplier invoice not found")
        return invoice

    @staticmethod
    async def list_invoices(
        db: AsyncSession,
        pharmacy_id: int,
        supplier_id: Optional[int] = None,
        payment_status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        status_filter: Optional[str] = None,
    ) -> Tuple[List[SupplierInvoice], int]:
        stmt = (
            select(SupplierInvoice)
            .options(
                selectinload(SupplierInvoice.supplier),
                selectinload(SupplierInvoice.goods_receipt),
                selectinload(SupplierInvoice.items).selectinload(SupplierInvoiceItem.medicine),
                selectinload(SupplierInvoice.items).selectinload(SupplierInvoiceItem.packaging_unit),
            )
            .where(SupplierInvoice.pharmacy_id == pharmacy_id)
        )
        if status_filter:
            stmt = stmt.where(SupplierInvoice.status == status_filter.upper())
        if supplier_id:
            stmt = stmt.where(SupplierInvoice.supplier_id == supplier_id)
        if payment_status:
            stmt = stmt.where(SupplierInvoice.payment_status == payment_status.upper())

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await db.execute(count_stmt)).scalar_one()

        stmt = stmt.order_by(SupplierInvoice.created_at.desc()).offset(offset).limit(limit)
        items = (await db.execute(stmt)).scalars().all()
        return list(items), total


# ══════════════════════════════════════════════════════════════════════════════
# 5. Supplier Return (Debit Note) Service
# ══════════════════════════════════════════════════════════════════════════════

class SupplierReturnService:
    @staticmethod
    async def generate_return_number(db: AsyncSession, pharmacy_id: int) -> str:
        prefix = f"PR-{business_now().strftime('%Y%m')}"
        return await next_document_number(
            db, pharmacy_id, "PR", prefix, SupplierReturn.return_number, SupplierReturn.pharmacy_id,
        )

    @staticmethod
    async def create_supplier_return(
        db: AsyncSession, pharmacy_id: int, payload: SupplierReturnCreate, user_id: int
    ) -> SupplierReturn:
        supplier = await SupplierService.get_supplier(db, pharmacy_id, payload.supplier_id)

        # Header references must belong to this pharmacy and supplier
        if payload.grn_id:
            ref_grn = await GoodsReceiptService.get_goods_receipt(db, pharmacy_id, payload.grn_id)
            if ref_grn.supplier_id != supplier.id:
                raise HTTPException(status_code=400, detail="Goods receipt does not belong to this supplier")
        if payload.invoice_id:
            ref_inv = await SupplierInvoiceService.get_invoice(db, pharmacy_id, payload.invoice_id)
            if ref_inv.supplier_id != supplier.id:
                raise HTTPException(status_code=400, detail="Supplier invoice does not belong to this supplier")

        return_number = await SupplierReturnService.generate_return_number(db, pharmacy_id)
        debit_ref = payload.debit_note_reference or f"DN-{return_number}"

        supplier_return = SupplierReturn(
            pharmacy_id=pharmacy_id,
            return_number=return_number,
            supplier_id=supplier.id,
            grn_id=payload.grn_id,
            invoice_id=payload.invoice_id,
            return_date=payload.return_date,
            status="DRAFT",
            reason_code=payload.reason_code.upper(),
            debit_note_reference=debit_ref,
            notes=payload.notes,
            created_by=user_id,
        )
        db.add(supplier_return)
        await db.flush()

        subtotal = Decimal("0.00")
        tax_total = Decimal("0.00")
        # Quantities claimed by earlier lines of this same return, so that several
        # lines cannot jointly exceed what is returnable or in stock.
        claimed_by_grn_item: Dict[int, Decimal] = {}
        claimed_by_batch: Dict[int, Decimal] = {}

        for item_data in payload.items:
            # 1. Validate Medicine and Batch
            med_res = await db.execute(
                select(Medicine).options(selectinload(Medicine.base_unit)).where(
                    Medicine.id == item_data.medicine_id,
                    Medicine.pharmacy_id == pharmacy_id,
                )
            )
            medicine = med_res.scalar_one_or_none()
            if not medicine:
                raise HTTPException(status_code=404, detail=f"Medicine ID {item_data.medicine_id} not found")

            batch_res = await db.execute(
                select(MedicineBatch).where(
                    MedicineBatch.id == item_data.batch_id,
                    MedicineBatch.pharmacy_id == pharmacy_id,
                )
            )
            batch = batch_res.scalar_one_or_none()
            if not batch:
                raise HTTPException(status_code=404, detail=f"Batch ID {item_data.batch_id} not found")
            if batch.medicine_id != medicine.id:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Batch '{batch.batch_number}' does not belong to medicine '{medicine.name}'",
                )

            # 2. Conversion factor
            conversion_factor = Decimal("1.0000")
            if item_data.packaging_unit_id != medicine.base_unit_id:
                conv_res = await db.execute(
                    select(MedicineUnitConversion).where(
                        MedicineUnitConversion.medicine_id == medicine.id,
                        MedicineUnitConversion.packaging_unit_id == item_data.packaging_unit_id,
                    )
                )
                conv = conv_res.scalar_one_or_none()
                if not conv:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Unit conversion not configured for packaging unit ID {item_data.packaging_unit_id}",
                    )
                conversion_factor = conv.conversion_factor

            return_qty_base = UnitConversionService.to_base_units(item_data.return_qty_pack, conversion_factor)

            # 3a. Validate Returnable Quantity against the specific GRN line if linked
            if item_data.grn_item_id:
                grn_item_res = await db.execute(
                    select(GoodsReceiptItem, GoodsReceipt)
                    .join(GoodsReceipt, GoodsReceipt.id == GoodsReceiptItem.grn_id)
                    .where(
                        GoodsReceiptItem.id == item_data.grn_item_id,
                        GoodsReceipt.pharmacy_id == pharmacy_id,
                    )
                )
                row = grn_item_res.one_or_none()
                if not row:
                    raise HTTPException(status_code=404, detail="Referenced GRN item not found")
                grn_item, grn_header = row

                if grn_header.supplier_id != supplier.id:
                    raise HTTPException(status_code=400, detail="Referenced GRN item was not received from this supplier")
                if payload.grn_id and grn_header.id != payload.grn_id:
                    raise HTTPException(status_code=400, detail="Referenced GRN item does not belong to the return's goods receipt")
                if grn_header.status != "POSTED":
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"Goods receipt '{grn_header.grn_number}' is not posted; nothing has entered stock to return",
                    )
                if grn_item.medicine_id != medicine.id or grn_item.batch_id != batch.id:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"Referenced GRN item does not match medicine '{medicine.name}' batch '{batch.batch_number}'",
                    )

                # Sum previously returned quantities for this GRN item across active returns
                prior_returns_q = select(
                    func.coalesce(func.sum(SupplierReturnItem.return_qty_base), Decimal("0.00"))
                ).select_from(SupplierReturnItem).join(SupplierReturn).where(
                    SupplierReturnItem.grn_item_id == grn_item.id,
                    SupplierReturn.status != "CANCELLED",
                    SupplierReturn.id != supplier_return.id,
                )
                already_returned_base = (await db.execute(prior_returns_q)).scalar_one()
                already_returned_base += claimed_by_grn_item.get(grn_item.id, Decimal("0.00"))

                returnable_base = grn_item.accepted_qty_base - already_returned_base
                if return_qty_base > returnable_base:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=(
                            f"Return quantity ({return_qty_base} units) exceeds returnable quantity "
                            f"({returnable_base} units) for batch '{batch.batch_number}' from GRN. "
                            f"(Accepted: {grn_item.accepted_qty_base}, Already returned: {already_returned_base})"
                        ),
                    )
                claimed_by_grn_item[grn_item.id] = (
                    claimed_by_grn_item.get(grn_item.id, Decimal("0.00")) + return_qty_base
                )

            # 3b. Validate against everything this supplier delivered into the batch.
            # This applies even without a GRN line reference, so a return cannot send
            # back more than the supplier supplied. Batches never received through a
            # GRN (e.g. opening stock) are limited only by the stock check below.
            received_from_supplier = (await db.execute(
                select(func.coalesce(func.sum(GoodsReceiptItem.accepted_qty_base), Decimal("0.00")))
                .select_from(GoodsReceiptItem)
                .join(GoodsReceipt, GoodsReceipt.id == GoodsReceiptItem.grn_id)
                .where(
                    GoodsReceiptItem.batch_id == batch.id,
                    GoodsReceipt.pharmacy_id == pharmacy_id,
                    GoodsReceipt.supplier_id == supplier.id,
                    GoodsReceipt.status == "POSTED",
                )
            )).scalar_one()
            received_any_grn = (await db.execute(
                select(func.count(GoodsReceiptItem.id))
                .join(GoodsReceipt, GoodsReceipt.id == GoodsReceiptItem.grn_id)
                .where(
                    GoodsReceiptItem.batch_id == batch.id,
                    GoodsReceipt.pharmacy_id == pharmacy_id,
                    GoodsReceipt.status == "POSTED",
                )
            )).scalar_one()
            if received_any_grn > 0:
                returned_to_supplier = (await db.execute(
                    select(func.coalesce(func.sum(SupplierReturnItem.return_qty_base), Decimal("0.00")))
                    .select_from(SupplierReturnItem)
                    .join(SupplierReturn)
                    .where(
                        SupplierReturnItem.batch_id == batch.id,
                        SupplierReturn.supplier_id == supplier.id,
                        SupplierReturn.status != "CANCELLED",
                        SupplierReturn.id != supplier_return.id,
                    )
                )).scalar_one()
                returned_to_supplier += claimed_by_batch.get(batch.id, Decimal("0.00"))
                returnable_to_supplier = Decimal(str(received_from_supplier)) - Decimal(str(returned_to_supplier))
                if return_qty_base > returnable_to_supplier:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=(
                            f"Return quantity ({return_qty_base} units) exceeds returnable quantity "
                            f"({returnable_to_supplier} units) for batch '{batch.batch_number}' "
                            f"received from supplier '{supplier.name}'."
                        ),
                    )

            # 4. Check available stock in batch (including earlier lines of this return)
            if batch.current_stock_base - claimed_by_batch.get(batch.id, Decimal("0.00")) < return_qty_base:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"Cannot return {return_qty_base} units. Batch '{batch.batch_number}' "
                        f"only has {batch.current_stock_base} units in current stock."
                    ),
                )
            claimed_by_batch[batch.id] = claimed_by_batch.get(batch.id, Decimal("0.00")) + return_qty_base

            # 5. Financials
            line_gross = round_cur(item_data.return_qty_pack * item_data.refund_rate)
            line_tax = Decimal("0.00")
            if item_data.tax_rate_id:
                tr_res = await db.execute(
                    select(TaxRate).where(
                        TaxRate.id == item_data.tax_rate_id,
                        TaxRate.pharmacy_id == pharmacy_id,
                    )
                )
                tax_rate = tr_res.scalar_one_or_none()
                if tax_rate:
                    tax_calc = GstTaxCalculationService.calculate_tax(
                        amount=line_gross,
                        rate_percentage=tax_rate.rate_percentage,
                        is_tax_inclusive=False,
                    )
                    line_tax = tax_calc["total_tax"]

            line_total = line_gross + line_tax
            subtotal += line_gross
            tax_total += line_tax

            ret_item = SupplierReturnItem(
                return_id=supplier_return.id,
                grn_item_id=item_data.grn_item_id,
                invoice_item_id=item_data.invoice_item_id,
                medicine_id=medicine.id,
                batch_id=batch.id,
                packaging_unit_id=item_data.packaging_unit_id,
                conversion_factor=conversion_factor,
                return_qty_pack=item_data.return_qty_pack,
                return_qty_base=return_qty_base,
                unit_cost_base=batch.cost_per_base_unit,
                refund_rate=item_data.refund_rate,
                tax_rate_id=item_data.tax_rate_id,
                tax_amount=line_tax,
                total_amount=line_total,
                reason=item_data.reason,
            )
            db.add(ret_item)

        supplier_return.subtotal = round_cur(subtotal)
        supplier_return.tax_total = round_cur(tax_total)
        supplier_return.total_return_amount = round_cur(subtotal + tax_total)

        db.add(AuditLog(
            pharmacy_id=pharmacy_id,
            user_id=user_id,
            action="supplier_return.create",
            entity_type="supplier_return",
            entity_id=str(supplier_return.id),
            metadata_json=json.dumps({
                "return_number": supplier_return.return_number,
                "amount": str(supplier_return.total_return_amount),
            }),
        ))
        await db.flush()
        return await SupplierReturnService.get_return(db, pharmacy_id, supplier_return.id)

    @staticmethod
    async def get_return(db: AsyncSession, pharmacy_id: int, return_id: int) -> SupplierReturn:
        stmt = (
            select(SupplierReturn)
            .options(
                selectinload(SupplierReturn.supplier),
                selectinload(SupplierReturn.goods_receipt),
                selectinload(SupplierReturn.invoice),
                selectinload(SupplierReturn.items).selectinload(SupplierReturnItem.medicine),
                selectinload(SupplierReturn.items).selectinload(SupplierReturnItem.batch),
                selectinload(SupplierReturn.items).selectinload(SupplierReturnItem.packaging_unit),
                selectinload(SupplierReturn.items).selectinload(SupplierReturnItem.tax_rate),
            )
            .where(SupplierReturn.id == return_id, SupplierReturn.pharmacy_id == pharmacy_id)
        )
        ret = (await db.execute(stmt)).scalar_one_or_none()
        if not ret:
            raise HTTPException(status_code=404, detail="Supplier return not found")
        return ret

    @staticmethod
    async def list_returns(
        db: AsyncSession,
        pharmacy_id: int,
        supplier_id: Optional[int] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[SupplierReturn], int]:
        stmt = (
            select(SupplierReturn)
            .options(
                selectinload(SupplierReturn.supplier),
                selectinload(SupplierReturn.goods_receipt),
                selectinload(SupplierReturn.invoice),
                selectinload(SupplierReturn.items).selectinload(SupplierReturnItem.medicine),
                selectinload(SupplierReturn.items).selectinload(SupplierReturnItem.batch),
                selectinload(SupplierReturn.items).selectinload(SupplierReturnItem.packaging_unit),
            )
            .where(SupplierReturn.pharmacy_id == pharmacy_id)
        )
        if supplier_id:
            stmt = stmt.where(SupplierReturn.supplier_id == supplier_id)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await db.execute(count_stmt)).scalar_one()

        stmt = stmt.order_by(SupplierReturn.created_at.desc()).offset(offset).limit(limit)
        items = (await db.execute(stmt)).scalars().all()
        return list(items), total

    @staticmethod
    async def post_supplier_return(
        db: AsyncSession, pharmacy_id: int, return_id: int, user_id: int
    ) -> SupplierReturn:
        """
        Atomically posts supplier return:
          - Validates idempotency (if already POSTED, raises 409 Conflict)
          - Row-locks batches with `with_for_update()`
          - Deducts `batch.current_stock_base`
          - Writes immutable `StockMovement` (type `RETURN_OUTWARD`)
          - Marks return as `POSTED`
        """
        ret_q = select(SupplierReturn).where(
            SupplierReturn.id == return_id,
            SupplierReturn.pharmacy_id == pharmacy_id,
        ).with_for_update()
        supplier_return = (await db.execute(ret_q)).scalar_one_or_none()
        if not supplier_return:
            raise HTTPException(status_code=404, detail="Supplier return not found")

        if supplier_return.status == "POSTED":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Supplier return '{supplier_return.return_number}' is already posted",
            )
        if supplier_return.status == "CANCELLED":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot post cancelled return '{supplier_return.return_number}'",
            )

        items_q = select(SupplierReturnItem).where(SupplierReturnItem.return_id == supplier_return.id)
        items = (await db.execute(items_q)).scalars().all()

        for item in items:
            batch_q = select(MedicineBatch).where(
                MedicineBatch.id == item.batch_id,
                MedicineBatch.pharmacy_id == pharmacy_id,
            ).with_for_update()
            batch = (await db.execute(batch_q)).scalar_one()

            if batch.current_stock_base < item.return_qty_base:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"Insufficient stock to post return for batch '{batch.batch_number}'. "
                        f"Current stock: {batch.current_stock_base}, required return: {item.return_qty_base}"
                    ),
                )

            # Atomically reduce stock
            batch.current_stock_base = (batch.current_stock_base - item.return_qty_base).quantize(TWO_PLACES)

            # Record immutable stock ledger movement
            movement = StockMovement(
                pharmacy_id=pharmacy_id,
                medicine_id=item.medicine_id,
                batch_id=batch.id,
                movement_type="RETURN_OUTWARD",
                reference_id=supplier_return.return_number,
                reference_type="purchase_return",
                quantity_base=-item.return_qty_base,
                balance_after_base=batch.current_stock_base,
                unit_cost=item.unit_cost_base,
                # Inventory value removed at cost; the refund value is on the debit note
                total_cost=round_cur(item.return_qty_base * item.unit_cost_base),
                reason=f"Purchase Return: {supplier_return.reason_code}",
                notes=f"Debit Note: {supplier_return.debit_note_reference}",
                user_id=user_id,
            )
            db.add(movement)

        supplier_return.status = "POSTED"
        supplier_return.posted_at = utcnow()
        supplier_return.updated_at = utcnow()

        db.add(AuditLog(
            pharmacy_id=pharmacy_id,
            user_id=user_id,
            action="supplier_return.post",
            entity_type="supplier_return",
            entity_id=str(supplier_return.id),
            metadata_json=json.dumps({
                "return_number": supplier_return.return_number,
                "debit_note_ref": supplier_return.debit_note_reference,
                "amount": str(supplier_return.total_return_amount),
            }),
        ))
        await db.flush()
        await gl_posting.record(db, pharmacy_id, user_id, "SUPPLIER_RETURN", supplier_return.id)
        return await SupplierReturnService.get_return(db, pharmacy_id, supplier_return.id)
