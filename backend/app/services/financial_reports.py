"""
Stage 5C: financial statements read from the general ledger (posted and reversed
entries; drafts never count).

* Profit & loss for a date range (year-end closing entries excluded so a closed year
  still shows its result).
* Balance sheet as at a date, with customer advances (credit balances on receivables)
  and supplier advances (debit balances on payables) shown on the correct side, and the
  not-yet-closed profit shown in equity. Assets = liabilities + equity is checked.
* Cash flow (direct method) from movements on cash, bank and payment-clearing accounts,
  classified by the counter-account.
* Day book, GST summary from the ledger, and payables ageing.
"""
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.accounting import JournalEntry, JournalLine, LedgerAccount
from app.models.purchasing import Supplier, SupplierInvoice
from app.services.ledger import POSTED_STATES, ZERO, money

YEAR_END = "YEAR_END"
COST_OF_SALES_KEYS = {"COGS", "PURCHASE_VARIANCE", "INVENTORY_WRITE_OFF", "STOCK_ADJUSTMENT"}
CASH_GROUPS = ("1110", "1120", "1130")        # Cash in hand, bank accounts, payment clearing


async def _accounts(db: AsyncSession, pharmacy_id: int) -> Dict[int, LedgerAccount]:
    return {a.id: a for a in (await db.execute(select(LedgerAccount).where(
        LedgerAccount.pharmacy_id == pharmacy_id))).scalars().all()}


def _ancestors(acc: LedgerAccount, by_id: Dict[int, LedgerAccount]) -> List[LedgerAccount]:
    out, p = [], acc.parent_id
    while p and p in by_id:
        out.append(by_id[p])
        p = by_id[p].parent_id
    return out


def _under(acc: LedgerAccount, by_id, codes) -> bool:
    return acc.code in codes or any(a.code in codes for a in _ancestors(acc, by_id))


def _group_name(acc: LedgerAccount, by_id) -> str:
    parent = by_id.get(acc.parent_id) if acc.parent_id else None
    return parent.name if parent else acc.account_type.title()


async def _sums(db: AsyncSession, pharmacy_id: int, date_from: Optional[date], date_to: date,
                exclude_year_end: bool = False, party: bool = False):
    cols = [JournalLine.account_id] + ([JournalLine.party_id] if party else [])
    conds = [JournalEntry.pharmacy_id == pharmacy_id, JournalEntry.status.in_(POSTED_STATES), JournalEntry.entry_date <= date_to]
    if date_from:
        conds.append(JournalEntry.entry_date >= date_from)
    if exclude_year_end:
        conds.append(JournalEntry.source_type != YEAR_END)
    rows = (await db.execute(select(*cols, func.sum(JournalLine.debit - JournalLine.credit))
                             .join(JournalEntry, JournalEntry.id == JournalLine.entry_id).where(*conds).group_by(*cols))).all()
    if party:
        return {(r[0], r[1]): money(r[2] or 0) for r in rows}
    return {r[0]: money(r[1] or 0) for r in rows}


# ── Profit & loss ──────────────────────────────────────────────────────────────

async def profit_and_loss(db: AsyncSession, pharmacy_id: int, date_from: date, date_to: date) -> dict:
    by_id = await _accounts(db, pharmacy_id)
    net = await _sums(db, pharmacy_id, date_from, date_to, exclude_year_end=True)
    sections = {"revenue": [], "cost_of_sales": [], "other_income": [], "expenses": []}
    for acc_id, dr in net.items():
        acc = by_id[acc_id]
        if dr == ZERO or acc.account_type not in ("INCOME", "EXPENSE"):
            continue
        amount = -dr if acc.account_type == "INCOME" else dr       # Income shown as credit-positive, expense debit-positive
        row = {"account_id": acc.id, "code": acc.code, "name": acc.name, "group": _group_name(acc, by_id), "amount": money(amount)}
        if acc.account_type == "INCOME":
            sections["other_income" if _under(acc, by_id, {"4300"}) else "revenue"].append(row)
        else:
            sections["cost_of_sales" if acc.system_key in COST_OF_SALES_KEYS else "expenses"].append(row)
    for rows in sections.values():
        rows.sort(key=lambda r: r["code"])
    total = {k: money(sum((r["amount"] for r in v), ZERO)) for k, v in sections.items()}
    gross = money(total["revenue"] - total["cost_of_sales"])
    net_profit = money(gross + total["other_income"] - total["expenses"])
    return {"date_from": date_from, "date_to": date_to, "sections": sections, "totals": total,
            "gross_profit": gross, "net_profit": net_profit}


