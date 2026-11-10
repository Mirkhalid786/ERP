"""Pydantic schemas for Phase 5A accounting."""
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

ACCOUNT_TYPES = ("ASSET", "LIABILITY", "EQUITY", "INCOME", "EXPENSE")


class SetupRequest(BaseModel):
    books_start_date: date


class AccountCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=20, pattern=r"^[0-9A-Za-z.-]+$")
    name: str = Field(..., min_length=2, max_length=150)
    account_type: str
    parent_id: Optional[int] = None
    is_group: bool = False
    description: Optional[str] = Field(None, max_length=500)

    @field_validator("account_type")
    @classmethod
    def check_type(cls, v):
        v = v.strip().upper()
        if v not in ACCOUNT_TYPES:
            raise ValueError(f"account_type must be one of {', '.join(ACCOUNT_TYPES)}")
        return v


class AccountUpdate(BaseModel):
    code: Optional[str] = Field(None, min_length=1, max_length=20, pattern=r"^[0-9A-Za-z.-]+$")
    name: Optional[str] = Field(None, min_length=2, max_length=150)
    description: Optional[str] = Field(None, max_length=500)
    is_active: Optional[bool] = None


class AccountResponse(BaseModel):
    id: int
    code: str
    name: str
    account_type: str
    normal_balance: str
    parent_id: Optional[int] = None
    is_group: bool
    system_key: Optional[str] = None
    party_type: Optional[str] = None
    is_control: bool
    is_active: bool
    description: Optional[str] = None
    has_postings: bool = False
    balance: Decimal = Decimal("0.00")      # Debit-positive
    model_config = ConfigDict(from_attributes=True)


class FiscalYearCreate(BaseModel):
    start_date: date
    end_date: date


class PeriodReopen(BaseModel):
    reason: str = Field(..., min_length=10, max_length=500)


class JournalLineIn(BaseModel):
    account_id: int
    debit: Decimal = Field(default=Decimal("0"), ge=0, max_digits=14, decimal_places=2)
    credit: Decimal = Field(default=Decimal("0"), ge=0, max_digits=14, decimal_places=2)
    party_type: Optional[str] = None
    party_id: Optional[int] = None
    description: Optional[str] = Field(None, max_length=500)

    @field_validator("party_type")
    @classmethod
    def check_party(cls, v):
        if v is None:
            return None
        v = v.upper()
        if v not in ("CUSTOMER", "SUPPLIER"):
            raise ValueError("party_type must be CUSTOMER or SUPPLIER")
        return v


class JournalDraftIn(BaseModel):
    idempotency_key: Optional[str] = Field(None, min_length=8, max_length=64)
    entry_date: date
    narration: str = Field(..., min_length=3, max_length=1000)
    reference: Optional[str] = Field(None, max_length=100)
    lines: List[JournalLineIn] = Field(..., min_length=2, max_length=200)


class ReverseRequest(BaseModel):
    reason: str = Field(..., min_length=5, max_length=500)
    reversal_date: Optional[date] = None


class JournalLineOut(BaseModel):
    id: int
    line_no: int
    account_id: int
    account_code: Optional[str] = None
    account_name: Optional[str] = None
    debit: Decimal
    credit: Decimal
    party_type: Optional[str] = None
    party_id: Optional[int] = None
    party_name: Optional[str] = None
    description: Optional[str] = None


class JournalOut(BaseModel):
    id: int
    entry_number: Optional[str] = None
    status: str
    entry_date: date
    document_date: Optional[date] = None
    period_name: Optional[str] = None
    source_type: str
    source_id: Optional[str] = None
    reference: Optional[str] = None
    narration: str
    total_debit: Decimal
    total_credit: Decimal
    created_by_name: Optional[str] = None
    created_at: datetime
    posted_by_name: Optional[str] = None
    posted_at: Optional[datetime] = None
    reversal_of_id: Optional[int] = None
    reversal_of_number: Optional[str] = None
    reversed_by_entry_id: Optional[int] = None
    reversed_by_number: Optional[str] = None
    reversed_at: Optional[datetime] = None
    reversal_reason: Optional[str] = None
    replayed: bool = False
    lines: List[JournalLineOut] = []


class OpeningLineIn(BaseModel):
    account_id: int
    debit: Decimal = Field(default=Decimal("0"), ge=0, max_digits=14, decimal_places=2)
    credit: Decimal = Field(default=Decimal("0"), ge=0, max_digits=14, decimal_places=2)
    party_type: Optional[str] = None
    party_id: Optional[int] = None
    document_number: Optional[str] = Field(None, max_length=60)
    document_date: Optional[date] = None
    due_date: Optional[date] = None
    notes: Optional[str] = Field(None, max_length=500)


class OpeningBatchIn(BaseModel):
    source_description: str = Field(..., min_length=5, max_length=500)
    notes: Optional[str] = None
    post_difference_to_suspense: bool = False
    lines: List[OpeningLineIn] = Field(default_factory=list, max_length=2000)


class OpeningPostRequest(BaseModel):
    acknowledge_warnings: bool = False
