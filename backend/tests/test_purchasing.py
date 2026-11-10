"""
Unit and integration tests for Purchasing, Goods Receipts, Invoices, and Returns (Phase 3).
"""
import pytest
from datetime import date, timedelta
from decimal import Decimal
from httpx import AsyncClient

from tests.conftest import VALID_SETUP_PAYLOAD


async def setup_pharmacy_and_catalogue(client: AsyncClient) -> dict:
    """Setup pharmacy, seed masters, create a supplier, packaging units, and medicine."""
    await client.post("/api/v1/setup", json=VALID_SETUP_PAYLOAD)
    login_resp = await client.post("/api/v1/auth/login", json={
        "username": VALID_SETUP_PAYLOAD["admin_username"],
        "password": VALID_SETUP_PAYLOAD["admin_password"],
    })
    assert login_resp.status_code == 200

    # 1. Create Supplier
    sup_resp = await client.post("/api/v1/suppliers", json={
        "name": "Cipla Distribution Hub",
        "legal_name": "Cipla Pharma Distribution Ltd",
        "gstin": "27AAACC1234D1Z8",
        "drug_license_no": "MH-20B-998877",
        "payment_terms_days": 30,
        "is_active": True,
    })
    assert sup_resp.status_code == 201
    supplier = sup_resp.json()

    # 2. Get standard units (Tablet, Strip, Box)
    units_resp = await client.get("/api/v1/units")
    units = {u["name"]: u for u in units_resp.json()}
    tablet_unit = units["Tablet"]
    strip_unit = units["Strip"]
    box_unit = units["Box"]

    # 3. Get standard 12% GST tax rate
    tax_resp = await client.get("/api/v1/tax-rates")
    tax_rates = {t["name"]: t for t in tax_resp.json()}
    gst_12 = tax_rates["GST 12%"]

    # 4. Create Medicine: Paracetamol 650mg (Base unit = Tablet)
    med_resp = await client.post("/api/v1/medicines", json={
        "name": "Dolo 650 Tablet",
        "brand_name": "Dolo",
        "generic_name": "Paracetamol",
        "strength": "650 mg",
        "dosage_form": "Tablet",
        "hsn_code": "3004",
        "schedule_type": "OTC",
        "base_unit_id": tablet_unit["id"],
        "tax_rate_id": gst_12["id"],
        "is_active": True,
    })
    assert med_resp.status_code == 201
    medicine = med_resp.json()

    # 5. Add packaging conversions: 1 Strip = 15 Tablets, 1 Box = 150 Tablets
    c1_resp = await client.post(f"/api/v1/medicines/{medicine['id']}/conversions", json={
        "packaging_unit_id": strip_unit["id"],
        "conversion_factor": "15.0000",
        "is_sales_unit": True,
        "is_purchase_unit": True,
    })
    assert c1_resp.status_code == 201

    c2_resp = await client.post(f"/api/v1/medicines/{medicine['id']}/conversions", json={
        "packaging_unit_id": box_unit["id"],
        "conversion_factor": "150.0000",
        "is_sales_unit": True,
        "is_purchase_unit": True,
    })
    assert c2_resp.status_code == 201

    return {
        "supplier": supplier,
        "medicine": medicine,
        "tablet_unit": tablet_unit,
        "strip_unit": strip_unit,
        "box_unit": box_unit,
        "gst_12": gst_12,
    }


