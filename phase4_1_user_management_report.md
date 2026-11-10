# Pharmacy ERP — Phase 4.1: User Management & Access Control
## Completion Report

**Date:** 22 October 2026
**Migration:** none needed — the existing `users`, `roles`, `user_sessions` and `audit_logs` tables already had every field required. The database stays at `006 (head)`.
**Tests:** 155 passed (SQLite) · 7 passed on MySQL 8.4 · `tsc` 0 errors · `next build` 22 routes · `/users` verified in the browser

---

## 1. What was built

A new **`/users`** page ("Users" in the main navigation, visible only with `users.manage`).

**Users list**
- Search by name, username or role, with a "show deactivated" option.
- Each row shows role, an administrator shield, active/deactivated status, signed-in session count, last login, and created date with who created it.

**Actions**
- **New user:** name, username, role, password + confirm. A live checklist shows the password rules, and the button stays disabled until the password meets them.
- **Edit:** name, username, role, active status.
- **Reset password:** new password + confirm.
- **Deactivate / Activate:** a confirm prompt before deactivating. There is no button on your own row.
- **History:** the account's administrative actions — created, role changed, deactivated, re-activated, password reset — with who did each and when.
- **"What each role can do":** shows, for each role, which sensitive permissions it actually has (void, refund, discount, credit override, prescription sales, adjustments, day-end approval, user management…). Admins can see what a role really allows before assigning it.

**Rules**
- **Administrator** means any role holding `users.manage`, decided by the role's permissions and never by its name.
- **Duplicate usernames** are refused (409), case-insensitively. Usernames are saved in lowercase, and login now matches them case-insensitively too (previously "Admin" failed where "admin" worked).
- **Last administrator:** you can't deactivate your own account, and you can't deactivate or demote the last active administrator (409, "Make another user an administrator first").
  - The check runs while every administrator's row is locked, so two admins demoting each other at the same moment can't both succeed. Verified on MySQL.
- **Deactivation** ends all of the user's sessions immediately and blocks login. All historical records — sales, payments, audit entries — are kept. Re-activation is possible.
- **Password reset** ends all of the user's sessions. If you reset your own, your current session is kept and your other devices are signed out.
- **Role changes** take effect on the user's very next request, because permissions are checked on every request.
- **Password policy** (server and screen use the same rule): 8–128 characters with an uppercase letter, a lowercase letter and a digit.
  - Passwords are hashed with the existing Argon2id hasher.
  - No default passwords; passwords and hashes are never returned, logged or written to the audit trail.

## 2. Files changed

**Backend — new**
- `app/services/users.py` — list/create/update/reset/audit, last-admin guard, session ending
- `app/api/v1/routes/users.py` — user endpoints (moved out of `auth.py`, same paths)
- `tests/test_user_management.py` — 8 tests
- `tests/mysql/test_mysql_users.py` — concurrent admin-demotion race

**Backend — modified**
- `app/schemas/auth.py` — shared `check_password_policy` / `check_username`, `UserUpdateRequest`, `PasswordResetRequest`, `ManagedUserResponse`; login username normalised
- `app/api/v1/routes/auth.py` — old users list/create handlers removed (now in `users.py`)
- `app/api/v1/router.py` — users router registered
- `tests/conftest.py` — `second_client` fixture (a second signed-in browser)

**Frontend — new:** `lib/users-api.ts` · `app/users/page.tsx`
**Frontend — modified:** `components/Navbar.tsx` (Users entry for `users.manage`)

## 3. Endpoints

