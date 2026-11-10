"""
Stage 5D: payment settlements, bank reconciliation, year-end close and the CA export pack.

* Settlement: UPI / card money in a clearing account arrives in the bank net of fees.
  Dr Bank (net), Dr Bank Charges (fee), Dr Input GST pending (GST on fee), Cr Clearing (gross).
  Posted through gl_posting (source SETTLEMENT); corrected by reversal (SETTLEMENT_REVERSAL).
* Bank reconciliation: statement lines are imported once (duplicate lines are refused by
  a content hash) and matched one-to-one with journal lines on the bank account,
  automatically (same amount, within a few days) or by hand. Nothing in the ledger changes.
* Year-end close: with every period but the last closed, a YEAR_END entry moves the
  year's income and expense balances to Retained Earnings, the last period is closed and
  the fiscal year is marked CLOSED; the next year is created if missing. Reopening
  reverses that entry. Profit & loss reports ignore YEAR_END entries.
* CA export pack: a ZIP of CSVs (trial balance, P&L, balance sheet, general ledger,
  day book, GST from the ledger, receivables and payables ageing, sales register).
"""
import csv
import hashlib
import io
import json
import zipfile
from datetime import date, timedelta
from decimal import Decimal
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.clock import today_local
from app.models.accounting import (
    AccountingPeriod, BankStatementLine, FiscalYear, JournalEntry, JournalLine, LedgerAccount, PaymentSettlement,
)
from app.models.auth import AuditLog, User
from app.services import financial_reports, gl_posting, ledger
from app.services.ledger import LineIn, POSTED_STATES, ZERO, money, utcnow
from app.services.numbering import next_document_number
from app.services.sales_common import financial_year_label, request_fingerprint

CLEARING_KEYS = ("UPI_CLEARING", "CARD_CLEARING", "OTHER_CLEARING")
MATCH_WINDOW_DAYS = 3


def _audit(db, user: User, action: str, entity_type: str, entity_id, details: dict, reason: str = None):
    db.add(AuditLog(pharmacy_id=user.pharmacy_id, user_id=user.id, action=action, entity_type=entity_type,
                    entity_id=str(entity_id), reason=reason, metadata_json=json.dumps(details, default=str)))


async def _require_books(db: AsyncSession, pharmacy_id: int) -> date:
    start = await ledger.books_start_date(db, pharmacy_id)
    if start is None:
        raise HTTPException(status_code=409, detail="Accounting has not been set up yet")
    return start


async def _bank_account(db: AsyncSession, pharmacy_id: int, account_id: int) -> LedgerAccount:
    acc = await ledger.get_account(db, pharmacy_id, account_id)
    group = (await db.execute(select(LedgerAccount).where(LedgerAccount.id == acc.parent_id))).scalar_one_or_none()
    if acc.is_group or not acc.is_active or not (acc.system_key == "BANK" or (group and group.code == "1120")):
        raise HTTPException(status_code=422, detail="Choose an active bank account (under 1120 Bank Accounts)")
    return acc


async def _account_balance(db: AsyncSession, pharmacy_id: int, account_id: int, as_of: date) -> Decimal:
    return money((await db.execute(select(func.coalesce(func.sum(JournalLine.debit - JournalLine.credit), 0))
                                   .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
                                   .where(JournalEntry.pharmacy_id == pharmacy_id, JournalEntry.status.in_(POSTED_STATES),
                                          JournalLine.account_id == account_id, JournalEntry.entry_date <= as_of))).scalar_one())


# ── Settlements ────────────────────────────────────────────────────────────────

