"""Customers, ledgers, statements, collections and adjustments."""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.json import decimal_json
from app.core.deps import AuthContext, require_context
from app.schemas.sales import (
    AdjustmentRequest, ApplyCreditRequest, CollectionRequest, CustomerCreate, CustomerResponse,
    CustomerStatement, CustomerUpdate, PaymentResponse,
)
from app.services import receivables, sales
from app.services.sales_common import get_customer as load_customer

router = APIRouter(prefix="/customers", tags=["Customers"])


@router.get("")
async def list_customers(
    q: Optional[str] = Query(None, max_length=100),
    active_only: bool = False,
    with_balance_only: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: AuthContext = Depends(require_context("customers.view")),
    db: AsyncSession = Depends(get_db),
):
    items, total = await receivables.list_customers(db, ctx.pharmacy_id, q, active_only, with_balance_only, limit, offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.post("", response_model=CustomerResponse, status_code=status.HTTP_201_CREATED)
async def create_customer(
    body: CustomerCreate,
    ctx: AuthContext = Depends(require_context("customers.manage")),
    db: AsyncSession = Depends(get_db),
):
    if body.credit_limit > 0 and "sales.credit_override" not in ctx.permissions:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Setting a credit limit needs 'sales.credit_override'")
    customer = await receivables.create_customer(db, ctx.user, body)
    return await receivables.customer_response(db, customer)


@router.get("/{customer_id}", response_model=CustomerResponse)
async def get_customer(
    customer_id: int,
    ctx: AuthContext = Depends(require_context("customers.view")),
    db: AsyncSession = Depends(get_db),
):
    customer = await load_customer(db, ctx.pharmacy_id, customer_id)
    return await receivables.customer_response(db, customer)


@router.put("/{customer_id}", response_model=CustomerResponse)
async def update_customer(
    customer_id: int,
    body: CustomerUpdate,
    ctx: AuthContext = Depends(require_context("customers.manage")),
    db: AsyncSession = Depends(get_db),
):
    customer = await receivables.update_customer(db, ctx.user, ctx.permissions, customer_id, body)
    return await receivables.customer_response(db, customer)


@router.get("/{customer_id}/statement", response_model=CustomerStatement)
async def customer_statement(
    customer_id: int,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    ctx: AuthContext = Depends(require_context("customers.view")),
    db: AsyncSession = Depends(get_db),
):
    """Ledger with running balance, plus open invoices and unapplied credit."""
    customer = await load_customer(db, ctx.pharmacy_id, customer_id)
    opening, closing, debits, credits, entries = await receivables.statement(
        db, ctx.pharmacy_id, customer_id, date_from, date_to)
    open_items, _ = await sales.list_invoices(
        db, ctx.pharmacy_id, 200, 0, customer_id=customer_id, status_filter="FINALIZED")
    return CustomerStatement(
        customer=await receivables.customer_response(db, customer), date_from=date_from, date_to=date_to,
        opening_balance=opening, closing_balance=closing, total_debits=debits, total_credits=credits,
        unapplied_credit=await receivables.unapplied_credit(db, ctx.pharmacy_id, customer_id),
        entries=entries, open_invoices=[i for i in open_items if i.balance_due > 0],
    )


@router.get("/{customer_id}/reconcile")
async def reconcile(
    customer_id: int,
    ctx: AuthContext = Depends(require_context("customers.view")),
    db: AsyncSession = Depends(get_db),
):
    """Check that the ledger balance equals open invoices minus unapplied credit."""
    await load_customer(db, ctx.pharmacy_id, customer_id)
    return decimal_json(await receivables.reconcile_customer(db, ctx.pharmacy_id, customer_id))


@router.post("/{customer_id}/payments", response_model=PaymentResponse, status_code=status.HTTP_201_CREATED)
async def receive_payment(
    customer_id: int,
    body: CollectionRequest,
    ctx: AuthContext = Depends(require_context("payments.receive")),
    db: AsyncSession = Depends(get_db),
):
    payment, replayed = await receivables.record_collection(db, ctx.user, ctx.permissions, customer_id, body)
    return await receivables.payment_response(db, payment, replayed)


@router.post("/{customer_id}/apply-credit")
async def apply_credit(
    customer_id: int,
    body: ApplyCreditRequest,
    ctx: AuthContext = Depends(require_context("payments.receive")),
    db: AsyncSession = Depends(get_db),
):
    return decimal_json({"applied": await receivables.apply_customer_credit(
        db, ctx.user, ctx.permissions, customer_id, body.allocations)})


@router.post("/{customer_id}/adjustments", status_code=status.HTTP_201_CREATED)
async def adjustment(
    customer_id: int,
    body: AdjustmentRequest,
    ctx: AuthContext = Depends(require_context("customers.adjust")),
    db: AsyncSession = Depends(get_db),
):
    entry = await receivables.post_adjustment(db, ctx.user, ctx.permissions, customer_id, body)
    return decimal_json({"ledger_entry_id": entry.id, "entry_type": entry.entry_type, "amount": entry.credit},
                        status_code=201)
