"""
Integration tests for Batches, Stock Ledger, Opening Stock, Adjustments, and Stock Alerts.
"""
from datetime import date, timedelta
from decimal import Decimal
import pytest
from httpx import AsyncClient

from tests.conftest import setup_pharmacy, login


@pytest.mark.asyncio
async def test_batch_uniqueness_and_validation(client: AsyncClient):
    """Batch numbers must be unique per medicine, and selling rate cannot exceed MRP."""
    await setup_pharmacy(client)
    await login(client)

    # 1. Create medicine
    med_res = await client.post("/api/v1/medicines", json={
        "name": "Amoxicillin 500mg",
        "generic_name": "Amoxicillin",
        "dosage_form": "Capsule",
        "base_unit_id": 1,
    })
    med_id = med_res.json()["id"]

    # 2. Create valid batch
    future_expiry = (date.today() + timedelta(days=365)).isoformat()
    batch_res1 = await client.post("/api/v1/batches", json={
        "medicine_id": med_id,
        "batch_number": "BATCH-001",
        "expiry_date": future_expiry,
        "mrp": 120.0,
        "purchase_rate": 80.0,
        "default_selling_rate": 110.0,
    })
    assert batch_res1.status_code == 201
    assert batch_res1.json()["batch_number"] == "BATCH-001"
    assert batch_res1.json()["is_expired"] is False

    # 3. Duplicate batch for same medicine rejected
    batch_res_dup = await client.post("/api/v1/batches", json={
        "medicine_id": med_id,
        "batch_number": "BATCH-001",
        "expiry_date": future_expiry,
        "mrp": 120.0,
    })
    assert batch_res_dup.status_code == 409
    assert "already exists" in batch_res_dup.json()["detail"]

    # 4. Selling rate > MRP rejected
    batch_res_invalid = await client.post("/api/v1/batches", json={
        "medicine_id": med_id,
        "batch_number": "BATCH-002",
        "expiry_date": future_expiry,
        "mrp": 100.0,
        "default_selling_rate": 120.0,  # > MRP
    })
    assert batch_res_invalid.status_code == 422


@pytest.mark.asyncio
async def test_batch_deletion_protection(client: AsyncClient):
    """Batches with movement history cannot be deleted; empty batches can be deleted."""
    await setup_pharmacy(client)
    await login(client)

    med_res = await client.post("/api/v1/medicines", json={
        "name": "Pantoprazole 40mg",
        "generic_name": "Pantoprazole",
        "dosage_form": "Tablet",
        "base_unit_id": 1,
    })
    med_id = med_res.json()["id"]
    future_expiry = (date.today() + timedelta(days=365)).isoformat()

    # Create unused batch
    b_res = await client.post("/api/v1/batches", json={
        "medicine_id": med_id,
        "batch_number": "UNUSED-BATCH",
        "expiry_date": future_expiry,
        "mrp": 90.0,
    })
    unused_id = b_res.json()["id"]

    # Delete unused batch -> 204
    del_res = await client.delete(f"/api/v1/batches/{unused_id}")
    assert del_res.status_code == 204

    # Post opening stock on another batch (creates movement)
    op_res = await client.post("/api/v1/inventory/opening-stock", json={
        "medicine_id": med_id,
        "batch_number": "ACTIVE-BATCH",
        "expiry_date": future_expiry,
        "quantity_in_pack": 50.0,
        "mrp": 90.0,
        "purchase_rate": 60.0,
    })
    assert op_res.status_code == 201
    active_batch_id = op_res.json()["batch_id"]

    # Attempt delete on active batch with history -> 409 Conflict
    del_active_res = await client.delete(f"/api/v1/batches/{active_batch_id}")
    assert del_active_res.status_code == 409
    assert "Cannot delete batch with movement history" in del_active_res.json()["detail"]


