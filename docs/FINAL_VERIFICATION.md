# Final comprehensive testing & review pass — exact steps

Run from `D:\Accounting` in Git Bash, unless the command shows PowerShell.
None of these steps touch the live database, except where marked **read-only**.

## 1. Backend and MySQL suites (one run)
```bash
cd backend
export TEST_MYSQL_URL=$(grep ^DATABASE_URL_LOCAL= ../.env | cut -d= -f2- | sed 's#/[^/?]*$#/pharmacy_erp_test#')
export TEST_BACKUP_VERIFY_DATABASE=pharmacy_erp_test_restore_verify
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider
```
- Expected result: **0 failed, 0 skipped** (the count grows as features are added; it was 289 before the purchasing-import work). This covers the SQLite suite plus the MySQL 8.4 concurrency, backup and restore tests.
- Without `TEST_MYSQL_URL`, the 22 MySQL tests are skipped.
- The MySQL tests drop and recreate `pharmacy_erp_test` only.
- Run them while nothing else is loading the MySQL server. One period-close race test failed once while a load script was running at the same time.

## 2. Frontend
```bash
cd frontend && npx tsc --noEmit && npm run build
```
Expected result: no type errors, and "Compiled successfully".

## 3. Migrations
```bash
# Full chain on an empty schema (it must exist; create it as root and grant pharmacy_app if needed)
export DATABASE_URL_SYNC=$(grep ^DATABASE_URL_LOCAL_SYNC= .env | cut -d= -f2- | sed 's#/pharmacy_erp$#/pharmacy_erp_migrate_check#')
export ALLOW_DB_MIGRATIONS=1          # required by the migration guard; scratch schema only, unset afterwards
cd backend && ./.venv/Scripts/python.exe -m alembic upgrade head      # 001 -> 011
./.venv/Scripts/python.exe -m alembic downgrade 008 && ./.venv/Scripts/python.exe -m alembic upgrade head
unset ALLOW_DB_MIGRATIONS
```
- Expected result: 11 upgrades, a clean downgrade to 008 and a clean re-upgrade.
- Compared with the models, nothing is missing. There is older index and comment drift on Phase 1–3 tables only.

## 4. Backup and restore
```powershell
powershell -ExecutionPolicy Bypass -File scripts\backup.ps1                       # read-only dump of live
powershell -ExecutionPolicy Bypass -File scripts\verify_restore.ps1 backups\<file>.sql
```
Expected result: "Restored 54 tables into pharmacy_erp_restore_verify; 0 table(s) differ from live".

## 5. Load and pilot data on staging (never live)
```bash
cd backend
export LOAD_DB_URL=$(grep ^DATABASE_URL_LOCAL= ../.env | cut -d= -f2- | sed 's#/[^/?]*$#/pharmacy_erp_staging#')
./.venv/Scripts/python.exe -m scripts.load_test_data --medicines 200 --customers 100 --days 30 --sales-per-day 150 --books
```
Expected result: every sale is accepted, the trial balance is balanced, and reconciliation is OK.

## 6. Critical end-to-end workflows (staging UI)
Start the staging API (with `ALLOW_UNMANAGED_SCHEMA=1`, because the load script builds the staging schema from the
models rather than with Alembic) on 127.0.0.1:8001 against `pharmacy_erp_staging`, and `next dev` on :3001 with
`INTERNAL_API_URL=http://127.0.0.1:8001`. Then check:
1. **Purchasing:** PO → GRN post → supplier bill → *Check & finalise* → partial and split payments → credit note → purchase return.
2. **POS:** cash, UPI and credit sales; barcode entry; discount permission; prescription capture; print receipt or A4.
3. **Credit:** collection, advance, apply credit, write-off; khata statement.
4. **Returns, refunds and voids** (refund and keep-as-credit), plus receipt reversal.
5. **Cash shift:** open, drawer expense, bank deposit, close with a variance, review.
6. **Accounting:**
   - set up accounting (staging only) and post opening balances;
   - Automatic postings shows "All good";
   - Financial statements: the balance sheet balances and cash flow reconciles;
   - settlement plus bank statement import and match;
   - close a period, retry a failed posting, year-end close and reopen;
   - CA pack download.
7. **Authorisation:** sign in as Pharmacist, Cashier and Inventory Manager and confirm the 403s and hidden buttons.
8. **Duplicates:** double-click submits, a repeated idempotency key, a repeated UTR or bill number.
9. **Admin:** Back up now → Verify → Download; System health; Audit log filters; Settings validation.

## 7. Financial reconciliation (staging, after step 6)
- Accounts → Automatic postings: 0 failed, 0 missing, 0 changed-since-posting.
- The control accounts agree: AR = khata, AP = supplier balances, Inventory = ERP stock value.
- The trial balance, balance sheet and integrity check are all OK. Cash flow opening + net = closing.

## 8. Live, read-only
- `docs/CUTOVER_REPORT.md` was produced read-only. Re-run it before go-live.
- **Do not** set up accounting, post opening balances or apply migrations on live without the owner's approval.
