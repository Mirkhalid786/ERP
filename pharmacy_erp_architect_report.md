# Pharmacy ERP — Architect Audit & Design Decision Report
**Version:** 1.0 | **Date:** 2026-10-19 | **Status:** Pre-implementation

---

## Part 1 — OSS Repository Audit

### Audit Methodology
GitHub repository home pages were fetched and analyzed. Source files inside each repo could not be read without authentication (GitHub returns login-gated HTML for file tree views). Audit findings below are based on: README content, file tree listings visible from the repo home page, declared tech stacks, commit history counts, star/fork counts, and structural indicators.

> [!WARNING]
> **Access limitation:** No authenticated GitHub session was available. Individual source files (models, migrations, business logic, tests) could not be read from any of the three repositories. All "evidence" below is derived from public README/homepage information only. Do **not** treat any claim about internal implementation quality as verified.

---

### Repo 1 — `ishaanpadashetty/pharmacare-erp`

| Attribute | Finding |
|---|---|
| **Stars / Forks** | 2 stars, 2 forks |
| **Commit history** | 3 commits total — **extremely thin** |
| **License** | MIT |
| **Tech stack** | Next.js 16, React 19, TypeScript, **SQLite via libsql/Turso**, Drizzle ORM |
| **Database** | SQLite (Turso/libsql) — incompatible with our MySQL target |
| **Backend** | Next.js Server Actions only — **no separate FastAPI backend** |
| **Auth** | NextAuth v5, JWT 24hr, bcrypt, Zod validation |
| **Declared features** | FEFO, multi-unit stock (Box→Strip→Tablet→Bottle→ml→Pack→Sachet), batch tracking, CGST/SGST/IGST, expiry alerts (30/60/90 day), audit logs, CSV export |
| **Tests** | Not mentioned |
| **Migration tooling** | Drizzle Kit (`db:push`) — push-based, not migration-file-based |
| **Deployment** | No offline/LAN installer. Cloud-first (Turso is a cloud SQLite service) |
| **Active maintenance** | Unknown — 3 commits suggests brand-new or stalled |

**Verdict:**
- Philosophy and domain model are well-aligned (FEFO, GST, multi-unit, batch tracking).
- Tech stack is fundamentally incompatible: SQLite/Turso vs MySQL 8.4, Next.js-only vs FastAPI + Next.js.
- 3-commit repo with no test suite cannot be trusted for production correctness.
- **Reuse value:** The _conceptual data model_ (multi-unit inventory, stock movement design) and _module list_ are worth studying as a reference. No code can be directly reused into our FastAPI/SQLAlchemy/MySQL stack.

---

### Repo 2 — `imdurgeshhh/pharmacy-management-system`

| Attribute | Finding |
|---|---|
| **Stars / Forks** | 0 stars, 0 forks |
| **Commit history** | 22 commits |
| **License** | MIT |
| **Tech stack** | React + Vite, Node.js/Express, **PostgreSQL**, Clerk auth, Zustand, Playwright e2e |
| **Database** | PostgreSQL — incompatible with MySQL target |
| **Backend** | Node.js/Express — incompatible with FastAPI |
| **Auth** | Clerk (cloud auth service) — requires internet, incompatible with offline-first LAN deployment |
| **Declared features** | RBAC (Admin, Shopkeeper, Employee), batch tracking, Drug Schedule tags (OTC, H, H1, X, G), POS billing, purchase orders, prescription OCR, multi-tier rate limiting, schema validation, PDF generation |
| **Tests** | Claims 236 backend + 169 frontend tests across 43 suites |
| **Migration tooling** | Not identified from README |
| **Deployment** | Vercel-hosted demo — cloud-first, Clerk requires external service |
| **Notable** | Has `.agent/workflows`, `.gsd`, `UAT_PLAN.md`, `SANITY_CHECKLIST.md`, `REGRESSION_LOG.md` — structured development workflow |

**Verdict:**
- Architecturally the most mature-looking of the three (test counts, structured workflow docs).
- Clerk auth is a hard blocker — it requires internet connectivity and a cloud account, directly contradicting offline LAN deployment.
- PostgreSQL + Node/Express are incompatible with our MySQL/FastAPI stack.
- Drug Schedule tag implementation (H/H1/X/G) and the structured UAT docs are conceptually useful references.
- **Reuse value:** UAT test structure and drug schedule classification logic are worth reviewing conceptually. No code can be directly reused.

