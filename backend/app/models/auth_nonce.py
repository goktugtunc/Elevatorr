"""auth_nonces — single-use SIWE nonce; stores the exact message text the wallet must sign (03 §3.4)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class AuthNonce(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "auth_nonces"

    address: Mapped[str] = mapped_column(String(42), nullable=False, index=True)  # lower-case 0x…
    nonce: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)  # 32 hex chars
    message: Mapped[str] = mapped_column(Text, nullable=False)  # signed text, byte-for-byte
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
