# Pharmacy ERP — Roadmap Completion Report

Date: 23 Oct 2026 · Roadmap source: `pharmacy_erp_architect_report.md` (Phases 1–8) plus the stage plan in
`phase5a_accounting_report.md` (5A–5D).

**Not done without your authorisation:**
- Live accounting has not been initialised.
- No books start date has been chosen and no opening balances posted.
- No historical TEST transaction has been posted.
- No live record was changed.
- **Migrations 009 and 010 have not been applied to live**, and nothing was deployed or restarted.

> ⚠️ **Act on this first.** The live `backend` container mounts the source folder and runs `alembic upgrade head` whenever
> it starts. It is still running the code it loaded before this work began. **Any restart of the container (Docker
> Desktop restart, PC reboot, crash) would load the new code and apply migrations 009 and 010 to live automatically.**
> Until you decide, avoid restarting Docker or the PC. To prevent it permanently, approve one of:
> (a) a planned deployment (§7), or (b) a change to the dev compose file so it no longer migrates on start.

## 1. Phases and key deliverables
| Phase | Status | Key deliverables |
|---|---|---|
| 1–4, 4.1 | Done earlier | Auth, RBAC, CSRF; catalogue, units, batches; purchasing; POS, credit, returns, shifts, WhatsApp; users |
| 5A | Done earlier | GL, chart of accounts, journals, periods, opening balances, trial balance, integrity hash |
| 5B prep | Done earlier | Supplier bill finalise/cancel, dated supplier payments, expenses, categorised cash movements |
| **5B** | **Done** | Automatic idempotent postings for every finalised document (`gl_posting.py`, 22 source types incl. settlements and supplier credit notes); failure, retry and reconciliation; per-document ledger badge (posted / not yet posted / failed) on invoices, bills, payments and expenses |
| **5C** | **Done** | P&L, balance sheet (advances reclassified, balance check), cash flow (direct, reconciles), day book, cash/bank book, GST from the ledger, payables ageing; CSV export |
| **5D** | **Done** | UPI/card settlement to the bank net of fees; bank statement import (duplicate-safe), auto and manual matching, reconciliation report; year-end close and reopen (closing entry to retained earnings, next FY); CA export pack (ZIP) |
| **Phase 6** | **Done** | Purchase summary and input GST by rate; expiry alerts with stock value; audit-log viewer; in-app backups (consistent snapshot, SHA-256, history, verify with optional test restore, download, optional daily schedule in the worker); CLI backup and verify-restore scripts; general Settings screen (validated, audited); **supplier credit notes** (rate difference, scheme, claims) |
| **Phase 7** | **Done (docs + config)** | `docker-compose.prod.yml` (baked images, `next start`, DB not exposed, shared backups volume, LAN bind); `frontend/Dockerfile.prod`; `docs/DEPLOYMENT.md` (Windows/Linux, autostart, NSSM, LAN/firewall, update, backup and restore, external services, go-live); System health page (DB, migrations, disk, last backup, worker heartbeat, outbox, postings); first-run wizard (existing) |
| **Phase 8** | **Prepared** | Load and pilot data script (`backend/scripts/load_test_data.py`, staging/test databases only); FY-rollover numbering test; concurrency tests; backup and restore test; `docs/FINAL_VERIFICATION.md`. The human pilot (real pharmacist, hardware) is yours to run. |

