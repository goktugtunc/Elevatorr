"""User / trader I/O models: registration with role-specific blocks (Figma 1e-1g), profiles,
the trader profile aggregate (3e), follows and ratings."""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError
from sqlalchemy import inspect as sa_inspect

from app.models.enums import MarketCategory, RiskLevel, RiskProfile, UserRole
from app.schemas.common import AddressOut, Amount, AmountIn, ORMModel

USERNAME_PATTERN = r"^[a-z0-9_]{3,32}$"
_USERNAME_RE = re.compile(USERNAME_PATTERN)
MAX_COMMISSION_BPS = 5_000  # contract MAX_COMMISSION_BPS

TraderSort = Literal["rating", "return", "capital", "followers", "newest"]
PerformanceRange = Literal["7d", "30d", "90d", "1y", "all"]


def normalize_username(value: str) -> str:
    """Lowercase + strip; the stored form. Raises ValueError when the result is not a valid username."""
    v = value.strip().lower()
    if not _USERNAME_RE.fullmatch(v):
        raise PydanticCustomError("invalid_username", "username must match {pattern}", {"pattern": USERNAME_PATTERN})
    return v


def dedupe_markets(values: list[MarketCategory]) -> list[MarketCategory]:
    if not values:
        raise PydanticCustomError("markets_empty", "select at least one market")
    out: list[MarketCategory] = []
    for m in values:
        if m not in out:
            out.append(m)
    return out


# --- registration ---------------------------------------------------------------------------------


class CustomerProfileIn(BaseModel):
    """Figma 1f: sermaye bütçesi, risk profili, piyasalar."""

    budget_amount: AmountIn
    risk_profile: RiskProfile
    markets: list[MarketCategory] = Field(min_length=1, max_length=3)

    @field_validator("markets")
    @classmethod
    def _markets(cls, v: list[MarketCategory]) -> list[MarketCategory]:
        return dedupe_markets(v)


class TraderProfileIn(BaseModel):
    """Figma 1g: piyasalar, strateji özeti, komisyon, min sermaye, risk seviyesi."""

    model_config = ConfigDict(str_strip_whitespace=True)

    markets: list[MarketCategory] = Field(min_length=1, max_length=3)
    strategy_summary: str = Field(min_length=1, max_length=2000)
    commission_bps: int = Field(ge=0, le=MAX_COMMISSION_BPS, description="trader share of profit, bps")
    min_capital: Decimal = Field(ge=0, decimal_places=18, max_digits=60)
    risk_level: RiskLevel

    @field_validator("markets")
    @classmethod
    def _markets(cls, v: list[MarketCategory]) -> list[MarketCategory]:
        return dedupe_markets(v)


class RegisterIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    role: UserRole
    username: str = Field(min_length=3, max_length=32, examples=["ayse_trader"])
    display_name: str = Field(min_length=1, max_length=80)
    bio: str | None = Field(default=None, max_length=2000)
    avatar_url: str | None = Field(default=None, max_length=500)
    customer: CustomerProfileIn | None = None
    trader: TraderProfileIn | None = None

    @field_validator("username")
    @classmethod
    def _username(cls, v: str) -> str:
        return normalize_username(v)

    @model_validator(mode="after")
    def _role_block(self) -> RegisterIn:
        # PydanticCustomError (not ValueError): its error entry stays JSON-serialisable in the 422 body
        if self.role is UserRole.customer:
            if self.customer is None:
                raise PydanticCustomError("role_block_missing", "'customer' block is required for role=customer")
            if self.trader is not None:
                raise PydanticCustomError("role_block_conflict", "'trader' block is not allowed for role=customer")
        else:
            if self.trader is None:
                raise PydanticCustomError("role_block_missing", "'trader' block is required for role=trader")
            if self.customer is not None:
                raise PydanticCustomError("role_block_conflict", "'customer' block is not allowed for role=trader")
        return self


# --- profiles -------------------------------------------------------------------------------------


class TraderStats(BaseModel):
    """Stats columns maintained by the indexer/reconciler (bps = basis points)."""

    total_return_bps: int = 0
    monthly_return_bps: int = 0
    max_drawdown_bps: int = 0
    win_rate_bps: int = 0
    managed_capital: Amount = Decimal("0")
    active_agreements: int = 0
    rating_avg: Amount = Decimal("0")
    rating_count: int = 0


STATS_FIELDS: tuple[str, ...] = tuple(TraderStats.model_fields)


