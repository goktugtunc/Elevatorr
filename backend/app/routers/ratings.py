"""Ratings: `POST /agreements/{id}/rating` (customer, settled agreements only) and `GET` of that rating.

Only the rating path lives here — every other `/agreements/...` route belongs to the agreements router."""
from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.api_deps import DB, CurrentUser
from app.core.errors import NotFoundError
from app.models import User
from app.schemas.ratings import RatingCreatedOut, RatingCreateIn, RatingOut
from app.services import ratings as ratings_service

router = APIRouter(prefix="/agreements", tags=["ratings"])


@router.post("/{agreement_id}/rating", response_model=RatingCreatedOut, status_code=201)
async def rate_agreement(agreement_id: uuid.UUID, body: RatingCreateIn, user: CurrentUser, db: DB) -> RatingCreatedOut:
    """The customer rates the trader once the Sözleşme is settled (one rating per agreement)."""
    rating, summary = await ratings_service.rate_agreement(db, user, agreement_id, body)
    return RatingCreatedOut(
        rating=ratings_service.rating_out(rating, user),
        trader_id=rating.trader_id,
        rating_avg=summary.avg,
        rating_count=summary.count,
    )


@router.get("/{agreement_id}/rating", response_model=RatingOut)
async def get_rating(agreement_id: uuid.UUID, user: CurrentUser, db: DB) -> RatingOut:
    """The rating of an agreement the caller is party to (404 until the customer rates it)."""
    _, rating = await ratings_service.get_rating(db, user, agreement_id)
    if rating is None:
        raise NotFoundError("Agreement not rated yet", code="rating_not_found")
    customer = await db.get(User, rating.customer_id)
    return ratings_service.rating_out(rating, customer)
