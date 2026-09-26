"""Offers (Figma 2d "Teklif Ver", 6c/6d "Teklif İste"): create, inbox/outbox, accept -> Agreement draft +
conversation + notifications, reject / withdraw, and the worker's `expire_stale` (03 §6.5).

Direction is derived from the sender's role and checked against the listing kind:
* trader  -> capital listing  = `trader_to_customer` (the trader offers to manage the customer's capital)
* customer -> service listing = `customer_to_trader` (the customer asks the trader to manage `amount`)
Accepting copies the negotiated terms into an `agreements` row (status `draft`): principal = amount,
duration_secs = duration_days × 86400, commission / max drawdown bps, base asset, risk profile and
`listing_ref = sha256(str(offer.id))` (the bytes32 the contract stores). Nothing touches the chain
here — the agreements slice builds the `open` / `open_reserved` / `propose` calldata the parties send.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, StateError, ValidationError
from app.models import (
    Agreement,
    AgreementStatus,
    Conversation,
    InteractionAction,
    InteractionTargetType,
    Listing,
    ListingKind,
    ListingStatus,
    Message,
    NotificationCategory,
    Offer,
    OfferDirection,
    OfferStatus,
    User,
    UserRole,
)
from app.schemas.offers import NextAction, OfferBox, OfferCreateIn, OfferOut
from app.services.chain.amounts import AmountError, format_amount, parse_amount
from app.services.listings import bump_counter, get_listing, record_interaction, resolve_base_asset
from app.services.notifications import notify

log = logging.getLogger(__name__)

DEFAULT_MAX_DRAWDOWN_BPS = 10_000
DIRECTION_FOR_ROLE = {
    UserRole.trader: (OfferDirection.trader_to_customer, ListingKind.capital),
    UserRole.customer: (OfferDirection.customer_to_trader, ListingKind.service),
}


def listing_ref_for(offer_id: uuid.UUID) -> str:
    """hex sha256 of the canonical offer id string — the contract's `Terms.listingRef` (bytes32)."""
    return hashlib.sha256(str(offer_id).encode("utf-8")).hexdigest()


# --- lookups ---------------------------------------------------------------------------------------------


async def get_offer(db: AsyncSession, offer_id: uuid.UUID, *, for_update: bool = False) -> Offer:
    if for_update:
        await db.execute(select(Offer.id).where(Offer.id == offer_id).with_for_update())
    offer = await db.get(Offer, offer_id, populate_existing=for_update)
    if offer is None:
        raise NotFoundError("Offer not found", code="offer_not_found")
    return offer


async def get_offer_for(db: AsyncSession, user: User, offer_id: uuid.UUID, *, for_update: bool = False) -> Offer:
    """An offer the user is party to (sender or recipient); anything else is a 404."""
    offer = await get_offer(db, offer_id, for_update=for_update)
    if user.id not in (offer.from_user_id, offer.to_user_id) and not user.is_admin:
        raise NotFoundError("Offer not found", code="offer_not_found")
    return offer


async def conversation_for_offer(db: AsyncSession, offer_id: uuid.UUID) -> Conversation | None:
    return (await db.execute(select(Conversation).where(Conversation.offer_id == offer_id))).scalar_one_or_none()


async def conversation_ids(db: AsyncSession, offer_ids: list[uuid.UUID]) -> dict[uuid.UUID, uuid.UUID]:
    if not offer_ids:
        return {}
    rows = (
        await db.execute(
            select(Conversation.offer_id, Conversation.id).where(Conversation.offer_id.in_(offer_ids))
        )
    ).all()
    return {oid: cid for oid, cid in rows if oid is not None}


# --- create -----------------------------------------------------------------------------------------------


def _ensure_pending(offer: Offer, now: datetime) -> None:
    """Pure check (the request transaction is rolled back on error, so nothing is mutated here); an
    offer past `expires_at` that the worker has not swept yet is reported as expired."""
    if offer.status is not OfferStatus.pending:
        raise StateError(
            f"Offer is {offer.status.value}", code="offer_not_pending", details={"status": offer.status.value}
        )
    if offer.expires_at <= now:
        raise StateError("Offer has expired", code="offer_expired", details={"status": OfferStatus.expired.value})


