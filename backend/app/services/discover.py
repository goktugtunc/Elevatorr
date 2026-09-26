"""Discover / Keşfet (Figma 2a-2d): role-aware swipe feed with keyset cursor pagination.

* A customer swipes **service listings** (rendered as trader cards: owner profile + stats); a trader
  swipes **capital listings**. Cards the viewer already passed / liked / saved / requested an offer on
  are excluded, as are listings of traders the viewer passed as a user, the viewer's own listings and
  listings of inactive users.
* Actions are recorded in `interactions` (idempotent) with side effects: `like` bumps the listing's
  like counter, `save` adds a favourite, `follow` follows the trader behind the card (`users.follow_trader`),
  `view` bumps the view counter, `offer_request` only marks the card (the offer itself is POST /offers).
"""
from __future__ import annotations

import base64
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.models import (
    Favorite,
    Follow,
    Interaction,
    InteractionAction,
    InteractionTargetType,
    Listing,
    ListingKind,
    MarketCategory,
    User,
    UserRole,
)
from app.schemas.discover import DiscoverActionOut, DiscoverCardOut
from app.services import users as users_service
from app.services.listings import (
    bump_counter,
    get_listing,
    public_query,
    record_interaction,
    to_out,
    viewer_flags,
)

log = logging.getLogger(__name__)

# listing-level actions that remove a card from the feed
HIDING_ACTIONS = (
    InteractionAction.pass_,
    InteractionAction.like,
    InteractionAction.save,
    InteractionAction.offer_request,
)
FEED_KIND = {UserRole.customer: ListingKind.service, UserRole.trader: ListingKind.capital}
LISTING_ACTIONS = frozenset(
    {
        InteractionAction.pass_,
        InteractionAction.like,
        InteractionAction.save,
        InteractionAction.follow,
        InteractionAction.view,
        InteractionAction.offer_request,
    }
)
USER_ACTIONS = frozenset(
    {InteractionAction.pass_, InteractionAction.like, InteractionAction.follow, InteractionAction.offer_request}
)


def feed_kind(user: User) -> ListingKind:
    return FEED_KIND[user.role]


# --- cursor ---------------------------------------------------------------------------------------------


def encode_cursor(created_at: datetime, listing_id: uuid.UUID) -> str:
    raw = f"{created_at.isoformat()}|{listing_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        at_s, id_s = raw.split("|", 1)
        at = datetime.fromisoformat(at_s)
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        return at, uuid.UUID(id_s)
    except Exception as e:  # noqa: BLE001 - any malformed cursor is a client error
        raise ValidationError("Invalid cursor", code="invalid_cursor") from e


# --- feed query ----------------------------------------------------------------------------------------------


def _feed_query(user: User, *, market: MarketCategory | None) -> Select:
    hidden_listings = select(Interaction.target_id).where(
        Interaction.user_id == user.id,
        Interaction.target_type == InteractionTargetType.listing,
        Interaction.action.in_(HIDING_ACTIONS),
    )
    passed_users = select(Interaction.target_id).where(
        Interaction.user_id == user.id,
        Interaction.target_type == InteractionTargetType.user,
        Interaction.action == InteractionAction.pass_,
    )
    return public_query(kind=feed_kind(user), market=market, exclude_owner_id=user.id).where(
        Listing.id.not_in(hidden_listings), Listing.owner_id.not_in(passed_users)
    )


