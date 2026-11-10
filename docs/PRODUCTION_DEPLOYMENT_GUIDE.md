# Pharmacy ERP — Production Deployment & Operations Guide

This guide covers installing, configuring, running, backing up, recovering and upgrading the Pharmacy ERP on your own.
Every command here exists in this repository and was run during the October 2026 deployment and verification.
Commands are for **PowerShell on Windows**, run from the project folder (`D:\Accounting`), unless marked *Linux* or
*Git Bash*. Replace `<…>` placeholders; never paste real passwords into documents or chat.

**Legend:** ✅ implemented and verified · ⚙️ implemented, needs your configuration · 🔎 needs manual testing with real
devices or accounts.

---

## 1. Architecture

| Part | Technology | Container | Notes |
|---|---|---|---|
| Database | MySQL 8.4 LTS (InnoDB, utf8mb4) | `pharmacy_db` | Volume `accounting_mysql_data`. |
| API | Python 3.12, FastAPI, SQLAlchemy 2 (async), Alembic | `pharmacy_backend` | Port 8000 inside Docker. |
| Worker | Same image as the API, `python -m app.worker` | `pharmacy_worker` | Runs every 60 s: WhatsApp outbox, reminders, heartbeat, scheduled backup. |
| Web UI | Next.js 15, React 19, Tailwind 4 | `pharmacy_frontend` | Port 3000; proxies `/api/*` to the API. |

- **Sessions:** a server-side session table plus an HTTP-only cookie. There is no browser token.
- **CSRF:** every change request must send a CSRF header.
- **Passwords:** hashed with Argon2id.
- **Migration control:** a guard means migrations run only when an operator explicitly asks (§6). The API and worker refuse to run against a database whose revision differs from the code.

### Two Compose files

| File | Use | Differences |
|---|---|---|
| `docker-compose.yml` | Development, and the setup the shop currently runs | Source folders are mounted, the UI runs as a dev server, MySQL is on `127.0.0.1:3306`, the API is on port 8000, `restart: unless-stopped`. |
| `docker-compose.prod.yml` | Production | The code is built into the images, the UI is compiled (`next start`), MySQL and the API are **not** published (only the UI port is), `APP_ENV=production` is forced, `restart: always`, and container logs rotate (5 × 10 MB). |

The production file was tested end to end on 25 Oct 2026 in an isolated stack: first install, migration, setup,
sign-in, all modules, logo upload, backup, verify and download. See §15 for moving the live shop onto it.

---

## 2. Prerequisites

- **Windows server:** Windows 10/11 Pro with **Docker Desktop** (WSL 2 backend).
- **Linux server:** Docker Engine with the Compose v2 plugin (`docker compose version`).
- **Hardware:** 4 GB RAM minimum, 8 GB recommended. 20 GB free disk, plus space for backups.
- **Network:** a fixed LAN IP for the server (a DHCP reservation in the router).
- **Optional:** Python 3.12 on the server, only for running the test suites (`backend\.venv`).

### Docker Desktop on Windows
1. Install Docker Desktop and enable *Use the WSL 2 based engine*.
2. *Settings → General → Start Docker Desktop when you sign in* makes the ERP come back after a reboot, because the containers restart automatically.
3. Check the installation:
   ```powershell
   docker version
   docker compose version
   ```

