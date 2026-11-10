"""Purchases → Import supplier bill. Upload/review: purchases.create. View the original: purchases.view.
Confirm: purchases.create, plus purchases.post / purchases.invoice_finalize for the optional posting steps."""
from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, File, Query, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import AuthContext, require_context
from app.services import bill_import

router = APIRouter(prefix="/purchases/bill-imports", tags=["Supplier bill import"])


class ConfirmBody(BaseModel):
    review: Dict[str, Any]
    post_grn: bool = False
    finalize: bool = False
    acknowledge_warnings: bool = False


@router.post("", status_code=201)
async def upload_bill(file: UploadFile = File(...), ctx: AuthContext = Depends(require_context("purchases.create")),
                      db: AsyncSession = Depends(get_db)):
    data = await file.read(bill_import.MAX_BYTES + 1)
    rec = await bill_import.upload(db, ctx.user, file.filename or "bill", data)
    return await bill_import.detail(db, ctx.pharmacy_id, rec.id)


@router.get("")
async def list_bills(limit: int = Query(50, ge=1, le=200), ctx: AuthContext = Depends(require_context("purchases.view")),
                     db: AsyncSession = Depends(get_db)):
    return await bill_import.list_imports(db, ctx.pharmacy_id, limit)


@router.get("/{import_id}")
async def get_bill(import_id: int, ctx: AuthContext = Depends(require_context("purchases.view")), db: AsyncSession = Depends(get_db)):
    return await bill_import.detail(db, ctx.pharmacy_id, import_id)


@router.get("/{import_id}/file")
async def bill_file(import_id: int, ctx: AuthContext = Depends(require_context("purchases.view")), db: AsyncSession = Depends(get_db)):
    rec = await bill_import.get(db, ctx.pharmacy_id, import_id)
    ext = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[rec.content_type]
    return Response(content=rec.data, media_type=rec.content_type, headers={
        "Content-Disposition": f'inline; filename="supplier-bill-{rec.id}.{ext}"',
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
        "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; plugin-types application/pdf",
        "X-Frame-Options": "SAMEORIGIN",
    })


@router.put("/{import_id}")
async def save_bill_draft(import_id: int, review: Dict[str, Any] = Body(...),
                          ctx: AuthContext = Depends(require_context("purchases.create")), db: AsyncSession = Depends(get_db)):
    return await bill_import.save_draft(db, ctx.user, import_id, review)


@router.post("/{import_id}/check")
async def check_bill(import_id: int, review: Dict[str, Any] = Body(...),
                     ctx: AuthContext = Depends(require_context("purchases.create")), db: AsyncSession = Depends(get_db)):
    rec = await bill_import.get(db, ctx.pharmacy_id, import_id)
    return await bill_import.check(db, rec, review)


@router.post("/{import_id}/confirm")
async def confirm_bill(import_id: int, body: ConfirmBody, ctx: AuthContext = Depends(require_context("purchases.create")),
                       db: AsyncSession = Depends(get_db)):
    return await bill_import.confirm(db, ctx.user, ctx.permissions, import_id, body.review, body.post_grn,
                                     body.finalize, body.acknowledge_warnings)


@router.post("/{import_id}/discard", status_code=204)
async def discard_bill(import_id: int, ctx: AuthContext = Depends(require_context("purchases.create")),
                       db: AsyncSession = Depends(get_db)):
    await bill_import.discard(db, ctx.user, import_id)
    return Response(status_code=204)
