# Pharmacy ERP — Phase 3: Suppliers and Purchasing
## Technical Completion & Verification Report

**Module:** Phase 3 — Suppliers and Purchasing  
**Date:** October 21, 2026  
**Status:** COMPLETE & VERIFIED  
**Database:** MySQL 8.4 LTS (Alembic Revision: `003 (head)`)  
**Backend:** FastAPI 0.115 + SQLAlchemy 2.0 (89/89 Tests Passing)  
**Frontend:** Next.js 15.1.0 App Router + TypeScript 5.7 (12/12 Static Routes Compiled Cleanly)

---

## 1. Executive Summary

Phase 3 introduces comprehensive procurement, supplier lifecycle management, purchase ordering, inward goods verification, commercial supplier invoicing, and debit note returns to the Indian Retail Pharmacy ERP.

All core domain rules—including DPCO/Legal Metrology retail price ceilings, mandatory expiry validation, physical isolation of rejected goods, allocated acquisition costing for promotional "free" goods, and immutable stock movement tracking—have been enforced at the database transaction layer using MySQL 8.4 row locking (`FOR UPDATE`) and Decimal precision arithmetic.

---

## 2. Implemented Schema & Migration (`003`)

Alembic migration `003_20261021_phase3_purchasing_schema.py` was applied against MySQL 8.4 LTS in Docker (`pharmacy_db`), creating 9 relational tables with foreign keys and compound indexes:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          PHASE 3 DATABASE SCHEMA                            │
└─────────────────────────────────────────────────────────────────────────────┘

  ┌───────────────────┐        ┌───────────────────┐
  │     suppliers     │◀───────│  purchase_orders  │
  └─────────┬─────────┘        └─────────┬─────────┘
            │                            │
            │                  ┌─────────▼─────────┐
            │                  │purchase_order_item│
            │                  └───────────────────┘
            │                            │
            ├────────────────────────────┼─────────────────────────┐
            │                            │                         │
  ┌─────────▼─────────┐        ┌─────────▼─────────┐     ┌─────────▼─────────┐
  │ supplier_invoices │        │  goods_receipts   │◀────│ supplier_returns  │
  └─────────┬─────────┘        └─────────┬─────────┘     └─────────┬─────────┘
            │                            │                         │
  ┌─────────▼─────────┐        ┌─────────▼─────────┐     ┌─────────▼─────────┐
  │supplier_inv_items │        │goods_receipt_items│     │supplier_ret_items │
  └───────────────────┘        └───────────────────┘     └───────────────────┘
                                         │
                               ┌─────────▼─────────┐
                               │   stock_movement  │ (Phase 2 Ledger)
                               └───────────────────┘
