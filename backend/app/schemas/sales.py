"""Pydantic schemas for Phase 4: POS, sales, payments, customers, returns, shifts, messaging."""
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.purchasing import validate_gstin_format

METHODS = ("CASH", "UPI", "CARD", "BANK_TRANSFER", "OTHER")


def _upper_choice(value: str, choices, field: str) -> str:
    v = value.strip().upper()
    if v not in choices:
        raise ValueError(f"{field} must be one of: {', '.join(choices)}")
    return v


# ── Customers ──────────────────────────────────────────────────────────────────

class CustomerBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=200)
    mobile: Optional[str] = Field(None, max_length=20)
    email: Optional[str] = Field(None, max_length=255)
    address: Optional[str] = None
    gstin: Optional[str] = Field(None, max_length=15)
    business_name: Optional[str] = Field(None, max_length=255)
    notes: Optional[str] = None
    credit_limit: Decimal = Field(default=Decimal("0.00"), ge=0)
    payment_terms_days: int = Field(default=30, ge=0, le=365)
    whatsapp_consent: bool = False
    reminders_opt_out: bool = False
    preferred_language: str = Field(default="en", max_length=10)
    is_active: bool = True

    @field_validator("gstin")
    @classmethod
    def check_gstin(cls, v):
        return validate_gstin_format(v)


class CustomerCreate(CustomerBase):
    pass


class CustomerUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=200)
    mobile: Optional[str] = Field(None, max_length=20)
    email: Optional[str] = Field(None, max_length=255)
    address: Optional[str] = None
    gstin: Optional[str] = Field(None, max_length=15)
    business_name: Optional[str] = Field(None, max_length=255)
    notes: Optional[str] = None
    credit_limit: Optional[Decimal] = Field(None, ge=0)
    payment_terms_days: Optional[int] = Field(None, ge=0, le=365)
    whatsapp_consent: Optional[bool] = None
    reminders_opt_out: Optional[bool] = None
    preferred_language: Optional[str] = Field(None, max_length=10)
    is_active: Optional[bool] = None

    @field_validator("gstin")
    @classmethod
    def check_gstin(cls, v):
        return validate_gstin_format(v)


class CustomerResponse(BaseModel):
    id: int
    name: str
    mobile: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    gstin: Optional[str] = None
    state_code: Optional[str] = None
    business_name: Optional[str] = None
    notes: Optional[str] = None
    credit_limit: Decimal
    payment_terms_days: int
    whatsapp_consent: bool
    whatsapp_consent_at: Optional[datetime] = None
    reminders_opt_out: bool
    preferred_language: str
    is_active: bool
    outstanding_balance: Decimal = Decimal("0.00")
    overdue_amount: Decimal = Decimal("0.00")
    available_credit: Decimal = Decimal("0.00")
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ── Sale finalisation ──────────────────────────────────────────────────────────

class SaleLineIn(BaseModel):
    medicine_id: int
    unit_id: int
    quantity: Decimal = Field(..., gt=0)
    batch_id: Optional[int] = None           # Pin a batch; otherwise FEFO
    discount_percent: Decimal = Field(default=Decimal("0.00"), ge=0, le=100)


class SalePaymentIn(BaseModel):
    method: str
    amount: Optional[Decimal] = Field(None, gt=0)       # Applied to the bill; for CASH may be omitted
    tendered: Optional[Decimal] = Field(None, gt=0)     # CASH only: notes handed over
    reference: Optional[str] = Field(None, max_length=100)

    @field_validator("method")
    @classmethod
    def check_method(cls, v):
        return _upper_choice(v, METHODS, "method")


class PrescriptionIn(BaseModel):
    prescription_reference: str = Field(..., min_length=1, max_length=100)
    prescription_date: Optional[date] = None
    prescriber_name: str = Field(..., min_length=2, max_length=200)
    prescriber_registration_no: Optional[str] = Field(None, max_length=100)
    prescriber_address: Optional[str] = None
    patient_name: str = Field(..., min_length=2, max_length=200)
    patient_address: Optional[str] = None


class SaleFinalizeRequest(BaseModel):
    idempotency_key: str = Field(..., min_length=8, max_length=64)
    customer_id: Optional[int] = None
    items: List[SaleLineIn] = Field(..., min_length=1)
    payments: List[SalePaymentIn] = Field(default_factory=list)
    due_date: Optional[date] = None
    credit_override_reason: Optional[str] = Field(None, max_length=500)
    prescription: Optional[PrescriptionIn] = None
    notes: Optional[str] = Field(None, max_length=1000)


