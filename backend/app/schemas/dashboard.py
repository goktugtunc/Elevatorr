"""Role-aware dashboard (Figma 3a customer "Panel", 5c trader "Panel") I/O models."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.models.enums import AgreementStatus, RiskLevel, RiskProfile
from app.schemas.common import Amount

ZERO = Decimal("0")


class ListingInteractionsOut(BaseModel):
    """"İlanıma Gelen Etkileşimler": counters summed over the user's listings."""

    listings: int = 0
    views: int = 0
    likes: int = 0
    offers: int = 0
    pending_offers: int = 0


class PositionBriefOut(BaseModel):
    agreement_id: uuid.UUID
    onchain_id: int | None = None
    status: AgreementStatus
    counterparty_id: uuid.UUID
    counterparty_username: str
    counterparty_display_name: str
    counterparty_avatar_url: str | None = None
    base_asset_code: str
    principal: Amount
    current_value: Amount
    pnl: Amount
    pnl_bps: int
    commission_bps: int
    duration_days: int
    start_time: datetime | None = None
    end_time: datetime | None = None


class FollowedTraderOut(BaseModel):
    trader_id: uuid.UUID
    username: str
    display_name: str
    avatar_url: str | None = None
    risk_level: RiskLevel | None = None
    commission_bps: int | None = None
    total_return_bps: int = 0
    monthly_return_bps: int = 0
    rating_avg: Amount = ZERO
    invested: bool = Field(description="the customer has an open agreement with this trader")
    invested_principal: Amount = ZERO
    open_pnl_bps: int | None = None


class CustomerDashboardOut(BaseModel):
    role: Literal["customer"] = "customer"
    generated_at: datetime
    base_asset_code: str
    portfolio_value: Amount = Field(description="positions_value + wallet_balance (base asset units)")
    wallet_balance: Amount | None = Field(default=None, description="live base-asset balance of the wallet")
    wallet_error: str | None = None
    invested_principal: Amount
    positions_value: Amount
    open_pnl: Amount
    open_pnl_bps: int
    month_pnl: Amount = Field(description="realised P&L of agreements settled in the last 30 days + open P&L")
    month_change_bps: int
    positions: list[PositionBriefOut] = Field(default_factory=list)
    followed: list[FollowedTraderOut] = Field(default_factory=list)
    followed_count: int = 0
    invested_count: int = 0
    listing_interactions: ListingInteractionsOut


class PendingOfferBriefOut(BaseModel):
    offer_id: uuid.UUID
    listing_id: uuid.UUID
    from_user_id: uuid.UUID
    from_username: str
    from_display_name: str
    from_avatar_url: str | None = None
    amount: Amount
    base_asset_code: str
    duration_days: int
    commission_bps: int
    markets: list[str] = Field(default_factory=list)
    risk_profile: RiskProfile | None = None
    expires_at: datetime
    created_at: datetime


class ProfileChecklistOut(BaseModel):
    """"Profilini Güçlendir" (Figma 5c)."""

    wallet_connected: bool = True
    has_avatar: bool = False
    has_strategy: bool = False
    has_service_listing: bool = False
    has_trade: bool = False
    completion_pct: int = 0


class TraderDashboardOut(BaseModel):
    role: Literal["trader"] = "trader"
    generated_at: datetime
    base_asset_code: str
    managed_capital: Amount = Field(description="current value of open agreements (principal until valued)")
    invested_principal: Amount
    open_pnl: Amount
    open_pnl_bps: int
    active_investors: int
    pending_offers_count: int
    month_commission: Amount = Field(description="trader_fee of agreements settled in the last 30 days")
    total_commission: Amount
    settled_agreements: int
    positions: list[PositionBriefOut] = Field(default_factory=list)
    pending_offers: list[PendingOfferBriefOut] = Field(default_factory=list)
    listing_interactions: ListingInteractionsOut
    profile_checklist: ProfileChecklistOut


DashboardOut = Annotated[CustomerDashboardOut | TraderDashboardOut, Field(discriminator="role")]