async def create_offer(db: AsyncSession, settings: Settings, sender: User, data: OfferCreateIn) -> Offer:
    listing = await get_listing(db, data.listing_id, for_update=True)
    if listing.status is not ListingStatus.active:
        raise StateError("Listing is not active", code="listing_not_active", details={"status": listing.status.value})
    if listing.owner_id == sender.id:
        raise ValidationError("You cannot make an offer on your own listing", code="own_listing")
    direction, expected_kind = DIRECTION_FOR_ROLE[sender.role]
    if listing.kind is not expected_kind:
        raise ValidationError(
            f"A {sender.role.value} can only make offers on {expected_kind.value} listings",
            code="listing_kind_mismatch",
            details={"kind": listing.kind.value},
        )
    owner: User = listing.owner
    if not owner.is_active:
        raise NotFoundError("Listing not found", code="listing_not_found")
    dup = (
        await db.execute(
            select(Offer.id).where(
                Offer.listing_id == listing.id, Offer.from_user_id == sender.id, Offer.status == OfferStatus.pending
            )
        )
    ).scalar_one_or_none()
    if dup is not None:
        raise ConflictError(
            "You already have a pending offer on this listing", code="offer_already_pending", details={"offer_id": str(dup)}
        )

    if direction is OfferDirection.trader_to_customer:
        # the customer's capital defines amount / asset / duration / max loss; the trader prices the service
        amount = data.amount if data.amount is not None else listing.amount
        if data.base_asset_id is not None and data.base_asset_id != listing.base_asset_id:
            raise ValidationError(
                "The base asset is fixed by the capital listing", code="base_asset_fixed",
                details={"base_asset_id": str(listing.base_asset_id)},
            )
        base_asset = listing.base_asset
        duration_days = data.duration_days if data.duration_days is not None else listing.duration_days
        max_drawdown = (
            data.max_drawdown_bps
            if data.max_drawdown_bps is not None
            else (listing.max_loss_bps or DEFAULT_MAX_DRAWDOWN_BPS)
        )
        if listing.max_loss_bps is not None and max_drawdown > listing.max_loss_bps:
            # BE-23: a trader may tighten the customer's max loss, never relax it.
            raise ValidationError(
                "max_drawdown_bps cannot exceed the listing's max_loss_bps",
                code="max_loss_relaxed",
                details={"max_loss_bps": listing.max_loss_bps, "requested": max_drawdown},
            )
        commission = data.commission_bps if data.commission_bps is not None else sender.commission_bps
        if commission is None:
            raise ValidationError("commission_bps is required", code="commission_required")
        exp_min, exp_max = data.expected_return_min_bps, data.expected_return_max_bps
    else:
        if data.amount is None:
            raise ValidationError("amount is required when requesting a trader's service", code="amount_required")
        amount = data.amount
        if data.duration_days is None:
            raise ValidationError("duration_days is required", code="duration_required")
        duration_days = data.duration_days
        base_asset = await resolve_base_asset(db, settings, data.base_asset_id)
        if listing.min_capital is not None and amount < listing.min_capital:
            raise ValidationError(
                f"amount is below the trader's minimum capital ({format_amount(listing.min_capital, base_asset.decimals)})",
                code="below_min_capital",
                details={"min_capital": format_amount(listing.min_capital, base_asset.decimals)},
            )
        max_drawdown = data.max_drawdown_bps if data.max_drawdown_bps is not None else DEFAULT_MAX_DRAWDOWN_BPS
        commission = (
            data.commission_bps
            if data.commission_bps is not None
            else (listing.commission_bps if listing.commission_bps is not None else owner.commission_bps)
        )
        if commission is None:
            raise ValidationError("commission_bps is required", code="commission_required")
        exp_min = data.expected_return_min_bps if data.expected_return_min_bps is not None else listing.expected_return_min_bps
        exp_max = data.expected_return_max_bps if data.expected_return_max_bps is not None else listing.expected_return_max_bps

    if amount is None or amount <= 0:
        raise ValidationError("amount must be positive", code="amount_required")
    if base_asset is None:
        raise ValidationError("The listing has no base asset", code="base_asset_required")
    if duration_days is None:
        raise ValidationError("duration_days is required", code="duration_required")
    try:
        amount = parse_amount(amount, base_asset.decimals)
    except AmountError as e:
        raise ValidationError(e.message, code=e.code, details=e.details) from e

    now = datetime.now(UTC)
    offer = Offer(
        listing_id=listing.id,
        from_user_id=sender.id,
        to_user_id=owner.id,
        direction=direction,
        amount=amount,
        base_asset_id=base_asset.id,
        duration_days=int(duration_days),
        commission_bps=int(commission),
        max_drawdown_bps=int(max_drawdown),
        expected_return_min_bps=exp_min,
        expected_return_max_bps=exp_max,
        note=data.note or None,
        status=OfferStatus.pending,
        expires_at=now + timedelta(hours=data.expires_in_hours),
    )
    db.add(offer)
    await db.flush()

    # side effects: counters, the swipe card is consumed, a chat thread opens, the owner is notified
    await bump_counter(db, listing.id, "offer_count", 1)
    await record_interaction(db, sender.id, InteractionTargetType.listing, listing.id, InteractionAction.offer_request)
    # Bu ikilinin zaten bir sohbeti varsa teklif oraya bağlanır, yenisi açılmaz.
    # Akış "ilgileniyorum → sohbet → sohbetten teklif ver" şeklinde; teklifin
    # ayrı bir kutu açması aynı kişiyle iki mesaj kutusu bırakıyordu.
    conv = (
        await db.execute(
            select(Conversation).where(
                Conversation.offer_id.is_(None),
                or_(
                    (Conversation.participant_a == sender.id) & (Conversation.participant_b == owner.id),
                    (Conversation.participant_a == owner.id) & (Conversation.participant_b == sender.id),
                ),
            )
        )
    ).scalars().first()
    if conv is None:
        conv = Conversation(participant_a=sender.id, participant_b=owner.id)
        db.add(conv)
    conv.offer_id = offer.id
    await db.flush()
    if offer.note:
        db.add(Message(conversation_id=conv.id, sender_id=sender.id, body=offer.note, created_at=now))
        conv.last_message_at = now
        await db.flush()
    await db.refresh(offer)
    symbol = offer.base_asset.symbol
    verb = "sent you an offer" if direction is OfferDirection.trader_to_customer else "asked you for an offer"
    await notify(
        db,
        owner.id,
        "offer_received",
        "New offer",
        f"{sender.display_name} {verb} on your listing: {format_amount(offer.amount, base_asset.decimals)} {symbol} · "
        f"{offer.commission_bps / 100:g}% commission",
        {"offer_id": str(offer.id), "listing_id": str(listing.id), "conversation_id": str(conv.id)},
        category=NotificationCategory.offer,
    )
    log.info("offer created id=%s listing=%s from=%s to=%s", offer.id, listing.id, sender.id, owner.id)
    return offer


