"""failed_events — vault events whose handler raised; retried with backoff so a poison event never stalls the
indexer cursor (03-backend §5.3)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class FailedEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "failed_events"
    __table_args__ = (
        UniqueConstraint("tx_hash", "log_index", name="uq_failed_events_tx_hash_log_index"),
        Index("ix_failed_events_next_retry_at", "next_retry_at"),
    )

    tx_hash: Mapped[str] = mapped_column(String(66), nullable=False)
    log_index: Mapped[int] = mapped_column(Integer, nullable=False)
    block_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_name: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Decoded event args as JSON (addresses lower-case hex, bytes 0x-hex, ints as strings/ints).
    args: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def is_resolved(self) -> bool:
        return self.resolved_at is not None
