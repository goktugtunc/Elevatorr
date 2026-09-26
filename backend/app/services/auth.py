"""Wallet login via Sign-In with Ethereum (EIP-4361), 02-api-sozlesme §1 / 03-backend-tasarim §3.3.

``create_nonce`` builds the exact message the wallet signs and stores it next to the nonce; ``verify_siwe_login``
checks the message field by field (each step has its own error code), burns the nonce and issues the JWT via
``build_login_response``. Deliberately does not import ``app.services.users`` (Dalga 1 constraint).
"""
from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ForbiddenError, RateLimitedError, UnauthorizedError
from app.core.ratelimit import nonce_limiter
from app.core.security import TokenClaims, create_access_token, decode_access_token
from app.models import AuthNonce, User
from app.schemas.auth import AuthMeOut, LoginOut, NonceOut
from app.schemas.users import MeOut
from app.services.chain.addresses import normalize
from app.services.siwe import (
    SignatureInvalid,
    SiweParseError,
    build_message,
    parse_message,
    recover_address,
)

log = logging.getLogger(__name__)


def refresh_max_age_seconds(settings: Settings) -> int:
    """Absolute session ceiling: ``jwt_absolute_ttl_days`` in seconds (refresh never extends it)."""
    return int(settings.jwt_absolute_ttl_days) * 86_400


# --- tokens ------------------------------------------------------------------------------------------


def issue_token(settings: Settings, address: str, user: User | None, *, auth_time: int) -> tuple[str, datetime]:
    """JWT for a wallet; role/uid claims only when a profile exists. Returns (token, expires_at)."""
    token = create_access_token(
        settings,
        address=address,
        user_id=user.id if user else None,
        role=user.role.value if user else None,
        auth_time=auth_time,
    )
    return token, decode_access_token(settings, token).expires_at


async def _load_user(db: AsyncSession, address: str) -> User | None:
    return (await db.execute(select(User).where(User.wallet_address == normalize(address)))).scalar_one_or_none()


async def build_login_response(db: AsyncSession, settings: Settings, address: str, *, auth_time: int) -> LoginOut:
    """Shared tail of verify/refresh: look up the profile, refuse disabled accounts, issue a token."""
    addr = normalize(address)
    user = await _load_user(db, addr)
    if user is not None:
        if not user.is_active:
            raise ForbiddenError("Account disabled", code="account_disabled")
        user.last_login_at = datetime.now(UTC)
        await db.flush()
        await db.refresh(user)  # reload server-side updated_at instead of leaving it expired
    token, expires_at = issue_token(settings, addr, user, auth_time=auth_time)
    return LoginOut(
        token=token,
        expires_at=expires_at,
        address=addr,
        registered=user is not None,
        user=MeOut.model_validate(user) if user else None,
    )


async def refresh_token(db: AsyncSession, settings: Settings, claims: TokenClaims) -> LoginOut:
    """New token for a still-valid one. ``auth_time`` is carried over; past the absolute ceiling → ``session_expired``."""
    now = int(datetime.now(UTC).timestamp())
    if now - claims.auth_time > refresh_max_age_seconds(settings):
        raise UnauthorizedError("Session expired, sign in again", code="session_expired")
    return await build_login_response(db, settings, claims.address, auth_time=claims.auth_time)


async def auth_me(db: AsyncSession, claims: TokenClaims) -> AuthMeOut:
    user = await _load_user(db, claims.address)
    if user is not None and not user.is_active:
        raise ForbiddenError("Account disabled", code="account_disabled")
    return AuthMeOut(
        address=claims.address,
        registered=user is not None,
        user=MeOut.model_validate(user) if user else None,
        token_expires_at=claims.expires_at,
    )


# --- SIWE nonce / verify -----------------------------------------------------------------------------------


