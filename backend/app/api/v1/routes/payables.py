"""Supplier payments, supplier balances and expenses (operational records; no GL posting yet)."""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.deps import AuthContext, require_context
from app.core.json import decimal_json
from app.models.payables import Expense, SupplierPayment
from app.models.purchasing import Supplier, SupplierInvoice
from app.schemas.payables import (
    AllocateRequest, DecisionRequest, ExpenseCategoryIn, ExpenseCategoryOut, ExpenseCreate, ExpenseOut,
    ReasonRequest, SupplierPaymentCreate, SupplierPaymentOut,
)
from app.services import expenses, supplier_payments
from app.services.sales import _user_names
from app.services.sales_common import ZERO, money

router = APIRouter(tags=["Payables & Expenses"])


async def _payment_out(db: AsyncSession, payment: SupplierPayment) -> dict:
    names = await _user_names(db, [payment.created_by, payment.reversed_by])
    supplier = (await db.execute(select(Supplier.name).where(Supplier.id == payment.supplier_id))).scalar_one_or_none()
    numbers = dict((await db.execute(select(SupplierInvoice.id, SupplierInvoice.invoice_number).where(
        SupplierInvoice.id.in_([a.invoice_id for a in payment.allocations])))).all()) if payment.allocations else {}
    allocated = money(sum((a.amount for a in payment.allocations if a.status == "ACTIVE"), ZERO))
    return SupplierPaymentOut(
        id=payment.id, payment_number=payment.payment_number, supplier_id=payment.supplier_id, supplier_name=supplier,
        payment_date=payment.payment_date, method=payment.method, cash_source=payment.cash_source,
        shift_id=payment.shift_id, amount=payment.amount, allocated=allocated,
        unallocated=money(payment.amount - allocated) if payment.status == "POSTED" else ZERO,
        reference=payment.reference, bank_account=payment.bank_account, notes=payment.notes, status=payment.status,
        is_legacy=payment.is_legacy, created_by_name=names.get(payment.created_by), created_at=payment.created_at,
        reversed_by_name=names.get(payment.reversed_by), reversed_at=payment.reversed_at,
        reversal_reason=payment.reversal_reason,
        allocations=[{"id": a.id, "invoice_id": a.invoice_id, "invoice_number": numbers.get(a.invoice_id),
                      "amount": a.amount, "status": a.status, "created_at": a.created_at} for a in payment.allocations],
    ).model_dump()


# ── Supplier payments ──────────────────────────────────────────────────────────

@router.get("/supplier-payments")
async def list_supplier_payments(
    supplier_id: Optional[int] = None,
    q: Optional[str] = Query(None, max_length=100),
    method: Optional[str] = Query(None, max_length=20),
    status_filter: Optional[str] = Query(None, alias="status"),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: AuthContext = Depends(require_context("supplier_payments.view")),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(SupplierPayment).options(selectinload(SupplierPayment.allocations)).where(
        SupplierPayment.pharmacy_id == ctx.pharmacy_id)
    if q:
        stmt = stmt.where(SupplierPayment.payment_number.ilike(f"%{q.strip()}%"))
    if method:
        stmt = stmt.where(SupplierPayment.method == method.upper())
    if supplier_id:
        stmt = stmt.where(SupplierPayment.supplier_id == supplier_id)
    if status_filter:
        stmt = stmt.where(SupplierPayment.status == status_filter.upper())
    if date_from:
        stmt = stmt.where(SupplierPayment.payment_date >= date_from)
    if date_to:
        stmt = stmt.where(SupplierPayment.payment_date <= date_to)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(SupplierPayment.payment_date.desc(), SupplierPayment.id.desc())
                             .limit(limit).offset(offset))).scalars().all()
    return decimal_json({"total": total, "items": [await _payment_out(db, p) for p in rows]})