---

### Repo 3 — `jibintech/PharmTech`

| Attribute | Finding |
|---|---|
| **Stars / Forks** | 0 stars, 0 forks |
| **Commit history** | 3 commits total — **extremely thin** |
| **License** | Not stated explicitly |
| **Tech stack** | React + Vite, **Spring Boot (Java)**, MySQL, JWT auth |
| **Database** | MySQL — **matches our target database** |
| **Backend** | Spring Boot (Java) — incompatible with FastAPI |
| **Auth** | JWT — compatible concept, incompatible implementation |
| **Declared features** | POS billing (scrollless high-density cart), prescription OCR (OCR.space API), GST brackets, Indian Rupee formatting, UPI payments, PDF receipt generation, Docker Compose |
| **Tests** | None mentioned |
| **Migration tooling** | Raw SQL schemas in `/database` directory |
| **Deployment** | Docker Compose available — positive signal |
| **Notable** | Has a `/database` directory with SQL schemas visible — only repo with MySQL schemas potentially readable |

**Verdict:**
- Only repo using MySQL, but Spring Boot backend makes it architecturally incompatible.
- 3-commit history and no license statement are red flags.
- POS cart design (high-density, no vertical scroll) is a useful UX reference.
- The raw MySQL schema files (if accessible) could provide useful starting-point DDL patterns.
- **Reuse value:** MySQL schema DDL patterns (if inspectable) and POS UX design concepts only.

---

### Comparison Matrix

| Criterion | PharmaCare ERP | pharmacy-management-system | PharmTech |
|---|---|---|---|
| License | ✅ MIT | ✅ MIT | ⚠️ Unstated |
| Stack compatibility | ❌ SQLite/Next-only | ❌ PostgreSQL/Node | ⚠️ MySQL/Java |
| FEFO implementation | ✅ Declared | ❓ Unknown | ❓ Unknown |
| Indian GST (CGST/SGST/IGST) | ✅ Declared | ❓ Unknown | ✅ Declared |
| Multi-unit inventory | ✅ Declared | ❓ Unknown | ❌ Not mentioned |
| Batch tracking | ✅ Declared | ✅ Declared | ❓ Unknown |
| Drug Schedule compliance | ❌ Not mentioned | ✅ Declared | ❌ Not mentioned |
| Offline/LAN deployment | ❌ Turso cloud | ❌ Clerk cloud | ✅ Docker Compose |
| Test coverage | ❌ None mentioned | ✅ 405 tests claimed | ❌ None mentioned |
| Commit depth | ❌ 3 commits | ⚠️ 22 commits | ❌ 3 commits |
| Reuse decision | Reference only | Reference only | Schema reference only |

---

## Part 2 — Reuse vs Rebuild Decision

**Decision: Build from scratch on our intended stack.**

No repository offers code that can be safely transplanted into FastAPI + SQLAlchemy 2.x + MySQL 8.4 + Next.js architecture. The architectural mismatches (ORM, language, database) are complete. Adopting any of these repos as a base would require rewriting everything that matters.

**What to borrow conceptually (not as code):**
1. **PharmaCare ERP** — Multi-unit stock conversion model (Box→Strip→Tablet), FEFO algorithm description, expiry alert thresholds (30/60/90 days), GST field structure on line items.
2. **pharmacy-management-system** — Drug Schedule classification tags (OTC, H, H1, X, G) as an enum in our medicine catalogue. UAT plan structure and sanity checklist approach.
3. **PharmTech** — POS cart UX (high-density, minimal scroll), Docker Compose deployment template structure, MySQL schema organization pattern.

---

## Part 3 — Architecture Confirmation

The proposed architecture is **confirmed and sound**. No OSS findings justify changing it.

```
Pharmacy LAN
      │
      ├── Cashier PC (browser) ──────┐
      ├── Owner PC (browser) ────────┤
      └── Manager PC (browser) ──────┘
                                     │
                          ┌──────────▼──────────┐
                          │   Next.js 15 App    │
                          │   (UI + API proxy)  │
                          └──────────┬──────────┘
                                     │ HTTP (LAN-internal)
                          ┌──────────▼──────────┐
                          │   FastAPI (Python)  │
                          │   Business Logic    │
                          │   Pydantic / Auth   │
                          └──────────┬──────────┘
                                     │ SQLAlchemy 2.x
                          ┌──────────▼──────────┐
                          │    MySQL 8.4 LTS    │
                          │    InnoDB / ACID    │
                          └─────────────────────┘
```

