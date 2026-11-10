"""Branding & appearance: global branding persistence and permissions, public data minimisation,
logo upload validation, per-user preference isolation and reset behaviour."""
import struct
import zlib

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.auth import AuditLog
from tests.conftest import login, setup_pharmacy
from tests.sales_helpers import add_user, login_as


@pytest.fixture(autouse=True)
def fresh_branding_cache():
    """Each test has its own database, so the process-wide public-branding cache must start empty."""
    from app.services import branding
    branding.invalidate_cache()
    yield
    branding.invalidate_cache()


def png(w=4, h=2) -> bytes:
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\x10\xb9\x81" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def jpeg(w=16, h=8) -> bytes:
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, h, w, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + sof + b"\xff\xd9"


def webp_lossless(w=10, h=6) -> bytes:
    bits = (w - 1) | ((h - 1) << 14)
    payload = b"\x2f" + struct.pack("<I", bits) + b"\x00" * 8
    body = b"WEBP" + b"VP8L" + struct.pack("<I", len(payload)) + payload
    return b"RIFF" + struct.pack("<I", len(body)) + body


async def admin(client: AsyncClient):
    await setup_pharmacy(client)
    await login(client)


async def as_cashier(client: AsyncClient, username="cash1"):
    await add_user(client, "Cashier", username)
    await client.post("/api/v1/auth/logout")
    await login_as(client, username, "Staff1234")


@pytest.mark.asyncio
async def test_public_branding_defaults_before_setup(client: AsyncClient):
    r = await client.get("/api/v1/branding/public")
    assert r.status_code == 200
    body = r.json()
    assert body["app_name"] == "My Pharmacy"
    assert body["appearance"]["preset"] == "emerald" and body["appearance"]["mode"] == "light"
    assert body["assets"] == {"logo": None, "logo_dark": None, "logo_compact": None, "favicon": None}


@pytest.mark.asyncio
async def test_public_branding_exposes_no_private_details(client: AsyncClient):
    await admin(client)
    await client.put("/api/v1/branding", json={"identity": {"website": "https://example.com", "receipt_footer": "Private note"},
                                               "pharmacy": {"phone": "9876543210", "email": "shop@example.com"}})
    await client.post("/api/v1/auth/logout")
    body = (await client.get("/api/v1/branding/public")).json()
    text = str(body)
    for secret in ("9876543210", "shop@example.com", "example.com", "Private note", "gstin", "address"):
        assert secret not in text
    assert set(body) == {"app_name", "short_name", "tagline", "login_welcome", "logo_fit", "text_only", "appearance", "assets"}


@pytest.mark.asyncio
async def test_global_branding_requires_settings_manage(client: AsyncClient):
    assert (await client.get("/api/v1/branding")).status_code == 401
    await admin(client)
    await as_cashier(client)
    assert (await client.get("/api/v1/branding")).status_code == 403
    assert (await client.put("/api/v1/branding", json={"identity": {"app_name": "Hacked"}})).status_code == 403
    assert (await client.post("/api/v1/branding/assets/logo", files={"file": ("l.png", png(), "image/png")})).status_code == 403
    assert (await client.delete("/api/v1/branding/assets/logo")).status_code == 403
    assert (await client.post("/api/v1/branding/reset/appearance")).status_code == 403
    assert (await client.get("/api/v1/branding/public")).json()["app_name"] == "My Pharmacy"


