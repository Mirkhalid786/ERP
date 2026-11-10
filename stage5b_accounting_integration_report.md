# Stage 5B Report: Accounting Integration

Date: 23 Oct 2026 · Live DB still at Alembic **008**; migration 009 is ready but **not applied** (awaiting approval). **Live accounting is still NOT set up**: no books start date, no chart, no journals, no opening balances. No historical TEST transaction was posted. Stage 5C, 5D and Phase 6 have not started.

> Passing tests do not mean production-ready. The posting rules below need your CA's review before the books go live.

## 1. Posting rules and accounts

Each event posts **one** balanced journal, keyed by its source (for example `SALE:12`). The posting happens in the same transaction as the business change, through the 5A `ledger.post_entry`. Nothing posts while the books are not set up, or for documents dated before the books start date; those belong in the opening balances.

| Event (source key) | Debit | Credit |
|---|---|---|
| **Sale** (`SALE`) | Cash 1110 / UPI clearing 1131 / Card clearing 1132 / Bank 1121 / Other 1133 for money taken at the sale; **AR 1140 (customer)** for the unpaid part; COGS 5100 at stock-movement cost | Sales 4100 (taxable); Output CGST/SGST/IGST 2121–2123; Round-off 4320; Inventory 1150 |
| **Customer collection** (`CUSTOMER_RECEIPT`) | Cash / clearing / bank | AR (customer). Revenue is not recognised again. |
| **Receipt reversal** (`CUSTOMER_RECEIPT_REVERSAL`) | AR (customer) | Cash / clearing |
| **Sales return** (`SALES_RETURN`) | Sales Returns 4200, Output GST, round-off; Inventory for restocked items (at cost); Inventory Write-offs 5150 for damaged items | AR (customer), or Refunds Payable 2160 for walk-in; COGS |
| **Void** (`SALE_VOID`) | Sales, Output GST, round-off; Inventory | AR (customer) or Refunds Payable; COGS |
| **Customer refund** (`CUSTOMER_REFUND`) | AR (customer) or Refunds Payable | Cash / clearing |
| **Write-off / goodwill adjustment** (`CUSTOMER_ADJUSTMENT`) | Bad Debts 5180 / Discounts Allowed 4110 | AR (customer) |
| **Goods receipt** (`GOODS_RECEIPT`) | Inventory (accepted qty × cost) | GRNI 2115 |
| **Supplier bill finalised** (`SUPPLIER_BILL`) | GRNI (the GRN cost of the billed lines); Purchase Price Variance 5110 (bill vs GRN cost, freight and other charges); Input GST Pending 1164; round-off | **AP 2110 (supplier)** |
| **Bill with no GRN line** | GRNI (it stays debit until the goods are received) | AP |
| **Finalised bill cancelled** (`SUPPLIER_BILL_CANCEL`) | Mirror of the bill entry; the original is marked REVERSED | |
| **Supplier return / debit note** (`SUPPLIER_RETURN`) | AP (supplier) | Inventory (at cost), Input GST Pending, variance |
| **Supplier payment** (`SUPPLIER_PAYMENT`): full, partial, split or advance | AP (supplier) | Cash (drawer or office) / Bank (bank transfer, UPI, cheque, card) |
| **Payment reversal** (`SUPPLIER_PAYMENT_REVERSAL`) | Mirror, original marked REVERSED | |
| **Approved expense** (`EXPENSE`) | The category's account (5210 rent, 5230 electricity …; default 5290); Input GST Pending for the GST part | Cash / Bank |
| **Expense reversal** (`EXPENSE_REVERSAL`) | Mirror, original marked REVERSED | |
| **Cash movement** (`CASH_MOVEMENT`) | Bank deposit: Bank / Cash. Withdrawal: Cash / Bank. Owner out: Drawings 3200 / Cash. Owner in: Cash / Capital 3100. Correction / other: Cash Shortages & Excess 5170 ↔ Cash | Drawer ↔ safe and float transfers: **no entry** (cash stays in Cash in Hand) |
| **Shift count difference** (`SHIFT_VARIANCE`) | Cash (excess) or 5170 (shortage) | the other |
| **Stock adjustment** (`STOCK_ADJUSTMENT`) | Inventory ± | Write-offs 5150 (expiry, damage, breakage, theft) or Stock Adjustment 5160 |

