"""Listings (Figma 5a capital / 5b service): CRUD, status transitions, public + owner queries, viewer
flags, the shared idempotent `record_interaction` helper and the `reserve` / `release` calldata builders
(03-backend-tasarim §6.3; 02-api-sozlesme §3.3–3.4, §8.3).

Rules
* a customer owns capital listings, a trader service listings (`kind` derived from the role);
* capital listings carry the on-chain terms the offers inherit (amount, base asset, duration, max loss);
  the base asset must be an active `is_base_allowed` asset of the configured chain;
* a capital listing is a `draft` until its `reserve` is confirmed by the indexer (`Reserved` event);
* counters (`view_count`, `like_count`, `offer_count`) are bumped with atomic UPDATEs.
Services flush only; the request session commits.
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ForbiddenError, NotFoundError, StateError, ValidationError
from app.models import (
    Asset,
    Favorite,
    Interaction,
    InteractionAction,
    InteractionTargetType,
    Listing,
    ListingKind,
    ListingStatus,
    MarketCategory,
    Offer,
    OfferStatus,
    PendingTransaction,
    PendingTxKind,
    PendingTxStatus,
    RiskProfile,
    User,
    UserRole,
)
from app.schemas.listings import (
    ListingCountsOut,
    ListingCreateIn,
    ListingOut,
    ListingSort,
    ListingStatusAction,
    ListingUpdateIn,
)
from app.services.agreements import compute_listing_ref, record_pending
from app.services.chain import amounts as money
from app.services.chain.gateway import ChainGateway
from app.services.chain.types import UnsignedTx

log = logging.getLogger(__name__)

CAPITAL_FIELDS = frozenset({"amount", "base_asset_id", "duration_days", "max_loss_bps"})
SERVICE_FIELDS = frozenset({"commission_bps", "min_capital", "expected_return_min_bps", "expected_return_max_bps"})
KIND_FOR_ROLE = {UserRole.customer: ListingKind.capital, UserRole.trader: ListingKind.service}


# --- interactions (shared) --------------------------------------------------------------------------------


async def record_interaction(
    db: AsyncSession,
    user_id: uuid.UUID,
    target_type: InteractionTargetType,
    target_id: uuid.UUID,
    action: InteractionAction,
) -> bool:
    """Insert an `interactions` row; returns False when the same (user, target, action) already exists."""
    stmt = (
        pg_insert(Interaction)
        .values(
            id=uuid.uuid4(),
            user_id=user_id,
            target_type=target_type,
            target_id=target_id,
            action=action,
            created_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing(
            index_elements=[Interaction.user_id, Interaction.target_type, Interaction.target_id, Interaction.action]
        )
    )
    result = await db.execute(stmt)
    return int(result.rowcount or 0) > 0


async def has_interaction(
    db: AsyncSession,
    user_id: uuid.UUID,
    target_type: InteractionTargetType,
    target_id: uuid.UUID,
    action: InteractionAction,
) -> bool:
    q = select(Interaction.id).where(
        Interaction.user_id == user_id,
        Interaction.target_type == target_type,
        Interaction.target_id == target_id,
        Interaction.action == action,
    )
    return (await db.execute(q)).scalar_one_or_none() is not None


async def bump_counter(db: AsyncSession, listing_id: uuid.UUID, column: str, delta: int = 1) -> None:
    col = getattr(Listing, column)
    await db.execute(
        update(Listing)
        .where(Listing.id == listing_id)
        .values({column: col + delta})
        .execution_options(synchronize_session=False)
    )


# --- assets ---------------------------------------------------------------------------------------------


async def default_base_asset(db: AsyncSession, settings: Settings) -> Asset:
    """The chain's default base asset (`settings.default_base_asset_code` = symbol, allow-listed as base)."""
    q = (
        select(Asset)
        .where(
            Asset.chain_id == settings.chain_id,
            func.lower(Asset.symbol) == settings.default_base_asset_code.lower(),
            Asset.is_active.is_(True),
            Asset.is_base_allowed.is_(True),
        )
        .order_by(Asset.created_at.asc(), Asset.id.asc())
        .limit(1)
    )
    asset = (await db.execute(q)).scalars().first()
    if asset is None:
        raise ValidationError(
            f"Default base asset {settings.default_base_asset_code} is not configured for chain {settings.chain_id}",
            code="base_asset_unavailable",
        )
    return asset


