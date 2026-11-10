"""
Branding and appearance.

Two layers:
  * Global (per pharmacy, `settings.manage`): application identity, logos and the default appearance.
    Text values live in the settings table under `branding.*`; the official business identity
    (name, address, contact) stays on the pharmacies row that invoices already print from.
  * Per user (any signed-in user, for themselves only): an appearance override that stores just
    the fields the user explicitly chose. Effective appearance = global defaults + user overrides,
    so changing the defaults never wipes a user's explicit choice.

Everything a browser may receive is validated here: colours are #RRGGBB only (no CSS injection),
enums are closed lists, text is length-limited and rendered as plain text by the UI.
"""
import hashlib
import json
import re
import struct
import time
from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth import AuditLog, Pharmacy, Setting, User
from app.models.branding import BrandAsset, UserPreference

# ── Identity ───────────────────────────────────────────────────────────────────
# key -> (default, max length, public?)  Public values are shown on the login page before sign-in.
IDENTITY: Dict[str, Tuple[str, int, bool]] = {
    "branding.app_name": ("My Pharmacy", 60, True),
    "branding.short_name": ("My Pharmacy", 24, True),
    "branding.tagline": ("Pharmacy billing, stock & accounts", 120, True),
    "branding.login_welcome": ("Welcome back. Sign in to continue.", 240, True),
    "branding.website": ("", 200, False),
    "branding.receipt_footer": ("", 300, False),
    "branding.logo_fit": ("contain", 10, True),
    "branding.text_only": ("false", 5, True),
}
APPEARANCE_KEY = "branding.appearance_defaults"
INVOICE_FOOTER_KEY = "invoice.footer_note"          # existing setting, reused as the invoice footer
PHARMACY_FIELDS = {                                  # editable official identity (GSTIN / state stay with setup)
    "name": 255, "legal_name": 255, "address_line1": 255, "address_line2": 255,
    "city": 100, "pincode": 6, "phone": 15, "email": 255,
}

# ── Appearance ─────────────────────────────────────────────────────────────────
PRESETS = [
    "emerald", "blue", "teal", "purple", "slate", "rose",
    "high_contrast", "dark_emerald", "dark_blue", "dark_neutral",
]
MODES = ["light", "dark", "system"]
DENSITIES = ["comfortable", "compact"]
NAV_STYLES = ["sidebar", "top"]
COLOR_KEYS = [
    "primary", "secondary", "accent", "background", "surface", "sidebar",
    "text", "muted", "border", "success", "warning", "error", "info",
]
DASHBOARD_WIDGETS = ["kpis", "quick_actions", "recent_activity", "system", "status"]
DEFAULT_APPEARANCE: Dict[str, Any] = {
    "preset": "emerald", "mode": "light", "colors": {}, "density": "comfortable",
    "nav": "sidebar", "sidebar_collapsed": False, "dashboard_hidden": [], "dashboard_order": [],
}
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

# ── Assets ─────────────────────────────────────────────────────────────────────
ASSET_KINDS = {"logo": 1_048_576, "logo_dark": 1_048_576, "logo_compact": 524_288, "favicon": 262_144}
MAX_DIMENSION = 4096


def validate_appearance(data: Any, partial: bool) -> Dict[str, Any]:
    """Return a clean appearance dict. With partial=True only the supplied keys are kept."""
    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail="Appearance must be an object")
    out: Dict[str, Any] = {}
    unknown = set(data) - set(DEFAULT_APPEARANCE)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown appearance field(s): {', '.join(sorted(unknown))}")
    choices = {"preset": PRESETS, "mode": MODES, "density": DENSITIES, "nav": NAV_STYLES}
    for key, allowed in choices.items():
        if key in data:
            if data[key] not in allowed:
                raise HTTPException(status_code=422, detail=f"{key} must be one of: {', '.join(allowed)}")
            out[key] = data[key]
    if "sidebar_collapsed" in data:
        if not isinstance(data["sidebar_collapsed"], bool):
            raise HTTPException(status_code=422, detail="sidebar_collapsed must be true or false")
        out["sidebar_collapsed"] = data["sidebar_collapsed"]
    if "colors" in data:
        colors = data["colors"]
        if not isinstance(colors, dict):
            raise HTTPException(status_code=422, detail="colors must be an object")
        clean = {}
        for k, v in colors.items():
            if k not in COLOR_KEYS:
                raise HTTPException(status_code=422, detail=f"Unknown colour '{k}'")
            if v in (None, ""):
                continue
            if not isinstance(v, str) or not HEX.match(v):
                raise HTTPException(status_code=422, detail=f"Colour '{k}' must be a hex value like #10b981")
            clean[k] = v.lower()
        out["colors"] = clean
    for key in ("dashboard_hidden", "dashboard_order"):
        if key in data:
            vals = data[key]
            if not isinstance(vals, list) or any(v not in DASHBOARD_WIDGETS for v in vals) or len(set(vals)) != len(vals):
                raise HTTPException(status_code=422, detail=f"{key} must list dashboard sections from: {', '.join(DASHBOARD_WIDGETS)}")
            out[key] = list(vals)
    if not partial:
        return {**DEFAULT_APPEARANCE, **out}
    return out