@pytest.mark.asyncio
async def test_opening_stock_and_ledger_movements(client: AsyncClient):
    """Recording opening stock updates batch balance and ledger accurately."""
    await setup_pharmacy(client)
    await login(client)

    med_res = await client.post("/api/v1/medicines", json={
        "name": "Ibuprofen 400mg",
        "generic_name": "Ibuprofen",
        "dosage_form": "Tablet",
        "base_unit_id": 1,
    })
    med_id = med_res.json()["id"]
    future_expiry = (date.today() + timedelta(days=200)).isoformat()

    # Post opening stock of 100 units
    op_res = await client.post("/api/v1/inventory/opening-stock", json={
        "medicine_id": med_id,
        "batch_number": "IBU-2026-01",
        "expiry_date": future_expiry,
        "quantity_in_pack": 100.0,
        "mrp": 50.0,
        "purchase_rate": 35.0,
        "notes": "Initial stock intake",
    })
    assert op_res.status_code == 201
    op_data = op_res.json()
    assert op_data["movement_type"] == "OPENING_STOCK"
    assert Decimal(str(op_data["quantity_base"])) == Decimal("100.00")
    assert Decimal(str(op_data["balance_after_base"])) == Decimal("100.00")

    # Check medicine current stock
    med_get = await client.get(f"/api/v1/medicines/{med_id}")
    assert med_get.status_code == 200
    assert Decimal(str(med_get.json()["current_stock_base"])) == Decimal("100.00")

    # Query movements ledger
    movs_res = await client.get(f"/api/v1/inventory/movements?medicine_id={med_id}")
    assert movs_res.status_code == 200
    movs = movs_res.json()
    assert len(movs) == 1
    assert movs[0]["movement_type"] == "OPENING_STOCK"
    assert movs[0]["batch_number"] == "IBU-2026-01"


@pytest.mark.asyncio
async def test_stock_adjustment_cycle(client: AsyncClient):
    """Test shrinkage reduction (-10) and surplus recovery (+5) via stock adjustments."""
    await setup_pharmacy(client)
    await login(client)

    med_res = await client.post("/api/v1/medicines", json={
        "name": "Cough Syrup 100ml",
        "generic_name": "Dextromethorphan",
        "dosage_form": "Syrup",
        "base_unit_id": 4,  # Bottle
    })
    med_id = med_res.json()["id"]
    future_expiry = (date.today() + timedelta(days=300)).isoformat()

    # Step 1: Opening stock of 50 bottles
    op_res = await client.post("/api/v1/inventory/opening-stock", json={
        "medicine_id": med_id,
        "batch_number": "CS-001",
        "expiry_date": future_expiry,
        "quantity_in_pack": 50.0,
        "mrp": 120.0,
        "purchase_rate": 75.0,
    })
    batch_id = op_res.json()["batch_id"]

    # Step 2: Physical audit adjustment (-5 bottles broken)
    adj_res = await client.post("/api/v1/inventory/adjustments", json={
        "reason_code": "DAMAGE_BREAKAGE",
        "notes": "Found 5 broken bottles in shelf C",
        "items": [
            {
                "medicine_id": med_id,
                "batch_id": batch_id,
                "physical_stock_base": 45.0,  # Drops from 50 to 45
                "notes": "Broken during shelf reorganization",
            }
        ]
    })
    assert adj_res.status_code == 201
    adj_data = adj_res.json()
    assert adj_data["status"] == "POSTED"
    assert len(adj_data["items"]) == 1
    item = adj_data["items"][0]
    assert Decimal(str(item["system_stock_base"])) == Decimal("50.00")
    assert Decimal(str(item["physical_stock_base"])) == Decimal("45.00")
    assert Decimal(str(item["adjustment_qty_base"])) == Decimal("-5.00")

    # Verify updated batch balance
    batch_get = await client.get(f"/api/v1/batches/{batch_id}")
    assert Decimal(str(batch_get.json()["current_stock_base"])) == Decimal("45.00")

    # Verify updated medicine stock
    med_get = await client.get(f"/api/v1/medicines/{med_id}")
    assert Decimal(str(med_get.json()["current_stock_base"])) == Decimal("45.00")

    # Step 3: Physical audit surplus (+3 bottles found)
    adj_surplus = await client.post("/api/v1/inventory/adjustments", json={
        "reason_code": "PHYSICAL_AUDIT_SURPLUS",
        "notes": "Found 3 misplaced bottles in back storage",
        "items": [
            {
                "medicine_id": med_id,
                "batch_id": batch_id,
                "physical_stock_base": 48.0,  # Increases from 45 to 48
            }
        ]
    })
    assert adj_surplus.status_code == 201
    surplus_item = adj_surplus.json()["items"][0]
    assert Decimal(str(surplus_item["adjustment_qty_base"])) == Decimal("3.00")

    # Verify movements ledger
    movs_res = await client.get(f"/api/v1/inventory/movements?batch_id={batch_id}")
    movs = movs_res.json()
    types = [m["movement_type"] for m in movs]
    assert "ADJUSTMENT_IN" in types
    assert "ADJUSTMENT_OUT" in types
    assert "OPENING_STOCK" in types