**Confirmed decisions:**
- Browser never connects directly to MySQL — enforced by architecture.
- FastAPI owns all business rules, transaction boundaries, and permission checks.
- MySQL InnoDB provides ACID guarantees for concurrent stock updates.
- Alembic provides version-controlled, reversible migrations (not push-based).
- Session-based auth (server-managed) avoids cloud dependencies — compatible with offline LAN.
- Docker Compose or native Windows services for local deployment.

---

## Part 4 — Blueprint Review and Design Corrections

The original blueprint is well-structured. The following gaps, inconsistencies, and risks were identified during review.

### 4.1 Critical gaps and corrections

#### G1 — Unit conversion model is incomplete

The blueprint defines `medicine_units` and `units_per_pack` on the `medicines` table, but does **not** define a multi-level conversion chain.

In practice, Indian pharmacies sell:
- **Paracetamol 500mg:** purchased in boxes (10 strips × 10 tablets), sold by strip or by tablet.
- **Syrup:** purchased by bottle (200ml), sold by bottle or fraction.
- **Injection:** purchased by box (10 vials), sold by vial.

**Missing:** A `medicine_unit_conversions` table recording the conversion factors between purchase unit, dispensing unit, and intermediate units.

**Recommended addition:**
```sql
medicine_unit_conversions (
  id, medicine_id, from_unit_id, to_unit_id, 
  conversion_factor DECIMAL(12,6), -- e.g. 1 strip = 10 tablets
  is_purchase_unit BOOLEAN,
  is_sale_unit BOOLEAN,
  created_at
)
```
Stock balances should be tracked in the **base dispensing unit** (tablet, ml, vial). Purchase quantities and sale quantities must be converted to this base unit before updating `stock_balances`.

#### G2 — Free quantity handling in purchases is ambiguous

`purchase_items` has `free_quantity` column, but the blueprint doesn't define:
- Whether free goods are valued at zero cost or allocated cost.
- How free goods affect the effective cost-per-unit used in `unit_cost_snapshot` on sales.
- Whether free goods receive a separate batch or share the primary batch.

**Recommended:** Add `free_quantity_cost_method` to pharmacy settings (options: `zero_cost` | `allocated`). For `allocated`, spread the line total cost across (quantity + free_quantity). Document this in code and UI.

#### G3 — `stock_balances.quantity_reserved` is undefined

The blueprint mentions `quantity_reserved` in `stock_balances` but never explains what creates or releases a reservation. Without a reservation system, you cannot safely hold stock for a draft invoice in a multi-cashier environment.

Two options:
- **Simple:** Remove `quantity_reserved` from V1. Rely on SELECT ... FOR UPDATE row locking at posting time. Accept that two drafts can compete for the same stock; the loser gets an error at posting.
- **Full:** Add `stock_reservations` table with TTL. More complex to implement and maintain.

**Recommendation for V1:** Remove `quantity_reserved`. Document the chosen concurrency approach (optimistic locking at posting). Add the reservation system as a Phase 4 enhancement if cashier feedback reveals it's needed.

#### G4 — Invoice number sequencing race condition

The blueprint mentions `invoice_sequences` with `last_number`, but doesn't define the atomic increment strategy.

`SELECT last_number FROM invoice_sequences WHERE ... FOR UPDATE` then `UPDATE last_number = last_number + 1` inside the same transaction is the correct approach. Do **not** use `SELECT MAX(invoice_number)` from the invoices table — that creates gaps and race conditions.

Also define:
- Financial year start (April 1 for India).
- Sequence reset behavior on April 1 (reset to 1 or continue — choose explicitly).
- Gap handling (a voided invoice leaves a gap — this is legally acceptable in India, but must be documented).

#### G5 — GST calculation order is not defined

The blueprint mentions CGST/SGST/IGST but doesn't specify the calculation order. In India:

```
Taxable value = (unit_price × quantity) - line_discount
CGST = taxable_value × (gst_rate / 200)   # half rate for CGST
SGST = taxable_value × (gst_rate / 200)   # half rate for SGST
-- OR --
IGST = taxable_value × (gst_rate / 100)   # full rate for IGST
Line total = taxable_value + CGST + SGST (or IGST)
```

