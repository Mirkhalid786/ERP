"""
Pydantic v2 validation and serialization schemas for Phase 2: Medicine Catalogue & Inventory.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict, field_validator


# ── Tax Rates ──────────────────────────────────────────────────────────────────

class TaxRateBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    rate_percentage: Decimal = Field(..., ge=0, le=100)
    cgst_percentage: Decimal = Field(default=Decimal("0.00"), ge=0, le=100)
    sgst_percentage: Decimal = Field(default=Decimal("0.00"), ge=0, le=100)
    igst_percentage: Decimal = Field(default=Decimal("0.00"), ge=0, le=100)
    cess_percentage: Decimal = Field(default=Decimal("0.00"), ge=0, le=100)
    is_active: bool = True


class TaxRateCreate(TaxRateBase):
    pass


class TaxRateUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    rate_percentage: Optional[Decimal] = Field(None, ge=0, le=100)
    cgst_percentage: Optional[Decimal] = Field(None, ge=0, le=100)
    sgst_percentage: Optional[Decimal] = Field(None, ge=0, le=100)
    igst_percentage: Optional[Decimal] = Field(None, ge=0, le=100)
    cess_percentage: Optional[Decimal] = Field(None, ge=0, le=100)
    is_active: Optional[bool] = None


class TaxRateResponse(TaxRateBase):
    id: int
    pharmacy_id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ── Categories ─────────────────────────────────────────────────────────────────

class CategoryBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=255)
    is_active: bool = True


class CategoryCreate(CategoryBase):
    pass


class CategoryUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=255)
    is_active: Optional[bool] = None


class CategoryResponse(CategoryBase):
    id: int
    pharmacy_id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ── Manufacturers ──────────────────────────────────────────────────────────────

class ManufacturerBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    short_code: Optional[str] = Field(None, max_length=50)
    contact_email: Optional[str] = Field(None, max_length=255)
    contact_phone: Optional[str] = Field(None, max_length=50)
    is_active: bool = True


class ManufacturerCreate(ManufacturerBase):
    pass


class ManufacturerUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    short_code: Optional[str] = Field(None, max_length=50)
    contact_email: Optional[str] = Field(None, max_length=255)
    contact_phone: Optional[str] = Field(None, max_length=50)
    is_active: Optional[bool] = None


class ManufacturerResponse(ManufacturerBase):
    id: int
    pharmacy_id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ── Units ──────────────────────────────────────────────────────────────────────

class UnitBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    abbreviation: str = Field(..., min_length=1, max_length=20)
    is_fractional_allowed: bool = False
    is_active: bool = True

    @field_validator("abbreviation")
    @classmethod
    def upper_abbr(cls, v: str) -> str:
        return v.strip().upper()


class UnitCreate(UnitBase):
    pass


class UnitUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=50)
    abbreviation: Optional[str] = Field(None, min_length=1, max_length=20)
    is_fractional_allowed: Optional[bool] = None
    is_active: Optional[bool] = None


class UnitResponse(UnitBase):
    id: int
    pharmacy_id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ── Packaging Conversions ──────────────────────────────────────────────────────

class MedicineUnitConversionCreate(BaseModel):
    packaging_unit_id: int
    conversion_factor: Decimal = Field(..., gt=0)
    is_sales_unit: bool = True
    is_purchase_unit: bool = True
    is_default_dispensing_unit: bool = False


class MedicineUnitConversionResponse(BaseModel):
    id: int
    pharmacy_id: int
    medicine_id: int
    packaging_unit_id: int
    packaging_unit_name: Optional[str] = None
    packaging_unit_abbr: Optional[str] = None
    conversion_factor: Decimal
    is_sales_unit: bool
    is_purchase_unit: bool
    is_default_dispensing_unit: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ── Alternate Barcodes ────────────────────────────────────────────────────────

class MedicineBarcodeCreate(BaseModel):
    barcode: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=100)


class MedicineBarcodeResponse(BaseModel):
    id: int
    pharmacy_id: int
    medicine_id: int
    barcode: str
    description: Optional[str] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ── Medicines ──────────────────────────────────────────────────────────────────

class MedicineBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    brand_name: Optional[str] = Field(None, max_length=255)
    generic_name: str = Field(..., min_length=1, max_length=500)
    strength: Optional[str] = Field(None, max_length=100)
    dosage_form: str = Field(..., min_length=1, max_length=100)  # e.g. "Tablet", "Capsule", "Syrup"
    
    category_id: Optional[int] = None
    manufacturer_id: Optional[int] = None
    tax_rate_id: Optional[int] = None
    
    hsn_code: str = Field(default="3004", max_length=20)
    schedule_type: str = Field(default="NONE", max_length=20)  # "NONE", "H", "H1", "X", "G", "NARCOTIC", "OTC"
    requires_prescription: bool = False
    
    base_unit_id: int
    barcode: Optional[str] = Field(None, max_length=100)
    
    reorder_level: Decimal = Field(default=Decimal("0.00"), ge=0)
    reorder_quantity: Decimal = Field(default=Decimal("0.00"), ge=0)
    storage_instructions: Optional[str] = Field(None, max_length=255)
    side_effects: Optional[str] = None
    
    # Catalogue reference pricing and provenance
    mrp: Optional[Decimal] = Field(None, ge=0)
    is_discontinued: bool = False
    source_name: Optional[str] = Field(None, max_length=100)
    source_id: Optional[str] = Field(None, max_length=100)
    pack_size_label: Optional[str] = Field(None, max_length=255)

    is_active: bool = True

    @field_validator("schedule_type")
    @classmethod
    def validate_schedule(cls, v: str) -> str:
        valid = {"NONE", "H", "H1", "X", "G", "NARCOTIC", "OTC"}
        upper_v = v.strip().upper()
        if upper_v not in valid:
            raise ValueError(f"schedule_type must be one of {valid}")
        return upper_v


class MedicineCreate(MedicineBase):
    pass


class MedicineUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    brand_name: Optional[str] = Field(None, max_length=255)
    generic_name: Optional[str] = Field(None, min_length=1, max_length=500)
    strength: Optional[str] = Field(None, max_length=100)
    dosage_form: Optional[str] = Field(None, min_length=1, max_length=100)
    category_id: Optional[int] = None
    manufacturer_id: Optional[int] = None
    tax_rate_id: Optional[int] = None
    hsn_code: Optional[str] = Field(None, max_length=20)
    schedule_type: Optional[str] = None
    requires_prescription: Optional[bool] = None
    base_unit_id: Optional[int] = None
    barcode: Optional[str] = Field(None, max_length=100)
    reorder_level: Optional[Decimal] = Field(None, ge=0)
    reorder_quantity: Optional[Decimal] = Field(None, ge=0)
    storage_instructions: Optional[str] = None
    side_effects: Optional[str] = None
    mrp: Optional[Decimal] = Field(None, ge=0)
    is_discontinued: Optional[bool] = None
    source_name: Optional[str] = Field(None, max_length=100)
    source_id: Optional[str] = Field(None, max_length=100)
    pack_size_label: Optional[str] = Field(None, max_length=255)
    is_active: Optional[bool] = None


class MedicineResponse(MedicineBase):
    id: int
    pharmacy_id: int
    created_at: datetime
    updated_at: datetime
    category_name: Optional[str] = None
    manufacturer_name: Optional[str] = None
    tax_rate_percentage: Optional[Decimal] = None
    base_unit_name: Optional[str] = None
    base_unit_abbr: Optional[str] = None
    current_stock_base: Decimal = Decimal("0.00")
    model_config = ConfigDict(from_attributes=True)


# ── Batches ────────────────────────────────────────────────────────────────────

class MedicineBatchBase(BaseModel):
    batch_number: str = Field(..., min_length=1, max_length=100)
    manufacturing_date: Optional[date] = None
    expiry_date: date
    mrp: Decimal = Field(..., ge=0)
    purchase_rate: Decimal = Field(default=Decimal("0.00"), ge=0)
    cost_per_base_unit: Optional[Decimal] = None
    default_selling_rate: Optional[Decimal] = Field(None, ge=0)
    rack_location: Optional[str] = Field(None, max_length=100)
    is_active: bool = True

    @field_validator("batch_number")
    @classmethod
    def upper_batch(cls, v: str) -> str:
        return v.strip().upper()


class MedicineBatchCreate(MedicineBatchBase):
    medicine_id: int


class MedicineBatchUpdate(BaseModel):
    manufacturing_date: Optional[date] = None
    expiry_date: Optional[date] = None
    mrp: Optional[Decimal] = Field(None, ge=0)
    purchase_rate: Optional[Decimal] = Field(None, ge=0)
    cost_per_base_unit: Optional[Decimal] = Field(None, ge=0)
    default_selling_rate: Optional[Decimal] = Field(None, ge=0)
    rack_location: Optional[str] = Field(None, max_length=100)
    is_active: Optional[bool] = None


class MedicineBatchResponse(MedicineBatchBase):
    id: int
    pharmacy_id: int
    medicine_id: int
    medicine_name: Optional[str] = None
    default_selling_rate: Decimal
    current_stock_base: Decimal
    is_expired: bool
    stock_status: str = "ACTIVE"
    price_unit_id: Optional[int] = None
    price_conversion_factor: Decimal = Decimal("1.0000")
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class BatchStockStatusUpdate(BaseModel):
    stock_status: str = Field(..., description="ACTIVE, QUARANTINED, RECALLED or REJECTED")
    reason: str = Field(..., min_length=3, max_length=500)

    @field_validator("stock_status")
    @classmethod
    def check_status(cls, v: str) -> str:
        v = v.strip().upper()
        if v not in ("ACTIVE", "QUARANTINED", "RECALLED", "REJECTED"):
            raise ValueError("stock_status must be ACTIVE, QUARANTINED, RECALLED or REJECTED")
        return v


# ── Opening Stock & Movements ──────────────────────────────────────────────────

class OpeningStockCreate(BaseModel):
    medicine_id: int
    batch_number: str = Field(..., min_length=1, max_length=100)
    manufacturing_date: Optional[date] = None
    expiry_date: date
    packaging_unit_id: Optional[int] = None
    quantity_in_pack: Decimal = Field(..., gt=0)
    mrp: Decimal = Field(..., ge=0)
    purchase_rate: Decimal = Field(default=Decimal("0.00"), ge=0)
    default_selling_rate: Optional[Decimal] = Field(None, ge=0)
    rack_location: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = None


class StockMovementResponse(BaseModel):
    id: int
    pharmacy_id: int
    medicine_id: int
    medicine_name: Optional[str] = None
    batch_id: int
    batch_number: Optional[str] = None
    movement_type: str
    reference_id: Optional[str] = None
    reference_type: Optional[str] = None
    quantity_base: Decimal
    balance_after_base: Decimal
    unit_cost: Decimal
    total_cost: Decimal
    reason: Optional[str] = None
    notes: Optional[str] = None
    user_id: Optional[int] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ── Stock Adjustments ──────────────────────────────────────────────────────────

class StockAdjustmentItemIn(BaseModel):
    batch_id: int
    physical_stock_base: Decimal = Field(..., ge=0)
    notes: Optional[str] = None


class StockAdjustmentCreate(BaseModel):
    reason_code: str = Field(..., description="DAMAGE, BREAKAGE, EXPIRY, THEFT, AUDIT_DISCREPANCY, FOUND, CORRECTION")
    notes: Optional[str] = None
    items: List[StockAdjustmentItemIn] = Field(..., min_length=1)


class StockAdjustmentItemResponse(BaseModel):
    id: int
    medicine_id: int
    medicine_name: Optional[str] = None
    batch_id: int
    batch_number: Optional[str] = None
    system_stock_base: Decimal
    physical_stock_base: Decimal
    adjustment_qty_base: Decimal
    unit_cost: Decimal
    total_cost: Decimal
    notes: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class StockAdjustmentResponse(BaseModel):
    id: int
    pharmacy_id: int
    adjustment_number: str
    reason_code: str
    status: str
    notes: Optional[str] = None
    adjusted_by: int
    created_at: datetime
    items: List[StockAdjustmentItemResponse] = []
    model_config = ConfigDict(from_attributes=True)


# ── Reports & Alerts ───────────────────────────────────────────────────────────

class LowStockAlert(BaseModel):
    medicine_id: int
    medicine_name: str
    generic_name: str
    dosage_form: str
    reorder_level: Decimal
    current_stock_base: Decimal
    base_unit_name: str
    base_unit_abbr: str


class ExpiryAlert(BaseModel):
    batch_id: int
    medicine_id: int
    medicine_name: str
    batch_number: str
    expiry_date: date
    days_to_expiry: int
    current_stock_base: Decimal
    base_unit_abbr: str
    rack_location: Optional[str] = None
    cost_per_base_unit: Decimal = Decimal("0.0000")
    stock_value: Decimal = Decimal("0.00")          # Stock at batch cost (the value at risk)
    is_expired: bool
