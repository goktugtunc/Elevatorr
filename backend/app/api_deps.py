"""FastAPI dependencies: DB session, settings, current user, role guards, admin key, chain gateway.

Usage in routers:
    async def handler(db: DB, user: CurrentUser, settings: SettingsDep, chain=ChainDep)
    router = APIRouter(prefix="/admin", dependencies=[AdminGuard])
"""
from __future__ import annotations

import hmac
import uuid
from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import ForbiddenError, UnauthorizedError, ValidationError
from app.core.security import TokenClaims, decode_access_token
from app.db.session import get_db
from app.models import User, UserRole
from app.services.chain import get_chain
from app.services.chain.addresses import normalize

bearer = HTTPBearer(auto_error=False)

DB = Annotated[AsyncSession, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

# Chain gateway: `chain = ChainDep` as a parameter default (MonadGateway is imported lazily inside get_chain).
ChainDep = Depends(get_chain)


async def get_claims(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)], settings: SettingsDep
) -> TokenClaims:
    """Validated JWT claims (wallet authenticated; may or may not have a profile yet)."""
    if creds is None or creds.scheme.lower() != "bearer":
        raise UnauthorizedError("Missing bearer token", code="missing_token")
    return decode_access_token(settings, creds.credentials)


Claims = Annotated[TokenClaims, Depends(get_claims)]


async def _load_user(db: AsyncSession, address: str) -> User | None:
    """Profile for a wallet; addresses are stored lower-case (K11)."""
    return (await db.execute(select(User).where(User.wallet_address == normalize(address)))).scalar_one_or_none()


async def get_current_user(claims: Claims, db: DB) -> User:
    """Authenticated AND registered user. 401 `not_registered` if the token is valid but no profile exists."""
    user = await _load_user(db, claims.address)
    if user is None:
        raise UnauthorizedError("Wallet authenticated but no profile registered", code="not_registered")
    if not user.is_active:
        raise ForbiddenError("Account disabled", code="account_disabled")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_optional_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)], settings: SettingsDep, db: DB
) -> User | None:
    """None when no token is sent; raises on an invalid token; None when the wallet has no profile."""
    if creds is None or creds.scheme.lower() != "bearer":
        return None
    claims = decode_access_token(settings, creds.credentials)
    user = await _load_user(db, claims.address)
    if user is not None and not user.is_active:
        raise ForbiddenError("Account disabled", code="account_disabled")
    return user


OptionalUser = Annotated[User | None, Depends(get_optional_user)]


def require_role(*roles: UserRole):
    """Dependency factory: the current user must have one of `roles` (admins always pass)."""

    async def _dep(user: CurrentUser) -> User:
        if user.role not in roles and not user.is_admin:
            raise ForbiddenError(f"Requires role: {', '.join(r.value for r in roles)}", code="wrong_role")
        return user

    return _dep


CustomerUser = Annotated[User, Depends(require_role(UserRole.customer))]
TraderUser = Annotated[User, Depends(require_role(UserRole.trader))]


async def require_admin_key(settings: SettingsDep, x_admin_key: Annotated[str | None, Header()] = None) -> None:
    if not x_admin_key or not hmac.compare_digest(x_admin_key, settings.admin_key):
        raise ForbiddenError("Invalid admin key", code="admin_key_invalid")


AdminGuard = Depends(require_admin_key)


def request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "") or ""


def parse_uuid(value: str, name: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as e:
        raise ValidationError(f"Invalid {name}") from e


__all__ = [
    "DB",
    "SettingsDep",
    "ChainDep",
    "Claims",
    "CurrentUser",
    "OptionalUser",
    "CustomerUser",
    "TraderUser",
    "AdminGuard",
    "get_claims",
    "get_current_user",
    "get_optional_user",
    "require_role",
    "require_admin_key",
    "request_id",
    "parse_uuid",
]