```

### Table Definitions:

| Table Name | Description | Key Indexes & Constraints |
|---|---|---|
| `suppliers` | Supplier master with GSTIN, Form 20B/21B drug licenses, state code, credit limits | `uq_supplier_name`, `idx_suppliers_gstin`, `idx_suppliers_active` |
| `purchase_orders` | Purchase orders / requisitions | `uq_po_number`, `idx_po_supplier_status` |
| `purchase_order_items` | Line items with ordered packs, pack unit, negotiated rate, discount | `idx_po_item_po_id`, `idx_po_item_medicine` |
| `goods_receipts` | Inward goods intake (GRN) linked to PO or direct receipt | `uq_grn_number`, `idx_grn_supplier_status` |
| `goods_receipt_items` | GRN lines capturing batch, mfg/exp date, received/free/rejected packs, MRP, selling price | `idx_grn_item_grn_id`, `idx_grn_item_batch` |
| `supplier_invoices` | Supplier's commercial bill record with discrepancy auditing | `uq_supplier_invoice` (`supplier_id`, `invoice_number`), `idx_inv_status` |
| `supplier_invoice_items` | Invoice line items with tax breakdowns (CGST+SGST or IGST) | `idx_inv_item_invoice_id` |
| `supplier_returns` | Outward debit note return documents | `uq_return_number`, `idx_return_supplier` |
| `supplier_return_items` | Returned batch items with return reason and credit values | `idx_ret_item_return_id`, `idx_ret_item_batch` |

---

## 3. Core Business Invariants & Implementation Details

### 3.1. Zero Inventory Mutation Invariant for Purchase Orders
- Creating or approving a Purchase Order (`DRAFT`, `SUBMITTED`, `APPROVED`) mutates **zero** inventory rows.
- No `StockMovement` records or `MedicineBatch` records are touched during PO creation or status progression.
- Only cancelling an unfulfilled PO is permitted. POs with receipts transitioned to `PARTIALLY_RECEIVED` or `RECEIVED` cannot be cancelled.

### 3.2. Goods Receipt (GRN) Inward & Strict DPCO / Expiry Invariants
- **Retail Selling Price Constraint (DPCO / Legal Metrology):**
  $$\text{selling\_rate} \le \text{mrp}$$
  Intakes with $\text{selling\_rate} > \text{mrp}$ are strictly rejected with HTTP 422 Unprocessable Entity.
- **Mandatory Expiry Validation:**
  $$\text{expiry\_date} > \text{today}$$
  Any intake of expired stock is blocked with HTTP 422 and cannot enter saleable inventory.
- **Rejected & Damaged Goods Isolation:**
  - `rejected_qty` is captured with structured rejection reasons (`DAMAGED_PACKAGING`, `NEAR_EXPIRY_REJECTED`, `COLD_CHAIN_BROKEN`, `WRONG_SPECIFICATION`, `COUNTERFEIT_SUSPECTED`).
  - Rejected units are recorded for supplier chargeback and debit notes but **never** added to saleable `MedicineBatch` inventory.
- **Allocated Costing for Free Goods:**
  - Promotional free goods (e.g., "Buy 10 packs, get 2 free") are converted using:
    $$\text{Total Base Units} = (\text{accepted\_qty} + \text{free\_qty}) \times \text{conversion\_factor}$$
    $$\text{cost\_per\_base\_unit} = \frac{\text{Net Purchase Line Amount}}{\text{Total Base Units}}$$
  - Ensures accurate Weighted Average Cost (WAC) valuation under Indian AS 2 (Inventories).
- **Idempotent Stock Ledger Posting:**
  - Posting a GRN creates or updates `MedicineBatch` with row locking (`SELECT ... FOR UPDATE`), creates an immutable `StockMovement` of type `GOODS_RECEIPT`, and records the intake.
  - Re-posting an already posted GRN returns HTTP 409 Conflict.

### 3.3. Supplier Invoices & Commercial Discrepancy Auditing
- **Supplier Invoice Number Uniqueness:**
  - Compound uniqueness `(supplier_id, invoice_number)` prevents duplicate invoice entry (HTTP 409).
- **Original vs Calculated Totals & Discrepancy Tracking:**
  - Captures `supplier_invoice_total` (vendor's declared amount) alongside the ERP's computed `total_amount`.
  - Discrepancy amount ($\Delta = \text{total\_amount} - \text{supplier\_invoice\_total}$) is computed and flagged with audit notes.
- **Intra-State vs Inter-State Tax Normalization:**
  - Indian state codes are normalized across 2-digit numbers (`"27"`) and 2-letter codes (`"MH"`).
  - Intra-state transactions divide GST into equal CGST and SGST components.
  - Inter-state transactions allocate to IGST.

### 3.4. Supplier Returns (Debit Notes) & Stock Depletion
- **Returnable Quantity Invariant:**
  $$\text{return\_qty\_base} \le \text{accepted\_qty\_base} - \sum \text{prior\_returns\_base}$$
  Over-returns exceeding previously accepted inward quantity are blocked with HTTP 422.
- **Stock Depletion & Atomic Ledger Update:**
  - Verifies that the batch has sufficient current stock on hand (`batch.current_stock_base >= return_qty_base`).
  - Posting the return atomically deducts batch stock, creates an immutable `StockMovement` of type `RETURN_OUTWARD`, and generates debit note references.
  - Re-posting is blocked with HTTP 409 Conflict.

---

## 4. API Endpoints Registered

All Phase 3 endpoints are mounted under `/api/v1` and protected by RBAC permissions:

| Method | Endpoint | Description | Permission Required |
|---|---|---|---|
| `GET` | `/api/v1/suppliers` | List/search suppliers with pagination | `suppliers:read` |
| `POST` | `/api/v1/suppliers` | Create new supplier master | `suppliers:create` |
| `GET` | `/api/v1/suppliers/{id}` | Get supplier details | `suppliers:read` |
| `PUT` | `/api/v1/suppliers/{id}` | Update supplier | `suppliers:update` |
| `DELETE`| `/api/v1/suppliers/{id}` | Soft-delete / deactivate supplier | `suppliers:delete` |
| `GET` | `/api/v1/purchases/orders` | List purchase orders | `purchases:read` |
| `POST` | `/api/v1/purchases/orders` | Create draft purchase order | `purchases:create` |
| `GET` | `/api/v1/purchases/orders/{id}` | Get purchase order details | `purchases:read` |
| `PATCH`| `/api/v1/purchases/orders/{id}/status` | Update PO status (`SUBMITTED`, `APPROVED`, `CANCELLED`)| `purchases:approve` |
| `GET` | `/api/v1/purchases/receipts` | List goods receipts (GRNs) | `purchases:read` |
| `POST` | `/api/v1/purchases/receipts` | Create goods receipt draft | `purchases:create` |
| `GET` | `/api/v1/purchases/receipts/{id}` | Get goods receipt details | `purchases:read` |
| `POST` | `/api/v1/purchases/receipts/{id}/post` | Post GRN to inventory (atomic batch update & stock ledger) | `purchases:create` |
| `GET` | `/api/v1/purchases/invoices` | List supplier invoices | `purchases:read` |
| `POST` | `/api/v1/purchases/invoices` | Create supplier invoice | `purchases:create` |
| `GET` | `/api/v1/purchases/invoices/{id}` | Get supplier invoice details | `purchases:read` |
| `PATCH`| `/api/v1/purchases/invoices/{id}/payment` | Record payment status & paid amount | `purchases:create` |
| `GET` | `/api/v1/purchases/returns` | List supplier returns / debit notes | `purchases:read` |
| `POST` | `/api/v1/purchases/returns` | Create draft supplier return | `purchases:create` |
| `GET` | `/api/v1/purchases/returns/{id}` | Get return details | `purchases:read` |
| `POST` | `/api/v1/purchases/returns/{id}/post` | Post return (reduces batch stock & logs `RETURN_OUTWARD`) | `purchases:create` |

---

## 5. Verification & Test Execution Results

### 5.1. Backend Automated Tests (Pytest)
Executed across both the host Python virtual environment and the isolated Docker container (`pharmacy_backend`):

```bash
docker exec pharmacy_backend pytest -v
# Result: 89 passed in 19.91s (100% pass rate)

