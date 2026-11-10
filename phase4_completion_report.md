# Pharmacy ERP — Phase 4: POS, Sales, Billing, Credit & Customer Communication
## Completion Report

**Date:** 22 October 2026
**Alembic revision:** `006 (head)` on the live database (`pharmacy_erp`) and the test database (`pharmacy_erp_test`)
**Backend tests:** 147 passed (SQLite), plus 6 passed on MySQL 8.4.11 (concurrency suite, 4 runs)
**Frontend:** `tsc --noEmit` 0 errors · `next build` 21 routes compiled
**Browser scenarios A–H:** all passed on the live Docker stack, using test data prefixed `TEST`

> Verified vs. not verified is marked throughout. WhatsApp **Cloud API** sending and webhooks
> are verified **only with mocked HTTP** — no real message was sent. Legal, GST and
> accounting correctness has **not** been verified by a professional (see §10).

---

## 0. Foundation check (before building)

| Check | Result |
|---|---|
| Backend regression suite | 115 passed |
| Frontend type-check / production build | 0 errors / 12 routes compiled |
| Lint | **Not configured** — no ESLint config in the project; `next lint` would start an interactive setup. Not run. |
| MySQL | 8.4.11 running in `pharmacy_db`, healthy |
| Alembic | `005 (head)` = latest revision |

No critical defects were open. Earlier in this session, the high- and medium-priority review
findings had already been fixed (CSRF, login rate limits, locked document numbering,
payment validation, GSTIN/state consistency, ledger cost).

Defects in the existing code that were found and fixed **while building Phase 4**:
1. **Batch prices had no unit.** Batch MRP and rates are entered per pack (strip/box) but the pack size was not stored, so a per-tablet price could not be derived. Added `price_unit_id` / `price_conversion_factor` (backfilled from goods receipts), and goods-receipt MRP checks now compare per base unit.
2. **Wrong business day after midnight.** The server uses UTC in Docker, so between 00:00 and 05:30 IST "today" was the previous day. This affected expiry checks and document numbers (`ADJ-20261021-…` printed on 22 Oct). Added a business clock (`APP_UTC_OFFSET_MINUTES`, default 330) used for all business dates.
3. **Timestamps shown 5½ hours off.** MySQL returns timestamps without a zone, so the API sent UTC wall time that browsers read as local time. This affected all phases. They are now marked as UTC on load.
4. **Stale reads after lock waits.** Under REPEATABLE READ, a transaction that waited on a lock still read an older snapshot, which could have let two credit sales together exceed a credit limit. MySQL connections now use READ COMMITTED.

## 1. Implemented features

**POS (`/pos`)**
- Search by name, brand, generic/composition, manufacturer or barcode. A scanner's code + Enter adds the item immediately.
- Keyboard shortcuts: F2 search, ↑/↓ + Enter add, F4 customer, F8 payment, F9 or Ctrl+Enter finalise, Esc clear.
- Live quote from the same engine as finalisation, so the screen and the saved bill always match.
- Per line: unit choice (tablet/strip/box from configured conversions), quantity with indivisible-unit check, batch pin or "Auto (FEFO)", discount (permission-gated, capped by `pos.max_discount_percent`).
- Payments: cash with tendered and change; UPI/card with reference; split payments; udhaar (full or part credit) with due date and credit-limit override reason.
- Prescription panel appears automatically for Rx / Schedule H, H1, X and narcotic medicines.
- The draft is kept in the browser until the sale succeeds, together with its checkout key. A failed or interrupted finalisation can be retried without billing twice.
- After the sale: change due, receipt / A4 / PDF, open invoice.

**FEFO and inventory safety**
- Only ACTIVE (not quarantined, recalled or rejected), unexpired, in-stock batches can be sold. New `PATCH /batches/{id}/stock-status`.
- Candidate batches are locked (`SELECT … FOR UPDATE`, in id order) and stock is re-checked at finalisation.
- Each invoice line is one batch allocation, linked to its immutable `SALE` stock movement.

**Invoices**
- Numbers like `CCP/26-27/00001`: per financial year, consecutive, from a locked counter, at most 16 characters (GST rule 46).
- Thermal 80 mm and A4 layouts with ORIGINAL / DUPLICATE labels and paid / part-paid / credit / returned / voided status. Server-side PDF. Reprints change nothing but the print counter.
- History with search (number, customer, mobile, medicine), filters (dates, method, payment status, credit, overdue, voided), pagination and CSV export.

