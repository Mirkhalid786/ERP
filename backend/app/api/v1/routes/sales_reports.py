"""Sales and receivables reports (JSON, or CSV with ?format=csv)."""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.json import decimal_json
from app.core.deps import AuthContext, require_context
from app.services import sales_reports as reports
from app.services.sales_common import today_local

router = APIRouter(prefix="/reports/sales", tags=["Reports"])

CSV_ROWS = {
    "daily": lambda data: data,
    "items": lambda data: data,
    "batches": lambda data: data,
    "cashiers": lambda data: data,
    "discounts": lambda data: data,
    "register": lambda data: data,
    "payments": lambda data: [{"method": k, **v} for k, v in data["by_method"].items()],
    "credit": lambda data: data["credit_sales"] + data["collections"],
    "returns": lambda data: [{"type": "RETURN", **r} for r in data["returns"]] + [{"type": "VOID", **v} for v in data["voids"]],
    "tax": lambda data: [{"section": "SALES", **r} for r in data["sales"]] + [{"section": "RETURNS", **r} for r in data["returns"]],
    "aging": lambda data: data["customers"],
    "summary": lambda data: [{k: v for k, v in data.items() if not isinstance(v, dict)}],
}


def _dates(date_from: Optional[date], date_to: Optional[date]):
    date_to = date_to or today_local()
    date_from = date_from or date_to.replace(day=1)
    if date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must be on or before date_to")
    if (date_to - date_from).days > 366:
        raise HTTPException(status_code=422, detail="Reports cover at most one year at a time")
    return date_from, date_to


@router.get("/dashboard")
async def dashboard(
    ctx: AuthContext = Depends(require_context("reports.view")),
    db: AsyncSession = Depends(get_db),
):
    return decimal_json(await reports.dashboard(db, ctx.pharmacy_id))


@router.get("/{report}")
async def report(
    report: str,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    format: str = Query("json", pattern="^(json|csv)$"),
    ctx: AuthContext = Depends(require_context("reports.view")),
    db: AsyncSession = Depends(get_db),
):
    d1, d2 = _dates(date_from, date_to)
    pid = ctx.pharmacy_id
    handlers = {
        "summary": lambda: reports.summary(db, pid, d1, d2),
        "daily": lambda: reports.daily(db, pid, d1, d2),
        "items": lambda: reports.items(db, pid, d1, d2),
        "batches": lambda: reports.items(db, pid, d1, d2, by_batch=True),
        "cashiers": lambda: reports.cashiers(db, pid, d1, d2),
        "payments": lambda: reports.payment_totals(db, pid, d1, d2),
        "credit": lambda: reports.credit_and_collections(db, pid, d1, d2),
        "returns": lambda: reports.returns_and_voids(db, pid, d1, d2),
        "discounts": lambda: reports.discounts(db, pid, d1, d2),
        "tax": lambda: reports.tax_summary(db, pid, d1, d2),
        "register": lambda: reports.register(db, pid, d1, d2),
        "aging": lambda: reports.aging(db, pid),
    }
    if report not in handlers:
        raise HTTPException(status_code=404, detail=f"Unknown report '{report}'")
    data = await handlers[report]()
    if format == "csv":
        if "reports.export" not in ctx.permissions:
            raise HTTPException(status_code=403, detail="Permission 'reports.export' is required to export reports")
        return PlainTextResponse(
            reports.to_csv(CSV_ROWS[report](data)), media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="sales-{report}-{d1}-to-{d2}.csv"'},
        )
    return decimal_json({"report": report, "date_from": d1, "date_to": d2, "data": data})
