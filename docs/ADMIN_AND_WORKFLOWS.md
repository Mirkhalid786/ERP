# Pharmacy ERP — Administrator & Workflow Guide

This guide describes how the delivered system actually behaves (verified against the
code and the automated test suite, November 2026). For installation, Docker and LAN
setup, recovery and upgrades, see `PRODUCTION_DEPLOYMENT_GUIDE.md` and `DEPLOYMENT.md`.

---

## 1. Roles and permissions (as seeded by default)

Permissions are enforced on the **backend** for every sensitive endpoint — hiding a
menu item is never the only protection. A user who opens a URL or calls an API
directly without the permission gets **403**; only an expired/missing session (**401**)
sends the user back to the login screen.

| Capability | Owner / Admin | Pharmacist | Inventory Manager | Cashier |
|---|:---:|:---:|:---:|:---:|
| POS billing (`pos.access`, `sales.create`) | ✅ | ✅ | — | ✅ |
| Sales view | ✅ | ✅ | — | ✅ |
| Sales returns / refunds (`sales.refund`) | ✅ | ✅ | — | — |
| Sales discounts (`sales.discount`) | ✅ | ✅ | — | — |
| Prescription sales (`sales.prescription`) | ✅ | ✅ | — | — |
| Credit sales / receive customer payments | ✅ | ✅ | — | receive only |
| Customers | ✅ | ✅ | — | ✅ |
| Medicine catalogue view | ✅ | ✅ | ✅ | ✅ |
| Medicine catalogue manage | ✅ | ✅ | ✅ | — |
| Stock view | ✅ | ✅ | ✅ | ✅ |
| Stock adjustments (`stock.adjust`) | ✅ | — | ✅ | — |
| Purchases view | ✅ | ✅ (view) | ✅ | — |
| Purchases create / post / return | ✅ | — | ✅ | — |
| Finalise supplier invoices | ✅ | — | ✅ | — |
| Supplier payments view | ✅ | — | ✅ | — |
| Supplier payments create / reverse | ✅ | — | — | — |
| Expenses | ✅ | view + create | view + create | create |
| Accounting (all `accounting.*`) | ✅ | — | — | — |
| Users, roles, settings, audit, backups | ✅ | — | — | — |
| Reports | ✅ | ✅ | ✅ | — |
| Cash shifts (`shifts.operate`) | ✅ | ✅ | — | ✅ |

Role assignments are data, not code: an Owner/Admin can adjust any role's
permissions under **Users → Roles** without a code change.

---

## 2. Direct purchasing — the normal workflow

**A supplier invoice means the goods have already arrived.** There is no mandatory
Purchase Order → GRN → Invoice chain for ordinary purchases.

**Purchasing → New Purchase**

1. Choose the supplier; enter invoice number, invoice date, due date / payment terms.
2. Add medicine lines from the master catalogue (search by brand, salt, manufacturer
   or barcode). Per line: quantity, **free quantity**, purchase rate, discount, GST,
   batch number, expiry date, MRP, selling price, rack location.
   The same medicine can appear in multiple batches on one invoice.
3. Check the totals: subtotal, discounts, taxable amount, GST split (CGST/SGST or
   IGST), freight/other charges, round-off, grand total. GST-inclusive and
   GST-exclusive pricing are both supported.

**Save Draft** — stores the invoice and an internal draft GRN. *No stock, no payable,
no accounting entries are created.*

**Save & Post Purchase** — one atomic transaction:
creates the supplier invoice (status `POSTED`), posts the internal GRN for
traceability, creates/updates batch stock, writes stock-ledger movements, records
the supplier payable, posts accounting entries (when accounting is set up) and
writes an audit record. Posting is guarded: a draft can be posted only once
(retrying returns a conflict, never double stock), and a duplicate supplier
invoice number for the same supplier is rejected.

