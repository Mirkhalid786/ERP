"""Phase 5A accounting API: setup, chart of accounts, periods, journals, opening balances, trial balance."""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import today_local
from app.core.database import get_db
from app.core.deps import AuthContext, require_context
from app.core.json import decimal_json
from app.models.accounting import (
    AccountingPeriod, FiscalYear, JournalEntry, JournalLine, LedgerAccount, OpeningBalanceBatch,
)
from app.models.auth import User
from app.models.purchasing import Supplier
from app.models.sales import Customer
from app.schemas.accounting import (
    AccountCreate, AccountResponse, AccountUpdate, FiscalYearCreate, JournalDraftIn, JournalLineOut, JournalOut,
    OpeningBatchIn, OpeningPostRequest, PeriodReopen, ReverseRequest, SetupRequest,
)
from app.services import ledger, opening_balances
from app.services.ledger import LineIn, ZERO, money
from app.services.sales_reports import to_csv

router = APIRouter(prefix="/accounting", tags=["Accounting"])


def _lines(items) -> list:
    return [LineIn(l.account_id, l.debit, l.credit, l.party_type, l.party_id, l.description) for l in items]


async def _require_setup(db: AsyncSession, pharmacy_id: int) -> date:
    start = await ledger.books_start_date(db, pharmacy_id)
    if start is None:
        raise HTTPException(status_code=409, detail="Accounting has not been set up yet")
    return start


# ── Setup ──────────────────────────────────────────────────────────────────────

@router.get("/status")
async def setup_status(ctx: AuthContext = Depends(require_context("accounting.view")), db: AsyncSession = Depends(get_db)):
    start = await ledger.books_start_date(db, ctx.pharmacy_id)
    keys = await ledger.accounts_by_key(db, ctx.pharmacy_id)
    opening = (await db.execute(select(OpeningBalanceBatch).where(
        OpeningBalanceBatch.pharmacy_id == ctx.pharmacy_id).order_by(OpeningBalanceBatch.id.desc()))).scalars().first()
    return {
        "is_set_up": start is not None, "books_start_date": start,
        "missing_system_accounts": sorted(ledger.REQUIRED_SYSTEM_KEYS - set(keys)) if start else [],
        "opening_batch": {"id": opening.id, "status": opening.status} if opening else None,
    }


@router.post("/setup", status_code=status.HTTP_201_CREATED)
async def setup(body: SetupRequest, ctx: AuthContext = Depends(require_context("accounting.accounts_manage")),
                db: AsyncSession = Depends(get_db)):
    return await ledger.setup_accounting(db, ctx.user, body.books_start_date)


# ── Chart of accounts ──────────────────────────────────────────────────────────

async def _account_out(db: AsyncSession, pharmacy_id: int, accounts) -> list:
    sums = {r[0]: money(r[1] or 0) for r in (await db.execute(
        select(JournalLine.account_id, func.sum(JournalLine.debit - JournalLine.credit))
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(JournalEntry.pharmacy_id == pharmacy_id, JournalEntry.status.in_(ledger.POSTED_STATES))
        .group_by(JournalLine.account_id))).all()}
    used = {r[0] for r in (await db.execute(select(JournalLine.account_id).distinct())).all()}
    return [AccountResponse.model_validate(a).model_copy(update={"has_postings": a.id in used, "balance": sums.get(a.id, ZERO)})
            for a in accounts]


@router.get("/accounts", response_model=list[AccountResponse])
async def list_accounts(include_inactive: bool = True, ctx: AuthContext = Depends(require_context("accounting.view")),
                        db: AsyncSession = Depends(get_db)):
    stmt = select(LedgerAccount).where(LedgerAccount.pharmacy_id == ctx.pharmacy_id).order_by(LedgerAccount.code)
    if not include_inactive:
        stmt = stmt.where(LedgerAccount.is_active.is_(True))
    return await _account_out(db, ctx.pharmacy_id, (await db.execute(stmt)).scalars().all())


