"""Activity feed — Figma 3b/3c "Hareketler" (`GET /activity`)."""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api_deps import DB, CurrentUser, SettingsDep
from app.api_paging import PageDep
from app.schemas.common import Page
from app.schemas.trades import ActivityItemOut, ActivityState
from app.services import activity as svc

router = APIRouter(prefix="/activity", tags=["activity"])


@router.get("", response_model=Page[ActivityItemOut])
async def list_activity(
    db: DB,
    user: CurrentUser,
    settings: SettingsDep,
    page: PageDep,
    trader_id: Annotated[uuid.UUID | None, Query(description="only this trader's trades")] = None,
    state: Annotated[ActivityState | None, Query(description="open | closed agreements")] = None,
) -> Page[ActivityItemOut]:
    """Trades of the traders you follow (flagged `notify_investors`) and of the agreements you are party to."""
    items, total = await svc.list_activity(
        db, user, trader_id=trader_id, state=state, limit=page.limit, offset=page.offset, settings=settings
    )
    return Page[ActivityItemOut](items=items, total=total, limit=page.limit, offset=page.offset)


__all__ = ["router"]
