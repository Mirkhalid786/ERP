"""Phase 4.1: staff user management and access control."""
import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.auth import AuditLog, User
from tests.conftest import login, setup_pharmacy

STRONG = "Counter2026"


async def roles(client: AsyncClient) -> dict:
    return {r["name"]: r for r in (await client.get("/api/v1/roles")).json()}


async def create(client: AsyncClient, role: str, username: str, password: str = STRONG, expect: int = 201) -> dict:
    resp = await client.post("/api/v1/users", json={
        "name": f"{role} Staff", "username": username, "password": password,
        "role_id": (await roles(client))[role]["id"],
    })
    assert resp.status_code == expect, resp.text
    return resp.json()


async def admin_setup(client: AsyncClient):
    await setup_pharmacy(client)
    await login(client)


def session_factory(test_engine):
    return async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.mark.asyncio
async def test_create_list_search_and_no_password_exposure(client: AsyncClient):
    await admin_setup(client)
    cashier = await create(client, "Cashier", "Cashier.One")
    assert cashier["username"] == "cashier.one" and cashier["is_admin"] is False
    assert cashier["created_by_name"] == "Admin User" and cashier["is_active"] is True

    listing = await client.get("/api/v1/users")
    assert listing.status_code == 200
    assert "password" not in listing.text and "argon2" not in listing.text
    names = {u["username"]: u for u in listing.json()}
    assert names["admin"]["is_admin"] is True and names["admin"]["last_login_at"]

    found = (await client.get("/api/v1/users?q=cashier")).json()
    assert [u["username"] for u in found] == ["cashier.one"]


@pytest.mark.asyncio
async def test_duplicate_username_and_weak_password_rejected(client: AsyncClient):
    await admin_setup(client)
    await create(client, "Cashier", "ravi")
    dup = await client.post("/api/v1/users", json={
        "name": "Ravi Two", "username": "RAVI", "password": STRONG, "role_id": (await roles(client))["Cashier"]["id"]})
    assert dup.status_code == 409 and "already taken" in dup.json()["detail"]

    for weak in ("short1A", "alllowercase1", "ALLUPPER123", "NoDigitsHere"):
        await create(client, "Cashier", f"weak{len(weak)}x", password=weak, expect=422)

    other = await create(client, "Pharmacist", "meena")
    rename = await client.put(f"/api/v1/users/{other['id']}", json={"username": "ravi"})
    assert rename.status_code == 409


@pytest.mark.asyncio
async def test_password_is_hashed_and_login_works(client: AsyncClient, second_client: AsyncClient, test_engine):
    await admin_setup(client)
    await create(client, "Cashier", "asha")
    async with session_factory(test_engine)() as db:
        stored = (await db.execute(select(User.password_hash).where(User.username == "asha"))).scalar_one()
    assert stored.startswith("$argon2id$") and STRONG not in stored

    ok = await second_client.post("/api/v1/auth/login", json={"username": " Asha ", "password": STRONG})
    assert ok.status_code == 200 and ok.json()["role"] == "Cashier"
    wrong = await second_client.post("/api/v1/auth/login", json={"username": "asha", "password": "Wrong2026x"})
    assert wrong.status_code == 401


@pytest.mark.asyncio
async def test_permissions_enforced_for_non_admins(client: AsyncClient, second_client: AsyncClient):
    await admin_setup(client)
    await create(client, "Cashier", "kiran")
    await create(client, "Pharmacist", "priya")

    assert (await second_client.get("/api/v1/users")).status_code == 401          # not signed in

    await login(second_client, "kiran", STRONG)
    me = (await second_client.get("/api/v1/auth/me")).json()["permissions"]
    assert "pos.access" in me and "payments.receive" in me
    for sensitive in ("users.manage", "sales.cancel", "sales.refund", "sales.discount", "sales.credit_override",
                      "sales.prescription", "customers.adjust", "shifts.review"):
        assert sensitive not in me
    assert (await second_client.get("/api/v1/users")).status_code == 403
    assert (await second_client.post("/api/v1/users", json={
        "name": "Sneaky", "username": "sneaky", "password": STRONG, "role_id": 1})).status_code == 403
    assert (await second_client.put("/api/v1/users/1", json={"is_active": False})).status_code == 403
    assert (await second_client.post("/api/v1/users/1/reset-password", json={"new_password": STRONG})).status_code == 403

    await login(second_client, "priya", STRONG)
    pharmacist = (await second_client.get("/api/v1/auth/me")).json()["permissions"]
    assert "sales.prescription" in pharmacist and "users.manage" not in pharmacist
    assert (await second_client.get("/api/v1/users")).status_code == 403


