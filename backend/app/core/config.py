"""Application settings (environment driven) — Monad edition (03-backend-tasarim §2).

All secrets come from the environment / .env file. Nothing here is hard-coded for a specific deployment except
sensible Monad Testnet defaults (06-monad-testnet.md). `minter_private_key`, `jwt_secret` and `admin_key` are never
written to logs or `repr()`.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

MONAD_TESTNET_CHAIN_ID = 10143
MONAD_MAINNET_CHAIN_ID = 143

_PRIVATE_KEY_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_SECRET_FIELDS = frozenset({"jwt_secret", "admin_key", "minter_private_key"})


def _normalize_address(value: object) -> str | None:
    """Lower-case a `0x…` address; empty / None -> None. Mixed-case input is only lowercased (checksum validated by
    `app.services.chain.addresses.normalize` at use time; here we avoid importing the chain package)."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("address must be a string")
    v = value.strip()
    if not v:
        return None
    if not _ADDRESS_RE.match(v):
        raise ValueError(f"invalid EVM address: {v!r}")
    return v.lower()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- app -------------------------------------------------------------
    app_name: str = "TraderKirala API"
    app_env: Literal["dev", "test", "prod"] = "prod"
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"
    # NoDecode: pydantic-settings would otherwise JSON-decode the env string; we split on commas ourselves.
    cors_origins: Annotated[list[str], NoDecode] = ["*"]  # prod .env narrows this (BE-25)
    docs_enabled: bool = True
    # Google Play herkese acik, calisan bir iletisim adresi sart; hukuki sayfalarda gosterilir.
    legal_contact_email: str | None = None

    # --- database ----------------------------------------------------------
    database_url: str = Field(
        default="postgresql+asyncpg://trader:trader@db:5432/traderkirala",
        description="SQLAlchemy async URL (asyncpg driver).",
    )
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_echo: bool = False

    # --- auth (SIWE, §3) ---------------------------------------------------
    jwt_secret: str = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 60 * 60 * 24 * 7  # 7 days (mobile friendly)
    jwt_absolute_ttl_days: int = 30  # refresh window; see `refresh_max_age_seconds`
    auth_nonce_ttl_seconds: int = 300
    auth_nonce_rate_limit_per_minute: int = 20  # per client IP
    siwe_domain: str = "monadback.yolalapp.com"  # also the JWT `iss`
    siwe_uri: str = "https://monadback.yolalapp.com"
    siwe_statement: str = "TraderKirala'ya giriş yap. Bu imza işlem yapmaz, gas harcamaz."

    admin_key: str = Field(min_length=16, description="Shared secret for X-Admin-Key admin endpoints")

    # --- chain (06-monad-testnet) ------------------------------------------
    chain_id: int = MONAD_TESTNET_CHAIN_ID
    chain_name: str = "Monad Testnet"
    rpc_url: str = "https://testnet-rpc.monad.xyz"
    ws_url: str | None = None
    explorer_url: str = "https://testnet.monadvision.com"
    faucet_url: str = "https://faucet.monad.xyz"  # native MON faucet link
    native_symbol: str = "MON"
    native_decimals: int = 18
    rpc_timeout_seconds: int = 10
    rpc_max_rps: int = 12
    rpc_call_max_rps: int = 8  # eth_call / estimateGas budget (QuickNode: 25 rps)
    rpc_retries: int = 3

    # --- contracts ----------------------------------------------------------
    vault_address: str | None = None  # TraderVault proxy; None -> indexer/reconciler skip, build 503 contract_not_configured
    router_address: str | None = None  # MockRouter
    multicall3_address: str | None = "0xcA11bde05977b3631167028862bE2a173976CA11"
    platform_address: str | None = None  # fee recipient default (set_fees); replaces PLATFORM_SECRET
    default_base_asset_code: str = "tUSDC"  # asset symbol
    settle_slippage_bps: int = 100  # min_out = quote × (1 − bps) when the backend builds `settle`
    default_trade_slippage_bps: int = 100
    deployments_file: str = "deployments/monad-testnet.json"  # relative to the backend root
    assets_json: str | None = None  # fallback when the deployments JSON is absent: [{symbol,address,decimals,isBase}]

    # --- indexer / pending tracker (§4, §5) ------------------------------------
    confirmations: int = 2
    indexer_block_window: int = 2000
    indexer_start_block: int | None = None  # None -> deployments JSON blockNumber -> latest - 5000
    indexer_max_windows_per_run: int = 10
    indexer_poll_seconds: int = 5
    pending_tracker_seconds: int = 5
    submitted_tx_timeout_minutes: int = 30  # submitted -> failed(not_included)
    reconcile_seconds: int = 60
    worker_offer_expiry_seconds: int = 60

    # --- transactions ---------------------------------------------------------
    pending_tx_ttl_seconds: int = 900  # UnsignedTx.expires_at
    tx_submit_timeout_seconds: int = 20  # POST /tx/submit waits for the receipt up to this long

    # --- faucet (K7) ---------------------------------------------------------
    minter_private_key: str | None = None  # TestToken MINTER_ROLE key; None -> /wallet/faucet 503 faucet_disabled
    faucet_amounts: Annotated[dict[str, str], NoDecode] = {  # symbol -> human-readable amount (env: JSON)
        "tUSDC": "1000",
        "tWETH": "0.5",
        "tWBTC": "0.02",
    }
    faucet_daily_limit: int = 1  # claims per user+asset per day

    # --- FX (USD -> TRY for TL display) -----------------------------------------------------
    fx_primary_url: str = "https://open.er-api.com/v6/latest/USD"
    fx_secondary_url: str = "https://api.frankfurter.dev/v1/latest?base=USD&symbols=TRY"
    fx_cache_seconds: int = 600
    usd_prices_json: str | None = None  # override of the indicative USD prices, {"tUSDC":"1",...}
    mon_usd_price: Decimal | None = None  # BE-14: fixed or disabled (None)

    # --- push notifications (Expo) -------------------------------------
    expo_push_enabled: bool = False
    expo_push_url: str = "https://exp.host/--/api/v2/push/send"
    expo_access_token: str | None = None

    # --- validators -----------------------------------------------------------
    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v):  # noqa: ANN001
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @field_validator("vault_address", "router_address", "platform_address", "multicall3_address", mode="before")
    @classmethod
    def _lower_address(cls, v):  # noqa: ANN001
        return _normalize_address(v)

    @field_validator("ws_url", "expo_access_token", "assets_json", "usd_prices_json", "legal_contact_email", mode="before")
    @classmethod
    def _empty_to_none(cls, v):  # noqa: ANN001
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("indexer_start_block", "mon_usd_price", mode="before")
    @classmethod
    def _empty_number_to_none(cls, v):  # noqa: ANN001
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("minter_private_key", mode="before")
    @classmethod
    def _check_private_key(cls, v):  # noqa: ANN001
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("minter_private_key must be a string")
        v = v.strip()
        if not v:
            return None
        if not _PRIVATE_KEY_RE.match(v):
            raise ValueError("minter_private_key must be 0x + 64 hex characters")
        return v

    @field_validator("faucet_amounts", mode="before")
    @classmethod
    def _parse_faucet_amounts(cls, v):  # noqa: ANN001
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return {}
            v = json.loads(v)
        if not isinstance(v, dict):
            raise ValueError("faucet_amounts must be a JSON object {symbol: amount}")
        return {str(k): str(val) for k, val in v.items()}

    # --- derived -----------------------------------------------------------
    @property
    def refresh_max_age_seconds(self) -> int:
        return self.jwt_absolute_ttl_days * 86_400

    @property
    def usd_prices(self) -> dict[str, str]:
        """Indicative USD prices keyed by symbol (fx.py may extend/override)."""
        if self.usd_prices_json:
            data = json.loads(self.usd_prices_json)
            return {str(k): str(v) for k, v in data.items()}
        return {"tUSDC": "1", "tWETH": "3000", "tWBTC": "60000"}

    @property
    def is_mainnet(self) -> bool:
        return self.chain_id == MONAD_MAINNET_CHAIN_ID

    def __repr__(self) -> str:
        parts = []
        for name in type(self).model_fields:
            value = "***" if name in _SECRET_FIELDS else repr(getattr(self, name))
            parts.append(f"{name}={value}")
        return f"Settings({', '.join(parts)})"

    __str__ = __repr__


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
