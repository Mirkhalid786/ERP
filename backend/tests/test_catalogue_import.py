"""Medicine catalogue import: preview, mapping, validation, duplicates, update mode, Excel, large files, permissions,
all-or-nothing commit, no stock created; and multi-word medicine search."""
import io
import json

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.auth import AuditLog
from app.models.inventory import Medicine, MedicineBatch, MedicineUnitConversion, StockMovement
from tests.conftest import login, setup_pharmacy
from tests.sales_helpers import add_user, login_as


async def admin(client):
    await setup_pharmacy(client)
    await login(client)


def csv_file(text: str, name="catalogue.csv"):
    return {"file": (name, text.encode("utf-8"), "text/csv")}


async def count(engine, model, *where):
    async with async_sessionmaker(engine)() as s:
        return (await s.execute(select(func.count()).select_from(model).where(*where))).scalar()


@pytest.mark.asyncio
async def test_starter_catalogue_imports_cleanly_without_stock(client: AsyncClient, test_engine):
    await admin(client)
    pre = (await client.post("/api/v1/medicines/import/preview", data={"source": "starter"})).json()
    assert pre["total_rows"] == 318 and pre["summary"] == {"create": 318, "update": 0, "skip": 0, "error": 0}, pre["summary"]
    done = await client.post("/api/v1/medicines/import/commit", data={"source": "starter"})
    assert done.status_code == 200, done.text
    assert done.json()["summary"]["create"] == 318
    assert await count(test_engine, Medicine) == 318
    assert await count(test_engine, MedicineBatch) == 0 and await count(test_engine, StockMovement) == 0
    assert await count(test_engine, AuditLog, AuditLog.action == "medicine.catalogue_import") == 1
    again = (await client.post("/api/v1/medicines/import/commit", data={"source": "starter"})).json()
    assert again["summary"] == {"create": 0, "update": 0, "skip": 318, "error": 0}
    assert await count(test_engine, Medicine) == 318


@pytest.mark.asyncio
async def test_auto_mapping_validation_and_in_file_duplicates(client: AsyncClient, test_engine):
    await admin(client)
    text = (
        "Item Name,Salt,Strength,Form,Company,GST %,HSN,Unit,Packing,Barcode\n"
        "Dolo 650 Tablet,Paracetamol,650 mg,Tablet,Micro Labs,12,3004,TAB,15,8901234567890\n"
        "Azee 500 Tab,Azithromycin,500 mg,,Cipla,12,3004,,3,\n"          # form from the name, unit from the form
        "Bad GST Syrup,Something,,Syrup,,7,3004,,,\n"                       # 7% is not a configured rate
        "Bad HSN Cream,Something,,Cream,,,30A4,,,\n"
        "Weird Unit Tab,Something,,Tablet,,,,Bucket,,\n"
        ",,,,,,,,,\n"                                                       # blank line ignored
        "No Form Product,Something,,,,,,,,\n"
        "dolo 650 tablet,Paracetamol,650 mg,Tablet,,,,,,\n"                 # same product again
        "Other Name,Other,,Tablet,,,,,,8901234567890\n"                     # same barcode again
    )
    pre = (await client.post("/api/v1/medicines/import/preview", files=csv_file(text))).json()
    assert pre["mapping"]["name"] == "Item Name" and pre["mapping"]["generic_name"] == "Salt" and pre["mapping"]["gst_rate"] == "GST %"
    assert pre["total_rows"] == 8
    assert pre["summary"] == {"create": 2, "update": 0, "skip": 2, "error": 4}, pre["summary"]
    errors = {p["row"]: " ".join(p["errors"]) for p in pre["problems"]}
    assert "GST rate 7" in errors[4] and "HSN" in errors[5] and "Bucket" in errors[6] and "Dosage form" in errors[8]
    azee = next(r for r in pre["rows"] if r["values"]["name"] == "Azee 500 Tab")
    assert azee["values"]["dosage_form"] == "Tablet" and azee["values"]["base_unit"] == "Tablet" and azee["values"]["pack_unit"] == "Strip"
    done = (await client.post("/api/v1/medicines/import/commit", files=csv_file(text))).json()
    assert done["summary"]["create"] == 2 and done["created_masters"]["manufacturers"] == 2
    assert await count(test_engine, Medicine) == 2
    assert await count(test_engine, MedicineUnitConversion) == 2      # Strip of 15 and Strip of 3


