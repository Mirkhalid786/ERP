# Recovery & Controlled Deployment Plan — EXECUTED 24 Oct 2026

**Outcome (24 Oct 2026, evening):** gates B–E were executed in one run.
- Live was **008** before (every table CHECKSUM-identical to the 23 Oct backup: no activity since). It is **010** now.
- Backup `backups\pharmacy_erp_20261024_223203.sql`, SHA-256 `488DFEA9F5057D3A6EBAF641843E720EA853F7CD45A669BE048E06E7192C2E86`,
  restored into `pharmacy_erp_restore_verify`: 54 tables, 0 row-count and 0 CHECKSUM differences.
- Rehearsal on that restore: refused without the flag (exit 3), 008 → 009 → 010 with it; only `permissions` (+3),
  `role_permissions` (+3) and 5 new empty tables changed.
- Live after migration is CHECKSUM-identical to the rehearsed copy on every table. Accounting is still not set up.
- The backend start command is now uvicorn-only (Dockerfile CMD and `command:` in both compose files).
- Final verification: backend + MySQL suite 246 passed / 0 skipped; frontend `tsc` and `next build` exit 0;
  migration chain 001 → 010, downgrade to 008 and re-upgrade clean on the new empty schema `pharmacy_erp_chain_check_20261024`.

Status at the time of writing the plan (24 Oct 2026, before execution):
- Docker Desktop and all containers are **stopped**.
- Live was last confirmed at Alembic **008**.
- The code on disk is at **010**.
- Accounting is not set up.
- No migration, config or record has been changed.

## 1. Why 009 and 010 would run automatically
| Fact | Where |
|---|---|
| The backend image's start command is `sh -c "alembic upgrade head && uvicorn …"` | `backend/Dockerfile` line 27 (CMD) |
| The dev compose file does not override the backend command, so this CMD runs every time the container starts | `docker-compose.yml` → `backend:` (no `command:`) |
| The backend container mounts the live source folder, so on start Alembic reads `backend/alembic/versions/` from disk, which now contains 009 and 010 | `docker-compose.yml` → `./backend:/app` |
| `restart: unless-stopped` restarts the containers automatically when Docker Desktop starts, because they were running when the laptop shut down | `docker-compose.yml` (all services) |
| `alembic/env.py` has no guard; any `alembic upgrade` connects and applies everything up to head | `backend/alembic/env.py` |
| The worker mounts the same source, so after a restart it would run the new code: it writes a heartbeat to `settings` and queries `backup_history`, which doesn't exist yet | `docker-compose.yml` → `worker:`; `app/worker.py` |
| Nothing else in the app migrates (no `create_all` or upgrade at startup) | checked `app/` |

The result: starting Docker Desktop alone would restart the backend and apply 009 and 010 within seconds, before a backup could be taken.

## 2. Proposed controlled-migration mechanism
Migrations run **only when an operator explicitly asks for them**. No migration file is touched.

**A1. Guard in `backend/alembic/env.py`** (an environment config file, not a migration file):
- Before running, it compares the database revision with the code's head.
- If the command is `upgrade`, `downgrade` or `stamp`, the database is not already at the target, and `ALLOW_DB_MIGRATIONS=1` is not set, it prints a clear message and exits with code 3 without changing anything.
- `alembic current` and `alembic history` still work.

```python
# --- added near the top of run_migrations_online(), before context.run_migrations() ---
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
cmd = getattr(config.cmd_opts, "cmd", None)
command = cmd[0].__name__ if cmd else ""
if command in ("upgrade", "downgrade", "stamp") and os.environ.get("ALLOW_DB_MIGRATIONS") != "1":
    current = MigrationContext.configure(connection).get_current_revision()
    head = ScriptDirectory.from_config(config).get_current_head()
    if current != head or command != "upgrade":
        print(f"[migration guard] database at {current}, code at {head}: refusing '{command}'. "
              f"Back up first, then run with ALLOW_DB_MIGRATIONS=1.", file=sys.stderr)
        sys.exit(3)
```
With this guard, the existing container's `alembic upgrade head && uvicorn` stops at the guard (exit 3), so uvicorn does not start and the backend serves nothing while the database is behind. Once the database is at head, the same command is a no-op and the backend starts normally.

