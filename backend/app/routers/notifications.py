"""In-app notifications for the current user (Figma 7a-7c: tabs by category)."""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api_deps import DB, CurrentUser
from app.api_paging import PageDep
from app.core.errors import NotFoundError, ValidationError
from app.models import Notification, NotificationCategory
from app.schemas.common import Page
from app.schemas.notifications import MarkReadIn, MarkReadOut, NotificationOut, PushTokenIn, UnreadCountOut
from app.schemas.users import MeOut
from app.services import notifications as notifications_service

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=Page[NotificationOut])
async def list_notifications(
    user: CurrentUser,
    db: DB,
    page: PageDep,
    category: Annotated[NotificationCategory | None, Query(description="tab filter")] = None,
    unread_only: Annotated[bool, Query()] = False,
) -> Page[NotificationOut]:
    items, total = await notifications_service.list_notifications(
        db, user.id, category=category, unread_only=unread_only, limit=page.limit, offset=page.offset
    )
    return Page[NotificationOut](
        items=[NotificationOut.model_validate(n) for n in items], total=total, limit=page.limit, offset=page.offset
    )


@router.get("/unread-count", response_model=UnreadCountOut)
async def unread_count(user: CurrentUser, db: DB) -> UnreadCountOut:
    total, by_category = await notifications_service.unread_count(db, user.id)
    return UnreadCountOut(unread=total, by_category=by_category)


@router.post("/read", response_model=MarkReadOut)
async def mark_read(body: MarkReadIn, user: CurrentUser, db: DB) -> MarkReadOut:
    """Mark the given notifications (or all, optionally one category) as read. Only the caller's rows."""
    updated = await notifications_service.mark_read(
        db, user.id, ids=body.ids, mark_all=body.all, category=body.category
    )
    return MarkReadOut(updated=updated)


@router.post("/{notification_id}/read", response_model=MarkReadOut)
async def mark_one_read(notification_id: uuid.UUID, user: CurrentUser, db: DB) -> MarkReadOut:
    """Mark one notification as read (404 when it is not the caller's; 0 when it was already read)."""
    n = await db.get(Notification, notification_id)
    if n is None or n.user_id != user.id:
        raise NotFoundError("Notification not found", code="notification_not_found")
    updated = await notifications_service.mark_read(db, user.id, ids=[notification_id])
    return MarkReadOut(updated=updated)


@router.put("/push-token", response_model=MeOut)
async def set_push_token(body: PushTokenIn, user: CurrentUser, db: DB) -> MeOut:
    """Register (or with null: unregister) the device's Expo push token."""
    if body.expo_push_token is not None and not notifications_service.is_expo_push_token(body.expo_push_token):
        raise ValidationError("expo_push_token must look like ExponentPushToken[...]", code="invalid_expo_token")
    user.expo_push_token = body.expo_push_token
    await db.flush()
    await db.refresh(user)
    return MeOut.model_validate(user)


@router.get("/{notification_id}", response_model=NotificationOut)
async def get_notification(notification_id: uuid.UUID, user: CurrentUser, db: DB) -> NotificationOut:
    n = await db.get(Notification, notification_id)
    if n is None or n.user_id != user.id:
        raise NotFoundError("Notification not found", code="notification_not_found")
    return NotificationOut.model_validate(n)
