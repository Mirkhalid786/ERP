"""
SQLAlchemy ORM models for Phase 2: Medicine Catalogue & Inventory Management.

Covers:
  - Tax rates (GST slabs)
  - Medicine categories
  - Manufacturers
  - Packaging & dispensing units
  - Medicines catalogue (with HSN, drug schedule, barcodes)
  - Alternate barcodes
  - Packaging unit conversions
  - Medicine batches (with MRP, purchase cost, expiry, rack location)
  - Stock movements (immutable inventory ledger)
  - Stock adjustments (audited physical adjustments)
"""
from datetime import datetime, timezone, date
from decimal import Decimal
from typing import Optional, List

from sqlalchemy import (
    Boolean, DateTime, Date, ForeignKey, Integer, Numeric,
    String, Text, UniqueConstraint, Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.mysql import BIGINT as MYSQL_BIGINT

from app.core.database import Base
from app.core.clock import today_local

# Cross-database BigInteger: BIGINT UNSIGNED on MySQL, INTEGER on SQLite
PK_BIGINT = Integer().with_variant(MYSQL_BIGINT(unsigned=True), "mysql")
FK_BIGINT = Integer().with_variant(MYSQL_BIGINT(unsigned=True), "mysql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── GST Tax Rates ──────────────────────────────────────────────────────────────

class TaxRate(Base):
    """
    Configurable GST tax rate slab.
    Indian retail pharmacies commonly deal with 0%, 5%, 12%, 18%, 28%, and Nil/Exempt.
    Supports splitting into intra-state (CGST + SGST) or inter-state (IGST).
    """
    __tablename__ = "tax_rates"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g., "GST 12%", "GST 5%", "Exempt"
    rate_percentage: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)  # e.g. 12.00
    cgst_percentage: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal("0.00"))  # e.g. 6.00
    sgst_percentage: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal("0.00"))  # e.g. 6.00
    igst_percentage: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal("0.00"))  # e.g. 12.00
    cess_percentage: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal("0.00"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("pharmacy_id", "name", name="uq_tax_rates_pharmacy_name"),
    )


# ── Medicine Categories ────────────────────────────────────────────────────────

class Category(Base):
    """
    Therapeutic / operational medicine category (e.g. Antibiotics, Analgesics, Cardiac).
    """
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("pharmacy_id", "name", name="uq_categories_pharmacy_name"),
    )


# ── Manufacturers ──────────────────────────────────────────────────────────────

class Manufacturer(Base):
    """
    Pharmaceutical manufacturing company (e.g. Sun Pharma, Cipla, Dr. Reddy's).
    """
    __tablename__ = "manufacturers"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    short_code: Mapped[Optional[str]] = mapped_column(String(50))
    contact_email: Mapped[Optional[str]] = mapped_column(String(255))
    contact_phone: Mapped[Optional[str]] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("pharmacy_id", "name", name="uq_manufacturers_pharmacy_name"),
    )


# ── Units of Measurement ───────────────────────────────────────────────────────

class Unit(Base):
    """
    Master units of measurement (Tablet, Capsule, Strip, Box, Bottle, Vial, ml, etc.).
    """
    __tablename__ = "units"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)          # e.g., "Tablet", "Strip", "Milliliter"
    abbreviation: Mapped[str] = mapped_column(String(20), nullable=False)  # e.g., "TAB", "STRIP", "ML"
    is_fractional_allowed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # True for fluids/powders
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("pharmacy_id", "name", name="uq_units_pharmacy_name"),
        UniqueConstraint("pharmacy_id", "abbreviation", name="uq_units_pharmacy_abbr"),
    )


# ── Medicines ──────────────────────────────────────────────────────────────────

