"""
Authentication and pharmacy setup service layer.
All business logic lives here — routes only handle HTTP concerns.
"""
import json
import secrets
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from fastapi import HTTPException, status, Request

from app.core.config import settings
from app.core.security import (
    hash_password, verify_password, password_needs_rehash,
    generate_session_token,
)
from app.models.auth import (
    Pharmacy, Role, Permission, RolePermission,
    User, UserSession, Setting, AuditLog,
)
from app.schemas.auth import PharmacySetupRequest


# ── Default permission set ─────────────────────────────────────────────────────

PERMISSION_DEFINITIONS = [
    # Sales
    ("sales.create",        "Create sales invoices",                  "sales"),
    ("sales.view",          "View sales invoices",                    "sales"),
    ("sales.refund",        "Process sales returns and refunds",      "sales"),
    ("sales.cancel",        "Cancel posted sales invoices",           "sales"),
    # Purchases
    ("purchases.create",    "Create purchase invoices",               "purchases"),
    ("purchases.view",      "View purchase invoices",                 "purchases"),
    ("purchases.post",      "Post purchase invoices to stock",        "purchases"),
    ("purchases.return",    "Process purchase returns",               "purchases"),
    # Stock / Inventory
    ("stock.view",          "View stock levels and movements",        "stock"),
    ("stock.adjust",        "Create and post stock adjustments",      "stock"),
    # Medicines and catalogue
    ("medicines.view",      "View medicine catalogue",                "catalogue"),
    ("medicines.manage",    "Create and edit medicines",              "catalogue"),
    # Suppliers and customers
    ("suppliers.manage",    "Create and edit suppliers",              "suppliers"),
    ("customers.manage",    "Create and edit customers",              "customers"),
    # Reports
    ("reports.view",        "View reports and analytics",             "reports"),
    # Administration
    ("users.manage",        "Create, edit and deactivate users",      "admin"),
    ("roles.manage",        "Create and edit roles and permissions",  "admin"),
    ("settings.manage",     "Change pharmacy settings",               "admin"),
    ("backups.manage",      "Create and restore backups",             "admin"),
    ("audit.view",          "View audit log",                         "admin"),
    # Phase 4: POS, credit and communication
    ("pos.access",          "Open the POS and look up saleable stock", "sales"),
    ("sales.discount",      "Give discounts on sales",                "sales"),
    ("sales.prescription",  "Finalise sales of prescription medicines", "sales"),
    ("prescriptions.view",  "View prescription and patient details",  "sales"),
    ("sales.credit",        "Make credit / part-paid sales",          "credit"),
    ("sales.credit_override", "Exceed a customer's credit limit",     "credit"),
    ("payments.receive",    "Record and allocate customer payments",  "credit"),
    ("customers.view",      "View customers and their ledgers",       "customers"),
    ("customers.adjust",    "Post ledger adjustments and write-offs", "credit"),
    ("messaging.send",      "Send invoices and reminders by WhatsApp", "messaging"),
    ("messaging.configure", "Configure WhatsApp templates and reminders", "messaging"),
    ("reports.export",      "Export reports",                         "reports"),
    ("shifts.operate",      "Open and close own cash shift",          "shifts"),
    ("shifts.review",       "Review cash shifts and approve paid-outs", "shifts"),
    # Phase 5: accounting (granted to Owner / Admin only by default)
    ("accounting.view",             "View chart of accounts, journals and financial reports", "accounting"),
    ("accounting.accounts_manage",  "Set up accounting, manage accounts and fiscal years",    "accounting"),
    ("accounting.journal_create",   "Prepare draft manual journals",                          "accounting"),
    ("accounting.journal_post",     "Post journal entries",                                   "accounting"),
    ("accounting.journal_reverse",  "Reverse posted journal entries",                         "accounting"),
    ("accounting.opening_balances", "Prepare and post opening balances",                      "accounting"),
    ("accounting.period_close",     "Close accounting periods",                               "accounting"),
    ("accounting.period_reopen",    "Reopen closed accounting periods",                       "accounting"),
    ("accounting.export",           "Export financial data",                                  "accounting"),
    ("accounting.postings_manage",  "Retry failed or missing automatic postings",             "accounting"),
    ("accounting.bank_reconcile",   "Record settlements and reconcile bank statements",       "accounting"),
    ("accounting.year_end",         "Close and reopen financial years",                       "accounting"),
    # Stage 5B preparation: supplier bills, supplier payments and expenses
    ("purchases.invoice_finalize",  "Finalise supplier invoices (amount becomes payable)",    "purchases"),
    ("purchases.invoice_cancel",    "Cancel finalised supplier invoices",                     "purchases"),
    ("supplier_payments.view",      "View supplier payments and balances",                    "payables"),
    ("supplier_payments.create",    "Record and allocate supplier payments",                  "payables"),
    ("supplier_payments.reverse",   "Reverse supplier payments",                              "payables"),
    ("expenses.view",               "View expenses",                                          "expenses"),
    ("expenses.create",             "Record expenses (pending approval unless also an approver)", "expenses"),
    ("expenses.approve",            "Approve or reject expenses; pay expenses from the drawer", "expenses"),
    ("expenses.reverse",            "Reverse approved expenses",                              "expenses"),
    ("expenses.configure",          "Manage expense categories",                              "expenses"),
]

