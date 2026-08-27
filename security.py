"""
Shared-secret guard and IP rate limiting for the write endpoints.

The app is deployed publicly (render.yaml), so every endpoint that creates,
changes or deletes a lead needs a token. Deliberately not OAuth and not a user
system - this is a demo, and a shared secret is the right size for it.

The public form on / posts to /demo-lead instead, which takes no token but is
rate limited per IP and carries a honeypot field.

If LEAD_TRIAGE_TOKEN is unset, a token is generated at startup and logged, so a
fresh deployment works without configuration while staying closed to anyone who
cannot read the logs.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import time
from collections import defaultdict, deque

from fastapi import Header, HTTPException, Request, status

# ─── Token guard ─────────────────────────────────────────────────

# Generated once per process when LEAD_TRIAGE_TOKEN is not set. That keeps a
# fresh deployment working out of the box without leaving the write endpoints
# open: the value is printed to the log at startup and nowhere else, so only
# someone who can read the logs can use it. Set the variable to pin it.
_generated_token: str | None = None


def effective_token() -> tuple[str, bool]:
    """Return the active token and whether it was generated rather than configured."""
    global _generated_token

    configured = os.getenv("LEAD_TRIAGE_TOKEN")
    if configured:
        return configured, False

    if _generated_token is None:
        _generated_token = secrets.token_urlsafe(32)
    return _generated_token, True


def reset_generated_token() -> None:
    """Tests only: forget the generated token so the next call makes a new one."""
    global _generated_token
    _generated_token = None


def require_token(x_api_token: str | None = Header(default=None)) -> None:
    """FastAPI dependency: reject anything without the shared secret."""
    token, _ = effective_token()

    if not secrets.compare_digest(x_api_token or "", token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing X-Api-Token",
        )


# ─── Rate limiting ───────────────────────────────────────────────

class InMemoryRateLimiter:
    """Pragmatic in-memory sliding-window limiter.

    Runs per process. Behind multiple workers the effective limit is
    max_requests * workers; for a single-instance demo that is fine.
    """

    def __init__(self, *, max_requests: int, window_seconds: int) -> None:
        self.max_requests = max_requests
        self.window = window_seconds
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def allow(self, key: str) -> bool:
        async with self._lock:
            now = time.monotonic()
            bucket = self._buckets[key]
            while bucket and now - bucket[0] > self.window:
                bucket.popleft()
            if len(bucket) >= self.max_requests:
                return False
            bucket.append(now)
            return True

    def reset(self) -> None:
        """Tests only: clear the entire state."""
        self._buckets.clear()


# 5 submissions per IP per 60 seconds.
demo_lead_limiter = InMemoryRateLimiter(max_requests=5, window_seconds=60)


def client_ip(request: Request) -> str:
    return (request.client.host if request.client else "unknown") or "unknown"
