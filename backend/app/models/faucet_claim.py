"""faucet_claims — one row per TestToken mint handed out by `POST /wallet/faucet` (K7); enforces the daily
per-user-per-asset limit (`settings.faucet_daily_limit`)."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin


class FaucetClaim(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "faucet_claims"
    __table_args__ = (Index("ix_faucet_claims_user_asset_created", "user_id", "asset_id", "created_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False
    )
    tx_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)  # mint tx (lower-case)
    amount: Mapped[Decimal] = mapped_column(Numeric(78, 18), nullable=False)  # token units
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    asset = relationship("Asset", lazy="joined", foreign_keys=[asset_id])