@router.post("/accounts", response_model=AccountResponse, status_code=status.HTTP_201_CREATED)
async def create_account(body: AccountCreate, ctx: AuthContext = Depends(require_context("accounting.accounts_manage")),
                         db: AsyncSession = Depends(get_db)):
    await _require_setup(db, ctx.pharmacy_id)
    account = await ledger.create_account(db, ctx.user, body.model_dump())
    return (await _account_out(db, ctx.pharmacy_id, [account]))[0]


@router.put("/accounts/{account_id}", response_model=AccountResponse)
async def update_account(account_id: int, body: AccountUpdate,
                         ctx: AuthContext = Depends(require_context("accounting.accounts_manage")),
                         db: AsyncSession = Depends(get_db)):
    account = await ledger.update_account(db, ctx.user, account_id, body.model_dump(exclude_unset=True))
    return (await _account_out(db, ctx.pharmacy_id, [account]))[0]


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(account_id: int, ctx: AuthContext = Depends(require_context("accounting.accounts_manage")),
                         db: AsyncSession = Depends(get_db)):
    await ledger.delete_account(db, ctx.user, account_id)


# ── Fiscal years and periods ───────────────────────────────────────────────────

@router.get("/periods")
async def list_periods(ctx: AuthContext = Depends(require_context("accounting.view")), db: AsyncSession = Depends(get_db)):
    years = (await db.execute(select(FiscalYear).where(FiscalYear.pharmacy_id == ctx.pharmacy_id)
                              .order_by(FiscalYear.start_date))).scalars().all()
    periods = (await db.execute(select(AccountingPeriod).where(AccountingPeriod.pharmacy_id == ctx.pharmacy_id)
                                .order_by(AccountingPeriod.start_date))).scalars().all()
    counts = {r[0]: r[1] for r in (await db.execute(
        select(JournalEntry.period_id, func.count(JournalEntry.id)).where(
            JournalEntry.pharmacy_id == ctx.pharmacy_id, JournalEntry.status.in_(ledger.POSTED_STATES))
        .group_by(JournalEntry.period_id))).all()}
    names = {u.id: u.name for u in (await db.execute(select(User).where(User.pharmacy_id == ctx.pharmacy_id))).scalars().all()}
    return decimal_json([{
        "id": y.id, "name": y.name, "start_date": y.start_date, "end_date": y.end_date, "status": y.status,
        "periods": [{
            "id": p.id, "name": p.name, "start_date": p.start_date, "end_date": p.end_date, "status": p.status,
            "entries": counts.get(p.id, 0), "closed_by": names.get(p.closed_by), "closed_at": p.closed_at,
            "reopened_by": names.get(p.reopened_by), "reopened_at": p.reopened_at, "reopen_reason": p.reopen_reason,
        } for p in periods if p.fiscal_year_id == y.id],
    } for y in years])


@router.post("/fiscal-years", status_code=status.HTTP_201_CREATED)
async def create_fiscal_year(body: FiscalYearCreate, ctx: AuthContext = Depends(require_context("accounting.accounts_manage")),
                             db: AsyncSession = Depends(get_db)):
    await _require_setup(db, ctx.pharmacy_id)
    if (body.end_date - body.start_date).days > 550 or body.end_date <= body.start_date:
        raise HTTPException(status_code=422, detail="A fiscal year must end after it starts and last at most 18 months")
    fy = await ledger.create_fiscal_year(db, ctx.user, body.start_date, body.end_date)
    return {"id": fy.id, "name": fy.name}


@router.post("/periods/{period_id}/close")
async def close_period(period_id: int, ctx: AuthContext = Depends(require_context("accounting.period_close")),
                       db: AsyncSession = Depends(get_db)):
    p = await ledger.close_period(db, ctx.user, period_id)
    return {"id": p.id, "name": p.name, "status": p.status}


@router.post("/periods/{period_id}/reopen")
async def reopen_period(period_id: int, body: PeriodReopen,
                        ctx: AuthContext = Depends(require_context("accounting.period_reopen")),
                        db: AsyncSession = Depends(get_db)):
    p = await ledger.reopen_period(db, ctx.user, period_id, body.reason)
    return {"id": p.id, "name": p.name, "status": p.status}


# ── Journals ───────────────────────────────────────────────────────────────────