@pytest.mark.asyncio
async def test_purchase_order_lifecycle_and_zero_inventory_mutation(client: AsyncClient):
    """
    Creating, submitting, or approving a Purchase Order MUST NOT mutate inventory or record stock movements.
    """
    data = await setup_pharmacy_and_catalogue(client)
    supplier = data["supplier"]
    medicine = data["medicine"]
    strip_unit = data["strip_unit"]
    gst_12 = data["gst_12"]

    # 1. Create Purchase Order for 20 strips @ Rs 25.00/strip with 5% discount
    po_resp = await client.post("/api/v1/purchases/orders", json={
        "supplier_id": supplier["id"],
        "order_date": str(date.today()),
        "expected_delivery_date": str(date.today() + timedelta(days=5)),
        "notes": "Urgent seasonal replenishment",
        "items": [
            {
                "medicine_id": medicine["id"],
                "packaging_unit_id": strip_unit["id"],
                "ordered_qty_pack": "20.00",
                "purchase_rate": "25.00",
                "discount_percent": "5.00",
                "tax_rate_id": gst_12["id"],
                "notes": "Pack of 15s",
            }
        ],
    })
    assert po_resp.status_code == 201
    po = po_resp.json()
    assert po["status"] == "DRAFT"
    assert po["po_number"].startswith("PO-")
    assert len(po["items"]) == 1

    # Line calculations:
    # Gross: 20 * 25 = 500.00
    # Disc 5%: 25.00 -> Taxable: 475.00
    # Tax 12%: 57.00 -> Line total: 532.00
    item = po["items"][0]
    assert float(item["ordered_qty_base"]) == 300.0  # 20 strips * 15 tabs = 300 tablets
    assert float(item["line_total"]) == 532.00
    assert float(po["grand_total"]) == 532.00

    # INVARIANT CHECK: Verify stock movements is EMPTY
    mov_resp = await client.get("/api/v1/inventory/movements")
    assert mov_resp.status_code == 200
    assert len(mov_resp.json()) == 0

    # 2. Submit PO
    sub_resp = await client.patch(f"/api/v1/purchases/orders/{po['id']}/status", json={"status": "SUBMITTED"})
    assert sub_resp.status_code == 200
    assert sub_resp.json()["status"] == "SUBMITTED"

    # 3. Approve PO
    appr_resp = await client.patch(f"/api/v1/purchases/orders/{po['id']}/status", json={"status": "APPROVED"})
    assert appr_resp.status_code == 200
    approved_po = appr_resp.json()
    assert approved_po["status"] == "APPROVED"
    assert approved_po["approved_by"] is not None

    # INVARIANT CHECK: Verify stock movements is STILL EMPTY after approval
    mov_resp2 = await client.get("/api/v1/inventory/movements")
    assert len(mov_resp2.json()) == 0


