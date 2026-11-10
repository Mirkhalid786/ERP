"""Sales returns, payments, receivables and cash shifts."""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.json import decimal_json
from app.core.deps import AuthContext, require_context
from app.models.sales import SalesInvoice, SalesReturn
from app.schemas.sales import (
    CashMovementClassify, CashMovementRequest, PaymentActionRequest, PaymentResponse, ReturnRequest, ReturnResponse,
    ShiftCloseRequest, ShiftOpenRequest, ShiftReviewRequest, ShiftSummary,
)
from app.services import receivables, sales, sales_returns, shifts
from app.services.sales_common import open_shift_for, today_local

router = APIRouter(tags=["Sales operations"])


# ── Returns ────────────────────────────────────────────────────────────────────

@router.post("/sales/returns", response_model=ReturnResponse, status_code=status.HTTP_201_CREATED)
async def create_return(
    body: ReturnRequest,
    ctx: AuthContext = Depends(require_context("sales.refund")),
    db: AsyncSession = Depends(get_db),
):
    sret, replayed = await sales_returns.create_return(db, ctx.user, ctx.permissions, body)
    return await sales_returns.return_response(db, sret, replayed)


@router.get("/sales/returns")
async def list_returns(
    q: Optional[str] = Query(None, max_length=100),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: AuthContext = Depends(require_context("sales.view")),
    db: AsyncSession = Depends(get_db),
):
    from app.services.sales_common import day_bounds
    conds = [SalesReturn.pharmacy_id == ctx.pharmacy_id]
    if q:
        like = f"%{q.strip()}%"
        conds.append(SalesReturn.return_number.ilike(like) | SalesInvoice.invoice_number.ilike(like)
                     | SalesInvoice.customer_name.ilike(like))
    if date_from:
        conds.append(SalesReturn.created_at >= day_bounds(date_from)[0])
    if date_to:
        conds.append(SalesReturn.created_at < day_bounds(date_to)[1])
    base = select(SalesReturn).join(SalesInvoice, SalesInvoice.id == SalesReturn.invoice_id).where(*conds)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(base.order_by(SalesReturn.created_at.desc()).offset(offset).limit(limit))).scalars().all()
    return {"items": [await sales_returns.return_response(db, r) for r in rows], "total": total,
            "limit": limit, "offset": offset}


@router.get("/sales/returns/{return_id}", response_model=ReturnResponse)
async def get_return(
    return_id: int,
    ctx: AuthContext = Depends(require_context("sales.view")),
    db: AsyncSession = Depends(get_db),
):
    sret = (await db.execute(
        select(SalesReturn).where(SalesReturn.id == return_id, SalesReturn.pharmacy_id == ctx.pharmacy_id)
    )).scalar_one_or_none()
    if sret is None:
        raise HTTPException(status_code=404, detail="Return not found")
    return await sales_returns.return_response(db, sret)


# ── Payments ───────────────────────────────────────────────────────────────────

@router.post("/sales/payments/{payment_id}/verify", response_model=PaymentResponse)
async def verify_payment(
    payment_id: int,
    body: PaymentActionRequest,
    ctx: AuthContext = Depends(require_context("payments.receive")),
    db: AsyncSession = Depends(get_db),
):
    payment = await receivables.verify_payment(db, ctx.user, ctx.permissions, payment_id, body.reason)
    return await receivables.payment_response(db, payment)


@router.post("/sales/payments/{payment_id}/reverse", response_model=PaymentResponse)
async def reverse_payment(
    payment_id: int,
    body: PaymentActionRequest,
    ctx: AuthContext = Depends(require_context("customers.adjust")),
    db: AsyncSession = Depends(get_db),
):
    payment = await receivables.reverse_payment(db, ctx.user, ctx.permissions, payment_id, body.reason)
    return await receivables.payment_response(db, payment)


# ── Receivables ────────────────────────────────────────────────────────────────

@router.get("/receivables")
async def receivables_list(
    q: Optional[str] = Query(None, max_length=100),
    overdue_only: bool = False,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    ctx: AuthContext = Depends(require_context("customers.view")),
    db: AsyncSession = Depends(get_db),
):
    """Open credit invoices (balance due) with overdue days."""
    conds = sales.invoice_filters(ctx.pharmacy_id, q=q, status_filter="FINALIZED", overdue_only=overdue_only)
    conds.append(SalesInvoice.balance_due > 0)
    total = (await db.execute(select(func.count(SalesInvoice.id)).where(*conds))).scalar_one()
    invoices = (await db.execute(
        select(SalesInvoice).where(*conds)
        .order_by(SalesInvoice.due_date.is_(None), SalesInvoice.due_date, SalesInvoice.id)
        .offset(offset).limit(limit)
    )).scalars().all()
    totals = (await db.execute(
        select(func.coalesce(func.sum(SalesInvoice.balance_due), 0)).where(*conds)
    )).scalar_one()
    return decimal_json({"items": await sales.list_items(db, invoices), "total": total,
                         "total_outstanding": totals, "limit": limit, "offset": offset})


