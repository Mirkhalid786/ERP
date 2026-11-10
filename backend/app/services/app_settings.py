"""
Typed access to per-pharmacy settings with documented defaults.

Phase 4 settings are read with a fallback default, so existing pharmacies work
without seeding rows; saving a value creates the row (see `set_setting`).
"""
from decimal import Decimal
from typing import Dict, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth import Setting

INVOICE_TEMPLATE = (
    "Hello {customer_name}, thank you for shopping with {pharmacy_name}. "
    "Your invoice {invoice_number} dated {invoice_date} totals ₹{invoice_total}. "
    "Paid: ₹{amount_paid}. Outstanding: ₹{balance_due}. "
    "Please contact us if you have any questions."
)
REMINDER_UPCOMING_TEMPLATE = (
    "Hello {customer_name}, a friendly reminder that ₹{balance_due} against invoice "
    "{invoice_number} is due on {due_date}. If you have already paid, please ignore this message."
)
REMINDER_DUE_TODAY_TEMPLATE = (
    "Hello {customer_name}, a friendly reminder that ₹{balance_due} against invoice "
    "{invoice_number} is due today ({due_date}). If you have already paid, please ignore this message."
)
REMINDER_OVERDUE_TEMPLATE = (
    "Hello {customer_name}, our records show ₹{balance_due} remains outstanding against invoice "
    "{invoice_number}, due on {due_date}. Please contact {pharmacy_name} if you need help with the payment."
)

# key -> (default, description)
DEFAULTS: Dict[str, Tuple[str, str]] = {
    "pos.walk_in_label": ("Walk-in Customer", "Customer name printed on bills without a customer"),
    "pos.max_discount_percent": ("20", "Highest line discount % any user may give"),
    "pos.require_open_shift": ("false", "Require an open cash shift before taking cash"),
    "pos.min_days_to_expiry": ("0", "Batches expiring within this many days are not sold"),
    "invoice.round_to_rupee": ("true", "Round bill totals to the nearest rupee (round-off line)"),
    "invoice.footer_note": ("Thank you. Get well soon!", "Footer printed on invoices"),
    "invoice.receipt_prefix": ("RCPT", "Prefix for payment receipt numbers"),
    "invoice.return_prefix": ("SR", "Prefix for sales return / credit note numbers"),
    "credit.default_terms_days": ("30", "Default days until a credit invoice is due"),
    "credit.allow_advances": ("true", "Allow customers to pay more than they owe (kept as advance credit)"),
    "rx.prescription_schedules": ("H,H1,X,NARCOTIC", "Schedules that always need a prescription at sale"),
    "rx.strict_schedules": ("H1,X,NARCOTIC", "Schedules that also need patient and prescriber address"),
    "shift.variance_review_threshold": ("100", "Cash variance (₹) above which a shift needs manager review"),
    "messaging.template.invoice": (INVOICE_TEMPLATE, "WhatsApp invoice message"),
    "messaging.template.reminder_upcoming": (REMINDER_UPCOMING_TEMPLATE, "Reminder before the due date"),
    "messaging.template.reminder_due_today": (REMINDER_DUE_TODAY_TEMPLATE, "Reminder on the due date"),
    "messaging.template.reminder_overdue": (REMINDER_OVERDUE_TEMPLATE, "Reminder after the due date"),
    "reminders.auto_send_enabled": ("false", "Send reminders automatically (needs the WhatsApp Cloud API)"),
    "reminders.upcoming_days_before": ("3", "Days before the due date to send the upcoming reminder (0 = off)"),
    "reminders.overdue_interval_days": ("7", "Days between overdue reminders"),
    "reminders.max_overdue_reminders": ("4", "Maximum overdue reminders per invoice"),
    "reminders.send_window_start_hour": ("9", "Earliest hour (local) to send automatic reminders"),
    "reminders.send_window_end_hour": ("20", "Latest hour (local) to send automatic reminders"),
    "accounting.books_start_date": ("", "Date the accounting books start (opening balances are as at this date)"),
    "backup.daily_time": ("", "Local time (HH:MM) for the daily automatic backup by the worker; empty = off"),
    "purchases.prohibit_self_approval": ("false", "Prohibit users from approving their own purchase orders"),
    "purchases.invoice_discrepancy_tolerance": ("1.00", "Largest difference (₹) between calculated and printed supplier bill total accepted without an explanation"),
}

