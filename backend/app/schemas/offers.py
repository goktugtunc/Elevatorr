"""Offer I/O models (Figma 2d "Teklif Ver" / 6c-6d "Teklif İste") and the agreement draft an accepted
offer produces. Kept free of imports from app.schemas.listings so listings can embed OfferOut."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from app.models.enums import (
    AgreementStatus,
    ListingKind,
    ListingStatus,
    OfferDirection,
    OfferStatus,
    RiskProfile,
    UserRole,
)
from app.schemas.assets import AssetOut
from app.schemas.common import Amount, AmountIn, ORMModel
from app.schemas.users import UserOut

# Contract `Terms` limits (01-kontrat-spec §3) — validated here so a draft can always go on-chain.
MIN_DURATION_DAYS = 1
MAX_DURATION_DAYS = 3 * 365  # 94_608_000 s
MAX_COMMISSION_BPS = 5_000
MIN_DRAWDOWN_BPS = 100
MAX_DRAWDOWN_BPS = 10_000  # 10_000 = drawdown check disabled
MAX_EXPECTED_RETURN_BPS = 100_000

OfferBox = Literal["inbox", "outbox", "all"]
NextAction = Literal["open", "open_reserved", "propose"]


class OfferCreateIn(BaseModel):
    """Terms the sender proposes. Fields left out fall back to the listing / the sender's profile:

    * trader -> capital listing ("Teklif Ver"): amount, duration, max drawdown and base asset come from
      the customer's listing; the trader sets `commission_bps` (default: profile) and the expected return.
      `max_drawdown_bps` may only tighten the listing's `max_loss_bps`, never relax it (BE-23).
    * customer -> service listing ("Teklif İste"): `amount` (>= listing min_capital) and `duration_days`
      are required; `commission_bps` defaults to the trader's listing, `max_drawdown_bps` to 10000
      (disabled), `base_asset_id` to the chain's default base asset.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    listing_id: uuid.UUID
    amount: AmountIn | None = None
    base_asset_id: uuid.UUID | None = None
    duration_days: int | None = Field(default=None, ge=MIN_DURATION_DAYS, le=MAX_DURATION_DAYS)
    commission_bps: int | None = Field(default=None, ge=0, le=MAX_COMMISSION_BPS)
    max_drawdown_bps: int | None = Field(default=None, ge=MIN_DRAWDOWN_BPS, le=MAX_DRAWDOWN_BPS)
    expected_return_min_bps: int | None = Field(default=None, ge=0, le=MAX_EXPECTED_RETURN_BPS)
    expected_return_max_bps: int | None = Field(default=None, ge=0, le=MAX_EXPECTED_RETURN_BPS)
    note: str | None = Field(default=None, max_length=2000)
    expires_in_hours: int = Field(default=72, ge=1, le=24 * 14, description="offer validity window")

    @model_validator(mode="after")
    def _return_range(self) -> OfferCreateIn:
        lo, hi = self.expected_return_min_bps, self.expected_return_max_bps
        if lo is not None and hi is not None and lo > hi:
            raise PydanticCustomError("return_range", "expected_return_min_bps must be <= expected_return_max_bps")
        return self


class OfferRejectIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    reason: str | None = Field(default=None, max_length=500)


class ListingBriefOut(ORMModel):
    """Enough of a listing to render an offer row (Figma 6c/6d)."""

    id: uuid.UUID
    owner_id: uuid.UUID
    kind: ListingKind
    title: str
    status: ListingStatus
    risk_profile: RiskProfile | None = None
    markets: list[str] = Field(default_factory=list)
    amount: Amount | None = None
    duration_days: int | None = None
    max_loss_bps: int | None = None
    commission_bps: int | None = None
    min_capital: Amount | None = None
    reservation_id: int | None = None
    reserved_amount: Amount | None = None
    is_funded: bool = False


class OfferOut(ORMModel):
    id: uuid.UUID
    listing_id: uuid.UUID
    listing: ListingBriefOut
    from_user_id: uuid.UUID
    to_user_id: uuid.UUID
    from_user: UserOut
    to_user: UserOut
    direction: OfferDirection
    amount: Amount
    base_asset_id: uuid.UUID
    base_asset: AssetOut
    duration_days: int
    commission_bps: int
    max_drawdown_bps: int
    expected_return_min_bps: int | None = None
    expected_return_max_bps: int | None = None
    note: str | None = None
    status: OfferStatus
    expires_at: datetime
    responded_at: datetime | None = None
    agreement_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime | None = None
    # viewer-dependent, filled by the service
    conversation_id: uuid.UUID | None = None
    is_incoming: bool | None = None  # True when the viewer is the recipient ("inbox")


class AgreementDraftOut(ORMModel):
    """The off-chain `agreements` row created by accepting an offer (status `draft`, nothing on-chain
    yet). The agreements slice serves the full view at GET /agreements/{id}."""

    id: uuid.UUID
    onchain_id: int | None = None
    offer_id: uuid.UUID | None = None
    listing_id: uuid.UUID | None = None
    customer_id: uuid.UUID
    trader_id: uuid.UUID
    customer: UserOut
    trader: UserOut
    base_asset_id: uuid.UUID
    base_asset: AssetOut
    principal: Amount
    duration_secs: int
    commission_bps: int
    max_drawdown_bps: int
    risk_profile: RiskProfile | None = None
    listing_ref: str
    status: AgreementStatus
    proposer_role: UserRole
    created_at: datetime

    @property
    def duration_days(self) -> int:
        return int(self.duration_secs) // 86_400


class OfferAcceptOut(BaseModel):
    offer: OfferOut
    agreement: AgreementDraftOut
    conversation_id: uuid.UUID
    next_action: NextAction = Field(
        description="on-chain creation the acceptor performs next: customer -> `open` (escrows the principal) or "
        "`open_reserved` (the listing's reservation covers it), trader -> `propose` (customer funds afterwards)"
    )


class OfferStatsOut(BaseModel):
    pending_inbox: int
    pending_outbox: int


__all__ = [
    "MIN_DURATION_DAYS",
    "MAX_DURATION_DAYS",
    "MAX_COMMISSION_BPS",
    "MIN_DRAWDOWN_BPS",
    "MAX_DRAWDOWN_BPS",
    "MAX_EXPECTED_RETURN_BPS",
    "NextAction",
    "OfferBox",
    "OfferCreateIn",
    "OfferRejectIn",
    "ListingBriefOut",
    "OfferOut",
    "AgreementDraftOut",
    "OfferAcceptOut",
    "OfferStatsOut",
]