**Payments**
- Cash (applied amount kept separate from tendered and change), UPI, card, bank transfer and other.
- Non-cash payments are "RECORDED — not verified" until a user marks them verified.
- Payment reversal for errors (e.g. a UPI transfer that never arrived), with a ledger entry.

**Customers and udhaar**
- Customer records: mobile (normalised to +91), consent, opt-out, credit limit, terms.
- Append-only customer ledger: INVOICE, PAYMENT, RETURN_CREDIT, REFUND, VOID_REVERSAL, PAYMENT_REVERSAL, ADJUSTMENT_CREDIT, WRITE_OFF. The balance is always the sum of the ledger and is never stored or overwritten.
- Partial payments, allocation to chosen invoices or oldest-first, advances and credit notes (unapplied credit) that can be applied later, adjustments and write-offs (permission plus a reason).
- Statements with running balance, ageing buckets (not due, 1–30, 31–60, 61–90, 90+), and a reconciliation check ("ledger = open bills − unapplied credit").

**Returns** (`/sales/returns`)
- Returns are linked to the invoice line and can't exceed the quantity still returnable. Partial and full returns are supported.
- The value is the line's share of the actual price charged (after discount, GST included); the last return of a line takes the exact remainder.
- The return first reduces the invoice's balance; the rest is refunded (original or chosen method) or kept as a credit note.
- Only sealed, saleable items in an active, unexpired batch are restocked (`SALES_RETURN` movement, batch average cost updated). Others are recorded as WRITTEN_OFF.

**Voids**
- Need `sales.cancel` and a reason. Not allowed after returns or write-offs.
- Stock is restored (`SALE_VOID` movements), allocations are reversed, money is refunded or kept as customer credit, the ledger gets a VOID_REVERSAL, and pending messages are cancelled. Nothing is deleted.

**Shifts and day-end** (`/day-end`)
- Opening cash, paid-outs and paid-ins (manager), expected cash, counted cash, variance and note, manager review.
- UPI, card and credit are listed separately and never counted as cash.
- Day report by method, plus a "cash taken with no shift open" figure.

**Reports**
- Dashboard KPIs: sales today/month, bills, average bill, collections, credit given, outstanding, overdue, returns.
- Summary, daily, item-wise, batch-wise, cashier-wise, payment methods, credit & collections, returns & voids, discounts, tax summary (sales vs credit notes by rate), invoice register and ageing. CSV export.
- Gross vs net sales, tax, collections and outstanding are labelled separately. Gross margin is shown as indicative, based on batch weighted-average cost.

**WhatsApp** (provider-independent, with a durable outbox)
- Manual mode: wa.me links, honestly recorded as `MANUAL_SHARE_OPENED` ("opened in WhatsApp — delivery not tracked").
- Cloud API mode: templates or text, optional PDF attachment, message ID recording, retries with back-off, and signed-webhook status updates (sent → delivered → read, forward only).
- Consent required, duplicate-send guard (10 minutes, "Send again" to override), retry reuses the same message row, no medicine details in messages.

**Reminders**
- Upcoming, due-today and overdue windows, plus manual reminders from the invoice and ledger pages (at most one per invoice per day).
- Dedupe keys; eligibility re-checked before sending; opt-out, pause and dispute; send-window hours; editable templates.
- Test mode: preview a scan for any date without writing anything.

**Worker**
- New `pharmacy_worker` container (`python -m app.worker`) for queued sends and reminder scans, with a `--once --dry-run --date` test mode.

## 2. Files

**Backend — new**
`app/models/sales.py` · `app/core/clock.py` · `app/core/json.py` · `app/services/{pos,sales,sales_common,receivables,sales_returns,shifts,sales_reports,messaging,invoice_pdf,app_settings}.py` · `app/worker.py` · `app/schemas/sales.py` · `app/api/v1/routes/{pos,customers,sales,sales_ops,sales_reports,messaging}.py` · `alembic/versions/006_20261021_phase4_sales.py` · `tests/{sales_helpers,test_sales_pos,test_sales_credit,test_sales_reports}.py` · `tests/mysql/{__init__,conftest,test_mysql_concurrency}.py`