@pytest.mark.asyncio
async def test_goods_receipt_posting_batches_and_free_goods_costing(client: AsyncClient):
    """
    Goods receipt inward creates batches, computes free goods cost allocation,
    updates stock atomically, and logs immutable stock movements.
    """
    data = await setup_pharmacy_and_catalogue(client)
    supplier = data["supplier"]
    medicine = data["medicine"]
    strip_unit = data["strip_unit"]
    gst_12 = data["gst_12"]

    # Create approved PO for 10 strips @ Rs 30.00
    po_resp = await client.post("/api/v1/purchases/orders", json={
        "supplier_id": supplier["id"],
        "order_date": str(date.today()),
        "items": [
            {
                "medicine_id": medicine["id"],
                "packaging_unit_id": strip_unit["id"],
                "ordered_qty_pack": "10.00",
                "purchase_rate": "30.00",
                "discount_percent": "0.00",
                "tax_rate_id": gst_12["id"],
            }
        ],
    })
    po_id = po_resp.json()["id"]
    po_item_id = po_resp.json()["items"][0]["id"]
    await client.patch(f"/api/v1/purchases/orders/{po_id}/status", json={"status": "SUBMITTED"})
    await client.patch(f"/api/v1/purchases/orders/{po_id}/status", json={"status": "APPROVED"})

    # Create GRN against this PO:
    # Received: 10 strips paid + 2 strips free - 1 strip damaged/rejected = 11 accepted strips
    # MRP = Rs 45.00/strip, Selling Rate = Rs 42.00/strip (<= MRP)
    # Expiry = 1 year from now
    expiry_date = str(date.today() + timedelta(days=365))
    grn_payload = {
        "supplier_id": supplier["id"],
        "po_id": po_id,
        "receipt_date": str(date.today()),
        "supplier_challan_no": "DC-8819",
        "supplier_invoice_no": "INV-7721",
        "items": [
            {
                "po_item_id": po_item_id,
                "medicine_id": medicine["id"],
                "batch_number": "DOLO-B01",
                "manufacturing_date": str(date.today() - timedelta(days=30)),
                "expiry_date": expiry_date,
                "packaging_unit_id": strip_unit["id"],
                "received_qty_pack": "10.00",
                "free_qty_pack": "2.00",
                "rejected_qty_pack": "1.00",
                "rejection_reason": "Broken blister pack",
                "purchase_rate": "30.00",
                "discount_percent": "0.00",
                "mrp": "45.00",
                "selling_rate": "42.00",
                "tax_rate_id": gst_12["id"],
                "rack_location": "Rack A-1",
            }
        ],
    }
    grn_resp = await client.post("/api/v1/purchases/receipts", json=grn_payload)
    assert grn_resp.status_code == 201
    grn = grn_resp.json()
    assert grn["status"] == "DRAFT"

    # Accepted packs = (10 + 2 - 1) = 11 strips
    # Base units per strip = 15 tablets -> 11 * 15 = 165 accepted tablets
    # Rejected base units = 1 * 15 = 15 rejected tablets
    item = grn["items"][0]
    assert float(item["accepted_qty_base"]) == 165.0
    assert float(item["rejected_qty_base"]) == 15.0

    # Allocated cost calculation check:
    # Paid 10 @ 30 = 300. Total physical strips received = 12 (10 paid + 2 free).
    # Effective cost per strip = 300 / 12 = 25.00/strip.
    # Cost per base unit (tablet) = 25.00 / 15 = 1.6667
    assert float(item["cost_per_base_unit"]) == 1.6667

    # Post GRN to stock
    post_resp = await client.post(f"/api/v1/purchases/receipts/{grn['id']}/post")
    assert post_resp.status_code == 200
    posted_grn = post_resp.json()
    assert posted_grn["status"] == "POSTED"

    # Verify batch was created with correct stock
    batch_id = posted_grn["items"][0]["batch_id"]
    batch_resp = await client.get(f"/api/v1/batches/{batch_id}")
    assert batch_resp.status_code == 200
    batch = batch_resp.json()
    assert batch["batch_number"] == "DOLO-B01"
    assert float(batch["current_stock_base"]) == 165.0  # Rejected 15 tablets NOT in stock!
    assert float(batch["mrp"]) == 45.00
    assert float(batch["default_selling_rate"]) == 42.00

    # Verify immutable StockMovement entry
    mov_resp = await client.get(f"/api/v1/inventory/movements?batch_id={batch_id}")
    assert mov_resp.status_code == 200
    movements = mov_resp.json()
    assert len(movements) == 1
    m = movements[0]
    assert m["movement_type"] == "GOODS_RECEIPT"
    assert float(m["quantity_base"]) == 165.0
    assert float(m["balance_after_base"]) == 165.0

    # Duplicate posting guard: Posting again MUST fail with 409 Conflict
    dup_post = await client.post(f"/api/v1/purchases/receipts/{grn['id']}/post")
    assert dup_post.status_code == 409
    assert "already posted" in dup_post.json()["detail"]


