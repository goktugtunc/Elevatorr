"""trades — indexed mirror of `Traded` events plus the trader's off-chain note."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin


class Trade(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "trades"
    __table_args__ = (
        # idempotent indexing: (tx hash, log index within the block) identifies a Traded event
        UniqueConstraint("tx_hash", "log_index", name="uq_trades_tx_hash_log_index"),
        Index("ix_trades_agreement_created", "agreement_id", "created_at"),
        Index("ix_trades_trader_created", "trader_id", "created_at"),
    )

    agreement_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agreements.id", ondelete="CASCADE"), nullable=False
    )
    log_index: Mapped[int | None] = mapped_column(Integer, nullable=True)  # receipt log index
    tx_hash: Mapped[str | None] = mapped_column(String(66), nullable=True, index=True)  # 0x… lower-case
    block_number: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    trader_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    token_in_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False
    )
    token_out_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False
    )
    amount_in: Mapped[Decimal] = mapped_column(Numeric(78, 18), nullable=False)
    amount_out: Mapped[Decimal] = mapped_column(Numeric(78, 18), nullable=False)
    value_after: Mapped[Decimal | None] = mapped_column(Numeric(78, 18), nullable=True)  # portfolio value in base
    note: Mapped[str | None] = mapped_column(Text, nullable=True)  # off-chain, editable by the trader
    symbol_label: Mapped[str | None] = mapped_column(String(60), nullable=True)  # "tWETH/tUSDC · Alış"
    notify_investors: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    token_in = relationship("Asset", lazy="joined", foreign_keys=[token_in_id])
    token_out = relationship("Asset", lazy="joined", foreign_keys=[token_out_id])
