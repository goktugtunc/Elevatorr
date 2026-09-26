"""conversations + messages (Figma 9b/9c)."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Conversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Thread between two users, attached to an offer and/or the agreement it produced."""

    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_participant_a", "participant_a"),
        Index("ix_conversations_participant_b", "participant_b"),
    )

    offer_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("offers.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    agreement_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agreements.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    participant_a: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    participant_b: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user_a = relationship("User", lazy="joined", foreign_keys=[participant_a])
    user_b = relationship("User", lazy="joined", foreign_keys=[participant_b])

    def has_participant(self, user_id: uuid.UUID) -> bool:
        return user_id in (self.participant_a, self.participant_b)

    def other_participant(self, user_id: uuid.UUID) -> uuid.UUID:
        return self.participant_b if user_id == self.participant_a else self.participant_a


class Message(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conversation_created", "conversation_id", "created_at"),)

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    sender_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
