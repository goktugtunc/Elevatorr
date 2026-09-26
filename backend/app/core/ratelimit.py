"""In-memory token-bucket rate limiter (03-backend-tasarim §3.6).

Per-process and best-effort: with two uvicorn workers the effective limit is roughly doubled. The real guard is the
nginx ``limit_req`` zone; this is the second line of defence. Keys untouched for 10 minutes are swept lazily from
inside ``hit`` so the dict never grows without bound.
"""
from __future__ import annotations

import threading
import time

from fastapi import Request

IDLE_EVICT_SECONDS = 600.0
SWEEP_INTERVAL_SECONDS = 60.0


class TokenBucket:
    """``rate_per_minute`` tokens refill continuously; ``burst`` (default = rate) is the bucket capacity."""

    def __init__(self, rate_per_minute: int, burst: int | None = None):
        if rate_per_minute <= 0:
            raise ValueError("rate_per_minute must be positive")
        self.rate = rate_per_minute / 60.0  # tokens per second
        self.capacity = float(burst if burst is not None else rate_per_minute)
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_seen)
        self._last_sweep = time.monotonic()
        self._lock = threading.Lock()

    def hit(self, key: str, now: float | None = None) -> float | None:
        """Consume one token for ``key``. Returns None when allowed, else seconds until the next token."""
        t = time.monotonic() if now is None else now
        with self._lock:
            self._maybe_sweep(t)
            tokens, last = self._buckets.get(key, (self.capacity, t))
            tokens = min(self.capacity, tokens + (t - last) * self.rate)
            if tokens >= 1.0:
                self._buckets[key] = (tokens - 1.0, t)
                return None
            self._buckets[key] = (tokens, t)
            return max(0.0, (1.0 - tokens) / self.rate)

    def reset(self, key: str | None = None) -> None:
        """Forget one key (or everything); handy in tests."""
        with self._lock:
            if key is None:
                self._buckets.clear()
            else:
                self._buckets.pop(key, None)

    def _maybe_sweep(self, t: float) -> None:
        if t - self._last_sweep < SWEEP_INTERVAL_SECONDS:
            return
        self._last_sweep = t
        stale = [k for k, (_, last) in self._buckets.items() if t - last > IDLE_EVICT_SECONDS]
        for k in stale:
            del self._buckets[k]


_nonce_limiter: TokenBucket | None = None


def nonce_limiter() -> TokenBucket:
    """Module-level bucket for ``POST /auth/nonce`` (created lazily from settings)."""
    global _nonce_limiter
    if _nonce_limiter is None:
        from app.core.config import get_settings

        per_minute = int(getattr(get_settings(), "auth_nonce_rate_limit_per_minute", 20))
        _nonce_limiter = TokenBucket(rate_per_minute=per_minute)
    return _nonce_limiter


def set_nonce_limiter(bucket: TokenBucket | None) -> None:
    """Test hook: inject a bucket (None resets to the lazy default)."""
    global _nonce_limiter
    _nonce_limiter = bucket


def client_ip(request: Request) -> str:
    """Peer address as uvicorn sees it (``--proxy-headers`` already resolves X-Forwarded-For behind nginx)."""
    client = request.client
    return client.host if client is not None and client.host else "unknown"


__all__ = ["TokenBucket", "client_ip", "nonce_limiter", "set_nonce_limiter"]