async def record_settlement(db: AsyncSession, user: User, body) -> tuple:
    pid = user.pharmacy_id
    await _require_books(db, pid)
    fingerprint = request_fingerprint(body.model_dump(mode="json"))
    found = (await db.execute(select(PaymentSettlement).where(
        PaymentSettlement.pharmacy_id == pid, PaymentSettlement.idempotency_key == body.idempotency_key))).scalar_one_or_none()
    if found:
        return found, True
    if body.clearing_key not in CLEARING_KEYS:
        raise HTTPException(status_code=422, detail=f"Clearing account must be one of {', '.join(CLEARING_KEYS)}")
    if body.settlement_date > today_local():
        raise HTTPException(status_code=422, detail="Settlement date cannot be in the future")
    bank = await _bank_account(db, pid, body.bank_account_id)
    gross, fee, gst = money(body.gross_amount), money(body.fee_amount or 0), money(body.fee_gst_amount or 0)
    net = money(gross - fee - gst)
    if gross <= ZERO or fee < ZERO or gst < ZERO or net < ZERO:
        raise HTTPException(status_code=422, detail="Gross must be positive and at least the fees")
    clearing = (await ledger.accounts_by_key(db, pid)).get(body.clearing_key)
    if clearing is None:
        raise HTTPException(status_code=422, detail=f"Account {body.clearing_key} is missing from the chart")
    # Lock the clearing account so two settlements cannot both use the same balance
    await db.execute(select(LedgerAccount.id).where(LedgerAccount.id == clearing.id).with_for_update())
    available = await _account_balance(db, pid, clearing.id, body.settlement_date)
    if gross > available:
        raise HTTPException(status_code=422, detail=f"Only ₹{available} is waiting in {clearing.name} on {body.settlement_date}")
    fy = await financial_year_label(db, pid, body.settlement_date)
    number = await next_document_number(db, pid, "SETTLEMENT", f"STL/{fy}", PaymentSettlement.settlement_number,
                                        PaymentSettlement.pharmacy_id, separator="/", width=5)
    st = PaymentSettlement(pharmacy_id=pid, settlement_number=number, idempotency_key=body.idempotency_key,
                           clearing_key=body.clearing_key, bank_account_id=bank.id, settlement_date=body.settlement_date,
                           gross_amount=gross, fee_amount=fee, fee_gst_amount=gst, net_amount=net,
                           reference=(body.reference or "").strip() or None, notes=(body.notes or "").strip() or None,
                           created_by=user.id)
    db.add(st)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail="This settlement was already recorded")
    _audit(db, user, "settlement.create", "payment_settlement", st.id,
           {"number": number, "gross": gross, "fee": fee, "gst": gst, "fingerprint": fingerprint[:16]})
    outcome = await gl_posting.record(db, pid, user.id, "SETTLEMENT", st.id)
    if outcome == "failed":
        # A settlement only exists for the ledger: refuse rather than leave it unposted
        issue = await gl_posting.issue_for(db, pid, f"SETTLEMENT:{st.id}")
        raise HTTPException(status_code=409, detail=f"Settlement could not be posted: {issue.error if issue else 'see posting issues'}")
    return st, False


async def reverse_settlement(db: AsyncSession, user: User, settlement_id: int, reason: str) -> PaymentSettlement:
    st = (await db.execute(select(PaymentSettlement).where(
        PaymentSettlement.id == settlement_id, PaymentSettlement.pharmacy_id == user.pharmacy_id).with_for_update())).scalar_one_or_none()
    if st is None:
        raise HTTPException(status_code=404, detail="Settlement not found")
    if st.status != "POSTED":
        raise HTTPException(status_code=409, detail="Settlement is already reversed")
    matched = (await db.execute(select(func.count(BankStatementLine.id)).join(
        JournalLine, JournalLine.id == BankStatementLine.matched_line_id).join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(JournalEntry.source_key == f"SETTLEMENT:{st.id}"))).scalar_one()
    if matched:
        raise HTTPException(status_code=409, detail="Unmatch the bank statement line for this settlement first")
    st.status, st.reversed_by, st.reversed_at, st.reversal_reason = "REVERSED", user.id, utcnow(), reason.strip()
    _audit(db, user, "settlement.reverse", "payment_settlement", st.id, {"number": st.settlement_number}, reason=reason)
    await db.flush()
    if await gl_posting.record(db, user.pharmacy_id, user.id, "SETTLEMENT_REVERSAL", st.id) == "failed":
        issue = await gl_posting.issue_for(db, user.pharmacy_id, f"SETTLEMENT_REVERSAL:{st.id}")
        raise HTTPException(status_code=409, detail=f"Reversal could not be posted: {issue.error if issue else ''}")
    return st