# ── Balance sheet ──────────────────────────────────────────────────────────────

async def balance_sheet(db: AsyncSession, pharmacy_id: int, as_of: date) -> dict:
    by_id = await _accounts(db, pharmacy_id)
    net = await _sums(db, pharmacy_id, None, as_of)
    by_party = await _sums(db, pharmacy_id, None, as_of, party=True)
    keyed = {a.system_key: a for a in by_id.values() if a.system_key}
    sides = {"assets": [], "liabilities": [], "equity": []}
    unclosed = ZERO

    def put(side, acc, amount, name=None):
        if amount != ZERO:
            sides[side].append({"account_id": acc.id, "code": acc.code, "name": name or acc.name,
                                "group": _group_name(acc, by_id), "amount": money(amount)})

    ar, ap = keyed.get("AR"), keyed.get("AP")
    for acc_id, dr in net.items():
        acc = by_id[acc_id]
        if acc.account_type in ("INCOME", "EXPENSE"):
            unclosed += -dr
        elif ar and acc_id == ar.id or ap and acc_id == ap.id:
            continue
        elif acc.account_type == "ASSET":
            put("assets", acc, dr)
        elif acc.account_type == "LIABILITY":
            put("liabilities", acc, -dr)
        else:
            put("equity", acc, -dr)
    # Party accounts: debit balances are assets, credit balances liabilities (advances)
    for control, debit_side, credit_side, debit_name, credit_name in (
            (ar, "assets", "liabilities", None, "Customer advances & credit notes (credit balances)"),
            (ap, "assets", "liabilities", "Supplier advances (debit balances)", None)):
        if control is None:
            continue
        dr_total = sum((v for (a, _), v in by_party.items() if a == control.id and v > 0), ZERO)
        cr_total = -sum((v for (a, _), v in by_party.items() if a == control.id and v < 0), ZERO)
        put(debit_side, control, dr_total, debit_name)
        put(credit_side, control, cr_total, credit_name)
    if unclosed != ZERO:
        sides["equity"].append({"account_id": None, "code": "", "name": "Profit / (loss) not yet closed to retained earnings",
                                "group": "Equity", "amount": money(unclosed)})
    totals = {k: money(sum((r["amount"] for r in v), ZERO)) for k, v in sides.items()}
    for rows in sides.values():
        rows.sort(key=lambda r: r["code"])
    return {"as_of": as_of, "sides": sides, "totals": totals,
            "liabilities_and_equity": money(totals["liabilities"] + totals["equity"]),
            "is_balanced": totals["assets"] == money(totals["liabilities"] + totals["equity"])}


# ── Cash flow (direct) ─────────────────────────────────────────────────────────

def _flow_category(acc: LedgerAccount, by_id) -> str:
    if acc.account_type == "EQUITY" or _under(acc, by_id, {"2200"}):
        return "financing"
    if _under(acc, by_id, {"1200"}):
        return "investing"
    return "operating"


async def cash_flow(db: AsyncSession, pharmacy_id: int, date_from: date, date_to: date) -> dict:
    by_id = await _accounts(db, pharmacy_id)
    cash_ids = {a.id for a in by_id.values() if not a.is_group and _under(a, by_id, set(CASH_GROUPS))}
    opening = money(sum((v for k, v in (await _sums(db, pharmacy_id, None, date_from.fromordinal(date_from.toordinal() - 1))).items()
                         if k in cash_ids), ZERO)) if date_from else ZERO
    entries = (await db.execute(select(JournalEntry).options(selectinload(JournalEntry.lines)).where(
        JournalEntry.pharmacy_id == pharmacy_id, JournalEntry.status.in_(POSTED_STATES),
        JournalEntry.entry_date >= date_from, JournalEntry.entry_date <= date_to))).scalars().all()
    flows: Dict[str, Dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    for e in entries:
        if not any(l.account_id in cash_ids for l in e.lines):
            continue
        for l in e.lines:
            if l.account_id in cash_ids:
                continue
            acc = by_id[l.account_id]
            flows[_flow_category(acc, by_id)][acc.name] += money(l.credit) - money(l.debit)   # Credit to a counter-account = cash in
    sections = {cat: [{"line": name, "amount": money(v)} for name, v in sorted(items.items()) if money(v) != ZERO]
                for cat, items in flows.items()}
    totals = {cat: money(sum((r["amount"] for r in rows), ZERO)) for cat, rows in sections.items()}
    net = money(sum(totals.values(), ZERO))
    closing = money(sum((v for k, v in (await _sums(db, pharmacy_id, None, date_to)).items() if k in cash_ids), ZERO))
    return {"date_from": date_from, "date_to": date_to, "opening_cash": opening, "sections": sections, "totals": totals,
            "net_change": net, "closing_cash": closing, "reconciles": money(opening + net) == closing,
            "cash_accounts": sorted(by_id[i].name for i in cash_ids)}


# ── Day book, GST, payables ageing ─────────────────────────────────────────────

async def day_book(db: AsyncSession, pharmacy_id: int, date_from: date, date_to: date, limit: int = 500, offset: int = 0) -> dict:
    by_id = await _accounts(db, pharmacy_id)
    stmt = select(JournalEntry).options(selectinload(JournalEntry.lines)).where(
        JournalEntry.pharmacy_id == pharmacy_id, JournalEntry.status.in_(POSTED_STATES),
        JournalEntry.entry_date >= date_from, JournalEntry.entry_date <= date_to)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(JournalEntry.entry_date, JournalEntry.id).limit(limit).offset(offset))).scalars().all()
    return {"total": total, "entries": [{
        "entry_id": e.id, "entry_number": e.entry_number, "entry_date": e.entry_date, "source_type": e.source_type,
        "reference": e.reference, "narration": e.narration, "status": e.status, "total": e.total_debit,
        "lines": [{"code": by_id[l.account_id].code, "account": by_id[l.account_id].name, "debit": l.debit, "credit": l.credit,
                   "party_type": l.party_type, "party_id": l.party_id} for l in e.lines],
    } for e in rows]}