# --- queries ----------------------------------------------------------------------------------------------


async def list_offers(
    db: AsyncSession,
    user: User,
    *,
    box: OfferBox = "inbox",
    status: OfferStatus | None = None,
    listing_id: uuid.UUID | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Offer], int]:
    if box == "inbox":
        where = [Offer.to_user_id == user.id]
    elif box == "outbox":
        where = [Offer.from_user_id == user.id]
    else:
        where = [(Offer.to_user_id == user.id) | (Offer.from_user_id == user.id)]
    if status is not None:
        where.append(Offer.status == status)
    if listing_id is not None:
        where.append(Offer.listing_id == listing_id)
    total = int((await db.execute(select(func.count()).select_from(Offer).where(*where))).scalar_one())
    rows = (
        await db.execute(
            select(Offer)
            .where(*where)
            .order_by((Offer.status == OfferStatus.pending).desc(), Offer.created_at.desc(), Offer.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return list(rows), total


async def pending_counts(db: AsyncSession, user_id: uuid.UUID) -> tuple[int, int]:
    """(pending inbox, pending outbox)."""
    q = select(
        func.count().filter(Offer.to_user_id == user_id),
        func.count().filter(Offer.from_user_id == user_id),
    ).where(Offer.status == OfferStatus.pending, (Offer.to_user_id == user_id) | (Offer.from_user_id == user_id))
    inbox, outbox = (await db.execute(q)).one()
    return int(inbox or 0), int(outbox or 0)


def offer_out(offer: Offer, *, viewer: User | None = None, conversation_id: uuid.UUID | None = None) -> OfferOut:
    out = OfferOut.model_validate(offer)
    out.conversation_id = conversation_id
    if viewer is not None:
        out.is_incoming = offer.to_user_id == viewer.id
    return out


async def offer_outs(db: AsyncSession, offers: list[Offer], *, viewer: User | None = None) -> list[OfferOut]:
    convs = await conversation_ids(db, [o.id for o in offers])
    return [offer_out(o, viewer=viewer, conversation_id=convs.get(o.id)) for o in offers]


# --- respond ----------------------------------------------------------------------------------------------


def next_onchain_action(acceptor_role: UserRole, listing: Listing | None = None, principal=None) -> NextAction:  # noqa: ANN001
    """What the acceptor sends next (02-api §8.4): trader -> `propose`; customer -> `open_reserved` when the
    listing's reservation still covers the principal, else `open`."""
    if acceptor_role is not UserRole.customer:
        return "propose"
    if listing is not None and listing.reservation_id is not None and listing.reserved_amount is not None:
        needed = principal if principal is not None else listing.amount
        if needed is not None and listing.reserved_amount >= needed:
            return "open_reserved"
    return "open"


async def accept_offer(db: AsyncSession, user: User, offer_id: uuid.UUID) -> tuple[Offer, Agreement, Conversation]:
    """Recipient accepts: agreement draft (terms copied), thread linked, both parties notified."""
    offer = await get_offer_for(db, user, offer_id, for_update=True)
    if offer.to_user_id != user.id:
        raise ForbiddenError("Only the recipient can accept an offer", code="not_offer_recipient")
    now = datetime.now(UTC)
    _ensure_pending(offer, now)
    listing: Listing = await get_listing(db, offer.listing_id, for_update=True)
    if listing.status is not ListingStatus.active:
        raise StateError("Listing is no longer active", code="listing_not_active", details={"status": listing.status.value})

    if offer.direction is OfferDirection.trader_to_customer:
        customer, trader = offer.to_user, offer.from_user
    else:
        customer, trader = offer.from_user, offer.to_user
    if customer.role is not UserRole.customer or trader.role is not UserRole.trader:
        raise StateError("Offer parties no longer match customer/trader roles", code="offer_roles_invalid")
    proposer_role = user.role if user.role in (UserRole.customer, UserRole.trader) else UserRole.customer

    agreement = Agreement(
        offer_id=offer.id,
        listing_id=listing.id,
        customer_id=customer.id,
        trader_id=trader.id,
        base_asset_id=offer.base_asset_id,
        principal=offer.amount,
        duration_secs=offer.duration_secs,
        commission_bps=offer.commission_bps,
        max_drawdown_bps=offer.max_drawdown_bps,
        risk_profile=listing.risk_profile or customer.risk_profile,
        listing_ref=listing_ref_for(offer.id),
        status=AgreementStatus.draft,
        proposer_role=proposer_role,
    )
    db.add(agreement)
    await db.flush()

    offer.status = OfferStatus.accepted
    offer.responded_at = now
    offer.agreement_id = agreement.id

    conv = await conversation_for_offer(db, offer.id)
    if conv is None:
        conv = Conversation(offer_id=offer.id, participant_a=offer.from_user_id, participant_b=offer.to_user_id)
        db.add(conv)
    conv.agreement_id = agreement.id
    await db.flush()
    await db.refresh(agreement)
    await db.refresh(offer)

    base = agreement.base_asset
    summary = (
        f"{format_amount(agreement.principal, base.decimals)} {base.symbol} · {offer.duration_days} days · "
        f"{agreement.commission_bps / 100:g}% commission"
    )
    data = {
        "offer_id": str(offer.id),
        "agreement_id": str(agreement.id),
        "conversation_id": str(conv.id),
        "next_action": next_onchain_action(proposer_role, listing, agreement.principal),
    }
    sender_id = offer.from_user_id
    await notify(
        db, sender_id, "offer_accepted", "Your offer was accepted",
        f"{user.display_name} accepted your offer: {summary}", data, category=NotificationCategory.offer,
    )
    for uid in (customer.id, trader.id):
        await notify(
            db, uid, "agreement_created", "Agreement draft ready",
            f"{summary}. Confirm it to put the agreement on-chain.", data, category=NotificationCategory.agreement,
        )
    log.info("offer accepted id=%s agreement=%s by=%s", offer.id, agreement.id, user.id)
    return offer, agreement, conv


async def reject_offer(db: AsyncSession, user: User, offer_id: uuid.UUID, reason: str | None = None) -> Offer:
    offer = await get_offer_for(db, user, offer_id, for_update=True)
    if offer.to_user_id != user.id:
        raise ForbiddenError("Only the recipient can reject an offer", code="not_offer_recipient")
    now = datetime.now(UTC)
    _ensure_pending(offer, now)
    offer.status = OfferStatus.rejected
    offer.responded_at = now
    await db.flush()
    await db.refresh(offer)
    conv = await conversation_for_offer(db, offer.id)
    body = f"{user.display_name} turned down your offer" + (f": {reason}" if reason else "")
    await notify(
        db, offer.from_user_id, "offer_rejected", "Offer declined", body,
        {"offer_id": str(offer.id), "conversation_id": str(conv.id) if conv else None},
        category=NotificationCategory.offer,
    )
    return offer


async def withdraw_offer(db: AsyncSession, user: User, offer_id: uuid.UUID) -> Offer:
    offer = await get_offer_for(db, user, offer_id, for_update=True)
    if offer.from_user_id != user.id:
        raise ForbiddenError("Only the sender can withdraw an offer", code="not_offer_sender")
    now = datetime.now(UTC)
    _ensure_pending(offer, now)
    offer.status = OfferStatus.withdrawn
    offer.responded_at = now
    await db.flush()
    await db.refresh(offer)
    await notify(
        db, offer.to_user_id, "offer_withdrawn", "Offer withdrawn",
        f"{user.display_name} withdrew their offer", {"offer_id": str(offer.id)}, category=NotificationCategory.offer,
    )
    return offer


async def expire_stale(db: AsyncSession, now: datetime | None = None) -> int:
    """Worker job: pending offers past `expires_at` -> expired (+ sender notified). Returns the count."""
    now = now or datetime.now(UTC)
    ids = (
        await db.execute(
            select(Offer.id)
            .where(Offer.status == OfferStatus.pending, Offer.expires_at <= now)
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    if not ids:
        return 0
    offers = (await db.execute(select(Offer).where(Offer.id.in_(list(ids))))).scalars().all()
    for offer in offers:
        offer.status = OfferStatus.expired
        offer.responded_at = now
        await notify(
            db, offer.from_user_id, "offer_expired", "Your offer expired",
            f"Your offer on {offer.listing.title} expired without an answer",
            {"offer_id": str(offer.id), "listing_id": str(offer.listing_id)}, category=NotificationCategory.offer,
        )
    await db.flush()
    log.info("expired %d stale offers", len(offers))
    return len(offers)


__all__ = [
    "DEFAULT_MAX_DRAWDOWN_BPS",
    "accept_offer",
    "conversation_for_offer",
    "conversation_ids",
    "create_offer",
    "expire_stale",
    "get_offer",
    "get_offer_for",
    "list_offers",
    "listing_ref_for",
    "next_onchain_action",
    "offer_out",
    "offer_outs",
    "pending_counts",
    "reject_offer",
    "withdraw_offer",
]