async def list_settlements(db: AsyncSession, pharmacy_id: int, limit: int = 200) -> List[PaymentSettlement]:
    return list((await db.execute(select(PaymentSettlement).where(PaymentSettlement.pharmacy_id == pharmacy_id)
                                  .order_by(PaymentSettlement.settlement_date.desc(), PaymentSettlement.id.desc())
                                  .limit(limit))).scalars().all())


async def clearing_balances(db: AsyncSession, pharmacy_id: int) -> list:
    keyed = await ledger.accounts_by_key(db, pharmacy_id)
    return [{"clearing_key": k, "account": keyed[k].name, "balance": await _account_balance(db, pharmacy_id, keyed[k].id, today_local())}
            for k in CLEARING_KEYS if k in keyed]


# ── Bank reconciliation ────────────────────────────────────────────────────────

def _line_hash(row, ordinal: int) -> str:
    raw = f"{row.statement_date}|{money(row.amount)}|{(row.reference or '').strip().upper()}|{row.description.strip().upper()}|{ordinal}"
    return hashlib.sha256(raw.encode()).hexdigest()


async def import_statement(db: AsyncSession, user: User, account_id: int, rows: list) -> dict:
    pid = user.pharmacy_id
    await _require_books(db, pid)
    bank = await _bank_account(db, pid, account_id)
    batch = utcnow().strftime("%Y%m%d%H%M%S")
    added = skipped = 0
    seen: dict = {}
    for row in rows:
        if money(row.amount) == ZERO:
            raise HTTPException(status_code=422, detail="Statement amounts cannot be zero")
        sig = (row.statement_date, money(row.amount), (row.reference or "").strip().upper(), row.description.strip().upper())
        seen[sig] = seen.get(sig, 0) + 1          # Identical lines on the same statement are kept apart by their position
        h = _line_hash(row, seen[sig])
        exists = (await db.execute(select(BankStatementLine.id).where(
            BankStatementLine.pharmacy_id == pid, BankStatementLine.account_id == bank.id,
            BankStatementLine.line_hash == h))).scalar_one_or_none()
        if exists:
            skipped += 1
            continue
        db.add(BankStatementLine(pharmacy_id=pid, account_id=bank.id, import_batch=batch, line_hash=h,
                                 statement_date=row.statement_date, description=row.description.strip()[:300],
                                 reference=(row.reference or "").strip()[:100] or None, amount=money(row.amount),
                                 created_by=user.id))
        added += 1
    await db.flush()
    _audit(db, user, "bank.statement_import", "ledger_account", bank.id, {"batch": batch, "added": added, "skipped_duplicates": skipped})
    matched = await auto_match(db, user, bank.id)
    return {"batch": batch, "added": added, "skipped_duplicates": skipped, "auto_matched": matched}


async def _unmatched_book_lines(db: AsyncSession, pharmacy_id: int, account_id: int):
    taken = select(BankStatementLine.matched_line_id).where(BankStatementLine.matched_line_id.is_not(None))
    return (await db.execute(select(JournalLine, JournalEntry).join(JournalEntry, JournalEntry.id == JournalLine.entry_id).where(
        JournalEntry.pharmacy_id == pharmacy_id, JournalEntry.status.in_(POSTED_STATES), JournalLine.account_id == account_id,
        ~JournalLine.id.in_(taken)).order_by(JournalEntry.entry_date, JournalLine.id))).all()


async def auto_match(db: AsyncSession, user: User, account_id: int) -> int:
    lines = (await db.execute(select(BankStatementLine).where(
        BankStatementLine.pharmacy_id == user.pharmacy_id, BankStatementLine.account_id == account_id,
        BankStatementLine.status == "UNMATCHED").order_by(BankStatementLine.statement_date, BankStatementLine.id)
        .with_for_update())).scalars().all()
    book = await _unmatched_book_lines(db, user.pharmacy_id, account_id)
    used, count = set(), 0
    for st in lines:
        target = money(st.amount)
        best = None
        for jl, je in book:
            if jl.id in used:
                continue
            if money(jl.debit - jl.credit) != target:
                continue
            gap = abs((je.entry_date - st.statement_date).days)
            if gap <= MATCH_WINDOW_DAYS and (best is None or gap < best[0]):
                best = (gap, jl)
        if best:
            used.add(best[1].id)
            st.status, st.matched_line_id, st.matched_by, st.matched_at = "MATCHED", best[1].id, user.id, utcnow()
            count += 1
    await db.flush()
    return count


