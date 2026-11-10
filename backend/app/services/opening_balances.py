"""
Opening balances (Phase 5A).

Workflow: prepare a DRAFT batch → preview (validation + reconciliation with the ERP)
→ post once. After posting the batch is read-only; corrections are separate journals
(or, with permission, reversing the opening entry and posting a new batch).

Rules
* Opening date = the books' start date; the entry is dated on it.
* Customer and supplier balances are entered per outstanding document with its date
  and due date, never as one lump sum; their total must equal the control account.
* Nothing the ERP already holds may be entered again: a customer or supplier with ERP
  activity before the opening date is refused, and opening inventory is compared with
  the stock value the ERP holds as at the opening date.
* If debits and credits differ, posting is refused unless the user explicitly asks for
  the difference to go to "Opening Balance Difference (Suspense)", where it stays
  visible until investigated. No balancing figure is ever invented silently.
"""
import json
from datetime import date
from decimal import Decimal
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.clock import day_bounds, utcnow
from app.models.accounting import LedgerAccount, OpeningBalanceBatch, OpeningBalanceLine
from app.models.auth import User
from app.models.inventory import StockMovement
from app.models.purchasing import Supplier, SupplierInvoice
from app.models.sales import Customer, CustomerLedgerEntry
from app.services import ledger
from app.services.ledger import LineIn, ZERO, money


async def get_batch(db: AsyncSession, pharmacy_id: int, batch_id: int, lock: bool = False) -> OpeningBalanceBatch:
    stmt = select(OpeningBalanceBatch).options(selectinload(OpeningBalanceBatch.lines)).where(
        OpeningBalanceBatch.id == batch_id, OpeningBalanceBatch.pharmacy_id == pharmacy_id)
    if lock:
        stmt = stmt.with_for_update()
    batch = (await db.execute(stmt)).scalar_one_or_none()
    if batch is None:
        raise HTTPException(status_code=404, detail="Opening balance batch not found")
    return batch


async def save_batch(db: AsyncSession, user: User, batch_id: Optional[int], data: dict) -> OpeningBalanceBatch:
    pid = user.pharmacy_id
    start = await ledger.books_start_date(db, pid)
    if start is None:
        raise HTTPException(status_code=409, detail="Set up accounting (books start date) first")
    if batch_id is None:
        if (await db.execute(select(OpeningBalanceBatch.id).where(
                OpeningBalanceBatch.pharmacy_id == pid, OpeningBalanceBatch.status.in_(("DRAFT", "POSTED"))))).first():
            raise HTTPException(status_code=409, detail="An opening balance batch already exists; edit it instead")
        batch = OpeningBalanceBatch(pharmacy_id=pid, opening_date=start, prepared_by=user.id,
                                    source_description=data["source_description"].strip())
        db.add(batch)
        await db.flush()
        await db.refresh(batch, attribute_names=["lines"])
    else:
        batch = await get_batch(db, pid, batch_id, lock=True)
        if batch.status != "DRAFT":
            raise HTTPException(status_code=409, detail="A posted opening batch cannot be changed")
        batch.source_description = data["source_description"].strip()
        for old in list(batch.lines):
            await db.delete(old)
        await db.flush()
    batch.notes = data.get("notes")
    batch.post_difference_to_suspense = bool(data.get("post_difference_to_suspense"))
    for line in data["lines"]:
        d, c = money(line.get("debit") or 0), money(line.get("credit") or 0)
        if (d > ZERO) == (c > ZERO) or d < ZERO or c < ZERO:
            raise HTTPException(status_code=422, detail="Each opening line needs either a debit or a credit")
        db.add(OpeningBalanceLine(
            batch_id=batch.id, account_id=line["account_id"], debit=d, credit=c,
            party_type=line.get("party_type"), party_id=line.get("party_id"),
            document_number=(line.get("document_number") or "").strip() or None,
            document_date=line.get("document_date"), due_date=line.get("due_date"),
            outstanding_amount=d or c, notes=line.get("notes"),
        ))
    await db.flush()
    ledger._audit(db, user, "opening.save", "opening_balance_batch", batch.id, {"lines": len(data["lines"])})
    await db.refresh(batch, attribute_names=["lines"])   # The cached collection predates the new lines
    return batch