For MRP-inclusive GST (common in retail pharmacy):
```
Taxable value = MRP_total / (1 + gst_rate/100)
Tax = MRP_total - taxable_value
```

**These are different calculations.** The blueprint must specify whether prices are tax-exclusive (B2B style) or MRP-inclusive (retail style). Indian retail pharmacies typically sell at MRP, which is GST-inclusive for scheduled medicines. This changes the tax calculation direction.

**Recommendation:** Add a `price_includes_gst` boolean to `medicines` or to pharmacy `settings`. Default to `true` for Indian retail. Document the formula in code comments with test cases.

#### G6 — Return quantity validation is under-specified

`purchase_return_items` says "prevent returns exceeding eligible quantity" but doesn't define:
- How to calculate "eligible" = original quantity − all prior returns on that purchase item.
- What happens to batch stock on a supplier return (reduce batch stock? quarantine?).
- Whether a credit note or debit note is issued (terminology matters for GST compliance).

**Recommendation:** Add `credited_quantity` running total column to `purchase_items`. On each return posting, validate: `return_qty ≤ (original_qty - credited_qty)`, then increment `credited_qty`.

Same pattern for `sales_return_items` → track `returned_quantity` on `sales_items`.

#### G7 — `sales_items.unit_cost_snapshot` cost method undefined

The blueprint correctly notes this is needed for margin reports, but doesn't define the cost method:
- **Weighted average cost** at time of sale — most common for pharmacy.
- **FIFO cost** — complex with batch tracking.
- **Batch purchase rate** — use `medicine_batches.purchase_rate` from the batch being sold.

**Recommendation:** Use batch purchase rate (simplest and most defensible — you know exactly which batch was sold). Record `medicine_batches.purchase_rate` as `unit_cost_snapshot` at the moment of invoice posting. Document this in the data dictionary.

#### G8 — No `payment_allocations` table for multi-invoice payments

`purchase_payments` has `purchase_invoice_id` as a direct FK. The blueprint notes this may need a separate allocation table. This **must** be resolved before Phase 3 — supplier credit scenarios (partial payments, advance payments applied across invoices) are extremely common in Indian pharmacy purchasing.

**Recommendation:** Remove `purchase_invoice_id` from `purchase_payments`. Add:
```sql
payment_allocations (
  id, payment_id, purchase_invoice_id, allocated_amount, created_at
)
```
Same pattern for `sales_payments` → `sales_payment_allocations`.

#### G9 — `cash_sessions` doesn't link to payments

`cash_sessions` tracks cashier opening/closing cash, but `sales_payments` and `expenses` don't reference `cash_session_id`. Without this link, you cannot reconcile which payments belong to which session.

**Recommendation:** Add `cash_session_id` FK to `sales_payments`, `cash_movements`, and `expenses`.

#### G10 — No `supplier_ledger` / `customer_ledger` running balance

`suppliers.opening_balance` and `customers.opening_balance` are defined but there's no ledger to track running balance changes. The current design requires summing all purchase invoices + payments + returns to compute the current outstanding, which is expensive and error-prone.

**Recommendation for V1:** Add computed balance fields to `suppliers` and `customers`, updated transactionally:
- `suppliers.current_balance` = opening + invoiced − paid − returns
- `customers.current_balance` = opening + sales − received − returns

These are denormalized but standard in operational ERP systems. Keep the movement records as the source of truth; treat balance fields as maintained aggregates.

---

### 4.2 Architectural risks