class SaleQuoteRequest(BaseModel):
    customer_id: Optional[int] = None
    items: List[SaleLineIn] = Field(..., min_length=1)


class InvoiceItemResponse(BaseModel):
    id: int
    line_no: int
    medicine_id: int
    batch_id: int
    medicine_name: str
    hsn_code: Optional[str] = None
    schedule_type: Optional[str] = None
    batch_number: str
    expiry_date: date
    unit_id: int
    unit_name: str
    conversion_factor: Decimal
    quantity: Decimal
    quantity_base: Decimal
    mrp: Decimal
    selling_rate: Decimal
    gross_amount: Decimal
    discount_percent: Decimal
    discount_amount: Decimal
    taxable_amount: Decimal
    tax_rate: Decimal
    cgst_amount: Decimal
    sgst_amount: Decimal
    igst_amount: Decimal
    tax_amount: Decimal
    line_total: Decimal
    returned_quantity_base: Decimal
    model_config = ConfigDict(from_attributes=True)


class PaymentSummary(BaseModel):
    id: int
    receipt_number: str
    method: str
    source: str
    amount: Decimal              # Payment total
    allocated_to_invoice: Decimal
    amount_tendered: Optional[Decimal] = None
    change_returned: Decimal
    reference: Optional[str] = None
    verification_status: str
    status: str
    received_by_name: Optional[str] = None
    received_at: datetime


class RefundSummary(BaseModel):
    id: int
    method: str
    amount: Decimal
    reference: Optional[str] = None
    reason: str
    return_id: Optional[int] = None
    refunded_at: datetime


class ReturnBrief(BaseModel):
    id: int
    return_number: str
    total_amount: Decimal
    refund_amount: Decimal
    applied_to_balance: Decimal
    credit_note_amount: Decimal
    created_at: datetime


class InvoiceResponse(BaseModel):
    id: int
    invoice_number: str
    invoice_date: datetime
    status: str
    customer_id: Optional[int] = None
    customer_name: str
    customer_mobile: Optional[str] = None
    customer_gstin: Optional[str] = None
    is_inter_state: bool
    cashier_id: int
    cashier_name: Optional[str] = None
    gross_amount: Decimal
    discount_total: Decimal
    taxable_total: Decimal
    cgst_total: Decimal
    sgst_total: Decimal
    igst_total: Decimal
    tax_total: Decimal
    round_off: Decimal
    grand_total: Decimal
    amount_paid: Decimal
    paid_at_sale: Decimal
    return_credit_applied: Decimal
    returned_value: Decimal
    refunded_amount: Decimal
    written_off_amount: Decimal
    balance_due: Decimal
    payment_status: str
    return_status: str
    is_credit_sale: bool
    due_date: Optional[date] = None
    overdue_days: int = 0
    prescription_required: bool
    has_prescription: bool = False
    reminders_paused: bool
    is_disputed: bool
    dispute_note: Optional[str] = None
    notes: Optional[str] = None
    print_count: int
    voided_at: Optional[datetime] = None
    void_reason: Optional[str] = None
    replayed: bool = False                     # True when an idempotent retry returned the original sale
    change_due: Decimal = Decimal("0.00")
    items: List[InvoiceItemResponse] = []
    payments: List[PaymentSummary] = []
    refunds: List[RefundSummary] = []
    returns: List[ReturnBrief] = []


class InvoiceListItem(BaseModel):
    id: int
    invoice_number: str
    invoice_date: datetime
    status: str
    customer_id: Optional[int] = None
    customer_name: str
    customer_mobile: Optional[str] = None
    cashier_name: Optional[str] = None
    grand_total: Decimal
    amount_paid: Decimal
    balance_due: Decimal
    payment_status: str
    return_status: str
    is_credit_sale: bool
    due_date: Optional[date] = None
    overdue_days: int = 0
    payment_methods: str = ""


class InvoiceListResponse(BaseModel):
    items: List[InvoiceListItem]
    total: int
    limit: int
    offset: int


class PrescriptionResponse(PrescriptionIn):
    verified_by_name: Optional[str] = None
    created_at: datetime


class VoidRequest(BaseModel):
    reason: str = Field(..., min_length=5, max_length=500)
    settlement: str = Field(default="REFUND", description="REFUND money paid, or keep it as CUSTOMER_CREDIT")

    @field_validator("settlement")
    @classmethod
    def check_settlement(cls, v):
        return _upper_choice(v, ("REFUND", "CUSTOMER_CREDIT"), "settlement")