@pytest.mark.asyncio
async def test_low_stock_and_expiry_alerts(client: AsyncClient):
    """Verify low-stock, near-expiry, and expired batch alert endpoints."""
    await setup_pharmacy(client)
    await login(client)

    today = date.today()

    # Medicine 1: Low stock item (reorder level 50, current stock 10)
    med1_res = await client.post("/api/v1/medicines", json={
        "name": "Atorvastatin 10mg",
        "generic_name": "Atorvastatin",
        "dosage_form": "Tablet",
        "base_unit_id": 1,
        "reorder_level": 50.0,
    })
    med1_id = med1_res.json()["id"]

    # Intake 10 units (low stock!)
    await client.post("/api/v1/inventory/opening-stock", json={
        "medicine_id": med1_id,
        "batch_number": "ATOR-01",
        "expiry_date": (today + timedelta(days=200)).isoformat(),
        "quantity_in_pack": 10.0,
        "mrp": 100.0,
    })

    # Medicine 2: Near expiry item (expiring in 20 days)
    med2_res = await client.post("/api/v1/medicines", json={
        "name": "Insulin Glargine",
        "generic_name": "Insulin Glargine",
        "dosage_form": "Vial",
        "base_unit_id": 7,  # Vial
        "reorder_level": 5.0,
    })
    med2_id = med2_res.json()["id"]

    await client.post("/api/v1/inventory/opening-stock", json={
        "medicine_id": med2_id,
        "batch_number": "INS-NEAR-EXP",
        "expiry_date": (today + timedelta(days=20)).isoformat(),
        "quantity_in_pack": 15.0,
        "mrp": 600.0,
    })

    # Medicine 3: Already expired item
    med3_res = await client.post("/api/v1/medicines", json={
        "name": "Expired Ointment",
        "generic_name": "Betamethasone",
        "dosage_form": "Tube",
        "base_unit_id": 6,  # Tube
    })
    med3_id = med3_res.json()["id"]

    await client.post("/api/v1/inventory/opening-stock", json={
        "medicine_id": med3_id,
        "batch_number": "EXP-BATCH-99",
        "expiry_date": (today - timedelta(days=10)).isoformat(),
        "quantity_in_pack": 5.0,
        "mrp": 80.0,
    })

    # Check Low Stock Alerts
    low_res = await client.get("/api/v1/inventory/alerts/low-stock")
    assert low_res.status_code == 200
    low_med_ids = [a["medicine_id"] for a in low_res.json()]
    assert med1_id in low_med_ids

    # Check Near Expiry Alerts (within 30 days)
    near_res = await client.get("/api/v1/inventory/alerts/near-expiry?days=30")
    assert near_res.status_code == 200
    near_batch_numbers = [a["batch_number"] for a in near_res.json()]
    assert "INS-NEAR-EXP" in near_batch_numbers

    # Check Expired Alerts
    exp_res = await client.get("/api/v1/inventory/alerts/expired")
    assert exp_res.status_code == 200
    exp_batch_numbers = [a["batch_number"] for a in exp_res.json()]
    assert "EXP-BATCH-99" in exp_batch_numbers


@pytest.mark.asyncio
async def test_unauthorized_user_cannot_adjust_stock(client: AsyncClient):
    """User without stock.adjust permission receives 403 Forbidden."""
    await setup_pharmacy(client)
    await login(client)

    # Fetch Cashier role
    roles_res = await client.get("/api/v1/roles")
    roles = {r["name"]: r for r in roles_res.json()}
    cashier_role_id = roles["Cashier"]["id"]

    # Create Cashier user
    user_res = await client.post("/api/v1/users", json={
        "name": "Bob Cashier",
        "username": "bobcashier",
        "password": "Password123",
        "role_id": cashier_role_id,
    })
    assert user_res.status_code == 201

    # Login as Bob Cashier
    await login(client, username="bobcashier", password="Password123")

    # Bob attempts to post opening stock -> 403
    op_res = await client.post("/api/v1/inventory/opening-stock", json={
        "medicine_id": 1,
        "batch_number": "B01",
        "expiry_date": "2027-01-01",
        "quantity_in_pack": 10.0,
        "mrp": 100.0,
    })
    assert op_res.status_code == 403

    # Bob attempts to post stock adjustment -> 403
    adj_res = await client.post("/api/v1/inventory/adjustments", json={
        "reason_code": "DAMAGE_BREAKAGE",
        "items": [],
    })
    assert adj_res.status_code == 403