class UserOut(ORMModel):
    """Public profile. Never exposes the Expo push token or admin flag.

    `wallet_address` is stored lower-case and serialised as EIP-55 checksum (K11). `budget_amount` is only
    returned to the owner (`MeOut`); public lookups blank it (BE-22, see `services.users.public_user_out`).
    """

    id: uuid.UUID
    wallet_address: AddressOut
    role: UserRole
    username: str
    display_name: str
    avatar_url: str | None = None
    bio: str | None = None
    markets: list[str] = Field(default_factory=list)
    created_at: datetime
    # customer (Figma 1f)
    budget_amount: Amount | None = None
    risk_profile: RiskProfile | None = None
    # trader (Figma 1g)
    strategy_summary: str | None = None
    #: Trader'ın geçmiş işlemlerini kendi cümleleriyle anlattığı bölüm.
    portfolio: str | None = None
    commission_bps: int | None = None
    min_capital: Amount | None = None
    risk_level: RiskLevel | None = None
    stats: TraderStats | None = None  # traders only

    @model_validator(mode="before")
    @classmethod
    def _from_orm_row(cls, data: Any) -> Any:
        """Accept a `User` ORM row: copy the columns and fold the stats columns into `stats`.

        Only attributes already loaded on the instance are read (`inspect(row).dict`), so an expired
        server-side column such as `updated_at` after a flush never triggers a lazy load (which would
        raise MissingGreenlet under the async engine); such fields fall back to their defaults."""
        if isinstance(data, dict) or not hasattr(data, "wallet_address"):
            return data
        state = sa_inspect(data, raiseerr=False)
        loaded: dict[str, Any] | None = dict(state.dict) if state is not None else None

        def get(name: str) -> Any:
            if loaded is not None:
                return loaded.get(name)
            return getattr(data, name, None)

        out = {name: get(name) for name in cls.model_fields if name != "stats"}
        if get("role") == UserRole.trader:
            out["stats"] = TraderStats(**{f: v for f in STATS_FIELDS if (v := get(f)) is not None})
        else:
            out["stats"] = None
        return out


class MeOut(UserOut):
    """The caller's own profile."""

    expo_push_token: str | None = None
    is_admin: bool = False
    is_active: bool = True
    last_login_at: datetime | None = None
    updated_at: datetime | None = None


class RegisterOut(BaseModel):
    user: MeOut
    token: str
    expires_at: datetime


class UserUpdateIn(BaseModel):
    """PATCH body: only fields present in the request are applied (null clears a nullable field).
    Role-specific fields are validated against the caller's role in the service."""

    model_config = ConfigDict(str_strip_whitespace=True)

    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    bio: str | None = Field(default=None, max_length=2000)
    avatar_url: str | None = Field(default=None, max_length=500)
    expo_push_token: str | None = Field(default=None, max_length=200)
    markets: list[MarketCategory] | None = Field(default=None, min_length=1, max_length=3)
    # customer
    budget_amount: AmountIn | None = None
    risk_profile: RiskProfile | None = None
    # trader
    strategy_summary: str | None = Field(default=None, min_length=1, max_length=2000)
    portfolio: str | None = Field(default=None, min_length=1, max_length=4000)
    commission_bps: int | None = Field(default=None, ge=0, le=MAX_COMMISSION_BPS)
    min_capital: Decimal | None = Field(default=None, ge=0, decimal_places=18, max_digits=60)
    risk_level: RiskLevel | None = None

    @field_validator("markets")
    @classmethod
    def _markets(cls, v: list[MarketCategory] | None) -> list[MarketCategory] | None:
        return dedupe_markets(v) if v is not None else None


# --- trader profile aggregate (Figma 3e) ------------------------------------------------------------------


class PerformancePoint(BaseModel):
    at: datetime
    value: Amount  # sum of open agreements' value at that bucket (base asset units)
    principal: Amount
    return_bps: int  # (value - principal) / principal in bps


class PositionBalanceOut(BaseModel):
    """02-api §6: `{asset_id, symbol, code, address, balance}` — `code` = `symbol` (deprecated alias)."""

    asset_id: uuid.UUID
    symbol: str
    code: str
    address: AddressOut
    balance: Amount


class PositionOut(BaseModel):
    """Live position on a public trader profile. Customer identity is anonymised and `principal` is rounded to
    one significant digit unless the viewer is the trader (BE-22): `customer_id` null, names "—"."""

    agreement_id: uuid.UUID
    onchain_id: int | None = None
    customer_id: uuid.UUID | None = None
    customer_username: str
    customer_display_name: str
    base_asset_code: str
    principal: Amount
    principal_approx: bool = Field(default=False, description="true when principal is a rounded band")
    current_value: Amount | None = None
    pnl_bps: int | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    balances: list[PositionBalanceOut] = Field(default_factory=list)


class TradeBriefOut(BaseModel):
    id: uuid.UUID
    agreement_id: uuid.UUID
    tx_hash: str | None = None
    block_number: int | None = None
    symbol_label: str
    token_in_code: str
    token_out_code: str
    amount_in: Amount
    amount_out: Amount
    value_after: Amount | None = None
    note: str | None = None
    created_at: datetime


class RatingOut(BaseModel):
    id: uuid.UUID
    agreement_id: uuid.UUID
    customer_id: uuid.UUID
    customer_username: str | None = None
    customer_display_name: str | None = None
    score: int
    comment: str | None = None
    created_at: datetime


class RatingsSummary(BaseModel):
    avg: Amount
    count: int
    distribution: dict[int, int] = Field(default_factory=dict)  # score -> count


class TraderProfileOut(BaseModel):
    user: UserOut
    stats: TraderStats
    follower_count: int
    is_following: bool | None = None  # None when the viewer is anonymous
    active_listings: int
    performance_range: str
    performance: list[PerformancePoint]
    positions: list[PositionOut]
    recent_trades: list[TradeBriefOut]
    ratings: RatingsSummary
    recent_ratings: list[RatingOut]


class TraderCardOut(BaseModel):
    user: UserOut
    follower_count: int
    is_following: bool | None = None


class FollowOut(BaseModel):
    trader_id: uuid.UUID
    following: bool
    follower_count: int
