"""
Medicines Catalogue API routes.
"""
import json
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select, func, or_, and_, case
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.deps import get_current_user, require_permission
from app.models.auth import User, AuditLog
from app.models.inventory import (
    Medicine, MedicineBarcode, MedicineUnitConversion, MedicineBatch,
    Category, Manufacturer, TaxRate, Unit,
)
from app.schemas.inventory import (
    MedicineCreate, MedicineUpdate, MedicineResponse,
    MedicineBarcodeCreate, MedicineBarcodeResponse,
    MedicineUnitConversionCreate, MedicineUnitConversionResponse,
)

router = APIRouter(prefix="/medicines", tags=["Medicines"])


def _build_medicine_response(m: Medicine, total_stock: Decimal = Decimal("0.00")) -> MedicineResponse:
    return MedicineResponse(
        id=m.id,
        pharmacy_id=m.pharmacy_id,
        name=m.name,
        brand_name=m.brand_name,
        generic_name=m.generic_name,
        strength=m.strength,
        dosage_form=m.dosage_form,
        category_id=m.category_id,
        category_name=m.category.name if m.category else None,
        manufacturer_id=m.manufacturer_id,
        manufacturer_name=m.manufacturer.name if m.manufacturer else None,
        tax_rate_id=m.tax_rate_id,
        tax_rate_percentage=m.tax_rate.rate_percentage if m.tax_rate else None,
        hsn_code=m.hsn_code,
        schedule_type=m.schedule_type,
        requires_prescription=m.requires_prescription,
        base_unit_id=m.base_unit_id,
        base_unit_name=m.base_unit.name if m.base_unit else None,
        base_unit_abbr=m.base_unit.abbreviation if m.base_unit else None,
        barcode=m.barcode,
        reorder_level=m.reorder_level,
        reorder_quantity=m.reorder_quantity,
        storage_instructions=m.storage_instructions,
        side_effects=m.side_effects,
        mrp=m.mrp,
        is_discontinued=m.is_discontinued,
        source_name=m.source_name,
        source_id=m.source_id,
        pack_size_label=m.pack_size_label,
        is_active=m.is_active,
        created_at=m.created_at,
        updated_at=m.updated_at,
        current_stock_base=total_stock,
    )