@pytest.mark.asyncio
async def test_expired_goods_and_selling_above_mrp_rejected(client: AsyncClient):
    """
    Expired medicines cannot enter saleable inventory.
    Selling price exceeding MRP violates DPCO regulations and must be rejected.
    """
    data = await setup_pharmacy_and_catalogue(client)
    supplier = data["supplier"]
    medicine = data["medicine"]
    strip_unit = data["strip_unit"]

    # 1. Test Expiry rejection (expiry_date in past or today)
    past_expiry = str(date.today() - timedelta(days=5))
    expired_grn = {
        "supplier_id": supplier["id"],
        "receipt_date": str(date.today()),
        "items": [
            {
                "medicine_id": medicine["id"],
                "batch_number": "EXPIRED-BATCH",
                "expiry_date": past_expiry,
                "packaging_unit_id": strip_unit["id"],
                "received_qty_pack": "5.00",
                "purchase_rate": "20.00",
                "mrp": "30.00",
                "selling_rate": "28.00",
            }
        ],
    }
    r_exp = await client.post("/api/v1/purchases/receipts", json=expired_grn)
    assert r_exp.status_code == 422
    assert "expired" in r_exp.json()["detail"].lower()

    # 2. Test DPCO MRP violation (selling_rate > mrp)
    future_expiry = str(date.today() + timedelta(days=200))
    above_mrp_grn = {
        "supplier_id": supplier["id"],
        "receipt_date": str(date.today()),
        "items": [
            {
                "medicine_id": medicine["id"],
                "batch_number": "OVERPRICED-B1",
                "expiry_date": future_expiry,
                "packaging_unit_id": strip_unit["id"],
                "received_qty_pack": "5.00",
                "purchase_rate": "20.00",
                "mrp": "30.00",
                "selling_rate": "35.00",  # VIOLATION: Selling rate > MRP
            }
        ],
    }
    r_mrp = await client.post("/api/v1/purchases/receipts", json=above_mrp_grn)
    assert r_mrp.status_code == 422
    assert "cannot exceed maximum retail price" in r_mrp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_supplier_invoice_verification_and_duplicate_check(client: AsyncClient):
    """
    Supplier invoices record tax components, check duplicate invoice numbers,
    and accurately compute discrepancy against supplier declared bill amounts.
    """
    data = await setup_pharmacy_and_catalogue(client)
    supplier = data["supplier"]
    medicine = data["medicine"]
    strip_unit = data["strip_unit"]
    gst_12 = data["gst_12"]

    invoice_payload = {
        "supplier_id": supplier["id"],
        "invoice_number": "CIPLA/2026/889",
        "invoice_date": str(date.today()),
        "received_date": str(date.today()),
        "due_date": str(date.today() + timedelta(days=30)),
        "payment_terms_days": 30,
        "is_tax_inclusive": False,
        "other_charges": "50.00",  # Freight
        "round_off": "-0.20",
        "supplier_declared_total": "1150.00",  # Physical bill total printed by vendor
        "notes": "Verified against delivery challan",
        "items": [
            {
                "medicine_id": medicine["id"],
                "batch_number": "DOLO-INV-01",
                "packaging_unit_id": strip_unit["id"],
                "billed_qty_pack": "40.00",
                "purchase_rate": "25.00",
                "discount_percent": "5.00",
                "tax_rate_id": gst_12["id"],
            }
        ],
    }

    # Gross: 40 * 25 = 1000.00
    # Disc 5%: 50.00 -> Taxable: 950.00
    # Tax 12%: 114.00 (split into CGST 57 + SGST 57)
    # Calculated Total = 950 + 114 + 50 (freight) - 0.20 (round off) = 1113.80
    # Discrepancy = 1113.80 - 1150.00 = -36.20

    resp = await client.post("/api/v1/purchases/invoices", json=invoice_payload)
    assert resp.status_code == 201
    inv = resp.json()
    assert inv["invoice_number"] == "CIPLA/2026/889"
    assert float(inv["subtotal"]) == 1000.00
    assert float(inv["discount_total"]) == 50.00
    assert float(inv["cgst_total"]) == 57.00
    assert float(inv["sgst_total"]) == 57.00
    assert float(inv["calculated_grand_total"]) == 1113.80
    assert float(inv["discrepancy_amount"]) == -36.20

    # DUPLICATE INVOICE CHECK: Same supplier + invoice number MUST be rejected with 409
    dup_resp = await client.post("/api/v1/purchases/invoices", json=invoice_payload)
    assert dup_resp.status_code == 409
    assert "already been recorded" in dup_resp.json()["detail"]

    # Same number written differently is still the same bill
    variant = await client.post("/api/v1/purchases/invoices", json={**invoice_payload, "invoice_number": "cipla 2026-889"})
    assert variant.status_code == 409

    # The ₹36.20 difference from the printed bill must be explained before finalising
    blocked = await client.post(f"/api/v1/purchases/invoices/{inv['id']}/finalize", json={"acknowledge_warnings": True})
    assert blocked.status_code == 422 and "discrepancy notes" in str(blocked.json()["detail"])
    fixed = await client.put(f"/api/v1/purchases/invoices/{inv['id']}", json={
        **invoice_payload, "discrepancy_notes": "Supplier charged ₹36.20 extra freight; debit note requested"})
    assert fixed.status_code == 200, fixed.text
    fin = await client.post(f"/api/v1/purchases/invoices/{inv['id']}/finalize", json={"acknowledge_warnings": True})
    assert fin.status_code == 200, fin.text
    assert fin.json()["status"] == "POSTED" and fin.json()["discrepancy_accepted"] is True

    # Payment through a supplier payment record
    pay_resp = await client.post("/api/v1/supplier-payments", json={
        "idempotency_key": "cipla-pay-0001", "supplier_id": supplier["id"], "payment_date": str(date.today()),
        "method": "BANK_TRANSFER", "reference": "UTR889", "amount": "1113.80", "auto_allocate": True,
    })
    assert pay_resp.status_code == 201, pay_resp.text
    paid = (await client.get(f"/api/v1/purchases/invoices/{inv['id']}")).json()
    assert paid["payment_status"] == "PAID"
    assert float(paid["paid_amount"]) == 1113.80 and float(paid["outstanding_amount"]) == 0