def merge_appearance(defaults: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = {**defaults, **{k: v for k, v in override.items() if k != "colors"}}
    if "colors" in override:
        merged["colors"] = override["colors"]          # a user's colour set replaces the default set as a whole
    return merged


# ── Cache (public branding is read on every page load; invalidated on every change) ────────────────
_CACHE: Dict[int, Tuple[float, Dict[str, Any]]] = {}
CACHE_SECONDS = 60


def invalidate_cache() -> None:
    _CACHE.clear()


async def _settings_map(db: AsyncSession, pharmacy_id: int) -> Dict[str, str]:
    rows = (await db.execute(select(Setting.setting_key, Setting.setting_value).where(
        Setting.pharmacy_id == pharmacy_id,
        Setting.setting_key.in_(list(IDENTITY) + [APPEARANCE_KEY, INVOICE_FOOTER_KEY]),
    ))).all()
    return {k: (v or "") for k, v in rows}


def _stored_defaults(values: Dict[str, str]) -> Dict[str, Any]:
    raw = values.get(APPEARANCE_KEY)
    if not raw:
        return dict(DEFAULT_APPEARANCE)
    try:
        return validate_appearance(json.loads(raw), partial=False)
    except Exception:          # A damaged value must never stop the app from rendering
        return dict(DEFAULT_APPEARANCE)


async def _asset_versions(db: AsyncSession, pharmacy_id: int) -> Dict[str, Optional[str]]:
    rows = (await db.execute(select(BrandAsset.kind, BrandAsset.sha256).where(BrandAsset.pharmacy_id == pharmacy_id))).all()
    found = {k: s[:12] for k, s in rows}
    return {k: found.get(k) for k in ASSET_KINDS}


async def default_pharmacy_id(db: AsyncSession) -> Optional[int]:
    """Version 1 runs one pharmacy; the public (signed-out) view shows the first one."""
    return (await db.execute(select(Pharmacy.id).order_by(Pharmacy.id).limit(1))).scalar_one_or_none()


async def public_branding(db: AsyncSession, pharmacy_id: Optional[int]) -> Dict[str, Any]:
    """Minimal, safe branding for anyone (login page, browser title, favicon). No address, contact or tax data."""
    if pharmacy_id is None:
        return {
            **{k.split(".", 1)[1]: d for k, (d, _, pub) in IDENTITY.items() if pub},
            "text_only": False, "appearance": dict(DEFAULT_APPEARANCE), "assets": {k: None for k in ASSET_KINDS},
        }
    hit = _CACHE.get(pharmacy_id)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    values = await _settings_map(db, pharmacy_id)
    out: Dict[str, Any] = {}
    for key, (default, _, public) in IDENTITY.items():
        if public:
            out[key.split(".", 1)[1]] = values.get(key) or default
    out["text_only"] = out["text_only"] == "true"
    out["appearance"] = _stored_defaults(values)
    out["assets"] = await _asset_versions(db, pharmacy_id)
    _CACHE[pharmacy_id] = (time.monotonic(), out)
    return out


async def admin_branding(db: AsyncSession, pharmacy_id: int) -> Dict[str, Any]:
    values = await _settings_map(db, pharmacy_id)
    ph = (await db.execute(select(Pharmacy).where(Pharmacy.id == pharmacy_id))).scalar_one()
    identity = {k.split(".", 1)[1]: values.get(k) or d for k, (d, _, _) in IDENTITY.items()}
    identity["text_only"] = identity["text_only"] == "true"
    from app.services import app_settings
    return {
        "identity": identity,
        "invoice_footer": values.get(INVOICE_FOOTER_KEY) or app_settings.DEFAULTS[INVOICE_FOOTER_KEY][0],
        "pharmacy": {f: getattr(ph, f) for f in PHARMACY_FIELDS} | {
            "gstin": ph.gstin, "drug_license_number": ph.drug_license_number, "state_code": ph.state_code},
        "appearance": _stored_defaults(values),
        "assets": await _asset_details(db, pharmacy_id),
        "options": options(),
    }


def options() -> Dict[str, Any]:
    return {"presets": PRESETS, "modes": MODES, "densities": DENSITIES, "nav": NAV_STYLES, "colors": COLOR_KEYS,
            "dashboard_widgets": DASHBOARD_WIDGETS,
            "asset_kinds": {k: {"max_bytes": v, "types": ["image/png", "image/jpeg", "image/webp"]} for k, v in ASSET_KINDS.items()}}


async def _asset_details(db: AsyncSession, pharmacy_id: int) -> Dict[str, Any]:
    rows = (await db.execute(select(BrandAsset.kind, BrandAsset.sha256, BrandAsset.content_type, BrandAsset.size_bytes,
                                    BrandAsset.width, BrandAsset.height, BrandAsset.created_at)
                             .where(BrandAsset.pharmacy_id == pharmacy_id))).all()
    found = {r.kind: {"version": r.sha256[:12], "content_type": r.content_type, "size_bytes": r.size_bytes,
                      "width": r.width, "height": r.height, "uploaded_at": r.created_at} for r in rows}
    return {k: found.get(k) for k in ASSET_KINDS}


async def _put_setting(db: AsyncSession, pharmacy_id: int, key: str, value: str, user_id: int, description: str) -> None:
    row = (await db.execute(select(Setting).where(Setting.pharmacy_id == pharmacy_id, Setting.setting_key == key))).scalar_one_or_none()
    if row is None:
        db.add(Setting(pharmacy_id=pharmacy_id, setting_key=key, setting_value=value, description=description, updated_by=user_id))
    else:
        row.setting_value = value
        row.updated_by = user_id


def _clean_text(value: Any, max_len: int, label: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail=f"{label} must be text")
    value = value.strip()
    if len(value) > max_len:
        raise HTTPException(status_code=422, detail=f"{label} is longer than {max_len} characters")
    if any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise HTTPException(status_code=422, detail=f"{label} contains control characters")
    return value


async def update_branding(db: AsyncSession, user: User, body: Dict[str, Any]) -> Dict[str, Any]:
    """Apply a partial update of identity / invoice footer / pharmacy contact / appearance defaults. Audited."""
    pid = user.pharmacy_id
    allowed = {"identity", "invoice_footer", "pharmacy", "appearance"}
    if set(body) - allowed:
        raise HTTPException(status_code=422, detail=f"Unknown section(s): {', '.join(sorted(set(body) - allowed))}")
    before = await admin_branding(db, pid)
    changes: Dict[str, Any] = {}

    for key, value in (body.get("identity") or {}).items():
        full = f"branding.{key}"
        if full not in IDENTITY:
            raise HTTPException(status_code=422, detail=f"Unknown branding field '{key}'")
        default, max_len, _ = IDENTITY[full]
        if key == "text_only":
            if not isinstance(value, bool):
                raise HTTPException(status_code=422, detail="text_only must be true or false")
            text = "true" if value else "false"
        elif key == "logo_fit":
            if value not in ("contain", "cover"):
                raise HTTPException(status_code=422, detail="logo_fit must be contain or cover")
            text = value
        else:
            text = _clean_text(value, max_len, key.replace("_", " "))
            if key in ("app_name", "short_name") and not text:
                raise HTTPException(status_code=422, detail=f"{key.replace('_', ' ').capitalize()} cannot be empty")
            if key == "website" and text and not re.match(r"^https?://[^\s<>\"']+$", text):
                raise HTTPException(status_code=422, detail="Website must start with http:// or https://")
        old = before["identity"].get(key)
        old = ("true" if old else "false") if key == "text_only" else old
        if text != old:
            changes[full] = {"from": old, "to": text}
        await _put_setting(db, pid, full, text, user.id, f"Branding: {key.replace('_', ' ')}")

    if "invoice_footer" in body:
        text = _clean_text(body["invoice_footer"], 300, "Invoice footer")
        if text != before["invoice_footer"]:
            changes[INVOICE_FOOTER_KEY] = {"from": before["invoice_footer"], "to": text}
        await _put_setting(db, pid, INVOICE_FOOTER_KEY, text, user.id, "Footer printed on invoices")

    if body.get("pharmacy"):
        ph = (await db.execute(select(Pharmacy).where(Pharmacy.id == pid))).scalar_one()
        for field, value in body["pharmacy"].items():
            if field not in PHARMACY_FIELDS:
                raise HTTPException(status_code=422, detail=f"'{field}' cannot be changed here")
            text = _clean_text(value, PHARMACY_FIELDS[field], field.replace("_", " "))
            if field == "name" and not text:
                raise HTTPException(status_code=422, detail="Business name cannot be empty")
            if field == "pincode" and text and not re.match(r"^\d{6}$", text):
                raise HTTPException(status_code=422, detail="PIN code must be 6 digits")
            if field == "email" and text and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", text):
                raise HTTPException(status_code=422, detail="Email address is not valid")
            if field == "phone" and text and not re.match(r"^[0-9+\- ]{6,15}$", text):
                raise HTTPException(status_code=422, detail="Phone number is not valid")
            if (getattr(ph, field) or "") != text:
                changes[f"pharmacy.{field}"] = {"from": getattr(ph, field), "to": text}
            setattr(ph, field, text if (text or field == "name") else None)

    if "appearance" in body:
        clean = validate_appearance(body["appearance"], partial=False)
        if clean != before["appearance"]:
            changes[APPEARANCE_KEY] = {"from": before["appearance"], "to": clean}
        await _put_setting(db, pid, APPEARANCE_KEY, json.dumps(clean), user.id, "Default appearance for all users")

    if changes:
        db.add(AuditLog(pharmacy_id=pid, user_id=user.id, action="branding.update", entity_type="branding",
                        entity_id=str(pid), metadata_json=json.dumps(changes, default=str)[:60000]))
    await db.flush()
    invalidate_cache()
    return await admin_branding(db, pid)


async def reset_branding(db: AsyncSession, user: User, section: str) -> Dict[str, Any]:
    """Restore the identity texts or the appearance defaults to the built-in values (official identity is untouched)."""
    pid = user.pharmacy_id
    if section == "appearance":
        return await update_branding(db, user, {"appearance": dict(DEFAULT_APPEARANCE)})
    if section == "identity":
        return await update_branding(db, user, {"identity": {
            k.split(".", 1)[1]: (d == "true" if k == "branding.text_only" else d) for k, (d, _, _) in IDENTITY.items()}})
    raise HTTPException(status_code=422, detail="section must be identity or appearance")


# ── Image validation ──────────────────────────────────────────────────────────

def _png_size(data: bytes) -> Tuple[int, int]:
    if len(data) < 24 or data[12:16] != b"IHDR":
        raise ValueError("bad PNG header")
    return struct.unpack(">II", data[16:24])


def _jpeg_size(data: bytes) -> Tuple[int, int]:
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            raise ValueError("bad JPEG marker")
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        i += 2 + length
    raise ValueError("no JPEG frame header")


def _webp_size(data: bytes) -> Tuple[int, int]:
    chunk = data[12:16]
    if chunk == b"VP8 " and len(data) >= 30:
        w, h = struct.unpack("<HH", data[26:30])
        return w & 0x3FFF, h & 0x3FFF
    if chunk == b"VP8L" and len(data) >= 25:
        b = data[21:25]
        w = 1 + (((b[1] & 0x3F) << 8) | b[0])
        h = 1 + (((b[3] & 0x0F) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6))
        return w, h
    if chunk == b"VP8X" and len(data) >= 30:
        w = 1 + int.from_bytes(data[24:27], "little")
        h = 1 + int.from_bytes(data[27:30], "little")
        return w, h
    raise ValueError("unsupported WebP variant")


def inspect_image(data: bytes) -> Tuple[str, int, int]:
    """Identify the image by its bytes (never by file name or browser-supplied type). PNG, JPEG and WebP only."""
    try:
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            ctype, (w, h) = "image/png", _png_size(data)
        elif data.startswith(b"\xff\xd8\xff"):
            ctype, (w, h) = "image/jpeg", _jpeg_size(data)
        elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            ctype, (w, h) = "image/webp", _webp_size(data)
        else:
            raise HTTPException(status_code=415, detail="Only PNG, JPEG or WebP images are accepted (SVG is not supported)")
    except ValueError:
        raise HTTPException(status_code=415, detail="The file is not a valid PNG, JPEG or WebP image")
    if not (1 <= w <= MAX_DIMENSION and 1 <= h <= MAX_DIMENSION):
        raise HTTPException(status_code=422, detail=f"Image must be between 1 and {MAX_DIMENSION} pixels on each side")
    return ctype, w, h


async def save_asset(db: AsyncSession, user: User, kind: str, data: bytes) -> Dict[str, Any]:
    if kind not in ASSET_KINDS:
        raise HTTPException(status_code=404, detail="Unknown image slot")
    if not data:
        raise HTTPException(status_code=422, detail="The file is empty")
    if len(data) > ASSET_KINDS[kind]:
        raise HTTPException(status_code=413, detail=f"Image is too large (maximum {ASSET_KINDS[kind] // 1024} KB)")
    ctype, w, h = inspect_image(data)
    digest = hashlib.sha256(data).hexdigest()
    row = (await db.execute(select(BrandAsset).where(BrandAsset.pharmacy_id == user.pharmacy_id, BrandAsset.kind == kind))).scalar_one_or_none()
    replaced = row is not None
    if row is None:
        row = BrandAsset(pharmacy_id=user.pharmacy_id, kind=kind)
        db.add(row)
    row.content_type, row.size_bytes, row.width, row.height, row.sha256, row.data, row.uploaded_by = ctype, len(data), w, h, digest, data, user.id
    from app.models.inventory import utcnow
    row.created_at = utcnow()
    db.add(AuditLog(pharmacy_id=user.pharmacy_id, user_id=user.id, action="branding.asset_upload", entity_type="brand_asset",
                    entity_id=kind, metadata_json=json.dumps({"replaced": replaced, "type": ctype, "bytes": len(data),
                                                              "width": w, "height": h, "sha256": digest})))
    await db.flush()
    invalidate_cache()
    return (await _asset_details(db, user.pharmacy_id))[kind]


async def delete_asset(db: AsyncSession, user: User, kind: str) -> None:
    if kind not in ASSET_KINDS:
        raise HTTPException(status_code=404, detail="Unknown image slot")
    row = (await db.execute(select(BrandAsset).where(BrandAsset.pharmacy_id == user.pharmacy_id, BrandAsset.kind == kind))).scalar_one_or_none()
    if row is None:
        return
    db.add(AuditLog(pharmacy_id=user.pharmacy_id, user_id=user.id, action="branding.asset_remove", entity_type="brand_asset",
                    entity_id=kind, metadata_json=json.dumps({"sha256": row.sha256})))
    await db.delete(row)
    await db.flush()
    invalidate_cache()


async def load_asset(db: AsyncSession, pharmacy_id: Optional[int], kind: str) -> Optional[BrandAsset]:
    if pharmacy_id is None or kind not in ASSET_KINDS:
        return None
    return (await db.execute(select(BrandAsset).where(BrandAsset.pharmacy_id == pharmacy_id, BrandAsset.kind == kind))).scalar_one_or_none()


# ── Per-user appearance ────────────────────────────────────────────────────────

async def _user_override(db: AsyncSession, user_id: int) -> Tuple[Optional[UserPreference], Dict[str, Any]]:
    row = (await db.execute(select(UserPreference).where(UserPreference.user_id == user_id))).scalar_one_or_none()
    if row is None or not row.appearance_json:
        return row, {}
    try:
        return row, validate_appearance(json.loads(row.appearance_json), partial=True)
    except Exception:
        return row, {}


async def my_appearance(db: AsyncSession, user: User) -> Dict[str, Any]:
    defaults = (await public_branding(db, user.pharmacy_id))["appearance"]
    _, override = await _user_override(db, user.id)
    return {"defaults": defaults, "override": override, "effective": merge_appearance(defaults, override),
            "customized": bool(override), "options": options()}


async def update_my_appearance(db: AsyncSession, user: User, body: Dict[str, Any], replace: bool) -> Dict[str, Any]:
    """replace=False merges the given fields into the user's override (e.g. collapsing the sidebar);
    replace=True stores exactly the given fields. Fields set to null go back to following the default."""
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Appearance must be an object")
    cleared = [k for k, v in body.items() if v is None]
    clean = validate_appearance({k: v for k, v in body.items() if v is not None}, partial=True)
    row, current = await _user_override(db, user.id)
    new = clean if replace else {**current, **clean}
    for k in cleared:
        new.pop(k, None)
    if row is None:
        row = UserPreference(user_id=user.id)
        db.add(row)
    row.appearance_json = json.dumps(new) if new else None
    await db.flush()
    return await my_appearance(db, user)


async def reset_user_appearance(db: AsyncSession, actor: User, user_id: int) -> None:
    target = (await db.execute(select(User).where(User.id == user_id, User.pharmacy_id == actor.pharmacy_id))).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    row = (await db.execute(select(UserPreference).where(UserPreference.user_id == user_id))).scalar_one_or_none()
    if row is not None:
        row.appearance_json = None
    if actor.id != user_id:
        db.add(AuditLog(pharmacy_id=actor.pharmacy_id, user_id=actor.id, action="user.appearance_reset", entity_type="user",
                        entity_id=str(user_id)))
    await db.flush()
