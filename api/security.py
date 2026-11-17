"""API-key protection and a small in-process rate limiter."""
from __future__ import annotations

import hmac
import os
import time
from collections import defaultdict, deque

from fastapi import Header, HTTPException


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Enforce X-API-Key when API_KEY is configured (constant-time comparison)."""
    expected = os.getenv("API_KEY", "").strip()
    if expected and not (x_api_key and hmac.compare_digest(x_api_key, expected)):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


class RateLimiter:
    """Sliding-window limiter keyed by client IP. Replace with a gateway/Redis limiter for multi-replica setups."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        if self.per_minute <= 0:
            return True
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_minute:
            return False
        q.append(now)
        if len(self.hits) > 20_000:  # bound memory
            self.hits.clear()
        return True


def require_admin_key(x_api_key: str | None = Header(default=None)) -> None:
    """Admin endpoints are never open: an API_KEY must be configured AND supplied."""
    expected = os.getenv("API_KEY", "").strip()
    if not expected:
        raise HTTPException(status_code=403, detail="Admin endpoints are disabled until API_KEY is configured")
    if not (x_api_key and hmac.compare_digest(x_api_key, expected)):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