async def erp_inventory_value(db: AsyncSession, pharmacy_id: int, opening: date) -> Decimal:
    """
    Stock value the ERP holds as at the opening date: every movement before that date, plus
    OPENING_STOCK movements on any date (they represent stock that existed before go-live).
    Valued at each movement's recorded unit cost (batch weighted-average cost).
    """
    start, _ = day_bounds(opening)
    value = (await db.execute(
        select(func.coalesce(func.sum(StockMovement.quantity_base * StockMovement.unit_cost), 0)).where(
            StockMovement.pharmacy_id == pharmacy_id,
            (StockMovement.created_at < start) | (StockMovement.movement_type == "OPENING_STOCK"),
        )
    )).scalar_one()
    return money(value)


async def preview(db: AsyncSession, pharmacy_id: int, batch: OpeningBalanceBatch) -> dict:
    accounts = {a.id: a for a in (await db.execute(select(LedgerAccount).where(
        LedgerAccount.pharmacy_id == pharmacy_id))).scalars().all()}
    keyed = {a.system_key: a for a in accounts.values() if a.system_key}
    errors: List[str] = []
    warnings: List[str] = []
    opening = batch.opening_date
    start, _ = day_bounds(opening)
    debit = credit = ZERO
    by_account: dict = {}
    party_docs: dict = {}

    for i, l in enumerate(batch.lines, start=1):
        a = accounts.get(l.account_id)
        if a is None:
            errors.append(f"Line {i}: account not found")
            continue
        if a.is_group or not a.is_active:
            errors.append(f"Line {i}: '{a.name}' is a group or inactive account")
        if a.account_type in ("INCOME", "EXPENSE"):
            errors.append(f"Line {i}: '{a.name}' is an income/expense account; opening balances are balance-sheet only "
                          f"(last year's profit belongs in Retained Earnings / Capital)")
        if a.system_key == "OPENING_DIFFERENCE":
            errors.append(f"Line {i}: do not enter the suspense account yourself; use 'post difference to suspense'")
        if a.party_type:
            if l.party_type != a.party_type or not l.party_id:
                errors.append(f"Line {i}: '{a.name}' needs a {a.party_type.lower()}")
            elif not l.document_number or not l.document_date:
                errors.append(f"Line {i}: enter the outstanding document number and date (not a lump sum)")
            elif l.document_date > opening:
                errors.append(f"Line {i}: document {l.document_number} is dated after the opening date")
            else:
                key = (l.party_type, l.party_id, l.document_number)
                if key in party_docs:
                    errors.append(f"Line {i}: document {l.document_number} is entered twice for the same party")
                party_docs[key] = i
        elif l.party_type or l.party_id:
            errors.append(f"Line {i}: '{a.name}' does not take a customer / supplier")
        debit += l.debit
        credit += l.credit
        net = by_account.get(a.id, ZERO) + l.debit - l.credit
        by_account[a.id] = net

    # Parties: no double entry of what the ERP already holds
    customers = {pid for (t, pid, _) in party_docs if t == "CUSTOMER"}
    suppliers = {pid for (t, pid, _) in party_docs if t == "SUPPLIER"}
    for cid in customers:
        c = (await db.execute(select(Customer).where(Customer.id == cid, Customer.pharmacy_id == pharmacy_id))).scalar_one_or_none()
        if c is None:
            errors.append(f"Customer {cid} not found")
            continue
        erp = (await db.execute(select(func.count(CustomerLedgerEntry.id)).where(
            CustomerLedgerEntry.customer_id == cid, CustomerLedgerEntry.created_at < start))).scalar_one()
        if erp:
            errors.append(f"Customer '{c.name}' already has ERP ledger activity before {opening}; "
                          f"those balances are already in the system and must not be entered again")
    for sid in suppliers:
        s = (await db.execute(select(Supplier).where(Supplier.id == sid, Supplier.pharmacy_id == pharmacy_id))).scalar_one_or_none()
        if s is None:
            errors.append(f"Supplier {sid} not found")
            continue
        erp = (await db.execute(select(func.count(SupplierInvoice.id)).where(
            SupplierInvoice.supplier_id == sid, SupplierInvoice.invoice_date < opening))).scalar_one()
        if erp:
            errors.append(f"Supplier '{s.name}' already has ERP bills dated before {opening}; do not enter them again")

    # Inventory: compare with what the ERP's stock records hold
    inv = keyed.get("INVENTORY")
    erp_inv = await erp_inventory_value(db, pharmacy_id, opening)
    entered_inv = money(by_account.get(inv.id, ZERO)) if inv else ZERO
    reconciliation = [{
        "item": "Inventory", "entered": entered_inv, "erp": erp_inv, "difference": money(entered_inv - erp_inv),
        "basis": "ERP stock movements before the opening date plus all OPENING_STOCK entries, at recorded unit cost",
    }]
    if entered_inv != erp_inv:
        warnings.append(
            f"Opening inventory ₹{entered_inv} differs from the ERP stock value ₹{erp_inv} by ₹{money(entered_inv - erp_inv)}. "
            f"Either the stock entered in the ERP or the accountant's figure needs correcting before posting."
        )
    for key, label in (("AR", "Customer receivables"), ("AP", "Supplier payables")):
        acc = keyed.get(key)
        entered = money(by_account.get(acc.id, ZERO)) if acc else ZERO
        if key == "AP":
            entered = -entered
        reconciliation.append({"item": label, "entered": entered, "erp": ZERO, "difference": entered,
                               "basis": "Itemised opening documents; the ERP has no balances before the opening date"})

    difference = money(debit - credit)
    if difference and not batch.post_difference_to_suspense:
        errors.append(f"Debits ₹{money(debit)} and credits ₹{money(credit)} differ by ₹{abs(difference)}. Correct the figures, "
                      f"or explicitly choose to post the difference to 'Opening Balance Difference (Suspense)'.")
    elif difference:
        warnings.append(f"₹{abs(difference)} will be posted to 'Opening Balance Difference (Suspense)' and must be investigated.")
    if len(batch.lines) < 1:
        errors.append("Enter at least one opening balance")
    period = None
    try:
        period = await ledger.period_for(db, pharmacy_id, opening)
        if period.status != "OPEN":
            errors.append(f"The opening date's period {period.name} is {period.status.lower()}")
    except HTTPException as exc:
        errors.append(exc.detail)

    return {
        "batch_id": batch.id, "status": batch.status, "opening_date": opening,
        "total_debit": money(debit), "total_credit": money(credit), "difference": difference,
        "post_difference_to_suspense": batch.post_difference_to_suspense,
        "errors": errors, "warnings": warnings, "reconciliation": reconciliation,
        "can_post": not errors and batch.status == "DRAFT",
    }