async def resolve_base_asset(db: AsyncSession, settings: Settings, asset_id: uuid.UUID | None) -> Asset:
    """`asset_id` -> Asset that may hold escrowed capital (active, base-allowed, this chain)."""
    if asset_id is None:
        return await default_base_asset(db, settings)
    asset = await db.get(Asset, asset_id)
    if asset is None or asset.chain_id != settings.chain_id:
        raise NotFoundError("Asset not found", code="asset_not_found")
    if not asset.is_active or not asset.is_base_allowed:
        raise ValidationError(
            f"{asset.symbol} cannot be used as a base asset", code="asset_not_base", details={"asset_id": str(asset.id)}
        )
    return asset


def _check_amount_precision(amount: Decimal, asset: Asset, field: str = "amount") -> Decimal:
    """`amount` must fit `asset.decimals` (422 `too_many_decimals`)."""
    try:
        return money.parse_amount(amount, asset.decimals)
    except money.AmountError as e:
        raise ValidationError(e.message, code=e.code, details={**e.details, "field": field}) from e


# --- lookups ---------------------------------------------------------------------------------------------


async def get_listing(db: AsyncSession, listing_id: uuid.UUID, *, for_update: bool = False) -> Listing:
    if for_update:
        # lock the row without dragging the eager-joined owner/asset into FOR UPDATE
        await db.execute(select(Listing.id).where(Listing.id == listing_id).with_for_update())
    listing = await db.get(Listing, listing_id, populate_existing=for_update)
    if listing is None:
        raise NotFoundError("Listing not found", code="listing_not_found")
    return listing


async def get_owned_listing(
    db: AsyncSession, owner: User, listing_id: uuid.UUID, *, for_update: bool = False
) -> Listing:
    listing = await get_listing(db, listing_id, for_update=for_update)
    if listing.owner_id != owner.id and not owner.is_admin:
        raise ForbiddenError("Not your listing", code="not_listing_owner")
    return listing


# --- create / update ----------------------------------------------------------------------------------


def _kind_for(owner: User, requested: ListingKind | None) -> ListingKind:
    kind = KIND_FOR_ROLE[owner.role]
    if requested is not None and requested != kind:
        raise ValidationError(
            f"A {owner.role.value} can only create {kind.value} listings",
            code="listing_kind_for_role",
            details={"kind": kind.value},
        )
    return kind


def _reject_fields(kind: ListingKind, changes: dict[str, Any]) -> None:
    wrong = sorted(changes.keys() & (SERVICE_FIELDS if kind is ListingKind.capital else CAPITAL_FIELDS))
    if wrong:
        raise ValidationError(
            f"Fields not allowed on a {kind.value} listing: {', '.join(wrong)}",
            code="field_not_allowed_for_kind",
            details={"fields": wrong},
        )


def _check_return_range(lo: int | None, hi: int | None) -> None:
    if lo is not None and hi is not None and lo > hi:
        raise ValidationError("expected_return_min_bps must be <= expected_return_max_bps", code="return_range")


async def create_listing(db: AsyncSession, settings: Settings, owner: User, data: ListingCreateIn) -> Listing:
    kind = _kind_for(owner, data.kind)
    changes = data.model_dump(exclude_unset=True, exclude={"kind"})
    _reject_fields(kind, {k: v for k, v in changes.items() if v is not None})

    listing = Listing(
        owner_id=owner.id,
        kind=kind,
        title=data.title,
        description=data.description or "",
        markets=[m.value for m in data.markets] if data.markets else list(owner.markets or []),
        # A capital listing only goes public once its capital is locked in the
        # vault, so it is born as a draft and `reserve` publishes it. A service
        # listing promises work, not money, and is live immediately.
        status=ListingStatus.draft if kind is ListingKind.capital else ListingStatus.active,
    )
    if kind is ListingKind.capital:
        if data.amount is None:
            raise ValidationError("amount is required for a capital listing", code="amount_required")
        if data.duration_days is None:
            raise ValidationError("duration_days is required for a capital listing", code="duration_required")
        asset = await resolve_base_asset(db, settings, data.base_asset_id)
        listing.amount = _check_amount_precision(Decimal(data.amount), asset)
        listing.base_asset_id = asset.id
        listing.duration_days = data.duration_days
        listing.max_loss_bps = data.max_loss_bps
        listing.risk_profile = data.risk_profile or owner.risk_profile
    else:
        commission = data.commission_bps if data.commission_bps is not None else owner.commission_bps
        if commission is None:
            raise ValidationError("commission_bps is required for a service listing", code="commission_required")
        _check_return_range(data.expected_return_min_bps, data.expected_return_max_bps)
        listing.commission_bps = commission
        listing.min_capital = data.min_capital if data.min_capital is not None else owner.min_capital
        listing.expected_return_min_bps = data.expected_return_min_bps
        listing.expected_return_max_bps = data.expected_return_max_bps
        listing.risk_profile = data.risk_profile
    if not listing.markets:
        raise ValidationError("markets is required", code="markets_required")

    db.add(listing)
    await db.flush()
    await db.refresh(listing)
    log.info("listing created id=%s kind=%s owner=%s", listing.id, kind.value, owner.id)
    return listing


