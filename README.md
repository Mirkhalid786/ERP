# Pharmacy ERP — Version 1.0 (Phases 1–4)

**Product:** Pharmacy ERP  
**Target Market:** Indian Retail Pharmacies  
**Deployment:** Local Installation with LAN Access (Zero Cloud Dependency)  
**Stack:** Next.js 15 (TypeScript) + FastAPI (Python 3.12) + MySQL 8.4 LTS + Alembic  

---

## 1. Architecture Overview

```
LAN Cashier / Admin Workstations
       │
       ├── Browser (PC 1: Cashier) ──────┐
       ├── Browser (PC 2: Admin/Owner) ──┤
       └── Browser (PC 3: Pharmacist) ───┘
                                         │ LAN HTTP (Port 3000)
                              ┌──────────▼──────────┐
                              │   Next.js 15 UI     │
                              │   Reverse Proxy     │
                              └──────────┬──────────┘
                                         │ Internal HTTP (Port 8000)
                              ┌──────────▼──────────┐
                              │  FastAPI (Python)   │
                              │  Argon2id / RBAC    │
                              └──────────┬──────────┘
                                         │ SQLAlchemy 2.x Async
                              ┌──────────▼──────────┐
                              │    MySQL 8.4 LTS    │
                              │    InnoDB / ACID    │
                              └─────────────────────┘
```

- **Browser never connects to MySQL directly.**
- **FastAPI owns all business rules, authentication, and database sessions.**
- **Sessions use HTTP-only, secure cookies with server-side validation.**
- **Passwords are hashed using Argon2id (`argon2-cffi`).**
- **Alembic manages deterministic, version-controlled migrations.**

---

## 2. Directory Structure

```
d:/Accounting/
├── docker-compose.yml              # Multi-container orchestration (MySQL, Backend, Frontend)
├── .env.example                    # Documented environment template
├── .gitignore                      # Git exclusion rules
├── README.md                       # System documentation
│
├── backend/
│   ├── Dockerfile                  # Python 3.12 slim backend container
│   ├── requirements.txt            # Pinned Python dependencies
│   ├── pytest.ini                  # Pytest configuration
│   ├── alembic.ini                 # Database migration configuration
│   ├── alembic/
│   │   ├── env.py                  # Alembic runtime environment (loads sync DB URL)
│   │   ├── script.py.mako          # Migration template
│   │   └── versions/
│   │       └── 001_20261019_initial_phase1_schema.py # Phase 1 DDL Migration
│   ├── app/
│   │   ├── main.py                 # FastAPI application factory & lifespan DB check
│   │   ├── core/
│   │   │   ├── config.py           # Pydantic BaseSettings
│   │   │   ├── database.py         # SQLAlchemy async engine & sessionmaker
│   │   │   ├── security.py         # Argon2id hashing & CSRF tokens
│   │   │   └── deps.py             # Auth & RBAC permission dependencies
│   │   ├── models/
│   │   │   └── auth.py             # SQLAlchemy 2.x ORM models
│   │   ├── schemas/
│   │   │   └── auth.py             # Pydantic v2 schemas
│   │   ├── services/
│   │   │   └── auth.py             # Setup & auth business logic
│   │   └── api/v1/
│   │       ├── router.py           # V1 aggregated router
│   │       └── routes/
│   │           ├── health.py       # Liveness and readiness endpoints
│   │           └── auth.py         # Auth, setup, user, role & settings routes
│   └── tests/
│       ├── conftest.py             # Async SQLite test engine & client fixtures
│       ├── test_health.py          # Liveness & readiness tests
│       ├── test_security.py        # Argon2id & CSRF unit tests
│       ├── test_setup.py           # First-run setup & validation tests
│       ├── test_auth.py            # Login, logout, session & /me tests
│       └── test_users_settings.py  # User management, roles & settings tests
│
└── frontend/
    ├── Dockerfile                  # Node 22 Alpine frontend container
    ├── package.json                # Next.js 15, React 19, Tailwind CSS 4
    ├── tsconfig.json               # TypeScript strict configuration
    ├── next.config.ts              # Proxy rewrites (/api/* -> FastAPI)
    ├── lib/
    │   └── api.ts                  # Typed client with credentials include
    └── app/
        ├── layout.tsx              # Root HTML & body structure
        ├── globals.css             # Tailwind CSS imports
        ├── page.tsx                # Auto-routing based on setup status
        ├── setup/page.tsx          # Initial pharmacy setup wizard
        ├── login/page.tsx          # Staff login view
        └── dashboard/page.tsx      # Admin dashboard with RBAC & settings
```

