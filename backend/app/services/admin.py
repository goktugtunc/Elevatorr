"""Operator helpers behind the X-Admin-Key endpoints (03-backend-tasarim §6.7; 02-api §5.3, §10): platform
stats, allow-list sync with the vault (`is_token_allowed` through the chain gateway), live contract config,
indexer status / reset and asset creation (decimals verified on-chain). Admin transactions live in
`app.services.admin_tx`."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ChainError, ConflictError, NotFoundError, ValidationError
from app.models import (
    Agreement,
    AgreementStatus,
    Asset,
    IndexerState,
    Listing,
    Notification,
    Offer,
    PendingTransaction,
    Trade,
    User,
)
from app.schemas.admin import (
    AdminAssetCreateIn,
    AdminStatsOut,
    AssetSyncOut,
    AssetSyncRow,
    IndexerStateOut,
    IndexerStatusOut,
)
from app.schemas.assets import ContractConfigOut
from app.services.chain.addresses import checksum, normalize
from app.services.chain.amounts import quantize
from app.services.chain.gateway import ChainGateway

log = logging.getLogger(__name__)

VAULT_EVENTS_KEY = "vault_events"


def _enum_counts(rows: list[tuple[Any, int]]) -> dict[str, int]:
    return {str(getattr(k, "value", k)): int(v) for k, v in rows}


async def _count_by(db: AsyncSession, column: Any) -> dict[str, int]:
    return _enum_counts((await db.execute(select(column, func.count()).group_by(column))).all())


def vault_address(chain: ChainGateway | None, settings: Settings) -> str | None:
    """The vault the gateway talks to (lower-case; None when not configured)."""
    v = getattr(chain, "vault_address", None) if chain is not None else None
    return (v if isinstance(v, str) and v else None) or settings.vault_address


async def stats(db: AsyncSession, settings: Settings) -> AdminStatsOut:
    users_by_role = await _count_by(db, User.role)
    users_active = int(
        (await db.execute(select(func.count()).select_from(User).where(User.is_active.is_(True)))).scalar_one()
    )
    managed = (
        await db.execute(
            select(func.coalesce(func.sum(Agreement.principal), 0)).where(Agreement.status == AgreementStatus.active)
        )
    ).scalar_one()
    trades_total = int((await db.execute(select(func.count()).select_from(Trade))).scalar_one())
    unread = int(
        (
            await db.execute(select(func.count()).select_from(Notification).where(Notification.read_at.is_(None)))
        ).scalar_one()
    )
    assets_total, assets_onchain = (
        await db.execute(
            select(func.count(), func.count().filter(Asset.onchain_allowed.is_(True))).where(
                Asset.chain_id == settings.chain_id
            )
        )
    ).one()
    indexer_rows = (await db.execute(select(IndexerState).order_by(IndexerState.key))).scalars().all()
    return AdminStatsOut(
        users_total=sum(users_by_role.values()),
        users_by_role=users_by_role,
        users_active=users_active,
        listings_by_status=await _count_by(db, Listing.status),
        offers_by_status=await _count_by(db, Offer.status),
        agreements_by_status=await _count_by(db, Agreement.status),
        managed_capital_active=quantize(Decimal(managed or 0), 18),
        trades_total=trades_total,
        pending_transactions_by_status=await _count_by(db, PendingTransaction.status),
        notifications_unread=unread,
        assets_total=int(assets_total or 0),
        assets_onchain_allowed=int(assets_onchain or 0),
        indexer=[IndexerStateOut.model_validate(r) for r in indexer_rows],
        vault_address=settings.vault_address,
        chain_id=settings.chain_id,
        generated_at=datetime.now(UTC),
    )


# --- contract reads through the gateway -------------------------------------------------------------------


async def contract_config(chain: ChainGateway) -> ContractConfigOut:
    """Live `getConfig()` + `owner()` of the vault (02-api §4); raises `ChainError` when unreachable."""
    cfg = await chain.read_config()
    return ContractConfigOut(
        owner=checksum(cfg.owner),
        router=checksum(cfg.router),
        platform_fee_bps=int(cfg.platform_fee_bps),
        fee_recipient=checksum(cfg.fee_recipient),
        paused=bool(cfg.paused),
        settle_slippage_bps=int(cfg.settle_slippage_bps),
    )


async def sync_assets_onchain(db: AsyncSession, settings: Settings, chain: ChainGateway) -> AssetSyncOut:
    """Mirror the vault allow-list into `assets.onchain_allowed` (chain-wide, active rows first).
    `is_base_allowed` (the off-chain product flag) is reported as `onchain_is_base` but not overwritten."""
    vault = vault_address(chain, settings)
    if not vault:
        raise ChainError("VAULT_ADDRESS is not configured", code="contract_not_configured")
    rows = (
        await db.execute(
            select(Asset).where(Asset.chain_id == settings.chain_id).order_by(Asset.is_active.desc(), Asset.symbol)
        )
    ).scalars().all()
    out: list[AssetSyncRow] = []
    changed = 0
    for asset in rows:
        try:
            info = await chain.is_token_allowed(asset.address)
        except Exception as e:  # noqa: BLE001 - one bad token must not abort the sync
            out.append(
                AssetSyncRow(asset_id=asset.id, symbol=asset.symbol, address=asset.address, error=f"{e.__class__.__name__}: {str(e)[:160]}")
            )
            continue
        did_change = asset.onchain_allowed != bool(info.allowed)
        if did_change:
            asset.onchain_allowed = bool(info.allowed)
            changed += 1
        out.append(
            AssetSyncRow(
                asset_id=asset.id,
                symbol=asset.symbol,
                address=asset.address,
                onchain_allowed=bool(info.allowed),
                onchain_is_base=bool(info.is_base),
                changed=did_change,
            )
        )
    if changed:
        await db.flush()
    log.info("assets sync-onchain: vault=%s checked=%d changed=%d", vault, len(rows), changed)
    return AssetSyncOut(vault_address=vault, checked=len(rows), changed=changed, rows=out)


async def indexer_status(db: AsyncSession, settings: Settings, chain: ChainGateway) -> IndexerStatusOut:
    states = (await db.execute(select(IndexerState).order_by(IndexerState.key))).scalars().all()
    latest: int | None = None
    rpc_error: str | None = None
    try:
        latest = int(await chain.latest_block())
    except Exception as e:  # noqa: BLE001 - status must render even when RPC is down
        rpc_error = f"{e.__class__.__name__}: {str(e)[:160]}"
    events = next((s for s in states if s.key == VAULT_EVENTS_KEY), None)
    lag = latest - events.block_number if latest is not None and events is not None and events.block_number is not None else None
    return IndexerStatusOut(
        states=[IndexerStateOut.model_validate(s) for s in states],
        latest_block=latest,
        lag_blocks=lag,
        confirmations=settings.confirmations,
        vault_address=vault_address(chain, settings),
        rpc_error=rpc_error,
    )


async def indexer_reset(db: AsyncSession, key: str, block_number: int | None) -> IndexerStateOut | None:
    """`block_number` given -> upsert the row (indexer restarts after that block); null -> delete the row."""
    row = await db.get(IndexerState, key)
    if block_number is None:
        if row is None:
            raise NotFoundError(f"indexer_state {key!r} not found", code="indexer_state_not_found")
        await db.delete(row)
        await db.flush()
        log.info("admin deleted indexer_state %s", key)
        return None
    if row is None:
        row = IndexerState(key=key)
        db.add(row)
    row.block_number = int(block_number)
    row.last_block_hash = None  # unknown until the indexer reads that block again
    row.updated_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(row)
    log.info("admin reset indexer_state %s block_number=%s", key, block_number)
    return IndexerStateOut.model_validate(row)


# --- assets ---------------------------------------------------------------------------------------------------


async def create_asset(db: AsyncSession, settings: Settings, chain: ChainGateway, body: AdminAssetCreateIn) -> Asset:
    """`POST /admin/assets` (02-api §5.3): address normalised, `(chain_id, address)` unique, `decimals`
    verified against the token's `decimals()` (422 `decimals_mismatch`)."""
    address = normalize(body.address)
    dup = (
        await db.execute(select(Asset.id).where(Asset.chain_id == settings.chain_id, Asset.address == address))
    ).scalar_one_or_none()
    if dup is not None:
        raise ConflictError("Asset already exists on this chain", code="asset_exists", details={"asset_id": str(dup)})
    try:
        _symbol, onchain_decimals = await chain.token_metadata(address)
    except ChainError as e:
        raise ChainError(f"token metadata unavailable for {address}: {e.message}", code="chain_error") from e
    if int(onchain_decimals) != int(body.decimals):
        raise ValidationError(
            f"decimals {body.decimals} does not match the token's decimals() = {onchain_decimals}",
            code="decimals_mismatch",
            details={"expected": int(onchain_decimals), "given": int(body.decimals)},
        )
    asset = Asset(
        chain_id=settings.chain_id,
        address=address,
        symbol=body.symbol,
        name=body.name,
        icon_url=body.icon_url,
        category=body.category,
        decimals=int(body.decimals),
        is_active=body.is_active,
        is_base_allowed=body.is_base_allowed,
        onchain_allowed=False,
    )
    db.add(asset)
    await db.flush()
    await db.refresh(asset)
    log.info("admin created asset %s (%s) on chain %s", asset.symbol, asset.address, settings.chain_id)
    return asset


__all__ = [
    "VAULT_EVENTS_KEY",
    "contract_config",
    "create_asset",
    "indexer_reset",
    "indexer_status",
    "stats",
    "sync_assets_onchain",
    "vault_address",
]