def listing_ref_bytes(listing: Listing) -> bytes:
    """32 raw bytes tying an on-chain reservation to this listing (`reserve(token, amount, listingRef)`)."""
    return bytes.fromhex(compute_listing_ref(listing.id))


async def _in_flight(db: AsyncSession, listing: Listing, kind: PendingTxKind) -> PendingTransaction | None:
    """A `pending` / `submitted` row of `kind` for this listing (03 §6.3): the wallet may already have
    broadcast it even if `/tx/submit` was never called, so no second one is built on top."""
    return (
        await db.execute(
            select(PendingTransaction)
            .where(
                PendingTransaction.listing_id == listing.id,
                PendingTransaction.kind == kind,
                PendingTransaction.status.in_((PendingTxStatus.pending, PendingTxStatus.submitted)),
                or_(PendingTransaction.status == PendingTxStatus.submitted, PendingTransaction.expires_at > datetime.now(UTC)),
            )
            .order_by(PendingTransaction.created_at.desc())
        )
    ).scalars().first()


async def build_reserve_tx(
    db: AsyncSession, settings: Settings, chain: ChainGateway, owner: User, listing_id: uuid.UUID
) -> tuple[Listing, UnsignedTx, PendingTransaction]:
    """Unsigned `reserve(token, amountRaw, listingRef)` that moves the listing's capital into the vault
    (+ an `approve` pre-step when the allowance is short). This is what publishes a capital listing.
    Returns `(listing, unsigned, pending)`."""
    listing = await get_owned_listing(db, owner, listing_id, for_update=True)
    if not listing.is_capital:
        raise ValidationError("Only a capital listing locks capital", code="listing_kind_mismatch")
    if listing.status is ListingStatus.closed:
        raise StateError("A closed listing cannot lock capital", code="listing_closed")
    if listing.reservation_id is not None:
        raise StateError(
            "This listing already has capital locked",
            code="listing_already_reserved",
            details={"reservation_id": listing.reservation_id},
        )
    if listing.amount is None or listing.base_asset_id is None:
        raise ValidationError("amount and base_asset_id are required", code="amount_required")
    in_flight = await _in_flight(db, listing, PendingTxKind.reserve)
    if in_flight is not None:
        raise StateError(
            "A deposit for this listing is already waiting to be sent or confirmed",
            code="listing_reserve_in_flight",
            details={"pending_tx_id": str(in_flight.id), "expires_at": in_flight.expires_at.isoformat()},
        )
    asset = await db.get(Asset, listing.base_asset_id)
    if asset is None:
        raise ValidationError("base asset not found", code="asset_not_supported")
    amount_raw = money.to_raw(listing.amount, asset.decimals)
    listing_ref = listing_ref_bytes(listing)
    unsigned = await chain.build_reserve(owner.wallet_address, asset.address, amount_raw, listing_ref)
    amount_str = money.format_amount(listing.amount, asset.decimals)
    summary = {
        "listing_id": str(listing.id),
        "amount": amount_str,
        "amount_raw": str(amount_raw),
        "asset_id": str(asset.id),
        "symbol": asset.symbol,
        "listing_ref": "0x" + listing_ref.hex(),
    }
    pending = await record_pending(
        db, owner, PendingTxKind.reserve, None, unsigned, summary, listing=listing,
        description=f"İlan için {amount_str} {asset.symbol} sermayeyi kasaya kilitle",
    )
    return listing, unsigned, pending


