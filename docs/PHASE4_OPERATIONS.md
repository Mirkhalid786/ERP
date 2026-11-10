# Phase 4 — POS, Sales, Credit & WhatsApp: Operations Guide

This guide covers running and configuring the Phase 4 sales module. For what was
built and how it was verified, see `phase4_completion_report.md` in the project root.

---

## 1. Services

| Container | Purpose |
|---|---|
| `pharmacy_db` | MySQL 8.4 (all data, including the WhatsApp outbox) |
| `pharmacy_backend` | FastAPI API; runs `alembic upgrade head` on start |
| `pharmacy_worker` | **New.** Sends queued WhatsApp messages and creates payment reminders |
| `pharmacy_frontend` | Next.js UI on port 3000 |

```bash
docker compose up -d --build            # build and start everything
docker compose logs -f worker           # watch the worker
```

The backend runs uvicorn **without** `--reload`, and the frontend image contains its
source code. After changing code: `docker compose restart backend worker` and
`docker compose up -d --build frontend`.

## 2. Scheduler / worker

The worker (`python -m app.worker`) is a separate, long-running process. It does not
depend on web requests, so nothing is lost when a request ends; every message is a row
in `message_outbox`, and a restart continues where it stopped.

Each pass (every `WORKER_INTERVAL_SECONDS`, default 60):
1. Marks messages stuck in `SENDING` for over 10 minutes as `FAILED` ("outcome unknown —
   check WhatsApp before retrying"). They are never re-sent automatically, so customers do
   not get duplicates.
2. Sends due `QUEUED` Cloud API messages (retrying 429/5xx with back-off, up to 5 attempts).
3. Every `WORKER_REMINDER_SCAN_MINUTES` (default 60): runs the reminder scan for each pharmacy.

Manual / test runs:

```bash
docker exec pharmacy_worker python -m app.worker --once --dry-run                 # what would be created; writes nothing
docker exec pharmacy_worker python -m app.worker --once --dry-run --date 2026-12-01
docker exec pharmacy_worker python -m app.worker --once                           # one real pass
```

Without Docker (Windows Task Scheduler / cron): run `python -m app.worker --once` every
few minutes from the `backend` folder with the same `.env`.

## 3. Payment reminders

* An invoice is eligible when it is finalised, has a balance, has a due date and a customer
  who is active, agreed to WhatsApp (`whatsapp_consent`), has not opted out, and the invoice
  is neither paused nor disputed.
* Windows: **upcoming** (within *n* days before due), **due today**, and **overdue** — one per
  `overdue_interval_days` window, at most `max_overdue_reminders`. A `dedupe_key` per
  invoice and window makes duplicates impossible (unique index).
* Eligibility is checked again just before sending; reminders for invoices that were
  paid, voided, paused or disputed in the meantime are **cancelled**.
* **Manual mode** (no Cloud API): the scan creates `PENDING` reminders. Staff open them
  from *Receivables → Waiting to be sent* ("Open WhatsApp"), which records
  `MANUAL_SHARE_OPENED` — not "delivered".
* **Automatic sending** happens only when `reminders.auto_send_enabled` is on **and** the
  Cloud API is configured, and only within the send-window hours.
* Templates and rules: *Sales → WhatsApp* (`/settings/communications`), permission
  `messaging.configure`.

## 4. WhatsApp configuration

### Mode B — manual sharing (default, no credentials)
`WHATSAPP_PROVIDER=manual`. "Send" opens `https://wa.me/<number>?text=<message>`; the
user reviews it and presses Send in WhatsApp. The app records only that WhatsApp was opened.
No PDF is attached in this mode; the message contains invoice number, date, total, paid
and balance (no medicine names).

### Mode A — official WhatsApp Business Platform (Cloud API)
1. In Meta Business Manager create a WhatsApp Business app, add a phone number, and get
   the **phone number ID**, a **permanent access token** (system user) and the **app secret**.
2. Create and get approval for message templates (needed for business-initiated messages
   outside the 24-hour customer-service window). Body parameters must be in this order:
   * Invoice: customer_name, pharmacy_name, invoice_number, invoice_date, invoice_total, amount_paid, balance_due
   * Reminder upcoming / due today: customer_name, balance_due, invoice_number, due_date
   * Reminder overdue: customer_name, balance_due, invoice_number, due_date, pharmacy_name
3. Set in `.env` (never commit real values):
   ```
   WHATSAPP_PROVIDER=cloud_api
   WHATSAPP_PHONE_NUMBER_ID=...
   WHATSAPP_ACCESS_TOKEN=...
   WHATSAPP_APP_SECRET=...
   WHATSAPP_WEBHOOK_VERIFY_TOKEN=<any random string>
   WHATSAPP_TEMPLATE_INVOICE=<approved template name>
   WHATSAPP_TEMPLATE_REMINDER_UPCOMING=...
   WHATSAPP_TEMPLATE_REMINDER_DUE_TODAY=...
   WHATSAPP_TEMPLATE_REMINDER_OVERDUE=...
   WHATSAPP_SEND_INVOICE_PDF=true        # optional: attach the PDF invoice
   ```
4. `docker compose up -d backend worker` (restart) and check *Sales → WhatsApp* shows
   "Cloud API configured".

Tokens are read only from the environment and are never sent to the browser or logged.

### Webhook (delivery / read receipts)
Meta must reach `https://<public-host>/api/v1/whatsapp/webhook` over HTTPS.
* Verification: `GET` with `hub.mode=subscribe` and your `WHATSAPP_WEBHOOK_VERIFY_TOKEN`.
* Status callbacks: `POST`, accepted only with a valid `X-Hub-Signature-256` (HMAC-SHA256
  of the body with `WHATSAPP_APP_SECRET`).
* Statuses only move forward: sent → delivered → read. A late "failed" after "delivered"
  is ignored.

**LAN installations are not reachable from the internet**, so without a tunnel or reverse
proxy (e.g. a Cloudflare Tunnel exposing only `/api/v1/whatsapp/webhook`) messages stay
at `SENT` ("sent to WhatsApp — delivery not yet confirmed"). That is accurate, not an error.
Do not expose the rest of the API or the database to the internet.

## 5. Time zone

Timestamps are stored in UTC. Business dates — invoice day, due dates, day-end, report
date ranges, document-number periods — use `APP_UTC_OFFSET_MINUTES` (India: 330).

## 6. Backups

Phase 4 adds financial records that must never be lost or edited outside the app:
`sales_invoices`, `sales_invoice_items`, `customer_payments`, `payment_allocations`,
`customer_refunds`, `customer_ledger_entries`, `sales_returns`, `cash_shifts`,
`sale_prescriptions` (sensitive) and `message_outbox`.

```bash
docker exec pharmacy_db sh -c 'mysqldump -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" --single-transaction --no-tablespaces "$MYSQL_DATABASE"' > backups/pharmacy_erp_$(date +%Y%m%d).sql
```

* Take a backup at least daily (after day-end) and before every upgrade/migration.
* Keep backups off the server too (encrypted USB / second PC). They contain customer
  phone numbers and prescription details.
* Restoring an old backup rolls back sales *and* stock together; never restore single tables.

## 7. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| "Open a cash shift…" when taking cash | `pos.require_open_shift` is on; open a shift on *Day-end* |
| Sale rejected "Insufficient saleable stock" | Stock is expired, quarantined/recalled, within `pos.min_days_to_expiry`, or simply short. Check *Inventory* |
| "Prescription details are required" / 403 | The medicine is Rx/Schedule H/H1/X; only users with `sales.prescription` (pharmacist) can finalise |
| "Credit limit exceeded" (403) | A user with `sales.credit_override` must finalise with a reason, or raise the limit |
| WhatsApp button opens nothing | Browser popup blocker — allow popups for the ERP address |
| Message stuck at SENT | No webhook reachable (see §4); delivery cannot be confirmed |
| Message FAILED "outcome unknown" | Worker was interrupted mid-send; check WhatsApp before using *Retry* |
| Customer ledger shows "MISMATCH" | Should never happen; stop and report — compare `/api/v1/customers/{id}/reconcile` |
| Invoice number error "longer than 16 characters" | Shorten `invoice.sales_prefix` (GST allows 16 characters) |

## 8. MySQL integration tests

The normal test suite uses SQLite. Row locking and concurrency are tested against a
**disposable** MySQL database whose name must end in `_test`:

```bash
docker exec pharmacy_db sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "CREATE DATABASE IF NOT EXISTS pharmacy_erp_test; GRANT ALL ON pharmacy_erp_test.* TO \`$MYSQL_USER\`@\`%\`;"'
cd backend
TEST_MYSQL_URL="mysql+aiomysql://pharmacy_app:<password>@127.0.0.1:3306/pharmacy_erp_test" .venv/Scripts/python -m pytest tests/mysql -v
```

Every MySQL test drops and recreates all tables in that database.