---

## 3. Getting Started

### Step 1: Clone and Configure Environment

```bash
cp .env.example .env
```
Generate a secret key and set it in `.env`:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### Step 2: Run with Docker Compose (Recommended)

```bash
docker compose up --build -d
```
This boots:
1. `pharmacy_db`: MySQL 8.4 on port 3306 with health check.
2. `pharmacy_backend`: Runs Alembic migrations (`alembic upgrade head`) and starts FastAPI on port 8000.
3. `pharmacy_frontend`: Starts Next.js on port 3000.

Access the application at `http://localhost:3000`.

---

## 4. Running Locally Without Docker

### Prerequisites
- Python 3.12+
- Node.js 20+
- MySQL 8.4 LTS running locally

### Backend Setup:
```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Run migrations
alembic upgrade head

# Run tests
pytest

# Start development server
uvicorn app.main:app --reload --port 8000
```

### Frontend Setup:
```bash
cd frontend
npm install
npm run dev
```

---

## 5. First-Run Setup Process

1. Open `http://localhost:3000` in your browser.
2. The system checks `GET /api/v1/setup/status` and detects that no pharmacy exists.
3. You are redirected to `/setup` to enter:
   - Pharmacy Trade Name and Legal Entity Name
   - GSTIN (15-character Indian GST format) and Drug License Number
   - Master Administrator credentials (username and password)
4. The system automatically creates:
   - The pharmacy profile
   - All 20 application permission codes across sales, purchases, stock, reports, and admin
   - The 4 default system roles: **Owner / Admin**, **Pharmacist**, **Cashier**, and **Inventory Manager**
   - Default settings for GST, near-expiry alerts (30/60/90 days), and invoice numbering
   - An immutable audit trail entry (`pharmacy.setup`)

---

## 6. Testing

The backend includes a comprehensive automated test suite run via Pytest against an isolated in-memory database:
```bash
cd backend
pytest -v
```

Tests cover:
- **Health:** `/api/v1/health/live` and `/api/v1/health/ready`
- **Security:** Argon2id hash uniqueness, password verification, CSRF token generation/tamper detection, constant-time comparisons.
- **Setup:** First-run creation, duplicate prevention (409 Conflict), GSTIN validation, password strength enforcement.
- **Authentication:** Login success/failure, user enumeration defense, session expiration, HTTP-only cookie validation.
- **Permissions & Roles:** Role-based access control, cashier permission limits, unauthorized user creation rejection.
- **Settings:** Listing and updating pharmacy operational parameters.

---

## 7. Phase 4 — POS, Sales, Credit & WhatsApp

| Page | Purpose |
|---|---|
| `/pos` | Counter billing: barcode/search, FEFO batches, GST, split payments, udhaar, prescriptions (F2/F4/F8/F9) |
| `/sales`, `/sales/[id]` | Invoice history, details, reprint (thermal/A4/PDF), WhatsApp, collect payment, void |
| `/sales/returns` | Returns against an invoice; refund or customer credit; restock or write-off |
| `/customers`, `/customers/[id]` | Customer accounts, ledger/statement, collections, credit, adjustments |
| `/receivables` | Open credit bills, ageing, payment reminders |
| `/day-end` | Cash shift: opening cash, paid-outs, count & close, manager review |
| `/reports/sales`, `/reports/receivables` | Sales, GST, payment-method, credit and ageing reports (CSV export) |
| `/settings/communications` | WhatsApp templates and reminder rules |

A background **worker** container (`python -m app.worker`) sends queued WhatsApp messages
and creates reminders. WhatsApp works without credentials (click-to-chat links); the
official Cloud API is optional.

* Operations, WhatsApp/webhook setup, scheduler, backups, troubleshooting: [docs/PHASE4_OPERATIONS.md](docs/PHASE4_OPERATIONS.md)
* What was built and verified: [phase4_completion_report.md](phase4_completion_report.md)

```bash
cd backend && .venv/Scripts/python -m pytest -q            # SQLite suite
# MySQL concurrency suite: see docs/PHASE4_OPERATIONS.md §8
```
