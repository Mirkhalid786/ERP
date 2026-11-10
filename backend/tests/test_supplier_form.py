"""Simplified supplier registration: only the trade name is required; GSTIN and state code are optional but,
when given, validated consistently; errors come back per field."""
import pytest
from httpx import AsyncClient

from tests.conftest import login, setup_pharmacy

BLANKS = {k: "" for k in ("legal_name", "gstin", "state_code", "drug_license_no", "contact_person", "phone",
                          "email", "address", "city", "state", "pincode", "notes")}


async def admin(client):
    await setup_pharmacy(client)
    await login(client)


def field_errors(resp) -> dict:
    return {e["loc"][-1]: e["msg"] for e in resp.json()["detail"]}


@pytest.mark.asyncio
async def test_trade_name_only_and_name_plus_phone(client: AsyncClient):
    await admin(client)
    r = await client.post("/api/v1/suppliers", json={"name": "Shree Medical Agencies"})
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["gstin"] is None and s["state_code"] is None and s["payment_terms_days"] == 30
    r = await client.post("/api/v1/suppliers", json={"name": "Balaji Pharma", "phone": " 9876543210 "})
    assert r.status_code == 201 and r.json()["phone"] == "9876543210"


@pytest.mark.asyncio
async def test_blank_optional_fields_are_accepted_as_not_provided(client: AsyncClient):
    """The form sends empty strings for untouched fields; they must not be rejected."""
    await admin(client)
    r = await client.post("/api/v1/suppliers", json={"name": "Krishna Distributors", **BLANKS, "payment_terms_days": 30,
                                                     "credit_limit": "0", "is_active": True})
    assert r.status_code == 201, r.text
    s = r.json()
    assert all(s[k] is None for k in BLANKS), s


@pytest.mark.asyncio
async def test_gstin_is_cleaned_and_state_derived(client: AsyncClient):
    await admin(client)
    r = await client.post("/api/v1/suppliers", json={"name": "Mahalaxmi Pharma", "gstin": "27 aaacc 1234d1z8", "state_code": ""})
    assert r.status_code == 201, r.text
    assert r.json()["gstin"] == "27AAACC1234D1Z8" and r.json()["state_code"] == "27"


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,field", [
    ({"gstin": "27AAACC1234"}, "gstin"),                         # incomplete
    ({"gstin": "27AAACC1234D1Z8X"}, "gstin"),                    # too long
    ({"gstin": "XXAAACC1234D1Z8"}, "gstin"),                     # bad format
    ({"gstin": "27AAACC1234D1Z8", "state_code": "29"}, "state_code"),   # contradicts GSTIN
    ({"state_code": "45"}, "state_code"),                        # not a GST state code
    ({"state_code": "M"}, "state_code"),
    ({"name": " "}, "name"),
])
async def test_invalid_values_get_field_level_errors(client: AsyncClient, payload, field):
    await admin(client)
    r = await client.post("/api/v1/suppliers", json={"name": "Some Stockist", **payload})
    assert r.status_code == 422
    assert field in field_errors(r), r.json()


@pytest.mark.asyncio
async def test_state_code_without_gstin(client: AsyncClient):
    await admin(client)
    r = await client.post("/api/v1/suppliers", json={"name": "Village Stockist", "state_code": "7"})
    assert r.status_code == 201 and r.json()["state_code"] == "07"


@pytest.mark.asyncio
async def test_update_with_blanks_clears_and_keeps_gst_rules(client: AsyncClient):
    await admin(client)
    s = (await client.post("/api/v1/suppliers", json={"name": "Edit Me Pharma", "state_code": "27", "city": "Pune"})).json()
    r = await client.put(f"/api/v1/suppliers/{s['id']}", json={"city": "", "state_code": ""})
    assert r.status_code == 200, r.text
    assert r.json()["city"] is None and r.json()["state_code"] is None
    r = await client.put(f"/api/v1/suppliers/{s['id']}", json={"gstin": "29AAACC1234D1Z8"})
    assert r.json()["state_code"] == "29"
    r = await client.put(f"/api/v1/suppliers/{s['id']}", json={"state_code": "27"})       # contradicts stored GSTIN
    assert r.status_code == 422 and "state_code" in field_errors(r)
    r = await client.put(f"/api/v1/suppliers/{s['id']}", json={"gstin": "bad"})
    assert r.status_code == 422 and "gstin" in field_errors(r)