async def gst_from_ledger(db: AsyncSession, pharmacy_id: int, date_from: date, date_to: date) -> dict:
    by_id = await _accounts(db, pharmacy_id)
    keyed = {a.system_key: a for a in by_id.values() if a.system_key}
    net = await _sums(db, pharmacy_id, date_from, date_to, exclude_year_end=True)
    def val(key, sign=1):
        acc = keyed.get(key)
        return money(sign * net.get(acc.id, ZERO)) if acc else ZERO
    output = {k: val(f"OUTPUT_{k}", -1) for k in ("CGST", "SGST", "IGST")}
    eligible = {k: val(f"INPUT_{k}") for k in ("CGST", "SGST", "IGST")}
    pending = val("INPUT_GST_PENDING")
    out_total, in_total = money(sum(output.values(), ZERO)), money(sum(eligible.values(), ZERO))
    return {"date_from": date_from, "date_to": date_to, "output": output, "output_total": out_total,
            "input_eligible": eligible, "input_eligible_total": in_total, "input_pending_review": pending,
            "net_payable": money(out_total - in_total),
            "note": "Input GST waiting for eligibility review is not set off until it is moved to the CGST/SGST/IGST input "
                    "accounts by journal. Figures are for review with your CA; nothing is filed."}


def _bucket(days: int) -> str:
    if days <= 0:
        return "current"
    if days <= 30:
        return "1-30"
    if days <= 60:
        return "31-60"
    if days <= 90:
        return "61-90"
    return "90+"


async def payables_ageing(db: AsyncSession, pharmacy_id: int, as_of: date) -> dict:
    from app.services.supplier_invoices import outstanding
    bills = (await db.execute(select(SupplierInvoice, Supplier.name).join(Supplier, Supplier.id == SupplierInvoice.supplier_id).where(
        SupplierInvoice.pharmacy_id == pharmacy_id, SupplierInvoice.status == "POSTED",
        SupplierInvoice.payment_status != "PAID"))).all()
    suppliers: Dict[int, dict] = {}
    buckets = ("current", "1-30", "31-60", "61-90", "90+")
    totals = {b: ZERO for b in buckets}
    for bill, name in bills:
        due = (await outstanding(db, bill))["outstanding"]
        if due <= ZERO:
            continue
        b = _bucket((as_of - bill.due_date).days)
        row = suppliers.setdefault(bill.supplier_id, {"supplier_id": bill.supplier_id, "supplier": name, "total": ZERO,
                                                      **{k: ZERO for k in buckets}, "bills": []})
        row[b] += due
        row["total"] += due
        totals[b] += due
        row["bills"].append({"invoice_id": bill.id, "invoice_number": bill.invoice_number, "due_date": bill.due_date,
                             "outstanding": due, "bucket": b})
    return {"as_of": as_of, "suppliers": sorted(suppliers.values(), key=lambda r: -r["total"]),
            "totals": {k: money(v) for k, v in totals.items()}, "total": money(sum(totals.values(), ZERO))}
