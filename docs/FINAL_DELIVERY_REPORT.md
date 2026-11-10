# Pharmacy ERP — Final Delivery & Test Report

**Date:** 10 November 2026
**Codebase:** github.com/Mirkhalid786/ERP, initial commit `e25ff08` + stabilization
branch `muse/stabilization` (commits `2ab636b`, `fbf6a53`, and this report's commit).
**Scope:** Complete takeover per the master directive: verify the existing
implementation against the actual code, fix confirmed defects, add regression
tests, and document what was *actually* tested — including what could not be
tested in the build workspace.

---

## 1. Test results (actual runs)

| Check | Result |
|---|---|
| Backend test suite (baseline, before fixes) | **324 passed, 23 skipped, 0 failed** (5m08s) |
| Backend test suite (final, after fixes) | **327 passed, 23 skipped, 0 failed** (5m36s) — the +3 are new regression tests added during stabilization |
| Affected-area re-runs (purchasing/medicines/inventory/payables) | 25/25 and 16/16 passed |
| Frontend type-check (`tsc --noEmit`) | **Pass** (before and after fixes) |
| Frontend production build (`next build`) | **Pass** — 34 routes compiled (before and after fixes) |
| Docker Compose files | Statically validated (see §4). Live `docker compose up` **not run** — no Docker in the build workspace (§5) |
| MySQL-specific tests (`backend/tests/mysql/`, 23 tests) | **Skipped** — no MySQL in the build workspace (§5) |

Test environment: Python 3.12.3, Node 24, in-memory SQLite for the main suite
(the suite's own design; MySQL tests are a separate suite).

## 2. Defects found in the actual code and fixed

1. **False logout on any error (frontend).** Dashboard, Inventory, Medicines,
   Masters and Suppliers redirected to `/login` on *any* load error; the shared
   staff hook behind POS, Sales, Payables, Customers, Expenses, Day-end and
   Receivables did the same when fetching the current user. A 403/500 therefore
   destroyed a valid session, contrary to §13/§15 of the directive.
   **Fix:** shared `isUnauthorized()` helper in `frontend/lib/api.ts`; all listed
   pages now redirect only on HTTP 401 and otherwise show an inline error.
2. **Low-stock filter counted expired stock (backend).** The Medicines list
   `low_stock_only` filter summed all active batches including expired ones,
   unlike the alerts endpoint — a medicine could look "in stock" on expired
   batches alone. **Fix:** the filter now excludes expired batches, one rule
   everywhere (§8).
3. **Purchase cancellation ignored free goods (backend).** Cancelling a posted
   direct purchase reversed only the billed quantity. Free quantities received
   into the same batch stayed in stock with no purchase backing them, and
   cancellation was allowed even after free units had been sold.
   **Fix:** cancellation now requires and reverses billed + free quantities.
   Regression tests: scenarios **F2** (cancel with free goods → stock exactly 0)
   and **G2** (free units consumed → cancellation blocked, 422), both passing.

## 3. Directive scenario coverage

The existing suite already encodes the directive's scenarios as end-to-end API
tests (all passing), notably in `test_direct_purchasing.py` (its own scenarios
A–J), `test_payables.py`, `test_sales_pos.py`, `test_sales_credit.py`,
`test_gl_posting.py`, `test_accounting.py`, `test_hardening.py`,
`test_user_management.py`, `test_bill_import.py`, `test_catalogue_import.py`
and `test_indian_catalogue_import.py`.

| Directive scenario | Evidence |
|---|---|
| A. Catalogue search (brand/salt/manufacturer, pagination) | Search is server-side and paginated in `routes/medicines.py` and POS search; catalogue source verified directly: **253,973 rows, 7,648 distinct manufacturers, 7,905 discontinued**; "Dolo 650 Tablet → Paracetamol (650mg) → Micro Labs Ltd" matches the source data |
| B. Direct purchase → invoice + GRN + batches + stock + payable + GL | `test_direct_purchasing` A/B, `test_unified_purchasing_redesign`, GL posting tests |
| C. Multiple batches / free goods / GST | `test_direct_purchasing` B/C (free qty: stock 120 for 10+2 packs, allocated cost ₹7.50/unit) |
| D/E. Supplier payments, oldest-first, advances | `test_payables` incl. the directive's exact ₹250/₹800/₹1,000 → pay ₹500 example, added as `test_auto_allocation_matches_directive_example_oldest_due_first` (created in reverse due order to prove due-date priority; ₹0 advance; balance ₹1,550; reconciliation clean) |
| F. Returns & financial credit notes | `test_direct_purchasing` D, `test_payables`, supplier credit-note service tests |
| G. Purchase cancellation rules | `test_direct_purchasing` E/F/G + new F2/G2 |
| H/I. POS, FEFO, expiry, sales returns | `test_sales_pos` (FEFO earliest-expiry allocation, expired/insufficient stock blocked), `test_sales_credit` |
| J. Low-stock & expiry rules | `test_inventory` alerts tests + fix #2 above |
| K. Role permissions | Route-level permission dependencies on every router; `test_user_management`, `test_payables` permission tests, `test_direct_purchasing` J (Cashier blocked 403), default role matrix documented in `ADMIN_AND_WORKFLOWS.md` |
| L. Accounting balance / reports | `test_accounting`, `test_gl_posting`, `test_sales_reports`, `test_reports_ops_admin` |
| M. Performance | Server-side search/pagination and dashboard aggregation are in code; **live timings against the full catalogue on MySQL were not measured** (§5) |
| N. Backup & restore | Backup service implements SHA-256 + row-count manifest and scratch-schema restore verification; the executable restore tests are in the MySQL suite (§5) |

## 4. Infrastructure review (static, code-level)

- Production Compose publishes **only** the frontend (`LAN_BIND`, default
  `0.0.0.0:3000`); MySQL and the API publish no ports in prod. Dev Compose binds
  MySQL to `127.0.0.1` only.
- All services start in health order (`depends_on: service_healthy`); the API
  refuses to start if the database revision does not match the code
  (schema guard). Persistent volumes for MySQL data and backups.
- Alembic head is **013**; no applied migration was modified; no new migration
  was needed for the stabilization fixes.
- Secrets: only `.env.example` is tracked; `.gitignore` excludes `.env`, dumps
  and backups. No real credentials were found in the repository. (If any
  example values were ever reused on a live install, rotate them — the repo
  was public for a period.)

## 5. Remaining limitations (not tested here — need a Docker/MySQL host)

These are stated plainly rather than claimed:

1. **MySQL test suite (23 tests)** — concurrency, MySQL accounting/GL/payables,
   MySQL backup and restore verification — skipped here; run
   `pytest tests/mysql` against a disposable MySQL 8.4 (e.g. via Docker) before
   go-live.
2. **Live Docker Compose startup & health** — validate with
   `docker compose -f docker-compose.prod.yml up` on the target machine and
   confirm all services report healthy.
3. **Full-catalogue search timing on MySQL** — import the Indian catalogue into
   MySQL and time brand/salt searches (target < 1s on the LAN). Search uses
   contains-matching with btree indexes on name/brand/generic/barcode/
   manufacturer; if timings disappoint on the real data, add a MySQL FULLTEXT
   index in a new migration (014) — do not edit existing migrations.
4. **Browser/device checks** — thermal-printer output, barcode scanners and
   WhatsApp delivery require real devices/accounts (the code paths and unit
   tests exist; the production guide marks these 🔎).

## 6. What was NOT changed

- Repository visibility (owner set it to private; untouched).
- Nothing was pushed to GitHub — stabilization work is on the local
  `muse/stabilization` branch.
- No existing data model, migration, or historical-record behaviour was altered
  beyond the three fixes in §2. The medicines list still displays total
  physical stock (including expired) as `current_stock_base`; only the
  low-stock *rule* was aligned — a deliberate, documented choice so stock
  displays don't silently change meaning.
- Supplier payment ordering is due-date, then invoice id (creation order),
  which matches due-date-then-invoice-date in normal entry order.

## 7. Handover contents

1. Updated source code — branch `muse/stabilization` in this repository.
2. Database migrations — unchanged, head 013.
3. Configuration example — `.env.example` (no secrets).
4. Installation / Docker / LAN / backup-restore — `docs/DEPLOYMENT.md`,
   `docs/PRODUCTION_DEPLOYMENT_GUIDE.md`.
5. Administrator, roles and workflow guide — `docs/ADMIN_AND_WORKFLOWS.md`.
6. This test report — `docs/FINAL_DELIVERY_REPORT.md`.