**Purchase Orders** remain available as an *optional* advance-ordering tool and can
be converted into a direct purchase with supplier and lines prefilled.

**Editing / deleting / cancelling**
- Draft: editable and deletable (no stock/financial impact).
- Posted: never hard-deleted. **Cancel** (with a reason, permission-checked)
  reverses stock, GRN, payable and GL entries — but only if nothing from the
  purchase has been sold/consumed and no supplier payment is allocated to it.
  Free quantities are reversed together with billed quantities.
- If stock was partly consumed, cancellation is blocked with an explanation;
  use a **purchase return** or **credit note** for the remaining quantity instead.

---

## 3. Supplier returns and credit notes

- **Physical return** (Purchasing → Returns): full or partial, batch-specific.
  Posting reduces stock, adjusts the supplier's outstanding balance and posts
  accounting entries. Returnable quantity is validated against what was received
  minus what was already returned.
- **Financial credit note** (Payables / Purchasing → Credit Notes): for rate
  corrections, extra discounts, short-billing or GST adjustments. It reduces what
  you owe the supplier **without touching physical stock**, unless it is linked to
  an actual goods return. Original invoice references and audit history are kept.

---

## 4. Supplier payments — how allocation works

**Payables → Record Payment**: choose supplier, amount, date, method
(Cash / Bank transfer / UPI / Cheque / other) and reference number.

- **Auto-allocation (default):** the payment is applied to the supplier's open
  posted invoices **oldest due date first** (invoice date breaks ties), continuing
  across invoices until the money runs out.
  Example: bills ₹250 (due first), ₹800, ₹1,000; a payment of ₹500 allocates
  ₹250 + ₹250, leaving ₹0 / ₹550 / ₹1,000 — total payable ₹1,550.
- An **advance** (unallocated remainder) exists *only* when the payment exceeds
  the supplier's total outstanding, or when you deliberately choose advance mode.
  An advance can be applied to later bills from the payment's **Allocate** action.
- **Manual allocation** to specific invoices is available; over-allocation and
  negative outstanding balances are rejected.
- Payments are idempotent (a retried submission with the same reference does not
  double-count), and a payment can be **reversed** with a reason, which restores
  the invoices' outstanding amounts.
- Supplier balances show: gross posted bills, amount paid, credit-note
  adjustments, net outstanding payable, unallocated advances. The Payables screen
  also offers a **reconciliation** view that cross-checks payments, allocations
  and invoice balances and reports any disagreement.

---

## 5. Inventory rules worth knowing

- The **master catalogue** (≈254k medicines) is *not* your stock. A medicine
  creates stock only through purchases, opening stock or adjustments.
- **Low-stock alerts** appear only for medicines that are active, not
  discontinued, have a reorder level **greater than zero**, and whose *usable*
  stock (active, **non-expired** batches) is at or below that level. The same
  rule drives the dashboard, Inventory and Medicines screens.
- POS billing allocates batches **FEFO** (first-expiry-first-out), never sells
  expired batches or more than the batch holds, and respects the
  `pos.min_days_to_expiry` setting.
- Every stock change writes an immutable stock-movement record (purchase, sale,
  return, adjustment, cancellation) with the running batch balance.

---

## 6. Backups

**Admin → Backups** (Owner/Admin only): create a backup, list past backups and
**Verify** them. Each backup carries a SHA-256 checksum and a per-table row-count
manifest; verification re-checks the file and can restore it into a scratch
database (name must end in `_restore_verify`) and compare counts — it can never
overwrite the live database. The worker also takes scheduled backups.
Restore procedures for a real recovery are in `DEPLOYMENT.md`.

---

## 7. Session & error behaviour (staff should know)

- Sessions use HTTP-only cookies with CSRF protection; passwords are Argon2id-hashed.
- If a session expires, the app returns to the login screen (401).
- If something else goes wrong (no permission — 403, or a server error — 500),
  the app now shows the error **on the page** and keeps the user logged in.