@pytest.mark.asyncio
async def test_supplier_return_debit_note_and_returnable_validation(client: AsyncClient):
    """
    Supplier return validates returnable quantities against original GRN accepted quantity,
    ensures batch has sufficient stock, atomically decreases inventory, and logs RETURN_OUTWARD movement.
    """
    data = await setup_pharmacy_and_catalogue(client)
    supplier = data["supplier"]
    medicine = data["medicine"]
    strip_unit = data["strip_unit"]
    gst_12 = data["gst_12"]

    # 1. Receive 10 strips (150 tablets)
    grn_resp = await client.post("/api/v1/purchases/receipts", json={
        "supplier_id": supplier["id"],
        "receipt_date": str(date.today()),
        "items": [
            {
                "medicine_id": medicine["id"],
                "batch_number": "RETURN-TEST-B1",
                "expiry_date": str(date.today() + timedelta(days=180)),
                "packaging_unit_id": strip_unit["id"],
                "received_qty_pack": "10.00",
                "purchase_rate": "30.00",
                "mrp": "45.00",
                "selling_rate": "40.00",
                "tax_rate_id": gst_12["id"],
            }
        ],
    })
    grn = grn_resp.json()
    post_resp = await client.post(f"/api/v1/purchases/receipts/{grn['id']}/post")
    assert post_resp.status_code == 200
    posted_grn = post_resp.json()
    grn_item = posted_grn["items"][0]
    batch_id = grn_item["batch_id"]

    # Current stock = 150 tablets (10 strips)

    # 2. Attempt to return MORE than accepted (e.g. 12 strips = 180 tablets > 150 accepted)
    bad_return_payload = {
        "supplier_id": supplier["id"],
        "grn_id": grn["id"],
        "return_date": str(date.today()),
        "reason_code": "DAMAGED",
        "items": [
            {
                "grn_item_id": grn_item["id"],
                "medicine_id": medicine["id"],
                "batch_id": batch_id,
                "packaging_unit_id": strip_unit["id"],
                "return_qty_pack": "12.00",  # Exceeds accepted 10 strips!
                "refund_rate": "30.00",
                "tax_rate_id": gst_12["id"],
            }
        ],
    }
    bad_ret = await client.post("/api/v1/purchases/returns", json=bad_return_payload)
    assert bad_ret.status_code == 422
    assert "exceeds returnable quantity" in bad_ret.json()["detail"].lower()

    # 3. Create Valid Return for 3 strips (45 tablets)
    valid_return_payload = {
        "supplier_id": supplier["id"],
        "grn_id": grn["id"],
        "return_date": str(date.today()),
        "reason_code": "NEAR_EXPIRY",
        "items": [
            {
                "grn_item_id": grn_item["id"],
                "medicine_id": medicine["id"],
                "batch_id": batch_id,
                "packaging_unit_id": strip_unit["id"],
                "return_qty_pack": "3.00",
                "refund_rate": "30.00",
                "tax_rate_id": gst_12["id"],
                "reason": "Expiring within 6 months, return to vendor",
            }
        ],
    }
    ret_resp = await client.post("/api/v1/purchases/returns", json=valid_return_payload)
    assert ret_resp.status_code == 201
    ret = ret_resp.json()
    assert ret["status"] == "DRAFT"
    assert ret["return_number"].startswith("PR-")
    # Gross: 3 * 30 = 90.00 + 12% tax (10.80) = 100.80
    assert float(ret["total_return_amount"]) == 100.80

    # Stock should NOT have changed yet (still in draft)
    b_before = (await client.get(f"/api/v1/batches/{batch_id}")).json()
    assert float(b_before["current_stock_base"]) == 150.0

    # 4. Post Return
    post_ret_resp = await client.post(f"/api/v1/purchases/returns/{ret['id']}/post")
    assert post_ret_resp.status_code == 200
    posted_ret = post_ret_resp.json()
    assert posted_ret["status"] == "POSTED"
    assert posted_ret["debit_note_reference"] is not None

    # Stock MUST be reduced by 45 tablets -> 150 - 45 = 105 tablets remaining!
    b_after = (await client.get(f"/api/v1/batches/{batch_id}")).json()
    assert float(b_after["current_stock_base"]) == 105.0

    # Verify immutable RETURN_OUTWARD ledger entry
    mov_resp = await client.get(f"/api/v1/inventory/movements?batch_id={batch_id}")
    movements = mov_resp.json()
    assert len(movements) == 2  # 1 GOODS_RECEIPT + 1 RETURN_OUTWARD
    ret_movement = next(m for m in movements if m["movement_type"] == "RETURN_OUTWARD")
    assert float(ret_movement["quantity_base"]) == -45.0  # Signed negative delta
    assert float(ret_movement["balance_after_base"]) == 105.0

    # 5. Duplicate return post guard: Posting again returns 409 Conflict
    dup_ret_post = await client.post(f"/api/v1/purchases/returns/{ret['id']}/post")
    assert dup_ret_post.status_code == 409
    assert "already posted" in dup_ret_post.json()["detail"]

    # 6. Second Return: Remaining returnable balance is 10 - 3 = 7 strips (105 tablets).
    # Attempting to return 8 strips must fail!
    excess_second_return = {
        "supplier_id": supplier["id"],
        "grn_id": grn["id"],
        "return_date": str(date.today()),
        "reason_code": "DAMAGED",
        "items": [
            {
                "grn_item_id": grn_item["id"],
                "medicine_id": medicine["id"],
                "batch_id": batch_id,
                "packaging_unit_id": strip_unit["id"],
                "return_qty_pack": "8.00",  # Exceeds remaining 7 strips!
                "refund_rate": "30.00",
            }
        ],
    }
    r_excess = await client.post("/api/v1/purchases/returns", json=excess_second_return)
    assert r_excess.status_code == 422
    assert "exceeds returnable quantity" in r_excess.json()["detail"].lower()