class Medicine(Base):
    """
    Core medicine catalogue item.
    Stock balance is ALWAYS accounted in `base_unit_id` (e.g. 1 Tablet or 1 ml).
    """
    __tablename__ = "medicines"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    brand_name: Mapped[Optional[str]] = mapped_column(String(255))
    generic_name: Mapped[str] = mapped_column(String(500), nullable=False)  # Active molecule / salt composition
    strength: Mapped[Optional[str]] = mapped_column(String(100))           # e.g., "500 mg", "625 Duo"
    dosage_form: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g., "Tablet", "Capsule", "Syrup"
    
    category_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("categories.id", ondelete="RESTRICT"), nullable=True)
    manufacturer_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("manufacturers.id", ondelete="RESTRICT"), nullable=True)
    tax_rate_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("tax_rates.id", ondelete="RESTRICT"), nullable=True)
    
    hsn_code: Mapped[str] = mapped_column(String(20), default="3004", nullable=False)
    schedule_type: Mapped[str] = mapped_column(String(20), default="NONE", nullable=False)  # "NONE", "H", "H1", "X", "G", "NARCOTIC", "OTC"
    requires_prescription: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    
    base_unit_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("units.id", ondelete="RESTRICT"), nullable=False)
    barcode: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    
    reorder_level: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    reorder_quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    storage_instructions: Mapped[Optional[str]] = mapped_column(String(255))
    side_effects: Mapped[Optional[str]] = mapped_column(Text)
    
    # Catalogue reference pricing and provenance (separate from batches/physical stock)
    mrp: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)  # Reference catalogue MRP
    is_discontinued: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # e.g., "junioralive/Indian-Medicine-Dataset"
    source_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)    # Source dataset row ID
    pack_size_label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # e.g., "strip of 10 tablets"

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    # Relationships
    category: Mapped[Optional["Category"]] = relationship()
    manufacturer: Mapped[Optional["Manufacturer"]] = relationship()
    tax_rate: Mapped[Optional["TaxRate"]] = relationship()
    base_unit: Mapped["Unit"] = relationship()
    barcodes: Mapped[List["MedicineBarcode"]] = relationship(back_populates="medicine", cascade="all, delete-orphan")
    conversions: Mapped[List["MedicineUnitConversion"]] = relationship(back_populates="medicine", cascade="all, delete-orphan")
    batches: Mapped[List["MedicineBatch"]] = relationship(back_populates="medicine")

    __table_args__ = (
        Index("ix_medicines_pharmacy_name", "pharmacy_id", "name"),
        Index("ix_medicines_pharmacy_generic", "pharmacy_id", "generic_name"),
        Index("ix_medicines_pharmacy_barcode", "pharmacy_id", "barcode"),
        Index("ix_medicines_pharmacy_brand", "pharmacy_id", "brand_name"),
        Index("ix_medicines_pharmacy_mfr", "pharmacy_id", "manufacturer_id"),
        Index("ix_medicines_pharmacy_source", "pharmacy_id", "source_name", "source_id"),
    )


# ── Alternate Barcodes ────────────────────────────────────────────────────────

class MedicineBarcode(Base):
    """
    Alternate or packaging-specific barcodes for a medicine.
    """
    __tablename__ = "medicine_barcodes"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False)
    medicine_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("medicines.id", ondelete="CASCADE"), nullable=False, index=True)
    barcode: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(100))  # e.g., "Strip barcode", "Box barcode"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    medicine: Mapped["Medicine"] = relationship(back_populates="barcodes")

    __table_args__ = (
        UniqueConstraint("pharmacy_id", "barcode", name="uq_medicine_barcodes_pharmacy_barcode"),
    )


# ── Unit Conversions ──────────────────────────────────────────────────────────

class MedicineUnitConversion(Base):
    """
    Medicine-specific packaging conversion factor to the medicine's base unit.
    Example:
      Base unit of Paracetamol = Tablet
      Packaging unit = Strip, conversion_factor = 10.0000 (1 Strip = 10 Tablets)
      Packaging unit = Box, conversion_factor = 100.0000 (1 Box = 100 Tablets)
    """
    __tablename__ = "medicine_unit_conversions"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False)
    medicine_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("medicines.id", ondelete="CASCADE"), nullable=False, index=True)
    packaging_unit_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("units.id", ondelete="RESTRICT"), nullable=False)
    conversion_factor: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)  # Multiplier to get base units
    is_sales_unit: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_purchase_unit: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_default_dispensing_unit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    medicine: Mapped["Medicine"] = relationship(back_populates="conversions")
    packaging_unit: Mapped["Unit"] = relationship()

    __table_args__ = (
        UniqueConstraint("medicine_id", "packaging_unit_id", name="uq_medicine_unit_conversions_pair"),
    )


# ── Batches ────────────────────────────────────────────────────────────────────

