"""User profiles: registration (role-specific fields), profile updates, public profiles, the trader
profile aggregate (Figma 3e), trader discovery helper, follows and ratings.

Monad edition (03-backend-tasarim §6.12): the wallet address comes from the JWT `sub` (lower-case, K11),
`get_by_wallet_address` is the stable lookup other services import, and the public trader profile hides
customer identities / exact principals and non-public trades (00-inceleme §C 1, BE-22).
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal

from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.security import create_access_token, decode_access_token
from app.models import (
    Agreement,
    AgreementStatus,
    AgreementValueSnapshot,
    Follow,
    Listing,
    ListingStatus,
    MarketCategory,
    NotificationCategory,
    Rating,
    RiskLevel,
    Trade,
    User,
    UserRole,
)
from app.schemas.users import (
    STATS_FIELDS,
    MeOut,
    PerformancePoint,
    PerformanceRange,
    PositionBalanceOut,
    PositionOut,
    RatingOut,
    RatingsSummary,
    RegisterIn,
    TradeBriefOut,
    TraderCardOut,
    TraderProfileOut,
    TraderSort,
    TraderStats,
    UserOut,
    UserUpdateIn,
    normalize_username,
)
from app.services.chain.addresses import normalize

log = logging.getLogger(__name__)

ZERO = Decimal("0")
CUSTOMER_FIELDS = frozenset({"budget_amount", "risk_profile"})
TRADER_FIELDS = frozenset({"strategy_summary", "portfolio", "commission_bps", "min_capital", "risk_level"})
_RANGE_DELTA: dict[str, timedelta | None] = {
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
    "1y": timedelta(days=365),
    "all": None,
}


# --- lookups -----------------------------------------------------------------------------------


async def get_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found", code="user_not_found")
    return user


async def get_by_wallet_address(db: AsyncSession, address: str) -> User | None:
    """Profile of a wallet; the address is normalised to lower-case first (K11). Invalid address -> 422."""
    return (
        await db.execute(select(User).where(User.wallet_address == normalize(address)))
    ).scalar_one_or_none()


def public_user_out(user: User) -> UserOut:
    """`GET /users/{id}` / by-username view: the customer's `budget_amount` is never public (BE-22)."""
    return UserOut.model_validate(user).model_copy(update={"budget_amount": None})


def me_out(user: User) -> MeOut:
    return MeOut.model_validate(user)


async def get_by_username(db: AsyncSession, username: str) -> User:
    try:
        uname = normalize_username(username)
    except ValueError as e:
        raise NotFoundError("User not found", code="user_not_found") from e
    user = (await db.execute(select(User).where(User.username == uname))).scalar_one_or_none()
    if user is None:
        raise NotFoundError("User not found", code="user_not_found")
    return user


async def get_public_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    """Public profile lookup: disabled accounts are hidden (404)."""
    user = await get_user(db, user_id)
    if not user.is_active:
        raise NotFoundError("User not found", code="user_not_found")
    return user


async def get_active_trader(db: AsyncSession, trader_id: uuid.UUID) -> User:
    user = await get_public_user(db, trader_id)
    if user.role is not UserRole.trader:
        raise NotFoundError("Trader not found", code="trader_not_found")
    return user


# --- registration / profile ----------------------------------------------------------------------


