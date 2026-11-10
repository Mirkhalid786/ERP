"""
Login brute-force protection.

Failed logins are counted per (client IP, username) and per client IP within a
sliding window. Once a limit is reached, further attempts get HTTP 429 until the
oldest failure leaves the window. A successful login clears that user's counter.

State is kept in process memory. That is sufficient for the single-worker LAN
deployment (see Dockerfile); multiple workers would each keep their own counts.
"""
import math
import time
from collections import deque
from typing import Deque, Dict, Optional

from fastapi import HTTPException, status

from app.core.config import settings


class LoginRateLimiter:
    def __init__(self) -> None:
        self._failures: Dict[str, Deque[float]] = {}

    @staticmethod
    def _user_key(ip: str, username: str) -> str:
        return f"user:{ip}:{username.strip().lower()}"

    @staticmethod
    def _ip_key(ip: str) -> str:
        return f"ip:{ip}"

    def _recent(self, key: str, now: float) -> Deque[float]:
        window_start = now - settings.LOGIN_FAILURE_WINDOW_SECONDS
        attempts = self._failures.get(key)
        if attempts is None:
            return deque()
        while attempts and attempts[0] <= window_start:
            attempts.popleft()
        if not attempts:
            del self._failures[key]
        return attempts

    def _retry_after(self, attempts: Deque[float], now: float) -> int:
        return max(1, math.ceil(attempts[0] + settings.LOGIN_FAILURE_WINDOW_SECONDS - now))

    def check(self, ip: Optional[str], username: str) -> None:
        """Raise 429 if this IP or IP+username has too many recent failures."""
        ip = ip or "unknown"
        now = time.monotonic()
        for key, limit in (
            (self._user_key(ip, username), settings.LOGIN_MAX_FAILURES_PER_USER),
            (self._ip_key(ip), settings.LOGIN_MAX_FAILURES_PER_IP),
        ):
            attempts = self._recent(key, now)
            if len(attempts) >= limit:
                retry_after = self._retry_after(attempts, now)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Too many failed login attempts. Try again in {math.ceil(retry_after / 60)} minute(s).",
                    headers={"Retry-After": str(retry_after)},
                )

    def record_failure(self, ip: Optional[str], username: str) -> None:
        ip = ip or "unknown"
        now = time.monotonic()
        for key in (self._user_key(ip, username), self._ip_key(ip)):
            self._recent(key, now)
            self._failures.setdefault(key, deque()).append(now)

    def record_success(self, ip: Optional[str], username: str) -> None:
        self._failures.pop(self._user_key(ip or "unknown", username), None)

    def reset(self) -> None:
        self._failures.clear()


login_rate_limiter = LoginRateLimiter()