d:\Accounting\backend\.venv\Scripts\pytest.exe -v
# Result: 89 passed in 23.63s (100% pass rate)
```

#### Test Suite Breakdown:
- **`test_suppliers.py` (4 tests):**
  - `test_supplier_crud_lifecycle`: Create, read, search, update, soft-delete.
  - `test_duplicate_supplier_name_rejected`: Enforces unique supplier names.
  - `test_invalid_gstin_format_rejected`: Validates Indian 15-character GSTIN regex.
  - `test_supplier_permission_enforcement`: Rejects non-privileged users without `suppliers:create`.
- **`test_purchasing.py` (8 tests):**
  - `test_purchase_order_lifecycle_and_zero_inventory_mutation`: Verifies state progression and proves 0 inventory changes occur.
  - `test_goods_receipt_posting_batches_and_free_goods_costing`: Verifies batch creation, unit conversions, stock ledger entry, and allocated cost per unit with free items.
  - `test_expired_goods_and_selling_above_mrp_rejected`: Verifies 422 rejection on DPCO violation and expired goods.
  - `test_supplier_invoice_verification_and_duplicate_check`: Verifies invoice calculations, intra-state tax split, duplicate prevention, and discrepancy auditing.
  - `test_supplier_return_debit_note_and_returnable_validation`: Verifies debit note posting, stock deduction, `RETURN_OUTWARD` ledger entry, and returnable quantity bounds.
  - `test_partial_goods_receipt_po_fulfillment_status`: Verifies PO transitions from `APPROVED` -> `PARTIALLY_RECEIVED` -> `RECEIVED`.
  - `test_purchase_order_cancellation_rules`: Verifies received POs cannot be cancelled.
  - `test_insufficient_stock_for_supplier_return`: Prevents negative batch inventory when returning stock.
- **Phase 1 & Phase 2 Regression Suites (77 tests):**
  - All auth, security, tax, unit conversion, medicine catalogue, batch management, inventory ledger, and alert tests continue to pass 100%.

### 5.2. Frontend Type Checking & Production Build
Executed in `frontend/`:

```bash
npx tsc --noEmit
# Result: 0 errors

