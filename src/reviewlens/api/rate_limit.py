"""In-memory rate limiting."""

from __future__ import annotations

import time

from cachetools import TTLCache
from fastapi import HTTPException, Request

from reviewlens.config import get_settings


class TokenBucket:
    """Token bucket rate limiter per client IP."""

    def __init__(self, rpm: int) -> None:
        self.capacity = float(max(1, rpm))
        self.tokens = float(max(1, rpm))
        self.interval = 60.0 / self.capacity
        self.last_update = time.monotonic()

    def consume(self) -> bool:
        now = time.monotonic()
        elapsed = now - self.last_update
        self.tokens = min(self.capacity, self.tokens + elapsed / self.interval)
        self.last_update = now

        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


# In-memory store: TTLCache evicts inactive IPs after 10 minutes, max 10,000 entries
_rate_limits: TTLCache[str, TokenBucket] = TTLCache(maxsize=10000, ttl=600)


def get_client_ip(request: Request) -> str:
    """Extract client IP from X-Forwarded-For (first hop) or direct client."""
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        # First hop for Cloud Run
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


def check_rate_limit(request: Request) -> None:
    """Dependency to check rate limits against settings.rate_limit_per_min."""
    settings = get_settings()
    rpm = settings.rate_limit_per_min
    ip = get_client_ip(request)

    bucket = _rate_limits.get(ip)
    if bucket is None:
        bucket = TokenBucket(rpm)
        _rate_limits[ip] = bucket

    if not bucket.consume():
        raise HTTPException(status_code=429, detail="rate_limited")
