"""Domain error hierarchy -> HTTP mapping is done in app.main (exception handlers).

Services raise these; routers never build HTTPException by hand for domain rules.
Chain-specific subclasses live in ``app.services.chain.errors``.
"""
from __future__ import annotations

from typing import Any


class AppError(Exception):
    status_code: int = 400
    code: str = "bad_request"

    def __init__(self, message: str, *, code: str | None = None, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = details or {}


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"


class ValidationError(AppError):
    status_code = 422
    code = "validation_error"


class ChainError(AppError):
    """RPC unreachable / timeout / unexpected answer from the Monad node."""

    status_code = 502
    code = "chain_error"


class RateLimitedError(AppError):
    """Too many requests for a rate-limited endpoint (``details.retry_after_seconds`` when known)."""

    status_code = 429
    code = "rate_limited"


class InsufficientFundsError(AppError):
    status_code = 400
    code = "insufficient_funds"


class StateError(AppError):
    """Operation not allowed in current entity state."""

    status_code = 409
    code = "invalid_state"
