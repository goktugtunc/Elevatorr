"""Rating I/O models: a customer rates the trader once a Sözleşme is settled."""
from __future__ import annotations

import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import Amount
from app.schemas.users import RatingOut


class RatingCreateIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    score: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=1000)


class RatingCreatedOut(BaseModel):
    rating: RatingOut
    trader_id: uuid.UUID
    rating_avg: Amount = Decimal("0")
    rating_count: int = 0


__all__ = ["RatingCreateIn", "RatingCreatedOut", "RatingOut"]
