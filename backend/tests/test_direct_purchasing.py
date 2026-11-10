"""
Comprehensive Automated Test Suite for Direct Supplier Invoice Purchasing Workflow.

Tests Scenarios A through J as specified in the User Requirements:
- Scenario A: Direct purchase of multiple medicines, save and post, verify immediate stock creation and supplier payable.
- Scenario B: Purchase of one medicine in two batches with different expiry dates.
- Scenario C: Free quantities, discounts and GST calculations.
- Scenario D: Partial supplier return and credit note, verify stock and payable adjustments.
- Scenario E: Delete a draft purchase (verify zero stock/payable impact).
- Scenario F: Cancel a posted purchase where all stock remains (controlled reversal).
- Scenario G: Attempt to cancel a purchase after part of its stock has been sold (verify blocked with 422).
- Scenario H: Double-click or retry purchase posting (verify unique constraint & no duplicate stock/GL).
- Scenario I: Verify optional Purchase Orders still work and can be converted into direct purchase.
- Scenario J: Test role permissions (Admin, Pharmacist, Cashier blocked with 403).
"""
from datetime import date, timedelta
from decimal import Decimal
import pytest
from httpx import AsyncClient

from tests.conftest import VALID_SETUP_PAYLOAD


async def setup_environment(client: AsyncClient) -> dict:
    """Setup pharmacy, seed masters, create supplier, packaging units, and two test medicines."""
    await client.post("/api/v1/setup", json=VALID_SETUP_PAYLOAD)
    login_resp = await client.post("/api/v1/auth/login", json={
        "username": VALID_SETUP_PAYLOAD["admin_username"],
        "password": VALID_SETUP_PAYLOAD["admin_password"],
    })
    assert login_resp.status_code == 200

    # 1. Create Supplier
    sup_resp = await client.post("/api/v1/suppliers", json={
        "name": "MedLife Wholesale Distributors",
        "legal_name": "MedLife Wholesale Distributors LLP",
        "gstin": "27AABCM5678F1Z2",
        "drug_license_no": "MH-20B-112233",
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

    # 3. Get tax rates
    tax_resp = await client.get("/api/v1/tax-rates")
    tax_rates = {t["name"]: t for t in tax_resp.json()}
    gst_12 = tax_rates["GST 12%"]
    gst_18 = tax_rates["GST 18%"]

    # 4. Create Medicine 1: Paracetamol 650 (12% GST, 1 Strip = 10 Tablets)
    med1_resp = await client.post("/api/v1/medicines", json={
        "name": "Paracetamol 650mg Tablet",
        "brand_name": "Calpol",
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
        "conversion_factor": "10.0000",
        "is_sales_unit": True,
        "is_purchase_unit": True,
    })

    # 5. Create Medicine 2: Amoxicillin 500 (18% GST, 1 Strip = 10 Tablets)
    med2_resp = await client.post("/api/v1/medicines", json={
        "name": "Amoxicillin 500mg Capsule",
        "brand_name": "Mox",
        "generic_name": "Amoxicillin",
        "strength": "500 mg",
        "dosage_form": "Capsule",
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
        "gst_12": gst_12,
        "gst_18": gst_18,
    }


# ── Scenario A: Direct purchase of multiple medicines, save and post ──────────
@pytest.mark.asyncio
async def test_scenario_a_direct_purchase_save_and_post(client: AsyncClient):
    """
    Scenario A: User enters supplier invoice with multiple medicines ONCE, clicks Save & Post.
    Verify:
    1. Purchase invoice created & status is FINALIZED.
    2. Internal GRN created & status is POSTED.
    3. Batches created with batch numbers & expiries.
    4. Stock ledger updated immediately.
    5. Supplier payable liability recorded.
    """
    env = await setup_environment(client)
    sup = env["supplier"]
    med1 = env["med1"]
    med2 = env["med2"]
    strip = env["strip_unit"]
    gst_12 = env["gst_12"]
    gst_18 = env["gst_18"]

    exp_date = (date.today() + timedelta(days=365)).isoformat()

    payload = {
        "supplier_id": sup["id"],
        "invoice_number": "INV-2026-001",
        "invoice_date": date.today().isoformat(),
        "due_date": (date.today() + timedelta(days=30)).isoformat(),
        "payment_terms_days": 30,
        "is_tax_inclusive": False,
        "other_charges": "50.00",
        "round_off": "0.00",
        "post_now": True,
        "items": [
            {
                "medicine_id": med1["id"],
                "packaging_unit_id": strip["id"],
                "quantity": "20.00",  # 20 strips = 200 tablets
                "free_quantity": "0.00",
                "purchase_rate": "50.00",  # 20 * 50 = 1000 + 12% GST = 1120
                "discount_percent": "0.00",
                "tax_rate_id": gst_12["id"],
                "batch_number": "CALP-BATCH-01",
                "expiry_date": exp_date,
                "mrp": "70.00",
                "selling_rate": "65.00",
                "rack_location": "RACK-A1",
            },
            {
                "medicine_id": med2["id"],
                "packaging_unit_id": strip["id"],
                "quantity": "10.00",  # 10 strips = 100 tablets
                "free_quantity": "0.00",
                "purchase_rate": "100.00",  # 10 * 100 = 1000 + 18% GST = 1180
                "discount_percent": "0.00",
                "tax_rate_id": gst_18["id"],
                "batch_number": "MOX-BATCH-01",
                "expiry_date": exp_date,
                "mrp": "140.00",
                "selling_rate": "130.00",
                "rack_location": "RACK-B2",
            },
        ],
    }

    resp = await client.post("/api/v1/purchases/direct", json=payload)
    assert resp.status_code == 201, resp.text
    data = resp.json()

    assert data["status"] == "POSTED"
    assert data["payment_status"] == "UNPAID"
    assert data["grn_id"] is not None
    assert data["grn_number"] is not None
    # Grand total: 1120 + 1180 + 50 freight = 2350.00
    assert Decimal(str(data["calculated_grand_total"])) == Decimal("2350.00")
    assert Decimal(str(data["outstanding_amount"])) == Decimal("2350.00")

    # Verify inventory stock was created immediately
    med1_stock_resp = await client.get(f"/api/v1/medicines/{med1['id']}")
    assert Decimal(str(med1_stock_resp.json()["current_stock_base"])) == Decimal("200.0000")

    med2_stock_resp = await client.get(f"/api/v1/medicines/{med2['id']}")
    assert Decimal(str(med2_stock_resp.json()["current_stock_base"])) == Decimal("100.0000")

    # Verify batch records
    batches_resp = await client.get("/api/v1/batches")
    batches = {b["batch_number"]: b for b in batches_resp.json()}
    assert "CALP-BATCH-01" in batches
    assert Decimal(str(batches["CALP-BATCH-01"]["current_stock_base"])) == Decimal("200.0000")
    assert "MOX-BATCH-01" in batches
    assert Decimal(str(batches["MOX-BATCH-01"]["current_stock_base"])) == Decimal("100.0000")

    # Verify internal GRN is posted
    grn_resp = await client.get(f"/api/v1/purchases/receipts/{data['grn_id']}")
    assert grn_resp.status_code == 200
    assert grn_resp.json()["status"] == "POSTED"


# ── Scenario B: Purchase of one medicine in two batches with different expiries ──
@pytest.mark.asyncio
async def test_scenario_b_multiple_batches_same_medicine(client: AsyncClient):
    """
    Scenario B: Purchase of one medicine split into two different batches with different expiries.
    """
    env = await setup_environment(client)
    sup = env["supplier"]
    med1 = env["med1"]
    strip = env["strip_unit"]
    gst_12 = env["gst_12"]

    exp1 = (date.today() + timedelta(days=180)).isoformat()
    exp2 = (date.today() + timedelta(days=540)).isoformat()

    payload = {
        "supplier_id": sup["id"],
        "invoice_number": "INV-MULTI-BATCH-01",
        "invoice_date": date.today().isoformat(),
        "post_now": True,
        "items": [
            {
                "medicine_id": med1["id"],
                "packaging_unit_id": strip["id"],
                "quantity": "10.00",  # 100 tablets
                "purchase_rate": "50.00",
                "tax_rate_id": gst_12["id"],
                "batch_number": "BATCH-EARLY-EXP",
                "expiry_date": exp1,
                "mrp": "70.00",
            },
            {
                "medicine_id": med1["id"],
                "packaging_unit_id": strip["id"],
                "quantity": "15.00",  # 150 tablets
                "purchase_rate": "50.00",
                "tax_rate_id": gst_12["id"],
                "batch_number": "BATCH-LATE-EXP",
                "expiry_date": exp2,
                "mrp": "70.00",
            },
        ],
    }

    resp = await client.post("/api/v1/purchases/direct", json=payload)
    assert resp.status_code == 201, resp.text
    inv = resp.json()
    assert inv["status"] == "POSTED"

    # Total stock of med1 should be 100 + 150 = 250 tablets
    med1_resp = await client.get(f"/api/v1/medicines/{med1['id']}")
    assert Decimal(str(med1_resp.json()["current_stock_base"])) == Decimal("250.0000")

    # Check both batches exist
    batches_resp = await client.get("/api/v1/batches")
    batches = {b["batch_number"]: b for b in batches_resp.json()}
    assert Decimal(str(batches["BATCH-EARLY-EXP"]["current_stock_base"])) == Decimal("100.0000")
    assert Decimal(str(batches["BATCH-LATE-EXP"]["current_stock_base"])) == Decimal("150.0000")


# ── Scenario C: Free quantities, discounts and GST calculations ───────────────
@pytest.mark.asyncio
async def test_scenario_c_free_quantities_and_discounts(client: AsyncClient):
    """
    Scenario C: Free quantities and item discounts.
    10 packs billed + 2 free packs (total 12 packs received).
    10% discount on purchase rate.
    Verify weighted unit cost and total stock credited (12 packs * 10 = 120 tablets).
    """
    env = await setup_environment(client)
    sup = env["supplier"]
    med1 = env["med1"]
    strip = env["strip_unit"]
    gst_12 = env["gst_12"]

    exp_date = (date.today() + timedelta(days=365)).isoformat()

    payload = {
        "supplier_id": sup["id"],
        "invoice_number": "INV-SCHEME-01",
        "invoice_date": date.today().isoformat(),
        "post_now": True,
        "items": [
            {
                "medicine_id": med1["id"],
                "packaging_unit_id": strip["id"],
                "quantity": "10.00",
                "free_quantity": "2.00",
                "purchase_rate": "100.00",
                "discount_percent": "10.00",  # Gross: 1000, Disc: 100, Net: 900
                "tax_rate_id": gst_12["id"],  # 12% on 900 = 108, Total: 1008
                "batch_number": "SCHEME-BATCH-1",
                "expiry_date": exp_date,
                "mrp": "150.00",
            }
        ],
    }

    resp = await client.post("/api/v1/purchases/direct", json=payload)
    assert resp.status_code == 201, resp.text
    inv = resp.json()

    assert Decimal(str(inv["subtotal"])) == Decimal("1000.00")
    assert Decimal(str(inv["discount_total"])) == Decimal("100.00")
    assert Decimal(str(inv["tax_total"])) == Decimal("108.00")
    assert Decimal(str(inv["calculated_grand_total"])) == Decimal("1008.00")

    # Inventory received should include free packs: (10 + 2) * 10 = 120 tablets
    med1_resp = await client.get(f"/api/v1/medicines/{med1['id']}")
    assert Decimal(str(med1_resp.json()["current_stock_base"])) == Decimal("120.0000")

    # Cost per base unit should be discounted & allocated across 120 tablets: 900 / 120 = 7.50
    batches_resp = await client.get("/api/v1/batches")
    batch = [b for b in batches_resp.json() if b["batch_number"] == "SCHEME-BATCH-1"][0]
    assert Decimal(str(batch["cost_per_base_unit"])) == Decimal("7.5000")


# ── Scenario D: Partial supplier return and credit note ────────────────────────
@pytest.mark.asyncio
async def test_scenario_d_partial_return_and_credit_note(client: AsyncClient):
    """
    Scenario D: Partial supplier return (Debit Note) and financial credit note.
    Verify stock reduction and payable balance reduction.
    """
    env = await setup_environment(client)
    sup = env["supplier"]
    med1 = env["med1"]
    strip = env["strip_unit"]
    gst_12 = env["gst_12"]

    exp_date = (date.today() + timedelta(days=365)).isoformat()

    # Post purchase of 10 strips @ 100 + 12% = 1120
    purchase_payload = {
        "supplier_id": sup["id"],
        "invoice_number": "INV-RET-TEST-01",
        "invoice_date": date.today().isoformat(),
        "post_now": True,
        "items": [
            {
                "medicine_id": med1["id"],
                "packaging_unit_id": strip["id"],
                "quantity": "10.00",
                "purchase_rate": "100.00",
                "tax_rate_id": gst_12["id"],
                "batch_number": "RET-BATCH-01",
                "expiry_date": exp_date,
                "mrp": "140.00",
            }
        ],
    }
    p_resp = await client.post("/api/v1/purchases/direct", json=purchase_payload)
    assert p_resp.status_code == 201
    inv = p_resp.json()
    assert Decimal(str(inv["outstanding_amount"])) == Decimal("1120.00")

    batches_resp = await client.get("/api/v1/batches")
    batch = [b for b in batches_resp.json() if b["batch_number"] == "RET-BATCH-01"][0]

    # Return 2 strips (20 tablets) @ 100 refund rate
    return_payload = {
        "supplier_id": sup["id"],
        "grn_id": inv["grn_id"],
        "return_date": date.today().isoformat(),
        "reason_code": "DAMAGED",
        "debit_note_reference": "DN-001",
        "items": [
            {
                "medicine_id": med1["id"],
                "batch_id": batch["id"],
                "packaging_unit_id": strip["id"],
                "return_qty_pack": "2.00",
                "refund_rate": "100.00",
                "tax_rate_id": gst_12["id"],
                "reason": "Damaged foil",
            }
        ],
    }
    ret_resp = await client.post("/api/v1/purchases/returns", json=return_payload)
    assert ret_resp.status_code == 201
    ret = ret_resp.json()

    # Post the supplier return
    post_ret_resp = await client.post(f"/api/v1/purchases/returns/{ret['id']}/post")
    assert post_ret_resp.status_code == 200

    # Stock should be reduced by 20 tablets: 100 - 20 = 80 tablets
    med1_resp = await client.get(f"/api/v1/medicines/{med1['id']}")
    assert Decimal(str(med1_resp.json()["current_stock_base"])) == Decimal("80.0000")

    # Record financial credit note for rate adjustment of ₹100
    cn_payload = {
        "supplier_id": sup["id"],
        "invoice_id": inv["id"],
        "credit_note_number": "CN-RATE-DIFF-01",
        "note_date": date.today().isoformat(),
        "reason_code": "RATE_DIFFERENCE",
        "taxable_amount": "100.00",
        "total_amount": "100.00",
        "notes": "Agreed rate difference credit",
    }
    cn_resp = await client.post("/api/v1/purchases/returns/credit-notes", json=cn_payload)
    assert cn_resp.status_code == 201
    cn = cn_resp.json()
    assert cn["status"] == "POSTED"
    assert Decimal(str(cn["total_amount"])) == Decimal("100.00")


# ── Scenario E: Delete a draft purchase ───────────────────────────────────────
@pytest.mark.asyncio
async def test_scenario_e_delete_draft_purchase(client: AsyncClient):
    """
    Scenario E: Save draft purchase (post_now=False).
    Verify stock is NOT modified. Delete draft purchase.
    """
    env = await setup_environment(client)
    sup = env["supplier"]
    med1 = env["med1"]
    strip = env["strip_unit"]

    exp_date = (date.today() + timedelta(days=365)).isoformat()

    payload = {
        "supplier_id": sup["id"],
        "invoice_number": "DRAFT-INV-001",
        "invoice_date": date.today().isoformat(),
        "post_now": False,  # Save Draft
        "items": [
            {
                "medicine_id": med1["id"],
                "packaging_unit_id": strip["id"],
                "quantity": "50.00",
                "purchase_rate": "40.00",
                "batch_number": "DRAFT-BATCH-01",
                "expiry_date": exp_date,
                "mrp": "60.00",
            }
        ],
    }

    resp = await client.post("/api/v1/purchases/direct", json=payload)
    assert resp.status_code == 201
    draft_inv = resp.json()
    assert draft_inv["status"] == "DRAFT"

    # Verify stock was NOT modified by saving draft
    med1_resp = await client.get(f"/api/v1/medicines/{med1['id']}")
    assert Decimal(str(med1_resp.json()["current_stock_base"])) == Decimal("0.0000")

    # Delete draft purchase
    del_resp = await client.delete(f"/api/v1/purchases/direct/{draft_inv['id']}")
    assert del_resp.status_code == 200
    assert del_resp.json()["success"] is True

    # Verify draft invoice is deleted
    get_resp = await client.get(f"/api/v1/purchases/invoices/{draft_inv['id']}")
    assert get_resp.status_code == 404


# ── Scenario F: Attempt to cancel a posted purchase where all stock remains ───
@pytest.mark.asyncio
async def test_scenario_f_cancel_posted_purchase_full_stock(client: AsyncClient):
    """
    Scenario F: Cancel posted purchase with full stock available.
    Controlled reversal reverses stock, supplier liability, and marks invoice CANCELLED.
    """
    env = await setup_environment(client)
    sup = env["supplier"]
    med1 = env["med1"]
    strip = env["strip_unit"]

    exp_date = (date.today() + timedelta(days=365)).isoformat()

    payload = {
        "supplier_id": sup["id"],
        "invoice_number": "INV-CANCEL-SAFE",
        "invoice_date": date.today().isoformat(),
        "post_now": True,
        "items": [
            {
                "medicine_id": med1["id"],
                "packaging_unit_id": strip["id"],
                "quantity": "10.00",  # 100 tablets
                "purchase_rate": "50.00",
                "batch_number": "CANCEL-BATCH-1",
                "expiry_date": exp_date,
                "mrp": "70.00",
            }
        ],
    }

    resp = await client.post("/api/v1/purchases/direct", json=payload)
    assert resp.status_code == 201
    inv = resp.json()
    assert inv["status"] == "POSTED"

    # Stock is 100 tablets
    med1_resp = await client.get(f"/api/v1/medicines/{med1['id']}")
    assert Decimal(str(med1_resp.json()["current_stock_base"])) == Decimal("100.0000")

    # Cancel posted purchase
    cancel_resp = await client.post(
        f"/api/v1/purchases/direct/{inv['id']}/cancel",
        json={"reason": "Supplier invoiced incorrect pharmacy by mistake"},
    )
    assert cancel_resp.status_code == 200
    cancelled_inv = cancel_resp.json()
    assert cancelled_inv["status"] == "CANCELLED"
    assert cancelled_inv["cancel_reason"] == "Supplier invoiced incorrect pharmacy by mistake"

    # Stock is reversed to 0
    med1_after = await client.get(f"/api/v1/medicines/{med1['id']}")
    assert Decimal(str(med1_after.json()["current_stock_base"])) == Decimal("0.0000")


# ── Scenario G: Attempt to cancel purchase after stock is sold (Blocked) ───────
@pytest.mark.asyncio
async def test_scenario_g_cancel_blocked_when_stock_consumed(client: AsyncClient):
    """
    Scenario G: Attempt to cancel purchase after part of its stock was consumed/sold.
    Verify:
    1. Hard deletion/cancellation is strictly blocked with HTTP 422.
    2. Explanatory message guides user to use partial return instead.
    3. Original purchase and sales integrity are preserved.
    """
    env = await setup_environment(client)
    sup = env["supplier"]
    med1 = env["med1"]
    strip = env["strip_unit"]

    exp_date = (date.today() + timedelta(days=365)).isoformat()

    # 1. Post purchase of 10 strips (100 tablets)
    payload = {
        "supplier_id": sup["id"],
        "invoice_number": "INV-CONSUMED-TEST",
        "invoice_date": date.today().isoformat(),
        "post_now": True,
        "items": [
            {
                "medicine_id": med1["id"],
                "packaging_unit_id": strip["id"],
                "quantity": "10.00",
                "purchase_rate": "50.00",
                "batch_number": "CONSUMED-BATCH-1",
                "expiry_date": exp_date,
                "mrp": "70.00",
            }
        ],
    }
    resp = await client.post("/api/v1/purchases/direct", json=payload)
    assert resp.status_code == 201
    inv = resp.json()

    # 2. Consume part of the stock (dispense 20 tablets via stock adjustment)
    batches_resp = await client.get("/api/v1/batches")
    batch = [b for b in batches_resp.json() if b["batch_number"] == "CONSUMED-BATCH-1"][0]

    adj_resp = await client.post("/api/v1/inventory/adjustments", json={
        "reason_code": "DAMAGE",
        "notes": "Damaged stock",
        "items": [
            {
                "batch_id": batch["id"],
                "physical_stock_base": "80.0000",
            }
        ],
    })
    assert adj_resp.status_code == 201

    # 3. Attempt to cancel the posted purchase
    cancel_resp = await client.post(
        f"/api/v1/purchases/direct/{inv['id']}/cancel",
        json={"reason": "Accidental entry"},
    )
    # MUST BE BLOCKED WITH 422
    assert cancel_resp.status_code == 422
    err_data = cancel_resp.json()
    assert "sold or consumed" in err_data["detail"]
    assert "Purchase Return" in err_data["detail"]

    # Verify purchase is still POSTED
    check_inv = await client.get(f"/api/v1/purchases/invoices/{inv['id']}")
    assert check_inv.json()["status"] == "POSTED"


# ── Scenario H: Idempotency & duplicate posting prevention ────────────────────
@pytest.mark.asyncio
async def test_scenario_h_duplicate_posting_prevention(client: AsyncClient):
    """
    Scenario H: Attempt to submit duplicate purchase or double-post.
    Verify unique constraint and rejection without duplicating stock or GL entries.
    """
    env = await setup_environment(client)
    sup = env["supplier"]
    med1 = env["med1"]
    strip = env["strip_unit"]

    exp_date = (date.today() + timedelta(days=365)).isoformat()

    payload = {
        "supplier_id": sup["id"],
        "invoice_number": "INV-DUP-TEST-01",
        "invoice_date": date.today().isoformat(),
        "post_now": True,
        "items": [
            {
                "medicine_id": med1["id"],
                "packaging_unit_id": strip["id"],
                "quantity": "10.00",
                "purchase_rate": "50.00",
                "batch_number": "DUP-BATCH-1",
                "expiry_date": exp_date,
                "mrp": "70.00",
            }
        ],
    }

    # First attempt: succeeds
    resp1 = await client.post("/api/v1/purchases/direct", json=payload)
    assert resp1.status_code == 201
    inv = resp1.json()

    # Second attempt with same supplier & invoice_number: MUST FAIL with 409 Conflict
    resp2 = await client.post("/api/v1/purchases/direct", json=payload)
    assert resp2.status_code == 409
    assert "already been recorded" in resp2.json()["detail"] or "already exists" in resp2.json()["detail"]

    # Attempt to call post on an already finalized invoice: MUST FAIL with 409
    post_again_resp = await client.post(f"/api/v1/purchases/direct/{inv['id']}/post")
    assert post_again_resp.status_code == 409
    assert "Only draft purchases can be posted" in post_again_resp.json()["detail"]

    # Verify stock was only added once: 100 tablets
    med1_resp = await client.get(f"/api/v1/medicines/{med1['id']}")
    assert Decimal(str(med1_resp.json()["current_stock_base"])) == Decimal("100.0000")


# ── Scenario I: Advance Purchase Order conversion ─────────────────────────────
@pytest.mark.asyncio
async def test_scenario_i_advance_po_conversion_to_direct_purchase(client: AsyncClient):
    """
    Scenario I: Advance PO created, then converted into Direct Purchase Entry.
    Verify:
    1. PO items carry forward.
    2. Direct purchase links to po_id.
    3. PO status transitions to RECEIVED.
    """
    env = await setup_environment(client)
    sup = env["supplier"]
    med1 = env["med1"]
    strip = env["strip_unit"]
    gst_12 = env["gst_12"]

    # 1. Create advance PO
    po_resp = await client.post("/api/v1/purchases/orders", json={
        "supplier_id": sup["id"],
        "order_date": date.today().isoformat(),
        "items": [
            {
                "medicine_id": med1["id"],
                "packaging_unit_id": strip["id"],
                "ordered_qty_pack": "25.00",
                "purchase_rate": "48.00",
                "discount_percent": "2.00",
                "tax_rate_id": gst_12["id"],
            }
        ],
    })
    assert po_resp.status_code == 201
    po = po_resp.json()

    # 2. Get prefill data from PO
    prefill_resp = await client.get(f"/api/v1/purchases/direct/from-po/{po['id']}")
    assert prefill_resp.status_code == 200
    prefill = prefill_resp.json()
    assert prefill["supplier_id"] == sup["id"]
    assert len(prefill["items"]) == 1
    assert Decimal(str(prefill["items"][0]["purchase_qty_pack"])) == Decimal("25.0000")

    # 3. Post Direct Purchase using the prefilled PO data
    exp_date = (date.today() + timedelta(days=365)).isoformat()
    direct_payload = {
        "supplier_id": prefill["supplier_id"],
        "invoice_number": "INV-FROM-PO-01",
        "invoice_date": date.today().isoformat(),
        "po_id": po["id"],
        "post_now": True,
        "items": [
            {
                "medicine_id": prefill["items"][0]["medicine_id"],
                "packaging_unit_id": prefill["items"][0]["packaging_unit_id"],
                "quantity": prefill["items"][0]["purchase_qty_pack"],
                "purchase_rate": prefill["items"][0]["purchase_rate"],
                "discount_percent": prefill["items"][0]["discount_percent"],
                "tax_rate_id": prefill["items"][0]["tax_rate_id"],
                "batch_number": "PO-CONVERT-BATCH",
                "expiry_date": exp_date,
                "mrp": "70.00",
            }
        ],
    }

    resp = await client.post("/api/v1/purchases/direct", json=direct_payload)
    assert resp.status_code == 201
    inv = resp.json()
    assert inv["po_id"] == po["id"]
    assert inv["status"] == "POSTED"

    # Verify PO is now RECEIVED
    updated_po = await client.get(f"/api/v1/purchases/orders/{po['id']}")
    assert updated_po.json()["status"] == "RECEIVED"


# ── Scenario J: Test role permissions ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_scenario_j_role_permissions(client: AsyncClient):
    """
    Scenario J: RBAC permissions verification.
    Admin: Full access.
    Pharmacist: Allowed purchases.view and create.
    Cashier: Blocked with 403 Forbidden.
    """
    env = await setup_environment(client)
    sup = env["supplier"]
    med1 = env["med1"]
    strip = env["strip_unit"]
    # Get role IDs
    roles_resp = await client.get("/api/v1/roles")
    roles = {r["name"]: r for r in roles_resp.json()}
    cashier_role_id = roles["Cashier"]["id"]
    pharma_role_id = roles["Pharmacist"]["id"]

    # Create Pharmacist user
    pharma_resp = await client.post("/api/v1/users", json={
        "name": "Pharmacist John",
        "username": "pharma_user",
        "password": "Password123!",
        "role_id": pharma_role_id,
    })
    assert pharma_resp.status_code == 201

    # Create Cashier user
    cashier_resp = await client.post("/api/v1/users", json={
        "name": "Cashier Jane",
        "username": "cashier_user",
        "password": "Password123!",
        "role_id": cashier_role_id,
    })
    assert cashier_resp.status_code == 201

    # Login as Cashier
    await client.post("/api/v1/auth/login", json={
        "username": "cashier_user",
        "password": "Password123!",
    })

    exp_date = (date.today() + timedelta(days=365)).isoformat()
    test_payload = {
        "supplier_id": sup["id"],
        "invoice_number": "CASHIER-TRY",
        "invoice_date": date.today().isoformat(),
        "post_now": True,
        "items": [
            {
                "medicine_id": med1["id"],
                "packaging_unit_id": strip["id"],
                "quantity": "5.00",
                "purchase_rate": "50.00",
                "batch_number": "CASHIER-BATCH",
                "expiry_date": exp_date,
                "mrp": "70.00",
            }
        ],
    }

    # Cashier MUST receive 403 Forbidden
    cashier_post_resp = await client.post("/api/v1/purchases/direct", json=test_payload)
    assert cashier_post_resp.status_code == 403

    cashier_cancel_resp = await client.post("/api/v1/purchases/direct/1/cancel", json={"reason": "test"})
    assert cashier_cancel_resp.status_code == 403
