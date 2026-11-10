"""Health and readiness check endpoints."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.core.database import get_db

router = APIRouter(tags=["Health"])


@router.get("/health/live")
async def liveness():
    """
    Liveness probe — confirms the application process is running.
    Does not check database. Used by Docker / process managers.
    """
    return {"status": "ok", "service": "pharmacy-erp-api"}


@router.get("/health/ready")
async def readiness(db: AsyncSession = Depends(get_db)):
    """
    Readiness probe — confirms the application can serve requests.
    Checks database connectivity. Used by load balancers and health monitors.
    Returns 200 when ready, 503 when the database is unavailable.
    """
    try:
        await db.execute(text("SELECT 1"))
        return {"status": "ready", "database": "connected"}
    except Exception as exc:
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database not available: {str(exc)}",
        )
