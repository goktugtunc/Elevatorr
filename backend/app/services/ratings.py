"""Ratings: the customer of a *settled* agreement rates the trader once (1..5 + comment); the trader's
`rating_avg` / `rating_count` columns are recomputed from the ratings table."""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, ForbiddenError, NotFoundError, StateError
from app.models import Agreement, AgreementStatus, NotificationCategory, Rating, User
from app.schemas.ratings import RatingCreateIn
from app.schemas.users import RatingOut, RatingsSummary
from app.services import users as users_service
from app.services.notifications import notify

log = logging.getLogger(__name__)


async def _agreement_for_party(db: AsyncSession, user: User, agreement_id: uuid.UUID) -> Agreement:
    ag = await db.get(Agreement, agreement_id)
    if ag is None or (ag.party_role(user.id) is None and not user.is_admin):
        raise NotFoundError("Agreement not found", code="agreement_not_found")
    return ag


def rating_out(rating: Rating, customer: User | None) -> RatingOut:
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


async def get_rating(db: AsyncSession, user: User, agreement_id: uuid.UUID) -> tuple[Agreement, Rating | None]:
    ag = await _agreement_for_party(db, user, agreement_id)
    rating = (await db.execute(select(Rating).where(Rating.agreement_id == ag.id))).scalar_one_or_none()
    return ag, rating


async def rate_agreement(
    db: AsyncSession, user: User, agreement_id: uuid.UUID, data: RatingCreateIn
) -> tuple[Rating, RatingsSummary]:
    ag = await _agreement_for_party(db, user, agreement_id)
    if ag.customer_id != user.id:
        raise ForbiddenError("Only the customer of the agreement can rate the trader", code="not_agreement_customer")
    if ag.status is not AgreementStatus.settled:
        raise StateError(
            "Only a settled agreement can be rated", code="agreement_not_settled", details={"status": ag.status.value}
        )
    existing = (await db.execute(select(Rating.id).where(Rating.agreement_id == ag.id))).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("This agreement was already rated", code="already_rated")
    rating = Rating(
        agreement_id=ag.id, customer_id=ag.customer_id, trader_id=ag.trader_id, score=data.score, comment=data.comment
    )
    try:
        async with db.begin_nested():
            db.add(rating)
            await db.flush()
    except IntegrityError as e:
        raise ConflictError("This agreement was already rated", code="already_rated") from e
    await db.refresh(rating)
    summary = await users_service.refresh_rating_stats(db, ag.trader_id)
    await notify(
        db,
        ag.trader_id,
        "rating_received",
        "New rating",
        f"{user.display_name} rated your agreement {data.score}/5",
        {"agreement_id": str(ag.id), "rating_id": str(rating.id), "score": data.score},
        category=NotificationCategory.agreement,
    )
    log.info("rating created agreement=%s trader=%s score=%s", ag.id, ag.trader_id, data.score)
    return rating, summary
