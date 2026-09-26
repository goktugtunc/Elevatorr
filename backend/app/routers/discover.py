"""Discover / Keşfet (Figma 2a-2d): role-aware swipe feed, remaining count and swipe actions."""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api_deps import DB, CurrentUser
from app.models import InteractionTargetType, MarketCategory
from app.schemas.discover import DiscoverActionIn, DiscoverActionOut, DiscoverFeedOut, DiscoverRemainingOut
from app.services import discover as discover_service

router = APIRouter(prefix="/discover", tags=["discover"])


@router.get("", response_model=DiscoverFeedOut)
async def feed(
    user: CurrentUser,
    db: DB,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    cursor: Annotated[str | None, Query(description="`next_cursor` of the previous page")] = None,
    market: Annotated[MarketCategory | None, Query()] = None,
) -> DiscoverFeedOut:
    """Customers get service listings (trader cards), traders get capital listings; cards already
    passed / liked / saved / offered on are excluded."""
    cards, next_cursor, remaining = await discover_service.feed(db, user, limit=limit, cursor=cursor, market=market)
    return DiscoverFeedOut(items=cards, next_cursor=next_cursor, remaining=remaining, kind=discover_service.feed_kind(user))


@router.get("/remaining", response_model=DiscoverRemainingOut)
async def remaining(
    user: CurrentUser, db: DB, market: Annotated[MarketCategory | None, Query()] = None
) -> DiscoverRemainingOut:
    n = await discover_service.remaining(db, user, market=market)
    return DiscoverRemainingOut(remaining=n, kind=discover_service.feed_kind(user))


@router.post("/{target_type}/{target_id}/action", response_model=DiscoverActionOut)
async def act(
    target_type: InteractionTargetType, target_id: uuid.UUID, body: DiscoverActionIn, user: CurrentUser, db: DB
) -> DiscoverActionOut:
    """Record a swipe: `pass` | `like` | `save` (listing) | `follow` (trader) | `view` | `offer_request`.
    Idempotent: repeating an action returns `created=false`."""
    return await discover_service.act(db, user, target_type, target_id, body.action)


@router.delete("/listing/{listing_id}/save", response_model=DiscoverActionOut)
async def unsave(listing_id: uuid.UUID, user: CurrentUser, db: DB) -> DiscoverActionOut:
    removed = await discover_service.unsave(db, user, listing_id)
    return DiscoverActionOut(
        target_type=InteractionTargetType.listing,
        target_id=listing_id,
        action="save",
        created=removed,
        remaining=await discover_service.remaining(db, user),
        saved=False,
    )