| Risk | Severity | Mitigation |
|---|---|---|
| Concurrent stock overselling | 🔴 Critical | `SELECT ... FOR UPDATE` on `stock_balances` rows within posting transaction. Test with concurrent Pytest threads. |
| Invoice number gaps/duplicates | 🔴 Critical | `FOR UPDATE` on `invoice_sequences` row. Unique constraint on `(pharmacy_id, document_type, invoice_number)`. |
| Posted invoice mutation | 🔴 Critical | Database trigger or application-level guard preventing UPDATE/DELETE on posted invoices and stock movements. |
| GST calculation drift | 🟠 High | Unit-test every GST formula with known invoice examples from a real pharmacist. |
| Expired stock sold silently | 🟠 High | FastAPI guard checks `expiry_date < today` before adding to cart and again at posting. Not just a UI warning. |
| Backup "false success" | 🟠 High | Backup process must verify checksum and file size after completion. Never write `status=success` before verification. |
| Windows service restart leaving open transactions | 🟠 High | InnoDB crash recovery handles this, but test it explicitly (kill the process mid-invoice). |
| LAN network interruption mid-POST | 🟡 Medium | Client should detect timeout and not retry a POST blindly. Implement idempotency key on invoice submission. |
| Multi-unit stock math rounding | 🟡 Medium | Use `DECIMAL(12,3)` for quantities. Document rounding rules. Test strip→tablet conversions. |
| Financial year rollover | 🟡 Medium | Define behavior before implementation. Test April 1 transition explicitly. |
| bcrypt → Argon2id | 🟡 Medium | Blueprint specifies Argon2id. PharmaCare ERP uses bcrypt (sufficient but weaker). Use `argon2-cffi` in Python. |

---

### 4.3 Missing tables (additions to the blueprint)

| New Table | Purpose |
|---|---|
| `medicine_unit_conversions` | Multi-level unit conversion chain (G1) |
| `payment_allocations` | Many-to-many payment → invoice mapping (G8) |
| `supplier_credit_notes` | Formal credit note records for purchase returns |
| `customer_credit_notes` | Formal credit note records for sales returns |
| `drug_schedule_types` | Lookup for OTC / H / H1 / X / G with dispensing rules |
| `idempotency_keys` | Prevent duplicate POSTing of same invoice from client retry |

---

### 4.4 Minor corrections

- `medicines.gst_rate` should be `DECIMAL(5,2)` not left undefined. Store as percentage value (e.g., `12.00` for 12%).
- `medicine_batches` unique key `(pharmacy_id, medicine_id, batch_number, expiry_date)` — confirm with a pharmacist whether the same batch_number can have two expiry dates from the same manufacturer. If yes, add `manufacturing_date` to the unique key.
- `audit_logs.metadata_json` — use `JSON` column type in MySQL 8.4 (not `TEXT`) to enable JSON indexing.
- `settings.setting_value` — define maximum length and value validation. Consider `JSON` type for complex settings.
- `users.role_id` — the blueprint uses a single role per user. If multi-role is needed later, add `user_roles` junction table. For V1, single role is acceptable; document the assumption.

---

## Part 5 — Finalized Entity Relationship Design

```
pharmacies ──┬── users ──── roles ──── role_permissions ──── permissions
             ├── settings
             ├── medicine_categories
             ├── manufacturers
             ├── medicines ──────────── medicine_unit_conversions
             │       │                 medicine_alternate_barcodes
             │       │
             │   medicine_batches ──── stock_balances
             │       │                stock_movements
             │       │                stock_adjustment_items ── stock_adjustments
             │       │
             │       ├── purchase_items ──── purchase_invoices ──── suppliers
             │       │                          payment_allocations ─── purchase_payments
             │       │                      purchase_return_items ── purchase_returns
             │       │
             │       ├── sales_items ──── sales_invoices ──── customers
             │       │                       sales_payment_allocations ─── sales_payments
             │       │                   sales_return_items ── sales_returns
             │       │
             │       └── stock_movements (reference via batch_id)
             │
             ├── expenses ──── cash_sessions ──── cash_movements
             ├── invoice_sequences
             ├── idempotency_keys
             ├── audit_logs
             └── backup_history
```

---

## Part 6 — Prioritized Module Implementation Plan

### Phase 1 — Foundation (Week 1–2)
- [ ] Repository setup: monorepo with `/backend` (FastAPI) and `/frontend` (Next.js)
- [ ] Docker Compose: MySQL 8.4, FastAPI, Next.js in one `docker-compose.yml`
- [ ] Alembic initialized, first migration: `pharmacies`, `users`, `roles`, `permissions`, `role_permissions`, `settings`
- [ ] FastAPI auth: Argon2id password hashing, server-side sessions, CSRF protection
- [ ] Middleware: role-based permission enforcement on all routes
- [ ] Health endpoints: `/api/v1/health/live`, `/api/v1/health/ready`
- [ ] OpenAPI schema auto-generated and verified

**Exit criteria:** Can create pharmacy, create users with roles, login/logout, check permissions, hit health endpoint.

