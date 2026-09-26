"""Operator endpoints, all behind the X-Admin-Key header (app.api_deps.AdminGuard): stats, users,
assets (+ sync with the vault allow-list), agreements, indexer status/reset and vault admin
transactions (calldata for the owner wallet + receipt check of the reported hash) — 02-api §3.6, §5.3, §10."""
from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, or_, select

from app.api_deps import DB, AdminGuard, ChainDep, SettingsDep
from app.api_paging import PageDep
from app.core.errors import NotFoundError, ValidationError
from app.models import Agreement, AgreementStatus, Asset, User, UserRole
from app.schemas.admin import (
    AdminAgreementOut,
    AdminAssetCreateIn,
    AdminAssetUpdateIn,
    AdminStatsOut,
    AdminTxOut,
    AdminTxSubmitIn,
    AdminTxSubmitOut,
    AdminUserOut,
    AdminUserUpdateIn,
    AssetSyncOut,
    IndexerResetIn,
    IndexerStateOut,
    IndexerStatusOut,
    SetFeesIn,
    SetPausedIn,
    SetRouterIn,
    SetSettleSlippageIn,
    SetTokenIn,
)
from app.schemas.assets import AssetOut, ContractConfigOut
from app.schemas.common import Page
from app.services import admin as admin_service
from app.services import admin_tx

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[AdminGuard])


def _like_pattern(q: str) -> str:
    escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# --- stats -------------------------------------------------------------------------------------------


@router.get("/stats", response_model=AdminStatsOut)
async def stats(db: DB, settings: SettingsDep) -> AdminStatsOut:
    return await admin_service.stats(db, settings)


# --- users -------------------------------------------------------------------------------------------


@router.get("/users", response_model=Page[AdminUserOut])
async def list_users(
    db: DB,
    page: PageDep,
    q: Annotated[str | None, Query(max_length=80, description="username / display name / wallet address")] = None,
    role: Annotated[UserRole | None, Query()] = None,
    is_active: Annotated[bool | None, Query()] = None,
) -> Page[AdminUserOut]:
    where = []
    if q and q.strip():
        pattern = _like_pattern(q)
        where.append(
            or_(
                User.username.ilike(pattern, escape="\\"),
                User.display_name.ilike(pattern, escape="\\"),
                User.wallet_address.ilike(pattern.lower(), escape="\\"),  # stored lower-case (K11)
            )
        )
    if role is not None:
        where.append(User.role == role)
    if is_active is not None:
        where.append(User.is_active.is_(is_active))
    total = int((await db.execute(select(func.count()).select_from(User).where(*where))).scalar_one())
    rows = (
        await db.execute(
            select(User).where(*where).order_by(User.created_at.desc(), User.id).limit(page.limit).offset(page.offset)
        )
    ).scalars()
    return Page[AdminUserOut](
        items=[AdminUserOut.model_validate(u) for u in rows], total=total, limit=page.limit, offset=page.offset
    )


@router.get("/users/{user_id}", response_model=AdminUserOut)
async def get_user(user_id: uuid.UUID, db: DB) -> AdminUserOut:
    user = await db.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found", code="user_not_found")
    return AdminUserOut.model_validate(user)


@router.patch("/users/{user_id}", response_model=AdminUserOut)
async def update_user(user_id: uuid.UUID, body: AdminUserUpdateIn, db: DB) -> AdminUserOut:
    """`is_active` / `is_admin`; `role` is immutable (422 `role_change_forbidden`, BE-25)."""
    user = await db.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found", code="user_not_found")
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if "role" in changes:
        raise ValidationError("a user's role cannot be changed", code="role_change_forbidden")
    for field, value in changes.items():
        setattr(user, field, value)
    if changes:
        await db.flush()
        await db.refresh(user)
        log.info("admin updated user %s: %s", user.id, sorted(changes))
    return AdminUserOut.model_validate(user)


# --- assets ------------------------------------------------------------------------------------------------


@router.get("/assets", response_model=list[AssetOut])
async def list_assets(db: DB, settings: SettingsDep) -> list[AssetOut]:
    """All rows of the configured chain, inactive included."""
    rows = (
        await db.execute(
            select(Asset).where(Asset.chain_id == settings.chain_id).order_by(Asset.is_active.desc(), Asset.symbol)
        )
    ).scalars()
    return [AssetOut.model_validate(a) for a in rows]


@router.post("/assets", response_model=AssetOut, status_code=201)
async def create_asset(body: AdminAssetCreateIn, db: DB, settings: SettingsDep, chain=ChainDep) -> AssetOut:
    """Register an ERC-20; `decimals` is verified against the token's `decimals()` (422 `decimals_mismatch`)."""
    asset = await admin_service.create_asset(db, settings, chain, body)
    return AssetOut.model_validate(asset)


@router.patch("/assets/{asset_id}", response_model=AssetOut)
async def update_asset(asset_id: uuid.UUID, body: AdminAssetUpdateIn, db: DB) -> AssetOut:
    asset = await db.get(Asset, asset_id)
    if asset is None:
        raise NotFoundError("Asset not found", code="asset_not_found")
    changes = body.model_dump(exclude_unset=True)
    for key in ("name", "category", "is_active", "is_base_allowed"):
        if key in changes and changes[key] is None:
            changes.pop(key)  # NOT NULL columns: ignore explicit null
    for field, value in changes.items():
        setattr(asset, field, value)
    if changes:
        await db.flush()
        await db.refresh(asset)
        log.info("admin updated asset %s: %s", asset.symbol, sorted(changes))
    return AssetOut.model_validate(asset)


