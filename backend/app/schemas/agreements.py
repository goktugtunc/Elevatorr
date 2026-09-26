"""Agreement (Sözleşme) I/O models (02-api-sozlesme §8.1; Figma 9d / 3a / 5c)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.enums import AgreementStatus, PendingTxKind, PendingTxStatus, RiskProfile, UserRole
from app.schemas.assets import AssetBriefOut
from app.schemas.common import AddressOut, Amount

ValueRange = Literal["24h", "7d", "30d", "90d", "all"]
#: `status` query of GET /agreements: a concrete status or a group.
STATUS_GROUPS = ("open", "closed")


class PartyOut(BaseModel):
    id: uuid.UUID
    username: str
    display_name: str
    avatar_url: str | None = None
    wallet_address: AddressOut
    role: UserRole


class BalanceOut(BaseModel):
    asset: AssetBriefOut
    balance: Amount
    updated_at: datetime | None = None
    value_try: Amount | None = Field(default=None, description="TL equivalent when a USD price is known")


class TlOut(BaseModel):
    """TL equivalents: base-asset amounts × indicative USD price × USD/TRY."""

    rate: Amount = Field(description="TRY per 1 USD")
    rate_source: str
    stale: bool = False
    base_usd_price: Amount | None = Field(default=None, description="USD per 1 base asset (null = unknown)")
    base_price_source: str | None = None
    principal_try: Amount | None = None
    current_value_try: Amount | None = None
    pnl_try: Amount | None = None
    final_value_try: Amount | None = None
    customer_payout_try: Amount | None = None


class PendingTxBriefOut(BaseModel):
    id: uuid.UUID
    kind: PendingTxKind
    action: str
    status: PendingTxStatus
    tx_hash: str | None = None
    created_at: datetime
    expires_at: datetime


class AgreementOut(BaseModel):
    id: uuid.UUID
    onchain_id: int | None = None
    offer_id: uuid.UUID | None = None
    listing_id: uuid.UUID | None = None
    status: AgreementStatus
    proposer_role: UserRole
    customer: PartyOut
    trader: PartyOut
    base_asset: AssetBriefOut
    vault_address: AddressOut | None = Field(default=None, description="vault (proxy) the agreement lives in")
    # terms (mirror of the on-chain Terms)
    principal: Amount
    duration_secs: int
    duration_days: int
    commission_bps: int
    max_drawdown_bps: int
    platform_fee_bps: int | None = None
    risk_profile: RiskProfile | None = None
    listing_ref: str
    # lifecycle
    created_tx: str | None = None
    activate_tx: str | None = None
    cancel_tx: str | None = None
    settle_tx: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    seconds_remaining: int | None = None
    is_expired: bool = False
    # live valuation
    current_value: Amount | None = None
    value_updated_at: datetime | None = None
    high_water_value: Amount | None = None
    pnl: Amount | None = Field(default=None, description="current (or final) value − principal")
    pnl_bps: int | None = None
    drawdown_floor: Amount = Field(description="principal × (1 − max_drawdown); trades below it are rejected")
    drawdown_bps: int = Field(default=0, description="current drawdown from principal in bps (0 when in profit)")
    # settlement
    final_value: Amount | None = None
    profit: Amount | None = None
    trader_fee: Amount | None = None
    platform_fee: Amount | None = None
    customer_payout: Amount | None = None
    settled_at: datetime | None = None
    settled_by: AddressOut | None = None
    last_event_block_number: int | None = None
    balances: list[BalanceOut] = Field(default_factory=list)
    # viewer context
    my_role: UserRole | None = Field(default=None, description="customer | trader | null (not a party)")
    available_actions: list[str] = Field(default_factory=list, description="tx actions the viewer may build now")
    claimable_assets: list[uuid.UUID] = Field(default_factory=list, description="asset ids `claim` can withdraw")
    pending_tx: PendingTxBriefOut | None = Field(default=None, description="latest unfinished transaction of the viewer")
    tl: TlOut | None = None
    created_at: datetime
    updated_at: datetime


class ValuePointOut(BaseModel):
    at: datetime
    value: Amount
    return_bps: int


class ValueHistoryOut(BaseModel):
    agreement_id: uuid.UUID
    range: ValueRange
    principal: Amount
    current_value: Amount | None = None
    high_water_value: Amount | None = None
    points: list[ValuePointOut]


__all__ = [
    "STATUS_GROUPS",
    "AgreementOut",
    "AssetBriefOut",
    "BalanceOut",
    "PartyOut",
    "PendingTxBriefOut",
    "TlOut",
    "ValueHistoryOut",
    "ValuePointOut",
    "ValueRange",
]
