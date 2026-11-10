"""
Pharmacy ERP — FastAPI Application Entry Point
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.database import engine, Base
from app.core.security import verify_session_csrf_token
from app.api.v1.router import api_router

# State-changing requests that need no CSRF token: they run before a session exists.
CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_EXEMPT_PATHS = {"/api/v1/auth/login", "/api/v1/setup"}


async def csrf_protect(request: Request, call_next):
    """
    Reject state-changing requests that carry a session cookie but not the
    matching X-CSRF-Token header. Requests without a session are left to the
    normal auth dependencies (which return 401).
    """
    if request.method not in CSRF_SAFE_METHODS and request.url.path not in CSRF_EXEMPT_PATHS:
        session_token = request.cookies.get(settings.SESSION_COOKIE_NAME)
        if session_token and not verify_session_csrf_token(
            session_token, request.headers.get(settings.CSRF_HEADER_NAME)
        ):
            return JSONResponse(
                status_code=403,
                content={"detail": "CSRF token missing or invalid. Reload the page and try again."},
            )
    return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle."""
    # Startup: verify database connectivity
    from sqlalchemy import text
    from app.core.database import AsyncSessionLocal
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:
        raise RuntimeError(f"Database connectivity check failed on startup: {exc}") from exc

    # The migration flag is for one-off operator commands only. If it is set in the server's
    # environment (e.g. left in .env) the start command would migrate unattended: refuse.
    import os
    if os.environ.get("ALLOW_DB_MIGRATIONS"):
        raise RuntimeError("Refusing to start: ALLOW_DB_MIGRATIONS is set in the server environment. "
                           "Remove it from .env / compose; pass it only to the one-off migration command")

    # Production must not sign sessions and CSRF tokens with the published placeholder key
    problem = production_config_problem()
    if problem:
        raise RuntimeError(f"Refusing to start in production: {problem}")

    # Refuse to serve against a schema this code was not written for (read-only check)
    from app.core.schema_guard import check_schema
    state = await check_schema(engine)
    if not state["ok"]:
        import logging
        logging.getLogger("app.startup").critical("Refusing to start: %s", state["reason"])
        raise RuntimeError(f"Refusing to start: {state['reason']}")
    
    yield
    
    # Shutdown: dispose connection pool
    await engine.dispose()


INSECURE_SECRET_MARKERS = ("INSECURE_DEFAULT", "CHANGE_THIS", "CHANGE_ME")


def production_config_problem() -> str | None:
    """A reason the production configuration is unsafe, or None. Development is not checked."""
    if settings.APP_ENV != "production":
        return None
    key = settings.SECRET_KEY or ""
    if len(key) < 32 or any(m in key.upper() for m in INSECURE_SECRET_MARKERS):
        return "SECRET_KEY is the placeholder or shorter than 32 characters. Generate one with: "                "python -c \"import secrets; print(secrets.token_hex(32))\""
    return None


def trusted_hosts() -> list:
    """Configured hosts plus "backend": the frontend container reaches the API at http://backend:8000 and the
    Next.js proxy sends that as the Host header. The name only resolves inside the Docker network."""
    return list(dict.fromkeys([*settings.ALLOWED_HOSTS, "backend"]))


def create_application() -> FastAPI:
    application = FastAPI(
        title="Pharmacy ERP API",
        description=(
            "Local-first Indian retail pharmacy ERP. "
            "Batch-wise inventory, GST billing, purchasing and reporting."
        ),
        version="1.0.0",
        openapi_url="/api/v1/openapi.json" if settings.APP_ENV != "production" else None,
        docs_url="/api/v1/docs" if settings.APP_ENV != "production" else None,
        redoc_url="/api/v1/redoc" if settings.APP_ENV != "production" else None,
        lifespan=lifespan,
    )

    # ── CSRF ─────────────────────────────────────────────────────────────────
    # Registered before CORS so CORS stays the outermost middleware.
    application.middleware("http")(csrf_protect)

    # ── CORS ─────────────────────────────────────────────────────────────────
    # Only allow configured origins. In LAN deployments this will include the
    # local server address and any allowed client machine origins.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
        expose_headers=["X-Total-Count"],
    )

    # ── Trusted host ─────────────────────────────────────────────────────────
    if settings.APP_ENV == "production":
        application.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=trusted_hosts(),
        )

    # ── Routes ───────────────────────────────────────────────────────────────
    application.include_router(api_router, prefix="/api/v1")

    return application


app = create_application()