**Why this cannot double-count:**
- Customer money always runs through AR per customer, exactly like the khata. Advances and credit notes are credit balances on AR, and applying them later moves nothing.
- Supplier money runs through AP per supplier, matching the supplier balance. Allocations move nothing.
- Goods receipt and supplier bill meet in GRNI.
- Drawer-paid expenses and supplier payments post once, from their own records. Paid-outs cannot be used for expenses.
- Payments taken at a sale are part of the sale entry and are not posted separately.

**Not posted:**
- Drafts, cancelled-before-finalising bills, pending or rejected expenses.
- OPENING_STOCK movements; these are covered by the opening balances.
- Legacy migrated payments and uncategorised pre-categories cash movements dated before the books start.

**New system accounts**, added to the default chart and created when accounting is set up: 4110 Discounts Allowed, 5110 Purchase Price Variance, 5170 Cash Shortages & Excess, 5180 Bad Debts Written Off.

## 2. Reliability and recovery
- **Idempotency:** a unique `source_key` per pharmacy. A replay returns the existing entry; a concurrent duplicate is blocked by the DB.
  - On MySQL, 6 concurrent sales plus a double-click produced exactly 6 journals.
  - 4 concurrent retries of the same event posted it once.
- **Failures never block a sale or payment, and are never dropped.** The journal is rolled back to a savepoint, and an issue is recorded in `gl_posting_issues` with the error, attempt count and an audit entry. Typical causes: a closed period, a missing account, or inconsistent document totals.
- **Retry:** `POST /accounting/postings/retry` (permission `accounting.postings_manage`, Owner only) re-posts open issues and documents found without a journal. It is idempotent, and originals are posted before their reversals.
- **Reconciliation:** `GET /accounting/postings/reconciliation` reports:
  - every eligible document without its journal;
  - documents whose current amounts no longer match the posted journal (detected by fingerprint; such a document is never re-posted silently);
  - open issues;
  - control checks: AR vs khata per customer, AP vs supplier balance per supplier, Inventory vs ERP stock value.
- **Corrections** stay as audited reversals or counter-entries. Posted entries are never edited.
- **UI:** the Accounts page has a new **Automatic postings** tab (issues, missing, mismatched, control checks, retry) and a **Cutover summary**. The cutover summary is also shown before setup.

## 3. Files and migrations
- **New:**
  - `app/services/gl_posting.py` (rules, posting, issues, reconciliation, retry)
  - `alembic/versions/009_20261023_stage5b_gl_postings.py` (`gl_posting_issues` table and permission; tested upgrade → downgrade → upgrade on an isolated restore)
  - `tests/test_gl_posting.py` (8)
  - `tests/mysql/test_mysql_gl_posting.py` (2)
- **Changed:**
  - `models/accounting.py` (GlPostingIssue)
  - `services/ledger.py` (4 accounts)
  - hooks in `sales.py`, `receivables.py`, `sales_returns.py`, `purchasing.py`, `supplier_invoices.py`, `supplier_payments.py`, `expenses.py`, `shifts.py`, `inventory.py`
  - `services/cutover.py` (customer and supplier balances, cash, bank note, GRNI estimate)
  - `services/auth.py`
  - `routes/accounting.py`
  - frontend `app/accounting/page.tsx` and `lib/accounting-api.ts`; notice text on the Payables and Expenses pages
- **Backup before 009:** `backups/pharmacy_erp_pre_009_20261023_165936.sql`, restored into `pharmacy_erp_restore_009`: 54 tables, 338 rows, checksums identical.

## 4. Test results
All runs exited with code 0.

| Check | Result |
|---|---|
| Backend full suite (SQLite), final run | **195 passed**, 21 skipped (the MySQL tests, run separately) |
| MySQL 8.4 suite (`pharmacy_erp_test`) | **21 passed**, including 2 new GL concurrency tests |
| Frontend `tsc --noEmit` | clean |
| Frontend production build (`next build`) | compiled successfully |
| Migration 009 vs model (autogenerate compare on the restored copy) | 0 differences for `gl_posting_issues` |

