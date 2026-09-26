"""Asset, FX and public-config I/O models (02-api-sozlesme §4, §5)."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, computed_field

from app.models.enums import MarketCategory
from app.schemas.common import Amount, ORMModel


class AssetOut(ORMModel):
    """Allow-listed ERC-20 on the configured chain (02-api §5.1).

    `symbol` is canonical; `code` carries the same value for backwards compatibility (deprecated).
    MON never appears in the assets table, so `is_native` is always False (K6).
    """

    id: uuid.UUID
    chain_id: int
    address: str  # ERC-20 address (checksum)
    symbol: str
    name: str
    decimals: int
    category: MarketCategory
    is_base_allowed: bool
    is_active: bool
    onchain_allowed: bool = False
    icon_url: str | None = None
    is_native: bool = False
    created_at: datetime

    @computed_field(deprecated=True)  # type: ignore[prop-decorator]
    @property
    def code(self) -> str:
        return self.symbol


class AssetBriefOut(ORMModel):
    """Compact asset reference (02-api §5.2): `AgreementOut.base_asset`, `BalanceOut.asset`, `TradeOut.token_in/out`."""

    id: uuid.UUID
    symbol: str
    address: str
    decimals: int
    name: str
    icon_url: str | None = None
    category: MarketCategory

    @computed_field(deprecated=True)  # type: ignore[prop-decorator]
    @property
    def code(self) -> str:
        return self.symbol


# --- FX ----------------------------------------------------------------------------------------------


class FxOut(BaseModel):
    pair: str = "USDTRY"
    base: str = "USD"
    quote: str = "TRY"
    rate: Amount
    source: str
    fetched_at: datetime
    stale: bool = False
    cache_seconds: int
    usd_prices: dict[str, Amount | None] = Field(
        default_factory=dict, description="indicative USD price per asset symbol (null = unknown)"
    )
    usd_prices_indicative: bool = True
    note: str


class FxConvertOut(BaseModel):
    amount_usd: Amount
    amount_try: Amount
    rate: Amount
    stale: bool


# --- public config -----------------------------------------------------------------------------------


class ContractLimits(BaseModel):
    """Constants baked into the vault contract (01-kontrat-spec §3.1)."""

    max_tokens: int = 6
    min_duration_days: int = 1
    max_duration_days: int = 3 * 365
    max_commission_bps: int = 5_000
    max_platform_fee_bps: int = 1_000
    min_drawdown_bps: int = 100
    max_drawdown_bps: int = 10_000
    max_settle_slippage_bps: int = 5_000


class ContractConfigOut(BaseModel):
    """Live `getConfig()` + `owner()` of the vault (read through the chain gateway). Addresses checksummed."""

    owner: str
    router: str
    platform_fee_bps: int
    fee_recipient: str
    paused: bool
    settle_slippage_bps: int


class ChainConfigOut(BaseModel):
    chain_id: int
    name: str
    rpc_url: str
    ws_url: str | None = None
    explorer_url: str
    native_symbol: str
    native_decimals: int
    faucet_url: str | None = None


class ContractsOut(BaseModel):
    vault: str | None = Field(default=None, description="TraderVault proxy; null until deployed / configured")
    router: str | None = None
    multicall3: str | None = None


class AuthConfigOut(BaseModel):
    siwe_domain: str
    siwe_uri: str
    siwe_statement: str
    nonce_ttl_seconds: int
    access_token_ttl_seconds: int
    refresh_max_age_seconds: int


class ConfigOut(BaseModel):
    version: str
    api_prefix: str
    chain: ChainConfigOut
    contracts: ContractsOut
    assets: list[AssetOut]
    default_base_asset_code: str
    default_base_asset_id: uuid.UUID | None = None
    platform_fee_bps: int | None = Field(default=None, description="live from the contract; null when unreachable")
    settle_slippage_bps: int
    default_trade_slippage_bps: int
    tx_submit_timeout_seconds: int
    pending_tx_ttl_seconds: int
    limits: ContractLimits
    contract: ContractConfigOut | None = None
    contract_error: str | None = None
    auth: AuthConfigOut
    fx_cache_seconds: int
    usd_prices: dict[str, Decimal | None] = Field(default_factory=dict, description="keyed by symbol")
