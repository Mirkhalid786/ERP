"""
Fresh-launch cleanup: remove the testing-period transactions and TEST master records so a real pharmacy can start
on a clean operational database, while keeping the schema, migration history, the owner's account, roles and
permissions, settings, branding, audit log, backup history and seeded reference data.

Safety:
  * Dry run unless --execute is given; --execute also needs --confirm-database <name of the database in the URL>.
  * Refuses unless the database revision equals the code's migration head (same rule as the schema guard).
  * Refuses unless --keep-username names exactly one active Owner / Admin account holding every permission.
  * Refuses if any medicine, supplier or customer is not named "TEST ..." (real data is never deleted by accident),
    or if any other user account to be removed does not look like a test account.
  * Refuses if the accounting books contain anything (ledger accounts, journals, opening balances, fiscal years, periods).
  * Refuses if the schema contains a table this script has not classified (e.g. after a future migration).
  * Deletion order is derived from the live foreign keys; a preserved table may only point at a cleared table through
    ON DELETE SET NULL (otherwise the script refuses). Everything runs in one transaction and is verified before commit.
  * Writes one audit-log entry (system.fresh_launch_cleanup) with the row counts removed.

Stop the backend, worker and frontend before running it.

Usage (from backend/, with DATABASE_URL_SYNC pointing at the database to clean):
  python -m scripts.fresh_launch_cleanup --keep-username admin                       # dry run
  python -m scripts.fresh_launch_cleanup --keep-username admin --execute --confirm-database pharmacy_erp
In the Docker setup:
  docker compose run --rm --no-deps backend python -m scripts.fresh_launch_cleanup --keep-username admin
"""
import argparse
import json
import os
import sys
from collections import defaultdict

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

# Tables emptied completely (all rows are testing-period data)
CLEAR = [
    # Sales, credit, cash and messaging
    "sale_prescriptions", "sales_invoice_items", "payment_allocations", "customer_refunds", "customer_ledger_entries",
    "sales_return_items", "sales_returns", "customer_payments", "sales_invoices", "message_outbox",
    "cash_movements", "cash_shifts",
    # Stock and catalogue (TEST medicines only; checked below)
    "stock_adjustment_items", "stock_adjustments", "stock_movements", "medicine_barcodes", "medicine_unit_conversions",
    "medicine_batches", "medicines",
    # Purchasing, payables and expenses
    "purchase_order_items", "purchase_orders", "goods_receipt_items", "goods_receipts", "supplier_invoice_items",
    "supplier_invoices", "supplier_return_items", "supplier_returns", "supplier_payment_allocations", "supplier_payments",
    "supplier_credit_notes", "expenses", "suppliers",
    # Customers (TEST customers only; checked below)
    "customers",
    # Automatic-posting work queues and bank matching (no books exist yet)
    "gl_posting_issues", "payment_settlements", "bank_statement_lines",
    # Document number counters (recreated on demand, starting after the highest existing number)
    "document_sequences",
]
# Rows of these tables are removed only for the user accounts being deleted
USER_SCOPED = {"users": "id", "user_sessions": "user_id", "user_preferences": "user_id"}
# Accounting books: must already be empty; never deleted by this script
MUST_BE_EMPTY = ["ledger_accounts", "journal_entries", "journal_lines", "opening_balance_batches", "opening_balance_lines",
                 "fiscal_years", "accounting_periods"]
PRESERVE = [
    "alembic_version", "pharmacies", "roles", "permissions", "role_permissions", "settings", "audit_logs",
    "backup_history", "brand_assets", "categories", "manufacturers", "units", "tax_rates", "expense_categories",
]
TEST_MASTERS = {"medicines": "name", "suppliers": "name", "customers": "name"}


def fail(msg: str) -> None:
    print(f"REFUSED: {msg}", file=sys.stderr)
    sys.exit(2)