class MedicineBatch(Base):
    """
    Physical medicine batch with expiry, pricing snapshots, and rack location.
    `current_stock_base` tracks the current quantity remaining in base units.
    """
    __tablename__ = "medicine_batches"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False)
    medicine_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("medicines.id", ondelete="RESTRICT"), nullable=False, index=True)
    batch_number: Mapped[str] = mapped_column(String(100), nullable=False)
    manufacturing_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    
    mrp: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)              # Printed MRP
    purchase_rate: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)     # Cost per pack / unit
    cost_per_base_unit: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False) # Normalized cost per base unit
    default_selling_rate: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False) # Retail selling price
    
    rack_location: Mapped[Optional[str]] = mapped_column(String(100))                 # e.g. "Rack B-2, Shelf 3"
    current_stock_base: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    # MRP / purchase / selling rates above are quoted per this unit (e.g. per Strip of 15).
    # NULL / factor 1 means per base unit.
    price_unit_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("units.id", ondelete="RESTRICT"), nullable=True)
    price_conversion_factor: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("1.0000"), nullable=False)
    # ACTIVE batches are saleable (if unexpired); QUARANTINED / RECALLED / REJECTED never are.
    stock_status: Mapped[str] = mapped_column(String(20), default="ACTIVE", nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    medicine: Mapped["Medicine"] = relationship(back_populates="batches")
    movements: Mapped[List["StockMovement"]] = relationship(back_populates="batch")

    __table_args__ = (
        UniqueConstraint("pharmacy_id", "medicine_id", "batch_number", name="uq_medicine_batches_num"),
        Index("ix_medicine_batches_pharmacy_exp", "pharmacy_id", "expiry_date"),
        Index("ix_medicine_batches_pharmacy_med", "pharmacy_id", "medicine_id"),
    )

    @property
    def is_expired(self) -> bool:
        return self.expiry_date < today_local()


# ── Inventory Stock Movements ──────────────────────────────────────────────────

class StockMovement(Base):
    """
    Immutable inventory movement ledger.
    Every balance change (opening stock, adjustment, goods receipt) is recorded here.
    Quantity is in base units:
      Positive = Stock Added (Opening stock, Adjustment in, Purchase receipt)
      Negative = Stock Deducted (Adjustment out, Sales dispense, Purchase return)
    """
    __tablename__ = "stock_movements"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False, index=True)
    medicine_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("medicines.id", ondelete="RESTRICT"), nullable=False, index=True)
    batch_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("medicine_batches.id", ondelete="RESTRICT"), nullable=False, index=True)
    
    movement_type: Mapped[str] = mapped_column(String(50), nullable=False)  # "OPENING_STOCK", "ADJUSTMENT_ADD", "ADJUSTMENT_SUB", etc.
    reference_id: Mapped[Optional[str]] = mapped_column(String(100))       # e.g., Adjustment Number "ADJ-001"
    reference_type: Mapped[Optional[str]] = mapped_column(String(50))     # e.g., "stock_adjustment", "opening_stock"
    
    quantity_base: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)       # Signed delta
    balance_after_base: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)  # Snapshot of batch balance after movement
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0.00"), nullable=False)
    total_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    
    reason: Mapped[Optional[str]] = mapped_column(String(255))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    user_id: Mapped[Optional[int]] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)

    medicine: Mapped["Medicine"] = relationship()
    batch: Mapped["MedicineBatch"] = relationship(back_populates="movements")

    __table_args__ = (
        Index("ix_stock_movements_pharmacy_med_time", "pharmacy_id", "medicine_id", "created_at"),
        Index("ix_stock_movements_pharmacy_batch_time", "pharmacy_id", "batch_id", "created_at"),
        Index("ix_stock_movements_type", "pharmacy_id", "movement_type"),
    )


# ── Stock Adjustments ──────────────────────────────────────────────────────────

class StockAdjustment(Base):
    """
    Physical stock verification adjustment session / voucher.
    Contains header metadata (reason, approval, notes) and line items.
    """
    __tablename__ = "stock_adjustments"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False, index=True)
    adjustment_number: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g. "ADJ-202610-0001"
    reason_code: Mapped[str] = mapped_column(String(50), nullable=False)        # "DAMAGE", "BREAKAGE", "EXPIRY", "THEFT", "AUDIT_DISCREPANCY", "FOUND", "CORRECTION"
    status: Mapped[str] = mapped_column(String(20), default="POSTED", nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    adjusted_by: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    items: Mapped[List["StockAdjustmentItem"]] = relationship(back_populates="adjustment", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("pharmacy_id", "adjustment_number", name="uq_stock_adjustments_num"),
    )


class StockAdjustmentItem(Base):
    """
    Item line in a physical stock adjustment voucher.
    """
    __tablename__ = "stock_adjustment_items"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    adjustment_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("stock_adjustments.id", ondelete="CASCADE"), nullable=False, index=True)
    medicine_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("medicines.id", ondelete="RESTRICT"), nullable=False)
    batch_id: Mapped[int] = mapped_column(FK_BIGINT, ForeignKey("medicine_batches.id", ondelete="RESTRICT"), nullable=False)
    
    system_stock_base: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    physical_stock_base: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    adjustment_qty_base: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)  # physical - system
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0.00"), nullable=False)
    total_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(String(255))

    adjustment: Mapped["StockAdjustment"] = relationship(back_populates="items")
    medicine: Mapped["Medicine"] = relationship()
    batch: Mapped["MedicineBatch"] = relationship()
