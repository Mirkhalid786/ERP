# Phase 5 — Stage 5A Report: Accounting Foundation

Date: 22 Oct 2026 · Scope: **Stage 5A only**. Stage 5B (automatic postings from sales, purchases, payments, stock) has **not** started and waits for your authorisation. Phase 6 has not started.

> Tests passing doesn't make this production-ready. The chart of accounts, the opening-balance procedure and the period policy need review by your CA before real books are kept in this system.

---

## 1. Audit (before any change)

| Check | Result |
|---|---|
| Project state | Not a git repository, so there's no diff history. Changes are listed in section 4. |
| Baseline tests | SQLite suite: 155 passed. MySQL suite: 7 passed. Frontend build OK. |
| Alembic | Live DB at `006 (head)` before this stage. |
| Existing ledger-like data | `customer_ledger` (khata, signed running balance), `payments`, `sales_invoices`, `supplier_invoices` (DRAFT only), `goods_receipts`, `stock_movements`, cash shift movements. There was no general ledger, chart of accounts, journal or period model. |

### Accounting gaps found (blockers or decisions for Stage 5B)

1. **Supplier invoices never leave DRAFT.** There's no point at which the purchase liability (AP) is recognised, so 5B needs a "post supplier invoice" step.
2. **Supplier "payments" are a single overwritable `paid_amount`.** They have no dated payment record, method, bank or reference, so a supplier-payment model is needed before AP can be ledgered.
3. **There's no expenses module.** Rent, salaries and similar can only be entered as manual journals, which works in 5A.
4. **Cash paid-out / paid-in shift movements have no account or category.** These can't be posted automatically until a category → account mapping exists.
5. **GRN and supplier invoice aren't reconciled.** I added a GRNI account (Goods Received Not Invoiced, 2115) so 5B can post stock at the GRN and clear it at the invoice without double-counting.
6. **GRN cost allocation spreads charges over rejected packs, and weighted-average cost is recalculated on each GRN.** As a result, movement-based stock valuation can differ from batch valuation. The policy needs a decision before stock is ledgered.
7. **UPI and card receipts are "RECORDED" but not verified** against settlement. A clearing account per method is recommended in 5B.
8. **The existing TEST data (20–22 Oct 2026) predates accounting.** 5B needs an explicit backfill-or-cut-over decision. Nothing historical is altered automatically.

## 2. Backup and restore verification

- Backup: `backups/pharmacy_erp_pre_phase5_20261022_222807.sql` (116 KB, 43 tables), made with the existing mysqldump process.
- Restored into an isolated database `pharmacy_erp_restore_check`.
  - All 43 tables have the same row counts as the live database.
  - Spot checksums match: invoices 1390.00, customer ledger 100.00, stock 540.00, payments 1190.00, audit rows 39, alembic 006.
- The live database was never reset or overwritten. Its operational data is identical before and after migration 007.
- `pharmacy_erp_restore_check` was then used as the **staging copy** for browser testing. It now contains TEST accounting entries. It's isolated and safe to drop whenever you like.

## 3. Design

### Chart of accounts (62 accounts, seeded on setup)

| Range | Group | Notable accounts |
|---|---|---|
| 1000–1299 | Assets | Cash in Hand 1110, Bank 1121, UPI/Card clearing, **Trade Receivables 1140** (control, per customer), **Inventory 1150** (control), Input GST (CGST/SGST/IGST), Input GST pending 1164, **Supplier Advances** (control, per supplier), Prepaid 1180, Fixed assets 1210–1230 |
| 2000–2299 | Liabilities | **Trade Payables 2110** (control, per supplier), **GRNI 2115** (control), Output GST, **Customer Advances** (control, per customer), loans |
| 3000–3999 | Equity | Proprietor's Capital 3100, Drawings (debit-normal), Retained earnings, **Opening Balance Difference 3900** (suspense) |
| 4000–4399 | Income | Sales by GST rate, **Sales Returns 4200** (contra, debit-normal), Discounts, **Round-off 4320**, Misc income |
| 5000–5299 | Expenses | Cost of goods sold, expiry write-offs, rent, salaries, utilities, etc. |

- **System accounts** (with a `system_key`) can't be deactivated or deleted. 5B postings refer to them by key, never by code, so you may renumber or rename them.
- **Control accounts** (AR, AP, Inventory, GRNI, both advances accounts) are **barred from manual journals**. Only sub-ledger postings may touch them, which keeps the khata and supplier balances and the GL in agreement.
- **Party-typed accounts** require a customer or supplier on every line.
- User accounts can be added under any group. Accounts can't be deleted once used, and codes can't change after postings.