async def register(
    db: AsyncSession,
    settings: Settings,
    address: str,
    data: RegisterIn,
    *,
    auth_time: int | None = None,
) -> tuple[User, str, datetime]:
    """Create the profile for an authenticated wallet. Returns (user, token_with_role, expires_at).

    `address` is the JWT `sub` (the request body carries no address; one there is ignored). `auth_time` is the
    caller's original SIWE verification time and is carried into the new token unchanged (02-api §1.5).
    409 `already_registered` when the wallet already has a profile, 409 `username_taken` when the
    username is in use (both also enforced by unique constraints; the race is mapped via a savepoint).
    """
    wallet_address = normalize(address)
    if await get_by_wallet_address(db, wallet_address) is not None:
        raise ConflictError("This wallet already has a profile", code="already_registered")
    taken = (await db.execute(select(User.id).where(User.username == data.username))).scalar_one_or_none()
    if taken is not None:
        raise ConflictError("Username is already taken", code="username_taken")

    user = User(
        wallet_address=wallet_address,
        role=data.role,
        username=data.username,
        display_name=data.display_name,
        bio=data.bio,
        avatar_url=data.avatar_url,
        is_active=True,
        is_admin=False,
    )
    if data.role is UserRole.customer:
        assert data.customer is not None  # enforced by the schema validator
        user.budget_amount = data.customer.budget_amount
        user.risk_profile = data.customer.risk_profile
        user.markets = [m.value for m in data.customer.markets]
    else:
        assert data.trader is not None
        user.markets = [m.value for m in data.trader.markets]
        user.strategy_summary = data.trader.strategy_summary
        user.commission_bps = data.trader.commission_bps
        user.min_capital = data.trader.min_capital
        user.risk_level = data.trader.risk_level
    try:
        async with db.begin_nested():
            db.add(user)
            await db.flush()
    except IntegrityError as e:
        constraint = str(getattr(e.orig, "constraint_name", "") or e.orig or e)
        if "username" in constraint:
            raise ConflictError("Username is already taken", code="username_taken") from e
        raise ConflictError("This wallet already has a profile", code="already_registered") from e
    await db.refresh(user)  # server-side created_at / updated_at

    token = create_access_token(
        settings, address=wallet_address, user_id=user.id, role=user.role.value, auth_time=auth_time
    )
    expires_at = decode_access_token(settings, token).expires_at
    log.info("user registered id=%s role=%s username=%s", user.id, user.role.value, user.username)
    return user, token, expires_at


# backwards-compatible alias (routers/tests written against the Stellar-era name)
register_user = register


async def update_profile(db: AsyncSession, user: User, data: UserUpdateIn) -> User:
    """Apply only the fields present in the PATCH body. Role-specific fields are rejected for the
    other role; fields that are mandatory for the role cannot be cleared with null."""
    changes = data.model_dump(exclude_unset=True)
    wrong = sorted(changes.keys() & (TRADER_FIELDS if user.is_customer else CUSTOMER_FIELDS))
    if wrong:
        raise ValidationError(
            f"Fields not allowed for role {user.role.value}: {', '.join(wrong)}",
            code="field_not_allowed_for_role",
            details={"fields": wrong},
        )
    required = (CUSTOMER_FIELDS if user.is_customer else TRADER_FIELDS) | {"markets", "display_name"}
    cleared = sorted(k for k, v in changes.items() if v is None and k in required)
    if cleared:
        raise ValidationError(
            f"Fields cannot be cleared: {', '.join(cleared)}", code="field_required", details={"fields": cleared}
        )
    if "expo_push_token" in changes and changes["expo_push_token"] is not None:
        from app.services.notifications import is_expo_push_token

        if not is_expo_push_token(changes["expo_push_token"]):
            raise ValidationError("expo_push_token must look like ExponentPushToken[...]", code="invalid_expo_token")
    if "markets" in changes:
        changes["markets"] = [m.value for m in data.markets or []]
    for field, value in changes.items():
        setattr(user, field, value)
    if changes:
        await db.flush()
        await db.refresh(user)
    return user


# --- follows -----------------------------------------------------------------------------------------


async def follower_count(db: AsyncSession, trader_id: uuid.UUID) -> int:
    q = select(func.count()).select_from(Follow).where(Follow.trader_id == trader_id)
    return int((await db.execute(q)).scalar_one())


async def is_following(db: AsyncSession, follower_id: uuid.UUID, trader_id: uuid.UUID) -> bool:
    return (await db.get(Follow, (follower_id, trader_id))) is not None


async def follow_trader(db: AsyncSession, follower: User, trader_id: uuid.UUID) -> tuple[bool, int]:
    """Idempotent follow. Returns (created, follower_count). Notifies the trader on a new follow."""
    if follower.id == trader_id:
        raise ValidationError("You cannot follow yourself", code="cannot_follow_self")
    trader = await get_active_trader(db, trader_id)
    created = False
    if await db.get(Follow, (follower.id, trader.id)) is None:
        db.add(Follow(follower_id=follower.id, trader_id=trader.id))
        await db.flush()
        created = True
        from app.services.notifications import notify

        await notify(
            db,
            trader.id,
            "new_follower",
            "New follower",
            f"{follower.display_name} started following you",
            {"follower_id": str(follower.id), "username": follower.username},
            category=NotificationCategory.system,
        )
    return created, await follower_count(db, trader.id)