All require `users.manage`.

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/v1/users?q=&include_inactive=` | List / search users (same path and fields as before, plus `is_admin`, `active_sessions`, `created_by_name`, `updated_at`) |
| POST | `/api/v1/users` | Create a user (409 on duplicate username, 422 on weak password) |
| GET | `/api/v1/users/{id}` | One user |
| PUT | `/api/v1/users/{id}` | Edit name / username / role / active (409 for last-admin or self-deactivation) |
| POST | `/api/v1/users/{id}/reset-password` | New password; ends that user's sessions |
| GET | `/api/v1/users/{id}/audit` | The account's administrative history |

## 4. Permissions

I inspected the actual grants. **No permission codes or role grants were changed** — the existing ones already match the requested split:

| Role | Has | Does not have |
|---|---|---|
| **Owner / Admin** | Every permission, including `users.manage`, `roles.manage` and `settings.manage` | — |
| **Pharmacist** | Medicines, prescription sales/viewing, discounts, credit sales, payments, customers, messaging, shifts, reports | Voids, refunds, credit-limit overrides, adjustments, day-end approval, user management |
| **Cashier** | POS, sales, customers, payment collection, invoice view/print, messaging, own shift | Discounts, credit sales, overrides, prescription sales, voids, refunds, adjustments, reports, day-end approval |
| **Inventory Manager** | Unchanged (purchasing, stock, medicines, suppliers, reports) | — |

Every check is enforced on the backend; the UI only hides what the user can't do. The browser test confirmed that a cashier gets 403 on `/users` and on reports while POS works.

## 5. Tests and results

| Command | Result |
|---|---|
| `pytest -q` (full regression, SQLite) | **155 passed**, 7 skipped (the MySQL tests, without `TEST_MYSQL_URL`) |
| `TEST_MYSQL_URL=…/pharmacy_erp_test pytest tests/mysql` | **7 passed** (6 Phase 4 concurrency tests + new admin race); the race test also passed 3 extra times |
| `npx tsc --noEmit` | 0 errors |
| `npm run build` | compiled, 22/22 routes (`/users` 5.36 kB) |

New tests cover:
- **Users:** creation and listing (no password or hash in responses), search, duplicate usernames (including case-insensitive), weak-password rejection.
- **Passwords:** Argon2id hash stored, login with the new password, wrong password rejected.
- **Access:** unauthenticated (401) and cashier/pharmacist (403) access to every user endpoint; the cashier's permissions exclude all the sensitive actions.
- **Sessions:** deactivation ends live sessions and blocks login; re-activation works; a reset ends sessions and the old password; resetting your own keeps your session.
- **Last admin:** self-deactivation and last-admin demotion blocked; demotion allowed once a second admin exists, and takes effect immediately.
- **Audit:** create, role change (from → to), password reset and deactivation are logged, with no secrets in the log.

## 6. Browser verification (live app)

Done on the live app as admin, with a test account `test.cashier` ("TEST Cashier (Phase 4.1)").

| Step | Result |
|---|---|
| Open `/users` | Admin row: shield, session count, last login, created date; no Deactivate on own row |
| Weak password | Create button disabled; checklist shows what is missing |
| Username "ADMIN" | "Username 'admin' is already taken" |
| Create `test.cashier` | Created; "Never" logged in; "by Dr. Rajesh Sharma" |
| Sign in as the cashier (separate session) | Login 200; `/users` 403; `/reports` 403; POS search 200; wrong password 401 |
| Change role Cashier → Pharmacist | Live session gained `sales.prescription` immediately |
| Reset password | "1 other session(s) signed out"; old session 401; old password 401; new password 200 |
| Deactivate | Shown as DEACTIVATED; live session 401; login 401 |
| Demote own (only) admin account | Refused: "This is the last active administrator…" |
| History | Deactivated · Password reset · Role changed (Cashier → Pharmacist) · Created, each with who and when |
| Layout | No horizontal overflow at 1440 px (9 menu items) or 375 px (phone) |

`test.cashier` is left **deactivated**. Accounts aren't deleted by design, so its audit history stays.

## 7. Existing data is intact

I took checksums of the live database before deploying and compared them afterwards:

| Data | Before | After |
|---|---|---|
| Sales invoices (count / total / balance / paid) | 4 / 1,390.00 / 100.00 / 1,190.00 | identical |
| Invoice lines, payments, returns | 5 / 6 / 1 | identical |
| Customer ledger (debits / credits / balance) | 1,200.00 / 1,100.00 / 100.00 | identical |
| Stock batches and movements (Σ stock 540) | 4 / 10 | identical |
| Audit log | 31 rows | 38 rows — 7 new rows only (4 user-admin actions + 3 logins); none changed or removed |
| Users | 1 | 2 (the deactivated test account) |

No password text or hash appears anywhere in the live audit log (checked by SQL: 0 rows).

## 8. Limitations

**Roles and passwords**
- **Roles are fixed.** There is no screen to create roles or edit their permissions; `roles.manage` exists but isn't used yet. The four system roles cover the requested split.
- **No "change password at next login".** The administrator knows the temporary password they set. Forcing a change would need a new column (a migration) — worth adding.
- **No self-service password change** for non-admin staff; an administrator resets it for them.
- **Password policy** has no breached-password list, no expiry, and no history check.

**Accounts and sessions**
- **No account deletion**, by design. Deactivated accounts stay so that historical bills keep their cashier.
- **Signed-in session counts** come from server-side sessions; an expired session that was never used again isn't counted.

**Testing**
- **The deactivate confirmation** (browser `confirm()`) wasn't clicked by automation; deactivation was verified through the Edit form's Active checkbox, which uses the same endpoint.

Phase 5 has **not** been started.