# Stage 5B-preparation grants beyond Owner / Admin (also applied by migration 008)
PAYABLES_ROLE_GRANTS = {
    "Pharmacist": ["expenses.view", "expenses.create"],
    "Cashier": ["expenses.create"],
    "Inventory Manager": ["purchases.invoice_finalize", "supplier_payments.view", "expenses.view", "expenses.create"],
}

# Phase 4 permission grants for the default roles (also applied to existing
# pharmacies by migration 006).
PHASE4_ROLE_GRANTS = {
    "Pharmacist": [
        "pos.access", "sales.discount", "sales.prescription", "prescriptions.view",
        "sales.credit", "payments.receive", "customers.view", "messaging.send",
        "shifts.operate",
    ],
    "Cashier": [
        "pos.access", "payments.receive", "customers.view", "messaging.send", "shifts.operate",
    ],
    "Inventory Manager": [],
}

# Permission codes granted to each default role
ROLE_PERMISSIONS = {
    "Owner / Admin": [p[0] for p in PERMISSION_DEFINITIONS],  # all permissions
    "Pharmacist": [
        "sales.create", "sales.view", "sales.refund",
        "purchases.view", "stock.view",
        "medicines.view", "medicines.manage",
        "suppliers.manage", "customers.manage",
        "reports.view",
    ],
    "Cashier": [
        "sales.create", "sales.view",
        "medicines.view", "customers.manage",
        "stock.view",
    ],
    "Inventory Manager": [
        "purchases.create", "purchases.view", "purchases.post", "purchases.return",
        "stock.view", "stock.adjust",
        "medicines.view", "medicines.manage",
        "suppliers.manage",
        "reports.view",
    ],
}
for _role, _codes in PHASE4_ROLE_GRANTS.items():
    ROLE_PERMISSIONS[_role] = ROLE_PERMISSIONS[_role] + _codes
for _role, _codes in PAYABLES_ROLE_GRANTS.items():
    ROLE_PERMISSIONS[_role] = ROLE_PERMISSIONS[_role] + _codes


# ── Setup service ──────────────────────────────────────────────────────────────

async def is_setup_required(db: AsyncSession) -> bool:
    """Return True if no pharmacy has been configured yet."""
    result = await db.execute(select(func.count(Pharmacy.id)))
    count = result.scalar_one()
    return count == 0


async def run_first_time_setup(
    db: AsyncSession,
    request_data: PharmacySetupRequest,
    ip_address: Optional[str] = None,
) -> Pharmacy:
    """
    Idempotency-safe first-run setup:
    1. Create pharmacy profile.
    2. Seed all permission codes.
    3. Create default roles with their permissions.
    4. Create the initial admin user.
    5. Seed default settings.
    6. Mark setup as complete.
    """
    # Guard: must not run if setup already complete
    if not await is_setup_required(db):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Pharmacy is already configured. Setup cannot be run again.",
        )

    # 1. Create pharmacy
    pharmacy = Pharmacy(
        name=request_data.pharmacy_name,
        legal_name=request_data.legal_name,
        gstin=request_data.gstin,
        drug_license_number=request_data.drug_license_number,
        address_line1=request_data.address_line1,
        city=request_data.city,
        state_code=request_data.state_code,
        pincode=request_data.pincode,
        phone=request_data.phone,
        invoice_prefix=request_data.invoice_prefix,
        is_setup_complete=False,  # set True after all steps succeed
    )
    db.add(pharmacy)
    await db.flush()  # get pharmacy.id

    # 2. Seed permissions (upsert-safe: skip if code already exists)
    existing_codes_result = await db.execute(
        select(Permission.code)
    )
    existing_codes = {row[0] for row in existing_codes_result.fetchall()}

    permission_map: dict[str, Permission] = {}
    for code, description, module in PERMISSION_DEFINITIONS:
        if code not in existing_codes:
            perm = Permission(code=code, description=description, module=module)
            db.add(perm)
            await db.flush()
        else:
            result = await db.execute(select(Permission).where(Permission.code == code))
            perm = result.scalar_one()
        permission_map[code] = perm

    # 3. Create default roles with permissions
    role_map: dict[str, Role] = {}
    for role_name, perm_codes in ROLE_PERMISSIONS.items():
        role = Role(
            pharmacy_id=pharmacy.id,
            name=role_name,
            description=f"Default {role_name} role",
            is_system_role=True,
        )
        db.add(role)
        await db.flush()  # get role.id

        for code in perm_codes:
            if code in permission_map:
                db.add(RolePermission(
                    role_id=role.id,
                    permission_id=permission_map[code].id,
                ))
        role_map[role_name] = role

    await db.flush()

    # 4. Create admin user
    admin_role = role_map["Owner / Admin"]
    admin_user = User(
        pharmacy_id=pharmacy.id,
        name=request_data.admin_name,
        username=request_data.admin_username,
        password_hash=hash_password(request_data.admin_password),
        role_id=admin_role.id,
        is_active=True,
    )
    db.add(admin_user)
    await db.flush()

    # 5. Seed default settings
    default_settings = [
        ("gst.price_includes_gst", "true",
         "Whether medicine prices entered include GST (true = MRP-inclusive retail mode)"),
        ("gst.default_state_code", request_data.state_code or "",
         "Home state code for intra/inter-state GST determination"),
        ("stock.expiry_warning_days_1", "30",
         "First near-expiry warning threshold in days"),
        ("stock.expiry_warning_days_2", "60",
         "Second near-expiry warning threshold in days"),
        ("stock.expiry_warning_days_3", "90",
         "Third near-expiry warning threshold in days"),
        ("stock.free_qty_cost_method", "allocated",
         "How to cost free quantities: 'allocated' spreads total cost, 'zero' assigns zero cost"),
        ("invoice.sales_prefix", request_data.invoice_prefix,
         "Prefix for sales invoice numbers"),
        ("invoice.purchase_prefix", "PUR",
         "Prefix for purchase invoice numbers"),
        ("invoice.financial_year_start_month", "4",
         "Month number when the financial year begins (4 = April for India)"),
        ("invoice.number_reset_on_fy", "true",
         "Whether invoice numbers reset to 1 at the start of each financial year"),
        ("receipt.show_gstin", "true",
         "Whether to print GSTIN on receipts"),
        ("receipt.show_dl_number", "true",
         "Whether to print drug license number on receipts"),
        ("purchases.prohibit_self_approval", "false",
         "Prohibit users from approving their own purchase orders"),
    ]
    for key, value, description in default_settings:
        db.add(Setting(
            pharmacy_id=pharmacy.id,
            setting_key=key,
            setting_value=value,
            description=description,
            updated_by=admin_user.id,
        ))

    # 6. Seed default inventory masters (tax rates, units, categories, manufacturers)
    from app.services.inventory import InventoryService
    await InventoryService.seed_default_inventory_masters(db, pharmacy.id)

    # 7. Mark setup complete
    pharmacy.is_setup_complete = True

    # 7. Audit log
    db.add(AuditLog(
        pharmacy_id=pharmacy.id,
        user_id=admin_user.id,
        action="pharmacy.setup",
        entity_type="pharmacy",
        entity_id=str(pharmacy.id),
        ip_address=ip_address,
        reason="First-run pharmacy setup completed",
        metadata_json=json.dumps({
            "pharmacy_name": pharmacy.name,
            "admin_username": admin_user.username,
        }),
    ))

    await db.flush()
    return pharmacy