async def match_line(db: AsyncSession, user: User, statement_line_id: int, journal_line_id: Optional[int]) -> BankStatementLine:
    st = (await db.execute(select(BankStatementLine).where(
        BankStatementLine.id == statement_line_id, BankStatementLine.pharmacy_id == user.pharmacy_id).with_for_update())).scalar_one_or_none()
    if st is None:
        raise HTTPException(status_code=404, detail="Statement line not found")
    if journal_line_id is None:
        if st.status != "MATCHED":
            raise HTTPException(status_code=409, detail="Line is not matched")
        _audit(db, user, "bank.unmatch", "bank_statement_line", st.id, {"journal_line_id": st.matched_line_id})
        st.status, st.matched_line_id, st.matched_by, st.matched_at = "UNMATCHED", None, None, None
        await db.flush()
        return st
    if st.status == "MATCHED":
        raise HTTPException(status_code=409, detail="Unmatch the line first")
    row = (await db.execute(select(JournalLine, JournalEntry).join(JournalEntry, JournalEntry.id == JournalLine.entry_id).where(
        JournalLine.id == journal_line_id, JournalEntry.pharmacy_id == user.pharmacy_id,
        JournalEntry.status.in_(POSTED_STATES)))).first()
    if row is None or row[0].account_id != st.account_id:
        raise HTTPException(status_code=422, detail="Choose a posted journal line on the same bank account")
    if money(row[0].debit - row[0].credit) != money(st.amount):
        raise HTTPException(status_code=422, detail="Amounts differ; record the difference (e.g. bank charges) as a journal first")
    st.status, st.matched_line_id, st.matched_by, st.matched_at = "MATCHED", journal_line_id, user.id, utcnow()
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail="That journal line is already matched to another statement line")
    _audit(db, user, "bank.match", "bank_statement_line", st.id, {"journal_line_id": journal_line_id})
    return st


async def reconciliation_report(db: AsyncSession, pharmacy_id: int, account_id: int, as_of: date) -> dict:
    bank = await _bank_account(db, pharmacy_id, account_id)
    book_balance = await _account_balance(db, pharmacy_id, bank.id, as_of)
    statement = (await db.execute(select(BankStatementLine).where(
        BankStatementLine.pharmacy_id == pharmacy_id, BankStatementLine.account_id == bank.id,
        BankStatementLine.statement_date <= as_of).order_by(BankStatementLine.statement_date, BankStatementLine.id))).scalars().all()
    statement_total = money(sum((money(s.amount) for s in statement), ZERO))
    unmatched_book = [{"journal_line_id": jl.id, "entry_number": je.entry_number, "entry_date": je.entry_date,
                       "narration": je.narration, "amount": money(jl.debit - jl.credit)}
                      for jl, je in await _unmatched_book_lines(db, pharmacy_id, bank.id) if je.entry_date <= as_of]
    unmatched_statement = [{"id": s.id, "statement_date": s.statement_date, "description": s.description,
                            "reference": s.reference, "amount": s.amount} for s in statement if s.status == "UNMATCHED"]
    return {"account": {"id": bank.id, "code": bank.code, "name": bank.name}, "as_of": as_of,
            "book_balance": book_balance, "statement_movements": statement_total,
            "matched_lines": sum(1 for s in statement if s.status == "MATCHED"),
            "unmatched_book": unmatched_book, "unmatched_statement": unmatched_statement,
            "note": "Statement movements are the sum of imported lines; add the opening statement balance at the books "
                    "start to compare with the closing balance on your bank statement."}


# ── Year-end close ─────────────────────────────────────────────────────────────