class ReminderControlRequest(BaseModel):
    reminders_paused: Optional[bool] = None
    is_disputed: Optional[bool] = None
    dispute_note: Optional[str] = Field(None, max_length=500)


class PrintRecordResponse(BaseModel):
    copy_label: str
    print_count: int
    pharmacy: dict = {}          # Header details for the printed bill
    footer_note: str = ""
    receipt_footer: str = ""     # Thermal-receipt footer (branding); empty = use footer_note
    website: str = ""


# ── Payments ───────────────────────────────────────────────────────────────────

class AllocationIn(BaseModel):
    invoice_id: int
    amount: Decimal = Field(..., gt=0)


class CollectionRequest(BaseModel):
    idempotency_key: str = Field(..., min_length=8, max_length=64)
    method: str
    amount: Decimal = Field(..., gt=0)
    tendered: Optional[Decimal] = Field(None, gt=0)
    reference: Optional[str] = Field(None, max_length=100)
    allocations: Optional[List[AllocationIn]] = None   # None = oldest invoices first
    notes: Optional[str] = Field(None, max_length=500)

    @field_validator("method")
    @classmethod
    def check_method(cls, v):
        return _upper_choice(v, METHODS, "method")


class ApplyCreditRequest(BaseModel):
    allocations: Optional[List[AllocationIn]] = None


class PaymentResponse(BaseModel):
    id: int
    receipt_number: str
    customer_id: Optional[int] = None
    source: str
    method: str
    amount: Decimal
    amount_tendered: Optional[Decimal] = None
    change_returned: Decimal
    refunded_amount: Decimal
    unapplied_amount: Decimal
    reference: Optional[str] = None
    verification_status: str
    status: str
    received_at: datetime
    replayed: bool = False
    allocations: List[AllocationIn] = []


class PaymentActionRequest(BaseModel):
    reason: str = Field(..., min_length=3, max_length=500)


class AdjustmentRequest(BaseModel):
    invoice_id: int
    adjustment_type: str
    amount: Decimal = Field(..., gt=0)
    reason: str = Field(..., min_length=5, max_length=500)

    @field_validator("adjustment_type")
    @classmethod
    def check_type(cls, v):
        return _upper_choice(v, ("ADJUSTMENT_CREDIT", "WRITE_OFF"), "adjustment_type")


class LedgerEntryResponse(BaseModel):
    id: int
    created_at: datetime
    entry_type: str
    reference: str
    description: str
    debit: Decimal
    credit: Decimal
    balance: Decimal
    invoice_id: Optional[int] = None
    reason: Optional[str] = None


class CustomerStatement(BaseModel):
    customer: CustomerResponse
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    opening_balance: Decimal
    closing_balance: Decimal
    total_debits: Decimal
    total_credits: Decimal
    unapplied_credit: Decimal
    entries: List[LedgerEntryResponse]
    open_invoices: List[InvoiceListItem]


# ── Returns ────────────────────────────────────────────────────────────────────

class ReturnLineIn(BaseModel):
    invoice_item_id: int
    quantity_base: Decimal = Field(..., gt=0)
    condition: str = "SALEABLE"

    @field_validator("condition")
    @classmethod
    def check_condition(cls, v):
        return _upper_choice(v, ("SALEABLE", "DAMAGED", "EXPIRED", "OPENED"), "condition")


class ReturnRequest(BaseModel):
    idempotency_key: str = Field(..., min_length=8, max_length=64)
    invoice_id: int
    reason: str = Field(..., min_length=3, max_length=500)
    items: List[ReturnLineIn] = Field(..., min_length=1)
    settlement: str = Field(default="REFUND", description="REFUND or CUSTOMER_CREDIT for any amount not used to reduce the balance")
    refund_method: Optional[str] = None
    refund_reference: Optional[str] = Field(None, max_length=100)

    @field_validator("settlement")
    @classmethod
    def check_settlement(cls, v):
        return _upper_choice(v, ("REFUND", "CUSTOMER_CREDIT"), "settlement")

    @field_validator("refund_method")
    @classmethod
    def check_refund_method(cls, v):
        return None if v is None else _upper_choice(v, METHODS, "refund_method")


class ReturnItemResponse(BaseModel):
    id: int
    invoice_item_id: int
    medicine_id: int
    medicine_name: Optional[str] = None
    batch_id: int
    batch_number: Optional[str] = None
    quantity_base: Decimal
    condition: str
    disposition: str
    taxable_amount: Decimal
    tax_amount: Decimal
    line_total: Decimal


