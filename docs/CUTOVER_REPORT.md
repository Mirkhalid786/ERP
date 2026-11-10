# Read-only Cutover Report — live operational data (23 Oct 2026)

**How this was produced:** read-only queries inside `START TRANSACTION READ ONLY` sessions. Most checks ran
against `pharmacy_erp_restore_009`, an isolated restore of the 23 Oct backup. Before using it, I confirmed that the
operational tables in that copy match live row for row (18 tables compared, 0 differences) and that the money,
stock and audit checksums are identical. **Nothing was posted, created or changed in the live database.**

## Live status
- Alembic revision **008**. Migrations 009 and 010 are written, tested on restored copies, and **not applied to live**.
- **Accounting is not set up:** 0 ledger accounts, 0 journals, 0 opening batches, no books start date.

## Operational activity (all TEST data, 21–22 Oct 2026, IST)
| Area | Count | Total |
|---|---|---|
| Sales invoices (finalised) | 4 (3 marked TEST, plus walk-in) | ₹1,390.00 (GST ₹148.92, cost ₹840.00) |
| Customer receipts | cash 4 (₹830), UPI 2 (₹360) | ₹1,190.00 |
| Sales returns | 1 (applied to the khata) | ₹100.00 |
| Goods receipts (posted, bill ref "TEST-DEMO") | 4 | ₹3,472.00 incl. GST; ₹3,099.99 at cost |
| Supplier bills / payments / returns / credit notes | 0 | — |
| Cash paid-out (uncategorised, "TEST courier charges") | 1 | ₹50.00 |
| Cash shifts | #1 closed (expected ₹1,780, counted ₹1,775, variance −₹5); **#2 open since 22 Oct 23:15** | — |
| TEST masters | 1 customer, 1 supplier, 3 medicines | — |

## Balances the ERP holds today
- **Customer dues (khata):** TEST Customer Ramesh ₹100.00. This reconciles: ledger = open invoices − unapplied credit.
- **Supplier obligations:** none recorded. No supplier bill has been finalised.
- **Goods received not billed:** ₹3,099.99 at cost (GRN-202610-0001 … 0004).
- **Stock at cost:** ₹2,319.99. Batch stock equals the stock-movement ledger for all 4 batches.
- **Cash:** the last count was ₹1,775.00 (shift #1). Shift #2 is open with ₹0 expected. Bank balances are not tracked
  operationally; take them from the bank statement.

## Unresolved items
1. **Shift #2 is open.** Close it before the books start date.
2. **₹360 of UPI receipts are unverified.** Either verify them against the bank/UPI statement, or treat them as TEST.
3. **4 TEST GRNs have no supplier bill.** If they are real goods they belong in opening stock, plus GRNI or the supplier's
   balance. If they are test goods, the stock should be adjusted out before the opening count.
4. **The ₹50 paid-out is uncategorised.** Classify it on the Day-end page (for example, as a petty expense).
5. **The −₹5 shift variance** was explained ("coins miscounted") but not reviewed.
6. **Invoice settlement fields:** 0 mismatches.

## Decisions needed from you / your CA (nothing has been done)
- **Books start date.** It should fall after the TEST data and before the first real transaction.
- **Opening balances** from your previous books: cash, bank, customers per document, suppliers per bill, stock at cost,
  GST, capital. The ERP figures above are a cross-check, not a source of opening balances.
- **What to do with the TEST records.** They stay as history; nothing is deleted. Optionally deactivate the TEST masters.
- **When to apply migrations 009 and 010 and deploy.** The steps are in `docs/FINAL_VERIFICATION.md`.
