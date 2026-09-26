"""listings — capital listings (customer) and service listings (trader)."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ListingKind, ListingStatus, RiskProfile


class Listing(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "listings"
    __table_args__ = (Index("ix_listings_kind_status_created", "kind", "status", "created_at"),)

    owner_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[ListingKind] = mapped_column(Enum(ListingKind, native_enum=False, length=32), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    risk_profile: Mapped[RiskProfile | None] = mapped_column(
        Enum(RiskProfile, native_enum=False, length=32), nullable=True
    )
    markets: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    status: Mapped[ListingStatus] = mapped_column(
        Enum(ListingStatus, native_enum=False, length=32),
        nullable=False,
        default=ListingStatus.active,
        server_default="active",
    )

    # capital listing (Figma 5a) --------------------------------------------------
    amount: Mapped[Decimal | None] = mapped_column(Numeric(78, 18), nullable=True)  # sermaye (base asset units)
    base_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.id", ondelete="RESTRICT"), nullable=True
    )
    duration_days: Mapped[int | None] = mapped_column(Integer, nullable=True)  # süre
    max_loss_bps: Mapped[int | None] = mapped_column(Integer, nullable=True)  # maks. kayıp -> max_drawdown_bps
    #: On-chain `reserve` that backs this listing. The capital sits in the vault
    #: from publication until an agreement draws on it or it is released, so a
    #: capital listing is never an amount the owner does not actually hold.
    reservation_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    #: What the reservation still holds (base asset units); None before the deposit.
    reserved_amount: Mapped[Decimal | None] = mapped_column(Numeric(78, 18), nullable=True)
    reserve_tx: Mapped[str | None] = mapped_column(String(66), nullable=True)
    release_tx: Mapped[str | None] = mapped_column(String(66), nullable=True)

    # service listing (Figma 5b) --------------------------------------------------
    commission_bps: Mapped[int | None] = mapped_column(Integer, nullable=True)  # komisyon
    min_capital: Mapped[Decimal | None] = mapped_column(Numeric(78, 18), nullable=True)
    expected_return_min_bps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_return_max_bps: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # counters ---------------------------------------------------------------------
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    like_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    offer_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    owner = relationship("User", lazy="joined", foreign_keys=[owner_id])
    base_asset = relationship("Asset", lazy="joined", foreign_keys=[base_asset_id])

    @property
    def is_funded(self) -> bool:
        """Capital is locked in the vault and still covers the advertised amount."""
        return (
            self.reservation_id is not None
            and self.reserved_amount is not None
            and self.amount is not None
            and self.reserved_amount >= self.amount
        )

    @property
    def is_capital(self) -> bool:
        return self.kind == ListingKind.capital

    @property
    def is_service(self) -> bool:
        return self.kind == ListingKind.service