**Backend — modified**
`app/models/{inventory,__init__}.py` · `app/core/{config,database,deps}.py` · `app/services/{auth,inventory,purchasing,numbering}.py` · `app/schemas/inventory.py` · `app/api/v1/routes/{batches,inventory}.py` · `app/api/v1/router.py` · `tests/{conftest,test_hardening}.py` · `requirements.txt` (+`fpdf2`, `tzdata`)

**Frontend — new**
`lib/sales-api.ts` · `components/{sales-ui,customer-modal}.tsx` · `app/pos/page.tsx` · `app/sales/page.tsx` · `app/sales/[id]/page.tsx` · `app/sales/[id]/print/page.tsx` · `app/sales/returns/page.tsx` · `app/customers/page.tsx` · `app/customers/[id]/page.tsx` · `app/receivables/page.tsx` · `app/day-end/page.tsx` · `app/reports/sales/page.tsx` · `app/reports/receivables/page.tsx` · `app/settings/communications/page.tsx`

**Frontend — modified:** `lib/api.ts` (exports `fetchJson`) · `components/Navbar.tsx` (POS/Sales entries; full menu ≥1400 px)

**Project:** `docker-compose.yml` (worker service) · `.env.example` (placeholders only) · `README.md` · `docs/PHASE4_OPERATIONS.md` (new)

## 3. Database migration

`006_20261021_phase4_sales.py` (005 → 006):
- **13 tables:** `customers`, `cash_shifts`, `cash_movements`, `sales_invoices`, `sales_invoice_items`, `sale_prescriptions`, `customer_payments`, `payment_allocations`, `customer_refunds`, `customer_ledger_entries`, `sales_returns`, `sales_return_items`, `message_outbox`.
- **`medicine_batches`:** gains `price_unit_id`, `price_conversion_factor` and `stock_status`, backfilled.
- **Permissions:** the 14 new codes are seeded and granted to the existing default roles.

Verification on `pharmacy_erp_test`: upgrade → downgrade to 005 → upgrade again, all clean.

On the live `pharmacy_erp`: a backup was taken first (`backups/pharmacy_erp_pre_006_20261021_235320.sql`, 30 tables), then the backend upgraded it on start. Current revision: `006 (head)`.

## 4. API endpoints and permissions

| Method | Endpoint | Permission |
|---|---|---|
| PATCH | `/api/v1/batches/{id}/stock-status` | stock.adjust |
| GET / POST | `/api/v1/pos/search`, `/api/v1/pos/quote` | pos.access |
| GET / POST | `/api/v1/customers` | customers.view / customers.manage (+ sales.credit_override to set a limit) |
| GET / PUT | `/api/v1/customers/{id}` | customers.view / customers.manage |
| GET | `/api/v1/customers/{id}/statement`, `/reconcile` | customers.view |
| POST | `/api/v1/customers/{id}/payments`, `/apply-credit` | payments.receive |
| POST | `/api/v1/customers/{id}/adjustments` | customers.adjust |
| POST / GET | `/api/v1/sales/invoices` | sales.create (+ sales.discount, sales.credit, sales.credit_override, sales.prescription as needed) / sales.view |
| GET | `/api/v1/sales/invoices/export.csv` | reports.export |
| GET | `/api/v1/sales/invoices/{id}`, `/pdf`, `/messages`; POST `/print` | sales.view |
| POST | `/api/v1/sales/invoices/{id}/void` | sales.cancel |
| GET | `/api/v1/sales/invoices/{id}/prescription` | prescriptions.view (each view is audit-logged) |
| PATCH | `/api/v1/sales/invoices/{id}/reminders` | payments.receive |
| POST | `/api/v1/sales/invoices/{id}/whatsapp`, `/reminder` | messaging.send |
| POST / GET | `/api/v1/sales/returns`, `/api/v1/sales/returns/{id}` | sales.refund / sales.view |
| POST | `/api/v1/sales/payments/{id}/verify` / `/reverse` | payments.receive / customers.adjust |
| GET | `/api/v1/receivables`, `/receivables/aging` | customers.view |
| POST / GET | `/api/v1/shifts`, `/shifts/{id}`, `/shifts/{id}/close` | shifts.operate |
| GET | `/api/v1/shifts/current` | pos.access |
| GET / POST | `/api/v1/shifts/day-end`, `/shifts/{id}/cash-movements`, `/shifts/{id}/review` | shifts.review |
| GET | `/api/v1/reports/sales/dashboard`, `/reports/sales/{summary,daily,items,batches,cashiers,payments,credit,returns,discounts,tax,register,aging}` | reports.view (`?format=csv` needs reports.export) |
| GET / PUT | `/api/v1/messaging/config` | messaging.configure |
| GET / POST | `/api/v1/messaging/outbox`, `/{id}/retry`, `/{id}/share`, `/{id}/cancel`, `/reminders/scan` | messaging.send (a real scan, not dry run, needs messaging.configure) |
| GET / POST | `/api/v1/whatsapp/webhook` | public; verify token / X-Hub-Signature-256 |