### Periods and fiscal years

- Indian fiscal year (April–March), split into monthly periods with statuses OPEN, CLOSED and PRE_OPENING.
- Months before the books start date are PRE_OPENING: nothing can be posted there.
- **Close** is refused while draft journals exist in the period. **Reopen** needs a separate permission and a written reason of at least 10 characters. Both are audited and show who did it and when.
- Postings take a **shared lock** on their period row and a close takes an **exclusive lock**, so a close waits for in-flight postings and later postings are refused. This was tested under real MySQL concurrency.
- Draft saves check the period as well. Verification found that drafts could be saved into a closed period; this is fixed, with a test.

### Journals: balancing, idempotency and immutability

- **States:** DRAFT → POSTED → REVERSED.
  - Posted entries are never edited or deleted.
  - A correction is a **reversal**: a mirror entry with its own number, linked both ways, with a mandatory reason. The reversal is posted and the original is marked REVERSED.
  - Reversing an entry twice is refused.
- **Numbering:** `JV/26-27/00001`, gap-free per fiscal year, using the existing locked document-number sequence.
- **Balancing is enforced three times:**
  1. by the service before posting;
  2. by re-summing the stored lines inside the transaction;
  3. by **MySQL CHECK constraints**: header `total_debit = total_credit`, and every line one-sided and non-negative. MySQL rejected a hand-written unbalanced insert with error 3819.
- **Idempotency:** each posting carries a `source_key` (for example `OPENING:1`, `REVERSAL:2`, `MANUAL:<uuid>`, and in 5B `SALE:123`), with a **unique index per pharmacy**.
  - A replay returns the existing entry.
  - A concurrent duplicate is blocked by the index. Tested with four concurrent sessions posting one key: exactly one entry.
- **Integrity hash:** a SHA-256 over the header and lines is stored at posting. The **Integrity check** recomputes every hash and the overall Dr = Cr balance, so it detects tampering. A test edits a row directly and confirms it's flagged.
- **Not done:** DB triggers that would forbid UPDATE/DELETE on posted rows. They need `log_bin_trust_function_creators` on MySQL. Hash checking covers detection; triggers are an option if you want DB-level prevention.

### Opening balances

- There's one batch per pharmacy: DRAFT → (preview) → POSTED, posted as one opening journal dated on the books start date.
- **The preview refuses:**
  - an imbalance, unless you explicitly choose to send the difference to the suspense account 3900, labelled "UNRESOLVED opening balance difference";
  - income or expense accounts;
  - lump-sum receivable/payable lines without a document number and date;
  - customers or suppliers who already have ERP activity before the opening date, because that would double-count.
- **Reconciliation:** the entered inventory is compared with the ERP stock value at cost on the opening date. Differences are warnings that must be acknowledged before posting.
- A posted batch is locked. Correcting it means reversing the opening journal, which needs the opening-balance permission too.

### Permissions (new; granted to *Owner / Admin* only)

`accounting.view`, `accounts_manage`, `journal_create`, `journal_post`, `journal_reverse`, `opening_balances`, `period_close`, `period_reopen`, `export`.

- Pharmacists and cashiers get 403 (tested).
- Creating and posting are separate permissions, so a maker/checker split is possible.
- CSV export of the trial balance is audited.

## 4. What was built

| Layer | Files |
|---|---|
| Models | `backend/app/models/accounting.py`: LedgerAccount, FiscalYear, AccountingPeriod, JournalEntry, JournalLine, OpeningBalanceBatch, OpeningBalanceLine |
| Services | `backend/app/services/ledger.py` (chart, periods, posting engine, reversal, trial balance, account ledger, integrity) and `backend/app/services/opening_balances.py` |
| API | `backend/app/api/v1/routes/accounting.py`, under `/api/v1/accounting/*`: status, setup, accounts CRUD, periods and fiscal years, close/reopen, journals (draft/edit/delete/post/reverse), opening balances (save/preview/post), trial balance (+CSV), account ledger, integrity |
| Schemas | `backend/app/schemas/accounting.py` |
| Migration | `backend/alembic/versions/007_20261022_phase5a_accounting.py`: 7 tables and permission seed. Upgrade → downgrade → upgrade verified on MySQL. |
| Modified | `services/auth.py` (permissions), `services/app_settings.py` (`accounting.books_start_date`), `api/v1/router.py`, `models/__init__.py` |
| Frontend | `frontend/app/accounting/page.tsx` (Accounts screen: setup, chart, journals, periods, opening balances, trial balance with ledger drill-down, integrity), `frontend/lib/accounting-api.ts`, Navbar "Accounts" item |
| Tests | `backend/tests/test_accounting.py` (14 tests) and `backend/tests/mysql/test_mysql_accounting.py` (6 tests) |