---

### Phase 2 — Medicine Catalogue (Week 3–4)
- [ ] Migrations: `medicine_categories`, `manufacturers`, `medicine_units`, `medicines`, `medicine_unit_conversions`, `medicine_alternate_barcodes`
- [ ] CRUD APIs for all catalogue entities
- [ ] Medicine search: name, generic name, barcode, manufacturer
- [ ] Unit conversion validation logic in FastAPI service layer
- [ ] Drug schedule enum and prescription_required flag enforcement

**Exit criteria:** Can add a medicine with units, define conversion chain (box→strip→tablet), search by barcode.

---

### Phase 3 — Purchasing and Inventory (Week 5–7)
- [ ] Migrations: `suppliers`, `purchase_invoices`, `purchase_items`, `purchase_payments`, `payment_allocations`, `medicine_batches`, `stock_balances`, `stock_movements`, `stock_adjustments`, `stock_adjustment_items`, `purchase_returns`, `purchase_return_items`
- [ ] Purchase invoice: draft → post workflow with atomic stock updates
- [ ] Batch creation on goods receipt
- [ ] Multi-unit conversion to base unit in stock movements
- [ ] Stock adjustment: draft → approved → posted workflow
- [ ] Expiry alert query: expired, 30/60/90 day near-expiry
- [ ] Low-stock alert query against `reorder_level`
- [ ] Supplier ledger balance update

**Exit criteria:** Purchase invoice posts correctly, stock moves, batch created, expiry alert fires, stock adjustment posts atomically.

---

### Phase 4 — POS and Sales (Week 8–10)
- [ ] Migrations: `customers`, `sales_invoices`, `sales_items`, `sales_payments`, `sales_payment_allocations`, `invoice_sequences`, `idempotency_keys`, `cash_sessions`, `cash_movements`
- [ ] Invoice sequence allocator (FOR UPDATE, financial year aware)
- [ ] Idempotency key validation on invoice POST
- [ ] FEFO batch selection: API returns available batches sorted by expiry_date ASC
- [ ] Sale posting: full 10-step transactional workflow with SELECT FOR UPDATE
- [ ] GST calculation engine with configurable MRP-inclusive vs exclusive mode
- [ ] Multi-payment split (cash + UPI + credit in one invoice)
- [ ] Customer outstanding balance update
- [ ] Receipt PDF or thermal print template

**Exit criteria:** Can post a sale; stock reduced exactly once; duplicate retry rejected; split payment recorded; customer balance updated; invoice number unique and gapless within session.

---

### Phase 5 — Returns and Operational Accounting (Week 11–12)
- [ ] Sales returns workflow with restock/quarantine decision
- [ ] Purchase returns workflow with credited_quantity validation
- [ ] Expenses with cash session linkage
- [ ] Cash session open/close with expected vs actual variance
- [ ] Credit notes (customer and supplier)

**Exit criteria:** Can return a sale; stock restocked or quarantined correctly; return quantity validated against original; cash session closes with reconciliation.

---

### Phase 6 — Reports and Administration (Week 13–14)
- [ ] Sales summary report (daily/weekly/monthly/product-wise)
- [ ] Stock valuation report (batch cost × quantity)
- [ ] Expiry report (expired + near-expiry with value)
- [ ] GST summary (CGST/SGST/IGST collected by rate bracket)
- [ ] Purchase summary (supplier-wise)
- [ ] Profit/margin report (sales revenue − cost using unit_cost_snapshot)
- [ ] Audit log viewer (filtered by entity, user, action)
- [ ] Backup: mysqldump automation, checksum verification, `backup_history` record
- [ ] Restore procedure documentation and test

**Exit criteria:** All reports reconcile to posted invoices and stock movements. Backup restored to clean database matches original data.

---

### Phase 7 — Local Deployment (Week 15–16)
- [ ] Docker Compose production configuration (Windows and Linux)
- [ ] Windows Service wrapper (NSSM or native) for non-Docker deployment
- [ ] LAN network configuration guide (static IP, firewall, mDNS)
- [ ] First-run setup wizard: pharmacy profile, admin user, initial settings
- [ ] Update procedure: Alembic migration on update
- [ ] System health dashboard in UI (DB connection, disk space, last backup time)

---

