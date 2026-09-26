"""Import every model so Base.metadata is complete (Alembic autogenerate + relationship resolution)."""
from app.db.base import Base
from app.models.agreement import Agreement, AgreementBalance, AgreementValueSnapshot
from app.models.asset import Asset
from app.models.auth_nonce import AuthNonce
from app.models.enums import (
    AgreementStatus,
    InteractionAction,
    InteractionTargetType,
    ListingKind,
    ListingStatus,
    MarketCategory,
    NotificationCategory,
    OfferDirection,
    OfferStatus,
    PendingTxKind,
    PendingTxStatus,
    ReservationStatus,
    RiskLevel,
    RiskProfile,
    UserRole,
)
from app.models.failed_event import FailedEvent
from app.models.faucet_claim import FaucetClaim
from app.models.indexer_state import IndexerState
from app.models.interaction import Favorite, Follow, Interaction
from app.models.listing import Listing
from app.models.messaging import Conversation, Message
from app.models.notification import Notification
from app.models.offer import Offer
from app.models.pending_transaction import PendingTransaction
from app.models.rating import Rating
from app.models.trade import Trade
from app.models.user import User

__all__ = [
    "Base",
    # tables
    "Agreement",
    "AgreementBalance",
    "AgreementValueSnapshot",
    "Asset",
    "AuthNonce",
    "Conversation",
    "FailedEvent",
    "FaucetClaim",
    "Favorite",
    "Follow",
    "IndexerState",
    "Interaction",
    "Listing",
    "Message",
    "Notification",
    "Offer",
    "PendingTransaction",
    "Rating",
    "Trade",
    "User",
    # enums
    "AgreementStatus",
    "InteractionAction",
    "InteractionTargetType",
    "ListingKind",
    "ListingStatus",
    "MarketCategory",
    "NotificationCategory",
    "OfferDirection",
    "OfferStatus",
    "PendingTxKind",
    "PendingTxStatus",
    "ReservationStatus",
    "RiskLevel",
    "RiskProfile",
    "UserRole",
]
