"""Role-aware dashboard aggregates (Figma 3a customer "Panel", 5c trader "Panel").

Customer: portfolio = value of open agreements (indexed `current_value`, principal until the reconciler
values it) + the wallet's live base-asset balance (read by the router through the chain gateway —
`token_balance(base.address, wallet_address)` — and passed in), change vs. principal, followed traders with
their returns, listing interactions.
Trader: managed capital, active investors, pending offers, commission earned from settled agreements.
Everything here is read-only over the indexed mirror; no chain access.
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models import (
    Agreement,
    AgreementStatus,
    Asset,
    Follow,
    Listing,
    ListingKind,
    ListingStatus,
    Offer,
    OfferStatus,
    Trade,
    User,
    UserRole,
)
from app.schemas.dashboard import (
    CustomerDashboardOut,
    FollowedTraderOut,
    ListingInteractionsOut,
    PendingOfferBriefOut,
    PositionBriefOut,
    ProfileChecklistOut,
    TraderDashboardOut,
)
from app.services.listings import default_base_asset

log = logging.getLogger(__name__)

ZERO = Decimal("0")
OPEN_STATUSES = (AgreementStatus.funded, AgreementStatus.active)  # principal is escrowed on-chain
MONTH = timedelta(days=30)


def _bps(value: Decimal, principal: Decimal) -> int:
    if principal <= 0:
        return 0
    return int(((value - principal) * 10_000 / principal).to_integral_value())


def position_value(ag: Agreement) -> Decimal:
    """Live value of an open agreement: the reconciler's `value_in_base`, else the escrowed principal."""
    return Decimal(ag.current_value) if ag.current_value is not None else Decimal(ag.principal)


async def open_agreements(db: AsyncSession, user_id: uuid.UUID, role: UserRole) -> list[Agreement]:
    col = Agreement.customer_id if role is UserRole.customer else Agreement.trader_id
    rows = (
        await db.execute(
            select(Agreement)
            .where(col == user_id, Agreement.status.in_(OPEN_STATUSES))
            .order_by(Agreement.start_time.desc().nulls_last(), Agreement.created_at.desc())
        )
    ).scalars().all()
    return list(rows)


def _position(ag: Agreement, viewer_role: UserRole) -> PositionBriefOut:
    other = ag.trader if viewer_role is UserRole.customer else ag.customer
    value = position_value(ag)
    principal = Decimal(ag.principal)
    return PositionBriefOut(
        agreement_id=ag.id,
        onchain_id=ag.onchain_id,
        status=ag.status,
        counterparty_id=other.id,
        counterparty_username=other.username,
        counterparty_display_name=other.display_name,
        counterparty_avatar_url=other.avatar_url,
        base_asset_code=ag.base_asset.symbol,
        principal=principal,
        current_value=value,
        pnl=value - principal,
        pnl_bps=_bps(value, principal),
        commission_bps=ag.commission_bps,
        duration_days=int(ag.duration_secs) // 86_400,
        start_time=ag.start_time,
        end_time=ag.end_time,
    )


async def listing_interactions(db: AsyncSession, owner_id: uuid.UUID) -> ListingInteractionsOut:
    q = select(
        func.count(),
        func.coalesce(func.sum(Listing.view_count), 0),
        func.coalesce(func.sum(Listing.like_count), 0),
        func.coalesce(func.sum(Listing.offer_count), 0),
    ).where(Listing.owner_id == owner_id, Listing.status != ListingStatus.closed)
    n, views, likes, offers = (await db.execute(q)).one()
    pending = int(
        (
            await db.execute(
                select(func.count())
                .select_from(Offer)
                .where(Offer.to_user_id == owner_id, Offer.status == OfferStatus.pending)
            )
        ).scalar_one()
    )
    return ListingInteractionsOut(
        listings=int(n or 0), views=int(views or 0), likes=int(likes or 0), offers=int(offers or 0), pending_offers=pending
    )


async def preferred_base_asset(db: AsyncSession, settings: Settings, user: User) -> Asset | None:
    """The base asset of the user's open agreements (most used, on the configured chain), else the chain
    default; None when no base asset is configured at all (dashboard still renders without a wallet balance)."""
    col = Agreement.customer_id if user.role is UserRole.customer else Agreement.trader_id
    q = (
        select(Asset)
        .join(Agreement, Agreement.base_asset_id == Asset.id)
        .where(col == user.id, Agreement.status.in_(OPEN_STATUSES), Asset.chain_id == settings.chain_id)
        .group_by(Asset.id)
        .order_by(func.count().desc(), Asset.created_at.asc())
        .limit(1)
    )
    asset = (await db.execute(q)).scalars().first()
    if asset is not None:
        return asset
    try:
        return await default_base_asset(db, settings)
    except Exception:  # noqa: BLE001 - ValidationError when the seed has not run; not fatal for the panel
        return None


async def _settled_since(db: AsyncSession, col, user_id: uuid.UUID, since: datetime | None):  # noqa: ANN001, ANN202
    where = [col == user_id, Agreement.status == AgreementStatus.settled]
    if since is not None:
        where.append(Agreement.settled_at >= since)
    return (await db.execute(select(Agreement).where(*where))).scalars().all()


# --- customer ------------------------------------------------------------------------------------------