@pytest.mark.asyncio
async def test_partial_goods_receipt_po_fulfillment_status(client: AsyncClient):
    """
    Partial receipt marks PO as PARTIALLY_RECEIVED, and final receipt completes it to RECEIVED.
    """
    data = await setup_pharmacy_and_catalogue(client)
    supplier = data["supplier"]
    medicine = data["medicine"]
    strip_unit = data["strip_unit"]

    # 1. Create and approve PO for 20 strips
    po_resp = await client.post("/api/v1/purchases/orders", json={
        "supplier_id": supplier["id"],
        "order_date": str(date.today()),
        "items": [
            {
                "medicine_id": medicine["id"],
                "packaging_unit_id": strip_unit["id"],
                "ordered_qty_pack": "20.00",
                "purchase_rate": "20.00",
            }
        ],
    })
    po_id = po_resp.json()["id"]
    po_item_id = po_resp.json()["items"][0]["id"]
    await client.patch(f"/api/v1/purchases/orders/{po_id}/status", json={"status": "SUBMITTED"})
    await client.patch(f"/api/v1/purchases/orders/{po_id}/status", json={"status": "APPROVED"})

    # 2. First partial delivery: 12 strips
    grn1_resp = await client.post("/api/v1/purchases/receipts", json={
        "supplier_id": supplier["id"],
        "po_id": po_id,
        "receipt_date": str(date.today()),
        "items": [
            {
                "po_item_id": po_item_id,
                "medicine_id": medicine["id"],
                "batch_number": "PART-B1",
                "expiry_date": str(date.today() + timedelta(days=300)),
                "packaging_unit_id": strip_unit["id"],
                "received_qty_pack": "12.00",
                "purchase_rate": "20.00",
                "mrp": "30.00",
                "selling_rate": "28.00",
            }
        ],
    })
    grn1 = grn1_resp.json()
    await client.post(f"/api/v1/purchases/receipts/{grn1['id']}/post")

    # PO status must now be PARTIALLY_RECEIVED
    po_after_1 = (await client.get(f"/api/v1/purchases/orders/{po_id}")).json()
    assert po_after_1["status"] == "PARTIALLY_RECEIVED"
    assert float(po_after_1["items"][0]["received_qty_base"]) == 180.0  # 12 * 15

    # 3. Second delivery: remaining 8 strips
    grn2_resp = await client.post("/api/v1/purchases/receipts", json={
        "supplier_id": supplier["id"],
        "po_id": po_id,
        "receipt_date": str(date.today()),
        "items": [
            {
                "po_item_id": po_item_id,
                "medicine_id": medicine["id"],
                "batch_number": "PART-B2",
                "expiry_date": str(date.today() + timedelta(days=300)),
                "packaging_unit_id": strip_unit["id"],
                "received_qty_pack": "8.00",
                "purchase_rate": "20.00",
                "mrp": "30.00",
                "selling_rate": "28.00",
            }
        ],
    })
    grn2 = grn2_resp.json()
    await client.post(f"/api/v1/purchases/receipts/{grn2['id']}/post")

    # PO status must now be RECEIVED (fully fulfilled)
    po_after_2 = (await client.get(f"/api/v1/purchases/orders/{po_id}")).json()
    assert po_after_2["status"] == "RECEIVED"
    assert float(po_after_2["items"][0]["received_qty_base"]) == 300.0  # 20 * 15


