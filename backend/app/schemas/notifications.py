"""Notification I/O models (Figma 7a-7c: tabs by category)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator
from pydantic_core import PydanticCustomError

from app.models.enums import NotificationCategory
from app.schemas.common import ORMModel


class NotificationOut(ORMModel):
    id: uuid.UUID
    category: NotificationCategory
    type: str
    title: str
    body: str
    data: dict[str, Any] = Field(default_factory=dict)
    read_at: datetime | None = None
    created_at: datetime


class MarkReadIn(BaseModel):
    ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)
    all: bool = False
    category: NotificationCategory | None = Field(default=None, description="with all=true: only this tab")

    @model_validator(mode="after")
    def _ids_or_all(self) -> MarkReadIn:
        if not self.all and not self.ids:
            raise PydanticCustomError("ids_or_all", "provide a non-empty 'ids' list or set 'all' to true")
        return self


class MarkReadOut(BaseModel):
    updated: int


class UnreadCountOut(BaseModel):
    unread: int
    by_category: dict[str, int] = Field(default_factory=dict)


class PushTokenIn(BaseModel):
    expo_push_token: str | None = Field(default=None, max_length=200, description="null unregisters the device")