async def customer_dashboard(
    db: AsyncSession,
    user: User,
    *,
    base_asset_code: str,
    wallet_balance: Decimal | None = None,
    wallet_error: str | None = None,
    now: datetime | None = None,
) -> CustomerDashboardOut:
    now = now or datetime.now(UTC)
    opens = await open_agreements(db, user.id, UserRole.customer)
    invested = sum((Decimal(a.principal) for a in opens), ZERO)
    positions_value = sum((position_value(a) for a in opens), ZERO)
    open_pnl = positions_value - invested
    settled_month = await _settled_since(db, Agreement.customer_id, user.id, now - MONTH)
    realised_month = sum(
        (Decimal(a.customer_payout if a.customer_payout is not None else a.final_value or a.principal) - Decimal(a.principal)
         for a in settled_month),
        ZERO,
    )
    month_pnl = realised_month + open_pnl
    month_base = invested + sum((Decimal(a.principal) for a in settled_month), ZERO)

    invested_traders: dict[uuid.UUID, list[Agreement]] = {}
    for a in opens:
        invested_traders.setdefault(a.trader_id, []).append(a)
    follows = (
        await db.execute(
            select(Follow).where(Follow.follower_id == user.id).order_by(Follow.created_at.desc())
        )
    ).scalars().all()
    followed: list[FollowedTraderOut] = []
    for f in follows:
        t: User = f.trader
        ags = invested_traders.get(t.id, [])
        principal = sum((Decimal(a.principal) for a in ags), ZERO)
        value = sum((position_value(a) for a in ags), ZERO)
        followed.append(
            FollowedTraderOut(
                trader_id=t.id,
                username=t.username,
                display_name=t.display_name,
                avatar_url=t.avatar_url,
                risk_level=t.risk_level,
                commission_bps=t.commission_bps,
                total_return_bps=t.total_return_bps,
                monthly_return_bps=t.monthly_return_bps,
                rating_avg=Decimal(t.rating_avg or ZERO),
                invested=bool(ags),
                invested_principal=principal,
                open_pnl_bps=_bps(value, principal) if ags else None,
            )
        )
    wallet = Decimal(wallet_balance) if wallet_balance is not None else None
    return CustomerDashboardOut(
        generated_at=now,
        base_asset_code=base_asset_code,
        portfolio_value=positions_value + (wallet or ZERO),
        wallet_balance=wallet,
        wallet_error=wallet_error,
        invested_principal=invested,
        positions_value=positions_value,
        open_pnl=open_pnl,
        open_pnl_bps=_bps(positions_value, invested),
        month_pnl=month_pnl,
        month_change_bps=_bps(month_base + month_pnl, month_base),
        positions=[_position(a, UserRole.customer) for a in opens],
        followed=followed,
        followed_count=len(followed),
        invested_count=len(invested_traders),
        listing_interactions=await listing_interactions(db, user.id),
    )


# --- trader -----------------------------------------------------------------------------------------------


async def profile_checklist(db: AsyncSession, user: User) -> ProfileChecklistOut:
    has_listing = (
        await db.execute(
            select(Listing.id)
            .where(Listing.owner_id == user.id, Listing.kind == ListingKind.service, Listing.status == ListingStatus.active)
            .limit(1)
        )
    ).scalar_one_or_none() is not None
    has_trade = (
        await db.execute(
            select(Trade.id).join(Agreement, Agreement.id == Trade.agreement_id).where(Agreement.trader_id == user.id).limit(1)
        )
    ).scalar_one_or_none() is not None
    items = ProfileChecklistOut(
        wallet_connected=True,
        has_avatar=bool(user.avatar_url),
        has_strategy=bool(user.strategy_summary),
        has_service_listing=has_listing,
        has_trade=has_trade,
    )
    flags = [items.wallet_connected, items.has_avatar, items.has_strategy, items.has_service_listing, items.has_trade]
    items.completion_pct = int(round(100 * sum(flags) / len(flags)))
    return items


async def trader_dashboard(
    db: AsyncSession, user: User, *, base_asset_code: str, now: datetime | None = None
) -> TraderDashboardOut:
    now = now or datetime.now(UTC)
    opens = await open_agreements(db, user.id, UserRole.trader)
    invested = sum((Decimal(a.principal) for a in opens), ZERO)
    managed = sum((position_value(a) for a in opens), ZERO)
    settled_all = await _settled_since(db, Agreement.trader_id, user.id, None)
    total_commission = sum((Decimal(a.trader_fee or ZERO) for a in settled_all), ZERO)
    month_commission = sum(
        (Decimal(a.trader_fee or ZERO) for a in settled_all if a.settled_at is not None and a.settled_at >= now - MONTH),
        ZERO,
    )
    pending_rows = (
        await db.execute(
            select(Offer)
            .where(Offer.to_user_id == user.id, Offer.status == OfferStatus.pending, Offer.expires_at > now)
            .order_by(Offer.created_at.desc())
        )
    ).scalars().all()
    pending = [
        PendingOfferBriefOut(
            offer_id=o.id,
            listing_id=o.listing_id,
            from_user_id=o.from_user_id,
            from_username=o.from_user.username,
            from_display_name=o.from_user.display_name,
            from_avatar_url=o.from_user.avatar_url,
            amount=Decimal(o.amount),
            base_asset_code=o.base_asset.symbol,
            duration_days=o.duration_days,
            commission_bps=o.commission_bps,
            markets=list(o.listing.markets or []),
            risk_profile=o.listing.risk_profile or o.from_user.risk_profile,
            expires_at=o.expires_at,
            created_at=o.created_at,
        )
        for o in pending_rows[:10]
    ]
    return TraderDashboardOut(
        generated_at=now,
        base_asset_code=base_asset_code,
        managed_capital=managed,
        invested_principal=invested,
        open_pnl=managed - invested,
        open_pnl_bps=_bps(managed, invested),
        active_investors=len({a.customer_id for a in opens}),
        pending_offers_count=len(pending_rows),
        month_commission=month_commission,
        total_commission=total_commission,
        settled_agreements=len(settled_all),
        positions=[_position(a, UserRole.trader) for a in opens],
        pending_offers=pending,
        listing_interactions=await listing_interactions(db, user.id),
        profile_checklist=await profile_checklist(db, user),
    )