@pytest.mark.asyncio
async def test_update_mode_fills_blanks_but_never_changes_stock_unit(client: AsyncClient, test_engine):
    await admin(client)
    await client.post("/api/v1/medicines/import/commit", files=csv_file("name,generic_name,dosage_form,base_unit\nCrocin Syrup,Paracetamol,Syrup,Bottle\n"))
    text = "name,generic_name,dosage_form,base_unit,manufacturer,strength,pack_unit,pack_size\nCrocin Syrup,Paracetamol,Syrup,Milliliter,GSK,250 mg/5 ml,Box,10\n"
    skip = (await client.post("/api/v1/medicines/import/preview", files=csv_file(text))).json()
    assert skip["summary"]["skip"] == 1
    done = (await client.post("/api/v1/medicines/import/commit", files=csv_file(text), data={"on_duplicate": "update"})).json()
    assert done["summary"] == {"create": 0, "update": 1, "skip": 0, "error": 0}
    med = (await client.get("/api/v1/medicines", params={"search": "crocin"})).json()[0]
    assert med["strength"] == "250 mg/5 ml" and med["manufacturer_name"] == "GSK" and med["base_unit_name"] == "Bottle"
    assert await count(test_engine, MedicineUnitConversion) == 1


@pytest.mark.asyncio
async def test_excel_import(client: AsyncClient, test_engine):
    from openpyxl import Workbook
    await admin(client)
    wb = Workbook()
    ws = wb.active
    ws.append(["Product Name", "Generic Name", "Dosage Form", "HSN Code", "Pack Size"])
    ws.append(["Pan 40 Tablet", "Pantoprazole", "Tablet", 3004, 15])
    ws.append(["Moxikind CV 625", "Amoxicillin + Clavulanic Acid", "Tablet", 3004, "1x6"])
    buf = io.BytesIO()
    wb.save(buf)
    files = {"file": ("catalogue.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    pre = (await client.post("/api/v1/medicines/import/preview", files=files)).json()
    assert pre["summary"]["create"] == 2, pre
    assert [r["values"]["pack_size"] for r in pre["rows"]] == ["15", "6"]
    await client.post("/api/v1/medicines/import/commit", files=files)
    assert await count(test_engine, Medicine) == 2


@pytest.mark.asyncio
async def test_large_import(client: AsyncClient, test_engine):
    await admin(client)
    rows = "\n".join(f"Generic Product {i} 10 mg Tablet,Molecule {i % 400},10 mg,Tablet" for i in range(5000))
    done = await client.post("/api/v1/medicines/import/commit", files=csv_file("name,generic_name,strength,dosage_form\n" + rows))
    assert done.status_code == 200 and done.json()["summary"]["create"] == 5000
    assert await count(test_engine, Medicine) == 5000
    hits = (await client.get("/api/v1/medicines", params={"search": "Product 4321", "limit": 5})).json()
    assert hits[0]["name"] == "Generic Product 4321 10 mg Tablet"


@pytest.mark.asyncio
async def test_failed_commit_leaves_nothing(client: AsyncClient, test_engine, monkeypatch):
    from app.services import catalogue_import as ci
    await admin(client)

    def boom(results):
        raise RuntimeError("simulated failure after the rows were added")
    monkeypatch.setattr(ci, "summary", boom)
    with pytest.raises(RuntimeError):
        await client.post("/api/v1/medicines/import/commit", data={"source": "starter"})
    assert await count(test_engine, Medicine) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("name,data,status", [
    ("x.csv", b"", 422),
    ("x.xls", b"\xd0\xcf\x11\xe0 old excel", 415),
    ("x.csv", b"name\x00,generic\n", 415),
    ("x.csv", b"colour,size\nred,big\n", 422),                    # nothing mappable
], ids=["empty", "old-xls", "binary", "unmappable"])
async def test_rejected_files(client: AsyncClient, name, data, status):
    await admin(client)
    r = await client.post("/api/v1/medicines/import/preview", files={"file": (name, data, "text/csv")})
    assert r.status_code == status, r.text


@pytest.mark.asyncio
async def test_bad_mapping_and_permissions(client: AsyncClient):
    await admin(client)
    r = await client.post("/api/v1/medicines/import/preview", files=csv_file("a,b\n1,2\n"), data={"mapping": json.dumps({"name": "zzz"})})
    assert r.status_code == 422 and "not in the file" in r.json()["detail"]
    await add_user(client, "Cashier", "cash1")
    await client.post("/api/v1/auth/logout")
    await login_as(client, "cash1", "Staff1234")
    assert (await client.post("/api/v1/medicines/import/preview", data={"source": "starter"})).status_code == 403
    assert (await client.post("/api/v1/medicines/import/commit", data={"source": "starter"})).status_code == 403


@pytest.mark.asyncio
async def test_multi_word_search(client: AsyncClient):
    await admin(client)
    await client.post("/api/v1/medicines/import/commit", data={"source": "starter"})
    hits = (await client.get("/api/v1/medicines", params={"search": "paracetamol 650"})).json()
    assert [m["name"] for m in hits] == ["Paracetamol 650 mg Tablet"]
    hits = (await client.get("/api/v1/medicines", params={"search": "amoxicillin syrup"})).json()
    assert hits and all("Amoxicillin" in m["name"] and "Syrup" in m["dosage_form"] for m in hits)
    hits = (await client.get("/api/v1/medicines", params={"search": "Para"})).json()
    assert hits[0]["name"].startswith("Para")