@router.post("/supplier-payments", status_code=status.HTTP_201_CREATED)
async def create_supplier_payment(
    body: SupplierPaymentCreate,
    ctx: AuthContext = Depends(require_context("supplier_payments.create")),
    db: AsyncSession = Depends(get_db),
):
    payment, replayed = await supplier_payments.record_payment(db, ctx.user, ctx.permissions, body)
    return decimal_json({**await _payment_out(db, payment), "replayed": replayed}, status_code=200 if replayed else 201)


@router.get("/supplier-payments/balances")
async def supplier_balances(
    supplier_id: Optional[int] = None,
    ctx: AuthContext = Depends(require_context("supplier_payments.view")),
    db: AsyncSession = Depends(get_db),
):
    return decimal_json(await supplier_payments.supplier_balances(db, ctx.pharmacy_id, supplier_id))


@router.get("/supplier-payments/reconciliation")
async def payables_reconciliation(
    ctx: AuthContext = Depends(require_context("supplier_payments.view")),
    db: AsyncSession = Depends(get_db),
):
    return decimal_json(await supplier_payments.reconciliation(db, ctx.pharmacy_id))


@router.get("/supplier-payments/open-invoices/{supplier_id}")
async def open_supplier_invoices(
    supplier_id: int,
    ctx: AuthContext = Depends(require_context("supplier_payments.view")),
    db: AsyncSession = Depends(get_db),
):
    from app.services.supplier_invoices import outstanding
    rows = []
    for inv in await supplier_payments.open_invoices(db, ctx.pharmacy_id, supplier_id):
        figures = await outstanding(db, inv)
        if figures["outstanding"] > ZERO:
            rows.append({"id": inv.id, "invoice_number": inv.invoice_number, "invoice_date": inv.invoice_date,
                         "due_date": inv.due_date, "total": inv.calculated_grand_total, **figures})
    return decimal_json(rows)


@router.get("/supplier-payments/{payment_id}")
async def get_supplier_payment(
    payment_id: int,
    ctx: AuthContext = Depends(require_context("supplier_payments.view")),
    db: AsyncSession = Depends(get_db),
):
    return decimal_json(await _payment_out(db, await supplier_payments.get_payment(db, ctx.pharmacy_id, payment_id)))


@router.get("/supplier-payments/{payment_id}/document")
async def supplier_payment_voucher_document(
    payment_id: int,
    ctx: AuthContext = Depends(require_context("supplier_payments.view")),
    db: AsyncSession = Depends(get_db),
):
    """Everything a supplier payment voucher prints: header, bill-wise
    allocations, payable after the payment and advance. Read-only."""
    from app.services import payment_docs
    return decimal_json(await payment_docs.supplier_voucher_document(db, ctx.pharmacy_id, payment_id))


