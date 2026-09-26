"""All domain enums (stored as VARCHAR via sa.Enum(native_enum=False)).

Chain-facing values follow docs/monad/01-kontrat-spec.md; API values docs/monad/02-api-sozlesme.md.
"""
from __future__ import annotations

from enum import IntEnum, StrEnum


class UserRole(StrEnum):
    customer = "customer"  # Müşteri: brings capital
    trader = "trader"


class RiskProfile(StrEnum):
    """Customer / listing risk profile (Figma 1f "risk profili")."""

    conservative = "conservative"
    balanced = "balanced"
    aggressive = "aggressive"


class RiskLevel(StrEnum):
    """Trader risk level (Figma 1g "risk seviyesi")."""

    low = "low"
    medium = "medium"
    high = "high"


class MarketCategory(StrEnum):
    """"Piyasalar" chips map to token categories."""

    crypto = "crypto"
    stable_fx = "stable_fx"
    defi = "defi"


class ListingKind(StrEnum):
    capital = "capital"  # by customer: sermaye, süre, piyasa, maks. kayıp, risk profili
    service = "service"  # by trader: strateji, komisyon %, min sermaye, piyasalar, risk seviyesi


class ListingStatus(StrEnum):
    #: Capital listing whose reservation deposit is not confirmed yet; not public.
    draft = "draft"
    active = "active"
    paused = "paused"
    closed = "closed"


class InteractionTargetType(StrEnum):
    listing = "listing"
    user = "user"


class InteractionAction(StrEnum):
    pass_ = "pass"
    like = "like"
    save = "save"
    follow = "follow"
    view = "view"
    offer_request = "offer_request"


class OfferDirection(StrEnum):
    trader_to_customer = "trader_to_customer"  # "Teklif Ver" on a capital listing
    customer_to_trader = "customer_to_trader"  # "Teklif İste" on a trader / service listing


class OfferStatus(StrEnum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"
    withdrawn = "withdrawn"
    expired = "expired"


class AgreementStatus(StrEnum):
    """Off-chain mirror of the contract `Status` plus the pre/post-chain states."""

    draft = "draft"  # offer accepted, nothing on-chain yet
    proposed = "proposed"  # contract Status.Proposed (trader created, unfunded)
    funded = "funded"  # contract Status.Funded (customer created + escrowed)
    active = "active"  # contract Status.Active
    settled = "settled"  # contract Status.Settled
    cancelled = "cancelled"  # contract Status.Cancelled
    failed = "failed"  # on-chain creation failed / expired without being submitted

    @classmethod
    def from_onchain(cls, status: int) -> AgreementStatus | None:
        """Map the contract's `Status` enum (1..5) to the mirror status; `0` (None) / unknown -> None."""
        return _ONCHAIN_STATUS.get(int(status))


_ONCHAIN_STATUS = {
    1: AgreementStatus.proposed,
    2: AgreementStatus.funded,
    3: AgreementStatus.active,
    4: AgreementStatus.settled,
    5: AgreementStatus.cancelled,
}


class ReservationStatus(IntEnum):
    """Contract `Reservation.status` (01-spec §2.2)."""

    None_ = 0
    Open = 1
    Released = 2
    Consumed = 3


class PendingTxKind(StrEnum):
    """What the wallet is being asked to send (03-backend §4.1)."""

    open = "open"
    open_reserved = "open_reserved"
    propose = "propose"
    fund = "fund"
    fund_reserved = "fund_reserved"
    accept = "accept"
    cancel = "cancel"
    settle = "settle"
    claim = "claim"
    trade = "trade"
    reserve = "reserve"  # customer locks a listing's capital in the vault
    release = "release"  # customer takes locked capital back out
    transfer = "transfer"  # ERC-20 / native transfer from the wallet screen
    admin = "admin"  # setToken / setPaused / setFees / setRouter … built for the admin key


class PendingTxStatus(StrEnum):
    """pending (built, not yet sent) -> submitted (hash reported) -> confirmed | failed; expired when never sent."""

    pending = "pending"
    submitted = "submitted"
    confirmed = "confirmed"
    failed = "failed"
    expired = "expired"


class NotificationCategory(StrEnum):
    listing = "listing"
    offer = "offer"
    agreement = "agreement"
    wallet = "wallet"
    system = "system"
