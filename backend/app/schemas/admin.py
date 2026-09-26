"""Admin I/O models (X-Admin-Key protected endpoints; 02-api-sozlesme §3.6, §5.3, §8.5, §10)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AgreementStatus, MarketCategory, UserRole
from app.schemas.common import Address, AddressOut, Amount, ORMModel, TxHash
from app.schemas.tx import UnsignedTxOut
from app.schemas.users import MeOut

AdminContractFunction = Literal["set_token", "set_paused", "set_fees", "set_router", "apply_router", "set_settle_slippage"]


class IndexerStateOut(ORMModel):
    key: str
    block_number: int | None = None
    last_block_hash: str | None = None
    updated_at: datetime


class AdminStatsOut(BaseModel):
    users_total: int
    users_by_role: dict[str, int]
    users_active: int
    listings_by_status: dict[str, int]
    offers_by_status: dict[str, int]
    agreements_by_status: dict[str, int]
    managed_capital_active: Amount  # sum of principal of active agreements (base asset units, mixed assets)
    trades_total: int
    pending_transactions_by_status: dict[str, int]
    notifications_unread: int
    assets_total: int
    assets_onchain_allowed: int
    indexer: list[IndexerStateOut]
    vault_address: AddressOut | None = None
    chain_id: int
    generated_at: datetime


class AdminUserOut(MeOut):
    pass


class AdminUserUpdateIn(BaseModel):
    role: UserRole | None = Field(default=None, description="kept for compatibility; sending it is rejected (BE-25)")
    is_active: bool | None = None
    is_admin: bool | None = None


class AdminAssetCreateIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    address: Address = Field(description="ERC-20 token address")
    symbol: str = Field(pattern=r"^[A-Za-z0-9]{1,12}$")
    name: str = Field(min_length=1, max_length=60)
    icon_url: str | None = Field(default=None, max_length=500)
    category: MarketCategory = MarketCategory.crypto
    decimals: int = Field(ge=0, le=18, description="verified against the token's decimals()")
    is_active: bool = True
    is_base_allowed: bool = False


class AdminAssetUpdateIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=60)
    icon_url: str | None = Field(default=None, max_length=500)
    category: MarketCategory | None = None
    is_active: bool | None = None
    is_base_allowed: bool | None = None


class AssetSyncRow(BaseModel):
    asset_id: uuid.UUID
    symbol: str
    address: AddressOut
    onchain_allowed: bool | None = None
    onchain_is_base: bool | None = None
    changed: bool = False
    error: str | None = None


class AssetSyncOut(BaseModel):
    vault_address: AddressOut
    checked: int
    changed: int
    rows: list[AssetSyncRow]


class AdminAgreementOut(ORMModel):
    id: uuid.UUID
    onchain_id: int | None = None
    offer_id: uuid.UUID | None = None
    listing_id: uuid.UUID | None = None
    customer_id: uuid.UUID
    trader_id: uuid.UUID
    base_asset_id: uuid.UUID
    vault_address: AddressOut | None = None
    principal: Amount
    duration_secs: int
    commission_bps: int
    max_drawdown_bps: int
    platform_fee_bps: int | None = None
    status: AgreementStatus
    proposer_role: UserRole
    created_tx: str | None = None
    activate_tx: str | None = None
    cancel_tx: str | None = None
    settle_tx: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    current_value: Amount | None = None
    value_updated_at: datetime | None = None
    final_value: Amount | None = None
    profit: Amount | None = None
    trader_fee: Amount | None = None
    platform_fee: Amount | None = None
    customer_payout: Amount | None = None
    settled_at: datetime | None = None
    last_event_block_number: int | None = None
    created_at: datetime
    updated_at: datetime


class IndexerStatusOut(BaseModel):
    states: list[IndexerStateOut]
    latest_block: int | None = None
    lag_blocks: int | None = Field(default=None, description="latest_block − vault_events.block_number")
    confirmations: int
    vault_address: AddressOut | None = None
    rpc_error: str | None = None


class IndexerResetIn(BaseModel):
    key: str = Field(default="vault_events", max_length=64)
    block_number: int | None = Field(default=None, ge=0, description="restart from this block (null = delete the row)")


# --- contract admin transactions ----------------------------------------------------------------------------


class SetTokenIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    token: str = Field(description="asset uuid or 0x token address")
    allowed: bool = True
    is_base: bool = False


class SetPausedIn(BaseModel):
    paused: bool


class SetFeesIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    platform_fee_bps: int = Field(ge=0, le=1_000)
    fee_recipient: Address | None = Field(default=None, description="0x address (default: PLATFORM_ADDRESS)")


class SetRouterIn(BaseModel):
    router: Address


class SetSettleSlippageIn(BaseModel):
    bps: int = Field(ge=0, le=5_000)


class AdminTxOut(UnsignedTxOut):
    """`UnsignedTxOut` + the admin call it encodes; `pending_tx_id` is null, `from_address` = contract owner."""

    function: str
    args: dict[str, Any]
    vault_address: AddressOut
    note: str = "Sign with the owner wallet and POST /admin/contract/tx/submit {tx_hash}"


class AdminTxSubmitIn(BaseModel):
    tx_hash: TxHash


class AdminTxSubmitOut(BaseModel):
    tx_hash: str
    status: Literal["submitted", "confirmed", "failed"]
    block_number: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    explorer_url: str | None = None


__all__ = [
    "AdminAgreementOut",
    "AdminAssetCreateIn",
    "AdminAssetUpdateIn",
    "AdminContractFunction",
    "AdminStatsOut",
    "AdminTxOut",
    "AdminTxSubmitIn",
    "AdminTxSubmitOut",
    "AdminUserOut",
    "AdminUserUpdateIn",
    "AssetSyncOut",
    "AssetSyncRow",
    "IndexerResetIn",
    "IndexerStateOut",
    "IndexerStatusOut",
    "SetFeesIn",
    "SetPausedIn",
    "SetRouterIn",
    "SetSettleSlippageIn",
    "SetTokenIn",
]