COMMUNICATION_KEYS = [k for k in DEFAULTS if k.startswith(("messaging.", "reminders."))]


async def get_setting(db: AsyncSession, pharmacy_id: int, key: str, default: Optional[str] = None) -> str:
    row = (await db.execute(
        select(Setting.setting_value).where(Setting.pharmacy_id == pharmacy_id, Setting.setting_key == key)
    )).scalar_one_or_none()
    if row is not None:
        return row
    if default is not None:
        return default
    return DEFAULTS[key][0] if key in DEFAULTS else ""


async def get_bool(db: AsyncSession, pharmacy_id: int, key: str) -> bool:
    return (await get_setting(db, pharmacy_id, key)).strip().lower() in ("1", "true", "yes", "on")


async def get_int(db: AsyncSession, pharmacy_id: int, key: str) -> int:
    value = await get_setting(db, pharmacy_id, key)
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(DEFAULTS[key][0])


async def get_decimal(db: AsyncSession, pharmacy_id: int, key: str) -> Decimal:
    value = await get_setting(db, pharmacy_id, key)
    try:
        return Decimal(value)
    except Exception:
        return Decimal(DEFAULTS[key][0])


async def get_list(db: AsyncSession, pharmacy_id: int, key: str) -> list:
    return [v.strip().upper() for v in (await get_setting(db, pharmacy_id, key)).split(",") if v.strip()]


async def set_setting(db: AsyncSession, pharmacy_id: int, key: str, value: str, user_id: int) -> Setting:
    if key not in DEFAULTS:
        raise HTTPException(status_code=404, detail=f"Unknown setting '{key}'")
    row = (await db.execute(
        select(Setting).where(Setting.pharmacy_id == pharmacy_id, Setting.setting_key == key)
    )).scalar_one_or_none()
    if row is None:
        row = Setting(
            pharmacy_id=pharmacy_id,
            setting_key=key,
            setting_value=value,
            description=DEFAULTS[key][1],
            updated_by=user_id,
        )
        db.add(row)
    else:
        row.setting_value = value
        row.updated_by = user_id
    await db.flush()
    return row


# ── General settings screen ────────────────────────────────────────────────────

# Edited elsewhere (WhatsApp page, accounting setup) or internal
NOT_ON_SETTINGS_PAGE = ("messaging.", "reminders.", "accounting.books_start_date", "system.")
NUMBER_KEYS = {"pos.max_discount_percent", "pos.min_days_to_expiry", "credit.default_terms_days",
               "shift.variance_review_threshold", "purchases.invoice_discrepancy_tolerance"}
BOOL_KEYS = {"pos.require_open_shift", "invoice.round_to_rupee", "credit.allow_advances"}


def general_keys() -> list:
    return [k for k in DEFAULTS if not k.startswith(NOT_ON_SETTINGS_PAGE)]


def validate_general(key: str, value: str) -> str:
    import re
    from decimal import Decimal, InvalidOperation
    if key not in general_keys():
        raise HTTPException(status_code=404, detail=f"Setting '{key}' cannot be changed here")
    value = (value or "").strip()
    if key in NUMBER_KEYS:
        try:
            if Decimal(value) < 0:
                raise InvalidOperation
        except InvalidOperation:
            raise HTTPException(status_code=422, detail=f"{key} must be a number of 0 or more")
        if key == "pos.max_discount_percent" and Decimal(value) > 100:
            raise HTTPException(status_code=422, detail="Discount cannot exceed 100%")
    elif key in BOOL_KEYS:
        if value.lower() not in ("true", "false"):
            raise HTTPException(status_code=422, detail=f"{key} must be true or false")
        value = value.lower()
    elif key == "backup.daily_time":
        if value and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
            raise HTTPException(status_code=422, detail="Use HH:MM (24-hour), or leave empty to turn daily backups off")
    elif key in ("invoice.receipt_prefix", "invoice.return_prefix"):
        if not re.fullmatch(r"[A-Za-z0-9]{1,6}", value):
            raise HTTPException(status_code=422, detail="Prefix must be 1-6 letters or digits")
        value = value.upper()
    elif not value and key != "invoice.footer_note":
        raise HTTPException(status_code=422, detail=f"{key} cannot be empty")
    return value
