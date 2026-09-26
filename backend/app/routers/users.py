"""Registration (Figma 1e-1g), profiles, the trader profile aggregate (3e), follows and ratings."""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api_deps import DB, Claims, CurrentUser, OptionalUser, SettingsDep
from app.api_paging import PageDep
from app.core.errors import NotFoundError
from app.models import MarketCategory, RiskLevel
from app.schemas.common import Page
from app.schemas.users import (
    FollowOut,
    MeOut,
    PerformanceRange,
    RatingOut,
    RegisterIn,
    RegisterOut,
    TraderCardOut,
    TraderProfileOut,
    TraderSort,
    UserOut,
    UserUpdateIn,
)
from app.services import users as users_service

router = APIRouter(tags=["users"])


# --- registration / me -------------------------------------------------------------------------------


@router.post("/users/register", response_model=RegisterOut, status_code=201)
async def register(body: RegisterIn, claims: Claims, db: DB, settings: SettingsDep) -> RegisterOut:
    """Create the profile for an authenticated wallet (SIWE token valid, no profile yet). The wallet address is
    the JWT `sub`; the role-specific block (`customer` or `trader`) is mandatory. Returns the profile and a new
    token carrying the role (same `auth_time`)."""
    user, token, expires_at = await users_service.register(
        db, settings, claims.address, body, auth_time=claims.auth_time
    )
    return RegisterOut(user=MeOut.model_validate(user), token=token, expires_at=expires_at)


@router.get("/users/me", response_model=MeOut)
async def get_me(user: CurrentUser) -> MeOut:
    return MeOut.model_validate(user)


@router.patch("/users/me", response_model=MeOut)
async def update_me(body: UserUpdateIn, user: CurrentUser, db: DB) -> MeOut:
    """Partial update; role-specific fields are validated against the caller's role."""
    updated = await users_service.update_profile(db, user, body)
    return MeOut.model_validate(updated)


@router.get("/users/by-username/{username}", response_model=UserOut)
async def get_by_username(username: str, db: DB) -> UserOut:
    user = await users_service.get_by_username(db, username)
    if not user.is_active:
        raise NotFoundError("User not found", code="user_not_found")
    return users_service.public_user_out(user)


@router.get("/users/{user_id}", response_model=UserOut)
async def get_user(user_id: uuid.UUID, db: DB) -> UserOut:
    """Public profile (never exposes push token / admin flag; `budget_amount` is always null here — BE-22)."""
    return users_service.public_user_out(await users_service.get_public_user(db, user_id))


# --- traders --------------------------------------------------------------------------------------------


@router.get("/traders", response_model=Page[TraderCardOut])
async def list_traders(
    db: DB,
    viewer: OptionalUser,
    page: PageDep,
    sort: Annotated[TraderSort, Query(description="rating | return | capital | followers | newest")] = "rating",
    market: Annotated[MarketCategory | None, Query()] = None,
    risk_level: Annotated[RiskLevel | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=80, description="username / display name search")] = None,
) -> Page[TraderCardOut]:
    """Active traders with stats + follower count (discovery / search helper)."""
    cards, total = await users_service.list_traders(
        db, limit=page.limit, offset=page.offset, sort=sort, market=market, risk_level=risk_level, q=q, viewer=viewer
    )
    return Page[TraderCardOut](items=cards, total=total, limit=page.limit, offset=page.offset)


@router.get("/traders/{trader_id}/profile", response_model=TraderProfileOut)
async def trader_profile(
    trader_id: uuid.UUID,
    db: DB,
    viewer: OptionalUser,
    range: Annotated[PerformanceRange, Query(description="performance window")] = "30d",  # noqa: A002
    trades: Annotated[int, Query(ge=0, le=50)] = 10,
) -> TraderProfileOut:
    """Figma 3e: stats, performance series (agreement value snapshots), live positions (open
    agreements + indexed balances), recent trades and ratings."""
    return await users_service.trader_profile(db, trader_id, viewer=viewer, range_=range, trades_limit=trades)


@router.post("/traders/{trader_id}/follow", response_model=FollowOut)
async def follow(trader_id: uuid.UUID, user: CurrentUser, db: DB) -> FollowOut:
    _, count = await users_service.follow_trader(db, user, trader_id)
    return FollowOut(trader_id=trader_id, following=True, follower_count=count)


@router.delete("/traders/{trader_id}/follow", response_model=FollowOut)
async def unfollow(trader_id: uuid.UUID, user: CurrentUser, db: DB) -> FollowOut:
    _, count = await users_service.unfollow_trader(db, user, trader_id)
    return FollowOut(trader_id=trader_id, following=False, follower_count=count)


@router.get("/traders/{trader_id}/ratings", response_model=Page[RatingOut])
async def trader_ratings(trader_id: uuid.UUID, db: DB, page: PageDep) -> Page[RatingOut]:
    await users_service.get_active_trader(db, trader_id)
    items, total = await users_service.list_ratings(db, trader_id, limit=page.limit, offset=page.offset)
    return Page[RatingOut](items=items, total=total, limit=page.limit, offset=page.offset)