async def remaining(db: AsyncSession, user: User, *, market: MarketCategory | None = None) -> int:
    base = _feed_query(user, market=market)
    return int((await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one())


def _tags(listing: Listing, now: datetime) -> list[str]:
    owner = listing.owner
    tags: list[str] = []
    if listing.is_service:
        if (owner.max_drawdown_bps or 0) <= 1_500 and owner.rating_count + owner.active_agreements > 0:
            tags.append("low_drawdown")
        if owner.total_return_bps > 0:
            tags.append("profitable")
        if owner.rating_avg >= 4 and owner.rating_count >= 3:
            tags.append("top_rated")
        if owner.active_agreements >= 5:
            tags.append("popular")
        if owner.created_at and now - owner.created_at >= timedelta(days=365):
            tags.append("12m_plus")
    else:
        if (listing.duration_days or 0) >= 180:
            tags.append("long_term")
        elif (listing.duration_days or 0) <= 30:
            tags.append("short_term")
        if listing.offer_count == 0:
            tags.append("no_offers_yet")
        elif listing.offer_count >= 5:
            tags.append("in_demand")
        if listing.max_loss_bps is not None and listing.max_loss_bps <= 1_000:
            tags.append("capital_protective")
    if now - listing.created_at <= timedelta(days=7):
        tags.append("new")
    return tags


async def feed(
    db: AsyncSession,
    user: User,
    *,
    limit: int = 10,
    cursor: str | None = None,
    market: MarketCategory | None = None,
) -> tuple[list[DiscoverCardOut], str | None, int]:
    """Newest-first keyset page of cards the viewer has not acted on. Returns (cards, next_cursor, remaining)."""
    base = _feed_query(user, market=market)
    total = int((await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one())
    stmt = base
    if cursor:
        c_at, c_id = decode_cursor(cursor)
        stmt = stmt.where(
            or_(Listing.created_at < c_at, and_(Listing.created_at == c_at, Listing.id < c_id))
        )
    rows = (
        await db.execute(stmt.order_by(Listing.created_at.desc(), Listing.id.desc()).limit(limit + 1))
    ).scalars().all()
    has_more = len(rows) > limit
    rows = list(rows[:limit])
    next_cursor = encode_cursor(rows[-1].created_at, rows[-1].id) if has_more and rows else None

    flags = await viewer_flags(db, user, [x.id for x in rows])
    following = await _following(db, user, [x.owner_id for x in rows])
    now = datetime.now(UTC)
    cards = [
        DiscoverCardOut(
            target_type=InteractionTargetType.listing,
            target_id=x.id,
            owner_target_id=x.owner_id,
            kind=x.kind,
            listing=to_out(x, viewer=user, flags=flags.get(x.id)),
            is_following=x.owner_id in following,
            is_saved=bool(flags.get(x.id, {}).get("is_saved", False)),
            tags=_tags(x, now),
        )
        for x in rows
    ]
    return cards, next_cursor, total


async def _following(db: AsyncSession, user: User, trader_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    if not trader_ids:
        return set()
    q = select(Follow.trader_id).where(Follow.follower_id == user.id, Follow.trader_id.in_(trader_ids))
    return set((await db.execute(q)).scalars().all())


# --- actions ----------------------------------------------------------------------------------------------


async def act(
    db: AsyncSession,
    user: User,
    target_type: InteractionTargetType,
    target_id: uuid.UUID,
    action: InteractionAction,
) -> DiscoverActionOut:
    if target_type is InteractionTargetType.listing:
        return await _act_on_listing(db, user, target_id, action)
    return await _act_on_user(db, user, target_id, action)


async def _act_on_listing(
    db: AsyncSession, user: User, listing_id: uuid.UUID, action: InteractionAction
) -> DiscoverActionOut:
    if action not in LISTING_ACTIONS:
        raise ValidationError(f"{action.value} is not a listing action", code="invalid_action")
    listing = await get_listing(db, listing_id)
    if listing.owner_id == user.id:
        raise ValidationError("You cannot act on your own listing", code="own_listing")
    out = DiscoverActionOut(
        target_type=InteractionTargetType.listing, target_id=listing.id, action=action, created=False, remaining=0
    )
    if action is InteractionAction.follow:
        # following the trader behind a service card; recorded against the user target
        return await _act_on_user(db, user, listing.owner_id, action)

    created = await record_interaction(db, user.id, InteractionTargetType.listing, listing.id, action)
    out.created = created
    if action is InteractionAction.like:
        if created:
            await bump_counter(db, listing.id, "like_count", 1)
            await db.refresh(listing, attribute_names=["like_count"])
        out.like_count = int(listing.like_count or 0)
    elif action is InteractionAction.view:
        if created:
            await bump_counter(db, listing.id, "view_count", 1)
    elif action is InteractionAction.save:
        if await db.get(Favorite, (user.id, listing.id)) is None:
            db.add(Favorite(user_id=user.id, listing_id=listing.id))
            await db.flush()
        out.saved = True
    out.remaining = await remaining(db, user)
    return out


async def _act_on_user(
    db: AsyncSession, user: User, target_user_id: uuid.UUID, action: InteractionAction
) -> DiscoverActionOut:
    if action not in USER_ACTIONS:
        raise ValidationError(f"{action.value} is not a user action", code="invalid_action")
    if target_user_id == user.id:
        raise ValidationError("You cannot act on yourself", code="own_profile")
    target = await db.get(User, target_user_id)
    if target is None or not target.is_active:
        raise NotFoundError("User not found", code="user_not_found")
    out = DiscoverActionOut(
        target_type=InteractionTargetType.user, target_id=target.id, action=action, created=False, remaining=0
    )
    if action is InteractionAction.follow:
        if target.role is not UserRole.trader:
            raise ValidationError("Only traders can be followed", code="not_a_trader")
        created, _ = await users_service.follow_trader(db, user, target.id)
        await record_interaction(db, user.id, InteractionTargetType.user, target.id, action)
        out.created = created
        out.following = True
    else:
        out.created = await record_interaction(db, user.id, InteractionTargetType.user, target.id, action)
    out.remaining = await remaining(db, user)
    return out


async def unsave(db: AsyncSession, user: User, listing_id: uuid.UUID) -> bool:
    fav = await db.get(Favorite, (user.id, listing_id))
    if fav is None:
        return False
    await db.delete(fav)
    await db.flush()
    return True