@router.post("/assets/sync-onchain", response_model=AssetSyncOut)
async def sync_assets_onchain(db: DB, settings: SettingsDep, chain=ChainDep) -> AssetSyncOut:
    """Read `isTokenAllowed` from the vault for every asset of the chain and mirror it into `assets.onchain_allowed`."""
    return await admin_service.sync_assets_onchain(db, settings, chain)


# --- agreements ---------------------------------------------------------------------------------------------


@router.get("/agreements", response_model=Page[AdminAgreementOut])
async def list_agreements(
    db: DB,
    page: PageDep,
    status: Annotated[AgreementStatus | None, Query()] = None,
    customer_id: Annotated[uuid.UUID | None, Query()] = None,
    trader_id: Annotated[uuid.UUID | None, Query()] = None,
    onchain_id: Annotated[int | None, Query(ge=0)] = None,
) -> Page[AdminAgreementOut]:
    where = []
    if status is not None:
        where.append(Agreement.status == status)
    if customer_id is not None:
        where.append(Agreement.customer_id == customer_id)
    if trader_id is not None:
        where.append(Agreement.trader_id == trader_id)
    if onchain_id is not None:
        where.append(Agreement.onchain_id == onchain_id)
    total = int((await db.execute(select(func.count()).select_from(Agreement).where(*where))).scalar_one())
    rows = (
        await db.execute(
            select(Agreement)
            .where(*where)
            .order_by(Agreement.created_at.desc(), Agreement.id)
            .limit(page.limit)
            .offset(page.offset)
        )
    ).scalars()
    return Page[AdminAgreementOut](
        items=[AdminAgreementOut.model_validate(a) for a in rows], total=total, limit=page.limit, offset=page.offset
    )


# --- indexer ---------------------------------------------------------------------------------------------------


@router.get("/indexer", response_model=IndexerStatusOut)
async def indexer_status(db: DB, settings: SettingsDep, chain=ChainDep) -> IndexerStatusOut:
    return await admin_service.indexer_status(db, settings, chain)


@router.post("/indexer/reset", response_model=IndexerStateOut | None)
async def indexer_reset(body: IndexerResetIn, db: DB) -> IndexerStateOut | None:
    """Move the cursor (`block_number` given) or delete the state row (null) so the indexer re-scans
    from its fallback window."""
    return await admin_service.indexer_reset(db, body.key, body.block_number)


# --- vault contract admin ----------------------------------------------------------------------------------------


@router.get("/contract/config", response_model=ContractConfigOut)
async def contract_config(chain=ChainDep) -> ContractConfigOut:
    """Live `getConfig()` + `owner()` of the vault (502 `chain_error` when unreachable)."""
    return await admin_service.contract_config(chain)


@router.post("/contract/tx/set_token", response_model=AdminTxOut)
async def tx_set_token(body: SetTokenIn, db: DB, settings: SettingsDep, chain=ChainDep) -> AdminTxOut:
    """Allow-list (or de-list) a token on the vault; `token` is an asset id or a 0x address."""
    token = await admin_tx.resolve_token(db, settings, body.token)
    return await admin_tx.build_admin_tx(
        settings, chain, "set_token", {"token": token, "allowed": body.allowed, "is_base": body.is_base}
    )


@router.post("/contract/tx/set_paused", response_model=AdminTxOut)
async def tx_set_paused(body: SetPausedIn, settings: SettingsDep, chain=ChainDep) -> AdminTxOut:
    return await admin_tx.build_admin_tx(settings, chain, "set_paused", {"paused": body.paused})


@router.post("/contract/tx/set_fees", response_model=AdminTxOut)
async def tx_set_fees(body: SetFeesIn, settings: SettingsDep, chain=ChainDep) -> AdminTxOut:
    return await admin_tx.build_admin_tx(
        settings, chain, "set_fees", {"platform_fee_bps": body.platform_fee_bps, "fee_recipient": body.fee_recipient}
    )


@router.post("/contract/tx/set_router", response_model=AdminTxOut)
async def tx_set_router(body: SetRouterIn, settings: SettingsDep, chain=ChainDep) -> AdminTxOut:
    """`proposeRouter(router)` — step 1 of the two-step router change (SC-08); `apply_router` after the delay."""
    return await admin_tx.build_admin_tx(settings, chain, "set_router", {"router": body.router})


@router.post("/contract/tx/apply_router", response_model=AdminTxOut)
async def tx_apply_router(settings: SettingsDep, chain=ChainDep) -> AdminTxOut:
    """`applyRouter()` — step 2 of the router change once `routerActivationTime` has passed."""
    return await admin_tx.build_admin_tx(settings, chain, "apply_router", {})


@router.post("/contract/tx/set_settle_slippage", response_model=AdminTxOut)
async def tx_set_settle_slippage(body: SetSettleSlippageIn, settings: SettingsDep, chain=ChainDep) -> AdminTxOut:
    return await admin_tx.build_admin_tx(settings, chain, "set_settle_slippage", {"bps": body.bps})


@router.post("/contract/tx/submit", response_model=AdminTxSubmitOut)
async def tx_submit(body: AdminTxSubmitIn, settings: SettingsDep, chain=ChainDep) -> AdminTxSubmitOut:
    """Wait for the receipt of an admin transaction the owner wallet sent; checks `to == vault`, `from == owner`."""
    return await admin_tx.submit_admin_tx(settings, chain, body.tx_hash)


__all__ = ["router"]
