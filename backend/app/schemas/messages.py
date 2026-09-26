"""Conversation / message I/O models (Figma 9b list, 9c thread)."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import ORMModel
from app.schemas.users import UserOut


class MessageOut(ORMModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    sender_id: uuid.UUID
    body: str
    created_at: datetime
    read_at: datetime | None = None
    is_mine: bool | None = None


class MessageCreateIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    body: str = Field(min_length=1, max_length=4000)


class ConversationCreateIn(BaseModel):
    """Teklife bağlı olmayan doğrudan sohbet açar (Keşfet kartı / trader profili)."""

    user_id: uuid.UUID


class ConversationOut(BaseModel):
    id: uuid.UUID
    offer_id: uuid.UUID | None = None
    agreement_id: uuid.UUID | None = None
    other_user: UserOut
    last_message: MessageOut | None = None
    last_message_at: datetime | None = None
    unread_count: int = 0
    created_at: datetime


class MessagesPageOut(BaseModel):
    conversation_id: uuid.UUID
    items: list[MessageOut] = Field(description="chronological (oldest first)")
    has_more: bool = False


class ConversationReadOut(BaseModel):
    conversation_id: uuid.UUID
    updated: int


class ConversationsUnreadOut(BaseModel):
    conversations: int = Field(description="threads with at least one unread message")
    messages: int