**New permission codes:** `pos.access`, `sales.discount`, `sales.prescription`, `prescriptions.view`, `sales.credit`, `sales.credit_override`, `payments.receive`, `customers.view`, `customers.adjust`, `messaging.send`, `messaging.configure`, `reports.export`, `shifts.operate`, `shifts.review`.

**Default grants:**

| Role | Permissions |
|---|---|
| Owner / Admin | All |
| Pharmacist | POS, discount, prescription sales and viewing, credit sales, payments, customers, messaging, shifts |
| Cashier | POS, plain sales, payments, customers, messaging, shifts — no discounts, credit, Rx, voids, reports or adjustments |

All checks are enforced on the backend; the UI only hides what the user can't do.

## 5. Configuration and optional services

All configuration lives in `.env`, with placeholders in `.env.example`:

| Group | Variables |
|---|---|
| Time zone | `APP_UTC_OFFSET_MINUTES` (330) |
| WhatsApp | `WHATSAPP_PROVIDER` (`manual` default / `cloud_api`), `WHATSAPP_API_VERSION`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_APP_SECRET`, `WHATSAPP_WEBHOOK_VERIFY_TOKEN`, `WHATSAPP_TEMPLATE_*`, `WHATSAPP_TEMPLATE_LANGUAGE`, `WHATSAPP_SEND_INVOICE_PDF` |
| Worker | `WORKER_INTERVAL_SECONDS`, `WORKER_REMINDER_SCAN_MINUTES` |

**Optional external service:** the WhatsApp Business Platform. It is **not configured**, and the app runs in manual mode. Delivery and read receipts additionally need the webhook reachable over public HTTPS (see `docs/PHASE4_OPERATIONS.md` §4).

Shop settings use defaults and can be changed: walk-in label, max discount, require open shift, minimum days to expiry, rounding, prefixes, credit terms, Rx schedules, variance threshold, templates and reminder rules.

## 6. Commands run and results

| Command | Result |
|---|---|
| `pytest -q` (backend, SQLite) | **147 passed, 6 skipped** (the 6 are the MySQL tests, skipped without `TEST_MYSQL_URL`) |
| `TEST_MYSQL_URL=…/pharmacy_erp_test pytest tests/mysql` | **6 passed**, 4 separate runs |
| `alembic upgrade head` / `downgrade 005` / `upgrade head` on `pharmacy_erp_test` | all applied cleanly |
| `mysqldump` of `pharmacy_erp` before migrating | 64 KB, 30 tables |
| `docker compose up -d --build backend worker frontend` | all healthy; `alembic current` → `006 (head)` |
| `npx tsc --noEmit` | 0 errors |
| `npm run build` | compiled, 21/21 routes |
| `npm run lint` | not run — no ESLint configuration in the project |

New tests cover spec items 1–27:
- **Sales and stock:** cash/UPI/card/split payments, change, paid/part/credit sales, outstanding balance, settlement, FEFO, expired/quarantined/insufficient stock, indivisible units, barcode search.
- **Money rules and safety:** credit limits and overrides, duplicate finalisation and payments, decimal/discount/GST/rounding, rollback on a simulated crash after stock deduction.
- **Prescriptions and permissions:** Rx/H/H1 controls and prescription privacy, cashier permission limits.
- **Returns and voids:** partial and full returns, limits, repeat protection, receivable reduction, credit notes, voids with audit, payment reversal and write-off.
- **Documents and reports:** print/PDF, report and dashboard figures and CSV, worker dry run.
- **WhatsApp and reminders:** wa.me encoding, Cloud API success/400/503 with mocked HTTP, webhook signature and forward-only status, reminder windows, dedupe, opt-out, dispute, paid invoices.
- **MySQL (item 12):** no overselling, concurrent duplicate submissions, unique consecutive numbers, credit limit under concurrency, concurrent duplicate payments, concurrent returns.

## 7. Browser verification (live Docker stack, in-app browser)

Test data was created and clearly marked: supplier "TEST Distributor (Phase 4 demo)"; medicines "TEST Paracetamol 650" (barcode `TEST8901000000011`), "TEST Cetirizine 10" and "TEST Azithromycin 500" (Schedule H); batches `TEST-*`; customer "TEST Customer Ramesh" (+91 90000 00001).

| Scenario | What was done in the UI | Verified result |
|---|---|---|
| **A** Normal sale | Typed the barcode + Enter, searched a second item, quantity 2, split ₹60 UPI + ₹200 cash, F9 | `CCP/26-27/00001`, ₹160; change ₹100; FEFO took the earlier-expiring batch; stock 75→45 and 300→290 tablets; SALE movements reference the invoice; thermal receipt rendered with header, GSTIN, DL, batch/expiry, ORIGINAL |
| **B** Credit sale | Customer picked, 10 strips (₹1,000), Udhaar with ₹400 cash | `00002`: paid ₹400, balance ₹600, due 20 Nov 2026; customer outstanding ₹600 |
| **C** Later collection | "Collect payment" ₹300 cash, then ₹300 UPI | Balance ₹600 → ₹300 → ₹0, status PAID, customer ₹0, ledger reconciled; UPI shown "not verified" |
| **D** WhatsApp | Invoice → WhatsApp → Send (manual mode) | Recorded `MANUAL_SHARE_OPENED`, "delivery not tracked", no delivered time; link `wa.me/919000000001?text=…` correctly encoded (`&` → `%26`); no medicine names. The browser pane blocks scripted popups, so WhatsApp itself was not opened. |
| **E** Overdue reminder | Receivables → scan date 22 Nov → Preview, then Create, then Preview again | Preview listed only the unpaid `00003` (paid `00002` absent) and wrote nothing; Create made one PENDING reminder with the overdue text; second preview: "Already created (#2, PENDING)" |
| **F** Return | Returns page, 10 tablets of `00003`, saleable | `SR/26-27/00001` ₹100 taken off the balance (₹200→₹100), no refund; batch 170→180 with SALES_RETURN movement; ledger reconciled |
| **G** Failure safety | Bill with one unfillable line; then two identical submissions at once | 422 with a clear message; stock, invoices and movements unchanged, no number used; double submit → one invoice `00004` (201 + 200 replay), stock deducted once |
| **H** Day-end | Shift opened with ₹1,000, ₹50 paid out, counted ₹1,775, closed | Expected ₹1,780 = 1,000 + 530 cash sales + 300 cash collections − 50; UPI ₹360 and credit ₹800 listed separately; variance −₹5 with note; day report splits CASH / UPI per business day |

Issues found during browser testing and fixed (then rebuilt, redeployed and rechecked):
1. Report and quote money was sent as JSON floats; now exact decimal strings.
2. "₹-0.00" for zero discounts.
3. WhatsApp tabs opened after the server reply could be popup-blocked; the tab now opens on the click itself.
4. The navbar overflowed horizontally at 1000–1250 px.
5. Document-number dates used UTC instead of the business date.

## 8. Not done, limitations, and manual tests still needed

**Not verified**
- **WhatsApp Cloud API** — tested only with mocked HTTP; no real credentials or messages.
- **Webhook** — needs public HTTPS; a LAN install can't receive it.
- **Hardware** — no physical thermal printer or barcode scanner was tested. The receipt is 80 mm browser-print HTML, and the scanner was emulated with keyboard input.
- **Full regression suite on MySQL** — only the 6 concurrency tests run on MySQL; the other 147 run on SQLite.

**Not built**
- **Customer opening balances** (existing paper khata) can't be entered. Adjustments only reduce an existing invoice. A proper "opening balance" document is needed before go-live if customers already owe money.
- **Credit-limit overrides** need the acting user to hold `sales.credit_override`. There is no second-person manager PIN approval at the counter.
- **Statutory registers** — no Schedule H1 register report (the data is captured in `sale_prescriptions`), no GSTR-1 JSON export, no e-invoicing (IRN).
- **Customer self-service** — no customer-accessible invoice link (the app is LAN-only); WhatsApp messages carry the summary only, or the PDF in Cloud API mode.
- **Languages** — no multi-language templates beyond editing the text (`preferred_language` is stored but not used yet).

**Known limitations**
- **PDF text** uses built-in fonts: "Rs." instead of "₹", and non-Latin characters are replaced.
- **Discount floor** — discounts are capped by percentage but not prevented from going below cost.
- **Print counting on GET** — `GET /sales/invoices/{id}/pdf` records a print (a counter-only side effect on a GET).
- **Test data in the live database** — TEST-prefixed records remain. The ledger and stock movements are deliberately not deletable. Start from a fresh database before real use, or keep them clearly marked.
- **Accessibility** — some form fields lack accessible names in automation (labels wrap inputs); keyboard flow works.
- **No user-management screen** — staff accounts with the Pharmacist/Cashier roles must be created through the API until a Users page is added.
- **No version control** — the project is still not a git repository.

**Manual tests still needed:** a real printer and scanner; real WhatsApp (both modes on a phone); multiple counters at once on the LAN; a mobile or tablet layout pass; and a restore drill from backup.

## 9. Accounting, GST and regulatory assumptions to validate with a CA / compliance adviser

**GST and pricing**
1. **MRP retail mode.** Selling rates include GST; tax is taken out per line with the inclusive formula, with CGST/SGST split (SGST takes the rounding paisa). The sale is treated as inter-state only when a customer GSTIN is from another state.
2. **Rounding.** The invoice total is rounded to the nearest rupee (`invoice.round_to_rupee`), with a round-off line. A return of the whole invoice also returns the round-off.
3. **Invoice numbers** run per financial year (April–March) in the format `PREFIX/YY-YY/NNNNN`, are consecutive, and are at most 16 characters. Voided invoices keep their numbers (they are never deleted).
4. **Discounts** are taken from the GST-inclusive price, before tax is extracted.

**Returns, credit and cash**

5. **Return value** is a proportional share of what was actually charged. Returns create credit-note style records. The tax summary reports them separately, but they are not yet formatted for GSTR-1 (CDNUR/CDNR).
6. **Udhaar**: a credit limit of 0 means no credit without an override. Advances (overpayments) are allowed by default. Write-offs reduce the receivable only — no bad-debt accounting yet (Phase 7).
7. **Gross margin** uses batch weighted-average cost excluding GST, assuming input tax credit is claimed. It is indicative only.
8. **Cash** counts only the amount kept (tendered minus change). UPI and card are recorded, not verified, until someone marks them verified.

**Prescriptions and privacy**

9. **Prescription rules (defaults, to be confirmed):**
   - A prescription is needed for `requires_prescription` medicines and for schedules H, H1, X and NARCOTIC.
   - H1, X and NARCOTIC also need the patient's and prescriber's addresses.
   - Only users with `sales.prescription` can finalise these bills.
   - No exemptions were invented.
   - Schedule X / narcotics may need forms and registers that this does not provide.
10. **Data protection (DPDP Act 2023)**:
    - Customer mobile numbers, WhatsApp consent and prescription details are personal data.
    - Consent is recorded with a timestamp, and opt-out is honoured.
    - Prescription details are permission-gated, and each view is audit-logged.
    - Backups contain this data and must be secured.
    - Retention periods (Rule 65: at least 2 years) and customer data requests need a written policy.
11. **WhatsApp**: business-initiated messages need approved templates and opt-in under Meta's policies. Reminder wording is polite and contains no medicine details. Confirm the frequency (default: at most 4 overdue reminders, 7 days apart, 9:00–20:00) is acceptable.

## 10. Running the updated application

```bash
cd D:\Accounting
docker compose up -d --build        # db, backend (migrates to 006), worker, frontend
```

* Open `http://localhost:3000`, or `http://<server-LAN-IP>:3000` from other counters.
* Owner/Admin has all Phase 4 permissions. **There is no user-management screen yet** (a Phase 1 gap): Pharmacist/Cashier users can only be created through the API (`POST /api/v1/users` with a role ID from `GET /api/v1/roles`).
* Typical day: Day-end → open shift → POS → (Receivables / reminders) → Day-end → count and close → manager review.
* Back up after day-end (see `docs/PHASE4_OPERATIONS.md` §6).

Phase 5 has **not** been started.