npm run build
# Result: Compiled successfully. 12/12 static routes generated.
```

- **Routes Built:**
  - `○ /suppliers`: 7.25 kB (First Load JS: 156 kB)
  - `○ /purchasing`: 12 kB (First Load JS: 161 kB)
  - `○ /medicines`: 8.75 kB (First Load JS: 158 kB)
  - `○ /inventory`: 7.28 kB (First Load JS: 156 kB)
  - `○ /dashboard`: 3.88 kB (First Load JS: 153 kB)
  - `○ /masters`: 6.25 kB (First Load JS: 155 kB)
  - `○ /login`: 3.72 kB (First Load JS: 109 kB)
  - `○ /setup`: 4.48 kB (First Load JS: 110 kB)
- **Live HTTP Check:**
  - `GET http://localhost:3000/suppliers` -> HTTP 200
  - `GET http://localhost:3000/purchasing` -> HTTP 200
  - `GET http://localhost:8000/api/v1/health/ready` -> HTTP 200 (`{"status":"ready","database":"connected"}`)

---

## 6. Accounting & Regulatory Considerations for Professional Validation

The following items are documented for formal validation with the pharmacy's chartered accountant (CA) and legal counsel before production go-live:

1. **GST Section 170 Currency Rounding Rules:**
   - Indian CGST Act Section 170 mandates that final tax and invoice amounts be rounded to the nearest whole rupee (fractions of 50 paise or more rounded up, less than 50 paise disregarded).
   - The current implementation captures exact round-off differentials (`round_off_amount`) to maintain mathematical equality:
     $$\text{grand\_total} = \text{subtotal} - \text{discount} + \text{taxes} + \text{other\_charges} + \text{round\_off}$$
   - *Recommendation:* Confirm whether the pharmacy's CA prefers line-by-line rounding or invoice-level rounding for GSTR-2B input tax credit (ITC) reconciliation.

2. **GSTR-2B Auto-Reconciliation:**
   - Inward supplier invoices entered into the ERP must match invoices uploaded by suppliers to the GST Portal in GSTR-1.
   - *Recommendation:* Phase 5/6 can introduce a CSV/JSON import of GSTR-2B statements to flag unmatched supplier bills or ITC mismatch.

3. **Schedule H & H1 Drug Purchase Register Retention:**
   - Under Rule 65 of the Drugs and Cosmetics Rules, 1945, records of purchases of Schedule H and H1 drugs must be preserved for at least two years from the date of the last entry.
   - *Recommendation:* Inward GRN entries for Schedule H/H1 medicines should be locked against hard deletion and archived in persistent storage.

4. **TDS on Purchase of Goods (Section 194Q):**
   - If the pharmacy's turnover exceeded ₹10 Crore in the preceding financial year and total aggregate purchases from a single supplier exceed ₹50 Lakh in a financial year, TDS at 0.1% must be deducted under Section 194Q of the Income Tax Act.
   - *Recommendation:* A threshold tracker per supplier can be activated in Phase 7 (Accounting).