**Reused existing features:** auth/RBAC (`require_context`), CSRF, audit log, the locked document-number sequence, business-timezone clock helpers, exact decimal JSON serialisation, customer and supplier masters as parties, and stock movements for the inventory reconciliation.

## 5. Test results

| Suite | Result |
|---|---|
| Backend SQLite | **169 passed**, 13 skipped (the MySQL tests skip without `TEST_MYSQL_URL`) |
| Backend MySQL 8.4 (`pharmacy_erp_test`) | **13 passed**, including the 6 new accounting concurrency tests |
| Frontend | `tsc` clean; production build OK |

**What the MySQL tests cover:**
- the same draft posted three times concurrently → one 200 and two 409s;
- one source key from four sessions → exactly one entry;
- six concurrent posts → numbers 00001–00006 with no gaps or duplicates;
- period close racing drafts and direct postings (three runs) → no posting ever lands in a closed period after its close time;
- the database rejects unbalanced headers and two-sided lines.

**Browser verification on the staging copy** (TEST data only, never the live database):
1. Setup with books start date 01-Oct-2026 → 62 accounts; Apr–Sep are PRE_OPENING.
2. Opening balances: the preview caught a ₹500 imbalance. After correction (Cash 15,000, Bank 50,000, receivable from TEST customer 500 against a document, Capital 65,500), it posted as `JV/26-27/00001`. The inventory reconciliation showed ERP ₹0 = entered ₹0.
3. Manual journal: Rent 3,000 Dr / Bank 3,000 Cr, posted as `JV/26-27/00002`. Control accounts aren't offered in the account list.
4. Trial balance: balanced. Closing Dr = Cr = ₹65,500.
5. Closing Oct 2026 blocked new journals in it (409). A reopen with a short reason was refused (422); a reopen with a proper reason succeeded and was recorded.
6. Reversal of JV/00002 created `JV/26-27/00003`. A second reversal was refused (409). The trial balance stayed balanced.
7. Integrity check: 3 entries and 8 lines checked, Dr = Cr = ₹71,500, no problems.

## 6. Live deployment state

- The live database is at migration **007 (head)**. The new tables exist but are **empty**. Operational data is unchanged.
- **Accounting is NOT set up on the live database.** The books start date and the opening balances are a business decision for you and your CA.
  - To start, open **Accounts → Set up accounting**, choose the books start date, then enter the closing balances from your previous books under **Opening balances**.
- The backend and worker were restarted and the frontend was rebuilt with the final code.

## 7. Risks and items for CA review

1. **Chart of accounts:** check the names, codes and GST sub-accounts against how your CA files returns.
2. **Books start date and cut-over:** the existing TEST sales and purchases from 20–22 Oct 2026 aren't in the GL. Decide whether real books start after a clean cut-over or whether history gets backfilled in 5B.
3. **Inventory valuation policy:** weighted average vs batch cost, and the GRN cost allocation (gap 6).
4. **Suspense usage:** any balance left in 3900 must be cleared before the year closes. The UI labels it "UNRESOLVED".
5. **No DB-level immutability triggers** (see section 3). Tampering is detected by the integrity check, not prevented.
6. **Single-currency (INR) and single-pharmacy postings.** Multi-branch consolidation is out of scope.
7. **Year-end closing** (profit to retained earnings) and the P&L and balance sheet reports belong to later stages.

## 8. Remaining stages (proposed — not started)

- **5B: automatic postings.** Posting rules for sales, returns, voids, receipts, credit/khata, supplier invoice posting, supplier payments, GRN/GRNI, stock adjustments and expiry, shift cash in/out. Each uses an idempotent source key. Includes a backfill/cut-over tool and khata ↔ AR reconciliation.
- **5C: financial reports.** P&L, balance sheet, cash and bank book, day book, GST summaries from the ledger, ageing.
- **5D: bank reconciliation, year-end close and the CA export pack.**

**Awaiting your authorisation before starting Stage 5B.**
