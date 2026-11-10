"""POS lookup and live bill quotes."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.json import decimal_json
from app.core.deps import AuthContext, require_context
from app.schemas.sales import SaleQuoteRequest
from app.services import pos
from app.services.sales_common import get_customer

router = APIRouter(prefix="/pos", tags=["POS"])


@router.get("/search")
async def search(
    q: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(20, ge=1, le=50),
    ctx: AuthContext = Depends(require_context("pos.access")),
    db: AsyncSession = Depends(get_db),
):
    """Medicines matching name / brand / composition / manufacturer / barcode, with saleable batches (FEFO order)."""
    return decimal_json(await pos.search_saleable(db, ctx.pharmacy_id, q, limit))


@router.post("/quote")
async def quote(
    body: SaleQuoteRequest,
    ctx: AuthContext = Depends(require_context("pos.access")),
    db: AsyncSession = Depends(get_db),
):
    """
    Price the cart exactly as finalisation would (FEFO allocation, GST, rounding)
    without locking or saving anything. Stock is re-checked at finalisation.
    """
    customer = await get_customer(db, ctx.pharmacy_id, body.customer_id) if body.customer_id else None
    plan = await pos.plan_sale(db, ctx.pharmacy_id, body.items, customer, lock=False)
    return decimal_json(pos.plan_to_quote(plan))