@pytest.mark.asyncio
async def test_branding_update_persists_is_audited_and_refreshes_public(client: AsyncClient, test_engine):
    await admin(client)
    assert (await client.get("/api/v1/branding/public")).json()["app_name"] == "My Pharmacy"     # warms the cache
    r = await client.put("/api/v1/branding", json={
        "identity": {"app_name": "Care Plus", "short_name": "Care+", "tagline": "Your health partner"},
        "invoice_footer": "Thank you, visit again",
        "pharmacy": {"name": "Care Plus Pharmacy LLP", "phone": "9876543210"},
        "appearance": {"preset": "blue", "mode": "dark", "colors": {"primary": "#1D4ED8"}},
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["identity"]["app_name"] == "Care Plus"
    assert body["pharmacy"]["name"] == "Care Plus Pharmacy LLP"
    assert body["appearance"]["colors"] == {"primary": "#1d4ed8"}
    pub = (await client.get("/api/v1/branding/public")).json()
    assert pub["app_name"] == "Care Plus" and pub["short_name"] == "Care+"
    assert pub["appearance"]["preset"] == "blue" and pub["appearance"]["mode"] == "dark"
    # The invoice footer is the existing invoice setting that invoices already print
    general = {s["key"]: s["value"] for s in (await client.get("/api/v1/settings/general")).json()}
    assert general["invoice.footer_note"] == "Thank you, visit again"
    from sqlalchemy.ext.asyncio import async_sessionmaker
    async with async_sessionmaker(test_engine)() as s:
        logs = (await s.execute(select(AuditLog).where(AuditLog.action == "branding.update"))).scalars().all()
    assert len(logs) == 1 and "Care Plus" in logs[0].metadata_json


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"appearance": {"colors": {"primary": "red;} body{display:none"}}},
    {"appearance": {"colors": {"primary": "url(javascript:alert(1))"}}},
    {"appearance": {"preset": "matrix"}},
    {"appearance": {"mode": "sepia"}},
    {"appearance": {"colors": {"glow": "#ffffff"}}},
    {"appearance": {"dashboard_hidden": ["sales_secret"]}},
    {"identity": {"website": "javascript:alert(1)"}},
    {"identity": {"app_name": ""}},
    {"identity": {"app_name": "x" * 61}},
    {"identity": {"made_up": "x"}},
    {"pharmacy": {"gstin": "29ABCDE1234F1Z5"}},
    {"pharmacy": {"pincode": "12ab"}},
    {"something": {}},
])
async def test_branding_input_validation(client: AsyncClient, payload):
    await admin(client)
    r = await client.put("/api/v1/branding", json=payload)
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
@pytest.mark.parametrize("data,ctype", [(png(), "image/png"), (jpeg(), "image/jpeg"), (webp_lossless(), "image/webp")], ids=["png", "jpeg", "webp"])
async def test_logo_upload_and_public_serving(client: AsyncClient, data, ctype):
    await admin(client)
    r = await client.post("/api/v1/branding/assets/logo", files={"file": ("anything.bin", data, "application/octet-stream")})
    assert r.status_code == 201, r.text
    info = r.json()
    assert info["content_type"] == ctype and info["width"] > 0
    await client.post("/api/v1/auth/logout")
    version = (await client.get("/api/v1/branding/public")).json()["assets"]["logo"]
    assert version == info["version"]
    img = await client.get(f"/api/v1/branding/assets/logo?v={version}")
    assert img.status_code == 200 and img.content == data
    assert img.headers["content-type"] == ctype
    assert img.headers["x-content-type-options"] == "nosniff"
    assert "immutable" in img.headers["cache-control"]
    assert (await client.get("/api/v1/branding/assets/logo")).headers["cache-control"] == "no-cache"
    again = await client.get("/api/v1/branding/assets/logo", headers={"If-None-Match": img.headers["etag"]})
    assert again.status_code == 304


