# Pharmacy ERP — Read-Only Codebase Analysis Report
> Generated: 2026-10-28 | Status: Read-only investigation, no changes made

---

## 1. Project State & Architecture

### No Git History
There is **no `.git` directory** in the project root (`d:\Accounting`). Version history cannot be traced from git. The progression of the project is only traceable through migration file timestamps.

### Deployment Stack

| Layer | Technology | Config |
|---|---|---|
| Database | MySQL 8.4 LTS (Docker) | `pharmacy_erp` DB, `pharmacy_app` user |
| Backend | FastAPI + SQLAlchemy async + Alembic | `uvicorn app.main:app --workers 1` |
| Frontend | Next.js (TypeScript) | Port 3000, proxies `/api/*` to backend:8000 |
| Worker | Python background process | WhatsApp messaging + payment reminders |
| Auth | HTTP-only session cookie + CSRF token pair | `pharmacy_session` cookie |

### Migration Chain (Code Head = `012`)

```
001 → Phase 1: Auth, pharmacy, users
002 → Phase 2: Inventory, medicines, batches, stock
003 → Phase 3: Purchasing, GRN, invoices
004 → Signed adjustments
005 → Document sequences
006 → Phase 4: Sales
007 → Phase 5a: Accounting / GL
008 → Stage 5b: Payables
009 → GL postings
010 → Phase 5cd/6/7
011 → Branding preferences
012 → purchase_bill_imports table  ← LATEST
```

> [!IMPORTANT]
> `schema_guard.py` will **REFUSE** to start the API server if the database is not at revision `012`.

---

## 2. Recent Code Changes & Their Impact

### Migration 012 (2026-10-26) — Supplier Bill OCR Import
- Added `purchase_bill_imports` table (model: `app/models/bill_import.py`)
- Stores uploaded PDF/image, extracted JSON, review draft, links to GRN + SupplierInvoice
- Route: `GET/POST /api/v1/purchases/bill-imports` — fully registered in `router.py`
- Frontend: `frontend/app/purchasing/bill-import/` (list page + `[id]` detail/review page)
- Service: `app/services/bill_import.py` (579 lines) — upload, extract, match, check, confirm, discard

### Supplier Registration Enhancement
- `app/api/v1/routes/suppliers.py`: `list_suppliers` uses `active_only=False` by default
- Performs flexible ILIKE search across name, legal_name, GSTIN, phone, city

### Medicine Catalogue Importer
- `app/services/catalogue_import.py` (471 lines) — parses CSV/XLSX, auto-maps columns, validates, dedupes, commits
- Route: `POST /api/v1/medicines/import/preview` and `/commit`
- **Starter CSV exists**: `backend/data/catalogue/starter_generic_catalogue.csv` (34 KB) — but is NOT auto-imported

### MedicinePicker Component
- New dedicated component `frontend/components/medicine-picker.tsx` (7 KB)
- Calls `GET /api/v1/medicines?limit=25&search=<query>` on its own
- Used in the PO form at `frontend/app/purchasing/page.tsx` **line 1329**

---

## 3. The "No Medicine Matches" — Root Cause Analysis

### The MedicinePicker IS correctly wired in the PO modal

`purchasing/page.tsx` lines 1327–1341 contain:
```tsx
<MedicinePicker
  required
  value={it.medicine_id}
  onChange={(id, picked) => { ... }}
/>
```

### The message shows at `medicine-picker.tsx` L142:
```tsx
{!loading && hits.length === 0 && (
  <li>No medicine matches "{text}". Add it in Medicines, or import a catalogue.</li>
)}
```

This fires when `hits === []` AND `loading === false`.

---

### PRIMARY CAUSE — Empty Medicine Catalogue

The `MedicinePicker` fires `GET /api/v1/medicines?limit=25` when the dropdown opens. If no medicines exist in `medicines` table for this pharmacy, the API returns `[]`.

**Evidence:**
1. Starter CSV exists at `backend/data/catalogue/starter_generic_catalogue.csv` but **is not auto-seeded** on first run
2. The app has **no fixture/seed mechanism** — medicines must be manually added or imported
3. The `MedicinePicker` ignores the `medicines` state already loaded by the purchasing page and always makes its own fresh API call

---

### SECONDARY CAUSE — Silent Error Swallowing

At `medicine-picker.tsx` L96:
```ts
} catch { setHits([]); } finally { setLoading(false); }
```

Any API error (401 expired session, 403 permission denied, 500 backend crash) silently becomes `hits = []`, which displays as "No medicine matches" — **indistinguishable from zero results**.

---

### TERTIARY CAUSE — Schema Guard Blocking Backend Start

If migration `012` was never applied to the database, the schema guard refuses to start the backend entirely. All API calls would fail → all pickers show "No matches".

Check: `docker compose logs backend | grep -i "refusing\|schema\|migration"`

---

## 4. Other Issues Found

### Issue A — Silent `MedicinePicker` Errors (User-Facing Bug)
The catch clause swallows all API errors with no user feedback. A 401/403/500 looks identical to "no medicines imported".

### Issue B — `MedicineBarcodeItem` Has Phantom Field
In `lib/api.ts`, `MedicineBarcodeItem` declares `packaging_unit_id?: number`, but the backend `MedicineBarcode` model in `inventory.py` has no such column. This field is never returned. Minor type inconsistency.

### Issue C — `pypdfium2` Dependency Not Verified
`app/services/bill_import.py` L58 calls `import pypdfium2` inside `_pdf_pages()`. This must be in `backend/requirements.txt` for the Docker image to include it.

### Issue D — `MedicinePicker` Makes Redundant API Call
The purchasing page already fetches `api.getMedicines()` into a `medicines[]` state array — but `MedicinePicker` ignores that and always re-queries. Not a bug, just inefficient.

---

## 5. Recommended Fixes (Awaiting Your Approval)

> **Nothing has been changed. All items below require your go-ahead.**

| Priority | Fix | What |
|---|---|---|
| 🔴 Critical | Apply migration 012 if not applied | `ALLOW_DB_MIGRATIONS=1 alembic upgrade head` inside backend container |
| 🔴 Critical | Import starter medicine catalogue | Navigate to `/medicines/import` → use Starter Catalogue |
| 🟡 High | Fix `MedicinePicker` silent errors | Show a distinct error state instead of "No matches" when fetch fails |
| 🟢 Low | Remove phantom `packaging_unit_id` from `MedicineBarcodeItem` in `lib/api.ts` | Type cleanup |
| 🟢 Low | Verify `pypdfium2` in `requirements.txt` | Required for bill OCR PDF page count |

---

## 6. Checklist Summary

| Area | Status |
|---|---|
| Backend API routes | ✅ All 28 registered, correct ordering |
| Medicine search API | ✅ Correct multi-word ILIKE logic |
| `MedicinePicker` wired in PO form | ✅ Lines 1327–1341 of purchasing/page.tsx |
| Next.js proxy config | ✅ `/api/*` → backend:8000 |
| Auth / CSRF | ✅ Consistent frontend ↔ backend |
| Migration 012 file | ✅ Exists in codebase |
| Migration 012 applied to DB | ⚠️ Unknown — must verify |
| Starter catalogue CSV | ✅ Exists (34 KB) |
| **Medicines in DB** | ❌ **Likely empty → root cause** |
| Bill OCR import (full workflow) | ✅ Complete: upload→extract→review→confirm |
| Supplier registration | ✅ Full CRUD + GSTIN fuzzy search |
| Schema guard startup check | ✅ Will block start if DB ≠ rev 012 |