# ── Authentication service ─────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_dummy_password_hash() -> str:
    """A valid Argon2id hash (same parameters as real ones) of a random, unusable password."""
    return hash_password(secrets.token_urlsafe(32))


async def authenticate_user(
    db: AsyncSession,
    pharmacy_id: int,
    username: str,
    password: str,
) -> User:
    """
    Verify username and password for the given pharmacy.
    Raises 401 on any failure (intentionally vague to prevent enumeration).
    Rehashes the password if Argon2 parameters have been updated.
    """
    result = await db.execute(
        select(User)
        .where(User.pharmacy_id == pharmacy_id)
        .where(User.username == username)
        .where(User.is_active == True)
    )
    user = result.scalar_one_or_none()

    # Always run a full Argon2 verification, even when the user does not exist,
    # so response time does not reveal whether a username is valid. The dummy
    # must be a real hash: a malformed one fails instantly without hashing.
    check_hash = user.password_hash if user else get_dummy_password_hash()

    if not verify_password(password, check_hash) or user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    # Rehash if parameters changed
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        await db.flush()

    return user


async def create_session(
    db: AsyncSession,
    user: User,
    request: Request,
) -> UserSession:
    """Create a new server-side session for the authenticated user."""
    from datetime import timedelta
    session = UserSession(
        user_id=user.id,
        pharmacy_id=user.pharmacy_id,
        token=generate_session_token(),
        expires_at=datetime.now(timezone.utc) + timedelta(
            seconds=settings.SESSION_MAX_AGE_SECONDS
        ),
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("User-Agent", "")[:500],
    )
    db.add(session)

    # Update last login
    user.last_login_at = datetime.now(timezone.utc)

    db.add(AuditLog(
        pharmacy_id=user.pharmacy_id,
        user_id=user.id,
        action="user.login",
        entity_type="user",
        entity_id=str(user.id),
        ip_address=session.ip_address,
    ))

    await db.flush()
    return session


async def invalidate_session(db: AsyncSession, session: UserSession, user: User) -> None:
    """Invalidate the current session (logout)."""
    session.is_active = False
    db.add(AuditLog(
        pharmacy_id=user.pharmacy_id,
        user_id=user.id,
        action="user.logout",
        entity_type="user",
        entity_id=str(user.id),
    ))
    await db.flush()


async def get_user_permissions(db: AsyncSession, user: User) -> list[str]:
    """Return list of permission codes for the user's role."""
    result = await db.execute(
        select(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id == user.role_id)
    )
    return [row[0] for row in result.fetchall()]
