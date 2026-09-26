"""Trader statistics maintained by the indexer / reconciler (03-backend-tasarim §5.5).

`refresh_trader_stats(db, trader_id)` recomputes the `users` stats columns of one trader from the agreements
mirror: active agreement count, managed capital, total / monthly return, win rate and max drawdown (all bps).
Called after `Activated`, `Cancelled`, `Settled` and status resyncs; flush only.

`max_drawdown_bps` is the worst principal-to-value loss across agreements (known simplification, Sprint 3).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Agreement, AgreementStatus, User
from app.services.chain import amounts as money

STATS_DECIMALS = 18  # Numeric(78,18) columns
MONTH = timedelta(days=30)


async def refresh_trader_stats(db: AsyncSession, trader_id: uuid.UUID) -> None:
    """Recompute the indexer-maintained `users` stats columns of a trader from the agreements mirror."""
    trader = await db.get(User, trader_id)
    if trader is None:
        return
    active_count, active_capital = (
        await db.execute(
            select(func.count(), func.coalesce(func.sum(Agreement.principal), 0)).where(
                Agreement.trader_id == trader_id, Agreement.status == AgreementStatus.active
            )
        )
    ).one()
    rows = (
        await db.execute(
            select(
                Agreement.status,
                Agreement.principal,
                Agreement.final_value,
                Agreement.current_value,
                Agreement.settled_at,
            ).where(
                Agreement.trader_id == trader_id,
                Agreement.status.in_([AgreementStatus.active, AgreementStatus.settled]),
            )
        )
    ).all()
    month_ago = datetime.now(UTC) - MONTH
    total_p = total_v = month_p = month_v = Decimal("0")
    wins = settled = 0
    max_dd = 0
    for status, principal, final_value, current_value, settled_at in rows:
        principal = Decimal(principal)
        if status is AgreementStatus.settled and final_value is not None:
            value = Decimal(final_value)
        else:
            value = Decimal(current_value if current_value is not None else principal)
        total_p += principal
        total_v += value
        if status is AgreementStatus.settled:
            settled += 1
            wins += int(value > principal)
            if settled_at is not None and settled_at >= month_ago:
                month_p += principal
                month_v += value
        elif status is AgreementStatus.active:
            month_p += principal
            month_v += value
        if principal > 0 and value < principal:
            max_dd = max(max_dd, int((principal - value) * money.BPS_DENOM / principal))
    trader.active_agreements = int(active_count or 0)
    trader.managed_capital = money.quantize(Decimal(active_capital or 0), STATS_DECIMALS)
    trader.total_return_bps = int((total_v - total_p) * money.BPS_DENOM / total_p) if total_p > 0 else 0
    trader.monthly_return_bps = int((month_v - month_p) * money.BPS_DENOM / month_p) if month_p > 0 else 0
    trader.win_rate_bps = int(wins * money.BPS_DENOM / settled) if settled else 0
    trader.max_drawdown_bps = max_dd
    await db.flush()


__all__ = ["STATS_DECIMALS", "refresh_trader_stats"]
