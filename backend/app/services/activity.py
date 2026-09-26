"""Activity feed (Figma 3b/3c "Hareketler"): trades of the traders the user follows and of the
agreements the user is party to, newest first, with trader / open|closed filters."""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models import Agreement, AgreementStatus, Follow, Trade, User
from app.schemas.trades import ActivityItemOut, ActivityState
from app.services.agreements import CLOSED_STATUSES, OPEN_STATUSES, party_out
from app.services.trading import trade_out

log = logging.getLogger(__name__)


async def list_activity(
    db: AsyncSession,
    user: User,
    *,
    trader_id: uuid.UUID | None = None,
    state: ActivityState | None = None,
    limit: int = 20,
    offset: int = 0,
    settings: Settings | None = None,
) -> tuple[list[ActivityItemOut], int]:
    """Visible trades: every trade of agreements the user is party to, plus trades flagged
    `notify_investors` of the traders the user follows."""
    followed = select(Follow.trader_id).where(Follow.follower_id == user.id)
    is_party = or_(Agreement.customer_id == user.id, Agreement.trader_id == user.id)
    is_followed = and_(Agreement.trader_id.in_(followed), Trade.notify_investors.is_(True))
    where = [or_(is_party, is_followed)]
    if trader_id is not None:
        where.append(Agreement.trader_id == trader_id)
    if state == "open":
        where.append(Agreement.status.in_(list(OPEN_STATUSES)))
    elif state == "closed":
        where.append(Agreement.status.in_(list(CLOSED_STATUSES)))
    base = select(Trade, Agreement).join(Agreement, Agreement.id == Trade.agreement_id).where(*where)
    total = int((await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one())
    rows = (await db.execute(base.order_by(Trade.created_at.desc(), Trade.id.desc()).limit(limit).offset(offset))).all()
    explorer = settings.explorer_url if settings is not None else None
    items: list[ActivityItemOut] = []
    for trade, ag in rows:
        party = user.id in (ag.customer_id, ag.trader_id)
        items.append(
            ActivityItemOut(
                trade=trade_out(trade, ag, explorer_url=explorer),
                agreement_status=AgreementStatus(ag.status),
                base_asset_code=ag.base_asset.symbol,
                trader=party_out(ag.trader),
                customer=party_out(ag.customer) if party else None,
                relation="party" if party else "following",
            )
        )
    return items, total


__all__ = ["list_activity"]