class ReturnResponse(BaseModel):
    id: int
    return_number: str
    invoice_id: int
    invoice_number: Optional[str] = None
    customer_id: Optional[int] = None
    customer_name: Optional[str] = None
    reason: str
    taxable_total: Decimal
    cgst_total: Decimal
    sgst_total: Decimal
    igst_total: Decimal
    tax_total: Decimal
    total_amount: Decimal
    applied_to_balance: Decimal
    refund_amount: Decimal
    credit_note_amount: Decimal
    refund_method: Optional[str] = None
    refund_reference: Optional[str] = None
    processed_by_name: Optional[str] = None
    created_at: datetime
    replayed: bool = False
    items: List[ReturnItemResponse] = []


# ── Shifts ─────────────────────────────────────────────────────────────────────

class ShiftOpenRequest(BaseModel):
    opening_cash: Decimal = Field(..., ge=0)


class ShiftCloseRequest(BaseModel):
    counted_cash: Decimal = Field(..., ge=0)
    variance_note: Optional[str] = Field(None, max_length=1000)


class CashMovementRequest(BaseModel):
    movement_type: str
    category: str = Field(..., min_length=3, max_length=30)
    amount: Decimal = Field(..., gt=0)
    reason: str = Field(..., min_length=3, max_length=500)
    reference: Optional[str] = Field(None, max_length=100)

    @field_validator("movement_type")
    @classmethod
    def check_type(cls, v):
        return _upper_choice(v, ("PAID_OUT", "PAID_IN"), "movement_type")

    @field_validator("category")
    @classmethod
    def upper_category(cls, v):
        return v.strip().upper()


class CashMovementClassify(BaseModel):
    category: str = Field(..., min_length=3, max_length=30)
    note: str = Field(..., min_length=5, max_length=500)

    @field_validator("category")
    @classmethod
    def upper_category(cls, v):
        return v.strip().upper()


class ShiftReviewRequest(BaseModel):
    review_note: str = Field(..., min_length=3, max_length=1000)


class ShiftSummary(BaseModel):
    id: int
    user_id: int
    user_name: Optional[str] = None
    status: str
    opened_at: datetime
    closed_at: Optional[datetime] = None
    opening_cash: Decimal
    cash_sales: Decimal
    cash_collections: Decimal
    cash_refunds: Decimal
    paid_out: Decimal
    paid_in: Decimal
    supplier_payments_paid: Decimal = Decimal("0.00")
    expenses_paid: Decimal = Decimal("0.00")
    expected_cash: Decimal
    counted_cash: Optional[Decimal] = None
    variance: Optional[Decimal] = None
    variance_note: Optional[str] = None
    needs_review: bool
    reviewed_by_name: Optional[str] = None
    review_note: Optional[str] = None
    non_cash_totals: dict = {}
    cash_movements: List[dict] = []


# ── Messaging ──────────────────────────────────────────────────────────────────

class SendInvoiceMessageRequest(BaseModel):
    to_number: Optional[str] = Field(None, max_length=20)   # Defaults to the customer's mobile
    mode: Optional[str] = None                               # CLOUD_API or MANUAL; default by configuration
    consent_confirmed: bool = False
    force_resend: bool = False

    @field_validator("mode")
    @classmethod
    def check_mode(cls, v):
        return None if v is None else _upper_choice(v, ("CLOUD_API", "MANUAL"), "mode")


class ReminderRequest(BaseModel):
    mode: Optional[str] = None

    @field_validator("mode")
    @classmethod
    def check_mode(cls, v):
        return None if v is None else _upper_choice(v, ("CLOUD_API", "MANUAL"), "mode")


class MessageResponse(BaseModel):
    id: int
    mode: str
    purpose: str
    status: str
    status_label: str
    customer_id: Optional[int] = None
    customer_name: Optional[str] = None
    invoice_id: Optional[int] = None
    invoice_number: Optional[str] = None
    to_number: str
    body: str
    attempts: int
    last_error: Optional[str] = None
    provider_message_id: Optional[str] = None
    share_url: Optional[str] = None
    created_at: datetime
    sent_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    read_at: Optional[datetime] = None
    failed_at: Optional[datetime] = None


class CommunicationSettingsUpdate(BaseModel):
    values: dict


class ReminderScanRequest(BaseModel):
    dry_run: bool = True
    on_date: Optional[date] = None