async def close_year(db: AsyncSession, user: User, fy_id: int) -> FiscalYear:
    pid = user.pharmacy_id
    fy = (await db.execute(select(FiscalYear).options(selectinload(FiscalYear.periods)).where(
        FiscalYear.id == fy_id, FiscalYear.pharmacy_id == pid).with_for_update())).scalar_one_or_none()
    if fy is None:
        raise HTTPException(status_code=404, detail="Fiscal year not found")
    if fy.status == "CLOSED":
        raise HTTPException(status_code=409, detail=f"{fy.name} is already closed")
    periods = sorted(fy.periods, key=lambda p: p.start_date)
    last = periods[-1]
    still_open = [p.name for p in periods[:-1] if p.status == "OPEN"]
    if still_open:
        raise HTTPException(status_code=409, detail=f"Close these periods first: {', '.join(still_open)}")
    if last.status != "OPEN":
        raise HTTPException(status_code=409, detail=f"The last period ({last.name}) must be open to post the closing entry")
    issues = await gl_posting.open_issue_count(db, pid)
    if issues:
        raise HTTPException(status_code=409, detail=f"{issues} automatic posting(s) failed; resolve them before closing the year")
    pl = await financial_reports.profit_and_loss(db, pid, fy.start_date, fy.end_date)
    keyed = await ledger.accounts_by_key(db, pid)
    retained = keyed.get("RETAINED_EARNINGS")
    if retained is None:
        raise HTTPException(status_code=422, detail="Retained Earnings account is missing")
    lines: List[LineIn] = []
    for section, rows in pl["sections"].items():
        is_income = section in ("revenue", "other_income")
        for row in rows:
            amount = money(row["amount"])
            if amount == ZERO:
                continue
            # Zero the account: income balances are credits (debit them), expense balances debits (credit them)
            debit_side = is_income == (amount > 0)
            lines.append(LineIn(row["account_id"], debit=abs(amount) if debit_side else ZERO,
                                credit=ZERO if debit_side else abs(amount), description="Year-end close"))
    profit = money(pl["net_profit"])
    if profit != ZERO:
        lines.append(LineIn(retained.id, debit=ZERO if profit > 0 else -profit, credit=profit if profit > 0 else ZERO,
                            description=f"{'Profit' if profit > 0 else 'Loss'} for {fy.name}"))
    entry = None
    previous = (await db.execute(select(func.count(JournalEntry.id)).where(
        JournalEntry.pharmacy_id == pid, JournalEntry.source_key.like(f"YEAR_END:{fy.id}:%")))).scalar_one()
    if lines:
        entry, _ = await ledger.post_entry(
            db, user, entry_date=fy.end_date, narration=f"Year-end close {fy.name}: income and expenses to retained earnings",
            lines=lines, source_type="YEAR_END", source_id=str(fy.id), source_key=f"YEAR_END:{fy.id}:{previous}",
            reference=fy.name, allow_control=True)
    await ledger.close_period(db, user, last.id)
    fy.status = "CLOSED"
    _audit(db, user, "fiscal_year.close", "fiscal_year", fy.id, {"name": fy.name, "net_profit": profit,
                                                                 "entry": entry.entry_number if entry else None})
    nxt_start = fy.end_date + timedelta(days=1)
    has_next = (await db.execute(select(FiscalYear.id).where(FiscalYear.pharmacy_id == pid,
                                                             FiscalYear.start_date == nxt_start))).scalar_one_or_none()
    if not has_next:
        await ledger.create_fiscal_year(db, user, nxt_start, date(nxt_start.year + 1, nxt_start.month, 1) - timedelta(days=1))
    await db.flush()
    return fy