@pytest.mark.asyncio
async def test_purchase_order_cancellation_rules(client: AsyncClient):
    """
    Purchase orders can be cancelled from DRAFT, SUBMITTED, or APPROVED,
    provided no active goods receipts exist against them.
    """
    data = await setup_pharmacy_and_catalogue(client)
    supplier = data["supplier"]
    medicine = data["medicine"]
    strip_unit = data["strip_unit"]

    # 1. Cancel DRAFT PO
    po1_resp = await client.post("/api/v1/purchases/orders", json={
        "supplier_id": supplier["id"],
        "order_date": str(date.today()),
        "items": [
            {
                "medicine_id": medicine["id"],
                "packaging_unit_id": strip_unit["id"],
                "ordered_qty_pack": "5.00",
                "purchase_rate": "20.00",
            }
        ],
    })
    po1_id = po1_resp.json()["id"]
    c1 = await client.patch(f"/api/v1/purchases/orders/{po1_id}/status", json={"status": "CANCELLED"})
    assert c1.status_code == 200
    assert c1.json()["status"] == "CANCELLED"
    assert c1.json()["cancelled_by"] is not None

    # 2. Cancel APPROVED PO
    po2_resp = await client.post("/api/v1/purchases/orders", json={
        "supplier_id": supplier["id"],
        "order_date": str(date.today()),
        "items": [
            {
                "medicine_id": medicine["id"],
                "packaging_unit_id": strip_unit["id"],
                "ordered_qty_pack": "5.00",
                "purchase_rate": "20.00",
            }
        ],
    })
    po2_id = po2_resp.json()["id"]
    await client.patch(f"/api/v1/purchases/orders/{po2_id}/status", json={"status": "SUBMITTED"})
    await client.patch(f"/api/v1/purchases/orders/{po2_id}/status", json={"status": "APPROVED"})
    c2 = await client.patch(f"/api/v1/purchases/orders/{po2_id}/status", json={"status": "CANCELLED"})
    assert c2.status_code == 200
    assert c2.json()["status"] == "CANCELLED"

    # 3. Cannot cancel when active GRN exists
    po3_resp = await client.post("/api/v1/purchases/orders", json={
        "supplier_id": supplier["id"],
        "order_date": str(date.today()),
        "items": [
            {
                "medicine_id": medicine["id"],
                "packaging_unit_id": strip_unit["id"],
                "ordered_qty_pack": "10.00",
                "purchase_rate": "20.00",
            }
        ],
    })
    po3_id = po3_resp.json()["id"]
    po3_item_id = po3_resp.json()["items"][0]["id"]
    await client.patch(f"/api/v1/purchases/orders/{po3_id}/status", json={"status": "SUBMITTED"})
    await client.patch(f"/api/v1/purchases/orders/{po3_id}/status", json={"status": "APPROVED"})

    # Create GRN
    await client.post("/api/v1/purchases/receipts", json={
        "supplier_id": supplier["id"],
        "po_id": po3_id,
        "receipt_date": str(date.today()),
        "items": [
            {
                "po_item_id": po3_item_id,
                "medicine_id": medicine["id"],
                "batch_number": "PO-CANCEL-TEST",
                "expiry_date": str(date.today() + timedelta(days=200)),
                "packaging_unit_id": strip_unit["id"],
                "received_qty_pack": "5.00",
                "purchase_rate": "20.00",
                "mrp": "30.00",
                "selling_rate": "25.00",
            }
        ],
    })

    # Cancellation must be rejected because GRN exists
    c3 = await client.patch(f"/api/v1/purchases/orders/{po3_id}/status", json={"status": "CANCELLED"})
    assert c3.status_code == 400
    assert "active goods receipts" in c3.json()["detail"].lower()