async def post_batch(db: AsyncSession, user: User, batch_id: int, acknowledge_warnings: bool):
    pid = user.pharmacy_id
    batch = await get_batch(db, pid, batch_id, lock=True)
    if batch.status != "DRAFT":
        raise HTTPException(status_code=409, detail=f"This batch is already {batch.status.lower()}")
    result = await preview(db, pid, batch)
    if result["errors"]:
        raise HTTPException(status_code=422, detail="; ".join(result["errors"]))
    if result["warnings"] and not acknowledge_warnings:
        raise HTTPException(status_code=422, detail="Review and acknowledge the warnings: " + " ".join(result["warnings"]))
    lines = [LineIn(l.account_id, l.debit, l.credit, l.party_type, l.party_id,
                    f"{l.document_number} dated {l.document_date}" if l.document_number else l.notes)
             for l in batch.lines]
    diff = result["difference"]
    if diff:
        suspense = await ledger.system_account(db, pid, "OPENING_DIFFERENCE")
        lines.append(LineIn(suspense.id, credit=diff if diff > 0 else ZERO, debit=-diff if diff < 0 else ZERO,
                            description="UNRESOLVED opening balance difference — investigate"))
    entry, _ = await ledger.post_entry(
        db, user, entry_date=batch.opening_date, narration=f"Opening balances as at {batch.opening_date}: {batch.source_description}",
        lines=lines, source_type="OPENING", source_id=str(batch.id), source_key=f"OPENING:{batch.id}",
        reference="Opening balances", allow_control=True,
    )
    batch.status, batch.journal_entry_id, batch.posted_by, batch.posted_at = "POSTED", entry.id, user.id, utcnow()
    ledger._audit(db, user, "opening.post", "opening_balance_batch", batch.id, {
        "journal": entry.entry_number, "total": result["total_debit"], "suspense": diff,
        "warnings_acknowledged": result["warnings"]})
    await db.flush()
    return batch, entry
