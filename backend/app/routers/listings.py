"""Listings (Figma 5a-5d, 9a): CRUD, mine, detail (+offers for the owner), reserve / release capital,
pause / resume / close (02-api-sozlesme §3.3–3.4, §8.3)."""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api_deps import DB, ChainDep, CurrentUser, OptionalUser, SettingsDep
from app.api_paging import PageDep
from app.models import ListingKind, ListingStatus, MarketCategory, RiskProfile
from app.schemas.common import Page
from app.schemas.listings import (
    ListingCountsOut,
    ListingCreateIn,
    ListingDetailOut,
    ListingOut,
    ListingSort,
    ListingUpdateIn,
    ReleaseIn,
)
from app.schemas.tx import UnsignedTxOut
from app.services import agreements as agreements_service
from app.services import listings as listings_service
from app.services import offers as offers_service

router = APIRouter(prefix="/listings", tags=["listings"])


@router.post("", response_model=ListingOut, status_code=201)
async def create_listing(body: ListingCreateIn, user: CurrentUser, db: DB, settings: SettingsDep) -> ListingOut:
    """Customer -> capital listing (5a, born as `draft` until `reserve` confirms), trader -> service listing (5b)."""
    listing = await listings_service.create_listing(db, settings, user, body)
    return listings_service.to_out(listing, viewer=user)


@router.get("", response_model=Page[ListingOut])
async def list_listings(
    db: DB,
    viewer: OptionalUser,
    page: PageDep,
    kind: Annotated[ListingKind | None, Query()] = None,
    market: Annotated[MarketCategory | None, Query()] = None,
    risk_profile: Annotated[RiskProfile | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=80, description="title / description / owner search")] = None,
    sort: Annotated[ListingSort, Query()] = "newest",
) -> Page[ListingOut]:
    """Public, active listings of active users."""
    rows, total = await listings_service.list_public(
        db, kind=kind, market=market, risk_profile=risk_profile, q=q, sort=sort, limit=page.limit, offset=page.offset
    )
    items = await listings_service.to_outs(db, rows, viewer)
    return Page[ListingOut](items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/mine", response_model=Page[ListingOut])
async def my_listings(
    user: CurrentUser,
    db: DB,
    page: PageDep,
    status: Annotated[ListingStatus | None, Query(description="draft | active | paused | closed")] = None,
) -> Page[ListingOut]:
    """"İlanlarım" (5d) — every status."""
    rows, total = await listings_service.list_mine(db, user, status=status, limit=page.limit, offset=page.offset)
    items = await listings_service.to_outs(db, rows, user)
    return Page[ListingOut](items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/mine/counts", response_model=ListingCountsOut)
async def my_listing_counts(user: CurrentUser, db: DB) -> ListingCountsOut:
    return await listings_service.counts_for_owner(db, user.id)


@router.get("/saved", response_model=Page[ListingOut])
async def saved_listings(user: CurrentUser, db: DB, page: PageDep) -> Page[ListingOut]:
    """Listings the user saved with the discover "Kaydet" action."""
    rows, total = await listings_service.favorites_of(db, user, limit=page.limit, offset=page.offset)
    items = await listings_service.to_outs(db, rows, user)
    return Page[ListingOut](items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/{listing_id}", response_model=ListingDetailOut)
async def get_listing(listing_id: uuid.UUID, db: DB, viewer: OptionalUser) -> ListingDetailOut:
    """Detail with counters. The owner also gets the offers on the listing; a non-owner viewer's visit
    counts as one view (once per user) and reveals their own pending offer id, if any."""
    listing = await listings_service.get_listing(db, listing_id)
    is_owner = viewer is not None and (viewer.id == listing.owner_id or viewer.is_admin)
    if viewer is not None and not is_owner:
        await listings_service.record_view(db, viewer, listing)
    flags = (await listings_service.viewer_flags(db, viewer, [listing.id])).get(listing.id)
    base = listings_service.to_out(listing, viewer=viewer, flags=flags)
    out = ListingDetailOut(**base.model_dump())
    out.pending_offers = await listings_service.pending_offer_count(db, listing.id)
    if is_owner:
        offers = await listings_service.offers_on_listing(db, listing.id)
        out.offers = await offers_service.offer_outs(db, offers, viewer=viewer)
    elif viewer is not None:
        out.my_offer_id = await listings_service.my_pending_offer_id(db, listing.id, viewer.id)
    return out


@router.patch("/{listing_id}", response_model=ListingOut)
async def update_listing(
    listing_id: uuid.UUID, body: ListingUpdateIn, user: CurrentUser, db: DB, settings: SettingsDep
) -> ListingOut:
    """Partial update; `amount` of a capital listing with reserved capital is locked (409 `amount_locked`)."""
    listing = await listings_service.update_listing(db, settings, user, listing_id, body)
    return listings_service.to_out(listing, viewer=user)


@router.post("/{listing_id}/tx/reserve", response_model=UnsignedTxOut)
async def reserve_listing_capital(
    listing_id: uuid.UUID, user: CurrentUser, db: DB, settings: SettingsDep, chain=ChainDep
) -> UnsignedTxOut:
    """Calldata for `reserve(token, amountRaw, listingRef)` (+ `approve` pre-step when the allowance is short):
    the customer moves this listing's capital into the vault. A capital listing is a draft until the
    `Reserved` event is indexed, so the amount on a public listing is always money the owner holds."""
    listing, unsigned, pending = await listings_service.build_reserve_tx(db, settings, chain, user, listing_id)
    return agreements_service.unsigned_out(pending, unsigned, asset=listing.base_asset)


@router.post("/{listing_id}/tx/release", response_model=UnsignedTxOut)
async def release_listing_capital(
    listing_id: uuid.UUID,
    user: CurrentUser,
    db: DB,
    settings: SettingsDep,
    body: ReleaseIn | None = None,
    chain=ChainDep,
) -> UnsignedTxOut:
    """Calldata for `releaseAll(reservationId)` (no body / `amount: null`) or `release(reservationId, amountRaw)`
    (`amount > 0`): hands locked capital back to the customer. 409 `no_reservation` / `reservation_locked`."""
    _, unsigned, pending = await listings_service.build_release_tx(
        db, settings, chain, user, listing_id, body.amount if body else None
    )
    return agreements_service.unsigned_out(pending, unsigned)


@router.post("/{listing_id}/pause", response_model=ListingOut)
async def pause_listing(listing_id: uuid.UUID, user: CurrentUser, db: DB) -> ListingOut:
    return listings_service.to_out(await listings_service.set_status(db, user, listing_id, "pause"), viewer=user)


@router.post("/{listing_id}/resume", response_model=ListingOut)
async def resume_listing(listing_id: uuid.UUID, user: CurrentUser, db: DB) -> ListingOut:
    return listings_service.to_out(await listings_service.set_status(db, user, listing_id, "resume"), viewer=user)


@router.post("/{listing_id}/close", response_model=ListingOut)
async def close_listing(listing_id: uuid.UUID, user: CurrentUser, db: DB) -> ListingOut:
    """Terminal: a closed listing accepts no new offers and pending offers on it cannot be accepted."""
    return listings_service.to_out(await listings_service.set_status(db, user, listing_id, "close"), viewer=user)


__all__ = ["router"]
