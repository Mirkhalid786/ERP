"""Branding & appearance API.

Public (no sign-in): GET /branding/public and GET /branding/assets/{kind} — only the minimal identity the login
page needs (name, tagline, welcome text, default theme, logo images). No address, contact or tax data.
Global branding: settings.manage. Own appearance: any signed-in user. Resetting another user's appearance: users.manage.
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, File, Request, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_permission
from app.models.auth import User
from app.services import branding

router = APIRouter(tags=["Branding"])


@router.get("/branding/public")
async def public_branding(db: AsyncSession = Depends(get_db)):
    return await branding.public_branding(db, await branding.default_pharmacy_id(db))


@router.get("/branding/assets/{kind}")
async def brand_asset(kind: str, request: Request, v: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    asset = await branding.load_asset(db, await branding.default_pharmacy_id(db), kind)
    if asset is None:
        return Response(status_code=404, headers={"Cache-Control": "no-store"})
    etag = f'"{asset.sha256[:32]}"'
    headers = {
        "ETag": etag,
        # A versioned URL never changes content; an unversioned one is revalidated every time
        "Cache-Control": "public, max-age=31536000, immutable" if v == asset.sha256[:12] else "no-cache",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "Content-Disposition": f'inline; filename="{kind}"',
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=asset.data, media_type=asset.content_type, headers=headers)


@router.get("/branding")
async def get_branding(user: User = Depends(require_permission("settings.manage")), db: AsyncSession = Depends(get_db)):
    return await branding.admin_branding(db, user.pharmacy_id)


@router.put("/branding")
async def put_branding(body: Dict[str, Any] = Body(...), user: User = Depends(require_permission("settings.manage")),
                       db: AsyncSession = Depends(get_db)):
    out = await branding.update_branding(db, user, body)
    await db.commit()
    branding.invalidate_cache()
    return out


@router.post("/branding/reset/{section}")
async def reset_branding(section: str, user: User = Depends(require_permission("settings.manage")), db: AsyncSession = Depends(get_db)):
    out = await branding.reset_branding(db, user, section)
    await db.commit()
    branding.invalidate_cache()
    return out


@router.post("/branding/assets/{kind}", status_code=201)
async def upload_asset(kind: str, file: UploadFile = File(...), user: User = Depends(require_permission("settings.manage")),
                       db: AsyncSession = Depends(get_db)):
    limit = branding.ASSET_KINDS.get(kind, 0)
    data = await file.read(limit + 1)             # Never read more than one byte past the limit
    out = await branding.save_asset(db, user, kind, data)
    await db.commit()
    branding.invalidate_cache()
    return out


@router.delete("/branding/assets/{kind}", status_code=204)
async def remove_asset(kind: str, user: User = Depends(require_permission("settings.manage")), db: AsyncSession = Depends(get_db)):
    await branding.delete_asset(db, user, kind)
    await db.commit()
    branding.invalidate_cache()
    return Response(status_code=204)


@router.get("/me/appearance")
async def my_appearance(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await branding.my_appearance(db, user)


@router.patch("/me/appearance")
async def patch_my_appearance(body: Dict[str, Any] = Body(...), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await branding.update_my_appearance(db, user, body, replace=False)


@router.put("/me/appearance")
async def put_my_appearance(body: Dict[str, Any] = Body(...), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await branding.update_my_appearance(db, user, body, replace=True)


@router.delete("/me/appearance")
async def reset_my_appearance(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await branding.reset_user_appearance(db, user, user.id)
    return await branding.my_appearance(db, user)


@router.delete("/users/{user_id}/appearance", status_code=204)
async def reset_user_appearance(user_id: int, user: User = Depends(require_permission("users.manage")), db: AsyncSession = Depends(get_db)):
    await branding.reset_user_appearance(db, user, user_id)
    return Response(status_code=204)