**A2. Schema check in the worker** (`app/worker.py`): at the start of each pass, if the database revision differs from the code's head, log *"waiting for planned migration"* and skip the pass. It writes nothing, sends nothing and attempts no backup.

**A3. Schema check in the backend startup** (`app/main.py` lifespan): refuse to start, with a clear log line, if the database revision differs from the code's head. This covers start paths that don't go through Alembic.

**A4. (after the migration) Remove migrate-on-start permanently:**
- `docker-compose.yml` and `docker-compose.prod.yml` get backend `command: ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000","--workers","1"]`.
- The `backend/Dockerfile` CMD becomes uvicorn only.
- Migrations then run only through the explicit command in §3 step D.

Tests: add unit tests for the guard (refuses without the flag, no-op at head, allowed with the flag), then run the full suites.

## 3. Backup-first procedure (exact commands)
Run from `D:\Accounting` in PowerShell unless marked otherwise. **Each lettered gate needs your approval.**

### Gate A — code change only (Docker stays off; no live effect) — ✅ DONE 24 Oct 2026
Implemented: `backend/alembic/env.py` guard, `backend/app/core/schema_guard.py`, checks in `app/main.py` and
`app/worker.py`, and `tests/test_schema_guard.py` (19 tests). The backend also refuses to start if `ALLOW_DB_MIGRATIONS`
is present in its own environment, so a flag left in `.env` cannot silently restore migrate-on-start.
Docs updated: DEPLOYMENT.md §2/§5/§7 and FINAL_VERIFICATION.md §3 now use the explicit migration command.

1. Apply A1 (guard), A2 (worker check) and A3 (backend check) to the source.
2. Run the tests on the developer machine (SQLite only — the MySQL tests need Docker):
   `cd backend; .\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider`
3. Show you the diff before Gate B.

### Gate B — start Docker in a controlled way; read the actual live version
```powershell
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
do { Start-Sleep 5; docker info --format '{{.ServerVersion}}' 2>$null } until ($LASTEXITCODE -eq 0)
docker compose ps -a                                   # expect: db healthy, backend restarting (guard), worker up (waiting), frontend up
docker compose logs backend --since 10m | Select-String "migration guard"     # expect: [migration guard] REFUSED 'upgrade': database at 008, code head 010
docker compose logs worker --since 10m | Select-String "Worker paused"       # expect: Worker paused, waiting for a planned migration
docker compose stop backend worker frontend            # stopped containers stay stopped on the next Docker start
docker exec pharmacy_db sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" pharmacy_erp -N -e "SELECT version_num FROM alembic_version; SHOW TABLES LIKE ''gl_posting_issues''; SHOW TABLES LIKE ''payment_settlements''; SELECT COUNT(*) FROM ledger_accounts; SELECT COUNT(*) FROM journal_entries; SELECT COUNT(*) FROM opening_balance_batches; SELECT COUNT(*) FROM settings WHERE setting_key=''accounting.books_start_date''" 2>/dev/null'
```
- **Expected:** `008`, no new tables, and 0 / 0 / 0 / 0.
- **If the result is 009 or 010, or any accounting count is not 0, stop and report. Change nothing.**
- Also compare the money, stock and audit checksums with the 23 Oct values (₹1,390 sales, ₹1,190 receipts, ₹100 khata, 540 stock units, ₹3,472 GRNs, ₹50 cash movements, 41 audit rows). Any difference means there was business activity on 24 Oct, and it will be listed.

### Gate C — fresh backup and verified restore (reads live; replaces only the scratch database `pharmacy_erp_restore_verify`)
```powershell
powershell -ExecutionPolicy Bypass -File scripts\backup.ps1
$f = (Get-ChildItem backups\pharmacy_erp_2026*.sql | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
powershell -ExecutionPolicy Bypass -File scripts\verify_restore.ps1 $f      # expect: 54 tables, 0 differ
Get-FileHash $f -Algorithm SHA256
```
Copy `$f` off the laptop (USB or cloud) before Gate D.

