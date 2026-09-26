"""Trading (Figma 4a/4b "Yeni İşlem") and activity (3b/3c "Hareketler") I/O models (02-api §3.2, §8.2)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AgreementStatus
from app.schemas.agreements import PartyOut
from app.schemas.assets import AssetBriefOut
from app.schemas.common import Amount, AmountIn

ActivityState = Literal["open", "closed"]
ActivityRelation = Literal["party", "following"]

MAX_NOTE_LEN = 500


class QuoteOut(BaseModel):
    """Router quote through the vault's allow-listed router plus the drawdown headroom the contract will
    enforce (`value_after >= principal × (1 − max_drawdown)`)."""

    agreement_id: uuid.UUID
    onchain_id: int
    token_in: AssetBriefOut
    token_out: AssetBriefOut
    amount_in: Amount
    amount_out: Amount = Field(description="router `getAmountsOut` result")
    min_out: Amount = Field(description="amount_out × (1 − slippage_bps) — what the trade tx will carry")
    slippage_bps: int
    price: Amount = Field(description="token_out per 1 token_in")
    source: str = Field(description="router (eth_call getAmountsOut) | fake")
    api_amount_out: Amount | None = Field(default=None, description="always null (kept for compatibility)")
    price_impact_pct: Amount | None = None
    balance_in: Amount = Field(description="agreement's current balance of token_in")
    value_before: Amount = Field(description="`valueInBase` now")
    value_after_estimate: Amount = Field(description="value_before − in-leg + out-leg (both valued in base)")
    principal: Amount
    max_drawdown_bps: int
    drawdown_floor: Amount
    headroom: Amount = Field(description="value_after_estimate − drawdown_floor (negative = would be rejected)")
    headroom_bps: int
    allowed: bool
    reason: str | None = Field(default=None, description="why the trade would be rejected (null when allowed)")
    deadline_seconds: int
    quoted_at: datetime


class TradeTxIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    token_in: str = Field(description="asset id, 0x token address or symbol")
    token_out: str = Field(description="asset id, 0x token address or symbol")
    amount_in: AmountIn = Field(description="at most token_in.decimals decimal places")
    slippage_bps: int | None = Field(default=None, ge=0, le=5_000, description="default settings.default_trade_slippage_bps")
    note: str | None = Field(default=None, max_length=MAX_NOTE_LEN)
    notify_investors: bool = True
    deadline_seconds: int = Field(default=300, ge=30, le=3_600)


class TradeOut(BaseModel):
    id: uuid.UUID
    agreement_id: uuid.UUID
    onchain_id: int | None = None
    log_index: int | None = None
    tx_hash: str | None = None
    block_number: int | None = None
    explorer_url: str | None = None
    trader_id: uuid.UUID | None = None
    token_in: AssetBriefOut
    token_out: AssetBriefOut
    amount_in: Amount
    amount_out: Amount
    price: Amount | None = Field(default=None, description="amount_out / amount_in")
    value_after: Amount | None = None
    note: str | None = None
    symbol_label: str | None = None
    notify_investors: bool = True
    created_at: datetime


class TradeNoteIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    note: str | None = Field(default=None, max_length=MAX_NOTE_LEN)
    notify_investors: bool | None = None


class ActivityItemOut(BaseModel):
    trade: TradeOut
    agreement_status: AgreementStatus
    base_asset_code: str
    trader: PartyOut
    customer: PartyOut | None = Field(default=None, description="only when the viewer is a party")
    relation: ActivityRelation


__all__ = [
    "MAX_NOTE_LEN",
    "ActivityItemOut",
    "ActivityRelation",
    "ActivityState",
    "QuoteOut",
    "TradeNoteIn",
    "TradeOut",
    "TradeTxIn",
]