async def _journal_out(db: AsyncSession, entry: JournalEntry, replayed: bool = False) -> JournalOut:
    await db.refresh(entry, attribute_names=["lines"])
    accounts = {a.id: a for a in (await db.execute(select(LedgerAccount).where(
        LedgerAccount.id.in_({l.account_id for l in entry.lines} or {0})))).scalars().all()}
    cust = {c.id: c.name for c in (await db.execute(select(Customer).where(Customer.id.in_(
        {l.party_id for l in entry.lines if l.party_type == "CUSTOMER"} or {0})))).scalars().all()}
    supp = {s.id: s.name for s in (await db.execute(select(Supplier).where(Supplier.id.in_(
        {l.party_id for l in entry.lines if l.party_type == "SUPPLIER"} or {0})))).scalars().all()}
    users = {u.id: u.name for u in (await db.execute(select(User).where(
        User.id.in_({entry.created_by, entry.posted_by or 0})))).scalars().all()}
    linked = {e.id: e.entry_number for e in (await db.execute(select(JournalEntry).where(
        JournalEntry.id.in_({entry.reversal_of_id or 0, entry.reversed_by_entry_id or 0})))).scalars().all()}
    period = (await db.execute(select(AccountingPeriod.name).where(AccountingPeriod.id == entry.period_id))).scalar_one_or_none() \
        if entry.period_id else None
    return JournalOut(
        id=entry.id, entry_number=entry.entry_number, status=entry.status, entry_date=entry.entry_date,
        document_date=entry.document_date, period_name=period, source_type=entry.source_type, source_id=entry.source_id,
        reference=entry.reference, narration=entry.narration, total_debit=entry.total_debit, total_credit=entry.total_credit,
        created_by_name=users.get(entry.created_by), created_at=entry.created_at,
        posted_by_name=users.get(entry.posted_by), posted_at=entry.posted_at,
        reversal_of_id=entry.reversal_of_id, reversal_of_number=linked.get(entry.reversal_of_id),
        reversed_by_entry_id=entry.reversed_by_entry_id, reversed_by_number=linked.get(entry.reversed_by_entry_id),
        reversed_at=entry.reversed_at, reversal_reason=entry.reversal_reason, replayed=replayed,
        lines=[JournalLineOut(
            id=l.id, line_no=l.line_no, account_id=l.account_id,
            account_code=accounts[l.account_id].code if l.account_id in accounts else None,
            account_name=accounts[l.account_id].name if l.account_id in accounts else None,
            debit=l.debit, credit=l.credit, party_type=l.party_type, party_id=l.party_id,
            party_name=(cust if l.party_type == "CUSTOMER" else supp).get(l.party_id) if l.party_type else None,
            description=l.description,
        ) for l in entry.lines],
    )


def _journal_conds(pharmacy_id, status_filter, source_type, date_from, date_to, q):
    conds = [JournalEntry.pharmacy_id == pharmacy_id]
    if status_filter:
        conds.append(JournalEntry.status == status_filter.upper())
    if source_type:
        conds.append(JournalEntry.source_type == source_type.upper())
    if date_from:
        conds.append(JournalEntry.entry_date >= date_from)
    if date_to:
        conds.append(JournalEntry.entry_date <= date_to)
    if q:
        like = f"%{q.strip()}%"
        conds.append(or_(JournalEntry.entry_number.ilike(like), JournalEntry.narration.ilike(like),
                         JournalEntry.reference.ilike(like)))
    return conds


@router.get("/journals")
async def list_journals(
    status_filter: Optional[str] = Query(None, alias="status"), source_type: Optional[str] = None,
    date_from: Optional[date] = None, date_to: Optional[date] = None, q: Optional[str] = Query(None, max_length=100),
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    ctx: AuthContext = Depends(require_context("accounting.view")), db: AsyncSession = Depends(get_db),
):
    conds = _journal_conds(ctx.pharmacy_id, status_filter, source_type, date_from, date_to, q)
    total = (await db.execute(select(func.count(JournalEntry.id)).where(*conds))).scalar_one()
    rows = (await db.execute(select(JournalEntry).where(*conds)
                             .order_by(JournalEntry.entry_date.desc(), JournalEntry.id.desc())
                             .offset(offset).limit(limit))).scalars().all()
    return decimal_json({"total": total, "items": [{
        "id": e.id, "entry_number": e.entry_number, "status": e.status, "entry_date": e.entry_date,
        "source_type": e.source_type, "reference": e.reference, "narration": e.narration,
        "total_debit": e.total_debit, "reversal_of_id": e.reversal_of_id, "reversed_by_entry_id": e.reversed_by_entry_id,
    } for e in rows]})


