"""Production configuration safety: placeholder secret refused; trusted hosts accept the Docker-internal API name."""
import pytest
from httpx import ASGITransport, AsyncClient

import app.main as main
from app.core.config import settings


@pytest.mark.parametrize("key,ok", [
    ("INSECURE_DEFAULT_CHANGE_IN_PRODUCTION", False),
    ("CHANGE_THIS_TO_A_RANDOM_64_CHARACTER_HEX_STRING", False),
    ("short-secret", False),
    ("", False),
    ("9f2c0d4e8b7a61f35c2e9d0b4a7f1c3e6d8b2a5f7c9e1d3b5a7f9c2e4d6b8a0f", True),
])
def test_production_secret_key_check(monkeypatch, key, ok):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "SECRET_KEY", key)
    assert (main.production_config_problem() is None) is ok


def test_development_is_not_blocked_by_secret_check(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "SECRET_KEY", "INSECURE_DEFAULT_CHANGE_IN_PRODUCTION")
    assert main.production_config_problem() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("host,status", [
    ("backend:8000", 200),        # how the Next.js proxy reaches the API inside Docker
    ("localhost:8000", 200),      # container health check
    ("evil.example.com", 400),
])
async def test_production_trusted_hosts(monkeypatch, host, status):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "ALLOWED_HOSTS", ["localhost", "127.0.0.1"])
    application = main.create_application()
    async with AsyncClient(transport=ASGITransport(app=application), base_url=f"http://{host}") as c:
        r = await c.get("/api/v1/health/live")
    assert r.status_code == status