async def build_release_tx(
    db: AsyncSession,
    settings: Settings,
    chain: ChainGateway,
    owner: User,
    listing_id: uuid.UUID,
    amount: Decimal | None = None,
) -> tuple[Listing, UnsignedTx, PendingTransaction]:
    """Unsigned `releaseAll(reservationId)` (no amount) or `release(reservationId, amountRaw)` that hands
    locked capital back to the customer (02-api §3.4)."""
    listing = await get_owned_listing(db, owner, listing_id, for_update=True)
    if listing.reservation_id is None:
        raise StateError("This listing has no locked capital", code="no_reservation")
    reserved = Decimal(listing.reserved_amount or 0)
    if reserved <= 0:
        raise StateError("The locked capital is already used or released", code="no_reservation")
    asset = listing.base_asset
    if asset is None:
        raise ValidationError("base asset not found", code="asset_not_supported")
    res_id = int(listing.reservation_id)
    summary: dict[str, Any] = {
        "listing_id": str(listing.id),
        "reservation_id": res_id,
        "asset_id": str(asset.id),
        "symbol": asset.symbol,
        "reserved_amount": money.format_amount(reserved, asset.decimals),
    }
    if amount is None:
        unsigned = await chain.build_release_all(owner.wallet_address, res_id)
        description = f"Rezervasyon #{res_id}'deki tüm {asset.symbol} bakiyesini serbest bırak"
    else:
        amount = Decimal(amount)
        if amount <= 0:
            raise ValidationError("amount must be positive (omit it to release everything)", code="invalid_amount")
        amount = _check_amount_precision(amount, asset)
        if amount > reserved:
            raise StateError(
                "the reservation does not hold that much (an open agreement may be drawing on it)",
                code="reservation_locked",
                details={
                    "reserved_amount": money.format_amount(reserved, asset.decimals),
                    "requested": money.format_amount(amount, asset.decimals),
                },
            )
        amount_raw = money.to_raw(amount, asset.decimals)
        unsigned = await chain.build_release(owner.wallet_address, res_id, amount_raw)
        summary.update({"amount": money.format_amount(amount, asset.decimals), "amount_raw": str(amount_raw)})
        description = f"Rezervasyon #{res_id}'den {money.format_amount(amount, asset.decimals)} {asset.symbol} serbest bırak"
    pending = await record_pending(
        db, owner, PendingTxKind.release, None, unsigned, summary, listing=listing, description=description
    )
    return listing, unsigned, pending


async def update_listing(
    db: AsyncSession, settings: Settings, owner: User, listing_id: uuid.UUID, data: ListingUpdateIn
) -> Listing:
    listing = await get_owned_listing(db, owner, listing_id, for_update=True)
    if listing.status is ListingStatus.closed:
        raise StateError("A closed listing cannot be edited", code="listing_closed")
    changes = data.model_dump(exclude_unset=True)
    _reject_fields(listing.kind, changes)
    if not changes:
        return listing

    required_non_null = {"title", "markets"} | (
        {"amount", "duration_days", "base_asset_id"} if listing.is_capital else {"commission_bps"}
    )
    cleared = sorted(k for k, v in changes.items() if v is None and k in required_non_null)
    if cleared:
        raise ValidationError(
            f"Fields cannot be cleared: {', '.join(cleared)}", code="field_required", details={"fields": cleared}
        )
    if listing.is_capital and listing.reservation_id is not None:
        # BE-23: the advertised amount / asset is backed by the on-chain reservation; release first.
        locked = sorted(k for k in ("amount", "base_asset_id") if k in changes and changes[k] != getattr(listing, k))
        if locked:
            raise StateError(
                "amount is locked while the listing's capital is reserved in the vault",
                code="amount_locked",
                details={"reservation_id": listing.reservation_id, "fields": locked},
            )
    if "base_asset_id" in changes:
        changes["base_asset_id"] = (await resolve_base_asset(db, settings, changes["base_asset_id"])).id
    if "amount" in changes and changes["amount"] is not None:
        asset = await db.get(Asset, changes.get("base_asset_id") or listing.base_asset_id)
        if asset is not None:
            changes["amount"] = _check_amount_precision(Decimal(changes["amount"]), asset)
    if "markets" in changes:
        changes["markets"] = [m.value for m in data.markets or []]
    if listing.is_service:
        lo = changes.get("expected_return_min_bps", listing.expected_return_min_bps)
        hi = changes.get("expected_return_max_bps", listing.expected_return_max_bps)
        _check_return_range(lo, hi)
    for field, value in changes.items():
        setattr(listing, field, value)
    await db.flush()
    await db.refresh(listing)
    return listing