### Phase 8 — Pilot Testing (Week 17–18)
- [ ] Load test data matching real pharmacy volume
- [ ] Concurrent sale simulation (2 cashiers, same batch, limited stock)
- [ ] Financial year rollover test (April 1 sequence reset)
- [ ] Crash recovery test (kill process mid-invoice, restart, verify consistency)
- [ ] Backup restore test (restore to clean MySQL instance)
- [ ] Hardware test: USB barcode scanner, thermal printer, A4 PDF print
- [ ] Pharmacist workflow validation with real user

---

## Part 7 — Risk Register

| # | Risk | Probability | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Concurrent oversell of last unit | Medium | 🔴 Critical | `SELECT FOR UPDATE` on `stock_balances`; integration test with concurrent threads |
| R2 | GST calculation incorrect for MRP-inclusive medicines | High | 🔴 Critical | Consult pharmacist; unit-test with known GST invoices; get CA review before production |
| R3 | Invoice number duplicate across cashiers | Low | 🔴 Critical | `FOR UPDATE` on sequence row; unique DB constraint |
| R4 | Expired stock sold despite UI warning | Medium | 🔴 Critical | Server-side expiry check at posting (not just UI) |
| R5 | Backup reports success without completing | Medium | 🟠 High | Verify checksum post-backup; never mark success until verified |
| R6 | Financial year rollover breaks sequences | Low | 🟠 High | Test April 1 scenario explicitly in staging |
| R7 | Windows service crash leaves open transaction | Medium | 🟠 High | InnoDB handles this; test explicitly with process kill |
| R8 | Multi-unit stock math rounding accumulates error | Medium | 🟠 High | `DECIMAL(12,3)` quantities; rounding unit tests; stock reconciliation report |
| R9 | Drug Schedule H/X medicines sold without prescription | Medium | 🟠 High | Server-side prescription_required check; audit log every sale of regulated medicine |
| R10 | LAN security (unauthorized access from same network) | Medium | 🟠 High | Bind services to local IP only; session auth; HTTPS on LAN |
| R11 | Alembic migration failure on live database | Low | 🟠 High | Backup before every migration; test rollback procedure |
| R12 | Free quantity cost allocation incorrect in margin reports | Low | 🟡 Medium | Document cost method; unit-test margin calculations |
| R13 | Customer credit limit not enforced at POS | Medium | 🟡 Medium | API validates credit_limit before allowing credit sale |
| R14 | Thermal printer compatibility varies by hardware | High | 🟡 Medium | Test on target printer models before pilot |

---

## Part 8 — Next Step Recommendation

**Do this next, in order:**

1. **Confirm two design decisions** before touching code:
   - GST mode: MRP-inclusive (retail) or MRP-exclusive (B2B)?  
   - Unit conversion: Will stock be tracked in base dispensing unit (tablets/ml) or in purchase units?
   - Free quantity cost method: zero-cost or allocated?

2. **Create the project scaffold** (Phase 1):
   ```
   pharmacy-erp/
   ├── backend/           # FastAPI + SQLAlchemy + Alembic
   │   ├── app/
   │   │   ├── api/v1/   # Route handlers
   │   │   ├── models/   # SQLAlchemy ORM models
   │   │   ├── schemas/  # Pydantic request/response models
   │   │   ├── services/ # Business logic layer
   │   │   └── core/     # Config, auth, security
   │   ├── alembic/      # Migration scripts
   │   └── tests/        # Pytest
   ├── frontend/          # Next.js 15 + TypeScript + Tailwind CSS 4
   │   ├── app/          # App Router pages
   │   ├── components/   # shadcn/ui + Watermelon Platform components
   │   └── lib/          # API client, auth hooks
   ├── docker-compose.yml
   ├── .env.example
   └── README.md
   ```

3. **Write the corrected MySQL DDL** incorporating all additions from Section 4 above.

4. **Write Alembic initial migration** from that DDL.

5. **Implement auth and pharmacy setup** with full test coverage before building any transaction module.

> [!IMPORTANT]
> **Do not implement the sales posting engine until the GST calculation mode and unit conversion model are finalized and unit-tested.** These decisions affect every table and every calculation in the system. Getting them wrong after data is live is extremely costly.

---

*Report compiled by Antigravity Architect Agent. Based on public repository information and deep blueprint review. Internal source code of audited repositories was not accessible without GitHub authentication. Verify all OSS conclusions before relying on them.*
