"""interactions (discover swipes), follows and favorites — DESIGN §2.1."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.models.enums import InteractionAction, InteractionTargetType


class Interaction(UUIDPrimaryKeyMixin, Base):
    """One user action on a listing or a user card. `target_id` is polymorphic (no FK)."""

    __tablename__ = "interactions"
    __table_args__ = (
        UniqueConstraint("user_id", "target_type", "target_id", "action", name="uq_interactions_user_target_action"),
        Index("ix_interactions_target", "target_type", "target_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_type: Mapped[InteractionTargetType] = mapped_column(
        Enum(InteractionTargetType, native_enum=False, length=32), nullable=False
    )
    target_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    action: Mapped[InteractionAction] = mapped_column(
        Enum(InteractionAction, native_enum=False, length=32), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Follow(Base):
    """follower (any role) -> trader."""

    __tablename__ = "follows"

    follower_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    trader_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    trader = relationship("User", lazy="joined", foreign_keys=[trader_id])


class Favorite(Base):
    """user -> saved listing."""

    __tablename__ = "favorites"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    listing_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("listings.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    listing = relationship("Listing", lazy="joined", foreign_keys=[listing_id])
