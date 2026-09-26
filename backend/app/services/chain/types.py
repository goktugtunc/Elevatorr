"""Chain data classes (03-backend-tasarim §1.2).

Conventions: every address is a lower-case ``str``; amounts are ``int`` raw units; contract timestamps are ``int``
unix seconds, block times are aware ``datetime``. All classes are ``frozen`` dataclasses.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from app.core.errors import ValidationError
from app.services.chain.addresses import ZERO_ADDRESS, checksum, same

# 01-kontrat-spec §3 constants (mirrored for Terms.validate)
MAX_TOKENS = 6
MIN_DURATION = 86_400
MAX_DURATION = 94_608_000
MAX_COMMISSION_BPS = 5_000
MIN_DRAWDOWN_BPS = 100
BPS_DENOM = 10_000

# Agreement.status
STATUS_NONE, STATUS_PROPOSED, STATUS_FUNDED, STATUS_ACTIVE, STATUS_SETTLED, STATUS_CANCELLED = range(6)
# Reservation.status
RES_NONE, RES_OPEN, RES_RELEASED, RES_CONSUMED = range(4)


def _hex(b: bytes | str) -> str:
    if isinstance(b, str):
        return b if b.startswith("0x") else "0x" + b
    return "0x" + bytes(b).hex()


@dataclass(frozen=True)
class Terms:
    """``ITraderVault.Terms`` (01-spec §2.2)."""

    customer: str
    trader: str
    base_token: str
    principal: int
    duration_seconds: int
    commission_bps: int
    max_drawdown_bps: int
    listing_ref: bytes  # 32 bytes

    def validate(self) -> None:
        """01-spec §4.0 ``_validateTerms`` order; raises ``ValidationError(code="invalid_terms")``.

        Step 7 (base token allow-list) needs chain state and is checked by the gateway, not here.
        """

        def fail(reason: str) -> None:
            raise ValidationError(f"invalid terms: {reason}", code="invalid_terms", details={"reason": reason})

        if same(self.customer, ZERO_ADDRESS) or same(self.trader, ZERO_ADDRESS):
            fail("zero_address")
        if self.principal <= 0:
            fail("principal")
        if self.duration_seconds < MIN_DURATION or self.duration_seconds > MAX_DURATION:
            fail("duration")
        if self.commission_bps < 0 or self.commission_bps > MAX_COMMISSION_BPS:
            fail("commission_bps")
        if self.max_drawdown_bps < MIN_DRAWDOWN_BPS or self.max_drawdown_bps > BPS_DENOM:
            fail("max_drawdown_bps")
        if same(self.customer, self.trader):
            fail("same_party")
        if len(self.listing_ref) != 32:
            fail("listing_ref")

    def as_abi_tuple(self) -> tuple:
        return (
            checksum(self.customer),
            checksum(self.trader),
            checksum(self.base_token),
            int(self.principal),
            int(self.duration_seconds),
            int(self.commission_bps),
            int(self.max_drawdown_bps),
            bytes(self.listing_ref),
        )

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["listing_ref"] = _hex(self.listing_ref)
        return d


@dataclass(frozen=True)
class AgreementView:
    """``getAgreement()`` output; field order follows the struct in 01-spec §2.2."""

    id: int
    terms: Terms
    status: int  # 0 None, 1 Proposed, 2 Funded, 3 Active, 4 Settled, 5 Cancelled
    proposer: str
    created_at: int
    start_time: int
    end_time: int
    settled_at: int
    platform_fee_bps: int
    tokens: list[str]
    final_value: int
    trader_fee: int
    platform_fee: int
    customer_payout: int
    last_value: int


@dataclass(frozen=True)
class ReservationView:
    id: int
    customer: str
    token: str
    amount: int
    original: int
    status: int  # 0 None, 1 Open, 2 Released, 3 Consumed
    created_at: int
    listing_ref: bytes


@dataclass(frozen=True)
class ConfigView:
    owner: str
    router: str
    fee_recipient: str
    platform_fee_bps: int
    settle_slippage_bps: int
    paused: bool
    pending_router: str | None
    router_activation_time: int
    router_delay: int


@dataclass(frozen=True)
class TokenInfoView:
    allowed: bool = False
    is_base: bool = False


@dataclass(frozen=True)
class TokenBalance:
    token: str
    raw: int
    symbol: str | None = None
    decimals: int | None = None


@dataclass(frozen=True)
class RouterQuote:
    path: list[str]
    amount_in: int
    amounts: list[int]
    source: str  # "router" | "fake"

    @property
    def amount_out(self) -> int:
        return self.amounts[-1] if self.amounts else 0


@dataclass(frozen=True)
class BlockInfo:
    number: int
    hash: str
    parent_hash: str
    timestamp: datetime


@dataclass(frozen=True)
class PreStep:
    """02-api §2.2 — a transaction the wallet must send before the main one (ERC-20 ``approve``)."""

    kind: str  # "approve"
    to: str
    data: str
    value: int
    gas: int | None
    description: str
    spender: str
    token: str
    amount_raw: int


@dataclass(frozen=True)
class UnsignedTx:
    """Chain half of 02-api §2.1 ``UnsignedTxOut``."""

    kind: str  # PendingTxKind value
    action: str  # ABI function name (camelCase) / "transfer"
    from_address: str
    to: str
    data: str
    value: int
    gas: int | None
    chain_id: int
    summary: dict[str, Any]  # JSON-safe, raw units
    expires_at: datetime  # now + settings.pending_tx_ttl_seconds
    pre_steps: list[PreStep] = field(default_factory=list)


@dataclass(frozen=True)
class EventRecord:
    """One decoded vault event from ``eth_getLogs`` or ``receipt.logs``."""

    block_number: int
    block_hash: str
    log_index: int
    tx_hash: str
    tx_index: int
    address: str  # emitting contract (vault), lower-case
    name: str  # Solidity event name: "Opened", "Traded", ...
    args: dict[str, Any]  # ABI names (camelCase) -> int / lower-case address str / bytes / bool
    topic0: str
    block_timestamp: datetime | None = None
    removed: bool = False


@dataclass(frozen=True)
class RevertInfo:
    selector: str | None
    name: str | None
    code: int | None  # VaultError code (1–30) or None
    args: dict[str, Any]
    message: str  # human readable; error_code derivation in 03 §4.4


@dataclass(frozen=True)
class TxInfo:
    """``eth_getTransactionByHash`` (may exist before a receipt does)."""

    tx_hash: str
    from_address: str
    to_address: str | None
    input: str
    value: int
    nonce: int
    gas: int
    block_number: int | None
    block_hash: str | None


@dataclass(frozen=True)
class TxReceiptResult:
    tx_hash: str
    status: int  # 1 success, 0 revert
    block_number: int
    block_hash: str
    block_timestamp: datetime | None
    from_address: str
    to_address: str | None
    input: str
    value: int
    gas_used: int
    gas_limit: int
    effective_gas_price: int
    events: list[EventRecord]  # only logs decodable from the vault address
    raw_log_count: int
    confirmations: int  # latest_block - block_number + 1 at read time
    revert: RevertInfo | None = None

    @property
    def ok(self) -> bool:
        return self.status == 1


@dataclass(frozen=True)
class SettlePreview:
    """``previewSettle()`` (optional view; None when absent from the ABI)."""

    tokens: list[str]
    balances: list[int]
    quotes: list[int]
    estimated_final_value: int
    drawdown_floor: int
    keeper_floor: int


__all__ = [
    "BPS_DENOM",
    "MAX_COMMISSION_BPS",
    "MAX_DURATION",
    "MAX_TOKENS",
    "MIN_DRAWDOWN_BPS",
    "MIN_DURATION",
    "RES_CONSUMED",
    "RES_NONE",
    "RES_OPEN",
    "RES_RELEASED",
    "STATUS_ACTIVE",
    "STATUS_CANCELLED",
    "STATUS_FUNDED",
    "STATUS_NONE",
    "STATUS_PROPOSED",
    "STATUS_SETTLED",
    "AgreementView",
    "BlockInfo",
    "ConfigView",
    "EventRecord",
    "PreStep",
    "ReservationView",
    "RevertInfo",
    "RouterQuote",
    "SettlePreview",
    "Terms",
    "TokenBalance",
    "TokenInfoView",
    "TxInfo",
    "TxReceiptResult",
    "UnsignedTx",
]
