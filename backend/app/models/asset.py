"""assets — allow-listed ERC-20 tokens per chain (02-api §5). Native MON is never an asset row (K6)."""
from __future__ import annotations

from sqlalchemy import Boolean, Enum, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import MarketCategory


class Asset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """`address` is the lower-case ERC-20 address used on-chain (vault allow-list, router paths)."""

    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("chain_id", "address", name="uq_assets_chain_id_address"),
        Index("ix_assets_chain_id_symbol", "chain_id", "symbol"),
    )

    chain_id: Mapped[int] = mapped_column(Integer, nullable=False)
    address: Mapped[str] = mapped_column(String(42), nullable=False)  # 0x… lower-case
    symbol: Mapped[str] = mapped_column(String(12), nullable=False)
    decimals: Mapped[int] = mapped_column(Integer, nullable=False)  # from the token's decimals(); no default
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    icon_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    category: Mapped[MarketCategory] = mapped_column(
        Enum(MarketCategory, native_enum=False, length=32), nullable=False, default=MarketCategory.crypto
    )
    is_base_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    # mirror of the vault contract allow-list (`tokenInfo(token).allowed`), refreshed by admin sync
    onchain_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    @property
    def code(self) -> str:
        """Backwards-compatible alias of `symbol` (02-api §5.1: `code` is deprecated, same value)."""
        return self.symbol

    @property
    def is_native(self) -> bool:
        """Always False: the native coin (MON) is not an asset row and never enters the vault (K6)."""
        return False

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Asset {self.chain_id} {self.symbol} {self.address[:8]}…>"
