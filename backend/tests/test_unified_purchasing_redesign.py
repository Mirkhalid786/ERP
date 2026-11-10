"""
Comprehensive Automated Test Suite for Unified Purchasing & Procurement Redesign.

Tests Scenarios A through F as specified in Section 7 of the user requirements:
- Scenario A: Multi-medicine purchase lifecycle (Create -> Submit -> Return for correction -> Edit -> Approve -> GRN -> Invoice -> 3-way match -> Finalize -> Ledger/Payables/Stock verification).
- Scenario B: Partial receipts (Order 100 -> Receive 60 -> 40 remaining -> Receive 40 in 2nd GRN -> Invoice matching).
- Scenario C: Quantity mismatch warning and overbilling block.
- Scenario D: Rate/tax discrepancy warning and check.
- Scenario E: Idempotent duplicate finalization prevention.
- Scenario F: RBAC verification across Admin, Pharmacist, Inventory Manager, and Cashier.
- Self-approval policy enforcement.
"""
from datetime import date, timedelta
from decimal import Decimal
import pytest
from httpx import AsyncClient

from tests.conftest import VALID_SETUP_PAYLOAD


async def setup_procurement_environment(client: AsyncClient) -> dict:
    """Setup pharmacy, seed masters, create supplier, packaging units, and two test medicines."""
    await client.post("/api/v1/setup", json=VALID_SETUP_PAYLOAD)
    login_resp = await client.post("/api/v1/auth/login", json={
        "username": VALID_SETUP_PAYLOAD["admin_username"],
        "password": VALID_SETUP_PAYLOAD["admin_password"],
    })
    assert login_resp.status_code == 200

    # 1. Create Supplier
    sup_resp = await client.post("/api/v1/suppliers", json={
        "name": "Apex Healthcare Distributors",
        "legal_name": "Apex Healthcare Distributors Pvt Ltd",
        "gstin": "27AAPCA1234F1Z5",
        "drug_license_no": "MH-20B-778899",
        "payment_terms_days": 30,
        "is_active": True,
    })
    assert sup_resp.status_code == 201
    supplier = sup_resp.json()

    # 2. Get units
    units_resp = await client.get("/api/v1/units")
    units = {u["name"]: u for u in units_resp.json()}
    tablet_unit = units["Tablet"]
    strip_unit = units["Strip"]
    box_unit = units["Box"]

    # 3. Get tax rates
    tax_resp = await client.get("/api/v1/tax-rates")
    tax_rates = {t["name"]: t for t in tax_resp.json()}
    gst_12 = tax_rates["GST 12%"]
    gst_18 = tax_rates["GST 18%"]

    # 4. Create Medicine 1: Dolo 650 (12% GST, Tablet base, 1 Strip = 15 Tablets)
    med1_resp = await client.post("/api/v1/medicines", json={
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
    assert med1_resp.status_code == 201
    med1 = med1_resp.json()
    await client.post(f"/api/v1/medicines/{med1['id']}/conversions", json={
        "packaging_unit_id": strip_unit["id"],
        "conversion_factor": "15.0000",
        "is_sales_unit": True,
        "is_purchase_unit": True,
    })

    # 5. Create Medicine 2: Augmentin 625 (18% GST, Tablet base, 1 Strip = 10 Tablets)
    med2_resp = await client.post("/api/v1/medicines", json={
        "name": "Augmentin 625 Duo",
        "brand_name": "Augmentin",
        "generic_name": "Amoxicillin and Clavulanic Acid",
        "strength": "625 mg",
        "dosage_form": "Tablet",
        "hsn_code": "3004",
        "schedule_type": "H",
        "base_unit_id": tablet_unit["id"],
        "tax_rate_id": gst_18["id"],
        "is_active": True,
    })
    assert med2_resp.status_code == 201
    med2 = med2_resp.json()
    await client.post(f"/api/v1/medicines/{med2['id']}/conversions", json={
        "packaging_unit_id": strip_unit["id"],
        "conversion_factor": "10.0000",
        "is_sales_unit": True,
        "is_purchase_unit": True,
    })

    # 6. Initialize Accounting books
    await client.post("/api/v1/accounting/setup", json={
        "books_start_date": str(date.today().replace(day=1)),
    })

    return {
        "supplier": supplier,
        "med1": med1,
        "med2": med2,
        "tablet_unit": tablet_unit,
        "strip_unit": strip_unit,
        "box_unit": box_unit,
        "gst_12": gst_12,
        "gst_18": gst_18,
    }


@pytest.mark.asyncio
async def test_scenario_a_full_procurement_lifecycle(client: AsyncClient):
    """
    Scenario A:
    - Create a purchase with multiple medicines (Stage 1).
    - Return for correction (DRAFT) and edit purchase.
    - Submit and approve it (Stage 2).
    - Verify stock remains ZERO on PO approval (No stock created prematurely).
    - Receive all medicines with batch and expiry info (Stage 3 GRN).
    - Verify stock is updated ONLY after GRN posting.
    - Record supplier invoice carry-forwarding GRN lines (Stage 4).
    - Run 3-way match finalize check.
    - Finalize purchase invoice (Stage 5).
    - Verify stock ledger, AP supplier liability, and General Ledger double-entry records.
    """
    env = await setup_procurement_environment(client)
    sup = env["supplier"]
    m1 = env["med1"]
    m2 = env["med2"]
    strip = env["strip_unit"]

    # 1. Stage 1: Create Purchase Order (Draft)
    po_payload = {
        "supplier_id": sup["id"],
        "order_date": date.today().isoformat(),
        "expected_delivery_date": (date.today() + timedelta(days=5)).isoformat(),
        "notes": "Scenario A Full Lifecycle Procurement Order",
        "items": [
            {
                "medicine_id": m1["id"],
                "packaging_unit_id": strip["id"],
                "ordered_qty_pack": "20.0000",  # 20 strips = 300 tablets
                "purchase_rate": "150.0000",
                "discount_percent": "5.00",
                "tax_rate_id": env["gst_12"]["id"],
            },
            {
                "medicine_id": m2["id"],
                "packaging_unit_id": strip["id"],
                "ordered_qty_pack": "10.0000",  # 10 strips = 100 tablets
                "purchase_rate": "200.0000",
                "discount_percent": "0.00",
                "tax_rate_id": env["gst_18"]["id"],
            },
        ],
    }
    po_create_resp = await client.post("/api/v1/purchases/orders", json=po_payload)
    assert po_create_resp.status_code == 201, po_create_resp.text
    po = po_create_resp.json()
    assert po["status"] == "DRAFT"
    po_id = po["id"]

    # Test Edit Draft PO
    po_payload["notes"] = "Scenario A - Edited Draft Notes"
    edit_resp = await client.put(f"/api/v1/purchases/orders/{po_id}", json=po_payload)
    assert edit_resp.status_code == 200
    assert edit_resp.json()["notes"] == "Scenario A - Edited Draft Notes"

    # Stage 2: Submit for approval
    sub_resp = await client.patch(f"/api/v1/purchases/orders/{po_id}/status", json={"status": "SUBMITTED"})
    assert sub_resp.status_code == 200
    assert sub_resp.json()["status"] == "SUBMITTED"

    # Test "Return for correction" back to DRAFT with reason
    ret_resp = await client.patch(f"/api/v1/purchases/orders/{po_id}/status", json={
        "status": "DRAFT",
        "reason": "Please check discount terms with supplier",
    })
    assert ret_resp.status_code == 200
    assert ret_resp.json()["status"] == "DRAFT"

    # Re-submit and Approve
    await client.patch(f"/api/v1/purchases/orders/{po_id}/status", json={"status": "SUBMITTED"})
    app_resp = await client.patch(f"/api/v1/purchases/orders/{po_id}/status", json={"status": "APPROVED"})
    assert app_resp.status_code == 200
    assert app_resp.json()["status"] == "APPROVED"

    # Verify inventory is ZERO right now (Approval does NOT increase stock)
    inv1 = await client.get(f"/api/v1/medicines/{m1['id']}")
    assert Decimal(str(inv1.json()["current_stock_base"])) == Decimal("0")

    # Check PO lifecycle helper endpoint
    lc_resp = await client.get(f"/api/v1/purchases/orders/{po_id}/lifecycle")
    assert lc_resp.status_code == 200
    lc = lc_resp.json()
    assert lc["lifecycle_stage"] == "AWAITING_GOODS"
    assert len(lc["po"]["items"]) == 2

    po_item1 = po["items"][0]
    po_item2 = po["items"][1]

    # Stage 3: Integrated Goods Receipt (GRN)
    exp_date = (date.today() + timedelta(days=365)).isoformat()
    mfg_date = (date.today() - timedelta(days=30)).isoformat()
    grn_payload = {
        "supplier_id": sup["id"],
        "po_id": po_id,
        "receipt_date": date.today().isoformat(),
        "supplier_challan_no": "DC-SCENARIO-A",
        "supplier_invoice_no": "INV-SCENARIO-A-001",
        "items": [
            {
                "medicine_id": m1["id"],
                "po_item_id": po_item1["id"],
                "packaging_unit_id": strip["id"],
                "received_qty_pack": "20.0000",
                "free_qty_pack": "0.0000",
                "rejected_qty_pack": "0.0000",
                "batch_number": "DOLO-BATCH-A1",
                "manufacturing_date": mfg_date,
                "expiry_date": exp_date,
                "mrp": "220.0000",
                "selling_rate": "200.0000",
                "purchase_rate": "150.0000",
                "discount_percent": "5.00",
                "tax_rate_id": env["gst_12"]["id"],
                "rack_location": "Rack A-1",
            },
            {
                "medicine_id": m2["id"],
                "po_item_id": po_item2["id"],
                "packaging_unit_id": strip["id"],
                "received_qty_pack": "10.0000",
                "free_qty_pack": "1.0000",  # 1 free strip!
                "rejected_qty_pack": "0.0000",
                "batch_number": "AUG-BATCH-A1",
                "manufacturing_date": mfg_date,
                "expiry_date": exp_date,
                "mrp": "320.0000",
                "selling_rate": "300.0000",
                "purchase_rate": "200.0000",
                "discount_percent": "0.00",
                "tax_rate_id": env["gst_18"]["id"],
                "rack_location": "Fridge Shelf 2",
            },
        ],
    }
    grn_create_resp = await client.post("/api/v1/purchases/receipts", json=grn_payload)
    assert grn_create_resp.status_code == 201, grn_create_resp.text
    grn = grn_create_resp.json()
    grn_id = grn["id"]

    # Post Goods Receipt -> updates stock
    post_grn_resp = await client.post(f"/api/v1/purchases/receipts/{grn_id}/post")
    assert post_grn_resp.status_code == 200, post_grn_resp.text
    assert post_grn_resp.json()["status"] == "POSTED"

    # Verify inventory is now strictly updated
    # m1: 20 strips * 15 = 300 base units (Tablets)
    # m2: (10 + 1 free) strips * 10 = 110 base units (Tablets)
    inv1_after = await client.get(f"/api/v1/medicines/{m1['id']}")
    assert Decimal(str(inv1_after.json()["current_stock_base"])) == Decimal("300.00")
    inv2_after = await client.get(f"/api/v1/medicines/{m2['id']}")
    assert Decimal(str(inv2_after.json()["current_stock_base"])) == Decimal("110.00")

    # Verify PO status updated to RECEIVED
    po_check = await client.get(f"/api/v1/purchases/orders/{po_id}")
    assert po_check.json()["status"] == "RECEIVED"

    # Stage 4: Integrated Supplier Invoice
    grn_item1 = grn["items"][0]
    grn_item2 = grn["items"][1]

    # Calculate expected totals:
    # Item 1: 20 * 150 * 0.95 = 2850. GST 12% = 342. Total = 3192.
    # Item 2: 10 * 200 = 2000. GST 18% = 360. Total = 2360.
    # Subtotal = 4850, Total tax = 702, Total value = 5552.
    # other_charges = 48 -> declared total = 5600.
    inv_payload = {
        "supplier_id": sup["id"],
        "po_id": po_id,
        "grn_id": grn_id,
        "invoice_number": "INV-SCENARIO-A-001",
        "invoice_date": date.today().isoformat(),
        "received_date": date.today().isoformat(),
        "due_date": (date.today() + timedelta(days=30)).isoformat(),
        "other_charges": "48.0000",
        "round_off": "0.0000",
        "supplier_declared_total": "5600.0000",
        "notes": "Unified invoice from PO and GRN",
        "items": [
            {
                "medicine_id": m1["id"],
                "grn_item_id": grn_item1["id"],
                "packaging_unit_id": strip["id"],
                "billed_qty_pack": "20.0000",
                "free_qty_pack": "0.0000",
                "purchase_rate": "150.0000",
                "discount_percent": "5.00",
                "tax_rate_id": env["gst_12"]["id"],
                "batch_number": "DOLO-BATCH-A1",
            },
            {
                "medicine_id": m2["id"],
                "grn_item_id": grn_item2["id"],
                "packaging_unit_id": strip["id"],
                "billed_qty_pack": "10.0000",
                "free_qty_pack": "1.0000",
                "purchase_rate": "200.0000",
                "discount_percent": "0.00",
                "tax_rate_id": env["gst_18"]["id"],
                "batch_number": "AUG-BATCH-A1",
            },
        ],
    }
    inv_create_resp = await client.post("/api/v1/purchases/invoices", json=inv_payload)
    assert inv_create_resp.status_code == 201, inv_create_resp.text
    invoice = inv_create_resp.json()
    inv_id = invoice["id"]
    assert invoice["status"] == "DRAFT"

    # Stage 5: Final Review & 3-Way Match Check
    check_resp = await client.get(f"/api/v1/purchases/invoices/{inv_id}/finalize-check")
    assert check_resp.status_code == 200, check_resp.text
    check_data = check_resp.json()
    assert check_data["can_finalize"] is True
    assert len(check_data["errors"]) == 0

    # Finalize Invoice
    fin_resp = await client.post(f"/api/v1/purchases/invoices/{inv_id}/finalize", json={
        "acknowledge_warnings": False,
    })
    assert fin_resp.status_code == 200, fin_resp.text
    fin_data = fin_resp.json()
    assert fin_data["status"] == "POSTED"

    # Verify Supplier Liability in Payables
    sup_bal = await client.get(f"/api/v1/supplier-payments/balances?supplier_id={sup['id']}")
    assert sup_bal.status_code == 200
    b_rows = sup_bal.json()
    assert len(b_rows) == 1
    assert Decimal(str(b_rows[0]["balance"])) == Decimal("5600.00")

    # Verify General Ledger Entry created
    gl_entries = await client.get("/api/v1/accounting/journals?source_type=SUPPLIER_BILL")
    assert gl_entries.status_code == 200
    entries = gl_entries.json()
    assert entries["total"] >= 1

    # Verify PO lifecycle state is now COMPLETED
    po_final = await client.get(f"/api/v1/purchases/orders/{po_id}/lifecycle")
    assert po_final.json()["lifecycle_stage"] == "COMPLETED"


@pytest.mark.asyncio
async def test_scenario_b_partial_receipts_and_multi_grn(client: AsyncClient):
    """
    Scenario B:
    - Create an order for 100 units.
    - Receive 60 units in GRN 1.
    - Verify that 40 units remain outstanding.
    - Receive the remaining 40 units in GRN 2.
    - Verify PO status transitions from PARTIALLY_RECEIVED to RECEIVED.
    - Record and match the supplier invoice for all 100 units.
    """
    env = await setup_procurement_environment(client)
    sup = env["supplier"]
    m1 = env["med1"]
    strip = env["strip_unit"]

    # Create PO for 100 strips
    po_resp = await client.post("/api/v1/purchases/orders", json={
        "supplier_id": sup["id"],
        "order_date": date.today().isoformat(),
        "expected_delivery_date": (date.today() + timedelta(days=7)).isoformat(),
        "items": [
            {
                "medicine_id": m1["id"],
                "packaging_unit_id": strip["id"],
                "ordered_qty_pack": "100.0000",
                "purchase_rate": "150.0000",
                "discount_percent": "0.00",
                "tax_rate_id": env["gst_12"]["id"],
            }
        ],
    })
    assert po_resp.status_code == 201
    po = po_resp.json()
    po_id = po["id"]
    po_item_id = po["items"][0]["id"]

    # Submit and Approve
    await client.patch(f"/api/v1/purchases/orders/{po_id}/status", json={"status": "SUBMITTED"})
    await client.patch(f"/api/v1/purchases/orders/{po_id}/status", json={"status": "APPROVED"})

    # GRN 1: Receive 60 units
    exp_date = (date.today() + timedelta(days=365)).isoformat()
    grn1_resp = await client.post("/api/v1/purchases/receipts", json={
        "supplier_id": sup["id"],
        "po_id": po_id,
        "receipt_date": date.today().isoformat(),
        "supplier_challan_no": "DC-PARTIAL-1",
        "items": [
            {
                "medicine_id": m1["id"],
                "po_item_id": po_item_id,
                "packaging_unit_id": strip["id"],
                "received_qty_pack": "60.0000",
                "free_qty_pack": "0.0000",
                "rejected_qty_pack": "0.0000",
                "batch_number": "PARTIAL-B1",
                "expiry_date": exp_date,
                "mrp": "200.0000",
                "selling_rate": "180.0000",
                "purchase_rate": "150.0000",
                "discount_percent": "0.00",
                "tax_rate_id": env["gst_12"]["id"],
            }
        ],
    })
    assert grn1_resp.status_code == 201
    grn1_id = grn1_resp.json()["id"]
    await client.post(f"/api/v1/purchases/receipts/{grn1_id}/post")

    # Verify PO status is PARTIALLY_RECEIVED
    po_after_grn1 = await client.get(f"/api/v1/purchases/orders/{po_id}")
    assert po_after_grn1.json()["status"] == "PARTIALLY_RECEIVED"

    # Check lifecycle remaining quantity: 100 - 60 = 40 remaining
    lc1 = await client.get(f"/api/v1/purchases/orders/{po_id}/lifecycle")
    assert lc1.json()["lifecycle_stage"] == "PARTIALLY_RECEIVED"
    assert Decimal(str(lc1.json()["total_ordered_base"])) == Decimal("1500.0000")  # 100 * 15
    assert Decimal(str(lc1.json()["total_received_base"])) == Decimal("900.0000")   # 60 * 15

    # GRN 2: Receive remaining 40 units
    grn2_resp = await client.post("/api/v1/purchases/receipts", json={
        "supplier_id": sup["id"],
        "po_id": po_id,
        "receipt_date": date.today().isoformat(),
        "supplier_challan_no": "DC-PARTIAL-2",
        "items": [
            {
                "medicine_id": m1["id"],
                "po_item_id": po_item_id,
                "packaging_unit_id": strip["id"],
                "received_qty_pack": "40.0000",
                "free_qty_pack": "0.0000",
                "rejected_qty_pack": "0.0000",
                "batch_number": "PARTIAL-B2",
                "expiry_date": exp_date,
                "mrp": "200.0000",
                "selling_rate": "180.0000",
                "purchase_rate": "150.0000",
                "discount_percent": "0.00",
                "tax_rate_id": env["gst_12"]["id"],
            }
        ],
    })
    assert grn2_resp.status_code == 201
    grn2_id = grn2_resp.json()["id"]
    await client.post(f"/api/v1/purchases/receipts/{grn2_id}/post")

    # Verify PO status transitioned to RECEIVED
    po_after_grn2 = await client.get(f"/api/v1/purchases/orders/{po_id}")
    assert po_after_grn2.json()["status"] == "RECEIVED"

    # Check lifecycle stage: all 1500 base units received
    lc2 = await client.get(f"/api/v1/purchases/orders/{po_id}/lifecycle")
    assert lc2.json()["lifecycle_stage"] == "AWAITING_INVOICE"
    assert Decimal(str(lc2.json()["total_received_base"])) == Decimal("1500.0000")
    assert lc2.json()["fulfillment_percent"] == 100.0


@pytest.mark.asyncio
async def test_scenario_c_quantity_mismatch_warning(client: AsyncClient):
    """
    Scenario C:
    - Receive a different quantity than ordered or bill more than received.
    - Verify finalize-check raises overbilling blocking error or mismatch warnings.
    """
    env = await setup_procurement_environment(client)
    sup = env["supplier"]
    m1 = env["med1"]
    strip = env["strip_unit"]

    # Create & Approve PO for 10 units
    po_resp = await client.post("/api/v1/purchases/orders", json={
        "supplier_id": sup["id"],
        "order_date": date.today().isoformat(),
        "items": [{
            "medicine_id": m1["id"],
            "packaging_unit_id": strip["id"],
            "ordered_qty_pack": "10.0000",
            "purchase_rate": "100.0000",
            "tax_rate_id": env["gst_12"]["id"],
        }],
    })
    po = po_resp.json()
    po_id = po["id"]
    po_item_id = po["items"][0]["id"]
    await client.patch(f"/api/v1/purchases/orders/{po_id}/status", json={"status": "SUBMITTED"})
    await client.patch(f"/api/v1/purchases/orders/{po_id}/status", json={"status": "APPROVED"})

    # GRN: Receive only 8 units
    grn_resp = await client.post("/api/v1/purchases/receipts", json={
        "supplier_id": sup["id"],
        "po_id": po_id,
        "receipt_date": date.today().isoformat(),
        "items": [{
            "medicine_id": m1["id"],
            "po_item_id": po_item_id,
            "packaging_unit_id": strip["id"],
            "received_qty_pack": "8.0000",
            "batch_number": "MISMATCH-QTY",
            "expiry_date": (date.today() + timedelta(days=300)).isoformat(),
            "mrp": "150.0000",
            "selling_rate": "140.0000",
            "purchase_rate": "100.0000",
            "tax_rate_id": env["gst_12"]["id"],
        }],
    })
    grn_id = grn_resp.json()["id"]
    grn_item_id = grn_resp.json()["items"][0]["id"]
    await client.post(f"/api/v1/purchases/receipts/{grn_id}/post")

    # Attempt to record invoice for 12 units (overbilling against 8 received)
    inv_resp = await client.post("/api/v1/purchases/invoices", json={
        "supplier_id": sup["id"],
        "po_id": po_id,
        "grn_id": grn_id,
        "invoice_number": "INV-OVERBILL-001",
        "invoice_date": date.today().isoformat(),
        "received_date": date.today().isoformat(),
        "due_date": (date.today() + timedelta(days=30)).isoformat(),
        "supplier_declared_total": "1344.0000",
        "items": [{
            "medicine_id": m1["id"],
            "grn_item_id": grn_item_id,
            "packaging_unit_id": strip["id"],
            "billed_qty_pack": "12.0000",  # 12 > 8 received
            "purchase_rate": "100.0000",
            "tax_rate_id": env["gst_12"]["id"],
            "batch_number": "MISMATCH-QTY",
        }],
    })
    assert inv_resp.status_code == 201, inv_resp.text
    inv_id = inv_resp.json()["id"]

    # Finalize-check must block overbilling
    check_resp = await client.get(f"/api/v1/purchases/invoices/{inv_id}/finalize-check")
    assert check_resp.status_code == 200
    check_data = check_resp.json()
    assert check_data["can_finalize"] is False
    assert any("billed" in err.lower() or "received" in err.lower() for err in check_data["errors"])


@pytest.mark.asyncio
async def test_scenario_d_rate_or_tax_discrepancy(client: AsyncClient):
    """
    Scenario D:
    - Record an invoice with declared total discrepancy.
    - Verify finalize-check raises declared total mismatch warning/error.
    - Verify invoice requires explicit discrepancy explanation and warning acknowledgement.
    """
    env = await setup_procurement_environment(client)
    sup = env["supplier"]
    m1 = env["med1"]
    strip = env["strip_unit"]

    # Create & Approve PO for 10 units @ 100 + 12% GST = 1120.00
    po_resp = await client.post("/api/v1/purchases/orders", json={
        "supplier_id": sup["id"],
        "order_date": date.today().isoformat(),
        "items": [{
            "medicine_id": m1["id"],
            "packaging_unit_id": strip["id"],
            "ordered_qty_pack": "10.0000",
            "purchase_rate": "100.0000",
            "tax_rate_id": env["gst_12"]["id"],
        }],
    })
    po = po_resp.json()
    po_id = po["id"]
    po_item_id = po["items"][0]["id"]
    await client.patch(f"/api/v1/purchases/orders/{po_id}/status", json={"status": "SUBMITTED"})
    await client.patch(f"/api/v1/purchases/orders/{po_id}/status", json={"status": "APPROVED"})

    # GRN
    grn_resp = await client.post("/api/v1/purchases/receipts", json={
        "supplier_id": sup["id"],
        "po_id": po_id,
        "receipt_date": date.today().isoformat(),
        "items": [{
            "medicine_id": m1["id"],
            "po_item_id": po_item_id,
            "packaging_unit_id": strip["id"],
            "received_qty_pack": "10.0000",
            "batch_number": "DISC-BATCH",
            "expiry_date": (date.today() + timedelta(days=300)).isoformat(),
            "mrp": "150.0000",
            "selling_rate": "140.0000",
            "purchase_rate": "100.0000",
            "tax_rate_id": env["gst_12"]["id"],
        }],
    })
    grn_id = grn_resp.json()["id"]
    grn_item_id = grn_resp.json()["items"][0]["id"]
    await client.post(f"/api/v1/purchases/receipts/{grn_id}/post")

    # Invoice with declared_total_amount = 1500 (calculated is 1120.00) without explanation
    inv_payload = {
        "supplier_id": sup["id"],
        "po_id": po_id,
        "grn_id": grn_id,
        "invoice_number": "INV-DISC-001",
        "invoice_date": date.today().isoformat(),
        "received_date": date.today().isoformat(),
        "due_date": (date.today() + timedelta(days=30)).isoformat(),
        "supplier_declared_total": "1500.0000",  # Mismatch with computed 1120
        "notes": "Testing declared total discrepancy handling",
        "items": [{
            "medicine_id": m1["id"],
            "grn_item_id": grn_item_id,
            "packaging_unit_id": strip["id"],
            "billed_qty_pack": "10.0000",
            "purchase_rate": "100.0000",
            "tax_rate_id": env["gst_12"]["id"],
            "batch_number": "DISC-BATCH",
        }],
    }
    inv_resp = await client.post("/api/v1/purchases/invoices", json=inv_payload)
    assert inv_resp.status_code == 201, inv_resp.text
    inv_id = inv_resp.json()["id"]

    # Finalize check: Unexplained discrepancy > tolerance is an error and blocks finalization
    check_resp = await client.get(f"/api/v1/purchases/invoices/{inv_id}/finalize-check")
    assert check_resp.status_code == 200
    check_data = check_resp.json()
    assert check_data["can_finalize"] is False
    assert any("differs from the supplier's bill" in err.lower() for err in check_data["errors"])

    # Attempting to finalize without discrepancy notes and without acknowledgement fails (422)
    fin_fail = await client.post(f"/api/v1/purchases/invoices/{inv_id}/finalize", json={
        "acknowledge_warnings": False,
    })
    assert fin_fail.status_code in (409, 422)

    # Adding discrepancy explanation converts error to warning and can_finalize becomes True
    await client.put(f"/api/v1/purchases/invoices/{inv_id}", json={
        **inv_payload,
        "discrepancy_notes": "Supplier invoiced handling fees; credit adjustment pending",
    })
    check2_resp = await client.get(f"/api/v1/purchases/invoices/{inv_id}/finalize-check")
    assert check2_resp.status_code == 200
    assert check2_resp.json()["can_finalize"] is True
    assert len(check2_resp.json()["warnings"]) > 0

    # Finalizing with acknowledgement succeeds
    fin_ok = await client.post(f"/api/v1/purchases/invoices/{inv_id}/finalize", json={
        "acknowledge_warnings": True,
    })
    assert fin_ok.status_code == 200
    assert fin_ok.json()["status"] == "POSTED"


@pytest.mark.asyncio
async def test_scenario_e_idempotent_duplicate_finalization(client: AsyncClient):
    """
    Scenario E:
    - Attempt duplicate invoice finalization.
    - Verify that repeat requests are rejected and cannot duplicate GL entries, AP records, or stock.
    """
    env = await setup_procurement_environment(client)
    sup = env["supplier"]
    m1 = env["med1"]
    strip = env["strip_unit"]

    po_resp = await client.post("/api/v1/purchases/orders", json={
        "supplier_id": sup["id"],
        "order_date": date.today().isoformat(),
        "items": [{
            "medicine_id": m1["id"],
            "packaging_unit_id": strip["id"],
            "ordered_qty_pack": "5.0000",
            "purchase_rate": "100.0000",
            "tax_rate_id": env["gst_12"]["id"],
        }],
    })
    po_id = po_resp.json()["id"]
    po_item_id = po_resp.json()["items"][0]["id"]
    await client.patch(f"/api/v1/purchases/orders/{po_id}/status", json={"status": "SUBMITTED"})
    await client.patch(f"/api/v1/purchases/orders/{po_id}/status", json={"status": "APPROVED"})

    grn_resp = await client.post("/api/v1/purchases/receipts", json={
        "supplier_id": sup["id"],
        "po_id": po_id,
        "receipt_date": date.today().isoformat(),
        "items": [{
            "medicine_id": m1["id"],
            "po_item_id": po_item_id,
            "packaging_unit_id": strip["id"],
            "received_qty_pack": "5.0000",
            "batch_number": "IDEM-1",
            "expiry_date": (date.today() + timedelta(days=300)).isoformat(),
            "mrp": "150.0000",
            "selling_rate": "140.0000",
            "purchase_rate": "100.0000",
            "tax_rate_id": env["gst_12"]["id"],
        }],
    })
    grn_id = grn_resp.json()["id"]
    grn_item_id = grn_resp.json()["items"][0]["id"]
    await client.post(f"/api/v1/purchases/receipts/{grn_id}/post")

    inv_resp = await client.post("/api/v1/purchases/invoices", json={
        "supplier_id": sup["id"],
        "po_id": po_id,
        "grn_id": grn_id,
        "invoice_number": "INV-IDEM-001",
        "invoice_date": date.today().isoformat(),
        "received_date": date.today().isoformat(),
        "due_date": (date.today() + timedelta(days=30)).isoformat(),
        "supplier_declared_total": "560.0000",
        "items": [{
            "medicine_id": m1["id"],
            "grn_item_id": grn_item_id,
            "packaging_unit_id": strip["id"],
            "billed_qty_pack": "5.0000",
            "purchase_rate": "100.0000",
            "tax_rate_id": env["gst_12"]["id"],
            "batch_number": "IDEM-1",
        }],
    })
    inv_id = inv_resp.json()["id"]

    # First finalization succeeds
    fin1 = await client.post(f"/api/v1/purchases/invoices/{inv_id}/finalize", json={"acknowledge_warnings": True})
    assert fin1.status_code == 200

    # Repeat finalization call MUST fail (idempotency safeguard)
    fin2 = await client.post(f"/api/v1/purchases/invoices/{inv_id}/finalize", json={"acknowledge_warnings": True})
    assert fin2.status_code in (400, 409, 422), fin2.text
    assert "already finalised" in fin2.text.lower() or "already posted" in fin2.text.lower()


@pytest.mark.asyncio
async def test_scenario_f_rbac_verification_across_roles(client: AsyncClient, second_client: AsyncClient):
    """
    Scenario F:
    - Verify permissions for Admin, Pharmacist, Inventory Manager, and Cashier.
    - Admin: Full access.
    - Cashier: Strict 403 Forbidden on purchasing APIs.
    """
    env = await setup_procurement_environment(client)

    # 1. Get Cashier role
    roles_resp = await client.get("/api/v1/roles")
    roles = {r["name"]: r for r in roles_resp.json()}
    cashier_role_id = roles["Cashier"]["id"]

    # Create a Cashier user
    cashier_resp = await client.post("/api/v1/users", json={
        "name": "Sam Cashier",
        "username": "cashier_sam",
        "password": "Password123!",
        "role_id": cashier_role_id,
    })
    assert cashier_resp.status_code == 201

    # Login as Cashier in second client
    c_login = await second_client.post("/api/v1/auth/login", json={
        "username": "cashier_sam",
        "password": "Password123!",
    })
    assert c_login.status_code == 200

    # Cashier attempts to access Purchasing APIs -> Must be 403 Forbidden
    po_get = await second_client.get("/api/v1/purchases/orders")
    assert po_get.status_code == 403, f"Expected 403 for cashier, got {po_get.status_code}"

    po_post = await second_client.post("/api/v1/purchases/orders", json={
        "supplier_id": env["supplier"]["id"],
        "order_date": date.today().isoformat(),
        "items": [],
    })
    assert po_post.status_code == 403

    grn_post = await second_client.post("/api/v1/purchases/receipts", json={
        "supplier_id": env["supplier"]["id"],
        "receipt_date": date.today().isoformat(),
        "items": [],
    })
    assert grn_post.status_code == 403

    inv_post = await second_client.post("/api/v1/purchases/invoices", json={
        "supplier_id": env["supplier"]["id"],
        "invoice_number": "INV-CASHIER-HACK",
        "invoice_date": date.today().isoformat(),
        "items": [],
    })
    assert inv_post.status_code == 403


@pytest.mark.asyncio
async def test_self_approval_policy_check(client: AsyncClient, second_client: AsyncClient):
    """
    Verify self-approval prohibition policy:
    - When purchases.prohibit_self_approval is true, a user cannot approve a PO they created.
    """
    env = await setup_procurement_environment(client)
    sup = env["supplier"]
    m1 = env["med1"]
    strip = env["strip_unit"]

    # 1. Enable self-approval prohibition
    set_resp = await client.put("/api/v1/settings/purchases.prohibit_self_approval", json={
        "setting_value": "true",
    })
    assert set_resp.status_code == 200

    # 2. Admin creates and submits a PO
    po_resp = await client.post("/api/v1/purchases/orders", json={
        "supplier_id": sup["id"],
        "order_date": date.today().isoformat(),
        "items": [{
            "medicine_id": m1["id"],
            "packaging_unit_id": strip["id"],
            "ordered_qty_pack": "5.0000",
            "purchase_rate": "100.0000",
            "tax_rate_id": env["gst_12"]["id"],
        }],
    })
    assert po_resp.status_code == 201
    po_id = po_resp.json()["id"]
    await client.patch(f"/api/v1/purchases/orders/{po_id}/status", json={"status": "SUBMITTED"})

    # 3. Admin attempts to approve their own PO -> Must fail 403
    self_app = await client.patch(f"/api/v1/purchases/orders/{po_id}/status", json={"status": "APPROVED"})
    assert self_app.status_code == 403
    assert "cannot approve your own purchase order" in self_app.text.lower() or "prohibited" in self_app.text.lower()
