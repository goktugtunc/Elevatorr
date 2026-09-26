"""Unsigned-transaction hand-off and submit I/O (02-api-sozlesme §2, §3.1).

Flow: `POST …/tx/…` -> `UnsignedTxOut` (calldata the wallet sends with `eth_sendTransaction`, plus ERC-20
`approve` pre-steps when the vault has to pull tokens) -> the wallet broadcasts -> `POST /tx/submit
{pending_tx_id, tx_hash}` -> `TxStatusOut` (also served by `GET /tx/{pending_id}`).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.enums import AgreementStatus, PendingTxKind, PendingTxStatus
from app.schemas.common import Amount, TxHash

TxAction = Literal["propose", "open", "open_reserved", "fund", "fund_reserved", "accept", "cancel", "settle", "claim"]


class TxActionIn(BaseModel):
    """Optional body of `POST /agreements/{id}/tx/{action}`."""

    slippage_bps: int | None = Field(
        default=None,
        ge=0,
        le=5_000,
        description="settle only: each min_out = router quote × (1 − bps); default settings.settle_slippage_bps",
    )
    asset_id: uuid.UUID | None = Field(default=None, description="claim only: the token to withdraw")


class PreStepOut(BaseModel):
    """A transaction the wallet must send (and wait for) before the main one — ERC-20 `approve` (02-api §2.2)."""

    kind: Literal["approve"] = "approve"
    to: str = Field(description="token address (checksum)")
    data: str = Field(description="approve(spender, amount_raw) calldata")
    value: str = "0"
    gas: str | None = None
    description: str
    spender: str = Field(description="vault address (checksum)")
    asset_id: uuid.UUID | None = None
    symbol: str | None = None
    amount: Amount | None = None
    amount_raw: str


class UnsignedTxOut(BaseModel):
    """What the wallet sends. `summary` explains the transaction ("what am I signing?")."""

    pending_tx_id: uuid.UUID | None = Field(description="pending_transactions.id; null on admin endpoints")
    kind: PendingTxKind
    action: str = Field(description="ABI function name (camelCase); `transfer` for native transfers")
    agreement_id: uuid.UUID | None = None
    listing_id: uuid.UUID | None = None
    chain_id: int
    from_address: str = Field(description="account that must send it (checksum)")
    to: str = Field(description="vault / token / recipient (checksum)")
    data: str = Field(description="0x calldata; `0x` for native transfers")
    value: str = Field(default="0", description="wei, decimal string")
    gas: str | None = Field(default=None, description="suggested gas limit (estimate × 1.2), decimal string")
    description: str = Field(default="", description="one Turkish sentence: what am I signing?")
    pre_steps: list[PreStepOut] = Field(default_factory=list, description="send these first, in order")
    summary: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime | None = None


class TxSubmitIn(BaseModel):
    pending_tx_id: uuid.UUID
    tx_hash: TxHash


class TxStatusOut(BaseModel):
    """`POST /tx/submit` and `GET /tx/{pending_id}` (02-api §2.3)."""

    pending_tx_id: uuid.UUID
    kind: PendingTxKind
    action: str
    status: PendingTxStatus
    tx_hash: str | None = None
    block_number: int | None = None
    confirmations: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    contract_error_code: int | None = None
    explorer_url: str | None = None
    agreement_id: uuid.UUID | None = None
    listing_id: uuid.UUID | None = None
    agreement_status: AgreementStatus | None = None
    onchain_id: int | None = None
    trade_id: uuid.UUID | None = None
    events: list[dict[str, Any]] = Field(default_factory=list, description="decoded vault events of the receipt")
    submitted_at: datetime | None = None
    updated_at: datetime | None = None
    expires_at: datetime | None = None


__all__ = ["PreStepOut", "TxAction", "TxActionIn", "TxStatusOut", "TxSubmitIn", "UnsignedTxOut"]