async def unfollow_trader(db: AsyncSession, follower: User, trader_id: uuid.UUID) -> tuple[bool, int]:
    """Idempotent unfollow. Returns (removed, follower_count)."""
    row = await db.get(Follow, (follower.id, trader_id))
    removed = False
    if row is not None:
        await db.delete(row)
        await db.flush()
        removed = True
    return removed, await follower_count(db, trader_id)


async def _following_set(db: AsyncSession, viewer: User | None, trader_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    if viewer is None or not trader_ids:
        return set()
    q = select(Follow.trader_id).where(Follow.follower_id == viewer.id, Follow.trader_id.in_(trader_ids))
    return set((await db.execute(q)).scalars().all())


# --- ratings -----------------------------------------------------------------------------------------


def _rating_out(rating: Rating, customer: User | None) -> RatingOut:
    return RatingOut(
        id=rating.id,
        agreement_id=rating.agreement_id,
        customer_id=rating.customer_id,
        customer_username=customer.username if customer else None,
        customer_display_name=customer.display_name if customer else None,
        score=rating.score,
        comment=rating.comment,
        created_at=rating.created_at,
    )


async def list_ratings(
    db: AsyncSession, trader_id: uuid.UUID, *, limit: int = 20, offset: int = 0
) -> tuple[list[RatingOut], int]:
    total = int(
        (await db.execute(select(func.count()).select_from(Rating).where(Rating.trader_id == trader_id))).scalar_one()
    )
    rows = (
        await db.execute(
            select(Rating, User)
            .outerjoin(User, User.id == Rating.customer_id)
            .where(Rating.trader_id == trader_id)
            .order_by(Rating.created_at.desc(), Rating.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return [_rating_out(r, u) for r, u in rows], total


async def ratings_summary(db: AsyncSession, trader_id: uuid.UUID) -> RatingsSummary:
    rows = (
        await db.execute(
            select(Rating.score, func.count()).where(Rating.trader_id == trader_id).group_by(Rating.score)
        )
    ).all()
    distribution = {int(score): int(n) for score, n in rows}
    count = sum(distribution.values())
    avg = ZERO
    if count:
        avg = (Decimal(sum(s * n for s, n in distribution.items())) / Decimal(count)).quantize(Decimal("0.01"))
    return RatingsSummary(avg=avg, count=count, distribution=distribution)


async def refresh_rating_stats(db: AsyncSession, trader_id: uuid.UUID) -> RatingsSummary:
    """Recompute users.rating_avg / rating_count from the ratings table (call after POST rating)."""
    summary = await ratings_summary(db, trader_id)
    trader = await db.get(User, trader_id)
    if trader is not None:
        trader.rating_avg = summary.avg
        trader.rating_count = summary.count
        await db.flush()
    return summary


# --- trader profile aggregate (Figma 3e) -------------------------------------------------------------------


def _stats_of(user: User) -> TraderStats:
    return TraderStats(**{f: getattr(user, f) for f in STATS_FIELDS})


def _bps(value: Decimal | None, principal: Decimal | None) -> int | None:
    if value is None or principal is None or principal <= 0:
        return None
    return int(((Decimal(value) - Decimal(principal)) * 10_000 / Decimal(principal)).to_integral_value())


async def performance_series(
    db: AsyncSession, trader_id: uuid.UUID, range_: PerformanceRange = "30d"
) -> list[PerformancePoint]:
    """Trader-level curve from `agreement_value_snapshots`: for every time bucket take the latest
    snapshot of each of the trader's agreements, then sum values / principals across agreements."""
    delta = _RANGE_DELTA[range_]
    unit = "hour" if range_ == "7d" else "day"
    bucket = func.date_trunc(unit, AgreementValueSnapshot.at).label("bucket")
    where = [Agreement.trader_id == trader_id]
    if delta is not None:
        where.append(AgreementValueSnapshot.at >= datetime.now(UTC) - delta)
    latest = (
        select(
            AgreementValueSnapshot.agreement_id.label("agreement_id"),
            bucket,
            AgreementValueSnapshot.value.label("value"),
            Agreement.principal.label("principal"),
        )
        .join(Agreement, Agreement.id == AgreementValueSnapshot.agreement_id)
        .where(*where)
        .distinct(AgreementValueSnapshot.agreement_id, bucket)
        .order_by(AgreementValueSnapshot.agreement_id, bucket, AgreementValueSnapshot.at.desc())
    ).subquery("latest")
    q = (
        select(latest.c.bucket, func.sum(latest.c.value), func.sum(latest.c.principal))
        .group_by(latest.c.bucket)
        .order_by(latest.c.bucket)
    )
    points: list[PerformancePoint] = []
    for at, value, principal in (await db.execute(q)).all():
        value_d, principal_d = Decimal(value or ZERO), Decimal(principal or ZERO)
        points.append(
            PerformancePoint(at=at, value=value_d, principal=principal_d, return_bps=_bps(value_d, principal_d) or 0)
        )
    return points


ANONYMOUS_LABEL = "—"


def approx_principal(value: Decimal) -> Decimal:
    """Round DOWN to one significant digit (1234.5 -> 1000, 0.0872 -> 0.08) so a public profile only reveals a band."""
    d = Decimal(value)
    if d <= 0:
        return Decimal(0)
    exponent = d.adjusted()  # position of the most significant digit
    unit = Decimal(1).scaleb(exponent)
    band = (d / unit).to_integral_value(rounding=ROUND_DOWN) * unit
    return band.quantize(Decimal(1)) if exponent >= 0 else band  # "1000", not "1E+3"


def _balance_out(b) -> PositionBalanceOut:  # noqa: ANN001 - AgreementBalance row
    return PositionBalanceOut(
        asset_id=b.asset_id, symbol=b.asset.symbol, code=b.asset.symbol, address=b.asset.address, balance=b.balance
    )


async def live_positions(db: AsyncSession, trader_id: uuid.UUID, *, viewer: User | None = None) -> list[PositionOut]:
    """Open (active) agreements of the trader with their indexed per-token balances.

    Public view (anyone but the trader): customer identity anonymised, `principal` rounded to a band and
    `current_value` scaled by the same factor so the P&L ratio stays readable (BE-22).
    """
    rows = (
        await db.execute(
            select(Agreement)
            .where(Agreement.trader_id == trader_id, Agreement.status == AgreementStatus.active)
            .order_by(Agreement.start_time.desc().nulls_last(), Agreement.created_at.desc())
        )
    ).scalars().all()
    full = viewer is not None and viewer.id == trader_id
    out: list[PositionOut] = []
    for ag in rows:
        principal = Decimal(ag.principal)
        current = Decimal(ag.current_value) if ag.current_value is not None else None
        pnl = _bps(current, principal)
        balances = [_balance_out(b) for b in ag.balances if b.balance > 0]
        if not full:
            shown = approx_principal(principal)
            if current is not None and principal > 0:
                current = (current * shown / principal).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
            principal = shown
            balances = []
        out.append(
            PositionOut(
                agreement_id=ag.id,
                onchain_id=ag.onchain_id,
                customer_id=ag.customer_id if full else None,
                customer_username=ag.customer.username if full else ANONYMOUS_LABEL,
                customer_display_name=ag.customer.display_name if full else ANONYMOUS_LABEL,
                base_asset_code=ag.base_asset.symbol,
                principal=principal,
                principal_approx=not full,
                current_value=current,
                pnl_bps=pnl,
                start_time=ag.start_time,
                end_time=ag.end_time,
                balances=balances,
            )
        )
    return out


def trade_brief(trade: Trade) -> TradeBriefOut:
    in_code, out_code = trade.token_in.symbol, trade.token_out.symbol
    return TradeBriefOut(
        id=trade.id,
        agreement_id=trade.agreement_id,
        tx_hash=trade.tx_hash,
        block_number=trade.block_number,
        symbol_label=trade.symbol_label or f"{in_code}/{out_code}",
        token_in_code=in_code,
        token_out_code=out_code,
        amount_in=trade.amount_in,
        amount_out=trade.amount_out,
        value_after=trade.value_after,
        note=trade.note,
        created_at=trade.created_at,
    )


async def recent_trades(
    db: AsyncSession, trader_id: uuid.UUID, *, limit: int = 10, viewer: User | None = None
) -> list[TradeBriefOut]:
    """Latest trades of the trader; trades flagged `notify_investors=False` are only visible to the trader."""
    if limit <= 0:
        return []
    where = [Agreement.trader_id == trader_id]
    if viewer is None or viewer.id != trader_id:
        where.append(Trade.notify_investors.is_(True))
    rows = (
        await db.execute(
            select(Trade)
            .join(Agreement, Agreement.id == Trade.agreement_id)
            .where(*where)
            .order_by(Trade.created_at.desc(), Trade.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [trade_brief(t) for t in rows]


async def active_listing_count(db: AsyncSession, owner_id: uuid.UUID) -> int:
    q = (
        select(func.count())
        .select_from(Listing)
        .where(Listing.owner_id == owner_id, Listing.status == ListingStatus.active)
    )
    return int((await db.execute(q)).scalar_one())


async def trader_profile(
    db: AsyncSession,
    trader_id: uuid.UUID,
    *,
    viewer: User | None = None,
    range_: PerformanceRange = "30d",
    trades_limit: int = 10,
    ratings_limit: int = 5,
) -> TraderProfileOut:
    trader = await get_active_trader(db, trader_id)
    ratings, _ = await list_ratings(db, trader.id, limit=ratings_limit)
    return TraderProfileOut(
        user=public_user_out(trader),
        stats=_stats_of(trader),
        follower_count=await follower_count(db, trader.id),
        is_following=(await is_following(db, viewer.id, trader.id)) if viewer else None,
        active_listings=await active_listing_count(db, trader.id),
        performance_range=range_,
        performance=await performance_series(db, trader.id, range_),
        positions=await live_positions(db, trader.id, viewer=viewer),
        recent_trades=await recent_trades(db, trader.id, limit=trades_limit, viewer=viewer),
        ratings=await ratings_summary(db, trader.id),
        recent_ratings=ratings,
    )


# --- trader discovery helper --------------------------------------------------------------------------------


def _traders_query(
    *,
    market: MarketCategory | None,
    risk_level: RiskLevel | None,
    q: str | None,
    exclude_ids: list[uuid.UUID] | None,
) -> Select:
    stmt = select(User).where(User.role == UserRole.trader, User.is_active.is_(True))
    if market is not None:
        stmt = stmt.where(User.markets.contains([market.value]))
    if risk_level is not None:
        stmt = stmt.where(User.risk_level == risk_level)
    if q and q.strip():
        pattern = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(
            or_(User.username.ilike(pattern, escape="\\"), User.display_name.ilike(pattern, escape="\\"))
        )
    if exclude_ids:
        stmt = stmt.where(User.id.not_in(exclude_ids))
    return stmt


async def list_traders(
    db: AsyncSession,
    *,
    limit: int = 20,
    offset: int = 0,
    sort: TraderSort = "rating",
    market: MarketCategory | None = None,
    risk_level: RiskLevel | None = None,
    q: str | None = None,
    viewer: User | None = None,
    exclude_ids: list[uuid.UUID] | None = None,
) -> tuple[list[TraderCardOut], int]:
    """Active traders for discovery / search. Returns (cards, total)."""
    followers = (
        select(Follow.trader_id.label("trader_id"), func.count().label("n")).group_by(Follow.trader_id).subquery()
    )
    n_followers = func.coalesce(followers.c.n, 0)
    base = _traders_query(market=market, risk_level=risk_level, q=q, exclude_ids=exclude_ids)
    total = int((await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one())
    stmt = base.add_columns(n_followers.label("follower_count")).outerjoin(
        followers, followers.c.trader_id == User.id
    )
    if sort == "return":
        order = [User.total_return_bps.desc(), User.rating_avg.desc()]
    elif sort == "capital":
        order = [User.managed_capital.desc(), User.rating_avg.desc()]
    elif sort == "followers":
        order = [n_followers.desc(), User.rating_avg.desc()]
    elif sort == "newest":
        order = [User.created_at.desc()]
    else:
        order = [User.rating_avg.desc(), User.rating_count.desc(), User.managed_capital.desc()]
    order += [User.created_at.asc(), User.id.asc()]
    rows = (await db.execute(stmt.order_by(*order).limit(limit).offset(offset))).all()
    following = await _following_set(db, viewer, [u.id for u, _ in rows])
    cards = [
        TraderCardOut(
            user=public_user_out(u),
            follower_count=int(n or 0),
            is_following=(u.id in following) if viewer else None,
        )
        for u, n in rows
    ]
    return cards, total
