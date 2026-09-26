"""Allow-listed tokens for the configured chain (public; 02-api-sozlesme §5)."""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api_deps import DB, SettingsDep
from app.core.errors import NotFoundError
from app.models import Asset
from app.schemas.assets import AssetOut

router = APIRouter(prefix="/assets", tags=["assets"])


def _order(q):  # noqa: ANN001
    return q.order_by(Asset.is_base_allowed.desc(), Asset.symbol.asc())


@router.get("", response_model=list[AssetOut])
async def list_assets(
    db: DB,
    settings: SettingsDep,
    base_only: Annotated[bool, Query(description="only tokens usable as agreement base asset")] = False,
    onchain_only: Annotated[bool, Query(description="only tokens the vault contract currently allows")] = False,
) -> list[AssetOut]:
    """Active assets on the configured chain (`settings.chain_id`), base-eligible ones first."""
    q = select(Asset).where(Asset.chain_id == settings.chain_id, Asset.is_active.is_(True))
    if base_only:
        q = q.where(Asset.is_base_allowed.is_(True))
    if onchain_only:
        q = q.where(Asset.onchain_allowed.is_(True))
    rows = (await db.execute(_order(q))).scalars()
    return [AssetOut.model_validate(a) for a in rows]


@router.get("/{asset_id}", response_model=AssetOut)
async def get_asset(asset_id: uuid.UUID, db: DB, settings: SettingsDep) -> AssetOut:
    asset = await db.get(Asset, asset_id)
    if asset is None or asset.chain_id != settings.chain_id:
        raise NotFoundError("Asset not found", code="asset_not_found")
    return AssetOut.model_validate(asset)
