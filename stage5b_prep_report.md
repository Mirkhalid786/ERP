# Stage 5B Preparation Report: Transaction Readiness

Date: 22 Oct 2026. Live DB is at Alembic **008**. **Accounting is still NOT initialised on live**: 0 ledger accounts, no books start date. **Nothing is posted to the GL.** Stage 5B integration and Phase 6 have not started.

> Passing tests do not mean production-ready. Review the cutover plan with your CA.

## 1. New workflows

### Supplier invoices: DRAFT → POSTED (finalised) → CANCELLED
- **Drafts** can be edited (`PUT /purchases/invoices/{id}`). Lines and totals are recomputed, and the audit log records before/after values.
- **Finalise check** (`GET …/finalize-check`) reports errors and warnings. **Finalise** (`POST …/finalize`) requires the warnings to be acknowledged and the new permission `purchases.invoice_finalize`.
  - **Errors block finalising:**
    - no lines;
    - a future invoice date, or a due date before the invoice date;
    - stored total ≠ sum of lines;
    - line tax inconsistencies (CGST/SGST/IGST);
    - difference from the supplier's printed total above the tolerance (setting `purchases.invoice_discrepancy_tolerance`, ₹1.00) with no explanation;
    - linked GRN not posted;
    - more billed than received on a GRN line, across all finalised bills.
  - **Warnings need acknowledgement:**
    - no GRN linked;
    - a GRN already billed elsewhere;
    - rate differs from the GRN rate;
    - received quantity not fully billed;
    - an explained discrepancy (the ERP-calculated total is the amount payable).
- **Duplicate numbers:** a unique key on (supplier, financial year, number with spaces and punctuation removed). So "GST/1001", "gst-1001" and "GST 1001" count as the same bill, while a new financial year allows the number again.
- **Cancel:** needs a reason of at least 10 characters. A draft needs `purchases.create`; a finalised bill needs the new `purchases.invoice_cancel`.
  - Refused while payments are allocated to the bill or posted returns refer to it.
  - The original is kept. Its number is freed so the corrected bill can be re-entered with `replaces_invoice_id`.
- The old `PATCH …/payment` (overwrote `paid_amount`) now returns **410 Gone**.

### Supplier payments (`/supplier-payments`)
- Records are dated and numbered (`SP/26-27/00001`). Each holds the method (bank/UPI/cheque/cash/card), the cash source (POS drawer of an open shift, or office cash), reference, bank account, notes and the user who recorded it.
- Payments are allocated to finalised bills only: explicitly, or oldest-due first. Any remainder is an advance, which can be allocated later.
- `supplier_invoices.paid_amount` / `payment_status` are now a cache recomputed from active allocations.
- **Outstanding** = bill total − active allocations − posted returns against the bill.
- **Duplicate prevention:**
  - an idempotency key (a replay returns the original; reusing a key with different data → 409);
  - a unique reference per supplier and method, released on reversal;
  - same-day same-amount payments without a reference need explicit confirmation.
- **Reversal** needs `supplier_payments.reverse` and a reason. It reverses the allocations and restores the outstanding amounts. A second reversal → 409.
- **Balances** (`/balances`): bills − returns − payments. **Reconciliation** (`/reconciliation`) cross-checks caches, allocations and per-supplier totals.

### Expenses (`/expenses`)
- 13 default categories, each with a suggested GL account code for 5B. Categories can be managed (`expenses.configure`).
- Each expense records date, total (GST included, with the GST part shown separately), payee, payee GSTIN, bill number, method, cash source and reference. Bank, UPI and cheque payments require a reference.
- **Approval:** an approver's own entry is APPROVED immediately; others' entries go to PENDING_APPROVAL, which an approver approves or rejects (rejection needs a note). Only APPROVED expenses count as spent.
- Approved expenses can be reversed with a reason. Nothing is ever edited or deleted.
- Paying from the drawer needs `expenses.approve` plus the payer's open shift, and must be dated today.
- Duplicate check: same payee with the same bill number, or the same amount and date, unless confirmed.

### Cash-shift movements
- Paid-outs and paid-ins now require a category:
  - **Out:** bank deposit, owner withdrawal, to safe, float out, correction, other.
  - **In:** float in, owner introduced, from safe, bank withdrawal, correction, other.
- Bank deposits and withdrawals need a slip reference. "Other" needs a reason of at least 10 characters. `authorised_by` is recorded.
- Expenses and supplier payments are **not** cash-movement categories. They come into the shift from their own records, as separate lines ("supplier payments from drawer", "expenses paid from drawer"), so nothing is counted twice.
- A reversal after the shift closed leaves the closed shift's figures unchanged.
- Old movements are UNCATEGORISED and can be classified once (including "petty expense before the Expenses module"). Their amount is untouched and no new record is created.

### Screens
- New **Payables** page: bills (check & finalise, pay, cancel), payments (record with an allocation grid, allocate advance, reverse), balances and reconciliation.
- New **Expenses** page: record, approve/reject/reverse, categories.
- **Day-end:** category, reference and classify.
- **Purchasing:** the bill column shows status; the overwrite "Pay" modal is replaced by a link to Payables.
- The navbar has one "Payables" item that covers both pages.

### Permissions (migration 008)
- **Owner:** all 10 new permissions.
- **Inventory Manager:** finalise bills, view payments, record expenses (pending approval).
- **Pharmacist:** view and record expenses.
- **Cashier:** record expenses only.

