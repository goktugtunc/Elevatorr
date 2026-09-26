"""In-app notifications + Expo push delivery.

`notify()` is the single entry point every other service uses to inform a user about an event; it
only flushes (the request session / worker transaction owns the commit). Push delivery happens in
the worker's `push` job (`deliver_pending_pushes`), which finds notifications not yet pushed
(`data.push_sent` absent), POSTs them to the Expo push API (`send_expo_push`) and records the
outcome (`mark_push_result`).
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models import Notification, NotificationCategory, User

log = logging.getLogger(__name__)

# Expo push tokens look like ExponentPushToken[xxxxxxxx] (legacy: ExpoPushToken[...])
_EXPO_TOKEN_RE = re.compile(r"^Expo(nent)?PushToken\[[A-Za-z0-9_\-]+\]$")
_PERMANENT_PUSH_ERRORS = {"DeviceNotRegistered", "InvalidCredentials", "MismatchSenderId"}


# --- in-app notifications ------------------------------------------------------------------------


async def notify(
    db: AsyncSession,
    user_id: uuid.UUID,
    type: str,  # noqa: A002 - matches the column name; kept for call-site readability
    title: str,
    body: str = "",
    data: dict[str, Any] | None = None,
    *,
    category: NotificationCategory | str = NotificationCategory.system,
) -> Notification:
    """Create an unread notification for `user_id` (flush only, never commit)."""
    n = Notification(
        user_id=user_id,
        category=NotificationCategory(category),
        type=type[:40],
        title=title[:120],
        body=body or "",
        data=_json_safe(dict(data or {})),
        created_at=datetime.now(UTC),
    )
    db.add(n)
    await db.flush()
    return n


async def notify_many(
    db: AsyncSession,
    user_ids: list[uuid.UUID],
    type: str,  # noqa: A002
    title: str,
    body: str = "",
    data: dict[str, Any] | None = None,
    *,
    category: NotificationCategory | str = NotificationCategory.system,
) -> list[Notification]:
    """Same notification for several users (e.g. followers of a trader)."""
    out = []
    for uid in dict.fromkeys(user_ids):  # dedupe, keep order
        out.append(await notify(db, uid, type, title, body, data, category=category))
    return out


async def list_notifications(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    category: NotificationCategory | None = None,
    unread_only: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Notification], int]:
    """Newest first. Returns (items, total matching)."""
    where = [Notification.user_id == user_id]
    if category is not None:
        where.append(Notification.category == category)
    if unread_only:
        where.append(Notification.read_at.is_(None))
    total = (await db.execute(select(func.count()).select_from(Notification).where(*where))).scalar_one()
    rows = (
        await db.execute(
            select(Notification)
            .where(*where)
            .order_by(Notification.created_at.desc(), Notification.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars()
    return list(rows), int(total)


async def mark_read(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    ids: list[uuid.UUID] | None = None,
    mark_all: bool = False,
    category: NotificationCategory | None = None,
) -> int:
    """Set read_at on the caller's unread notifications; returns how many rows changed."""
    if not mark_all and not ids:
        return 0
    stmt = (
        update(Notification)
        .where(Notification.user_id == user_id, Notification.read_at.is_(None))
        .values(read_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )
    if not mark_all:
        stmt = stmt.where(Notification.id.in_(list(ids or [])))
    elif category is not None:
        stmt = stmt.where(Notification.category == category)
    result = await db.execute(stmt)
    return int(result.rowcount or 0)


async def unread_count(db: AsyncSession, user_id: uuid.UUID) -> tuple[int, dict[str, int]]:
    """(total unread, unread per category) for the tab badges."""
    q = (
        select(Notification.category, func.count())
        .where(Notification.user_id == user_id, Notification.read_at.is_(None))
        .group_by(Notification.category)
    )
    rows = (await db.execute(q)).all()
    by_category = {str(getattr(c, "value", c)): int(n) for c, n in rows}
    return sum(by_category.values()), by_category


# --- Expo push -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PushResult:
    ok: bool
    ticket_id: str | None = None
    error: str | None = None
    permanent: bool = False  # True when retrying with the same token is pointless (e.g. DeviceNotRegistered)

    def __bool__(self) -> bool:
        return self.ok


def is_expo_push_token(token: str | None) -> bool:
    return bool(token) and _EXPO_TOKEN_RE.fullmatch(token or "") is not None


async def pending_push_notifications(db: AsyncSession, limit: int = 100) -> list[tuple[Notification, str]]:
    """Notifications that have never been pushed, for active users with a push token (oldest first)."""
    q = (
        select(Notification, User.expo_push_token)
        .join(User, User.id == Notification.user_id)
        .where(
            User.expo_push_token.isnot(None),
            User.is_active.is_(True),
            ~Notification.data.has_key("push_sent"),
        )
        .order_by(Notification.created_at.asc())
        .limit(limit)
    )
    rows = (await db.execute(q)).all()
    return [(n, token) for n, token in rows if token]


def mark_push_result(notification: Notification, *, sent: bool, error: str | None = None) -> None:
    """Record the push outcome in Notification.data (reassigns the dict so the JSONB change is detected).

    Call with sent=True after a successful send, or sent=False for a *permanent* failure; leave the
    row untouched on transient errors so the worker retries it on the next tick.
    """
    data = dict(notification.data or {})
    data["push_sent"] = sent
    data["push_at"] = datetime.now(UTC).isoformat()
    if error:
        data["push_error"] = error[:200]
    else:
        data.pop("push_error", None)
    notification.data = data


async def send_expo_push(
    settings: Settings,
    token: str,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
    *,
    client: httpx.AsyncClient | None = None,
) -> PushResult:
    """POST one message to the Expo push API. Never raises; inspect the returned PushResult.

    The caller decides whether pushes are enabled (`settings.expo_push_enabled`). `client` lets tests
    inject an httpx client with a MockTransport.
    """
    if not is_expo_push_token(token):
        return PushResult(ok=False, error="invalid_expo_token", permanent=True)

    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if settings.expo_access_token:
        headers["Authorization"] = f"Bearer {settings.expo_access_token}"
    payload: dict[str, Any] = {
        "to": token,
        "title": title[:120],
        "body": body[:1000],
        "data": _json_safe(data or {}),
        "sound": "default",
        "priority": "high",
    }
    try:
        if client is not None:
            resp = await client.post(settings.expo_push_url, json=payload, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as own:
                resp = await own.post(settings.expo_push_url, json=payload, headers=headers)
    except httpx.HTTPError as e:
        log.warning("expo push transport error: %s", e.__class__.__name__)
        return PushResult(ok=False, error=f"transport:{e.__class__.__name__}")

    if resp.status_code >= 500:
        log.warning("expo push server error status=%s", resp.status_code)
        return PushResult(ok=False, error=f"http_{resp.status_code}")
    try:
        parsed = resp.json()
    except ValueError:
        log.warning("expo push: non-JSON response status=%s", resp.status_code)
        return PushResult(ok=False, error=f"http_{resp.status_code}_non_json", permanent=resp.status_code < 500)

    # Request-level errors: {"errors": [{"code": "...", "message": "..."}]}
    if isinstance(parsed, dict) and parsed.get("errors"):
        first = parsed["errors"][0] if isinstance(parsed["errors"], list) and parsed["errors"] else {}
        code = str(first.get("code", "request_error")) if isinstance(first, dict) else "request_error"
        log.warning("expo push request error: %s", code)
        return PushResult(ok=False, error=code, permanent=resp.status_code in (400, 401, 403))

    ticket: dict[str, Any] = {}
    tickets = parsed.get("data") if isinstance(parsed, dict) else None
    if isinstance(tickets, list) and tickets:
        ticket = tickets[0] if isinstance(tickets[0], dict) else {}
    elif isinstance(tickets, dict):
        ticket = tickets

    if ticket.get("status") == "ok":
        return PushResult(ok=True, ticket_id=str(ticket.get("id")) if ticket.get("id") else None)

    details = ticket.get("details") if isinstance(ticket.get("details"), dict) else {}
    err_code = str(details.get("error") or ticket.get("message") or f"http_{resp.status_code}")
    log.warning("expo push ticket error: %s", err_code)
    return PushResult(ok=False, error=err_code, permanent=err_code in _PERMANENT_PUSH_ERRORS)


async def deliver_pending_pushes(
    db: AsyncSession, settings: Settings, *, limit: int = 100, client: httpx.AsyncClient | None = None
) -> dict[str, int]:
    """Worker `push` job body: send every not-yet-pushed notification. Flush only (caller commits).

    When `settings.expo_push_enabled` is false the rows are marked as skipped (push_sent=false,
    push_error=disabled) so they are not retried forever. Returns counters.
    """
    counters = {"sent": 0, "failed": 0, "retry": 0, "disabled": 0}
    rows = await pending_push_notifications(db, limit=limit)
    for n, token in rows:
        if not settings.expo_push_enabled:
            mark_push_result(n, sent=False, error="disabled")
            counters["disabled"] += 1
            continue
        result = await send_expo_push(
            settings,
            token,
            n.title,
            n.body,
            {"notification_id": str(n.id), "category": n.category.value, "type": n.type, **(n.data or {})},
            client=client,
        )
        if result.ok:
            mark_push_result(n, sent=True)
            counters["sent"] += 1
        elif result.permanent:
            mark_push_result(n, sent=False, error=result.error)
            counters["failed"] += 1
            if result.error == "DeviceNotRegistered":
                user = await db.get(User, n.user_id)
                if user is not None and user.expo_push_token == token:
                    user.expo_push_token = None
        else:
            counters["retry"] += 1
    if rows:
        await db.flush()
    return counters


def _json_safe(value: Any) -> Any:
    """Expo / JSONB require JSON-serialisable data; stringify UUIDs/Decimals/datetimes defensively."""
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)
