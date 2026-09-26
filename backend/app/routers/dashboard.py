"""`GET /dashboard` — role-aware aggregates (Figma 3a customer "Panel", 5c trader "Panel").

For a customer the wallet's base-asset balance is read live from the chain (`balanceOf` through the gateway);
an unreachable RPC degrades to `wallet_balance = null` + `wallet_error` instead of failing the panel.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter

from app.api_deps import DB, ChainDep, CurrentUser, SettingsDep
from app.core.errors import AppError
from app.schemas.dashboard import DashboardOut
from app.services import dashboard as dashboard_service
from app.services.chain.amounts import from_raw

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
log = logging.getLogger(__name__)


@router.get("", response_model=DashboardOut)
async def dashboard(user: CurrentUser, db: DB, settings: SettingsDep, chain=ChainDep) -> DashboardOut:
    base = await dashboard_service.preferred_base_asset(db, settings, user)
    code = base.symbol if base is not None else settings.default_base_asset_code
    if user.is_trader:
        return await dashboard_service.trader_dashboard(db, user, base_asset_code=code)

    balance = None
    error: str | None = None
    if base is None:
        error = "base_asset_unavailable"
    else:
        try:
            raw = await chain.token_balance(base.address, user.wallet_address)
            balance = from_raw(int(raw), base.decimals)
        except AppError as e:
            error = e.code
            log.warning("dashboard wallet balance unavailable user=%s: %s", user.id, e.message)
        except Exception as e:  # noqa: BLE001 - RPC/transport failures must not break the panel
            error = e.__class__.__name__
            log.warning("dashboard wallet balance unavailable user=%s: %r", user.id, e)
    return await dashboard_service.customer_dashboard(
        db, user, base_asset_code=code, wallet_balance=balance, wallet_error=error
    )
