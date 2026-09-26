"""pending_transactions — every unsigned EVM call handed to the wallet (03-backend §4.1, 02-api §2)."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.models.enums import PendingTxKind, PendingTxStatus


class PendingTransaction(UUIDPrimaryKeyMixin, Base):
    """Lifecycle: pending ({to, data, value, gas} returned) -> submitted (client reported the hash via
    POST /tx/submit) -> confirmed | failed (receipt / indexer); expired when never submitted.
    `payload` carries builder context (`context`) and the gateway `summary`; `result` the receipt summary
    (`{events:[…], gas_used, effective_gas_price, applied}`)."""

    __tablename__ = "pending_transactions"
    __table_args__ = (
        Index("ix_pending_transactions_user_created", "user_id", "created_at"),
        Index(
            "uq_pending_transactions_tx_hash",
            "tx_hash",
            unique=True,
            postgresql_where=text("tx_hash IS NOT NULL"),
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[PendingTxKind] = mapped_column(Enum(PendingTxKind, native_enum=False, length=32), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)  # ABI fn: open, openReserved, transfer, setToken…
    agreement_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agreements.id", ondelete="SET NULL"), nullable=True, index=True
    )
    listing_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("listings.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # what the wallet must send ---------------------------------------------------------------
    from_address: Mapped[str] = mapped_column(String(42), nullable=False)  # expected sender (lower-case)
    to_address: Mapped[str] = mapped_column(String(42), nullable=False)  # vault / token / recipient
    calldata: Mapped[str] = mapped_column(Text, nullable=False)  # 0x… lower-case; "0x" for native transfers
    value: Mapped[Decimal] = mapped_column(Numeric(78, 0), nullable=False, default=Decimal(0), server_default="0")  # wei
    gas: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # suggested gas limit
    chain_id: Mapped[int] = mapped_column(Integer, nullable=False)

    # outcome -----------------------------------------------------------------------------------
    tx_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    status: Mapped[PendingTxStatus] = mapped_column(
        Enum(PendingTxStatus, native_enum=False, length=32),
        nullable=False,
        default=PendingTxStatus.pending,
        server_default="pending",
        index=True,
    )
    block_number: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    block_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 02-api §2.6
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    contract_error_code: Mapped[int | None] = mapped_column(Integer, nullable=True)  # VaultError number
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # also on failed
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    @property
    def is_terminal(self) -> bool:
        return self.status in (PendingTxStatus.confirmed, PendingTxStatus.failed, PendingTxStatus.expired)