@pytest.mark.asyncio
async def test_deactivated_user_cannot_login_or_keep_using_session(client: AsyncClient, second_client: AsyncClient):
    await admin_setup(client)
    staff = await create(client, "Cashier", "deepak")
    await login(second_client, "deepak", STRONG)
    assert (await second_client.get("/api/v1/auth/me")).status_code == 200

    off = await client.put(f"/api/v1/users/{staff['id']}", json={"is_active": False})
    assert off.status_code == 200 and off.json()["is_active"] is False and off.json()["active_sessions"] == 0
    assert (await second_client.get("/api/v1/auth/me")).status_code == 401           # session ended
    relog = await second_client.post("/api/v1/auth/login", json={"username": "deepak", "password": STRONG})
    assert relog.status_code == 401

    back = await client.put(f"/api/v1/users/{staff['id']}", json={"is_active": True})
    assert back.json()["is_active"] is True
    assert (await second_client.post("/api/v1/auth/login", json={"username": "deepak", "password": STRONG})).status_code == 200


@pytest.mark.asyncio
async def test_password_reset_ends_sessions_and_old_password(client: AsyncClient, second_client: AsyncClient):
    await admin_setup(client)
    staff = await create(client, "Cashier", "farhan")
    await login(second_client, "farhan", STRONG)

    weak = await client.post(f"/api/v1/users/{staff['id']}/reset-password", json={"new_password": "weakpass"})
    assert weak.status_code == 422
    reset = await client.post(f"/api/v1/users/{staff['id']}/reset-password", json={"new_password": "NewCounter2027"})
    assert reset.status_code == 200 and reset.json()["sessions_ended"] == 1
    assert (await second_client.get("/api/v1/auth/me")).status_code == 401
    assert (await second_client.post("/api/v1/auth/login", json={"username": "farhan", "password": STRONG})).status_code == 401
    assert (await second_client.post("/api/v1/auth/login", json={"username": "farhan", "password": "NewCounter2027"})).status_code == 200

    # Resetting your own password keeps the session you are using
    me = (await client.get("/api/v1/auth/me")).json()
    own = await client.post(f"/api/v1/users/{me['user_id']}/reset-password", json={"new_password": "Admin2027x"})
    assert own.status_code == 200
    assert (await client.get("/api/v1/auth/me")).status_code == 200


@pytest.mark.asyncio
async def test_last_active_administrator_is_protected(client: AsyncClient):
    await admin_setup(client)
    me = (await client.get("/api/v1/auth/me")).json()
    r = await roles(client)

    self_off = await client.put(f"/api/v1/users/{me['user_id']}", json={"is_active": False})
    assert self_off.status_code == 409
    demote = await client.put(f"/api/v1/users/{me['user_id']}", json={"role_id": r["Cashier"]["id"]})
    assert demote.status_code == 409 and "last active administrator" in demote.json()["detail"]

    second = await create(client, "Owner / Admin", "owner2")
    assert second["is_admin"] is True
    # With another admin present, the first can now be demoted (role decides, not the name)
    ok = await client.put(f"/api/v1/users/{me['user_id']}", json={"role_id": r["Pharmacist"]["id"]})
    assert ok.status_code == 200 and ok.json()["is_admin"] is False
    # ...and that change took effect immediately for this session
    assert (await client.get("/api/v1/users")).status_code == 403


@pytest.mark.asyncio
async def test_admin_actions_are_audited_without_secrets(client: AsyncClient, test_engine):
    await admin_setup(client)
    staff = await create(client, "Cashier", "gita")
    r = await roles(client)
    await client.put(f"/api/v1/users/{staff['id']}", json={"role_id": r["Pharmacist"]["id"]})
    await client.post(f"/api/v1/users/{staff['id']}/reset-password", json={"new_password": "Secret2027Pass"})
    await client.put(f"/api/v1/users/{staff['id']}", json={"is_active": False})

    history = (await client.get(f"/api/v1/users/{staff['id']}/audit")).json()
    assert [h["action"] for h in history] == ["user.deactivate", "user.password_reset", "user.role_change", "user.create"]
    role_change = next(h for h in history if h["action"] == "user.role_change")
    assert role_change["details"]["role"] == {"from": "Cashier", "to": "Pharmacist"}
    assert all(h["by"] == "Admin User" for h in history)

    async with session_factory(test_engine)() as db:
        logs = (await db.execute(select(AuditLog).where(AuditLog.entity_type == "user"))).scalars().all()
    blob = json.dumps([(l.action, l.metadata_json, l.reason) for l in logs])
    assert "Secret2027Pass" not in blob and STRONG not in blob and "argon2" not in blob
