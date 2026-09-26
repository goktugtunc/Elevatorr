"""Offers (Figma 2d, 6c/6d): create, inbox / outbox, detail, accept (-> agreement draft), reject, withdraw."""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api_deps import DB, CurrentUser, SettingsDep
from app.api_paging import PageDep
from app.models import Listing, OfferStatus
from app.schemas.common import Page
from app.schemas.offers import (
    AgreementDraftOut,
    OfferAcceptOut,
    OfferBox,
    OfferCreateIn,
    OfferOut,
    OfferRejectIn,
    OfferStatsOut,
)
from app.services import offers as offers_service

router = APIRouter(prefix="/offers", tags=["offers"])


@router.post("", response_model=OfferOut, status_code=201)
async def create_offer(body: OfferCreateIn, user: CurrentUser, db: DB, settings: SettingsDep) -> OfferOut:
    """Trader -> capital listing ("Teklif Ver"), customer -> service listing ("Teklif İste"). Opens a
    conversation with the listing owner (the note becomes its first message) and notifies them."""
    offer = await offers_service.create_offer(db, settings, user, body)
    conv = await offers_service.conversation_for_offer(db, offer.id)
    return offers_service.offer_out(offer, viewer=user, conversation_id=conv.id if conv else None)


@router.get("", response_model=Page[OfferOut])
async def list_offers(
    user: CurrentUser,
    db: DB,
    page: PageDep,
    box: Annotated[OfferBox, Query(description="inbox (received) | outbox (sent) | all")] = "inbox",
    status: Annotated[OfferStatus | None, Query()] = None,
    listing_id: Annotated[uuid.UUID | None, Query()] = None,
) -> Page[OfferOut]:
    rows, total = await offers_service.list_offers(
        db, user, box=box, status=status, listing_id=listing_id, limit=page.limit, offset=page.offset
    )
    items = await offers_service.offer_outs(db, rows, viewer=user)
    return Page[OfferOut](items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/stats", response_model=OfferStatsOut)
async def offer_stats(user: CurrentUser, db: DB) -> OfferStatsOut:
    inbox, outbox = await offers_service.pending_counts(db, user.id)
    return OfferStatsOut(pending_inbox=inbox, pending_outbox=outbox)


@router.get("/{offer_id}", response_model=OfferOut)
async def get_offer(offer_id: uuid.UUID, user: CurrentUser, db: DB) -> OfferOut:
    offer = await offers_service.get_offer_for(db, user, offer_id)
    conv = await offers_service.conversation_for_offer(db, offer.id)
    return offers_service.offer_out(offer, viewer=user, conversation_id=conv.id if conv else None)


@router.post("/{offer_id}/accept", response_model=OfferAcceptOut)
async def accept_offer(offer_id: uuid.UUID, user: CurrentUser, db: DB) -> OfferAcceptOut:
    """Recipient accepts: creates the `agreements` draft with the negotiated terms and links the thread.
    `next_action` names the on-chain step the acceptor performs next (POST /agreements/{id}/tx/{action}):
    `open_reserved` when the listing's reservation covers the principal, else `open`; `propose` for a trader."""
    offer, agreement, conv = await offers_service.accept_offer(db, user, offer_id)
    listing = await db.get(Listing, agreement.listing_id) if agreement.listing_id is not None else None
    return OfferAcceptOut(
        offer=offers_service.offer_out(offer, viewer=user, conversation_id=conv.id),
        agreement=AgreementDraftOut.model_validate(agreement),
        conversation_id=conv.id,
        next_action=offers_service.next_onchain_action(agreement.proposer_role, listing, agreement.principal),
    )


@router.post("/{offer_id}/reject", response_model=OfferOut)
async def reject_offer(
    offer_id: uuid.UUID, user: CurrentUser, db: DB, body: OfferRejectIn | None = None
) -> OfferOut:
    offer = await offers_service.reject_offer(db, user, offer_id, body.reason if body else None)
    conv = await offers_service.conversation_for_offer(db, offer.id)
    return offers_service.offer_out(offer, viewer=user, conversation_id=conv.id if conv else None)


@router.post("/{offer_id}/withdraw", response_model=OfferOut)
async def withdraw_offer(offer_id: uuid.UUID, user: CurrentUser, db: DB) -> OfferOut:
    offer = await offers_service.withdraw_offer(db, user, offer_id)
    conv = await offers_service.conversation_for_offer(db, offer.id)
    return offers_service.offer_out(offer, viewer=user, conversation_id=conv.id if conv else None)


__all__ = ["router"]
