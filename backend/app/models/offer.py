"""offers — two-way proposals; accepting one creates an Agreement ."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import OfferDirection, OfferStatus


class Offer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "offers"
    __table_args__ = (
        Index("ix_offers_to_user_status", "to_user_id", "status"),
        Index("ix_offers_from_user_status", "from_user_id", "status"),
    )

    listing_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("listings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    to_user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    direction: Mapped[OfferDirection] = mapped_column(
        Enum(OfferDirection, native_enum=False, length=32), nullable=False
    )

    # negotiated terms (these become the on-chain Terms when accepted) ---------------------
    amount: Mapped[Decimal] = mapped_column(Numeric(78, 18), nullable=False)  # principal in base asset units
    base_asset_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False
    )
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    commission_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    max_drawdown_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=10_000, server_default="10000")
    expected_return_min_bps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_return_max_bps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[OfferStatus] = mapped_column(
        Enum(OfferStatus, native_enum=False, length=32),
        nullable=False,
        default=OfferStatus.pending,
        server_default="pending",
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # offers.agreement_id <-> agreements.offer_id is a cycle; this side is created with ALTER TABLE
    agreement_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agreements.id", ondelete="SET NULL", use_alter=True, name="fk_offers_agreement_id_agreements"),
        nullable=True,
    )

    listing = relationship("Listing", lazy="joined", foreign_keys=[listing_id])
    from_user = relationship("User", lazy="joined", foreign_keys=[from_user_id])
    to_user = relationship("User", lazy="joined", foreign_keys=[to_user_id])
    base_asset = relationship("Asset", lazy="joined", foreign_keys=[base_asset_id])

    @property
    def duration_secs(self) -> int:
        return int(self.duration_days) * 86_400
