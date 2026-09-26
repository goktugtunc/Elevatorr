"""Messages (Figma 9b/9c): conversation list, thread (poll with `after`), send, mark read, unread badge."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from app.api_deps import DB, CurrentUser
from app.api_paging import PageDep
from app.schemas.common import Page
from app.schemas.messages import (
    ConversationCreateIn,
    ConversationOut,
    ConversationReadOut,
    ConversationsUnreadOut,
    MessageCreateIn,
    MessageOut,
    MessagesPageOut,
)
from app.core.errors import ValidationError
from app.services import messages as messages_service
from app.services import users as users_service

router = APIRouter(prefix="/conversations", tags=["messages"])


@router.get("", response_model=Page[ConversationOut])
async def list_conversations(user: CurrentUser, db: DB, page: PageDep) -> Page[ConversationOut]:
    items, total = await messages_service.list_conversations(db, user, limit=page.limit, offset=page.offset)
    return Page[ConversationOut](items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("", response_model=ConversationOut, status_code=201)
async def start_conversation(data: ConversationCreateIn, user: CurrentUser, db: DB) -> ConversationOut:
    """Open (or reuse) a direct thread with another user.

    Idempotent: `get_or_create_direct` returns the existing offer-less thread when there is
    one, so the client may call this every time the user taps "message" without creating
    duplicates. The service function already existed for this ("opened from a profile") but
    was never routed.
    """
    if data.user_id == user.id:
        raise ValidationError("You cannot message yourself", code="own_profile")
    other = await users_service.get_public_user(db, data.user_id)
    conv = await messages_service.get_or_create_direct(db, user, other.id)
    return (await messages_service.conversation_outs(db, user, [conv]))[0]


@router.get("/unread-count", response_model=ConversationsUnreadOut)
async def unread_count(user: CurrentUser, db: DB) -> ConversationsUnreadOut:
    convs, msgs = await messages_service.unread_summary(db, user)
    return ConversationsUnreadOut(conversations=convs, messages=msgs)


@router.get("/{conversation_id}", response_model=ConversationOut)
async def get_conversation(conversation_id: uuid.UUID, user: CurrentUser, db: DB) -> ConversationOut:
    conv = await messages_service.get_conversation(db, user, conversation_id)
    return (await messages_service.conversation_outs(db, user, [conv]))[0]


@router.get("/{conversation_id}/messages", response_model=MessagesPageOut)
async def list_messages(
    conversation_id: uuid.UUID,
    user: CurrentUser,
    db: DB,
    after: Annotated[datetime | None, Query(description="only messages created after this instant (polling)")] = None,
    before: Annotated[datetime | None, Query(description="only messages created before this instant (scroll back)")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> MessagesPageOut:
    items, has_more = await messages_service.list_messages(
        db, user, conversation_id, after=after, before=before, limit=limit
    )
    return MessagesPageOut(conversation_id=conversation_id, items=items, has_more=has_more)


@router.post("/{conversation_id}/messages", response_model=MessageOut, status_code=201)
async def send_message(conversation_id: uuid.UUID, body: MessageCreateIn, user: CurrentUser, db: DB) -> MessageOut:
    msg = await messages_service.send_message(db, user, conversation_id, body.body)
    return messages_service.message_out(msg, user.id)


@router.post("/{conversation_id}/read", response_model=ConversationReadOut)
async def mark_read(conversation_id: uuid.UUID, user: CurrentUser, db: DB) -> ConversationReadOut:
    updated = await messages_service.mark_read(db, user, conversation_id)
    return ConversationReadOut(conversation_id=conversation_id, updated=updated)
