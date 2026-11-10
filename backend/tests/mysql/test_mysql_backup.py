"""Backups on real MySQL: consistent dump, checksum, verification and (optionally) a test restore.

The dump always targets the disposable TEST_MYSQL_URL database, never the live one.
Set TEST_BACKUP_VERIFY_DATABASE (a schema ending in "_restore_verify" that the test user
may write) to also exercise the restore test.
"""
import gzip
import os

import pytest
from httpx import AsyncClient

from app.core.config import settings
from tests.mysql.conftest import MYSQL_URL, pytestmark  # noqa: F401
from tests.sales_helpers import line, receive_stock, sell, key
from tests.test_purchasing import setup_pharmacy_and_catalogue


@pytest.fixture
def backup_env(tmp_path, monkeypatch):
    sync_url = MYSQL_URL.replace("mysql+aiomysql://", "mysql+pymysql://")
    assert sync_url.rstrip("/").endswith("_test")
    monkeypatch.setattr(settings, "DATABASE_URL_SYNC", sync_url)
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "BACKUP_VERIFY_DATABASE", os.environ.get("TEST_BACKUP_VERIFY_DATABASE", ""))
    return tmp_path


@pytest.mark.asyncio
async def test_backup_is_complete_checksummed_and_verifiable(mysql_client: AsyncClient, backup_env):
    data = await setup_pharmacy_and_catalogue(mysql_client)
    await receive_stock(mysql_client, data, "BK-B1")
    await sell(mysql_client, [line(data, "1")], [{"method": "CASH", "amount": "42.00"}])
    made = await mysql_client.post("/api/v1/admin/backups")
    assert made.status_code == 201, made.text
    rec = made.json()
    assert rec["status"] == "COMPLETED" and len(rec["sha256"]) == 64 and rec["total_rows"] > 0
    path = backup_env / rec["file_name"]
    with gzip.open(path, "rt", encoding="utf-8") as f:
        body = f.read()
    assert "INSERT INTO `sales_invoices`" in body and body.rstrip().endswith("DUMP COMPLETE")
    ok = (await mysql_client.post(f"/api/v1/admin/backups/{rec['id']}/verify")).json()
    assert ok["verify_status"] == "PASSED", ok["verify_detail"]
    if settings.BACKUP_VERIFY_DATABASE:
        assert '"restore_test": "passed"' in ok["verify_detail"]
    dl = await mysql_client.get(f"/api/v1/admin/backups/{rec['id']}/download")
    assert dl.status_code == 200 and dl.content[:2] == b"\x1f\x8b"

    # A damaged file fails verification
    with open(path, "ab") as f:
        f.write(b"tampered")
    bad = (await mysql_client.post(f"/api/v1/admin/backups/{rec['id']}/verify")).json()
    assert bad["verify_status"] == "FAILED" and "Checksum" in bad["verify_detail"]
    health = (await mysql_client.get("/api/v1/admin/system-health")).json()
    assert health["checks"]["database"]["ok"] and health["checks"]["backup"]["last_file"] == rec["file_name"]


@pytest.mark.asyncio
async def test_backup_includes_binary_logo_byte_for_byte(mysql_client: AsyncClient, backup_env):
    """Logos are binary (MEDIUMBLOB). The dump must still be valid UTF-8 text and restore them exactly."""
    from tests.test_branding import png
    from app.services import admin_ops
    await setup_pharmacy_and_catalogue(mysql_client)
    logo = png(37, 11) + bytes(range(256))         # every byte value, including invalid UTF-8 sequences
    up = await mysql_client.post("/api/v1/branding/assets/logo", files={"file": ("l.png", logo, "image/png")})
    assert up.status_code == 201, up.text
    made = await mysql_client.post("/api/v1/admin/backups")
    rec = made.json()
    assert made.status_code == 201 and rec["status"] == "COMPLETED", rec
    with gzip.open(backup_env / rec["file_name"], "rt", encoding="utf-8") as f:
        assert "INSERT INTO `brand_assets`" in f.read()
    ok = (await mysql_client.post(f"/api/v1/admin/backups/{rec['id']}/verify")).json()
    assert ok["verify_status"] == "PASSED", ok["verify_detail"]
    if settings.BACKUP_VERIFY_DATABASE:
        conn = admin_ops._connect(settings.DATABASE_URL_SYNC, database=settings.BACKUP_VERIFY_DATABASE)
        try:
            cur = conn.cursor()
            cur.execute("SELECT data FROM brand_assets WHERE kind = 'logo'")
            assert cur.fetchone()[0] == logo
        finally:
            conn.close()