### Linux
Install Docker Engine and the Compose plugin from Docker's repository, add your user to the `docker` group, and copy
the project folder (for example to `/opt/pharmacy-erp`). Every `docker compose …` command in this guide is the same on
Linux. The two PowerShell scripts in `scripts\` are Windows-only; on Linux use the in-app backups (§11) or the
equivalent `docker exec pharmacy_db mysqldump …` command shown in §11.3.

---

## 3. Configuration (`.env`)

Create it from the template and fill in **every** value:
```powershell
Copy-Item .env.example .env
notepad .env
```

| Variable | What to set |
|---|---|
| `MYSQL_ROOT_PASSWORD`, `MYSQL_PASSWORD` | Long random values. They take effect **only when the database volume is first created**; changing them later does not change the MySQL accounts. |
| `MYSQL_DATABASE`, `MYSQL_USER` | `pharmacy_erp`, `pharmacy_app` (the defaults used throughout). |
| `DATABASE_URL`, `DATABASE_URL_SYNC` | The same credentials, with host `db`: `mysql+aiomysql://pharmacy_app:<pw>@db:3306/pharmacy_erp` and `mysql+pymysql://…`. |
| `DATABASE_URL_LOCAL`, `DATABASE_URL_LOCAL_SYNC` | The same, with host `127.0.0.1`. Used only by host tools and tests in the development setup. |
| `APP_ENV` | `production` for a live shop. In production the API refuses to start with a placeholder `SECRET_KEY`. |
| `SECRET_KEY` | 64 hex characters: `python -c "import secrets; print(secrets.token_hex(32))"`. Changing it signs everyone out. |
| `SESSION_COOKIE_SECURE` | `false` on a plain-HTTP LAN; `true` only behind HTTPS (§13). |
| `SESSION_MAX_AGE_SECONDS` | Session length (default 28800 = 8 h). |
| `ALLOWED_HOSTS` | Host names the API accepts in production. `backend` is always accepted (the UI's internal proxy). Default `localhost,127.0.0.1`. |
| `CORS_ORIGINS` | Only matters if browsers call the API directly. The UI proxies, so the default is fine. |
| `APP_UTC_OFFSET_MINUTES` | `330` for India. Business dates and the backup time use it. |
| `BACKUP_DIR`, `BACKUP_RETENTION_DAYS` | `/app/backups` (a Docker volume) and the number of days to keep scheduled backups. |
| `BACKUP_VERIFY_DATABASE` | Optional: a scratch schema ending in `_restore_verify` that the app user can write, used for test restores (§11). |
| `WHATSAPP_*` | See §10. Leave `WHATSAPP_PROVIDER=manual` until a Meta Cloud API account is set up. |
| `LAN_BIND` | Production only: the address the UI listens on, for example `192.168.1.10:3000`. Default `0.0.0.0:3000`. |

Rules:
- Never commit `.env`; it is listed in `.gitignore`.
- **Never put `ALLOW_DB_MIGRATIONS` in `.env`.** The API refuses to start if it sees it.
- `.env` and the backups contain secrets and customer data. Keep them readable only by the administrator.

---

## 4. First installation (new server)

**Production:**
```powershell
docker compose -f docker-compose.prod.yml --env-file .env up -d db
docker compose -f docker-compose.prod.yml --env-file .env run --rm --no-deps -e ALLOW_DB_MIGRATIONS=1 backend alembic upgrade head
docker compose -f docker-compose.prod.yml --env-file .env up -d --build
docker compose -f docker-compose.prod.yml ps
```

**Development** (the setup currently in use): the same commands without `-f docker-compose.prod.yml --env-file .env`.

Then:
1. Open `http://<server-ip>:3000`. With an empty database the app opens **first-run setup**: pharmacy name, legal
   name, GSTIN, state, drug licence, invoice prefix and the first administrator.
2. Sign in, then create staff under **Users** (§7).
3. Configure branding (§8), application settings (*Admin → Application settings*) and the daily backup time (§11).

---

## 5. Starting, stopping and status

| Task | Command (development file; add `-f docker-compose.prod.yml` for production) |
|---|---|
| Status | `docker compose ps` |
| Start everything | `docker compose up -d` |
| Stop the app but keep the DB running (before maintenance) | `docker compose stop backend worker frontend` |
| Start the app again | `docker compose up -d backend worker frontend` |
| Restart the API and worker after a code change (dev mounts the source) | `docker compose restart backend worker` |
| Rebuild the UI after a code change | `docker compose up -d --build frontend` |
| Stop everything | `docker compose stop` (`down` also removes containers; the **volumes stay**) |

**Never run `docker compose down -v`.** The `-v` flag deletes the database volume.

Startup order: MySQL becomes healthy, then the API starts (connectivity check, then the migration-revision check),
then the worker and UI. Stopped containers stay stopped until started again, even after a reboot.

---

## 6. Database migrations (controlled)

The current schema head is **012**: 001–008 core, 009 automatic postings, 010 settlements/bank/backups/credit notes,
011 branding, 012 supplier bill imports.

How the guard works:
- `alembic upgrade`, `downgrade` and `stamp` **refuse (exit code 3)** unless `ALLOW_DB_MIGRATIONS=1` is set for that single command.
- One exception: `upgrade` when the database is already at head is allowed, because it has nothing to do.
- Read-only commands (`current`, `history`, `heads`, `show`) always work.
- The API refuses to start, and the worker pauses, while the database revision differs from the code.

```powershell
docker compose run --rm --no-deps backend alembic current        # read the version
docker compose run --rm --no-deps backend alembic history        # list migrations
```

Applying migrations (only after a verified backup, §11):
```powershell
docker compose stop backend worker frontend
docker compose run --rm --no-deps -e ALLOW_DB_MIGRATIONS=1 backend alembic upgrade head
docker compose run --rm --no-deps backend alembic current        # expect the new head, e.g. 012 (head)
docker compose up -d backend worker frontend
```
- If the API container keeps restarting with `[migration guard] REFUSED 'upgrade'` or `Refusing to start: database is at revision …`, the code is newer than the database. Back up, then run the command above.
- **Rehearse first:** restore the backup into the scratch schema (§11.2), point a one-off command at it with `-e DATABASE_URL_SYNC=<url ending /pharmacy_erp_restore_verify>`, and migrate that copy before touching live.
- **Rollback** is a recovery action, not a routine step. Either restore the pre-migration backup (§11.4), or downgrade with the flag (`… -e ALLOW_DB_MIGRATIONS=1 backend alembic downgrade <rev>`) after a backup.

---

## 7. Users, roles, permissions and account recovery

**Default roles:** *Owner / Admin* (every permission), *Pharmacist*, *Cashier*, *Inventory Manager*. Role details are in
the **Users** page and in `backend/app/services/auth.py` (`ROLE_PERMISSIONS`, `PHASE4_ROLE_GRANTS`, `PAYABLES_ROLE_GRANTS`).
Accounting permissions are granted to Owner / Admin only.

- **Users page** (`users.manage`): create users, edit them, change roles, reset passwords, deactivate or activate, view a user's history, and reset a user's appearance. The last active administrator cannot be deactivated or demoted.
- **Password rule:** 8–128 characters, with an upper-case letter, a lower-case letter and a digit.
- **Sign-in protection:** repeated failures are rate-limited per user and IP (5 per user, 20 per IP, in 15 minutes).

**Account recovery** (for example when the only administrator has forgotten their password). This needs access to
the server; it cannot be done over the network:
```powershell
docker compose exec backend python -m scripts.reset_password <username>             # prompts twice, hidden
docker compose exec backend python -m scripts.reset_password <username> --activate  # also re-activates the account
```
It checks the password rule, ends the account's sessions and writes `user.password_reset` (reason "operator
recovery") to the audit log. The password is never shown or logged.

---

## 8. Branding, logos, themes and user preferences ✅

See `docs/BRANDING.md` for details. In short:
- **Admin → Branding & appearance** (`/settings/branding`, `settings.manage`):
  - app name, short name, tagline, login welcome text and website;
  - business name, legal name, address, phone and email (printed on invoices; GSTIN, drug licence and state are read-only);
  - invoice and receipt footers;
  - four logos: main, dark-mode, compact and favicon;
  - the **default** theme, mode, colours, navigation, density and dashboard layout.
- **My appearance** (`/settings/appearance`, the palette icon in the sidebar): each user's own theme. It never changes other users or the default.
- **Themes:** Emerald + White (default), Blue, Teal, Purple, Slate, Rose/Burgundy, High contrast, Dark Emerald, Dark Blue, Dark Neutral. Each works with light, dark or system mode, plus 13 optional custom colours.
- **Logos:**
  - PNG, JPEG or WebP only, detected from the file content; SVG is rejected.
  - Size limits: main and dark logo 1 MB, compact logo 512 KB, favicon 256 KB.
  - Stored in the database table `brand_assets`, so every backup includes them and no extra volume is needed.
- Invoices and receipts always print black on white.

---

## 9. Daily operations (what is implemented)

| Area | Where | Status |
|---|---|---|
| Medicine catalogue, packaging/unit conversions, barcodes, GST rates, categories, manufacturers | Medicines, Masters & Tax | ✅ |
| Medicine catalogue import (CSV/Excel, preview, duplicates; 318-item starter list of generics) — see `docs/PURCHASING_IMPORTS.md` | Medicines → Import catalogue | ✅ |
| Supplier bill import: PDF/scan/photo, local OCR, mandatory review, confirm into GRN + supplier bill — see `docs/PURCHASING_IMPORTS.md` | Purchasing → Import supplier bill | ✅ (reading accuracy depends on the bill; always review) |
| Suppliers, purchase orders, goods receipts (GRN → batch stock), supplier bills (check & finalise), payments, credit notes, purchase returns | Suppliers, Purchasing, Payables | ✅ |
| Batch and expiry stock, stock ledger (append-only), opening stock, adjustments, low-stock and expiry alerts | Inventory | ✅ |
| POS: cash, UPI, card, split and credit (udhaar) sales; discounts within limits; prescriptions for scheduled drugs | POS | ✅ |
| Invoices: view, A4 and 80 mm receipt print, PDF, void | Sales → Invoices | ✅ |
| Customers and khata: collections, advances, write-offs, statements, reminders | Sales → Customers, Receivables | ✅ |
| Returns and refunds (refund or keep as credit), receipt reversal | Sales → Returns | ✅ |
| Cash shifts: open/close, drawer expenses, bank deposits, variance review; day-end | Sales → Day-end | ✅ |
| Expenses with approval | Payables → Expenses | ✅ |
| Reports: sales, receivables, purchases, CSV export | Sales → Reports | ✅ |
| Accounting: chart of accounts, journals, automatic postings, periods, trial balance, statements, settlements, bank reconciliation, year end, CA pack | Accounts | ✅ (not set up yet — §9.1) |

`docs/PHASE4_OPERATIONS.md` describes the POS, credit and shift rules in detail.

### 9.1 Accounting go-live — decisions needed before setup
Accounting is **deliberately not set up**: no chart of accounts, journals, fiscal years or books start date. Setting it
up is one action (*Accounts → Set up accounting*) that needs:
1. **Books start date.** Pick it with your CA: the day real trading on this system begins. Postings start from that date.
2. **Opening balances as at that date**, from your previous books: cash in hand, bank balances, customer dues per invoice, supplier dues per bill, stock at cost, GST balances, capital and loans. Enter them in *Accounts → Opening balances*; the preview must show no errors before you post.
3. From then on every finalised sale, receipt, return, supplier bill or payment, expense, cash movement, stock adjustment and settlement **posts automatically**. Check *Accounts → Automatic postings* daily and retry any failures there.
4. **Month end:** settle UPI and card clearing, import the bank statement, reconcile, then close the period. **Year end:** close all months, then *Close year*, then send the CA pack.

The ERP never invents opening balances, and it does not post anything dated before the books start date.

---

## 10. Printers, barcode scanners, WhatsApp

| Integration | How | Status |
|---|---|---|
| Invoice and receipt printing | The browser print dialog: A4 or 80 mm layout from the invoice page. Set the thermal printer's paper size in Windows. The app records each print (ORIGINAL, then DUPLICATE). | ⚙️ 🔎 Layouts verified on screen; test on your printer. |
| Barcode scanners | USB or Bluetooth scanners in keyboard (HID) mode, set to send *Enter*, type into the POS search box. | 🔎 Test with your scanner. |
| WhatsApp, manual (default) | `WHATSAPP_PROVIDER=manual`: the app opens a WhatsApp link with the message for staff to send. | ✅ |
| WhatsApp, automatic | `WHATSAPP_PROVIDER=cloud_api` plus `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_APP_SECRET`, `WHATSAPP_WEBHOOK_VERIFY_TOKEN` and approved template names (Meta Business account). The webhook URL is `https://<public-host>/api/v1/whatsapp/webhook` (needs HTTPS, §13). | ⚙️ 🔎 Only mock-tested; needs a Meta account and a live test. |

---

## 11. Backups, verification, restore and disaster recovery

### 11.1 Making backups
- **In the app:** *Admin → Backups → Back up now*.
  - Each backup is a consistent gzip SQL snapshot, logos included, with a SHA-256 checksum recorded.
  - *Verify* re-reads the file and checks the row count of every table. If `BACKUP_VERIFY_DATABASE` is set, it also test-restores the file.
  - *Download* saves the file to your computer.
  - Scheduled daily backup: *Admin → Application settings → `backup.daily_time`* (for example `22:30`). The worker runs it and keeps `BACKUP_RETENTION_DAYS` days of scheduled backups.
  - Files live in the Docker volume `accounting_backend_backups`.
- **Command line (Windows):** writes `backups\pharmacy_erp_<timestamp>.sql` in the project folder and prints its SHA-256:
  ```powershell
  powershell -ExecutionPolicy Bypass -File scripts\backup.ps1
  ```

### 11.2 Verifying a backup (restores into the scratch DB `pharmacy_erp_restore_verify`; live is only read)
```powershell
powershell -ExecutionPolicy Bypass -File scripts\verify_restore.ps1 backups\<file>.sql
# Expected: "Restored N tables into pharmacy_erp_restore_verify; 0 table(s) differ from live"
Get-FileHash backups\<file>.sql -Algorithm SHA256
```
Copy backups **off the server** (USB drive, NAS or cloud) at least weekly. They contain customer data, so store them securely.

### 11.3 Linux backup equivalent
```bash
docker exec pharmacy_db sh -c 'mysqldump -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" --single-transaction --no-tablespaces "$MYSQL_DATABASE"' > pharmacy_erp_$(date +%Y%m%d_%H%M%S).sql
sha256sum pharmacy_erp_*.sql
```

### 11.4 Restoring (this replaces live data; only with the owner's written decision)
```powershell
docker compose stop backend worker frontend
powershell -ExecutionPolicy Bypass -File scripts\verify_restore.ps1 backups\<file>.sql      # test it first
# mysqldump file (from scripts\backup.ps1):
docker cp backups\<file>.sql pharmacy_db:/tmp/restore.sql
docker exec pharmacy_db sh -c 'mysql -uroot -p$MYSQL_ROOT_PASSWORD pharmacy_erp < /tmp/restore.sql && rm -f /tmp/restore.sql'
# app backup (.sql.gz, downloaded from Admin → Backups):
docker cp <file>.sql.gz pharmacy_db:/tmp/restore.sql.gz
docker exec pharmacy_db sh -c 'gunzip -c /tmp/restore.sql.gz | mysql -uroot -p$MYSQL_ROOT_PASSWORD pharmacy_erp && rm -f /tmp/restore.sql.gz'
docker compose run --rm --no-deps backend alembic current     # older than the code? apply migrations (§6)
docker compose up -d backend worker frontend
```
Afterwards check *Admin → System health* (migration = code head) and, once accounting is live, the trial balance and the integrity check.

### 11.5 Disaster recovery (server lost)
1. Install Docker on a new machine and copy the project folder plus your `.env`. A new `.env` works too, but the MySQL passwords in it set up the new volume.
2. `docker compose up -d db`, then restore the latest off-site backup (§11.4).
3. Apply migrations if the code is newer (§6), then start the app (§5) and verify (§16).

---

## 12. Upgrading the application
1. Close all cash shifts and tell staff about the downtime.
2. Back up and verify (§11). Copy the backup off the server.
3. Replace the application files. Keep `.env` and **do not edit existing migration files**.
4. Stop the app, migrate explicitly, rebuild and start:
   ```powershell
   docker compose stop backend worker frontend
   docker compose run --rm --no-deps -e ALLOW_DB_MIGRATIONS=1 backend alembic upgrade head
   docker compose up -d --build
   ```
5. Check `docker compose ps`, the health URL (§14) and *Admin → System health*.
6. If something is wrong: stop the stack, restore the backup (§11.4), and reinstall the previous version.

---

## 13. Security hardening

**Built in ✅**
- Argon2id password hashing and server-side sessions in an HTTP-only cookie.
- CSRF protection on every change request.
- Sign-in rate limiting; RBAC on every API route; the last-admin guard.
- An append-only audit log (*Admin → Audit log*).
- In production: API docs switched off, trusted-host checking, and the API refuses to start with a placeholder `SECRET_KEY`.
- Upload validation for logos (content-sniffed raster images only, size limits).
- Security headers on the UI (`X-Frame-Options DENY`, `nosniff`, `Referrer-Policy`).

**Your configuration ⚙️**
- **Firewall (Windows, admin PowerShell):** open only the UI port, to the private network:
  ```powershell
  New-NetFirewallRule -DisplayName "Pharmacy ERP" -Direction Inbound -Protocol TCP -LocalPort 3000 -Action Allow -Profile Private
  ```
- **The development file publishes the API on port 8000 and MySQL on 127.0.0.1:3306.** Do not open port 8000 in the firewall. Use the production file for a live shop (§15): only the UI is published there.
- **HTTPS** is needed for access from outside the LAN, and for WhatsApp webhooks.
  - Put a reverse proxy (Caddy or nginx) with a certificate in front of port 3000 and set `SESSION_COOKIE_SECURE=true`.
  - If browsers reach the API directly, add the public host name to `ALLOWED_HOSTS`.
  - 🔎 Not configured or tested yet.
- **Never expose MySQL to the network.** Rotate the passwords if `.env` was ever shared.
- Give each person their own account with the least role they need. Deactivate leavers at once.

---

## 14. Health checks, logs and troubleshooting

| Check | How |
|---|---|
| Liveness / readiness | `curl.exe http://localhost:8000/api/v1/health/live` and `…/health/ready`. In production, run them inside the container: `docker compose -f docker-compose.prod.yml exec backend python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/api/v1/health/ready').read())"` |
| Full health | *Admin → System health*: database, migration revision against the code, worker heartbeat, last backup. |
| Logs | `docker compose logs backend --since 30m`, `docker compose logs worker --since 30m`, `docker compose logs frontend --since 30m` |
| Database version | `docker compose run --rm --no-deps backend alembic current` |

| Symptom | Cause and fix |
|---|---|
| API restarting, log shows `[migration guard] REFUSED` or `Refusing to start: database is at revision X but this code requires Y` | The code is newer than the database. Back up, then apply migrations (§6). |
| Log shows `Refusing to start: ALLOW_DB_MIGRATIONS is set` | Remove it from `.env` or compose. |
| Log shows `Refusing to start in production: SECRET_KEY …` | Set a real 64-hex `SECRET_KEY` (§3). |
| Worker log shows `Worker paused, waiting for a planned migration` | Same cause as the guard refusal; it resumes by itself once the revision matches. |
| UI shows errors and API returns `400 Invalid host header` (production) | A custom `ALLOWED_HOSTS` is missing the name clients use. `backend` is added automatically. |
| `Database connectivity check failed on startup` | MySQL is not healthy yet, or the credentials in `DATABASE_URL` are wrong. Check `docker compose ps` and `docker compose logs db`. |
| Login says too many attempts | Wait 15 minutes, or restart the API (the limiter is in memory). |
| Forgot the only admin password | `scripts.reset_password` (§7). |
| Backup failed | The error is shown on the Backups page and in the audit log. Check free disk space and the `accounting_backend_backups` volume. |

---

## 15. Moving the live shop from the development to the production Compose file (recommended, a planned window)

Both files use the same project name (the folder name), so the **same volumes** `accounting_mysql_data` and
`accounting_backend_backups` are reused. Data is kept.

1. Back up and verify (§11); copy the backup off the server.
2. In `.env`, set:
   - `APP_ENV=production`;
   - a real `SECRET_KEY`;
   - `SESSION_COOKIE_SECURE=false` (HTTP LAN);
   - optionally `LAN_BIND=<server-ip>:3000`.
3. Switch over:
   ```powershell
   docker compose stop
   docker compose rm -f backend worker frontend
   docker compose -f docker-compose.prod.yml --env-file .env up -d --build
   docker compose -f docker-compose.prod.yml ps
   ```
4. Verify (§16). From now on, add `-f docker-compose.prod.yml` to every `docker compose` command.
5. The PowerShell backup scripts keep working, because they use `docker exec pharmacy_db`. The host-run test suites need the development file, because the production file does not publish MySQL.

---

## 16. Deployment verification checklist
- [ ] `docker compose ps`: db healthy, backend healthy, worker and frontend up.
- [ ] `alembic current` shows the code head (012 as of this guide).
- [ ] The health URL returns `{"status":"ready","database":"connected"}`.
- [ ] The login page shows your branding, and the administrator can sign in.
- [ ] *Admin → System health*: all checks OK; the worker heartbeat is recent.
- [ ] Dashboard, Medicines, Inventory, Purchasing, POS, Sales, Customers, Suppliers and Accounts load without errors.
- [ ] *Back up now*, then *Verify* passes; download a copy off the server.
- [ ] Print a test receipt and an A4 invoice on the real printers; scan one barcode.
- [ ] Logs since the start contain no `ERROR`, `Traceback` or `Refusing` lines.

## 17. Maintenance checklist
- **Daily:**
  - The automatic backup succeeded (Backups page).
  - Close cash shifts and review variances.
  - Once accounting is live, check *Automatic postings* (0 failed).
  - Review expiry and low-stock alerts.
- **Weekly:** copy backups off the server; run *Verify* on one; review the audit log for unexpected changes.
- **Monthly:**
  - Run `scripts\verify_restore.ps1` on a backup.
  - Settle UPI/card, reconcile the bank, close the period (once accounting is live).
  - Update Windows and Docker Desktop; deactivate leavers.
- **Before every upgrade:** follow §12.

## 18. Reference: test and verification commands (development setup, Git Bash)
```bash
cd backend
export TEST_MYSQL_URL=$(grep ^DATABASE_URL_LOCAL= ../.env | cut -d= -f2- | sed 's#/[^/?]*$#/pharmacy_erp_test#')
export TEST_BACKUP_VERIFY_DATABASE=pharmacy_erp_test_restore_verify
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider      # backend + MySQL suites (MySQL part needs Docker)
cd ../frontend && npx tsc --noEmit && npm run build
```
The MySQL tests drop and recreate only `pharmacy_erp_test`. They never touch the live database. More detail is in
`docs/FINAL_VERIFICATION.md`.

## 19. The fresh-launch cleanup (already done on 25 Oct 2026)
The testing-period data was removed with `backend/scripts/fresh_launch_cleanup.py`:
- It is a dry run by default.
- It refuses unless the revision matches the code and the kept account is a verified Owner / Admin.
- It refuses if any medicine, supplier or customer is not named `TEST…`.
- It refuses if the accounting books hold anything, or if any table is unclassified.
- It runs in one transaction and writes an audit entry.

It is not needed again. Because it only removes TEST-named master data, it refuses on a database with real records.