async def set_status(db: AsyncSession, owner: User, listing_id: uuid.UUID, action: ListingStatusAction) -> Listing:
    """pause: active -> paused; resume: paused -> active; close: draft|active|paused -> closed (terminal)."""
    listing = await get_owned_listing(db, owner, listing_id, for_update=True)
    transitions: dict[str, tuple[set[ListingStatus], ListingStatus]] = {
        "pause": ({ListingStatus.active}, ListingStatus.paused),
        "resume": ({ListingStatus.paused}, ListingStatus.active),
        # A draft never went public, so closing it is just discarding it.
        "close": ({ListingStatus.draft, ListingStatus.active, ListingStatus.paused}, ListingStatus.closed),
    }
    allowed_from, target = transitions[action]
    if listing.status not in allowed_from:
        raise StateError(
            f"Cannot {action} a {listing.status.value} listing",
            code="listing_invalid_transition",
            details={"status": listing.status.value},
        )
    if target is ListingStatus.closed and listing.reserved_amount and listing.reserved_amount > 0:
        raise StateError(
            "Release the capital locked for this listing before closing it",
            code="listing_capital_locked",
            details={"reservation_id": listing.reservation_id, "reserved_amount": money.format_amount(listing.reserved_amount)},
        )
    listing.status = target
    if target is ListingStatus.closed:
        listing.closed_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(listing)
    return listing


# --- queries ----------------------------------------------------------------------------------------------


def public_query(
    *,
    kind: ListingKind | None = None,
    market: MarketCategory | None = None,
    risk_profile: RiskProfile | None = None,
    q: str | None = None,
    exclude_owner_id: uuid.UUID | None = None,
) -> Select:
    stmt = (
        select(Listing)
        .join(User, User.id == Listing.owner_id)
        .where(Listing.status == ListingStatus.active, User.is_active.is_(True))
    )
    if kind is not None:
        stmt = stmt.where(Listing.kind == kind)
    if market is not None:
        stmt = stmt.where(Listing.markets.contains([market.value]))
    if risk_profile is not None:
        stmt = stmt.where(Listing.risk_profile == risk_profile)
    if q and q.strip():
        pattern = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(
            or_(
                Listing.title.ilike(pattern, escape="\\"),
                Listing.description.ilike(pattern, escape="\\"),
                User.display_name.ilike(pattern, escape="\\"),
                User.username.ilike(pattern, escape="\\"),
            )
        )
    if exclude_owner_id is not None:
        stmt = stmt.where(Listing.owner_id != exclude_owner_id)
    return stmt


def _order(sort: ListingSort):  # noqa: ANN202 - list of SQL order expressions
    if sort == "popular":
        return [Listing.like_count.desc(), Listing.offer_count.desc(), Listing.view_count.desc()]
    if sort == "amount":
        return [func.coalesce(Listing.amount, Listing.min_capital).desc().nulls_last()]
    return []


async def list_public(
    db: AsyncSession,
    *,
    kind: ListingKind | None = None,
    market: MarketCategory | None = None,
    risk_profile: RiskProfile | None = None,
    q: str | None = None,
    sort: ListingSort = "newest",
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Listing], int]:
    base = public_query(kind=kind, market=market, risk_profile=risk_profile, q=q)
    total = int((await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one())
    order = [*_order(sort), Listing.created_at.desc(), Listing.id.desc()]
    rows = (await db.execute(base.order_by(*order).limit(limit).offset(offset))).scalars().all()
    return list(rows), total


async def list_mine(
    db: AsyncSession,
    owner: User,
    *,
    status: ListingStatus | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Listing], int]:
    where = [Listing.owner_id == owner.id]
    if status is not None:
        where.append(Listing.status == status)
    total = int((await db.execute(select(func.count()).select_from(Listing).where(*where))).scalar_one())
    rows = (
        await db.execute(
            select(Listing).where(*where).order_by(Listing.created_at.desc(), Listing.id.desc()).limit(limit).offset(offset)
        )
    ).scalars().all()
    return list(rows), total


async def counts_for_owner(db: AsyncSession, owner_id: uuid.UUID) -> ListingCountsOut:
    rows = (
        await db.execute(
            select(Listing.status, func.count()).where(Listing.owner_id == owner_id).group_by(Listing.status)
        )
    ).all()
    counts = {str(getattr(s, "value", s)): int(n) for s, n in rows}
    return ListingCountsOut(**{k: counts.get(k, 0) for k in ("draft", "active", "paused", "closed")})


