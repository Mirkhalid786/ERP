# Pharmacy ERP — Deployment, LAN, Updates, Backup & Restore

> **The complete, current operations manual is `docs/PRODUCTION_DEPLOYMENT_GUIDE.md`** (verified commands for install,
> migrations, backups/restore, recovery and upgrades). This page is kept as a summary; where they differ, follow the guide.

This guide covers running the ERP for a shop on Windows or Linux with Docker, opening it to
the shop's computers on the local network, updating it safely, and backing up / restoring.

## 1. Requirements
- Windows 10/11 Pro with **Docker Desktop** (WSL 2 backend), or Linux with Docker Engine + Compose v2.
- 4 GB RAM minimum (8 GB recommended), 20 GB free disk plus space for backups.
- A static LAN IP for the server computer (see §4).

## 2. First installation
1. Copy the project folder to the server (for example `D:\Accounting`).
2. `copy .env.example .env` and fill in **every** value. Generate secrets with
   `python -c "import secrets; print(secrets.token_hex(32))"`. Set `APP_ENV=production`.
   - `SESSION_COOKIE_SECURE=true` only when the UI is served over HTTPS; on a plain-HTTP LAN keep `false`.
   - Never commit or share `.env`.
3. Create the database schema explicitly (migrations never run unattended — a guard refuses them unless
   `ALLOW_DB_MIGRATIONS=1` is passed to that one command; never put it in `.env`):
   ```
   docker compose -f docker-compose.prod.yml --env-file .env up -d db
   docker compose -f docker-compose.prod.yml --env-file .env run --rm --no-deps -e ALLOW_DB_MIGRATIONS=1 backend alembic upgrade head
   docker compose -f docker-compose.prod.yml --env-file .env up -d --build
   ```
   The backend and worker refuse to run while the database revision differs from the code.
4. Open `http://<server-ip>:3000` and complete the **first-run setup** (pharmacy profile, GSTIN/state,
   admin user). Then create staff users under **Users**.
5. **Settings → Application settings**: review POS, credit, shift and backup settings
   (set *daily backup time*, e.g. `22:30`).
6. **Admin → System health** should show every check OK (the worker check turns green after one pass).

## 3. Starting automatically on Windows
- Docker Desktop: *Settings → General → Start Docker Desktop when you sign in*. The containers use
  `restart: always`, so they come back after a reboot once Docker starts.
- For a server that should run without anyone signing in, use a Windows scheduled task
  "At startup" running `docker compose -f D:\Accounting\docker-compose.prod.yml up -d`
  under a service account, or run Docker Engine on Linux.
- (Without Docker) the backend can run under NSSM as a Windows service:
  `nssm install PharmacyAPI D:\Accounting\backend\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000`
  plus a second service for `python -m app.worker`, and `next start` for the frontend. Docker is the supported route.

## 4. Local network (LAN)
1. Give the server a static IP in the router (DHCP reservation) — e.g. `192.168.1.10`.
2. Allow the UI port through Windows Firewall (admin PowerShell):
   `New-NetFirewallRule -DisplayName "Pharmacy ERP" -Direction Inbound -Protocol TCP -LocalPort 3000 -Action Allow -Profile Private`
3. Other counters open `http://192.168.1.10:3000`. Bookmark it; optionally add a hosts-file entry
   (`192.168.1.10 pharmacy.local`) on each counter PC.
4. Only port 3000 is published by the production compose file; MySQL and the API stay inside Docker.
   `LAN_BIND=192.168.1.10:3000` in `.env` restricts the UI to that interface.

## 5. Updating to a new version
1. Close all cash shifts; tell staff the system will be down for a few minutes.
2. **Back up and verify** (§6) — do not skip.
3. Replace the application files (keep `.env`).
4. Stop the app containers, then apply migrations explicitly and start the new version:
   `docker compose -f docker-compose.prod.yml stop backend worker frontend`
   `docker compose -f docker-compose.prod.yml run --rm --no-deps -e ALLOW_DB_MIGRATIONS=1 backend alembic upgrade head`
   `docker compose -f docker-compose.prod.yml up -d --build`