def deletion_order(fks, tables):
    """Children before parents among `tables` (self-references ignored)."""
    pending, order = set(tables), []
    while pending:
        ready = [t for t in sorted(pending) if not any(c in pending and c != t for c, p, *_ in fks if p == t)]
        if not ready:
            fail(f"circular foreign keys among {sorted(pending)}")
        order.extend(ready)
        pending -= set(ready)
    return order


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keep-username", required=True, help="Login name of the owner account to keep")
    ap.add_argument("--execute", action="store_true", help="Delete (default: dry run)")
    ap.add_argument("--confirm-database", help="Required with --execute: the database name, typed out")
    args = ap.parse_args()

    url = os.environ.get("DATABASE_URL_SYNC")
    if not url:
        from app.core.config import settings
        url = settings.DATABASE_URL_SYNC
    dbname = make_url(url).database
    if args.execute and args.confirm_database != dbname:
        fail(f"--execute needs --confirm-database {dbname}")

    from app.core.schema_guard import code_head
    engine = create_engine(url, isolation_level="REPEATABLE READ")
    with engine.begin() as conn:
        q = lambda sql, **p: conn.execute(text(sql), p)
        tables = {r[0] for r in q("SELECT table_name FROM information_schema.tables WHERE table_schema = :s AND table_type = 'BASE TABLE'", s=dbname)}
        known = set(CLEAR) | set(USER_SCOPED) | set(MUST_BE_EMPTY) | set(PRESERVE)
        if tables - known:
            fail(f"unclassified table(s): {sorted(tables - known)}. Classify them in this script first.")
        if known - tables:
            fail(f"expected table(s) missing: {sorted(known - tables)}")

        revision = q("SELECT version_num FROM alembic_version").scalar()
        if revision != code_head():
            fail(f"database is at {revision} but the code is at {code_head()}")

        # The account to keep: exactly one active Owner / Admin with every permission
        keep = q("SELECT u.id, u.username, u.name, u.is_active, r.name AS role, u.role_id FROM users u JOIN roles r ON r.id = u.role_id "
                 "WHERE u.username = :u", u=args.keep_username).mappings().all()
        if len(keep) != 1:
            fail(f"{len(keep)} account(s) have the username '{args.keep_username}'")
        keep = keep[0]
        missing = q("SELECT COUNT(*) FROM permissions p WHERE NOT EXISTS (SELECT 1 FROM role_permissions rp "
                    "WHERE rp.role_id = :r AND rp.permission_id = p.id)", r=keep["role_id"]).scalar()
        if not keep["is_active"] or keep["role"] != "Owner / Admin" or missing:
            fail(f"'{args.keep_username}' is not an active Owner / Admin with every permission "
                 f"(active={keep['is_active']}, role={keep['role']}, missing permissions={missing})")

        others = q("SELECT id, username, name FROM users WHERE id <> :k", k=keep["id"]).mappings().all()
        for u in others:
            if "test" not in f"{u['username']} {u['name']}".lower():
                fail(f"user '{u['username']}' ({u['name']}) does not look like a test account; remove it in the app instead")
        for table, col in TEST_MASTERS.items():
            real = q(f"SELECT COUNT(*) FROM `{table}` WHERE `{col}` NOT LIKE 'TEST%'").scalar()
            if real:
                fail(f"{table} has {real} record(s) not named 'TEST ...'; this script only removes test data")
        for table in MUST_BE_EMPTY:
            if q(f"SELECT COUNT(*) FROM `{table}`").scalar():
                fail(f"accounting table {table} is not empty; accounting data is never removed by this script")

        fks = q("SELECT k.table_name, k.referenced_table_name, k.column_name, r.delete_rule FROM information_schema.key_column_usage k "
                "JOIN information_schema.referential_constraints r ON r.constraint_schema = k.constraint_schema "
                "AND r.constraint_name = k.constraint_name WHERE k.table_schema = :s AND k.referenced_table_name IS NOT NULL", s=dbname).all()
        cleared = set(CLEAR) | {"users"}
        anonymised = defaultdict(int)
        other_ids = [u["id"] for u in others]
        for child, parent, col, rule in fks:
            if parent in cleared and child not in cleared and child not in USER_SCOPED:
                if parent == "users":
                    n = q(f"SELECT COUNT(*) FROM `{child}` WHERE `{col}` IN :ids", ids=tuple(other_ids) or (0,)).scalar() if other_ids else 0
                else:
                    n = q(f"SELECT COUNT(*) FROM `{child}` WHERE `{col}` IS NOT NULL").scalar()
                if n and rule != "SET NULL":
                    fail(f"{child}.{col} -> {parent} is {rule} and {n} preserved row(s) point at rows being removed")
                if n:
                    anonymised[f"{child}.{col}"] += n

        before = {t: q(f"SELECT COUNT(*) FROM `{t}`").scalar() for t in sorted(tables)}
        order = deletion_order([(c, p) for c, p, *_ in fks], CLEAR)
        print(f"Database {dbname} at revision {revision}. Keeping '{keep['username']}' ({keep['name']}, {keep['role']}).")
        print("Will remove user accounts:", ", ".join(f"{u['username']} ({u['name']})" for u in others) or "none")
        print("Preserved rows whose user/link becomes NULL:", dict(anonymised) or "none")
        print("Rows to delete:", {t: before[t] for t in order if before[t]})
        if not args.execute:
            print("Dry run: nothing changed. Add --execute --confirm-database", dbname)
            conn.rollback()
            return

        # Self-references inside cleared tables: unlink first so rows can go in any order
        for child, parent, col, rule in fks:
            if child == parent and child in CLEAR:
                q(f"UPDATE `{child}` SET `{col}` = NULL WHERE `{col}` IS NOT NULL")
        for t in order:
            q(f"DELETE FROM `{t}`")
        if other_ids:
            q("DELETE FROM user_sessions WHERE user_id IN :ids", ids=tuple(other_ids))
            q("DELETE FROM user_preferences WHERE user_id IN :ids", ids=tuple(other_ids))
            q("DELETE FROM users WHERE id IN :ids", ids=tuple(other_ids))

        after = {t: q(f"SELECT COUNT(*) FROM `{t}`").scalar() for t in sorted(tables)}
        leftovers = {t: after[t] for t in CLEAR if after[t]}
        if leftovers:
            raise RuntimeError(f"rows remain after delete: {leftovers}")
        for t in PRESERVE:
            if t != "audit_logs" and after[t] != before[t]:
                raise RuntimeError(f"preserved table {t} changed: {before[t]} -> {after[t]}")
        if after["users"] != 1:
            raise RuntimeError(f"expected exactly one user to remain, found {after['users']}")
        removed = {t: before[t] - after[t] for t in tables if before[t] != after[t]}
        q("INSERT INTO audit_logs (pharmacy_id, user_id, action, entity_type, entity_id, reason, metadata_json, timestamp) "
          "SELECT p.id, :uid, 'system.fresh_launch_cleanup', 'database', :db, :reason, :meta, UTC_TIMESTAMP() FROM pharmacies p ORDER BY p.id LIMIT 1",
          uid=keep["id"], db=dbname, reason="Testing-period transactions and TEST master records removed for the fresh launch",
          meta=json.dumps({"rows_removed": removed, "users_removed": [u["username"] for u in others],
                           "links_set_null": dict(anonymised), "revision": revision}))
        print("Deleted:", removed)
    print("Committed.")


if __name__ == "__main__":
    main()