@pytest.mark.asyncio
async def test_rollback_on_transaction_failure(client: AsyncClient):
    """When a multi-item adjustment fails on one item, the entire transaction rolls back."""
    await setup_pharmacy(client)
    await login(client)

    # 1. Create medicine and batch with opening stock 50
    med_res = await client.post("/api/v1/medicines", json={
        "name": "Vitamin C 500mg",
        "generic_name": "Ascorbic Acid",
        "dosage_form": "Tablet",
        "base_unit_id": 1,
    })
    med_id = med_res.json()["id"]

    op_res = await client.post("/api/v1/inventory/opening-stock", json={
        "medicine_id": med_id,
        "batch_number": "VITC-01",
        "expiry_date": "2027-12-31",
        "quantity_in_pack": 50.0,
        "mrp": 40.0,
    })
    batch_id = op_res.json()["batch_id"]

    # 2. Attempt multi-item adjustment where second item is non-existent batch (ID 99999)
    bad_adj_res = await client.post("/api/v1/inventory/adjustments", json={
        "reason_code": "AUDIT_DISCREPANCY",
        "items": [
            {
                "medicine_id": med_id,
                "batch_id": batch_id,
                "physical_stock_base": 20.0,  # Would deduct 30
            },
            {
                "medicine_id": med_id,
                "batch_id": 99999,  # Non-existent batch!
                "physical_stock_base": 10.0,
            }
        ]
    })
    assert bad_adj_res.status_code == 404

    # 3. Verify batch stock was NOT touched (remains 50.00)
    batch_get = await client.get(f"/api/v1/batches/{batch_id}")
    assert Decimal(str(batch_get.json()["current_stock_base"])) == Decimal("50.00")

    # 4. Verify no ADJUSTMENT movement was recorded
    movs_res = await client.get(f"/api/v1/inventory/movements?batch_id={batch_id}")
    mov_types = [m["movement_type"] for m in movs_res.json()]
    assert "ADJUSTMENT_OUT" not in mov_types
    assert len(mov_types) == 1  # Only OPENING_STOCK


@pytest.mark.asyncio
async def test_concurrent_or_sequential_adjustments(client: AsyncClient):
    """Multiple adjustments in succession properly record serial balances in ledger."""
    await setup_pharmacy(client)
    await login(client)

    med_res = await client.post("/api/v1/medicines", json={
        "name": "Paracetamol 650mg",
        "generic_name": "Paracetamol",
        "dosage_form": "Tablet",
        "base_unit_id": 1,
    })
    med_id = med_res.json()["id"]

    op_res = await client.post("/api/v1/inventory/opening-stock", json={
        "medicine_id": med_id,
        "batch_number": "PCM-650",
        "expiry_date": "2027-06-30",
        "quantity_in_pack": 100.0,
        "mrp": 30.0,
    })
    batch_id = op_res.json()["batch_id"]

    # Adjustment 1: Reduce by 10 (physical = 90)
    await client.post("/api/v1/inventory/adjustments", json={
        "reason_code": "BREAKAGE",
        "items": [{"medicine_id": med_id, "batch_id": batch_id, "physical_stock_base": 90.0}],
    })

    # Adjustment 2: Reduce by 20 (physical = 70)
    await client.post("/api/v1/inventory/adjustments", json={
        "reason_code": "EXPIRY",
        "items": [{"medicine_id": med_id, "batch_id": batch_id, "physical_stock_base": 70.0}],
    })

    # Adjustment 3: Increase by 5 (physical = 75)
    await client.post("/api/v1/inventory/adjustments", json={
        "reason_code": "FOUND",
        "items": [{"medicine_id": med_id, "batch_id": batch_id, "physical_stock_base": 75.0}],
    })

    # Final check
    batch_check = await client.get(f"/api/v1/batches/{batch_id}")
    assert Decimal(str(batch_check.json()["current_stock_base"])) == Decimal("75.00")

    # Movements audit check
    movs = (await client.get(f"/api/v1/inventory/movements?batch_id={batch_id}")).json()
    assert len(movs) == 4
    # Movements are returned newest first: 75 -> 70 -> 90 -> 100
    balances = [Decimal(str(m["balance_after_base"])) for m in movs]
    assert balances == [Decimal("75.00"), Decimal("70.00"), Decimal("90.00"), Decimal("100.00")]
