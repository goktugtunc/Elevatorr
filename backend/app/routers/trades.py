"""Trade rows (indexed `Traded` events): the trader's editable note (02-api §8.2)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.api_deps import DB, CurrentUser, SettingsDep
from app.schemas.trades import TradeNoteIn, TradeOut
from app.services import trading

router = APIRouter(prefix="/trades", tags=["trades"])


@router.patch("/{trade_id}", response_model=TradeOut)
async def update_trade(trade_id: uuid.UUID, body: TradeNoteIn, db: DB, user: CurrentUser, settings: SettingsDep) -> TradeOut:
    """Edit the off-chain note / `notify_investors` flag of a trade (trader of the agreement only)."""
    trade, agreement = await trading.update_trade_note(db, user, trade_id, body)
    return trading.trade_out(trade, agreement, explorer_url=settings.explorer_url)


__all__ = ["router"]