@router.get("/journals/{entry_id}", response_model=JournalOut)
async def get_journal(entry_id: int, ctx: AuthContext = Depends(require_context("accounting.view")),
                      db: AsyncSession = Depends(get_db)):
    return await _journal_out(db, await ledger.get_entry(db, ctx.pharmacy_id, entry_id))


@router.post("/journals", response_model=JournalOut, status_code=status.HTTP_201_CREATED)
async def create_draft(body: JournalDraftIn, ctx: AuthContext = Depends(require_context("accounting.journal_create")),
                       db: AsyncSession = Depends(get_db)):
    """Save a manual journal as DRAFT (no effect on balances until posted)."""
    await _require_setup(db, ctx.pharmacy_id)
    pid = ctx.pharmacy_id
    try:
        entry = await ledger.save_draft(db, ctx.user, None, entry_date=body.entry_date, narration=body.narration,
                                        reference=body.reference, lines=_lines(body.lines), idempotency_key=body.idempotency_key)
    except ledger.DuplicateSource:
        await db.rollback()
        entry = await ledger.entry_by_source(db, pid, f"MANUAL:{body.idempotency_key}")
    return await _journal_out(db, entry)


@router.put("/journals/{entry_id}", response_model=JournalOut)
async def update_draft(entry_id: int, body: JournalDraftIn,
                       ctx: AuthContext = Depends(require_context("accounting.journal_create")),
                       db: AsyncSession = Depends(get_db)):
    entry = await ledger.save_draft(db, ctx.user, entry_id, entry_date=body.entry_date, narration=body.narration,
                                    reference=body.reference, lines=_lines(body.lines), idempotency_key=None)
    return await _journal_out(db, entry)


@router.delete("/journals/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_draft(entry_id: int, ctx: AuthContext = Depends(require_context("accounting.journal_create")),
                       db: AsyncSession = Depends(get_db)):
    await ledger.delete_draft(db, ctx.user, entry_id)


@router.post("/journals/{entry_id}/post", response_model=JournalOut)
async def post_draft(entry_id: int, ctx: AuthContext = Depends(require_context("accounting.journal_post")),
                     db: AsyncSession = Depends(get_db)):
    return await _journal_out(db, await ledger.post_draft(db, ctx.user, entry_id))


@router.post("/journals/{entry_id}/reverse", response_model=JournalOut)
async def reverse(entry_id: int, body: ReverseRequest,
                  ctx: AuthContext = Depends(require_context("accounting.journal_reverse")),
                  db: AsyncSession = Depends(get_db)):
    original = await ledger.get_entry(db, ctx.pharmacy_id, entry_id)
    if original.source_type == "OPENING" and "accounting.opening_balances" not in ctx.permissions:
        raise HTTPException(status_code=403, detail="Reversing the opening entry also needs 'accounting.opening_balances'")
    reversal = await ledger.reverse_entry(db, ctx.user, entry_id, body.reason, body.reversal_date)
    if original.source_type == "OPENING":
        batch = await opening_balances.get_batch(db, ctx.pharmacy_id, int(original.source_id), lock=True)
        batch.status = "REVERSED"     # Allows a corrected batch to be prepared
        await db.flush()
    return await _journal_out(db, reversal)


# ── Opening balances ───────────────────────────────────────────────────────────

