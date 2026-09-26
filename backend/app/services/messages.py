"""Conversations & messages (Figma 9b list / 9c thread). A thread is opened by an offer (offers service)
and linked to the agreement it produces; only its two participants can read or write it."""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models import Conversation, Message, NotificationCategory, User
from app.schemas.messages import ConversationOut, MessageOut
from app.schemas.users import UserOut
from app.services.notifications import notify

log = logging.getLogger(__name__)

MESSAGE_PREVIEW_LEN = 120


async def get_conversation(db: AsyncSession, user: User, conversation_id: uuid.UUID) -> Conversation:
    conv = await db.get(Conversation, conversation_id)
    if conv is None or (not conv.has_participant(user.id) and not user.is_admin):
        raise NotFoundError("Conversation not found", code="conversation_not_found")
    return conv


async def get_or_create_direct(db: AsyncSession, a: User, b_id: uuid.UUID) -> Conversation:
    """The thread between two users, opened from a profile or a listing.

    Bir ikilinin tek bir mesaj kutusu vardır: teklif ya da sözleşme o kutuya
    bağlanır, ayrı bir kutu açmaz. Bu yüzden burada `offer_id`/`agreement_id`
    boş olma koşulu aranmaz — arandığında, teklif verilmiş bir kişiye tekrar
    yazmak ikinci bir kutu yaratıyordu. En eskisi seçilir ki iki taraf da aynı
    kutuda buluşsun.
    """
    q = (
        select(Conversation)
        .where(
            or_(
                (Conversation.participant_a == a.id) & (Conversation.participant_b == b_id),
                (Conversation.participant_a == b_id) & (Conversation.participant_b == a.id),
            )
        )
        .order_by(Conversation.created_at)
    )
    conv = (await db.execute(q)).scalars().first()
    if conv is None:
        conv = Conversation(participant_a=a.id, participant_b=b_id)
        db.add(conv)
        await db.flush()
        await db.refresh(conv)
    return conv


async def _unread_by_conversation(
    db: AsyncSession, user_id: uuid.UUID, conversation_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    if not conversation_ids:
        return {}
    q = (
        select(Message.conversation_id, func.count())
        .where(
            Message.conversation_id.in_(conversation_ids),
            Message.sender_id != user_id,
            Message.read_at.is_(None),
        )
        .group_by(Message.conversation_id)
    )
    return {cid: int(n) for cid, n in (await db.execute(q)).all()}


async def _last_messages(db: AsyncSession, conversation_ids: list[uuid.UUID]) -> dict[uuid.UUID, Message]:
    if not conversation_ids:
        return {}
    q = (
        select(Message)
        .where(Message.conversation_id.in_(conversation_ids))
        .distinct(Message.conversation_id)
        .order_by(Message.conversation_id, Message.created_at.desc(), Message.id.desc())
    )
    return {m.conversation_id: m for m in (await db.execute(q)).scalars().all()}


def message_out(message: Message, viewer_id: uuid.UUID | None = None) -> MessageOut:
    out = MessageOut.model_validate(message)
    if viewer_id is not None:
        out.is_mine = message.sender_id == viewer_id
    return out


async def conversation_outs(db: AsyncSession, user: User, convs: list[Conversation]) -> list[ConversationOut]:
    ids = [c.id for c in convs]
    unread = await _unread_by_conversation(db, user.id, ids)
    last = await _last_messages(db, ids)
    out: list[ConversationOut] = []
    for c in convs:
        other = c.user_b if c.participant_a == user.id else c.user_a
        lm = last.get(c.id)
        out.append(
            ConversationOut(
                id=c.id,
                offer_id=c.offer_id,
                agreement_id=c.agreement_id,
                other_user=UserOut.model_validate(other),
                last_message=message_out(lm, user.id) if lm else None,
                last_message_at=c.last_message_at or (lm.created_at if lm else None),
                unread_count=unread.get(c.id, 0),
                created_at=c.created_at,
            )
        )
    return out


async def list_conversations(
    db: AsyncSession, user: User, *, limit: int = 20, offset: int = 0
) -> tuple[list[ConversationOut], int]:
    where = [or_(Conversation.participant_a == user.id, Conversation.participant_b == user.id)]
    total = int((await db.execute(select(func.count()).select_from(Conversation).where(*where))).scalar_one())
    rows = (
        await db.execute(
            select(Conversation)
            .where(*where)
            .order_by(
                func.coalesce(Conversation.last_message_at, Conversation.created_at).desc(), Conversation.id.desc()
            )
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return await conversation_outs(db, user, list(rows)), total


async def list_messages(
    db: AsyncSession,
    user: User,
    conversation_id: uuid.UUID,
    *,
    after: datetime | None = None,
    before: datetime | None = None,
    limit: int = 50,
) -> tuple[list[MessageOut], bool]:
    """Chronological page. `after` = poll for newer messages (returns the oldest `limit` after it);
    `before` = scroll back (returns the newest `limit` before it). Returns (items, has_more)."""
    conv = await get_conversation(db, user, conversation_id)
    where = [Message.conversation_id == conv.id]
    if after is not None:
        where.append(Message.created_at > after)
    if before is not None:
        where.append(Message.created_at < before)
    if after is not None and before is None:
        order = [Message.created_at.asc(), Message.id.asc()]
    else:
        order = [Message.created_at.desc(), Message.id.desc()]
    rows = list((await db.execute(select(Message).where(*where).order_by(*order).limit(limit + 1))).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    rows.sort(key=lambda m: (m.created_at, m.id))
    return [message_out(m, user.id) for m in rows], has_more


async def send_message(db: AsyncSession, user: User, conversation_id: uuid.UUID, body: str) -> Message:
    conv = await get_conversation(db, user, conversation_id)
    now = datetime.now(UTC)
    msg = Message(conversation_id=conv.id, sender_id=user.id, body=body, created_at=now)
    db.add(msg)
    conv.last_message_at = now
    await db.flush()
    await db.refresh(msg)
    other_id = conv.other_participant(user.id) if conv.has_participant(user.id) else None
    if other_id is not None:
        preview = body if len(body) <= MESSAGE_PREVIEW_LEN else body[: MESSAGE_PREVIEW_LEN - 1] + "…"
        await notify(
            db,
            other_id,
            "message_received",
            user.display_name,
            preview,
            {"conversation_id": str(conv.id), "message_id": str(msg.id), "sender_id": str(user.id)},
            category=NotificationCategory.system,
        )
    return msg


async def mark_read(db: AsyncSession, user: User, conversation_id: uuid.UUID) -> int:
    """Mark every message from the other participant as read; returns the number of rows changed."""
    conv = await get_conversation(db, user, conversation_id)
    result = await db.execute(
        update(Message)
        .where(Message.conversation_id == conv.id, Message.sender_id != user.id, Message.read_at.is_(None))
        .values(read_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)


async def unread_summary(db: AsyncSession, user: User) -> tuple[int, int]:
    """(conversations with unread messages, unread messages) for the tab badge."""
    q = (
        select(func.count(func.distinct(Message.conversation_id)), func.count())
        .select_from(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            or_(Conversation.participant_a == user.id, Conversation.participant_b == user.id),
            Message.sender_id != user.id,
            Message.read_at.is_(None),
        )
    )
    convs, msgs = (await db.execute(q)).one()
    return int(convs or 0), int(msgs or 0)
