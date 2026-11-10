"""Pydantic schemas for authentication and pharmacy setup endpoints."""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator, model_validator
import re


# ── Shared ─────────────────────────────────────────────────────────────────────

class MessageResponse(BaseModel):
    """Generic success/informational response."""
    message: str


# ── Pharmacy Setup ─────────────────────────────────────────────────────────────

class PharmacySetupRequest(BaseModel):
    """
    First-run pharmacy profile and initial admin account creation.
    Submitted once on first installation.
    """
    # Pharmacy fields
    pharmacy_name: str = Field(..., min_length=2, max_length=255)
    legal_name: Optional[str] = Field(None, max_length=255)
    gstin: Optional[str] = Field(None, max_length=15)
    drug_license_number: Optional[str] = Field(None, max_length=100)
    address_line1: Optional[str] = Field(None, max_length=255)
    city: Optional[str] = Field(None, max_length=100)
    state_code: Optional[str] = Field(None, min_length=2, max_length=2)
    pincode: Optional[str] = Field(None, min_length=6, max_length=6)
    phone: Optional[str] = Field(None, max_length=15)
    invoice_prefix: str = Field("INV", max_length=10)

    # Initial admin account
    admin_name: str = Field(..., min_length=2, max_length=255)
    admin_username: str = Field(..., min_length=3, max_length=100)
    admin_password: str = Field(..., min_length=8)

    @field_validator("gstin")
    @classmethod
    def validate_gstin(cls, v):
        if v is None:
            return v
        pattern = r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$"
        if not re.match(pattern, v):
            raise ValueError("GSTIN must be in the format: 29ABCDE1234F1Z5")
        return v

    @field_validator("pincode")
    @classmethod
    def validate_pincode(cls, v):
        if v is None:
            return v
        if not v.isdigit():
            raise ValueError("Pincode must contain only digits")
        return v

    @field_validator("admin_username")
    @classmethod
    def validate_username(cls, v):
        if not re.match(r"^[a-zA-Z0-9_.-]+$", v):
            raise ValueError(
                "Username may only contain letters, digits, underscore, dot and hyphen"
            )
        return v.lower()

    @field_validator("admin_password")
    @classmethod
    def validate_password_strength(cls, v):
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        return v


class PharmacyResponse(BaseModel):
    """Pharmacy profile as returned by the API."""
    id: int
    name: str
    legal_name: Optional[str]
    gstin: Optional[str]
    drug_license_number: Optional[str]
    address_line1: Optional[str]
    city: Optional[str]
    state_code: Optional[str]
    pincode: Optional[str]
    phone: Optional[str]
    invoice_prefix: str
    is_setup_complete: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Authentication ─────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1)

    @field_validator("username")
    @classmethod
    def normalise_username(cls, v):
        return v.strip().lower()   # Usernames are stored in lowercase


class LoginResponse(BaseModel):
    message: str
    user_id: int
    username: str
    name: str
    role: str
    pharmacy_id: int


class CurrentUserResponse(BaseModel):
    """The authenticated user's profile and permissions."""
    user_id: int
    username: str
    name: str
    role_id: int
    role: str
    pharmacy_id: int
    permissions: list[str]
    last_login_at: Optional[datetime]

    model_config = {"from_attributes": True}


# ── Users ──────────────────────────────────────────────────────────────────────

def check_password_policy(v: str) -> str:
    """Staff password rule: 8-128 characters with an uppercase letter, a lowercase letter and a digit."""
    if len(v) < 8:
        raise ValueError("Password must be at least 8 characters")
    if len(v) > 128:
        raise ValueError("Password must be at most 128 characters")
    if not any(c.isupper() for c in v):
        raise ValueError("Password must contain at least one uppercase letter")
    if not any(c.islower() for c in v):
        raise ValueError("Password must contain at least one lowercase letter")
    if not any(c.isdigit() for c in v):
        raise ValueError("Password must contain at least one digit")
    return v


def check_username(v: str) -> str:
    v = v.strip()
    if not re.match(r"^[a-zA-Z0-9_.-]+$", v):
        raise ValueError("Username may only contain letters, digits, underscore, dot and hyphen")
    return v.lower()


class UserCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=8)
    role_id: int

    @field_validator("username")
    @classmethod
    def validate_username(cls, v):
        return check_username(v)

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v):
        return check_password_policy(v)


class UserUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    username: Optional[str] = Field(None, min_length=3, max_length=100)
    role_id: Optional[int] = None
    is_active: Optional[bool] = None

    @field_validator("username")
    @classmethod
    def validate_username(cls, v):
        return None if v is None else check_username(v)


class PasswordResetRequest(BaseModel):
    new_password: str = Field(..., min_length=8)

    @field_validator("new_password")
    @classmethod
    def validate_password_strength(cls, v):
        return check_password_policy(v)


class ManagedUserResponse(BaseModel):
    id: int
    name: str
    username: str
    role_id: int
    role_name: str
    is_active: bool
    is_admin: bool
    last_login_at: Optional[datetime]
    created_at: datetime
    updated_at: Optional[datetime] = None
    created_by_name: Optional[str] = None
    active_sessions: int = 0


class UserResponse(BaseModel):
    id: int
    name: str
    username: str
    role_id: int
    role_name: str
    is_active: bool
    last_login_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Roles ──────────────────────────────────────────────────────────────────────

class RoleResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    is_system_role: bool
    permissions: list[str] = []

    model_config = {"from_attributes": True}


# ── Settings ───────────────────────────────────────────────────────────────────

class SettingUpdateRequest(BaseModel):
    setting_value: Optional[str] = None


class SettingResponse(BaseModel):
    setting_key: str
    setting_value: Optional[str]
    description: Optional[str]
    updated_at: datetime

    model_config = {"from_attributes": True}