## 2. Remaining incomplete items
1. **Stock adjustment approval** (the roadmap's draft → approved → posted). Adjustments still post immediately for users with `stock.adjust`; they are audited and GL-posted. A two-person approval step was not added because it would change an existing workflow. I recommend deciding on it in the review.
2. **Hardware and external services** cannot be verified here: WhatsApp Cloud API (needs credentials), thermal/A4 printers, USB barcode scanners. These are documented in DEPLOYMENT.md §8. The app never claims delivery or printing beyond what the provider or browser reports.
3. **Windows service without Docker** is documented (NSSM), not scripted.
4. **Pre-existing index and comment drift** on Phase 1–3 tables (16 index differences, mostly missing secondary indexes on `stock_movements` and `medicine_batches`). This is not a correctness issue; an index migration can be added after review.

## 3. Important files and migrations (this round)
- **Backend:**
  - services: `gl_posting.py`, `financial_reports.py`, `accounting_ops.py`, `admin_ops.py`, `purchase_reports.py`, `supplier_credit_notes.py`, `cutover.py`
  - routes: `admin.py`, plus additions to `accounting.py`, `payables.py` and `auth.py` (general settings)
  - models: `admin.py` (BackupRecord), plus additions to `accounting.py` and `payables.py`
  - hooks in the sales, receivables, returns, purchasing, invoices, payments, expenses, shifts and inventory services; `worker.py` (heartbeat and scheduled backup)
- **Migrations:**
  - `009_..._stage5b_gl_postings.py`: `gl_posting_issues` and a permission.
  - `010_..._phase_5cd_6_7.py`: `payment_settlements`, `bank_statement_lines`, `backup_history`, `supplier_credit_notes` and 2 permissions.
  - Both add tables only, and are tested up → down → up on isolated restores.
- **Frontend:**
  - Accounts page: Financial statements, Bank & settlements, Automatic postings, Cutover, year-end buttons.
  - New pages: `/admin`, `/settings`, `/reports/purchases`.
  - Payables credit notes tab; ledger badges; expiry value; navbar entries.
- **Ops:** `docker-compose.prod.yml`, `frontend/Dockerfile.prod`, `scripts/backup.ps1`, `scripts/verify_restore.ps1`, `.dockerignore` updates.
- **Docs:** `docs/DEPLOYMENT.md`, `docs/CUTOVER_REPORT.md`, `docs/FINAL_VERIFICATION.md`, `stage5b_accounting_integration_report.md`.

## 4. Database migration status
| Database | Revision | Notes |
|---|---|---|
| **pharmacy_erp (LIVE)** | **008** | Accounting not set up; data unchanged (checksums identical before and after this work) |
| pharmacy_erp_restore_009 | 010 | Isolated restore of the 23 Oct backup; used for migration tests and the cutover checks |
| pharmacy_erp_migrate_check | 010 | Empty schema migrated 001 → 010 |
| pharmacy_erp_staging | (create_all) | Load data with books set up; used for the UI checks |
| pharmacy_erp_test / _test_restore_verify / _restore_verify | — | Disposable test and scratch databases |
| pharmacy_erp_restore_check / _5b / _008 | 007 / 010 / 008 | Older isolated restores, kept (not cleaned up without your OK) |

## 5. Verification actually run in this round
- **Backend + MySQL 8.4, single final run:** **227 passed**, exit 0.
  - One earlier MySQL run had 1 failure in a 5A period-close race test while a load script ran at the same time. It then passed 30+ isolated runs and a clean full rerun (22/22). The root cause was not captured, so this is listed as a risk.
- **Frontend:** `tsc` clean; `next build` compiled successfully.
- **Migrations:** 001 → 010 on an empty schema; 009 and 010 up/down/up on restored copies.
- **Backups:**
  - CLI dump of live (read-only) restored into a scratch DB: 54 tables, 0 differences.
  - The in-app backup test restores into a scratch schema.
  - UI backup and verify passed on staging.
- **Staging end-to-end:** 200 load sales with books set up gave a balanced trial balance, a balanced balance sheet, a reconciling cash flow, and all three control accounts agreeing; UI pages checked in the browser.
- **Defects found and fixed during verification:**
  - a backup failed on a schema without `alembic_version`;
  - a failed backup showed a bare 500;
  - the Windows backup scripts re-encoded files (BOM and quoting).

## 6. Risks and decisions for you
1. **The auto-migrate-on-restart risk** described at the top of this report.
2. **Books start date and opening balances.** See `docs/CUTOVER_REPORT.md` for the unresolved TEST items: the open shift, ₹360 of unverified UPI, 4 unbilled GRNs, an uncategorised ₹50 paid-out.
3. **CA review of posting rules:** input-GST pending policy, PPV vs inventory capitalisation, cash-movement and write-off accounts, year-end approach.
4. **Stock adjustment approval** (§2.1). Whether to clean up the old restore-test databases.
5. **Production switch:** move from `docker-compose.yml` (dev server, source mounts) to `docker-compose.prod.yml`. The dev compose worker does not share the backups volume, so scheduled backups made by it would not be downloadable from the UI.

## 7. Deployment steps (prepared, not executed — needs your approval)
1. Back up live and verify the restore (`scripts/backup.ps1`, `scripts/verify_restore.ps1`).
2. `docker compose -f docker-compose.prod.yml --env-file .env up -d --build`. Migrations 009 and 010 apply on backend start.
3. Check that System health is all OK, that live operational tables are row-for-row unchanged, and that accounting is still not set up.
4. Rollback: `alembic downgrade 008` (drops only the new tables and permissions), or restore the backup.

## 8. Final testing pass
Follow `docs/FINAL_VERIFICATION.md` (exact commands for suites, build, migrations, backup and restore, load data, the end-to-end checklist and reconciliation).

**The roadmap is complete. No further phase was started; nothing was deployed and the live books were not initialised.**
