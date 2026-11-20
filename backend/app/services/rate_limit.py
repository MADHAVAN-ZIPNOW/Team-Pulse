"""Tiny in-memory sliding-window rate limiter (single process, local use)."""
from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self.max_requests = max_requests
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> bool:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.max_requests:
            return False
        hits.append(now)
        return True


def rate_limit_dependency(limiter: RateLimiter, scope: str):
    async def dependency(request: Request) -> None:
        client = request.client.host if request.client else "unknown"
        session = request.cookies.get("pra_session", "")[:16]
        if not limiter.check(f"{scope}:{client}:{session}"):
            raise HTTPException(status_code=429, detail="Too many requests. Please slow down.")

    return dependency