async def create_nonce(db: AsyncSession, settings: Settings, address: str, client_ip: str) -> NonceOut:
    """Step 1: rate-limit by IP, build the EIP-4361 message and persist it with a fresh nonce."""
    retry_after = nonce_limiter().hit(f"nonce:{client_ip}")
    if retry_after is not None:
        raise RateLimitedError(
            "Too many nonce requests", details={"retry_after_seconds": max(1, int(retry_after + 0.999))}
        )
    addr = normalize(address)  # 422 invalid_address
    now = datetime.now(UTC)
    await purge_expired_nonces(db, older_than=now - timedelta(hours=1))
    nonce = secrets.token_hex(16)
    expires_at = now + timedelta(seconds=settings.auth_nonce_ttl_seconds)
    message = build_message(
        domain=settings.siwe_domain,
        address=addr,
        statement=settings.siwe_statement,
        uri=settings.siwe_uri,
        chain_id=settings.chain_id,
        nonce=nonce,
        issued_at=now,
        expiration_time=expires_at,
    )
    row = AuthNonce(address=addr, nonce=nonce, message=message, issued_at=now, expires_at=expires_at, used_at=None)
    db.add(row)
    await db.flush()
    return NonceOut(
        nonce=nonce, message=message, expires_at=expires_at, chain_id=settings.chain_id, domain=settings.siwe_domain
    )


async def verify_siwe_login(db: AsyncSession, settings: Settings, message: str, signature: str) -> LoginOut:
    """Step 2: validate the message (02-api §1.2 order), check the signature, burn the nonce, log the wallet in."""
    try:
        fields = parse_message(message)
    except SiweParseError as e:
        raise UnauthorizedError("Malformed SIWE message", code="siwe_invalid") from e

    if fields.domain != settings.siwe_domain:
        raise UnauthorizedError("SIWE domain mismatch", code="siwe_domain_mismatch")
    if fields.uri != settings.siwe_uri:
        raise UnauthorizedError("SIWE URI mismatch", code="siwe_uri_mismatch")
    if fields.chain_id != int(settings.chain_id):
        raise UnauthorizedError("SIWE chain id mismatch", code="siwe_chain_mismatch")

    now = datetime.now(UTC)
    if fields.expiration_time is None or fields.expiration_time <= now:
        raise UnauthorizedError("SIWE message expired", code="siwe_expired")
    if fields.not_before is not None and fields.not_before > now:
        raise UnauthorizedError("SIWE message not yet valid", code="siwe_not_yet_valid")

    msg_address = normalize(fields.address)
    row = (
        await db.execute(select(AuthNonce).where(AuthNonce.nonce == fields.nonce).with_for_update())
    ).scalar_one_or_none()
    if row is None:
        raise UnauthorizedError("Unknown nonce", code="nonce_invalid")
    if row.used_at is not None:
        raise UnauthorizedError("Nonce already used", code="nonce_used")
    if row.expires_at <= now:
        raise UnauthorizedError("Nonce expired", code="nonce_expired")
    if row.address != msg_address:
        raise UnauthorizedError("Nonce was issued for another address", code="nonce_invalid")
    if row.message != message:
        raise UnauthorizedError("Message differs from the issued one", code="siwe_invalid")

    try:
        signer = recover_address(message, signature)
    except SignatureInvalid as e:
        raise UnauthorizedError("Signature does not match", code="signature_invalid") from e
    if signer != row.address:
        raise UnauthorizedError("Signature does not match", code="signature_invalid")

    row.used_at = now
    await db.flush()
    log.info("siwe login ok address=%s", row.address)
    return await build_login_response(db, settings, row.address, auth_time=int(now.timestamp()))


async def purge_expired_nonces(db: AsyncSession, *, older_than: datetime | None = None) -> int:
    """Delete nonces whose expiry passed before `older_than` (default: now). Returns rows removed."""
    cutoff = older_than or datetime.now(UTC)
    result = await db.execute(delete(AuthNonce).where(AuthNonce.expires_at < cutoff))
    return int(result.rowcount or 0)


__all__ = [
    "auth_me",
    "build_login_response",
    "create_nonce",
    "issue_token",
    "purge_expired_nonces",
    "refresh_max_age_seconds",
    "refresh_token",
    "verify_siwe_login",
]