5. Check **Admin → System health** (migration revision = code head) and make a test sale/void on a TEST customer if required.
6. If anything is wrong: stop the stack, restore the backup (§7), and reinstall the previous version.

## 6. Backups
- **In the app:** Admin → Backups → *Back up now*, or the daily automatic backup (Settings → daily backup time).
  Each backup is a consistent snapshot, gzip-compressed SQL, with a SHA-256 checksum recorded in `backup_history`.
  *Verify* re-reads the file and compares every table's row count; if `BACKUP_VERIFY_DATABASE` is set
  (a scratch schema ending in `_restore_verify` that the app user can write), it also test-restores into it.
- **Copy backups off the server** (USB drive / NAS / cloud) at least weekly. Backups contain customer data — store them securely.
- **Command line (the original supported process):** `scripts\backup.ps1` runs `mysqldump` inside the database
  container and writes `backups\pharmacy_erp_<timestamp>.sql`; `scripts\verify_restore.ps1 <file>` restores it into
  an isolated scratch database and compares row counts with the live database.

## 7. Restoring (operator procedure — never from the UI)
1. Stop the application containers: `docker compose -f docker-compose.prod.yml stop backend worker frontend`.
2. Test the file first: `scripts\verify_restore.ps1 <file>` must report 0 differences against the expected counts.
3. Restore into the live schema (this REPLACES current data — only with the owner's written approval):
   - app backup: `gunzip -c file.sql.gz | docker exec -i pharmacy_db sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" <database>'`
   - mysqldump file: `docker exec -i pharmacy_db sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" <database>' < file.sql`
4. If the restored database is older than the code, apply migrations explicitly (§5 step 4); otherwise just start the
   containers. Check System health and the trial balance / integrity check.

## 8. External services and hardware (need configuration; cannot be verified without them)
- **WhatsApp:** `WHATSAPP_PROVIDER=manual` (default) opens WhatsApp links for staff to send. For automatic sending set
  `WHATSAPP_PROVIDER=cloud_api`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN` and approved templates
  (Meta Business account). A message counts as sent only when the provider accepts it; delivery status is shown as reported.
- **Barcode scanners:** USB/Bluetooth scanners in keyboard (HID) mode work in the POS search box; configure the scanner
  to send *Enter* after the code.
- **Printers:** invoices print through the browser (A4 PDF or thermal-width layout). Set the thermal printer's paper
  size in Windows. The app records that a print was requested; it cannot confirm the printer output.
- **HTTPS:** for access beyond the LAN put a reverse proxy (Caddy/nginx) with a certificate in front of port 3000 and set
  `SESSION_COOKIE_SECURE=true`.

## 8a. Branding & appearance (migration 011)
- Branding, logos and per-user themes are configured in the app; see `docs/BRANDING.md`.
- Migration 011 adds two empty tables (`brand_assets`, `user_preferences`). Deploy it with the normal update
  procedure (§5): back up and verify, stop the app containers, run the explicit migration command, then rebuild and
  start the frontend (`docker compose up -d --build frontend`) and start the backend and worker.
- Until 011 is applied, a restarted backend or worker refuses to run (schema guard), because the code expects 011.
- Logos are stored in the database, so no extra Docker volume is needed and backups include them.

## 9. Accounting go-live (cutover) — needs the owner's / CA's decisions
1. Agree the **books start date** (after any test data) and gather **opening balances** from the previous books.
2. Accounts → Set up accounting with that date; enter and post opening balances (preview must show no errors).
3. From that date every finalised sale, receipt, return, supplier bill/payment, expense, cash movement, stock adjustment
   and settlement posts automatically. Check **Accounts → Automatic postings** daily; retry any failed posting.
4. Month end: settle UPI/card clearing (Bank & settlements), import and reconcile the bank statement, close the period.
5. Year end: close all months, *Close year* (moves profit to retained earnings), send the **CA export pack**.
