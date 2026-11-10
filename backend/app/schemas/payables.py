"""Schemas for supplier invoice workflow actions, supplier payments and expenses."""
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from app.models.payables import CASH_SOURCES, EXPENSE_PAYMENT_METHODS, SUPPLIER_PAYMENT_METHODS


def _choice(value: Optional[str], allowed, field: str) -> Optional[str]:
    if value is None or value == "":
        return None
    cleaned = value.strip().upper()
    if cleaned not in allowed:
        raise ValueError(f"{field} must be one of: {', '.join(allowed)}")
    return cleaned


# ── Supplier invoice workflow ──────────────────────────────────────────────────

class InvoiceFinalizeRequest(BaseModel):
    acknowledge_warnings: bool = False


class ReasonRequest(BaseModel):
    reason: str = Field(..., min_length=10, max_length=1000)


# ── Supplier payments ──────────────────────────────────────────────────────────

class AllocationIn(BaseModel):
    invoice_id: int
    amount: Decimal = Field(..., gt=0)


class SupplierPaymentCreate(BaseModel):
    idempotency_key: str = Field(..., min_length=8, max_length=64)
    supplier_id: int
    payment_date: date
    method: str
    cash_source: Optional[str] = None
    amount: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2)
    reference: Optional[str] = Field(None, max_length=100)
    bank_account: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = Field(None, max_length=1000)
    allocations: Optional[List[AllocationIn]] = None
    auto_allocate: bool = False          # Oldest due first, when no allocations are given
    confirm_duplicate: bool = False

    @field_validator("method")
    @classmethod
    def check_method(cls, v):
        return _choice(v, SUPPLIER_PAYMENT_METHODS, "method")

    @field_validator("cash_source")
    @classmethod
    def check_source(cls, v):
        return _choice(v, CASH_SOURCES, "cash_source")


class AllocateRequest(BaseModel):
    allocations: Optional[List[AllocationIn]] = None   # None = oldest due first


class AllocationOut(BaseModel):
    id: int
    invoice_id: int
    invoice_number: Optional[str] = None
    amount: Decimal
    status: str
    created_at: datetime


class SupplierPaymentOut(BaseModel):
    id: int
    payment_number: str
    supplier_id: int
    supplier_name: Optional[str] = None
    payment_date: date
    method: str
    cash_source: Optional[str] = None
    shift_id: Optional[int] = None
    amount: Decimal
    allocated: Decimal
    unallocated: Decimal
    reference: Optional[str] = None
    bank_account: Optional[str] = None
    notes: Optional[str] = None
    status: str
    is_legacy: bool
    created_by_name: Optional[str] = None
    created_at: datetime
    reversed_by_name: Optional[str] = None
    reversed_at: Optional[datetime] = None
    reversal_reason: Optional[str] = None
    allocations: List[AllocationOut] = []


# ── Expenses ───────────────────────────────────────────────────────────────────

class ExpenseCategoryIn(BaseModel):
    code: str = Field("", max_length=30)
    name: str = Field(..., min_length=2, max_length=100)
    description: Optional[str] = Field(None, max_length=255)
    account_code_hint: Optional[str] = Field(None, max_length=20)
    is_active: Optional[bool] = None


class ExpenseCategoryOut(BaseModel):
    id: int
    code: str
    name: str
    description: Optional[str] = None
    account_code_hint: Optional[str] = None
    is_active: bool


class ExpenseCreate(BaseModel):
    idempotency_key: str = Field(..., min_length=8, max_length=64)
    category_id: int
    expense_date: date
    amount: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2)
    gst_amount: Decimal = Field(Decimal("0.00"), ge=0, max_digits=12, decimal_places=2)
    payee: str = Field(..., min_length=2, max_length=200)
    payee_gstin: Optional[str] = Field(None, max_length=15)
    supplier_id: Optional[int] = None
    bill_number: Optional[str] = Field(None, max_length=100)
    payment_method: str
    cash_source: Optional[str] = None
    reference: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = Field(None, max_length=1000)
    confirm_duplicate: bool = False

    @field_validator("payment_method")
    @classmethod
    def check_method(cls, v):
        return _choice(v, EXPENSE_PAYMENT_METHODS, "payment_method")

    @field_validator("cash_source")
    @classmethod
    def check_source(cls, v):
        return _choice(v, CASH_SOURCES, "cash_source")


class DecisionRequest(BaseModel):
    note: Optional[str] = Field(None, max_length=1000)


class ExpenseOut(BaseModel):
    id: int
    expense_number: str
    category_id: int
    category_name: Optional[str] = None
    expense_date: date
    amount: Decimal
    gst_amount: Decimal
    payee: str
    payee_gstin: Optional[str] = None
    supplier_id: Optional[int] = None
    bill_number: Optional[str] = None
    payment_method: str
    cash_source: Optional[str] = None
    shift_id: Optional[int] = None
    reference: Optional[str] = None
    notes: Optional[str] = None
    status: str
    created_by_name: Optional[str] = None
    created_at: datetime
    approved_by_name: Optional[str] = None
    approved_at: Optional[datetime] = None
    decision_note: Optional[str] = None
    reversed_by_name: Optional[str] = None
    reversed_at: Optional[datetime] = None
    reversal_reason: Optional[str] = None