@router.get("/receivables/aging")
async def aging(
    ctx: AuthContext = Depends(require_context("customers.view")),
    db: AsyncSession = Depends(get_db),
):
    return decimal_json(await receivables.aging_report(db, ctx.pharmacy_id))


# ── Shifts / day-end ───────────────────────────────────────────────────────────

@router.post("/shifts", response_model=ShiftSummary, status_code=status.HTTP_201_CREATED)
async def open_shift(
    body: ShiftOpenRequest,
    ctx: AuthContext = Depends(require_context("shifts.operate")),
    db: AsyncSession = Depends(get_db),
):
    shift = await shifts.open_shift(db, ctx.user, ctx.permissions, body.opening_cash)
    return await shifts.shift_summary(db, shift)


@router.get("/shifts/current")
async def current_shift(
    ctx: AuthContext = Depends(require_context("pos.access")),
    db: AsyncSession = Depends(get_db),
):
    shift = await open_shift_for(db, ctx.pharmacy_id, ctx.user.id)
    return {"shift": await shifts.shift_summary(db, shift) if shift else None}


@router.get("/shifts")
async def list_shifts(
    day: Optional[date] = None,
    limit: int = Query(50, ge=1, le=200),
    ctx: AuthContext = Depends(require_context("shifts.operate")),
    db: AsyncSession = Depends(get_db),
):
    rows = await shifts.list_shifts(db, ctx.pharmacy_id, day, limit)
    if "shifts.review" not in ctx.permissions:
        rows = [s for s in rows if s.user_id == ctx.user.id]
    return [await shifts.shift_summary(db, s) for s in rows]


@router.get("/shifts/day-end")
async def day_end(
    day: Optional[date] = None,
    ctx: AuthContext = Depends(require_context("shifts.review")),
    db: AsyncSession = Depends(get_db),
):
    return decimal_json(await shifts.day_end(db, ctx.pharmacy_id, day or today_local()))


@router.get("/shifts/cash-movement-categories")
async def cash_movement_categories(ctx: AuthContext = Depends(require_context("shifts.operate"))):
    return {"categories": shifts.MOVEMENT_CATEGORIES, "legacy_only": shifts.LEGACY_ONLY,
            "reference_required": list(shifts.REFERENCE_REQUIRED)}


@router.get("/shifts/{shift_id}", response_model=ShiftSummary)
async def get_shift(
    shift_id: int,
    ctx: AuthContext = Depends(require_context("shifts.operate")),
    db: AsyncSession = Depends(get_db),
):
    shift = await shifts.get_shift(db, ctx.pharmacy_id, shift_id)
    if shift.user_id != ctx.user.id and "shifts.review" not in ctx.permissions:
        raise HTTPException(status_code=403, detail="You can only view your own shifts")
    return await shifts.shift_summary(db, shift)


@router.post("/shifts/{shift_id}/cash-movements", status_code=status.HTTP_201_CREATED)
async def cash_movement(
    shift_id: int,
    body: CashMovementRequest,
    ctx: AuthContext = Depends(require_context("shifts.review")),
    db: AsyncSession = Depends(get_db),
):
    move = await shifts.add_cash_movement(db, ctx.user, ctx.permissions, shift_id, body.movement_type,
                                          body.category, body.amount, body.reason, body.reference)
    return {"id": move.id, "movement_type": move.movement_type, "category": move.category, "amount": move.amount}


@router.post("/shifts/cash-movements/{movement_id}/classify")
async def classify_cash_movement(
    movement_id: int,
    body: CashMovementClassify,
    ctx: AuthContext = Depends(require_context("shifts.review")),
    db: AsyncSession = Depends(get_db),
):
    move = await shifts.classify_legacy_movement(db, ctx.user, ctx.permissions, movement_id, body.category, body.note)
    return {"id": move.id, "category": move.category}


@router.post("/shifts/{shift_id}/close", response_model=ShiftSummary)
async def close_shift(
    shift_id: int,
    body: ShiftCloseRequest,
    ctx: AuthContext = Depends(require_context("shifts.operate")),
    db: AsyncSession = Depends(get_db),
):
    shift = await shifts.close_shift(db, ctx.user, ctx.permissions, shift_id, body.counted_cash, body.variance_note)
    return await shifts.shift_summary(db, shift)


@router.post("/shifts/{shift_id}/review", response_model=ShiftSummary)
async def review_shift(
    shift_id: int,
    body: ShiftReviewRequest,
    ctx: AuthContext = Depends(require_context("shifts.review")),
    db: AsyncSession = Depends(get_db),
):
    shift = await shifts.review_shift(db, ctx.user, ctx.permissions, shift_id, body.review_note)
    return await shifts.shift_summary(db, shift)