async def _batch_out(db: AsyncSession, pharmacy_id: int, batch: OpeningBalanceBatch) -> dict:
    preview = await opening_balances.preview(db, pharmacy_id, batch)
    return {
        "id": batch.id, "status": batch.status, "opening_date": batch.opening_date,
        "source_description": batch.source_description, "notes": batch.notes,
        "post_difference_to_suspense": batch.post_difference_to_suspense, "journal_entry_id": batch.journal_entry_id,
        "posted_at": batch.posted_at,
        "lines": [{
            "id": l.id, "account_id": l.account_id, "debit": l.debit, "credit": l.credit, "party_type": l.party_type,
            "party_id": l.party_id, "document_number": l.document_number, "document_date": l.document_date,
            "due_date": l.due_date, "outstanding_amount": l.outstanding_amount, "notes": l.notes,
        } for l in batch.lines],
        "preview": preview,
    }


@router.get("/opening-balances")
async def get_opening(ctx: AuthContext = Depends(require_context("accounting.view")), db: AsyncSession = Depends(get_db)):
    batch = (await db.execute(select(OpeningBalanceBatch).where(OpeningBalanceBatch.pharmacy_id == ctx.pharmacy_id)
                              .order_by(OpeningBalanceBatch.id.desc()))).scalars().first()
    if batch is None:
        return decimal_json(None)
    batch = await opening_balances.get_batch(db, ctx.pharmacy_id, batch.id)
    return decimal_json(await _batch_out(db, ctx.pharmacy_id, batch))


@router.post("/opening-balances", status_code=status.HTTP_201_CREATED)
async def create_opening(body: OpeningBatchIn, ctx: AuthContext = Depends(require_context("accounting.opening_balances")),
                         db: AsyncSession = Depends(get_db)):
    batch = await opening_balances.save_batch(db, ctx.user, None, body.model_dump())
    return decimal_json(await _batch_out(db, ctx.pharmacy_id, batch), status_code=201)


@router.put("/opening-balances/{batch_id}")
async def update_opening(batch_id: int, body: OpeningBatchIn,
                         ctx: AuthContext = Depends(require_context("accounting.opening_balances")),
                         db: AsyncSession = Depends(get_db)):
    batch = await opening_balances.save_batch(db, ctx.user, batch_id, body.model_dump())
    return decimal_json(await _batch_out(db, ctx.pharmacy_id, batch))


@router.post("/opening-balances/{batch_id}/post")
async def post_opening(batch_id: int, body: OpeningPostRequest,
                       ctx: AuthContext = Depends(require_context("accounting.opening_balances")),
                       db: AsyncSession = Depends(get_db)):
    if "accounting.journal_post" not in ctx.permissions:
        raise HTTPException(status_code=403, detail="Posting the opening entry also needs 'accounting.journal_post'")
    batch, entry = await opening_balances.post_batch(db, ctx.user, batch_id, body.acknowledge_warnings)
    return decimal_json({"batch_id": batch.id, "status": batch.status, "journal_entry_id": entry.id,
                         "entry_number": entry.entry_number})


# ── Reports & integrity ────────────────────────────────────────────────────────

