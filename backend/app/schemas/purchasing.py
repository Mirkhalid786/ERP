"""
Pydantic v2 validation and serialization schemas for Phase 3: Suppliers and Purchasing.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
import re

from pydantic import BaseModel, Field, ConfigDict, field_validator


# ── GSTIN Validation Helper ───────────────────────────────────────────────────

def validate_gstin_format(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    cleaned = v.strip().upper()
    if not cleaned:
        return None
    pattern = r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$"
    if not re.match(pattern, cleaned):
        raise ValueError("GSTIN must be in the format: 29ABCDE1234F1Z5 (15 characters)")
    return cleaned


# ── Suppliers ──────────────────────────────────────────────────────────────────

# GST state codes (01-38 states/UTs, 97 Other Territory, 99 Centre Jurisdiction)
GST_STATE_CODES = {f"{n:02d}" for n in range(1, 39)} | {"97", "99"}
SUPPLIER_TEXT_FIELDS = ("legal_name", "drug_license_no", "contact_person", "phone", "email", "address",
                        "city", "state", "pincode", "notes")


class _SupplierFieldRules:
    """Shared supplier rules. Only the trade name is required: every other text field may be left blank,
    and a blank value means "not provided". A GSTIN, when given, must be valid; the state code then follows it."""

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator(*SUPPLIER_TEXT_FIELDS, mode="before")
    @classmethod
    def blank_to_none(cls, v):
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v

    @field_validator("gstin", mode="before")
    @classmethod
    def clean_gstin(cls, v):
        if isinstance(v, str):
            v = re.sub(r"[\s-]", "", v).upper()      # "27 aabca 1234f1z5" -> "27AABCA1234F1Z5"
            return v or None
        return v

    @field_validator("gstin")
    @classmethod
    def check_gstin(cls, v):
        return validate_gstin_format(v)

    @field_validator("state_code", mode="before")
    @classmethod
    def clean_state_code(cls, v):
        if isinstance(v, int):
            v = str(v)
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return None
            if v.isdigit() and len(v) == 1:
                v = "0" + v                           # "7" -> "07"
        return v

    @field_validator("state_code")
    @classmethod
    def check_state_code(cls, v, info):
        gstin = info.data.get("gstin")
        if v is None:
            return gstin[:2] if gstin else None       # Derived from the GSTIN when not given
        if v not in GST_STATE_CODES:
            raise ValueError("State code must be a GST state code from 01 to 38 (e.g. 27 for Maharashtra)")
        if gstin and gstin[:2] != v:
            raise ValueError(f"State code {v} does not match the GSTIN, which is registered in state {gstin[:2]}")
        return v


class SupplierBase(_SupplierFieldRules, BaseModel):
    name: str = Field(..., min_length=2, max_length=200)
    legal_name: Optional[str] = Field(None, max_length=255)
    gstin: Optional[str] = Field(None, max_length=15)
    state_code: Optional[str] = Field(None, max_length=2)
    drug_license_no: Optional[str] = Field(None, max_length=100)
    contact_person: Optional[str] = Field(None, max_length=100)
    phone: Optional[str] = Field(None, max_length=50)
    email: Optional[str] = Field(None, max_length=255)
    address: Optional[str] = None
    city: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=100)
    pincode: Optional[str] = Field(None, max_length=20)
    payment_terms_days: int = Field(default=30, ge=0, le=365)
    credit_limit: Decimal = Field(default=Decimal("0.00"), ge=0)
    notes: Optional[str] = None
    is_active: bool = True


class SupplierCreate(SupplierBase):
    pass


class SupplierUpdate(_SupplierFieldRules, BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=200)
    legal_name: Optional[str] = Field(None, max_length=255)
    gstin: Optional[str] = Field(None, max_length=15)
    state_code: Optional[str] = Field(None, max_length=2)
    drug_license_no: Optional[str] = Field(None, max_length=100)
    contact_person: Optional[str] = Field(None, max_length=100)
    phone: Optional[str] = Field(None, max_length=50)
    email: Optional[str] = Field(None, max_length=255)
    address: Optional[str] = None
    city: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=100)
    pincode: Optional[str] = Field(None, max_length=20)
    payment_terms_days: Optional[int] = Field(None, ge=0, le=365)
    credit_limit: Optional[Decimal] = Field(None, ge=0)
    notes: Optional[str] = None
    is_active: Optional[bool] = None


class SupplierResponse(SupplierBase):
    id: int
    pharmacy_id: int
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ── Purchase Orders ────────────────────────────────────────────────────────────

class PurchaseOrderItemCreate(BaseModel):
    medicine_id: int
    packaging_unit_id: int
    ordered_qty_pack: Decimal = Field(..., gt=0)
    purchase_rate: Decimal = Field(..., ge=0)
    discount_percent: Decimal = Field(default=Decimal("0.00"), ge=0, le=100)
    tax_rate_id: Optional[int] = None
    notes: Optional[str] = Field(None, max_length=255)


class PurchaseOrderItemResponse(BaseModel):
    id: int
    po_id: int
    medicine_id: int
    medicine_name: Optional[str] = None
    packaging_unit_id: int
    packaging_unit_name: Optional[str] = None
    conversion_factor: Decimal
    ordered_qty_pack: Decimal
    ordered_qty_base: Decimal
    received_qty_base: Decimal
    purchase_rate: Decimal
    discount_percent: Decimal
    tax_rate_id: Optional[int] = None
    tax_rate_percentage: Optional[Decimal] = None
    tax_amount: Decimal
    line_total: Decimal
    notes: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class PurchaseOrderCreate(BaseModel):
    supplier_id: int
    order_date: date
    expected_delivery_date: Optional[date] = None
    notes: Optional[str] = None
    items: List[PurchaseOrderItemCreate] = Field(..., min_length=1)


class PurchaseOrderUpdate(BaseModel):
    supplier_id: Optional[int] = None
    order_date: Optional[date] = None
    expected_delivery_date: Optional[date] = None
    notes: Optional[str] = None
    items: Optional[List[PurchaseOrderItemCreate]] = None


class PurchaseOrderStatusUpdate(BaseModel):
    status: str = Field(..., description="SUBMITTED, APPROVED, DRAFT, or CANCELLED")
    reason: Optional[str] = Field(None, max_length=500)


class PurchaseOrderResponse(BaseModel):
    id: int
    pharmacy_id: int
    po_number: str
    supplier_id: int
    supplier_name: Optional[str] = None
    order_date: date
    expected_delivery_date: Optional[date] = None
    status: str
    subtotal: Decimal
    discount_total: Decimal
    tax_total: Decimal
    grand_total: Decimal
    notes: Optional[str] = None
    created_by: int
    created_by_name: Optional[str] = None
    approved_by: Optional[int] = None
    approved_at: Optional[datetime] = None
    cancelled_by: Optional[int] = None
    cancelled_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    lifecycle_stage: Optional[str] = None
    goods_receipts_count: Optional[int] = 0
    invoices_count: Optional[int] = 0
    total_received_base: Optional[Decimal] = Decimal("0.00")
    total_ordered_base: Optional[Decimal] = Decimal("0.00")
    cancel_reason: Optional[str] = None
    items: List[PurchaseOrderItemResponse] = []
    model_config = ConfigDict(from_attributes=True)


# ── Goods Receipt Inward (GRN) ─────────────────────────────────────────────────

class GoodsReceiptItemCreate(BaseModel):
    po_item_id: Optional[int] = None
    medicine_id: int
    batch_number: str = Field(..., min_length=1, max_length=100)
    manufacturing_date: Optional[date] = None
    expiry_date: date
    packaging_unit_id: int
    received_qty_pack: Decimal = Field(default=Decimal("0.00"), ge=0)
    free_qty_pack: Decimal = Field(default=Decimal("0.00"), ge=0)
    rejected_qty_pack: Decimal = Field(default=Decimal("0.00"), ge=0)
    rejection_reason: Optional[str] = Field(None, max_length=255)
    purchase_rate: Decimal = Field(..., ge=0)
    discount_percent: Decimal = Field(default=Decimal("0.00"), ge=0, le=100)
    mrp: Decimal = Field(..., gt=0)
    selling_rate: Decimal = Field(..., gt=0)
    tax_rate_id: Optional[int] = None
    rack_location: Optional[str] = Field(None, max_length=100)


class GoodsReceiptItemResponse(BaseModel):
    id: int
    grn_id: int
    po_item_id: Optional[int] = None
    medicine_id: int
    medicine_name: Optional[str] = None
    batch_id: Optional[int] = None
    batch_number: str
    manufacturing_date: Optional[date] = None
    expiry_date: date
    packaging_unit_id: int
    packaging_unit_name: Optional[str] = None
    conversion_factor: Decimal
    received_qty_pack: Decimal
    free_qty_pack: Decimal
    rejected_qty_pack: Decimal
    accepted_qty_base: Decimal
    rejected_qty_base: Decimal
    rejection_reason: Optional[str] = None
    purchase_rate: Decimal
    discount_percent: Decimal
    mrp: Decimal
    selling_rate: Decimal
    cost_per_base_unit: Decimal
    tax_rate_id: Optional[int] = None
    cgst_amount: Decimal
    sgst_amount: Decimal
    igst_amount: Decimal
    tax_amount: Decimal
    line_total: Decimal
    rack_location: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class GoodsReceiptCreate(BaseModel):
    supplier_id: int
    po_id: Optional[int] = None
    receipt_date: date
    supplier_challan_no: Optional[str] = Field(None, max_length=100)
    supplier_invoice_no: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = None
    items: List[GoodsReceiptItemCreate] = Field(..., min_length=1)


class GoodsReceiptResponse(BaseModel):
    id: int
    pharmacy_id: int
    grn_number: str
    supplier_id: int
    supplier_name: Optional[str] = None
    po_id: Optional[int] = None
    po_number: Optional[str] = None
    receipt_date: date
    supplier_challan_no: Optional[str] = None
    supplier_invoice_no: Optional[str] = None
    status: str
    total_received_base: Decimal
    total_accepted_base: Decimal
    total_rejected_base: Decimal
    total_free_base: Decimal
    subtotal: Decimal
    tax_total: Decimal
    grand_total: Decimal
    notes: Optional[str] = None
    received_by: int
    received_by_name: Optional[str] = None
    posted_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    items: List[GoodsReceiptItemResponse] = []
    model_config = ConfigDict(from_attributes=True)


# ── Supplier Purchase Invoices ─────────────────────────────────────────────────

class SupplierInvoiceItemCreate(BaseModel):
    grn_item_id: Optional[int] = None
    medicine_id: int
    batch_number: str = Field(..., min_length=1, max_length=100)
    packaging_unit_id: int
    billed_qty_pack: Decimal = Field(..., gt=0)
    free_qty_pack: Decimal = Field(default=Decimal("0.00"), ge=0)
    purchase_rate: Decimal = Field(..., ge=0)
    discount_percent: Decimal = Field(default=Decimal("0.00"), ge=0, le=100)
    tax_rate_id: Optional[int] = None


class SupplierInvoiceItemResponse(BaseModel):
    id: int
    invoice_id: int
    grn_item_id: Optional[int] = None
    medicine_id: int
    medicine_name: Optional[str] = None
    batch_id: Optional[int] = None
    batch_number: str
    packaging_unit_id: int
    packaging_unit_name: Optional[str] = None
    conversion_factor: Decimal
    billed_qty_pack: Decimal
    billed_qty_base: Decimal
    free_qty_pack: Decimal
    purchase_rate: Decimal
    discount_percent: Decimal
    discount_amount: Decimal
    taxable_amount: Decimal
    tax_rate_id: Optional[int] = None
    cgst_amount: Decimal
    sgst_amount: Decimal
    igst_amount: Decimal
    tax_amount: Decimal
    line_total: Decimal
    model_config = ConfigDict(from_attributes=True)


class SupplierInvoiceCreate(BaseModel):
    supplier_id: int
    invoice_number: str = Field(..., min_length=1, max_length=100)
    grn_id: Optional[int] = None
    po_id: Optional[int] = None
    invoice_date: date
    received_date: date
    due_date: Optional[date] = None
    payment_terms_days: int = Field(default=30, ge=0)
    is_tax_inclusive: bool = False
    currency: str = "INR"
    other_charges: Decimal = Field(default=Decimal("0.00"), ge=0)
    round_off: Decimal = Field(default=Decimal("0.00"))
    supplier_declared_total: Decimal = Field(..., ge=0, description="Amount printed on physical supplier bill")
    discrepancy_notes: Optional[str] = None
    notes: Optional[str] = None
    replaces_invoice_id: Optional[int] = None   # Re-entry of a cancelled bill
    items: List[SupplierInvoiceItemCreate] = Field(..., min_length=1)


class SupplierInvoiceUpdate(BaseModel):
    due_date: Optional[date] = None
    other_charges: Optional[Decimal] = Field(None, ge=0)
    round_off: Optional[Decimal] = None
    supplier_declared_total: Optional[Decimal] = Field(None, ge=0)
    discrepancy_notes: Optional[str] = None
    notes: Optional[str] = None


class SupplierInvoiceResponse(BaseModel):
    id: int
    pharmacy_id: int
    invoice_number: str
    supplier_id: int
    supplier_name: Optional[str] = None
    grn_id: Optional[int] = None
    grn_number: Optional[str] = None
    po_id: Optional[int] = None
    po_number: Optional[str] = None
    invoice_date: date
    received_date: date
    due_date: date
    payment_terms_days: int
    is_tax_inclusive: bool
    currency: str
    subtotal: Decimal
    discount_total: Decimal
    cgst_total: Decimal
    sgst_total: Decimal
    igst_total: Decimal
    tax_total: Decimal
    other_charges: Decimal
    round_off: Decimal
    calculated_grand_total: Decimal
    supplier_declared_total: Decimal
    discrepancy_amount: Decimal
    discrepancy_notes: Optional[str] = None
    status: str
    payment_status: str
    paid_amount: Decimal
    returned_amount: Decimal = Decimal("0.00")
    outstanding_amount: Decimal = Decimal("0.00")
    notes: Optional[str] = None
    discrepancy_accepted: bool = False
    finalized_by: Optional[int] = None
    finalized_by_name: Optional[str] = None
    finalized_at: Optional[datetime] = None
    cancelled_by_name: Optional[str] = None
    cancelled_at: Optional[datetime] = None
    cancel_reason: Optional[str] = None
    replaces_invoice_id: Optional[int] = None
    created_by: int
    created_by_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    items: List[SupplierInvoiceItemResponse] = []
    model_config = ConfigDict(from_attributes=True)


# ── Supplier Returns ───────────────────────────────────────────────────────────

class SupplierReturnItemCreate(BaseModel):
    grn_item_id: Optional[int] = None
    invoice_item_id: Optional[int] = None
    medicine_id: int
    batch_id: int
    packaging_unit_id: int
    return_qty_pack: Decimal = Field(..., gt=0)
    refund_rate: Decimal = Field(..., ge=0)
    tax_rate_id: Optional[int] = None
    reason: Optional[str] = Field(None, max_length=255)


class SupplierReturnItemResponse(BaseModel):
    id: int
    return_id: int
    grn_item_id: Optional[int] = None
    invoice_item_id: Optional[int] = None
    medicine_id: int
    medicine_name: Optional[str] = None
    batch_id: int
    batch_number: Optional[str] = None
    packaging_unit_id: int
    packaging_unit_name: Optional[str] = None
    conversion_factor: Decimal
    return_qty_pack: Decimal
    return_qty_base: Decimal
    unit_cost_base: Decimal
    refund_rate: Decimal
    tax_rate_id: Optional[int] = None
    tax_amount: Decimal
    total_amount: Decimal
    reason: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class SupplierReturnCreate(BaseModel):
    supplier_id: int
    grn_id: Optional[int] = None
    invoice_id: Optional[int] = None
    return_date: date
    reason_code: str = Field(..., description="DAMAGED, EXPIRED, NEAR_EXPIRY, EXCESS_SUPPLY, RECALLED, WRONG_ITEM, OTHER")
    debit_note_reference: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = None
    items: List[SupplierReturnItemCreate] = Field(..., min_length=1)


class SupplierReturnResponse(BaseModel):
    id: int
    pharmacy_id: int
    return_number: str
    supplier_id: int
    supplier_name: Optional[str] = None
    grn_id: Optional[int] = None
    grn_number: Optional[str] = None
    invoice_id: Optional[int] = None
    invoice_number: Optional[str] = None
    return_date: date
    status: str
    reason_code: str
    subtotal: Decimal
    tax_total: Decimal
    total_return_amount: Decimal
    debit_note_reference: Optional[str] = None
    notes: Optional[str] = None
    created_by: int
    created_by_name: Optional[str] = None
    posted_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    items: List[SupplierReturnItemResponse] = []
    model_config = ConfigDict(from_attributes=True)


# ── Direct Purchase Entry ──────────────────────────────────────────────────────

class DirectPurchaseItemPayload(BaseModel):
    medicine_id: int
    packaging_unit_id: int
    quantity: Decimal = Field(..., gt=0, description="Purchased pack quantity")
    free_quantity: Decimal = Field(default=Decimal("0.00"), ge=0, description="Bonus free pack quantity")
    purchase_rate: Decimal = Field(..., ge=0, description="Purchase rate per pack")
    discount_percent: Decimal = Field(default=Decimal("0.00"), ge=0, le=100)
    tax_rate_id: Optional[int] = None
    batch_number: str = Field(..., min_length=1, max_length=100)
    manufacturing_date: Optional[date] = None
    expiry_date: date
    mrp: Decimal = Field(..., gt=0)
    selling_rate: Optional[Decimal] = Field(None, gt=0)
    rack_location: Optional[str] = Field(None, max_length=100)


class DirectPurchaseCreatePayload(BaseModel):
    supplier_id: int
    invoice_number: str = Field(..., min_length=1, max_length=100)
    invoice_date: date
    due_date: Optional[date] = None
    payment_terms_days: int = Field(default=30, ge=0)
    is_tax_inclusive: bool = False
    po_id: Optional[int] = None
    other_charges: Decimal = Field(default=Decimal("0.00"), ge=0)
    round_off: Decimal = Field(default=Decimal("0.00"))
    supplier_declared_total: Optional[Decimal] = None
    notes: Optional[str] = None
    post_now: bool = Field(default=True, description="True to save & post immediately; False to save draft")
    items: List[DirectPurchaseItemPayload] = Field(..., min_length=1)


class DirectPurchaseCancelPayload(BaseModel):
    reason: str = Field(..., min_length=3, max_length=500)


# ── Supplier Credit Notes (Financial) ──────────────────────────────────────────

class SupplierCreditNoteCreate(BaseModel):
    supplier_id: int
    credit_note_number: str = Field(..., min_length=1, max_length=100)
    note_date: date
    invoice_id: Optional[int] = None
    reason_code: str = Field(..., description="RATE_DIFFERENCE, SCHEME_DISCOUNT, EXPIRY_CLAIM, DAMAGE_CLAIM, SHORT_SUPPLY, OTHER")
    taxable_amount: Decimal = Field(..., ge=0)
    cgst_amount: Decimal = Field(default=Decimal("0.00"), ge=0)
    sgst_amount: Decimal = Field(default=Decimal("0.00"), ge=0)
    igst_amount: Decimal = Field(default=Decimal("0.00"), ge=0)
    total_amount: Decimal = Field(..., gt=0)
    notes: Optional[str] = None


class SupplierCreditNoteResponse(BaseModel):
    id: int
    pharmacy_id: int
    supplier_id: int
    supplier_name: Optional[str] = None
    credit_note_number: str
    note_date: date
    invoice_id: Optional[int] = None
    invoice_number: Optional[str] = None
    reason_code: str
    taxable_amount: Decimal
    cgst_amount: Decimal
    sgst_amount: Decimal
    igst_amount: Decimal
    total_amount: Decimal
    notes: Optional[str] = None
    status: str
    created_by: int
    created_at: datetime
    cancelled_by: Optional[int] = None
    cancelled_at: Optional[datetime] = None
    cancel_reason: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)