@pytest.mark.asyncio
@pytest.mark.parametrize("name,data,status", [
    ("logo.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', 415),
    ("logo.png", b"#!/bin/sh\necho not an image\n", 415),
    ("logo.png", b"<html><body>hi</body></html>", 415),
    ("logo.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 4, 415),
    ("logo.png", b"", 422),
    ("logo.png", png() + b"\x00" * 1_048_600, 413),
], ids=["svg-with-script", "shell-script", "html", "truncated-png", "empty", "too-large"])
async def test_logo_upload_rejects_unsafe_files(client: AsyncClient, name, data, status):
    await admin(client)
    r = await client.post("/api/v1/branding/assets/logo", files={"file": (name, data, "image/png")})
    assert r.status_code == status, r.text
    assert (await client.get("/api/v1/branding/public")).json()["assets"]["logo"] is None


@pytest.mark.asyncio
async def test_logo_slots_replace_remove_and_unknown_slot(client: AsyncClient):
    await admin(client)
    first = (await client.post("/api/v1/branding/assets/favicon", files={"file": ("f.png", png(2, 2), "image/png")})).json()
    second = (await client.post("/api/v1/branding/assets/favicon", files={"file": ("f.png", png(3, 3), "image/png")})).json()
    assert first["version"] != second["version"] and second["width"] == 3
    assert (await client.post("/api/v1/branding/assets/../../etc", files={"file": ("f.png", png(), "image/png")})).status_code == 404
    assert (await client.post("/api/v1/branding/assets/banner", files={"file": ("f.png", png(), "image/png")})).status_code == 404
    assert (await client.delete("/api/v1/branding/assets/favicon")).status_code == 204
    assert (await client.get("/api/v1/branding/assets/favicon")).status_code == 404
    assert (await client.get("/api/v1/branding/assets/missing")).status_code == 404


@pytest.mark.asyncio
async def test_user_preferences_are_independent(client: AsyncClient, second_client: AsyncClient):
    await admin(client)
    await add_user(client, "Cashier", "cash1")
    await add_user(client, "Pharmacist", "pharm1")
    await client.put("/api/v1/me/appearance", json={"preset": "emerald", "mode": "light"})
    await login_as(second_client, "cash1", "Staff1234")
    await second_client.put("/api/v1/me/appearance", json={"preset": "blue"})
    mine = (await client.get("/api/v1/me/appearance")).json()
    theirs = (await second_client.get("/api/v1/me/appearance")).json()
    assert mine["effective"]["preset"] == "emerald"
    assert theirs["effective"]["preset"] == "blue" and theirs["customized"] is True
    # A personal choice never changes the global default
    assert (await client.get("/api/v1/branding/public")).json()["appearance"]["preset"] == "emerald"
    await second_client.post("/api/v1/auth/logout")
    await login_as(second_client, "pharm1", "Staff1234")
    pharm = (await second_client.get("/api/v1/me/appearance")).json()
    assert pharm["effective"]["preset"] == "emerald" and pharm["customized"] is False


@pytest.mark.asyncio
async def test_changing_defaults_keeps_explicit_user_choices(client: AsyncClient, second_client: AsyncClient):
    await admin(client)
    await add_user(client, "Cashier", "cash1")
    await login_as(second_client, "cash1", "Staff1234")
    await second_client.put("/api/v1/me/appearance", json={"preset": "dark_neutral"})
    await client.put("/api/v1/branding", json={"appearance": {"preset": "teal", "mode": "dark", "density": "compact"}})
    eff = (await second_client.get("/api/v1/me/appearance")).json()["effective"]
    assert eff["preset"] == "dark_neutral"                     # explicit choice kept
    assert eff["mode"] == "dark" and eff["density"] == "compact"   # everything else follows the new defaults


@pytest.mark.asyncio
async def test_patch_merges_and_null_returns_to_default(client: AsyncClient):
    await admin(client)
    await client.put("/api/v1/me/appearance", json={"preset": "purple"})
    r = (await client.patch("/api/v1/me/appearance", json={"sidebar_collapsed": True})).json()
    assert r["override"] == {"preset": "purple", "sidebar_collapsed": True}
    r = (await client.patch("/api/v1/me/appearance", json={"preset": None})).json()
    assert r["override"] == {"sidebar_collapsed": True} and r["effective"]["preset"] == "emerald"
    assert (await client.patch("/api/v1/me/appearance", json={"colors": {"text": "expression(1)"}})).status_code == 422


@pytest.mark.asyncio
async def test_reset_own_and_other_users_appearance(client: AsyncClient, second_client: AsyncClient):
    await admin(client)
    cashier = await add_user(client, "Cashier", "cash1")
    await login_as(second_client, "cash1", "Staff1234")
    await second_client.put("/api/v1/me/appearance", json={"preset": "rose", "mode": "dark"})
    # A cashier cannot reset someone else's appearance
    admin_id = (await client.get("/api/v1/auth/me")).json()["user_id"]
    assert (await second_client.delete(f"/api/v1/users/{admin_id}/appearance")).status_code == 403
    # An administrator with users.manage can
    assert (await client.delete(f"/api/v1/users/{cashier['id']}/appearance")).status_code == 204
    me = (await second_client.get("/api/v1/me/appearance")).json()
    assert me["customized"] is False and me["effective"]["preset"] == "emerald"
    # Reset-to-default on one's own preferences
    await second_client.put("/api/v1/me/appearance", json={"mode": "dark"})
    assert (await second_client.delete("/api/v1/me/appearance")).json()["customized"] is False
    assert (await client.delete("/api/v1/users/99999/appearance")).status_code == 404


@pytest.mark.asyncio
async def test_my_appearance_requires_sign_in(client: AsyncClient):
    await setup_pharmacy(client)
    assert (await client.get("/api/v1/me/appearance")).status_code == 401
    assert (await client.put("/api/v1/me/appearance", json={"preset": "blue"})).status_code in (401, 403)


@pytest.mark.asyncio
async def test_reset_branding_sections(client: AsyncClient):
    await admin(client)
    await client.put("/api/v1/branding", json={"identity": {"app_name": "Other"}, "appearance": {"preset": "rose"}})
    assert (await client.post("/api/v1/branding/reset/appearance")).json()["appearance"]["preset"] == "emerald"
    body = (await client.post("/api/v1/branding/reset/identity")).json()
    assert body["identity"]["app_name"] == "My Pharmacy"
    assert (await client.post("/api/v1/branding/reset/everything")).status_code == 422