@router.get("/trial-balance")
async def trial_balance(date_from: Optional[date] = None, date_to: Optional[date] = None,
                        format: str = Query("json", pattern="^(json|csv)$"),
                        ctx: AuthContext = Depends(require_context("accounting.view")), db: AsyncSession = Depends(get_db)):
    data = await ledger.trial_balance(db, ctx.pharmacy_id, date_from, date_to or today_local())
    if format == "csv":
        if "accounting.export" not in ctx.permissions:
            raise HTTPException(status_code=403, detail="Permission 'accounting.export' is required")
        ledger._audit(db, ctx.user, "accounting.export", "report", "trial_balance", {"from": date_from, "to": data["date_to"]})
        return PlainTextResponse(to_csv(data["rows"]), media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="trial-balance-{data["date_to"]}.csv"'})
    return decimal_json(data)


@router.get("/ledger/{account_id}")
async def account_ledger(account_id: int, date_from: Optional[date] = None, date_to: Optional[date] = None,
                         party_type: Optional[str] = None, party_id: Optional[int] = None,
                         format: str = Query("json", pattern="^(json|csv)$"),
                         ctx: AuthContext = Depends(require_context("accounting.view")), db: AsyncSession = Depends(get_db)):
    data = await ledger.account_ledger(db, ctx.pharmacy_id, account_id, date_from, date_to or today_local(),
                                       party_type.upper() if party_type else None, party_id)
    if format == "csv":
        if "accounting.export" not in ctx.permissions:
            raise HTTPException(status_code=403, detail="Permission 'accounting.export' is required")
        ledger._audit(db, ctx.user, "accounting.export", "report", f"ledger:{account_id}", {"to": data["date_to"]})
        return PlainTextResponse(to_csv(data["entries"]), media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="ledger-{data["account"]["code"]}.csv"'})
    return decimal_json(data)


@router.get("/integrity")
async def integrity(ctx: AuthContext = Depends(require_context("accounting.view")), db: AsyncSession = Depends(get_db)):
    return decimal_json(await ledger.integrity_check(db, ctx.pharmacy_id))


@router.get("/historical-summary")
async def historical_summary(ctx: AuthContext = Depends(require_context("accounting.view")), db: AsyncSession = Depends(get_db)):
    """Read-only inventory of pre-accounting transactions, for cut-over planning."""
    from app.services.cutover import historical_summary as summarise
    return decimal_json(await summarise(db, ctx.pharmacy_id))


# ── Stage 5B: automatic postings ───────────────────────────────────────────────

@router.get("/postings/issues")
async def posting_issues(status_filter: Optional[str] = Query("OPEN", alias="status"),
                         ctx: AuthContext = Depends(require_context("accounting.view")), db: AsyncSession = Depends(get_db)):
    from app.models.accounting import GlPostingIssue
    from app.services.gl_posting import _issue_out
    stmt = select(GlPostingIssue).where(GlPostingIssue.pharmacy_id == ctx.pharmacy_id)
    if status_filter:
        stmt = stmt.where(GlPostingIssue.status == status_filter.upper())
    rows = (await db.execute(stmt.order_by(GlPostingIssue.id.desc()).limit(500))).scalars().all()
    return decimal_json([_issue_out(i) for i in rows])


@router.get("/postings/reconciliation")
async def posting_reconciliation(ctx: AuthContext = Depends(require_context("accounting.view")),
                                 db: AsyncSession = Depends(get_db)):
    """Documents without their journal, journals that no longer match, open issues, control accounts."""
    from app.services import gl_posting
    return decimal_json(await gl_posting.reconciliation(db, ctx.pharmacy_id))


@router.post("/postings/retry")
async def retry_postings(body: dict, ctx: AuthContext = Depends(require_context("accounting.postings_manage")),
                         db: AsyncSession = Depends(get_db)):
    """Post open issues and (by default) documents found without a journal. Safe to repeat."""
    from app.services import gl_posting
    source_key = (body or {}).get("source_key") or None
    include_missing = bool((body or {}).get("include_missing", True))
    return decimal_json(await gl_posting.retry(db, ctx.user, include_missing=include_missing, source_key=source_key))


# ── Stage 5C: financial statements ─────────────────────────────────────────────

from decimal import Decimal  # noqa: E402

from pydantic import BaseModel, Field as _Field  # noqa: E402


def _period(date_from: Optional[date], date_to: Optional[date]):
    to = date_to or today_local()
    return date_from or to.replace(day=1), to


@router.get("/reports/{report}")
async def financial_report(report: str, date_from: Optional[date] = None, date_to: Optional[date] = None,
                           account_id: Optional[int] = None, format: str = "json",
                           ctx: AuthContext = Depends(require_context("accounting.view")), db: AsyncSession = Depends(get_db)):
    from app.services import financial_reports as fr
    start, end = _period(date_from, date_to)
    await _require_setup(db, ctx.pharmacy_id)
    if report == "profit-loss":
        data = await fr.profit_and_loss(db, ctx.pharmacy_id, start, end)
        rows = [{"section": s, **r} for s, items in data["sections"].items() for r in items]
    elif report == "balance-sheet":
        data = await fr.balance_sheet(db, ctx.pharmacy_id, end)
        rows = [{"side": s, **r} for s, items in data["sides"].items() for r in items]
    elif report == "cash-flow":
        data = await fr.cash_flow(db, ctx.pharmacy_id, start, end)
        rows = [{"category": c, **r} for c, items in data["sections"].items() for r in items]
    elif report == "day-book":
        data = await fr.day_book(db, ctx.pharmacy_id, start, end)
        rows = [{"entry_number": e["entry_number"], "date": e["entry_date"], "narration": e["narration"], **l}
                for e in data["entries"] for l in e["lines"]]
    elif report == "gst":
        data = await fr.gst_from_ledger(db, ctx.pharmacy_id, start, end)
        flat = {**{f"output_{a}": b for a, b in data["output"].items()},
                **{f"input_{a}": b for a, b in data["input_eligible"].items()},
                "input_pending_review": data["input_pending_review"], "net_payable": data["net_payable"]}
        rows = [{"item": k, "amount": v} for k, v in flat.items()]
    elif report == "payables-ageing":
        data = await fr.payables_ageing(db, ctx.pharmacy_id, end)
        rows = [{k: v for k, v in r.items() if k != "bills"} for r in data["suppliers"]]
    elif report == "cash-book":
        keyed = await ledger.accounts_by_key(db, ctx.pharmacy_id)
        data = await ledger.account_ledger(db, ctx.pharmacy_id, account_id or keyed["CASH"].id, start, end)
        rows = data["entries"]
    else:
        raise HTTPException(status_code=404, detail="Unknown report")
    if format == "csv":
        if "accounting.export" not in ctx.permissions:
            raise HTTPException(status_code=403, detail="Permission 'accounting.export' is required to export")
        return PlainTextResponse(to_csv(rows), media_type="text/csv",
                                 headers={"Content-Disposition": f"attachment; filename={report}_{start}_{end}.csv"})
    return decimal_json(data)


# ── Stage 5D: settlements, bank reconciliation, year-end, CA pack ──────────────

class SettlementIn(BaseModel):
    idempotency_key: str = _Field(..., min_length=8, max_length=64)
    clearing_key: str
    bank_account_id: int
    settlement_date: date
    gross_amount: Decimal = _Field(..., gt=0)
    fee_amount: Decimal = _Field(Decimal("0"), ge=0)
    fee_gst_amount: Decimal = _Field(Decimal("0"), ge=0)
    reference: Optional[str] = _Field(None, max_length=100)
    notes: Optional[str] = _Field(None, max_length=1000)


class StatementRowIn(BaseModel):
    statement_date: date
    description: str = _Field(..., min_length=1, max_length=300)
    reference: Optional[str] = _Field(None, max_length=100)
    amount: Decimal


class StatementImportIn(BaseModel):
    account_id: int
    rows: list[StatementRowIn] = _Field(..., min_length=1, max_length=5000)


class MatchIn(BaseModel):
    journal_line_id: Optional[int] = None


def _settlement_out(s) -> dict:
    return {"id": s.id, "settlement_number": s.settlement_number, "clearing_key": s.clearing_key, "bank_account_id": s.bank_account_id,
            "settlement_date": s.settlement_date, "gross_amount": s.gross_amount, "fee_amount": s.fee_amount,
            "fee_gst_amount": s.fee_gst_amount, "net_amount": s.net_amount, "reference": s.reference, "status": s.status,
            "reversal_reason": s.reversal_reason, "created_at": s.created_at}


@router.get("/settlements")
async def settlements(ctx: AuthContext = Depends(require_context("accounting.view")), db: AsyncSession = Depends(get_db)):
    from app.services import accounting_ops
    return decimal_json({"items": [_settlement_out(s) for s in await accounting_ops.list_settlements(db, ctx.pharmacy_id)],
                         "clearing": await accounting_ops.clearing_balances(db, ctx.pharmacy_id)})


@router.post("/settlements", status_code=201)
async def create_settlement(body: SettlementIn, ctx: AuthContext = Depends(require_context("accounting.bank_reconcile")),
                            db: AsyncSession = Depends(get_db)):
    from app.services import accounting_ops
    st, replayed = await accounting_ops.record_settlement(db, ctx.user, body)
    return decimal_json({**_settlement_out(st), "replayed": replayed}, status_code=200 if replayed else 201)


@router.post("/settlements/{settlement_id}/reverse")
async def reverse_settlement(settlement_id: int, body: PeriodReopen,
                             ctx: AuthContext = Depends(require_context("accounting.bank_reconcile")),
                             db: AsyncSession = Depends(get_db)):
    from app.services import accounting_ops
    return decimal_json(_settlement_out(await accounting_ops.reverse_settlement(db, ctx.user, settlement_id, body.reason)))


@router.post("/bank/statements")
async def import_statement(body: StatementImportIn, ctx: AuthContext = Depends(require_context("accounting.bank_reconcile")),
                           db: AsyncSession = Depends(get_db)):
    from app.services import accounting_ops
    return decimal_json(await accounting_ops.import_statement(db, ctx.user, body.account_id, body.rows))


@router.post("/bank/statement-lines/{line_id}/match")
async def match_statement_line(line_id: int, body: MatchIn,
                               ctx: AuthContext = Depends(require_context("accounting.bank_reconcile")),
                               db: AsyncSession = Depends(get_db)):
    from app.services import accounting_ops
    st = await accounting_ops.match_line(db, ctx.user, line_id, body.journal_line_id)
    return {"id": st.id, "status": st.status, "matched_line_id": st.matched_line_id}


@router.post("/bank/{account_id}/auto-match")
async def auto_match(account_id: int, ctx: AuthContext = Depends(require_context("accounting.bank_reconcile")),
                     db: AsyncSession = Depends(get_db)):
    from app.services import accounting_ops
    await accounting_ops._bank_account(db, ctx.pharmacy_id, account_id)
    return {"matched": await accounting_ops.auto_match(db, ctx.user, account_id)}


@router.get("/bank/{account_id}/reconciliation")
async def bank_reconciliation(account_id: int, as_of: Optional[date] = None,
                              ctx: AuthContext = Depends(require_context("accounting.view")), db: AsyncSession = Depends(get_db)):
    from app.services import accounting_ops
    return decimal_json(await accounting_ops.reconciliation_report(db, ctx.pharmacy_id, account_id, as_of or today_local()))


@router.post("/fiscal-years/{fy_id}/close")
async def close_fiscal_year(fy_id: int, ctx: AuthContext = Depends(require_context("accounting.year_end")),
                            db: AsyncSession = Depends(get_db)):
    from app.services import accounting_ops
    fy = await accounting_ops.close_year(db, ctx.user, fy_id)
    return {"id": fy.id, "name": fy.name, "status": fy.status}


@router.post("/fiscal-years/{fy_id}/reopen")
async def reopen_fiscal_year(fy_id: int, body: PeriodReopen, ctx: AuthContext = Depends(require_context("accounting.year_end")),
                             db: AsyncSession = Depends(get_db)):
    from app.services import accounting_ops
    if "accounting.period_reopen" not in ctx.permissions:
        raise HTTPException(status_code=403, detail="Permission 'accounting.period_reopen' is also required")
    fy = await accounting_ops.reopen_year(db, ctx.user, fy_id, body.reason)
    return {"id": fy.id, "name": fy.name, "status": fy.status}


@router.get("/export/ca-pack")
async def ca_pack(date_from: Optional[date] = None, date_to: Optional[date] = None,
                  ctx: AuthContext = Depends(require_context("accounting.export")), db: AsyncSession = Depends(get_db)):
    from fastapi.responses import Response
    from app.services import accounting_ops
    start, end = _period(date_from, date_to)
    data = await accounting_ops.ca_pack(db, ctx.user, start, end)
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f"attachment; filename=ca_pack_{start}_{end}.zip"})


@router.get("/postings/status")
async def posting_status(keys: str = Query(..., description="Comma-separated source keys, e.g. SALE:12,SUPPLIER_BILL:3"),
                         ctx: AuthContext = Depends(require_context("accounting.view")), db: AsyncSession = Depends(get_db)):
    from app.services import gl_posting
    return decimal_json(await gl_posting.statuses(db, ctx.pharmacy_id, [k.strip() for k in keys.split(",") if k.strip()]))