@pytest.mark.asyncio
async def test_insufficient_stock_for_supplier_return(client: AsyncClient):
    """
    If available batch stock is less than requested return quantity, return must be rejected.
    """
    data = await setup_pharmacy_and_catalogue(client)
    supplier = data["supplier"]
    medicine = data["medicine"]
    strip_unit = data["strip_unit"]

    # 1. Receive 5 strips (75 tablets)
    grn_resp = await client.post("/api/v1/purchases/receipts", json={
        "supplier_id": supplier["id"],
        "receipt_date": str(date.today()),
        "items": [
            {
                "medicine_id": medicine["id"],
                "batch_number": "SHORT-STOCK-B1",
                "expiry_date": str(date.today() + timedelta(days=200)),
                "packaging_unit_id": strip_unit["id"],
                "received_qty_pack": "5.00",
                "purchase_rate": "20.00",
                "mrp": "30.00",
                "selling_rate": "25.00",
            }
        ],
    })
    grn = grn_resp.json()
    post_resp = await client.post(f"/api/v1/purchases/receipts/{grn['id']}/post")
    batch_id = post_resp.json()["items"][0]["batch_id"]

    # Adjust stock down to 15 tablets (1 strip) due to breakage
    await client.post("/api/v1/inventory/adjustments", json={
        "reason_code": "BREAKAGE",
        "notes": "Accidental damage",
        "items": [
            {
                "medicine_id": medicine["id"],
                "batch_id": batch_id,
                "physical_stock_base": "15.00",
            }
        ],
    })

    # Batch only has 15 tablets remaining.
    # Attempting to return 2 strips = 30 tablets (> 15 tablets) must be rejected with 422!
    ret_resp = await client.post("/api/v1/purchases/returns", json={
        "supplier_id": supplier["id"],
        "grn_id": grn["id"],
        "return_date": str(date.today()),
        "reason_code": "DAMAGED",
        "items": [
            {
                "medicine_id": medicine["id"],
                "batch_id": batch_id,
                "packaging_unit_id": strip_unit["id"],
                "return_qty_pack": "2.00",  # 30 tablets > 15 available!
                "refund_rate": "20.00",
            }
        ],
    })
    assert ret_resp.status_code == 422
    assert "only has 15.00 units in current stock" in ret_resp.json()["detail"].lower()