async def offers_on_listing(db: AsyncSession, listing_id: uuid.UUID, *, limit: int = 50) -> list[Offer]:
    rows = (
        await db.execute(
            select(Offer)
            .where(Offer.listing_id == listing_id)
            .order_by(
                (Offer.status == OfferStatus.pending).desc(), Offer.created_at.desc(), Offer.id.desc()
            )
            .limit(limit)
        )
    ).scalars().all()
    return list(rows)


async def pending_offer_count(db: AsyncSession, listing_id: uuid.UUID) -> int:
    q = (
        select(func.count())
        .select_from(Offer)
        .where(Offer.listing_id == listing_id, Offer.status == OfferStatus.pending)
    )
    return int((await db.execute(q)).scalar_one())


async def my_pending_offer_id(db: AsyncSession, listing_id: uuid.UUID, user_id: uuid.UUID) -> uuid.UUID | None:
    q = select(Offer.id).where(
        Offer.listing_id == listing_id, Offer.from_user_id == user_id, Offer.status == OfferStatus.pending
    )
    return (await db.execute(q)).scalars().first()


# --- viewer flags / serialisation -------------------------------------------------------------------------


async def viewer_flags(
    db: AsyncSession, viewer: User | None, listing_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[str, bool]]:
    """{listing_id: {"is_saved", "is_liked"}} for the viewer (empty for anonymous)."""
    if viewer is None or not listing_ids:
        return {}
    saved = set(
        (
            await db.execute(
                select(Favorite.listing_id).where(Favorite.user_id == viewer.id, Favorite.listing_id.in_(listing_ids))
            )
        ).scalars()
    )
    liked = set(
        (
            await db.execute(
                select(Interaction.target_id).where(
                    Interaction.user_id == viewer.id,
                    Interaction.target_type == InteractionTargetType.listing,
                    Interaction.action == InteractionAction.like,
                    Interaction.target_id.in_(listing_ids),
                )
            )
        ).scalars()
    )
    return {lid: {"is_saved": lid in saved, "is_liked": lid in liked} for lid in listing_ids}


def to_out(listing: Listing, *, viewer: User | None = None, flags: dict[str, bool] | None = None) -> ListingOut:
    out = ListingOut.model_validate(listing)
    if viewer is not None:
        out.is_owner = listing.owner_id == viewer.id
        out.is_saved = bool((flags or {}).get("is_saved", False))
        out.is_liked = bool((flags or {}).get("is_liked", False))
    return out


async def to_outs(db: AsyncSession, listings: list[Listing], viewer: User | None) -> list[ListingOut]:
    flags = await viewer_flags(db, viewer, [x.id for x in listings])
    return [to_out(x, viewer=viewer, flags=flags.get(x.id)) for x in listings]


async def record_view(db: AsyncSession, viewer: User | None, listing: Listing) -> bool:
    """Count one view per viewer (interaction `view`); owners never count. Returns True on a new view."""
    if viewer is None or viewer.id == listing.owner_id:
        return False
    created = await record_interaction(db, viewer.id, InteractionTargetType.listing, listing.id, InteractionAction.view)
    if created:
        await bump_counter(db, listing.id, "view_count", 1)
        await db.refresh(listing, attribute_names=["view_count"])
    return created


async def favorites_of(db: AsyncSession, user: User, *, limit: int = 20, offset: int = 0) -> tuple[list[Listing], int]:
    """Saved listings ("Kaydet"), newest save first."""
    where = [Favorite.user_id == user.id]
    total = int((await db.execute(select(func.count()).select_from(Favorite).where(*where))).scalar_one())
    rows = (
        await db.execute(
            select(Listing)
            .join(Favorite, and_(Favorite.listing_id == Listing.id, Favorite.user_id == user.id))
            .order_by(Favorite.created_at.desc(), Listing.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return list(rows), total


__all__ = [
    "CAPITAL_FIELDS",
    "KIND_FOR_ROLE",
    "SERVICE_FIELDS",
    "build_release_tx",
    "build_reserve_tx",
    "bump_counter",
    "counts_for_owner",
    "create_listing",
    "default_base_asset",
    "favorites_of",
    "get_listing",
    "get_owned_listing",
    "has_interaction",
    "list_mine",
    "list_public",
    "listing_ref_bytes",
    "my_pending_offer_id",
    "offers_on_listing",
    "pending_offer_count",
    "public_query",
    "record_interaction",
    "record_view",
    "resolve_base_asset",
    "set_status",
    "to_out",
    "to_outs",
    "update_listing",
    "viewer_flags",
]