async def reopen_year(db: AsyncSession, user: User, fy_id: int, reason: str) -> FiscalYear:
    pid = user.pharmacy_id
    fy = (await db.execute(select(FiscalYear).options(selectinload(FiscalYear.periods)).where(
        FiscalYear.id == fy_id, FiscalYear.pharmacy_id == pid).with_for_update())).scalar_one_or_none()
    if fy is None:
        raise HTTPException(status_code=404, detail="Fiscal year not found")
    if fy.status != "CLOSED":
        raise HTTPException(status_code=409, detail=f"{fy.name} is not closed")
    last = sorted(fy.periods, key=lambda p: p.start_date)[-1]
    if last.status != "OPEN":
        await ledger.reopen_period(db, user, last.id, reason)
    closing = (await db.execute(select(JournalEntry).where(
        JournalEntry.pharmacy_id == pid, JournalEntry.source_type == "YEAR_END", JournalEntry.source_id == str(fy.id),
        JournalEntry.status == "POSTED"))).scalars().first()
    if closing is not None:
        await db.refresh(closing, attribute_names=["lines"])
        mirror = [LineIn(l.account_id, l.credit, l.debit, l.party_type, l.party_id, "Year-end reopen") for l in closing.lines]
        reversal, _ = await ledger.post_entry(
            db, user, entry_date=fy.end_date, narration=f"Reopen {fy.name}: {reason.strip()}", lines=mirror,
            source_type="YEAR_END", source_id=str(fy.id), source_key=f"YEAR_END_REVERSAL:{closing.id}",
            reference=closing.entry_number, allow_control=True, reversal_of_id=closing.id)
        closing.status = "REVERSED"
        closing.reversed_by_entry_id, closing.reversed_at, closing.reversal_reason = reversal.id, utcnow(), reason.strip()
    fy.status = "OPEN"
    _audit(db, user, "fiscal_year.reopen", "fiscal_year", fy.id, {"name": fy.name}, reason=reason)
    await db.flush()
    return fy


# ── CA export pack ─────────────────────────────────────────────────────────────

def _csv(rows: List[dict]) -> str:
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if v is None else v) for k, v in r.items()})
    return buf.getvalue()


async def ca_pack(db: AsyncSession, user: User, date_from: date, date_to: date) -> bytes:
    from app.services import sales_reports
    pid = user.pharmacy_id
    await _require_books(db, pid)
    tb = await ledger.trial_balance(db, pid, date_from, date_to)
    pl = await financial_reports.profit_and_loss(db, pid, date_from, date_to)
    bs = await financial_reports.balance_sheet(db, pid, date_to)
    gst = await financial_reports.gst_from_ledger(db, pid, date_from, date_to)
    ap = await financial_reports.payables_ageing(db, pid, date_to)
    ar = await sales_reports.aging(db, pid)
    book = await financial_reports.day_book(db, pid, date_from, date_to, limit=100000)
    gl_rows = [{"entry_number": e["entry_number"], "date": e["entry_date"], "source": e["source_type"], "reference": e["reference"],
                "narration": e["narration"], "account_code": l["code"], "account": l["account"], "debit": l["debit"],
                "credit": l["credit"], "party_type": l["party_type"], "party_id": l["party_id"]}
               for e in book["entries"] for l in e["lines"]]
    files = {
        "trial_balance.csv": _csv(tb["rows"]),
        "profit_and_loss.csv": _csv([{"section": s, **r} for s, rows in pl["sections"].items() for r in rows]
                                    + [{"section": "NET_PROFIT", "amount": pl["net_profit"]}]),
        "balance_sheet.csv": _csv([{"side": s, **r} for s, rows in bs["sides"].items() for r in rows]),
        "journal_lines.csv": _csv(gl_rows),
        "gst_from_ledger.csv": _csv([{"item": k, "amount": v} for k, v in
                                     [*(("output_" + a, b) for a, b in gst["output"].items()),
                                      *(("input_eligible_" + a, b) for a, b in gst["input_eligible"].items()),
                                      ("input_pending_review", gst["input_pending_review"]), ("net_payable", gst["net_payable"])]]),
        "payables_ageing.csv": _csv([{k: v for k, v in r.items() if k != "bills"} for r in ap["suppliers"]]),
        "receivables_ageing.csv": _csv(ar.get("customers", [])),
        "sales_register.csv": _csv(await sales_reports.register(db, pid, date_from, date_to)),
    }
    readme = (f"CA export pack {date_from} to {date_to}\nGenerated {utcnow().isoformat()} by user {user.id}\n"
              f"Trial balance balanced: {tb['is_balanced']}; balance sheet balanced: {bs['is_balanced']}\n"
              "Amounts in INR. Journal lines include posted and reversed entries (reversals are their own entries).\n"
              "GST figures are from the ledger for review; this pack is not a GST filing.\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", readme)
        for name, content in files.items():
            z.writestr(name, content)
    _audit(db, user, "accounting.ca_pack_export", "accounting", f"{date_from}..{date_to}", {"files": list(files)})
    await db.flush()
    return buf.getvalue()