## 2. Migration 008 and backups
- **Backups:**
  - `backups/pharmacy_erp_pre_5b_prep_20261022_230250.sql` and `pharmacy_erp_pre_008_20261022_234029.sql` (taken just before the live migration).
  - Both were restored into isolated databases (`pharmacy_erp_restore_5b`, `pharmacy_erp_restore_008`): all 50 tables have identical row counts, and the money, stock and audit checksums match.
- **What 008 does:**
  - adds the new columns and tables;
  - backfills `number_key`; a clash gets a `#id` suffix and is printed;
  - preserves any non-zero `paid_amount` as a LEGACY payment plus allocation (live had none);
  - backfills `authorised_by` on cash movements;
  - seeds the expense categories and permissions.
- **Tested on the isolated copy** with synthetic legacy rows: upgrade → downgrade → upgrade. `paid_amount` is preserved throughout.
- **Live after 008:** sales, payments, khata, stock movements, GRNs, supplier bills, cash shifts and movements, and returns are **row-for-row identical** to before (SHA-256 compared). Only `permissions` (+10) and `role_permissions` (+17) changed.

## 3. Tests
- **Backend (SQLite):** 184 passed, 19 skipped (the MySQL tests). This includes 15 new tests in `tests/test_payables.py`:
  - workflow, edit lock, overbilling, dates, discrepancy, unposted GRN;
  - duplicate numbers;
  - cancel/re-entry;
  - returns;
  - partial payments, advances and allocation;
  - duplicate payments;
  - reversal;
  - drawer cash;
  - permissions;
  - expenses (approval, reversal, validation, duplicates);
  - cash categories and legacy classification;
  - historical summary (read-only).
  - Existing tests that used the retired endpoint were updated.
- **MySQL 8.4:** 6 new concurrency tests in `tests/mysql/test_mysql_payables.py` passed:
  - concurrent over-allocation;
  - same payment submitted 4 times, and the same reference raced;
  - the same bill number in 4 spellings;
  - concurrent finalise over one GRN;
  - concurrent reversal;
  - DB CHECKs.
  - The earlier MySQL suite passed as well.
  - A bug found here was fixed: an expired attribute was read after a savepoint rollback.
- A final full re-run was started after the last small change (stock-valuation fix in the summary); its result is not in this report.
- **Frontend:** `tsc` is clean, and the Docker production build succeeded.
- **Browser, on the isolated copy:**
  - the finalise check blocked a bill with no lines;
  - a bill matched to its GRN finalised;
  - a ₹200 partial UPI payment → bill partially paid, ₹248 owed;
  - balances and reconciliation agree;
  - an expense recorded via the UI;
  - the old paid-out was classified, and the closed shift's figures did not change.

## 4. Historical transactions on live (all TEST data, 21–22 Oct 2026 IST)
| Item | Count | Total |
|---|---|---|
| Sales invoices (finalised) | 4 (3 to TEST customer, 2 walk-in… see note) | ₹1,390.00 (GST ₹148.92, cost ₹840) |
| Customer payments | cash 4 (₹830), UPI 2 (₹360, unverified) | ₹1,190.00 |
| Sales return | 1 (applied to khata) | ₹100.00 |
| Khata outstanding | TEST Customer Ramesh | ₹100.00 |
| GRNs (posted, supplier bill "TEST-DEMO") | 4 | ₹3,472.00 (GST ₹372) |
| Supplier invoices / payments / returns | 0 / 0 / 0 | — |
| Cash shifts | 1 closed (variance −₹5), **1 open since 22 Oct 23:15 IST** | — |
| Cash paid-out (uncategorised courier) | 1 | ₹50.00 |
| Stock on hand | 540 base units | ₹2,319.99 at cost |

Note: invoices CCP/00001 and CCP/00004 are walk-in sales made during the TEST scenarios. Test masters: 1 customer, 1 supplier, 3 medicines, all named TEST. The read-only endpoint `GET /api/v1/accounting/historical-summary` reproduces this inventory.

## 5. Outstanding blockers (before automatic 5B postings)
1. **The 4 TEST GRNs have no supplier bills.** They will sit in GRNI or need a decision. Recommended: leave them unposted and exclude them by cut-over date.
2. **UPI/card receipts are not verified against settlement.** A clearing-account policy is needed.
3. **GRN cost allocation over rejected packs and the weighted-average cost policy** are still open (from 5A).
4. **No unpaid-expense (accrual) bills.** Expenses are recorded as paid only.
5. **No price-only debit/credit notes from suppliers.** Only stock returns exist.
6. **Supplier payments still in transit** (cheque issued but not cleared) are not tracked.
7. **The live open shift** should be closed normally before any cut-over.

## 6. Proposed cutover plan (for approval, not executed)
1. Agree with your CA on a clean **books start date** (suggest 1 Nov 2026, or the next month after go-live) and **do not backfill** the 21–22 Oct TEST transactions into the GL. They stay as operational history.
2. Before that date, stop using TEST masters for real work. Optionally deactivate the TEST customer, supplier and medicines; nothing is deleted.
3. On the start date, close all shifts and count stock. Enter **opening balances** from the previous books: cash, bank, receivables per customer with documents, payables per supplier with bill numbers, stock at cost reconciled to the ERP stock value, GST balances, and capital.
4. Post opening balances (5A workflow); the trial balance must balance.
5. From that date, Stage 5B posts each sale, receipt, return, finalised supplier bill, supplier payment, approved expense and cash movement idempotently, by its source key.
6. **Daily reconciliation:**
   - khata vs AR;
   - supplier balances vs AP;
   - drawer cash vs the cash account;
   - stock value vs inventory.

**Waiting for your approval before starting Stage 5B.**
