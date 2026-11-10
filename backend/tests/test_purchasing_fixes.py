"""
Regression tests for purchasing / inventory correctness fixes:
  1. Tax-inclusive supplier invoices must not count GST twice in the grand total.
  2. GRN cost per base unit is net of trade discount.
  3. Receiving into an existing batch uses weighted average cost and refuses
     MRP / expiry mismatches instead of silently re-pricing stock on hand.
  4. Stock ledger quantity_base is a signed delta for every movement type.
  5. Supplier returns validate GRN line, batch, medicine and supplier ownership,
     including across multiple lines of the same return.
  6. GRN lines and invoice references must belong to their parent documents.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient

from app.services.units import UnitConversionService
from tests.test_purchasing import setup_pharmacy_and_catalogue


EXPIRY = str(date.today() + timedelta(days=365))


def grn_line(medicine_id, unit_id, batch="FIX-B1", qty="10.00", rate="30.00", mrp="45.00",
             selling="40.00", expiry=EXPIRY, **extra) -> dict:
    line = {
        "medicine_id": medicine_id,
        "batch_number": batch,
        "expiry_date": expiry,
        "packaging_unit_id": unit_id,
        "received_qty_pack": qty,
        "purchase_rate": rate,
        "mrp": mrp,
        "selling_rate": selling,
    }
    line.update(extra)
    return line


async def create_grn(client: AsyncClient, supplier_id: int, lines: list, **extra):
    payload = {"supplier_id": supplier_id, "receipt_date": str(date.today()), "items": lines}
    payload.update(extra)
    return await client.post("/api/v1/purchases/receipts", json=payload)


async def receive_and_post(client: AsyncClient, supplier_id: int, lines: list) -> dict:
    resp = await create_grn(client, supplier_id, lines)
    assert resp.status_code == 201, resp.text
    post = await client.post(f"/api/v1/purchases/receipts/{resp.json()['id']}/post")
    assert post.status_code == 200, post.text
    return post.json()


async def create_supplier(client: AsyncClient, name: str) -> dict:
    resp = await client.post("/api/v1/suppliers", json={"name": name, "state_code": "27"})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def create_approved_po(client: AsyncClient, supplier_id: int, medicine_id: int, unit_id: int) -> dict:
    resp = await client.post("/api/v1/purchases/orders", json={
        "supplier_id": supplier_id,
        "order_date": str(date.today()),
        "items": [{
            "medicine_id": medicine_id,
            "packaging_unit_id": unit_id,
            "ordered_qty_pack": "10.00",
            "purchase_rate": "30.00",
        }],
    })
    assert resp.status_code == 201, resp.text
    po = resp.json()
    await client.patch(f"/api/v1/purchases/orders/{po['id']}/status", json={"status": "SUBMITTED"})
    await client.patch(f"/api/v1/purchases/orders/{po['id']}/status", json={"status": "APPROVED"})
    return po


def return_payload(supplier_id: int, lines: list, **extra) -> dict:
    payload = {
        "supplier_id": supplier_id,
        "return_date": str(date.today()),
        "reason_code": "DAMAGED",
        "items": lines,
    }
    payload.update(extra)
    return payload


def return_line(medicine_id, batch_id, unit_id, qty="1.00", **extra) -> dict:
    line = {
        "medicine_id": medicine_id,
        "batch_id": batch_id,
        "packaging_unit_id": unit_id,
        "return_qty_pack": qty,
        "refund_rate": "30.00",
    }
    line.update(extra)
    return line


# ── 1. Tax-inclusive invoice grand total ───────────────────────────────────────

@pytest.mark.asyncio
async def test_tax_inclusive_invoice_does_not_double_count_gst(client: AsyncClient):
    data = await setup_pharmacy_and_catalogue(client)

    resp = await client.post("/api/v1/purchases/invoices", json={
        "supplier_id": data["supplier"]["id"],
        "invoice_number": "INCL-001",
        "invoice_date": str(date.today()),
        "received_date": str(date.today()),
        "is_tax_inclusive": True,
        "supplier_declared_total": "1120.00",
        "items": [{
            "medicine_id": data["medicine"]["id"],
            "batch_number": "INCL-B1",
            "packaging_unit_id": data["strip_unit"]["id"],
            "billed_qty_pack": "10.00",
            "purchase_rate": "112.00",  # GST-inclusive rate
            "tax_rate_id": data["gst_12"]["id"],
        }],
    })
    assert resp.status_code == 201, resp.text
    inv = resp.json()

    # 10 x 112 = 1120 inclusive -> taxable 1000 + GST 120 (CGST 60 + SGST 60)
    assert Decimal(str(inv["tax_total"])) == Decimal("120.00")
    assert Decimal(str(inv["cgst_total"])) == Decimal("60.00")
    assert Decimal(str(inv["items"][0]["taxable_amount"])) == Decimal("1000.00")
    # Grand total is the bill value, NOT 1120 + 120
    assert Decimal(str(inv["calculated_grand_total"])) == Decimal("1120.00")
    assert Decimal(str(inv["discrepancy_amount"])) == Decimal("0.00")


# ── 2. Discount in GRN costing ─────────────────────────────────────────────────

def test_allocated_cost_is_net_of_discount():
    # 10 strips @ 30 with 10% discount + 2 free; 1 strip = 15 tablets
    # Net paid = 300 - 30 = 270 over 12 strips x 15 = 180 tablets -> 1.50 / tablet
    result = UnitConversionService.calculate_allocated_cost(
        paid_quantity=Decimal("10"),
        free_quantity=Decimal("2"),
        unit_purchase_rate=Decimal("30"),
        conversion_factor=Decimal("15"),
        discount_percent=Decimal("10"),
    )
    assert result["total_cost"] == Decimal("270.00")
    assert result["cost_per_base_unit"] == Decimal("1.5000")


@pytest.mark.asyncio
async def test_goods_receipt_cost_includes_trade_discount(client: AsyncClient):
    data = await setup_pharmacy_and_catalogue(client)
    resp = await create_grn(client, data["supplier"]["id"], [grn_line(
        data["medicine"]["id"], data["strip_unit"]["id"],
        free_qty_pack="2.00", discount_percent="10.00",
    )])
    assert resp.status_code == 201, resp.text
    assert Decimal(str(resp.json()["items"][0]["cost_per_base_unit"])) == Decimal("1.5000")


# ── 3. Weighted average cost and batch MRP / expiry protection ─────────────────

@pytest.mark.asyncio
async def test_existing_batch_receipt_uses_weighted_average_cost(client: AsyncClient):
    data = await setup_pharmacy_and_catalogue(client)
    sid, mid, uid = data["supplier"]["id"], data["medicine"]["id"], data["strip_unit"]["id"]

    # 150 tablets @ 2.00 (30 / 15), then 150 tablets @ 3.00 (45 / 15)
    first = await receive_and_post(client, sid, [grn_line(mid, uid, rate="30.00")])
    await receive_and_post(client, sid, [grn_line(mid, uid, rate="45.00")])

    batch_id = first["items"][0]["batch_id"]
    batch = (await client.get(f"/api/v1/batches/{batch_id}")).json()
    assert Decimal(str(batch["current_stock_base"])) == Decimal("300.00")
    assert Decimal(str(batch["cost_per_base_unit"])) == Decimal("2.5000")
    assert Decimal(str(batch["mrp"])) == Decimal("45.00")


@pytest.mark.asyncio
async def test_existing_batch_with_different_mrp_or_expiry_is_rejected(client: AsyncClient):
    data = await setup_pharmacy_and_catalogue(client)
    sid, mid, uid = data["supplier"]["id"], data["medicine"]["id"], data["strip_unit"]["id"]

    first = await receive_and_post(client, sid, [grn_line(mid, uid, mrp="45.00")])
    batch_id = first["items"][0]["batch_id"]

    # Same batch number, different MRP -> posting refused, stock untouched
    bad_mrp = await create_grn(client, sid, [grn_line(mid, uid, mrp="50.00")])
    assert bad_mrp.status_code == 201
    post = await client.post(f"/api/v1/purchases/receipts/{bad_mrp.json()['id']}/post")
    assert post.status_code == 422
    assert "already in stock with mrp" in post.json()["detail"].lower()

    # Same batch number, different expiry -> posting refused
    other_expiry = str(date.today() + timedelta(days=500))
    bad_exp = await create_grn(client, sid, [grn_line(mid, uid, expiry=other_expiry)])
    post = await client.post(f"/api/v1/purchases/receipts/{bad_exp.json()['id']}/post")
    assert post.status_code == 422
    assert "already in stock with expiry" in post.json()["detail"].lower()

    batch = (await client.get(f"/api/v1/batches/{batch_id}")).json()
    assert Decimal(str(batch["current_stock_base"])) == Decimal("150.00")
    assert Decimal(str(batch["mrp"])) == Decimal("45.00")


# ── 4. Signed stock ledger ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stock_ledger_is_signed_and_sums_to_batch_balance(client: AsyncClient):
    data = await setup_pharmacy_and_catalogue(client)
    sid, mid, uid = data["supplier"]["id"], data["medicine"]["id"], data["strip_unit"]["id"]

    grn = await receive_and_post(client, sid, [grn_line(mid, uid)])  # +150
    batch_id = grn["items"][0]["batch_id"]

    adj = await client.post("/api/v1/inventory/adjustments", json={
        "reason_code": "BREAKAGE",
        "items": [{"medicine_id": mid, "batch_id": batch_id, "physical_stock_base": "100.00"}],
    })
    assert adj.status_code == 201, adj.text  # -50

    ret = await client.post("/api/v1/purchases/returns", json=return_payload(
        sid, [return_line(mid, batch_id, uid, qty="1.00")],
    ))
    assert ret.status_code == 201, ret.text
    assert (await client.post(f"/api/v1/purchases/returns/{ret.json()['id']}/post")).status_code == 200  # -15

    movements = (await client.get(f"/api/v1/inventory/movements?batch_id={batch_id}")).json()
    by_type = {m["movement_type"]: Decimal(str(m["quantity_base"])) for m in movements}
    assert by_type["GOODS_RECEIPT"] == Decimal("150.00")
    assert by_type["ADJUSTMENT_OUT"] == Decimal("-50.00")
    assert by_type["RETURN_OUTWARD"] == Decimal("-15.00")

    batch = (await client.get(f"/api/v1/batches/{batch_id}")).json()
    assert sum(by_type.values()) == Decimal(str(batch["current_stock_base"])) == Decimal("85.00")


# ── 5. Supplier return ownership / quantity validation ─────────────────────────

@pytest.mark.asyncio
async def test_return_rejects_batch_of_another_medicine(client: AsyncClient):
    data = await setup_pharmacy_and_catalogue(client)
    sid, mid, uid = data["supplier"]["id"], data["medicine"]["id"], data["strip_unit"]["id"]
    grn = await receive_and_post(client, sid, [grn_line(mid, uid)])
    batch_id = grn["items"][0]["batch_id"]

    other = await client.post("/api/v1/medicines", json={
        "name": "Crocin 500 Tablet",
        "generic_name": "Paracetamol",
        "dosage_form": "Tablet",
        "base_unit_id": data["tablet_unit"]["id"],
        "tax_rate_id": data["gst_12"]["id"],
        "is_active": True,
    })
    assert other.status_code == 201, other.text

    resp = await client.post("/api/v1/purchases/returns", json=return_payload(
        sid, [return_line(other.json()["id"], batch_id, data["tablet_unit"]["id"], qty="5.00")],
    ))
    assert resp.status_code == 422
    assert "does not belong to medicine" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_return_rejects_grn_item_for_a_different_batch(client: AsyncClient):
    data = await setup_pharmacy_and_catalogue(client)
    sid, mid, uid = data["supplier"]["id"], data["medicine"]["id"], data["strip_unit"]["id"]
    grn_a = await receive_and_post(client, sid, [grn_line(mid, uid, batch="BATCH-A", qty="2.00")])
    grn_b = await receive_and_post(client, sid, [grn_line(mid, uid, batch="BATCH-B", qty="20.00")])

    # Cite batch A's GRN line (2 strips) to return from batch B
    resp = await client.post("/api/v1/purchases/returns", json=return_payload(
        sid, [return_line(mid, grn_b["items"][0]["batch_id"], uid, qty="2.00",
                          grn_item_id=grn_a["items"][0]["id"])],
    ))
    assert resp.status_code == 422
    assert "does not match" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_return_without_grn_reference_is_capped_at_supplier_deliveries(client: AsyncClient):
    data = await setup_pharmacy_and_catalogue(client)
    sid, mid, uid = data["supplier"]["id"], data["medicine"]["id"], data["strip_unit"]["id"]
    grn = await receive_and_post(client, sid, [grn_line(mid, uid)])  # 150 tablets supplied
    batch_id = grn["items"][0]["batch_id"]

    # Audit surplus lifts stock to 300, but the supplier only ever delivered 150
    adj = await client.post("/api/v1/inventory/adjustments", json={
        "reason_code": "PHYSICAL_AUDIT_SURPLUS",
        "items": [{"medicine_id": mid, "batch_id": batch_id, "physical_stock_base": "300.00"}],
    })
    assert adj.status_code == 201, adj.text

    too_many = await client.post("/api/v1/purchases/returns", json=return_payload(
        sid, [return_line(mid, batch_id, uid, qty="11.00")],  # 165 > 150
    ))
    assert too_many.status_code == 422
    assert "exceeds returnable quantity" in too_many.json()["detail"].lower()

    # A different supplier never delivered this batch at all
    other_supplier = await create_supplier(client, "Other Distributor")
    wrong_supplier = await client.post("/api/v1/purchases/returns", json=return_payload(
        other_supplier["id"], [return_line(mid, batch_id, uid, qty="1.00")],
    ))
    assert wrong_supplier.status_code == 422

    ok = await client.post("/api/v1/purchases/returns", json=return_payload(
        sid, [return_line(mid, batch_id, uid, qty="10.00")],
    ))
    assert ok.status_code == 201, ok.text


@pytest.mark.asyncio
async def test_return_lines_cannot_jointly_exceed_returnable_quantity(client: AsyncClient):
    data = await setup_pharmacy_and_catalogue(client)
    sid, mid, uid = data["supplier"]["id"], data["medicine"]["id"], data["strip_unit"]["id"]
    grn = await receive_and_post(client, sid, [grn_line(mid, uid)])  # 10 strips
    grn_item = grn["items"][0]

    line = return_line(mid, grn_item["batch_id"], uid, qty="6.00", grn_item_id=grn_item["id"])
    resp = await client.post("/api/v1/purchases/returns", json=return_payload(sid, [line, dict(line)]))
    assert resp.status_code == 422  # 6 + 6 strips > 10 received
    assert "exceeds returnable quantity" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_return_rejects_unposted_grn_and_foreign_supplier_grn(client: AsyncClient):
    data = await setup_pharmacy_and_catalogue(client)
    sid, mid, uid = data["supplier"]["id"], data["medicine"]["id"], data["strip_unit"]["id"]
    posted = await receive_and_post(client, sid, [grn_line(mid, uid)])
    batch_id = posted["items"][0]["batch_id"]

    draft = await create_grn(client, sid, [grn_line(mid, uid)])
    resp = await client.post("/api/v1/purchases/returns", json=return_payload(
        sid, [return_line(mid, batch_id, uid, grn_item_id=draft.json()["items"][0]["id"])],
    ))
    assert resp.status_code == 422
    assert "not posted" in resp.json()["detail"]

    other_supplier = await create_supplier(client, "Other Distributor")
    resp = await client.post("/api/v1/purchases/returns", json=return_payload(
        other_supplier["id"], [return_line(mid, batch_id, uid)], grn_id=posted["id"],
    ))
    assert resp.status_code == 400
    assert "does not belong to this supplier" in resp.json()["detail"]


# ── 6. GRN line and invoice reference ownership ────────────────────────────────

@pytest.mark.asyncio
async def test_grn_rejects_po_line_from_another_order(client: AsyncClient):
    data = await setup_pharmacy_and_catalogue(client)
    sid, mid, uid = data["supplier"]["id"], data["medicine"]["id"], data["strip_unit"]["id"]
    po_a = await create_approved_po(client, sid, mid, uid)
    po_b = await create_approved_po(client, sid, mid, uid)

    resp = await create_grn(
        client, sid, [grn_line(mid, uid, po_item_id=po_b["items"][0]["id"])], po_id=po_a["id"],
    )
    assert resp.status_code == 400
    assert "does not belong to purchase order" in resp.json()["detail"]

    # po_item_id without a linked PO
    resp = await create_grn(client, sid, [grn_line(mid, uid, po_item_id=po_b["items"][0]["id"])])
    assert resp.status_code == 400

    # Order B must be untouched
    po_b_after = (await client.get(f"/api/v1/purchases/orders/{po_b['id']}")).json()
    assert Decimal(str(po_b_after["items"][0]["received_qty_base"])) == Decimal("0.00")


@pytest.mark.asyncio
async def test_invoice_rejects_references_from_another_supplier(client: AsyncClient):
    data = await setup_pharmacy_and_catalogue(client)
    sid, mid, uid = data["supplier"]["id"], data["medicine"]["id"], data["strip_unit"]["id"]
    grn = await receive_and_post(client, sid, [grn_line(mid, uid)])
    other_supplier = await create_supplier(client, "Other Distributor")

    invoice = {
        "supplier_id": other_supplier["id"],
        "invoice_number": "OTHER-001",
        "invoice_date": str(date.today()),
        "received_date": str(date.today()),
        "supplier_declared_total": "300.00",
        "grn_id": grn["id"],
        "items": [{
            "medicine_id": mid,
            "batch_number": "FIX-B1",
            "packaging_unit_id": uid,
            "billed_qty_pack": "10.00",
            "purchase_rate": "30.00",
        }],
    }
    resp = await client.post("/api/v1/purchases/invoices", json=invoice)
    assert resp.status_code == 400
    assert "does not belong to this supplier" in resp.json()["detail"]

    # Right supplier, but a GRN item id that is not on the cited GRN
    invoice.update(supplier_id=sid)
    invoice["items"][0]["grn_item_id"] = grn["items"][0]["id"] + 999
    resp = await client.post("/api/v1/purchases/invoices", json=invoice)
    assert resp.status_code == 400
    assert "does not belong to the invoice's goods receipt" in resp.json()["detail"]