**End-to-end HTTP tests** (`tests/test_gl_posting.py`, 11) cover:
- cash, credit and UPI sales; collections and advances; credit application (no GL movement);
- returns with refund or credit note; voids with refund or kept as credit;
- receipt reversal and write-off;
- GRN → bill → partial and split supplier payments; a bill without a GRN; finalised-bill cancellation; supplier return; cheque bounce;
- expense with GST, plus reversal; drawer expense;
- bank deposit; drawer ↔ safe moving nothing in the GL; shift shortage; expiry adjustment;
- drafts, pending and rejected expenses, and cancelled drafts never posting;
- reversals linked both ways and audited;
- a closed-period failure that still lets the sale through, then retry after reopening;
- replays and retries never duplicating; tampering detected and never re-posted;
- nothing posted before setup;
- the cutover summary being read-only.

**Live read-only inspection** (inside a `READ ONLY` transaction, rolled back):
- 4 sales (₹1,390), 6 customer receipts (₹1,190), 1 return, 4 GRNs (₹3,099.99 at cost, none billed).
- 1 uncategorised paid-out of ₹50; khata ₹100; stock ₹2,319.99.
- 1 open shift (#2).
- **0 ledger accounts, 0 journals, 0 opening batches, 0 fiscal years, no books start date.**

## 5. Reconciliation results and known limitations
- **Live:** accounting is not set up, so reconciliation reports "not set up" and there is nothing to post.
- **Hand-checked trial balance** from the end-to-end scenario:
  - Cash ₹50 = sale 84 + collection 50 − return refund 42 − bank deposit 40 − shortage 2; the void refund and the reversed drawer expense net out.
  - Inventory ₹170 = 300 − 180 sold + 30 voided + 30 returned − 10 expired.
  - Output GST ₹18.
  - AR ₹56, equal to the khata.
  - AP 0, GRNI 0.
- **Limitations:**
  1. UPI and card receipts sit in the clearing accounts (1131/1132) until a settlement or bank-reconciliation step exists (Stage 5D).
  2. All input GST goes to "Pending Eligibility Review" (1164); moving it to CGST/SGST/IGST after a GSTR-2B match is a manual journal today.
  3. Customer advances are credit balances in AR, not reclassified to 2130. Supplier advances are debit balances in AP. Reclassification belongs to balance-sheet reporting (5C).
  4. Purchase Price Variance takes the bill/GRN cost differences. Stock and COGS stay at the ERP's weighted-average cost; landed-cost capitalisation is not done.
  5. A void or return after the books start of a sale made before it reverses revenue in the current period. This is correct accounting, but worth knowing.
  6. Only single-pharmacy, INR postings. No P&L / balance-sheet reports yet (5C).
  7. Postings are synchronous; a very large backlog retry runs in one request.

## 6. Decisions still required before live cutover
1. **Books start date.** Recommended: after the TEST data (for example 1 Nov 2026) and before the first real transaction. Everything dated on or after it will post automatically.
2. **Opening balances** from your previous books / CA: cash, bank, customers (per customer and document), suppliers (per supplier and bill), stock at cost (reconciled to the ERP), GST balances, capital.
3. **The 4 TEST goods receipts** (₹3,099.99 at cost) have no supplier bill. Recommended: keep them out of the books by choosing a later start date. If they are real goods, include them in the opening stock and in GRNI or AP.
4. **TEST masters and data:** leave the history as it is; optionally deactivate the TEST customer, supplier and medicines. Nothing is deleted.
5. **CA review** of the posting rules: input-GST pending policy, PPV vs inventory capitalisation, write-off reasons, and owner cash movements.
6. **Close the open live cash shift** (opened 22 Oct 23:15) before the start date.

## 7. Deployment steps (prepared, NOT executed)
1. Fresh backup and restore check: `mysqldump` → `backups/pharmacy_erp_pre_009_<time>.sql`, restored into an isolated database, with row counts and checksums compared against live.
2. `docker compose restart backend worker`. The backend runs `alembic upgrade head` on start: 008 → 009 creates `gl_posting_issues` and the permission. The restart is a few seconds of API downtime; the open cash shift is unaffected.
3. `docker compose up -d --build frontend`.
4. Verify:
   - every operational table is row-for-row identical to the backup (SHA-256 compare);
   - only `permissions` (+1) and `role_permissions` (+1) changed;
   - Alembic is at 009, and accounting is still not set up.
5. Rollback if needed: `alembic downgrade 008` (drops only the new table and permission), or restore the backup.

Because live accounting is not set up, every hook returns "skipped" after the deploy, and nothing is posted until you approve the books start date.

**Stopped after Stage 5B. Waiting for your approval to deploy.**
