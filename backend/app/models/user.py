"""users — one row per EVM wallet; role is customer or trader."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Enum, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import RiskLevel, RiskProfile, UserRole


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    # identity ---------------------------------------------------------------
    #: 0x… lower-case (K11); API output is EIP-55 via schemas.common.AddressOut.
    wallet_address: Mapped[str] = mapped_column(String(42), unique=True, nullable=False, index=True)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole, native_enum=False, length=32), nullable=False)
    username: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)  # lowercase
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)

    # customer fields (Figma 1f) -----------------------------------------------
    budget_amount: Mapped[Decimal | None] = mapped_column(Numeric(78, 18), nullable=True)
    risk_profile: Mapped[RiskProfile | None] = mapped_column(
        Enum(RiskProfile, native_enum=False, length=32), nullable=True
    )
    # shared: list of MarketCategory values, e.g. ["crypto", "stable_fx"]
    markets: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")

    # trader fields (Figma 1g) --------------------------------------------------
    #: Trader'ın geçmişini kendi cümleleriyle anlattığı yer: hangi işlemleri
    #: yaptı, nasıl bir yol izledi. İstatistikler zincirden gelir; bu onların
    #: anlatısı — yatırımcı sayıların arkasındaki hikâyeyi de görsün.
    portfolio: Mapped[str | None] = mapped_column(Text, nullable=True)
    strategy_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    commission_bps: Mapped[int | None] = mapped_column(Integer, nullable=True)  # komisyon, 0..=5000
    min_capital: Mapped[Decimal | None] = mapped_column(Numeric(78, 18), nullable=True)
    risk_level: Mapped[RiskLevel | None] = mapped_column(Enum(RiskLevel, native_enum=False, length=32), nullable=True)

    # account ----------------------------------------------------------------
    expo_push_token: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # stats maintained by the indexer / reconciler (bps = basis points) -------------
    total_return_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    monthly_return_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_drawdown_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    win_rate_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    managed_capital: Mapped[Decimal] = mapped_column(
        Numeric(78, 18), nullable=False, default=Decimal("0"), server_default="0"
    )
    active_agreements: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    rating_avg: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False, default=Decimal("0"), server_default="0")
    rating_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    @property
    def is_customer(self) -> bool:
        return self.role == UserRole.customer

    @property
    def is_trader(self) -> bool:
        return self.role == UserRole.trader

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.username} {self.role} {self.wallet_address[:8]}…>"
