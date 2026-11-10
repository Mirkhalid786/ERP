"""
Application configuration — loaded from environment variables.
All settings have documented defaults suitable for local development.
"""
from typing import List, Union
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Environment ───────────────────────────────────────────────────────────
    APP_ENV: str = "development"

    # ── Database ──────────────────────────────────────────────────────────────
    DATABASE_URL: str = "mysql+aiomysql://pharmacy_app:password@localhost:3306/pharmacy_erp"
    DATABASE_URL_SYNC: str = "mysql+pymysql://pharmacy_app:password@localhost:3306/pharmacy_erp"

    # ── Security ──────────────────────────────────────────────────────────────
    SECRET_KEY: str = "INSECURE_DEFAULT_CHANGE_IN_PRODUCTION"

    # Session cookie configuration
    SESSION_COOKIE_NAME: str = "pharmacy_session"
    SESSION_COOKIE_SECURE: bool = False     # True in production (HTTPS)
    SESSION_COOKIE_HTTPONLY: bool = True
    SESSION_COOKIE_SAMESITE: str = "lax"
    SESSION_MAX_AGE_SECONDS: int = 28800   # 8 hours

    # CSRF cookie (readable by the frontend, echoed back in the X-CSRF-Token header)
    CSRF_COOKIE_NAME: str = "pharmacy_csrf"
    CSRF_HEADER_NAME: str = "X-CSRF-Token"

    # Login brute-force protection (in-memory; the backend runs a single worker)
    LOGIN_MAX_FAILURES_PER_USER: int = 5    # per IP + username
    LOGIN_MAX_FAILURES_PER_IP: int = 20     # per IP across all usernames
    LOGIN_FAILURE_WINDOW_SECONDS: int = 900  # 15 minutes

    # Argon2id hashing parameters
    ARGON2_TIME_COST: int = 3
    ARGON2_MEMORY_COST: int = 65536        # 64 MB
    ARGON2_PARALLELISM: int = 4

    # ── CORS ──────────────────────────────────────────────────────────────────
    CORS_ORIGINS: Union[List[str], str] = ["http://localhost:3000"]

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v):
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("[") and v.endswith("]"):
                import json
                try:
                    return json.loads(v)
                except Exception:
                    pass
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    # ── Trusted hosts (production only) ───────────────────────────────────────
    ALLOWED_HOSTS: Union[List[str], str] = ["localhost", "127.0.0.1"]

    @field_validator("ALLOWED_HOSTS", mode="before")
    @classmethod
    def parse_allowed_hosts(cls, v):
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("[") and v.endswith("]"):
                import json
                try:
                    return json.loads(v)
                except Exception:
                    pass
            return [h.strip() for h in v.split(",") if h.strip()]
        return v

    # ── Business time zone ────────────────────────────────────────────────────
    # Fixed UTC offset of the pharmacy (India: +5:30 = 330 minutes, no DST).
    # Business dates (invoice day, due dates, day-end) are computed in this zone;
    # timestamps are stored in UTC.
    APP_UTC_OFFSET_MINUTES: int = 330

    # ── WhatsApp Business Platform (optional; leave blank for manual sharing) ─
    WHATSAPP_PROVIDER: str = "manual"            # "manual" or "cloud_api"
    WHATSAPP_API_VERSION: str = "v21.0"
    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_ACCESS_TOKEN: str = ""
    WHATSAPP_APP_SECRET: str = ""                # Verifies X-Hub-Signature-256 on webhooks
    WHATSAPP_WEBHOOK_VERIFY_TOKEN: str = ""
    WHATSAPP_TEMPLATE_LANGUAGE: str = "en"
    WHATSAPP_TEMPLATE_INVOICE: str = ""          # Approved template names (blank = plain text,
    WHATSAPP_TEMPLATE_REMINDER_UPCOMING: str = ""  # which only works inside the 24 h service window)
    WHATSAPP_TEMPLATE_REMINDER_DUE_TODAY: str = ""
    WHATSAPP_TEMPLATE_REMINDER_OVERDUE: str = ""
    WHATSAPP_SEND_INVOICE_PDF: bool = False      # Attach the PDF invoice (Cloud API only)
    WHATSAPP_GRAPH_BASE_URL: str = "https://graph.facebook.com"

    # ── Background worker ─────────────────────────────────────────────────────
    WORKER_INTERVAL_SECONDS: int = 60
    WORKER_REMINDER_SCAN_MINUTES: int = 60

    # ── Backup ────────────────────────────────────────────────────────────────
    BACKUP_DIR: str = "/app/backups"
    BACKUP_RETENTION_DAYS: int = 30
    # Optional scratch schema used to test-restore backups; must end in "_restore_verify" and the
    # app user needs privileges on it. Empty = verification checks the file only.
    BACKUP_VERIFY_DATABASE: str = ""

    @property
    def is_development(self) -> bool:
        return self.APP_ENV == "development"

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"


settings = Settings()
