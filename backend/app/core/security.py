"""JWT access tokens (02-api-sozlesme §1.5, 03-backend-tasarim §3.5).

Claims: ``sub`` (wallet address, lower-case), ``uid``, ``role``, ``iat``, ``exp``, ``jti``, ``iss`` (= ``siwe_domain``)
and ``auth_time`` (epoch seconds of the original SIWE verification; refresh carries it over unchanged).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.core.config import Settings
from app.core.errors import UnauthorizedError
from app.services.chain.addresses import normalize
from app.services.chain.errors import InvalidAddressError

REQUIRED_CLAIMS = ["sub", "exp", "iat", "jti", "auth_time"]


@dataclass(frozen=True)
class TokenClaims:
    address: str  # wallet address, lower-case (sub)
    user_id: uuid.UUID | None
    role: str | None
    expires_at: datetime
    jti: str
    auth_time: int  # epoch seconds of the first SIWE verification in this session


def create_access_token(
    settings: Settings,
    *,
    address: str,
    user_id: uuid.UUID | None,
    role: str | None,
    auth_time: int | None = None,
) -> str:
    now = datetime.now(UTC)
    iat = int(now.timestamp())
    payload: dict[str, Any] = {
        "sub": normalize(address),
        "uid": str(user_id) if user_id else None,
        "role": role,
        "iat": iat,
        "exp": int((now + timedelta(seconds=settings.access_token_ttl_seconds)).timestamp()),
        "jti": uuid.uuid4().hex,
        "iss": settings.siwe_domain,
        "auth_time": int(auth_time) if auth_time is not None else iat,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(settings: Settings, token: str) -> TokenClaims:
    try:
        data = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.siwe_domain,
            options={"require": REQUIRED_CLAIMS},
        )
    except jwt.ExpiredSignatureError as e:
        raise UnauthorizedError("Token expired", code="token_expired") from e
    except jwt.InvalidTokenError as e:
        raise UnauthorizedError("Invalid token", code="token_invalid") from e
    try:
        address = normalize(str(data["sub"]))
    except InvalidAddressError as e:
        raise UnauthorizedError("Invalid token", code="token_invalid") from e
    uid = data.get("uid")
    try:
        user_id = uuid.UUID(uid) if uid else None
        auth_time = int(data["auth_time"])
    except (ValueError, TypeError) as e:
        raise UnauthorizedError("Invalid token", code="token_invalid") from e
    return TokenClaims(
        address=address,
        user_id=user_id,
        role=data.get("role"),
        expires_at=datetime.fromtimestamp(data["exp"], tz=UTC),
        jti=str(data["jti"]),
        auth_time=auth_time,
    )


__all__ = ["REQUIRED_CLAIMS", "TokenClaims", "create_access_token", "decode_access_token"]
