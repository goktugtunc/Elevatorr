"""agreements (Sözleşme mirror of the TraderVault contract), per-token balances and value snapshots."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import AgreementStatus, RiskProfile, UserRole


class Agreement(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Off-chain row created when an offer is accepted (status draft); the indexer fills the rest
    from contract events (`Proposed/Opened -> Activated -> Traded* -> Settled|Cancelled`).
    Amounts are Decimal in token units (`Numeric(78,18)`); on-chain uint256 raw units via
    app.services.chain.amounts (`to_raw/from_raw` with the asset's `decimals`).
    """

    __tablename__ = "agreements"
    __table_args__ = (
        Index("ix_agreements_customer_status", "customer_id", "status"),
        Index("ix_agreements_trader_status", "trader_id", "status"),
    )

    onchain_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)  # contract uint256 id
    offer_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("offers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    listing_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("listings.id", ondelete="SET NULL"), nullable=True, index=True
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    trader_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    base_asset_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False
    )

    # Terms (mirror of contract `Terms`) ---------------------------------------------------
    principal: Mapped[Decimal] = mapped_column(Numeric(78, 18), nullable=False)
    duration_secs: Mapped[int] = mapped_column(BigInteger, nullable=False)
    commission_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    max_drawdown_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_profile: Mapped[RiskProfile | None] = mapped_column(
        Enum(RiskProfile, native_enum=False, length=32), nullable=True
    )
    listing_ref: Mapped[str] = mapped_column(String(64), nullable=False)  # hex of sha256(offer id) = bytes32
    #: Fee snapshot taken by the contract at open/propose time (`Agreement.platformFeeBps`).
    platform_fee_bps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Vault (proxy) the agreement lives in; lets rows survive a redeploy.
    vault_address: Mapped[str | None] = mapped_column(String(42), nullable=True)

    # lifecycle ----------------------------------------------------------------------------
    status: Mapped[AgreementStatus] = mapped_column(
        Enum(AgreementStatus, native_enum=False, length=32),
        nullable=False,
        default=AgreementStatus.draft,
        server_default="draft",
        index=True,
    )
    proposer_role: Mapped[UserRole] = mapped_column(Enum(UserRole, native_enum=False, length=32), nullable=False)
    created_tx: Mapped[str | None] = mapped_column(String(66), nullable=True)  # open / propose
    activate_tx: Mapped[str | None] = mapped_column(String(66), nullable=True)  # fund / accept
    cancel_tx: Mapped[str | None] = mapped_column(String(66), nullable=True)
    settle_tx: Mapped[str | None] = mapped_column(String(66), nullable=True)
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # live valuation (reconciler: `valueInBase`) -------------------------------------------
    current_value: Mapped[Decimal | None] = mapped_column(Numeric(78, 18), nullable=True)
    value_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    high_water_value: Mapped[Decimal | None] = mapped_column(Numeric(78, 18), nullable=True)

    # settlement (mirror of `Settled` event / contract fields) -----------------------------------
    final_value: Mapped[Decimal | None] = mapped_column(Numeric(78, 18), nullable=True)
    profit: Mapped[Decimal | None] = mapped_column(Numeric(78, 18), nullable=True)
    trader_fee: Mapped[Decimal | None] = mapped_column(Numeric(78, 18), nullable=True)
    platform_fee: Mapped[Decimal | None] = mapped_column(Numeric(78, 18), nullable=True)
    customer_payout: Mapped[Decimal | None] = mapped_column(Numeric(78, 18), nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    settled_by: Mapped[str | None] = mapped_column(String(42), nullable=True)  # `by` address of Settled (lower)
    last_event_block_number: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    customer = relationship("User", lazy="joined", foreign_keys=[customer_id])
    trader = relationship("User", lazy="joined", foreign_keys=[trader_id])
    base_asset = relationship("Asset", lazy="joined", foreign_keys=[base_asset_id])
    balances = relationship(
        "AgreementBalance", lazy="selectin", cascade="all, delete-orphan", passive_deletes=True,
        order_by="AgreementBalance.asset_id",
    )

    @property
    def is_open(self) -> bool:
        return self.status in (AgreementStatus.proposed, AgreementStatus.funded, AgreementStatus.active)

    def party_role(self, user_id: uuid.UUID) -> UserRole | None:
        if user_id == self.customer_id:
            return UserRole.customer
        if user_id == self.trader_id:
            return UserRole.trader
        return None


class AgreementBalance(Base):
    """Mirror of contract `balanceOf(id, token)`; one row per token the agreement currently holds."""

    __tablename__ = "agreement_balances"

    agreement_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agreements.id", ondelete="CASCADE"), primary_key=True
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.id", ondelete="RESTRICT"), primary_key=True
    )
    balance: Mapped[Decimal] = mapped_column(Numeric(78, 18), nullable=False, default=Decimal("0"), server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    asset = relationship("Asset", lazy="joined", foreign_keys=[asset_id])


class AgreementValueSnapshot(UUIDPrimaryKeyMixin, Base):
    """Periodic `valueInBase` samples for performance charts and value-history."""

    __tablename__ = "agreement_value_snapshots"
    __table_args__ = (Index("ix_agreement_value_snapshots_agreement_at", "agreement_id", "at"),)

    agreement_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agreements.id", ondelete="CASCADE"), nullable=False
    )
    value: Mapped[Decimal] = mapped_column(Numeric(78, 18), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