@router.get("", response_model=List[MedicineResponse])
async def list_medicines(
    response: Response,
    search: Optional[str] = Query(None),
    category_id: Optional[int] = Query(None),
    schedule_type: Optional[str] = Query(None),
    requires_prescription: Optional[bool] = Query(None),
    active_only: bool = Query(True),
    discontinued: Optional[bool] = Query(None),
    low_stock_only: Optional[bool] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List medicines with optional full-text search, filtering, low-stock filter and stock totals."""
    q = select(Medicine).where(Medicine.pharmacy_id == current_user.pharmacy_id)
    
    if active_only:
        q = q.where(Medicine.is_active == True)
    if discontinued is not None:
        q = q.where(Medicine.is_discontinued == discontinued)
    if category_id:
        q = q.where(Medicine.category_id == category_id)
    if schedule_type:
        q = q.where(Medicine.schedule_type == schedule_type.upper())
    if requires_prescription is not None:
        q = q.where(Medicine.requires_prescription == requires_prescription)

    if low_stock_only:
        batch_stock_subq = (
            select(
                MedicineBatch.medicine_id.label("med_id"),
                func.coalesce(func.sum(MedicineBatch.current_stock_base), Decimal("0.00")).label("stock_sum"),
            )
            .where(
                MedicineBatch.pharmacy_id == current_user.pharmacy_id,
                MedicineBatch.is_active == True,
            )
            .group_by(MedicineBatch.medicine_id)
            .subquery()
        )
        stock_val = func.coalesce(batch_stock_subq.c.stock_sum, Decimal("0.00"))
        q = q.outerjoin(batch_stock_subq, Medicine.id == batch_stock_subq.c.med_id).where(
            Medicine.is_discontinued == False,
            Medicine.reorder_level > Decimal("0.00"),
            stock_val <= Medicine.reorder_level,
        )
        
    order = [Medicine.name.asc()]
    if search:
        words = [w for w in search.strip().split()[:6] if w]
        if words:
            # Pre-lookup matching manufacturers to avoid expensive outer join across entire catalogue
            mfr_q = select(Manufacturer.id).where(
                Manufacturer.pharmacy_id == current_user.pharmacy_id,
                or_(*(Manufacturer.name.ilike(f"%{w}%") for w in words)),
            )
            mfr_res = await db.execute(mfr_q)
            matching_mfr_ids = mfr_res.scalars().all()

            for w in words:
                s = f"%{w}%"
                conds = [
                    Medicine.name.ilike(s),
                    Medicine.brand_name.ilike(s),
                    Medicine.generic_name.ilike(s),
                    Medicine.strength.ilike(s),
                    Medicine.dosage_form.ilike(s),
                    Medicine.barcode.ilike(s),
                ]
                if matching_mfr_ids:
                    conds.append(Medicine.manufacturer_id.in_(matching_mfr_ids))
                q = q.where(or_(*conds))

            order = [case((Medicine.name.ilike(f"{search.strip()}%"), 0), else_=1), Medicine.name.asc()]

    # Fetch paginated rows first
    fetch_q = q.options(
        selectinload(Medicine.category),
        selectinload(Medicine.manufacturer),
        selectinload(Medicine.tax_rate),
        selectinload(Medicine.base_unit),
        selectinload(Medicine.batches),
    ).order_by(*order).offset(offset).limit(limit)

    res = await db.execute(fetch_q)
    medicines = res.scalars().all()

    # Total count for pagination header:
    # If first page and fewer than `limit` rows returned, count is known immediately
    if offset == 0 and len(medicines) < limit:
        total_count = len(medicines)
    else:
        count_q = select(func.count()).select_from(q.order_by(None).subquery())
        total_count = (await db.execute(count_q)).scalar() or 0

    response.headers["X-Total-Count"] = str(total_count)
    response.headers["Access-Control-Expose-Headers"] = "X-Total-Count"

    output = []
    for m in medicines:
        # Sum current stock across active batches
        total_stock = sum(
            (b.current_stock_base for b in m.batches if b.is_active),
            start=Decimal("0.00"),
        )
        output.append(_build_medicine_response(m, total_stock))
    return output


@router.post("", response_model=MedicineResponse, status_code=status.HTTP_201_CREATED)
async def create_medicine(
    body: MedicineCreate,
    current_user: User = Depends(require_permission("medicines.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new medicine in the catalogue."""
    # Check duplicate barcode if barcode provided
    if body.barcode:
        b_clean = body.barcode.strip()
        dup_b = select(Medicine).where(
            Medicine.pharmacy_id == current_user.pharmacy_id,
            Medicine.barcode == b_clean,
        )
        dup_res = await db.execute(dup_b)
        if dup_res.scalar_one_or_none():
            raise HTTPException(status_code=409, detail=f"Barcode '{b_clean}' is already assigned to another medicine")

        # Check alternate barcodes as well
        alt_b = select(MedicineBarcode).where(
            MedicineBarcode.pharmacy_id == current_user.pharmacy_id,
            MedicineBarcode.barcode == b_clean,
        )
        alt_res = await db.execute(alt_b)
        if alt_res.scalar_one_or_none():
            raise HTTPException(status_code=409, detail=f"Barcode '{b_clean}' already exists in alternate barcodes")

    # Verify base unit exists
    u_q = select(Unit).where(Unit.id == body.base_unit_id, Unit.pharmacy_id == current_user.pharmacy_id)
    u_res = await db.execute(u_q)
    base_unit = u_res.scalar_one_or_none()
    if not base_unit:
        raise HTTPException(status_code=422, detail="Invalid base_unit_id")

    medicine = Medicine(
        pharmacy_id=current_user.pharmacy_id,
        name=body.name.strip(),
        brand_name=body.brand_name.strip() if body.brand_name else None,
        generic_name=body.generic_name.strip(),
        strength=body.strength.strip() if body.strength else None,
        dosage_form=body.dosage_form.strip(),
        category_id=body.category_id,
        manufacturer_id=body.manufacturer_id,
        tax_rate_id=body.tax_rate_id,
        hsn_code=body.hsn_code.strip() if body.hsn_code else "3004",
        schedule_type=body.schedule_type,
        requires_prescription=body.requires_prescription,
        base_unit_id=body.base_unit_id,
        barcode=body.barcode.strip() if body.barcode else None,
        reorder_level=body.reorder_level,
        reorder_quantity=body.reorder_quantity,
        storage_instructions=body.storage_instructions.strip() if body.storage_instructions else None,
        side_effects=body.side_effects.strip() if body.side_effects else None,
        mrp=body.mrp,
        is_discontinued=body.is_discontinued,
        source_name=body.source_name.strip() if body.source_name else None,
        source_id=body.source_id.strip() if body.source_id else None,
        pack_size_label=body.pack_size_label.strip() if body.pack_size_label else None,
        is_active=body.is_active,
    )
    db.add(medicine)
    await db.flush()

    db.add(AuditLog(
        pharmacy_id=current_user.pharmacy_id,
        user_id=current_user.id,
        action="medicine.create",
        entity_type="medicine",
        entity_id=str(medicine.id),
        metadata_json=json.dumps({"name": medicine.name, "generic": medicine.generic_name}),
    ))

    # Reload relationships for response
    q = select(Medicine).options(
        selectinload(Medicine.category),
        selectinload(Medicine.manufacturer),
        selectinload(Medicine.tax_rate),
        selectinload(Medicine.base_unit),
    ).where(Medicine.id == medicine.id)
    res = await db.execute(q)
    m = res.scalar_one()

    return _build_medicine_response(m, Decimal("0.00"))


@router.get("/lookup/barcode/{barcode}", response_model=MedicineResponse)
async def lookup_by_barcode(
    barcode: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Lookup medicine by primary barcode or alternate barcode."""
    b_clean = barcode.strip()
    # Check primary barcode
    q = select(Medicine).where(
        Medicine.pharmacy_id == current_user.pharmacy_id,
        Medicine.barcode == b_clean,
    )
    res = await db.execute(q)
    med = res.scalar_one_or_none()

    if not med:
        # Check alternate barcodes
        alt_q = select(MedicineBarcode).where(
            MedicineBarcode.pharmacy_id == current_user.pharmacy_id,
            MedicineBarcode.barcode == b_clean,
        )
        alt_res = await db.execute(alt_q)
        alt_item = alt_res.scalar_one_or_none()
        if alt_item:
            q = select(Medicine).where(Medicine.id == alt_item.medicine_id)
            res = await db.execute(q)
            med = res.scalar_one_or_none()

    if not med:
        raise HTTPException(status_code=404, detail=f"No medicine found with barcode '{b_clean}'")

    # Load with details
    q_full = select(Medicine).options(
        selectinload(Medicine.category),
        selectinload(Medicine.manufacturer),
        selectinload(Medicine.tax_rate),
        selectinload(Medicine.base_unit),
        selectinload(Medicine.batches),
    ).where(Medicine.id == med.id)
    r = await db.execute(q_full)
    m = r.scalar_one()

    total_stock = sum((b.current_stock_base for b in m.batches if b.is_active), start=Decimal("0.00"))
    return _build_medicine_response(m, total_stock)


@router.get("/{medicine_id}", response_model=MedicineResponse)
async def get_medicine(
    medicine_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve full details of a medicine."""
    q = select(Medicine).options(
        selectinload(Medicine.category),
        selectinload(Medicine.manufacturer),
        selectinload(Medicine.tax_rate),
        selectinload(Medicine.base_unit),
        selectinload(Medicine.batches),
    ).where(
        Medicine.id == medicine_id,
        Medicine.pharmacy_id == current_user.pharmacy_id,
    )
    res = await db.execute(q)
    m = res.scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="Medicine not found")

    total_stock = sum((b.current_stock_base for b in m.batches if b.is_active), start=Decimal("0.00"))
    return _build_medicine_response(m, total_stock)


@router.put("/{medicine_id}", response_model=MedicineResponse)
async def update_medicine(
    medicine_id: int,
    body: MedicineUpdate,
    current_user: User = Depends(require_permission("medicines.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Update medicine catalogue details."""
    q = select(Medicine).where(
        Medicine.id == medicine_id,
        Medicine.pharmacy_id == current_user.pharmacy_id,
    )
    res = await db.execute(q)
    med = res.scalar_one_or_none()
    if not med:
        raise HTTPException(status_code=404, detail="Medicine not found")

    old_vals = {"name": med.name, "barcode": med.barcode}

    # If updating barcode, ensure uniqueness
    if body.barcode is not None and body.barcode.strip() != med.barcode:
        b_clean = body.barcode.strip()
        if b_clean:
            dup_b = select(Medicine).where(
                Medicine.pharmacy_id == current_user.pharmacy_id,
                Medicine.barcode == b_clean,
                Medicine.id != medicine_id,
            )
            dup_res = await db.execute(dup_b)
            if dup_res.scalar_one_or_none():
                raise HTTPException(status_code=409, detail=f"Barcode '{b_clean}' already assigned to another medicine")

    if body.name is not None:
        med.name = body.name.strip()
    if body.brand_name is not None:
        med.brand_name = body.brand_name.strip() if body.brand_name else None
    if body.generic_name is not None:
        med.generic_name = body.generic_name.strip()
    if body.strength is not None:
        med.strength = body.strength.strip() if body.strength else None
    if body.dosage_form is not None:
        med.dosage_form = body.dosage_form.strip()
    if body.category_id is not None:
        med.category_id = body.category_id
    if body.manufacturer_id is not None:
        med.manufacturer_id = body.manufacturer_id
    if body.tax_rate_id is not None:
        med.tax_rate_id = body.tax_rate_id
    if body.hsn_code is not None:
        med.hsn_code = body.hsn_code.strip()
    if body.schedule_type is not None:
        med.schedule_type = body.schedule_type.upper()
    if body.requires_prescription is not None:
        med.requires_prescription = body.requires_prescription
    if body.barcode is not None:
        med.barcode = body.barcode.strip() if body.barcode else None
    if body.reorder_level is not None:
        med.reorder_level = body.reorder_level
    if body.reorder_quantity is not None:
        med.reorder_quantity = body.reorder_quantity
    if body.storage_instructions is not None:
        med.storage_instructions = body.storage_instructions.strip() if body.storage_instructions else None
    if body.side_effects is not None:
        med.side_effects = body.side_effects.strip() if body.side_effects else None
    if body.mrp is not None:
        med.mrp = body.mrp
    if body.is_discontinued is not None:
        med.is_discontinued = body.is_discontinued
    if body.source_name is not None:
        med.source_name = body.source_name.strip() if body.source_name else None
    if body.source_id is not None:
        med.source_id = body.source_id.strip() if body.source_id else None
    if body.pack_size_label is not None:
        med.pack_size_label = body.pack_size_label.strip() if body.pack_size_label else None
    if body.is_active is not None:
        med.is_active = body.is_active

    db.add(AuditLog(
        pharmacy_id=current_user.pharmacy_id,
        user_id=current_user.id,
        action="medicine.update",
        entity_type="medicine",
        entity_id=str(med.id),
        metadata_json=json.dumps({
            "old_values": old_vals,
            "new_values": {"name": med.name, "barcode": med.barcode},
        }),
    ))
    await db.flush()

    # Re-fetch full response
    q_full = select(Medicine).options(
        selectinload(Medicine.category),
        selectinload(Medicine.manufacturer),
        selectinload(Medicine.tax_rate),
        selectinload(Medicine.base_unit),
        selectinload(Medicine.batches),
    ).where(Medicine.id == med.id)
    r = await db.execute(q_full)
    m = r.scalar_one()

    total_stock = sum((b.current_stock_base for b in m.batches if b.is_active), start=Decimal("0.00"))
    return _build_medicine_response(m, total_stock)


# ── Alternate Barcodes Sub-resource ───────────────────────────────────────────

@router.get("/{medicine_id}/barcodes", response_model=List[MedicineBarcodeResponse])
async def list_alternate_barcodes(
    medicine_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List alternate barcodes registered for a medicine."""
    q = select(MedicineBarcode).where(
        MedicineBarcode.medicine_id == medicine_id,
        MedicineBarcode.pharmacy_id == current_user.pharmacy_id,
    )
    res = await db.execute(q)
    return res.scalars().all()


@router.post("/{medicine_id}/barcodes", response_model=MedicineBarcodeResponse, status_code=status.HTTP_201_CREATED)
async def add_alternate_barcode(
    medicine_id: int,
    body: MedicineBarcodeCreate,
    current_user: User = Depends(require_permission("medicines.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Register an alternate barcode for a medicine."""
    # Verify medicine exists
    m_q = select(Medicine).where(Medicine.id == medicine_id, Medicine.pharmacy_id == current_user.pharmacy_id)
    m_res = await db.execute(m_q)
    if not m_res.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Medicine not found")

    b_clean = body.barcode.strip()
    # Check duplicate in barcodes or medicines
    dup_q = select(MedicineBarcode).where(
        MedicineBarcode.pharmacy_id == current_user.pharmacy_id,
        MedicineBarcode.barcode == b_clean,
    )
    if (await db.execute(dup_q)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Barcode '{b_clean}' already registered")

    med_dup = select(Medicine).where(
        Medicine.pharmacy_id == current_user.pharmacy_id,
        Medicine.barcode == b_clean,
    )
    if (await db.execute(med_dup)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Barcode '{b_clean}' already in use as primary barcode")

    item = MedicineBarcode(
        pharmacy_id=current_user.pharmacy_id,
        medicine_id=medicine_id,
        barcode=b_clean,
        description=body.description.strip() if body.description else None,
    )
    db.add(item)
    await db.flush()
    return item


@router.delete("/{medicine_id}/barcodes/{barcode_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alternate_barcode(
    medicine_id: int,
    barcode_id: int,
    current_user: User = Depends(require_permission("medicines.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Delete an alternate barcode."""
    q = select(MedicineBarcode).where(
        MedicineBarcode.id == barcode_id,
        MedicineBarcode.medicine_id == medicine_id,
        MedicineBarcode.pharmacy_id == current_user.pharmacy_id,
    )
    res = await db.execute(q)
    item = res.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Barcode not found")
    await db.delete(item)
    await db.flush()


# ── Packaging Conversions Sub-resource ────────────────────────────────────────

@router.get("/{medicine_id}/conversions", response_model=List[MedicineUnitConversionResponse])
async def list_unit_conversions(
    medicine_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List packaging unit conversions for a medicine."""
    q = select(MedicineUnitConversion).options(
        selectinload(MedicineUnitConversion.packaging_unit)
    ).where(
        MedicineUnitConversion.medicine_id == medicine_id,
        MedicineUnitConversion.pharmacy_id == current_user.pharmacy_id,
    )
    res = await db.execute(q)
    items = res.scalars().all()
    return [
        MedicineUnitConversionResponse(
            id=c.id,
            pharmacy_id=c.pharmacy_id,
            medicine_id=c.medicine_id,
            packaging_unit_id=c.packaging_unit_id,
            packaging_unit_name=c.packaging_unit.name if c.packaging_unit else None,
            packaging_unit_abbr=c.packaging_unit.abbreviation if c.packaging_unit else None,
            conversion_factor=c.conversion_factor,
            is_sales_unit=c.is_sales_unit,
            is_purchase_unit=c.is_purchase_unit,
            is_default_dispensing_unit=c.is_default_dispensing_unit,
            created_at=c.created_at,
        )
        for c in items
    ]


@router.post("/{medicine_id}/conversions", response_model=MedicineUnitConversionResponse, status_code=status.HTTP_201_CREATED)
async def create_unit_conversion(
    medicine_id: int,
    body: MedicineUnitConversionCreate,
    current_user: User = Depends(require_permission("medicines.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Add a packaging unit conversion factor to base units."""
    m_q = select(Medicine).where(Medicine.id == medicine_id, Medicine.pharmacy_id == current_user.pharmacy_id)
    m_res = await db.execute(m_q)
    med = m_res.scalar_one_or_none()
    if not med:
        raise HTTPException(status_code=404, detail="Medicine not found")

    if body.packaging_unit_id == med.base_unit_id:
        raise HTTPException(status_code=422, detail="Packaging unit cannot be identical to the medicine's base unit")

    u_q = select(Unit).where(Unit.id == body.packaging_unit_id, Unit.pharmacy_id == current_user.pharmacy_id)
    u_res = await db.execute(u_q)
    pkg_unit = u_res.scalar_one_or_none()
    if not pkg_unit:
        raise HTTPException(status_code=422, detail="Packaging unit does not exist")

    # Check duplicate conversion pair
    dup_q = select(MedicineUnitConversion).where(
        MedicineUnitConversion.medicine_id == medicine_id,
        MedicineUnitConversion.packaging_unit_id == body.packaging_unit_id,
    )
    if (await db.execute(dup_q)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Conversion for unit '{pkg_unit.name}' already configured")

    conv = MedicineUnitConversion(
        pharmacy_id=current_user.pharmacy_id,
        medicine_id=medicine_id,
        packaging_unit_id=body.packaging_unit_id,
        conversion_factor=body.conversion_factor,
        is_sales_unit=body.is_sales_unit,
        is_purchase_unit=body.is_purchase_unit,
        is_default_dispensing_unit=body.is_default_dispensing_unit,
    )
    db.add(conv)
    await db.flush()

    return MedicineUnitConversionResponse(
        id=conv.id,
        pharmacy_id=conv.pharmacy_id,
        medicine_id=conv.medicine_id,
        packaging_unit_id=conv.packaging_unit_id,
        packaging_unit_name=pkg_unit.name,
        packaging_unit_abbr=pkg_unit.abbreviation,
        conversion_factor=conv.conversion_factor,
        is_sales_unit=conv.is_sales_unit,
        is_purchase_unit=conv.is_purchase_unit,
        is_default_dispensing_unit=conv.is_default_dispensing_unit,
        created_at=conv.created_at,
    )


@router.delete("/{medicine_id}/conversions/{conversion_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_unit_conversion(
    medicine_id: int,
    conversion_id: int,
    current_user: User = Depends(require_permission("medicines.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Delete a unit conversion."""
    q = select(MedicineUnitConversion).where(
        MedicineUnitConversion.id == conversion_id,
        MedicineUnitConversion.medicine_id == medicine_id,
        MedicineUnitConversion.pharmacy_id == current_user.pharmacy_id,
    )
    res = await db.execute(q)
    item = res.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Conversion not found")
    await db.delete(item)
    await db.flush()
