"""Public client configuration (`GET /config`) and the USD->TRY rate (`GET /fx`, `GET /fx/convert`).

02-api-sozlesme §4 / 03-backend-tasarim §6.8. Mounted under the API prefix by app.main; listed before
`meta` in ROUTER_MODULES (Starlette matches routes in registration order).
"""
from __future__ import annotations

import logging
import time
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.api_deps import DB, SettingsDep
from app.core.config import Settings
from app.models import Asset
from app.schemas.assets import (
    AssetOut,
    AuthConfigOut,
    ChainConfigOut,
    ConfigOut,
    ContractConfigOut,
    ContractLimits,
    ContractsOut,
    FxConvertOut,
    FxOut,
)
from app.services import fx as fx_service
from app.services.chain import get_chain
from app.services.chain.addresses import checksum

log = logging.getLogger("app.config")

router = APIRouter(tags=["config"])

API_VERSION = "2.0.0"
CONTRACT_CONFIG_TTL = 60.0
_contract_cache: tuple[float, ContractConfigOut | None] = (0.0, None)


def _checksum_or_none(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return checksum(value)
    except Exception:  # keep /config alive on a malformed .env value
        return value


def _contract_out(view: Any) -> ContractConfigOut:
    return ContractConfigOut(
        owner=checksum(view.owner),
        router=checksum(view.router),
        platform_fee_bps=int(view.platform_fee_bps),
        fee_recipient=checksum(view.fee_recipient),
        paused=bool(view.paused),
        settle_slippage_bps=int(view.settle_slippage_bps),
    )


async def read_contract_config(chain: Any, settings: Settings) -> tuple[ContractConfigOut | None, str | None]:
    """Live vault `getConfig()`; cached 60 s in-process so client start-ups do not hammer the RPC.
    Never raises: (config, None) on success, (None, error) otherwise."""
    global _contract_cache
    if not settings.vault_address:
        return None, "contract_not_configured: VAULT_ADDRESS is not set"
    ts, cfg = _contract_cache
    if cfg is not None and time.monotonic() - ts < CONTRACT_CONFIG_TTL:
        return cfg, None
    try:
        view = await chain.read_config()
        cfg = _contract_out(view)
    except Exception as e:
        log.warning("vault read_config failed: %s: %s", e.__class__.__name__, str(e)[:200])
        return None, f"{e.__class__.__name__}: {str(e)[:200]}"
    _contract_cache = (time.monotonic(), cfg)
    return cfg, None


def reset_contract_cache() -> None:
    """Test hook."""
    global _contract_cache
    _contract_cache = (0.0, None)


@router.get("/config", response_model=ConfigOut)
async def public_config(db: DB, settings: SettingsDep, chain: Any = Depends(get_chain)) -> ConfigOut:
    """Everything the client needs at start-up: chain parameters, contract addresses, allow-listed tokens,
    fee bps (live from the vault), SIWE parameters and indicative USD prices."""
    assets = (
        await db.execute(
            select(Asset)
            .where(Asset.chain_id == settings.chain_id, Asset.is_active.is_(True))
            .order_by(Asset.is_base_allowed.desc(), Asset.symbol.asc())
        )
    ).scalars().all()
    contract, err = await read_contract_config(chain, settings)
    default_base = next((a for a in assets if a.symbol == settings.default_base_asset_code), None)
    symbols = sorted({a.symbol for a in assets} | {settings.native_symbol})
    return ConfigOut(
        version=API_VERSION,
        api_prefix=settings.api_prefix,
        chain=ChainConfigOut(
            chain_id=settings.chain_id,
            name=settings.chain_name,
            rpc_url=settings.rpc_url,
            ws_url=settings.ws_url,
            explorer_url=settings.explorer_url,
            native_symbol=settings.native_symbol,
            native_decimals=settings.native_decimals,
            faucet_url=settings.faucet_url,
        ),
        contracts=ContractsOut(
            vault=_checksum_or_none(settings.vault_address),
            router=_checksum_or_none(settings.router_address),
            multicall3=_checksum_or_none(settings.multicall3_address),
        ),
        assets=[AssetOut.model_validate(a) for a in assets],
        default_base_asset_code=settings.default_base_asset_code,
        default_base_asset_id=default_base.id if default_base is not None else None,
        platform_fee_bps=contract.platform_fee_bps if contract else None,
        settle_slippage_bps=contract.settle_slippage_bps if contract else settings.settle_slippage_bps,
        default_trade_slippage_bps=settings.default_trade_slippage_bps,
        tx_submit_timeout_seconds=settings.tx_submit_timeout_seconds,
        pending_tx_ttl_seconds=settings.pending_tx_ttl_seconds,
        limits=ContractLimits(),
        contract=contract,
        contract_error=err,
        auth=AuthConfigOut(
            siwe_domain=settings.siwe_domain,
            siwe_uri=settings.siwe_uri,
            siwe_statement=settings.siwe_statement,
            nonce_ttl_seconds=settings.auth_nonce_ttl_seconds,
            access_token_ttl_seconds=settings.access_token_ttl_seconds,
            refresh_max_age_seconds=settings.refresh_max_age_seconds,
        ),
        fx_cache_seconds=settings.fx_cache_seconds,
        usd_prices=fx_service.usd_price_map(symbols),
    )


@router.get("/fx", response_model=FxOut)
async def fx(db: DB, settings: SettingsDep) -> FxOut:
    """USD->TRY (cached `fx_cache_seconds`; primary/secondary sources; persisted fallback) plus the
    indicative USD price map (keyed by symbol) used for TL display."""
    rate = await fx_service.get_usd_try(settings, db)
    symbols = (await db.execute(select(Asset.symbol).where(Asset.chain_id == settings.chain_id).distinct())).scalars()
    return FxOut(
        rate=rate.rate,
        source=rate.source,
        fetched_at=rate.fetched_at,
        stale=rate.stale,
        cache_seconds=settings.fx_cache_seconds,
        usd_prices=fx_service.usd_price_map(
            sorted(set(symbols) | set(fx_service.INDICATIVE_USD_PRICES) | {settings.native_symbol})
        ),
        note=fx_service.INDICATIVE_NOTE,
    )


@router.get("/fx/convert", response_model=FxConvertOut)
async def fx_convert(
    db: DB,
    settings: SettingsDep,
    amount_usd: Annotated[Decimal, Query(ge=0, decimal_places=18, max_digits=60)],
) -> FxConvertOut:
    rate = await fx_service.get_usd_try(settings, db)
    return FxConvertOut(
        amount_usd=amount_usd,
        amount_try=fx_service.to_try(amount_usd, rate),
        rate=rate.rate,
        stale=rate.stale,
    )
