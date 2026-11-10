"""Last-administrator protection under concurrency (MySQL row locks)."""
import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.main import app
from tests.conftest import VALID_SETUP_PAYLOAD
from tests.mysql.conftest import pytestmark  # noqa: F401  (skip without TEST_MYSQL_URL)


async def _client_logged_in(username: str, password: str) -> AsyncClient:
    c = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")

    async def add_csrf_header(request):
        if request.method not in ("GET", "HEAD", "OPTIONS") and "x-csrf-token" not in request.headers:
            token = c.cookies.get(settings.CSRF_COOKIE_NAME)
            if token:
                request.headers["X-CSRF-Token"] = token

    c.event_hooks["request"].append(add_csrf_header)
    resp = await c.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return c


@pytest.mark.asyncio
async def test_two_admins_demoting_each_other_leaves_one_admin(mysql_client: AsyncClient):
    assert (await mysql_client.post("/api/v1/setup", json=VALID_SETUP_PAYLOAD)).status_code == 201
    a = await _client_logged_in("admin", "Admin123")
    roles = {r["name"]: r["id"] for r in (await a.get("/api/v1/roles")).json()}
    second = await a.post("/api/v1/users", json={"name": "Second Admin", "username": "admin2",
                                                  "password": "Admin2026x", "role_id": roles["Owner / Admin"]})
    assert second.status_code == 201
    b = await _client_logged_in("admin2", "Admin2026x")
    me_a = (await a.get("/api/v1/auth/me")).json()["user_id"]
    me_b = second.json()["id"]

    try:
        results = await asyncio.gather(
            a.put(f"/api/v1/users/{me_b}", json={"role_id": roles["Cashier"]}),
            b.put(f"/api/v1/users/{me_a}", json={"role_id": roles["Cashier"]}),
        )
        codes = sorted(r.status_code for r in results)
        assert codes in ([200, 403], [200, 409]), [r.text for r in results]
        survivor = a if results[0].status_code == 200 else b
        users = (await survivor.get("/api/v1/users")).json()
        assert sum(1 for u in users if u["is_admin"] and u["is_active"]) == 1
    finally:
        await a.aclose()
        await b.aclose()
