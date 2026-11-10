"""
Security utilities: Argon2id password hashing, session token management,
CSRF token generation and verification.
"""
import hashlib
import hmac
import os
import secrets
import time
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

from app.core.config import settings

# ── Argon2id password hasher ──────────────────────────────────────────────────
_hasher = PasswordHasher(
    time_cost=settings.ARGON2_TIME_COST,
    memory_cost=settings.ARGON2_MEMORY_COST,
    parallelism=settings.ARGON2_PARALLELISM,
    hash_len=32,
    salt_len=16,
)


def hash_password(plain_password: str) -> str:
    """
    Hash a password using Argon2id.
    Returns the encoded hash string (includes algorithm, params and salt).
    Never store the plain password.
    """
    return _hasher.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plain password against its Argon2id hash.
    Returns True on match. Returns False on any mismatch.
    Raises nothing — all exceptions are caught and return False.
    """
    try:
        return _hasher.verify(hashed_password, plain_password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(hashed_password: str) -> bool:
    """
    Returns True if the password hash was created with outdated parameters
    and should be rehashed on the next successful login.
    """
    return _hasher.check_needs_rehash(hashed_password)


# ── Session token ─────────────────────────────────────────────────────────────

def generate_session_token() -> str:
    """Generate a cryptographically secure random session token."""
    return secrets.token_urlsafe(48)


def generate_csrf_token() -> str:
    """
    Generate a CSRF token bound to the session.
    Uses HMAC-SHA256 with the application secret key.
    """
    nonce = secrets.token_hex(16)
    timestamp = str(int(time.time()))
    message = f"{nonce}:{timestamp}"
    signature = hmac.new(
        settings.SECRET_KEY.encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{message}:{signature}"


def verify_csrf_token(token: str, max_age_seconds: int = 3600) -> bool:
    """
    Verify a CSRF token.
    Checks HMAC signature and optionally rejects tokens older than max_age_seconds.
    """
    try:
        parts = token.split(":")
        if len(parts) != 3:
            return False
        nonce, timestamp_str, provided_sig = parts
        timestamp = int(timestamp_str)
        if time.time() - timestamp > max_age_seconds:
            return False
        message = f"{nonce}:{timestamp_str}"
        expected_sig = hmac.new(
            settings.SECRET_KEY.encode(),
            message.encode(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected_sig, provided_sig)
    except Exception:
        return False


def csrf_token_for_session(session_token: str) -> str:
    """
    Derive the CSRF token bound to a session (HMAC of the session token).
    The browser receives it in a readable cookie and echoes it in the X-CSRF-Token
    header; a cross-site page can trigger requests that carry the HTTP-only session
    cookie but cannot read this value, so it cannot forge the header.
    """
    return hmac.new(
        settings.SECRET_KEY.encode(),
        f"csrf:{session_token}".encode(),
        hashlib.sha256,
    ).hexdigest()


def verify_session_csrf_token(session_token: str, provided: Optional[str]) -> bool:
    """Timing-safe check that the provided header matches the session's CSRF token."""
    if not provided:
        return False
    return hmac.compare_digest(csrf_token_for_session(session_token), provided)


# ── Constant-time string comparison ───────────────────────────────────────────

def safe_str_compare(a: str, b: str) -> bool:
    """Timing-safe string comparison to prevent timing attacks."""
    return hmac.compare_digest(a.encode(), b.encode())
