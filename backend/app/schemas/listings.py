"""Listing I/O models: capital listings by customers (Figma 5a) and service listings by traders (5b)."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from app.models.enums import ListingKind, ListingStatus, MarketCategory, RiskProfile
from app.schemas.assets import AssetOut
from app.schemas.common import Amount, AmountIn, ORMModel
from app.schemas.offers import (
    MAX_COMMISSION_BPS,
    MAX_DRAWDOWN_BPS,
    MAX_DURATION_DAYS,
    MAX_EXPECTED_RETURN_BPS,
    MIN_DRAWDOWN_BPS,
    MIN_DURATION_DAYS,
    OfferOut,
)
from app.schemas.users import UserOut, dedupe_markets

ListingSort = Literal["newest", "popular", "amount"]
ListingStatusAction = Literal["pause", "resume", "close"]


class ListingCreateIn(BaseModel):
    """`kind` is optional: a customer always creates a capital listing, a trader a service listing.
    Capital: `amount`, `duration_days` required; `base_asset_id` defaults to the chain's default base
    asset; `max_loss_bps` optional (None = no drawdown limit). Service: `commission_bps` / `min_capital`
    default to the trader's profile. `markets` defaults to the owner's profile markets."""

    model_config = ConfigDict(str_strip_whitespace=True)

    kind: ListingKind | None = None
    title: str = Field(min_length=3, max_length=120)
    description: str = Field(default="", max_length=4000)
    markets: list[MarketCategory] | None = Field(default=None, min_length=1, max_length=3)
    risk_profile: RiskProfile | None = None
    # capital (Figma 5a: sermaye, süre, piyasa, maks. kayıp, risk profili)
    amount: AmountIn | None = None
    base_asset_id: uuid.UUID | None = None
    duration_days: int | None = Field(default=None, ge=MIN_DURATION_DAYS, le=MAX_DURATION_DAYS)
    max_loss_bps: int | None = Field(default=None, ge=MIN_DRAWDOWN_BPS, le=MAX_DRAWDOWN_BPS)
    # service (Figma 5b: strateji, komisyon %, min sermaye, piyasalar, beklenen getiri)
    commission_bps: int | None = Field(default=None, ge=0, le=MAX_COMMISSION_BPS)
    min_capital: Decimal | None = Field(default=None, ge=0, decimal_places=18, max_digits=60)
    expected_return_min_bps: int | None = Field(default=None, ge=0, le=MAX_EXPECTED_RETURN_BPS)
    expected_return_max_bps: int | None = Field(default=None, ge=0, le=MAX_EXPECTED_RETURN_BPS)

    @field_validator("markets")
    @classmethod
    def _markets(cls, v: list[MarketCategory] | None) -> list[MarketCategory] | None:
        return dedupe_markets(v) if v is not None else None

    @model_validator(mode="after")
    def _return_range(self) -> ListingCreateIn:
        lo, hi = self.expected_return_min_bps, self.expected_return_max_bps
        if lo is not None and hi is not None and lo > hi:
            raise PydanticCustomError("return_range", "expected_return_min_bps must be <= expected_return_max_bps")
        return self


class ListingUpdateIn(BaseModel):
    """PATCH body: only the fields present are applied. `kind` and `owner` never change."""

    model_config = ConfigDict(str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=3, max_length=120)
    description: str | None = Field(default=None, max_length=4000)
    markets: list[MarketCategory] | None = Field(default=None, min_length=1, max_length=3)
    risk_profile: RiskProfile | None = None
    amount: AmountIn | None = None
    base_asset_id: uuid.UUID | None = None
    duration_days: int | None = Field(default=None, ge=MIN_DURATION_DAYS, le=MAX_DURATION_DAYS)
    max_loss_bps: int | None = Field(default=None, ge=MIN_DRAWDOWN_BPS, le=MAX_DRAWDOWN_BPS)
    commission_bps: int | None = Field(default=None, ge=0, le=MAX_COMMISSION_BPS)
    min_capital: Decimal | None = Field(default=None, ge=0, decimal_places=18, max_digits=60)
    expected_return_min_bps: int | None = Field(default=None, ge=0, le=MAX_EXPECTED_RETURN_BPS)
    expected_return_max_bps: int | None = Field(default=None, ge=0, le=MAX_EXPECTED_RETURN_BPS)

    @field_validator("markets")
    @classmethod
    def _markets(cls, v: list[MarketCategory] | None) -> list[MarketCategory] | None:
        return dedupe_markets(v) if v is not None else None


class ReleaseIn(BaseModel):
    """Optional body of `POST /listings/{id}/tx/release` (02-api §3.4): no amount -> `releaseAll`."""

    amount: AmountIn | None = Field(default=None, description="partial release; omit for releaseAll")


class ListingOut(ORMModel):
    id: uuid.UUID
    owner_id: uuid.UUID
    owner: UserOut
    kind: ListingKind
    title: str
    description: str = ""
    risk_profile: RiskProfile | None = None
    markets: list[str] = Field(default_factory=list)
    status: ListingStatus
    # capital
    amount: Amount | None = None
    base_asset_id: uuid.UUID | None = None
    base_asset: AssetOut | None = None
    duration_days: int | None = None
    max_loss_bps: int | None = None
    #: On-chain reservation holding this listing's capital in the vault. A capital
    #: listing is only public once this is set, so the advertised amount is always
    #: money the owner actually deposited.
    reservation_id: int | None = None
    #: What that reservation still holds; drops as agreements draw on it.
    reserved_amount: Amount | None = None
    is_funded: bool = False
    # service
    commission_bps: int | None = None
    min_capital: Amount | None = None
    expected_return_min_bps: int | None = None
    expected_return_max_bps: int | None = None
    # counters
    view_count: int = 0
    like_count: int = 0
    offer_count: int = 0
    closed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None
    # viewer-dependent (None for anonymous viewers)
    is_owner: bool | None = None
    is_saved: bool | None = None
    is_liked: bool | None = None


class ListingDetailOut(ListingOut):
    """GET /listings/{id}: the owner additionally sees the offers on the listing and a viewer sees
    whether they already have a pending offer."""

    offers: list[OfferOut] | None = None  # owner only
    pending_offers: int = 0
    my_offer_id: uuid.UUID | None = None  # the viewer's own pending offer on this listing, if any


class ListingCountsOut(BaseModel):
    #: Capital listings whose deposit is not confirmed yet — only the owner sees these.
    draft: int = 0
    active: int = 0
    paused: int = 0
    closed: int = 0


__all__ = [
    "ListingSort",
    "ListingStatusAction",
    "ListingCreateIn",
    "ListingUpdateIn",
    "ListingOut",
    "ListingDetailOut",
    "ListingCountsOut",
    "ReleaseIn",
]
