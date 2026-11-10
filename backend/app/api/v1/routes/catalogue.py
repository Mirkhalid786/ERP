"""Medicine catalogue import (permission: medicines.manage). Preview first, then commit; the commit is all-or-nothing."""
import json
import os
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import require_permission
from app.models.auth import User
from app.services import catalogue_import as ci

router = APIRouter(prefix="/medicines/import", tags=["Medicine catalogue import"])
STARTER = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))),
                       "data", "catalogue", "starter_generic_catalogue.csv")


async def _source(file: Optional[UploadFile], source: str) -> tuple:
    if source == "starter":
        with open(STARTER, "rb") as f:
            return "starter_generic_catalogue.csv", f.read()
    if file is None:
        raise HTTPException(status_code=422, detail="Choose a CSV or Excel file")
    return file.filename or "upload.csv", await file.read(ci.MAX_BYTES + 1)


def _mapping(text: Optional[str]) -> Optional[dict]:
    if not text:
        return None
    try:
        value = json.loads(text)
    except ValueError:
        raise HTTPException(status_code=422, detail="mapping must be JSON")
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise HTTPException(status_code=422, detail="mapping must map field names to column names")
    return value


def _dup(on_duplicate: str) -> str:
    if on_duplicate not in ("skip", "update"):
        raise HTTPException(status_code=422, detail="on_duplicate must be skip or update")
    return on_duplicate


@router.get("/template", response_class=PlainTextResponse)
async def template(user: User = Depends(require_permission("medicines.manage"))):
    head = ",".join(ci.FIELDS)
    example = "Paracetamol 650 mg Tablet,Paracetamol,650 mg,Tablet,,,Analgesics & Antipyretics,3004,5,NONE,no,Tablet,Strip,15,,0"
    return PlainTextResponse(f"{head}\n{example}\n", headers={"Content-Disposition": 'attachment; filename="medicine_catalogue_template.csv"'})


@router.get("/starter")
async def starter_file(user: User = Depends(require_permission("medicines.manage"))):
    return FileResponse(STARTER, media_type="text/csv", filename="starter_generic_catalogue.csv")


@router.post("/preview")
async def preview(file: Optional[UploadFile] = File(None), source: str = Form("upload"), mapping: Optional[str] = Form(None),
                  on_duplicate: str = Form("skip"), user: User = Depends(require_permission("medicines.manage")),
                  db: AsyncSession = Depends(get_db)):
    name, data = await _source(file, source)
    return await ci.preview(db, user, name, data, _mapping(mapping), _dup(on_duplicate))


@router.post("/commit")
async def commit(file: Optional[UploadFile] = File(None), source: str = Form("upload"), mapping: Optional[str] = Form(None),
                 on_duplicate: str = Form("skip"), user: User = Depends(require_permission("medicines.manage")),
                 db: AsyncSession = Depends(get_db)):
    name, data = await _source(file, source)
    return await ci.commit(db, user, name, data, _mapping(mapping), _dup(on_duplicate))


# ── Indian Branded Medicine Catalogue Import ─────────────────────────────────

@router.get("/indian/preview")
async def indian_preview(user: User = Depends(require_permission("medicines.manage"))):
    """Dry-run preview of the Indian Medicine Dataset with record counts and field coverage."""
    from app.services import indian_medicine_import as imi
    return imi.preview_indian_catalogue()


@router.post("/indian/commit")
async def indian_commit(
    batch_size: int = Form(2000),
    limit: Optional[int] = Form(None),
    on_duplicate: str = Form("skip"),
    user: User = Depends(require_permission("medicines.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Import the Indian Medicine Dataset in bounded transactional batches."""
    from app.services import indian_medicine_import as imi
    return await imi.import_indian_catalogue(
        db=db,
        pharmacy_id=user.pharmacy_id,
        user_id=user.id,
        batch_size=batch_size,
        limit=limit,
        on_duplicate=on_duplicate,
    )