@router.get("/supplier-payments/{payment_id}/document.pdf")
async def supplier_payment_voucher_pdf(
    payment_id: int,
    ctx: AuthContext = Depends(require_context("supplier_payments.view")),
    db: AsyncSession = Depends(get_db),
):
    from fastapi.responses import Response
    from app.services import payment_pdf
    content, filename = await payment_pdf.supplier_voucher_pdf_bytes(db, ctx.pharmacy_id, payment_id)
    return Response(content=content, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/supplier-payments/{payment_id}/allocate")
async def allocate_supplier_payment(
    payment_id: int,
    body: AllocateRequest,
    ctx: AuthContext = Depends(require_context("supplier_payments.create")),
    db: AsyncSession = Depends(get_db),
):
    payment = await supplier_payments.allocate(db, ctx.user, ctx.permissions, payment_id, body.allocations)
    return decimal_json(await _payment_out(db, payment))


@router.post("/supplier-payments/{payment_id}/reverse")
async def reverse_supplier_payment(
    payment_id: int,
    body: ReasonRequest,
    ctx: AuthContext = Depends(require_context("supplier_payments.reverse")),
    db: AsyncSession = Depends(get_db),
):
    payment = await supplier_payments.reverse(db, ctx.user, ctx.permissions, payment_id, body.reason)
    return decimal_json(await _payment_out(db, payment))


# ── Expenses ───────────────────────────────────────────────────────────────────

async def _expense_out(db: AsyncSession, e: Expense) -> dict:
    names = await _user_names(db, [e.created_by, e.approved_by, e.reversed_by])
    return ExpenseOut(
        id=e.id, expense_number=e.expense_number, category_id=e.category_id,
        category_name=e.category.name if e.category else None, expense_date=e.expense_date, amount=e.amount,
        gst_amount=e.gst_amount, payee=e.payee, payee_gstin=e.payee_gstin, supplier_id=e.supplier_id,
        bill_number=e.bill_number, payment_method=e.payment_method, cash_source=e.cash_source, shift_id=e.shift_id,
        reference=e.reference, notes=e.notes, status=e.status, created_by_name=names.get(e.created_by),
        created_at=e.created_at, approved_by_name=names.get(e.approved_by), approved_at=e.approved_at,
        decision_note=e.decision_note, reversed_by_name=names.get(e.reversed_by), reversed_at=e.reversed_at,
        reversal_reason=e.reversal_reason,
    ).model_dump()


@router.get("/expenses/categories")
async def list_expense_categories(
    ctx: AuthContext = Depends(require_context("expenses.view")),
    db: AsyncSession = Depends(get_db),
):
    return [ExpenseCategoryOut.model_validate(c, from_attributes=True).model_dump()
            for c in await expenses.list_categories(db, ctx.pharmacy_id)]


@router.post("/expenses/categories", status_code=status.HTTP_201_CREATED)
async def create_expense_category(
    body: ExpenseCategoryIn,
    ctx: AuthContext = Depends(require_context("expenses.configure")),
    db: AsyncSession = Depends(get_db),
):
    cat = await expenses.save_category(db, ctx.user, None, body)
    return ExpenseCategoryOut.model_validate(cat, from_attributes=True).model_dump()


@router.put("/expenses/categories/{category_id}")
async def update_expense_category(
    category_id: int,
    body: ExpenseCategoryIn,
    ctx: AuthContext = Depends(require_context("expenses.configure")),
    db: AsyncSession = Depends(get_db),
):
    cat = await expenses.save_category(db, ctx.user, category_id, body)
    return ExpenseCategoryOut.model_validate(cat, from_attributes=True).model_dump()


@router.get("/expenses")
async def list_expenses(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    category_id: Optional[int] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: AuthContext = Depends(require_context("expenses.view")),
    db: AsyncSession = Depends(get_db),
):
    result = await expenses.list_expenses(db, ctx.pharmacy_id, date_from=date_from, date_to=date_to,
                                          category_id=category_id, status=status_filter, limit=limit, offset=offset)
    return decimal_json({**result, "items": [await _expense_out(db, e) for e in result["items"]]})


@router.post("/expenses", status_code=status.HTTP_201_CREATED)
async def create_expense(
    body: ExpenseCreate,
    ctx: AuthContext = Depends(require_context("expenses.create")),
    db: AsyncSession = Depends(get_db),
):
    expense, replayed = await expenses.record_expense(db, ctx.user, ctx.permissions, body)
    return decimal_json({**await _expense_out(db, expense), "replayed": replayed}, status_code=200 if replayed else 201)


@router.get("/expenses/{expense_id}")
async def get_expense(
    expense_id: int,
    ctx: AuthContext = Depends(require_context("expenses.view")),
    db: AsyncSession = Depends(get_db),
):
    return decimal_json(await _expense_out(db, await expenses.get_expense(db, ctx.pharmacy_id, expense_id)))


@router.post("/expenses/{expense_id}/approve")
async def approve_expense(
    expense_id: int,
    body: DecisionRequest,
    ctx: AuthContext = Depends(require_context("expenses.approve")),
    db: AsyncSession = Depends(get_db),
):
    expense = await expenses.decide(db, ctx.user, ctx.permissions, expense_id, True, body.note)
    return decimal_json(await _expense_out(db, expense))


@router.post("/expenses/{expense_id}/reject")
async def reject_expense(
    expense_id: int,
    body: DecisionRequest,
    ctx: AuthContext = Depends(require_context("expenses.approve")),
    db: AsyncSession = Depends(get_db),
):
    expense = await expenses.decide(db, ctx.user, ctx.permissions, expense_id, False, body.note)
    return decimal_json(await _expense_out(db, expense))


@router.post("/expenses/{expense_id}/reverse")
async def reverse_expense(
    expense_id: int,
    body: ReasonRequest,
    ctx: AuthContext = Depends(require_context("expenses.reverse")),
    db: AsyncSession = Depends(get_db),
):
    expense = await expenses.reverse(db, ctx.user, ctx.permissions, expense_id, body.reason)
    return decimal_json(await _expense_out(db, expense))


# ── Supplier credit notes ──────────────────────────────────────────────────────

from pydantic import BaseModel, Field  # noqa: E402
from datetime import date as _date  # noqa: E402
from decimal import Decimal  # noqa: E402


class CreditNoteIn(BaseModel):
    idempotency_key: str = Field(..., min_length=8, max_length=64)
    supplier_id: int
    credit_note_number: str = Field(..., min_length=1, max_length=100)
    note_date: _date
    invoice_id: Optional[int] = None
    reason_code: str
    taxable_amount: Decimal = Field(..., ge=0)
    cgst_amount: Decimal = Field(Decimal("0"), ge=0)
    sgst_amount: Decimal = Field(Decimal("0"), ge=0)
    igst_amount: Decimal = Field(Decimal("0"), ge=0)
    notes: Optional[str] = Field(None, max_length=1000)


async def _note_out(db: AsyncSession, n) -> dict:
    supplier = (await db.execute(select(Supplier.name).where(Supplier.id == n.supplier_id))).scalar_one_or_none()
    bill = (await db.execute(select(SupplierInvoice.invoice_number).where(SupplierInvoice.id == n.invoice_id))).scalar_one_or_none() \
        if n.invoice_id else None
    return {"id": n.id, "credit_note_number": n.credit_note_number, "supplier_id": n.supplier_id, "supplier_name": supplier,
            "note_date": n.note_date, "invoice_id": n.invoice_id, "invoice_number": bill, "reason_code": n.reason_code,
            "taxable_amount": n.taxable_amount, "cgst_amount": n.cgst_amount, "sgst_amount": n.sgst_amount,
            "igst_amount": n.igst_amount, "total_amount": n.total_amount, "notes": n.notes, "status": n.status,
            "cancel_reason": n.cancel_reason, "created_at": n.created_at}


@router.get("/supplier-credit-notes")
async def list_credit_notes(supplier_id: Optional[int] = None,
                            ctx: AuthContext = Depends(require_context("supplier_payments.view")), db: AsyncSession = Depends(get_db)):
    from app.services import supplier_credit_notes
    return decimal_json([await _note_out(db, n) for n in await supplier_credit_notes.list_notes(db, ctx.pharmacy_id, supplier_id)])


@router.post("/supplier-credit-notes", status_code=status.HTTP_201_CREATED)
async def create_credit_note(body: CreditNoteIn, ctx: AuthContext = Depends(require_context("supplier_payments.create")),
                             db: AsyncSession = Depends(get_db)):
    from app.services import supplier_credit_notes
    note, replayed = await supplier_credit_notes.create(db, ctx.user, ctx.permissions, body)
    return decimal_json({**await _note_out(db, note), "replayed": replayed}, status_code=200 if replayed else 201)


@router.post("/supplier-credit-notes/{note_id}/cancel")
async def cancel_credit_note(note_id: int, body: ReasonRequest,
                             ctx: AuthContext = Depends(require_context("purchases.invoice_cancel")), db: AsyncSession = Depends(get_db)):
    from app.services import supplier_credit_notes
    return decimal_json(await _note_out(db, await supplier_credit_notes.cancel(db, ctx.user, ctx.permissions, note_id, body.reason)))
