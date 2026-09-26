"""Discover (Keşfet, Figma 2a-2d) I/O models: the swipe feed and the swipe actions."""
from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.models.enums import InteractionAction, InteractionTargetType, ListingKind
from app.schemas.listings import ListingOut


class DiscoverCardOut(BaseModel):
    """One swipe card. A customer sees service listings rendered as trader cards (owner + stats), a
    trader sees capital listings. `target_type`/`target_id` are what POST /discover/{type}/{id}/action
    expects for this card; `owner_target_id` lets the client follow / pass the trader behind the card."""

    target_type: InteractionTargetType = InteractionTargetType.listing
    target_id: uuid.UUID
    owner_target_id: uuid.UUID
    kind: ListingKind
    listing: ListingOut
    is_following: bool = False  # viewer follows the card's owner (trader cards)
    is_saved: bool = False
    tags: list[str] = Field(default_factory=list, description="machine keys the client localises")


class DiscoverFeedOut(BaseModel):
    items: list[DiscoverCardOut]
    next_cursor: str | None = None
    remaining: int = Field(description="cards the viewer has not acted on yet (incl. this page)")
    kind: ListingKind


class DiscoverRemainingOut(BaseModel):
    remaining: int
    kind: ListingKind


class DiscoverActionIn(BaseModel):
    action: InteractionAction


class DiscoverActionOut(BaseModel):
    target_type: InteractionTargetType
    target_id: uuid.UUID
    action: InteractionAction
    created: bool = Field(description="False when the same action was already recorded (idempotent)")
    remaining: int
    following: bool | None = None  # after follow
    saved: bool | None = None  # after save
    like_count: int | None = None  # after like on a listing