**Recommended rehearsal (still Gate C — scratch copy only):** apply the migration to the verified restore first, so the
allowed path (guard + commit) is proven on MySQL before live:
```powershell
docker exec pharmacy_db sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "GRANT ALL PRIVILEGES ON pharmacy_erp_restore_verify.* TO ''pharmacy_app''@''%''" 2>/dev/null'
$scratch = (Select-String -Path .env -Pattern '^DATABASE_URL_SYNC=').Line.Split('=',2)[1] -replace '/pharmacy_erp$','/pharmacy_erp_restore_verify'
docker compose run --rm --no-deps -e DATABASE_URL_SYNC=$scratch backend alembic upgrade head                            # expect REFUSED (exit 3)
docker compose run --rm --no-deps -e DATABASE_URL_SYNC=$scratch -e ALLOW_DB_MIGRATIONS=1 backend alembic upgrade head   # expect 008 -> 009 -> 010
docker exec pharmacy_db sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" pharmacy_erp_restore_verify -N -e "SELECT version_num FROM alembic_version" 2>/dev/null'   # expect 010
```

### Gate D — apply 009 and 010 explicitly (the only step that changes live schema)
```powershell
docker compose run --rm --no-deps -e ALLOW_DB_MIGRATIONS=1 backend alembic upgrade head
docker exec pharmacy_db sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" pharmacy_erp -N -e "SELECT version_num FROM alembic_version" 2>/dev/null'   # expect 010
```
Integrity check against the verified restore (read-only):
- every pre-existing table has the same row count as `pharmacy_erp_restore_verify`;
- SHA-256 per row set on sales, payments, khata, stock, GRNs, supplier bills, cash shifts and movements, and returns is identical;
- only `permissions` (+3) and `role_permissions` (+3) changed;
- the new tables (`gl_posting_issues`, `payment_settlements`, `bank_statement_lines`, `backup_history`, `supplier_credit_notes`) exist and are empty;
- `ledger_accounts`, `journal_entries` and `opening_balance_batches` are still 0, and there is no books start date.

**Rollback**, if anything is wrong:
- `docker compose run --rm --no-deps -e ALLOW_DB_MIGRATIONS=1 backend alembic downgrade 008` (drops only the new, empty tables and permissions); or
- restore `$f` per `docs/DEPLOYMENT.md` §7.

### Gate E — remove migrate-on-start, then start the services
1. Apply A4 (compose `command:` override for the backend in both compose files; Dockerfile CMD uvicorn-only).
2. Start the services:
```powershell
docker compose up -d backend worker frontend          # backend is recreated with the new command; no migration on start
docker compose ps
curl.exe -s -o NUL -w "%{http_code}`n" http://localhost:8000/api/v1/health/ready     # expect 200
docker compose logs backend worker --since 5m | Select-String -Pattern "error|Traceback|migration guard"
```
3. Check in the app: Admin → System health shows migrations 010 = code 010 and accounting "not set up"; a quick POS screen load works.

## 4. What stays untouched throughout
- Accounting is **not** initialised: no books start date, no chart, no opening balances.
- No historical TEST transaction is posted.
- No live business record is edited or deleted.
- No migration file is moved, renamed or edited.
- Old restore databases are not dropped. Only the scratch `pharmacy_erp_restore_verify` is replaced in Gate C, as the verify script always does.

## 5. Approvals needed
| Gate | What changes | Live impact |
|---|---|---|
| A | Source code: Alembic guard, worker and backend schema checks, tests | None (Docker off) |
| B | Starts Docker Desktop; stops backend, worker and frontend; reads the version | The app is offline during the window; no data change |
| C | Backup file; replaces the scratch verify DB | Read-only on live |
| D | Applies migrations 009 and 010 | Adds 5 empty tables + 3 permissions |
| E | Compose and Dockerfile command change; starts the services | App back online, no migration on start |
