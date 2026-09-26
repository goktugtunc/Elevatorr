"""Wallet (Figma 8c Cüzdan) I/O models — Monad edition (02-api-sozlesme §3.5, §7).

Balances come straight from the RPC (native MON + every active allow-listed ERC-20); transfers are unsigned
calldata (`UnsignedTxOut`, `app.schemas.tx`); the test-token faucet is server-minted (K7). No trustlines,
no anchor, no SEP-7: the pay URI is EIP-681.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import Address, AddressOut, Amount, AmountIn, TxHash


class NativeBalanceOut(BaseModel):
    symbol: str = "MON"
    decimals: int = 18
    balance: Amount
    balance_raw: str = Field(description="wei as a decimal string")


class TokenBalanceOut(BaseModel):
    asset_id: uuid.UUID
    address: AddressOut = Field(description="ERC-20 address (EIP-55)")
    symbol: str
    decimals: int
    balance: Amount
    balance_raw: str = Field(description="raw uint256 units as a decimal string")
    is_base_allowed: bool = False
    error: str | None = Field(default=None, description="set when this token's balanceOf failed (balance shown as 0)")


class WalletOut(BaseModel):
    address: AddressOut
    chain_id: int
    native: NativeBalanceOut
    tokens: list[TokenBalanceOut] = Field(default_factory=list, description="every active allow-listed asset")
    explorer_url: str
    updated_at: datetime


class TokenFaucetAssetOut(BaseModel):
    asset_id: uuid.UUID
    symbol: str
    amount: Amount = Field(description="fixed amount handed out per claim")
    daily_limit: int
    next_allowed_at: datetime | None = Field(default=None, description="null = the caller can claim now")


class TokenFaucetOut(BaseModel):
    enabled: bool = Field(description="false when the server has no minter key (POST /wallet/faucet -> 503)")
    assets: list[TokenFaucetAssetOut] = Field(default_factory=list)


class DepositInfoOut(BaseModel):
    address: AddressOut
    chain_id: int
    pay_uri: str = Field(description="EIP-681 `ethereum:<address>@<chain_id>` for QR codes")
    faucet_url: str | None = Field(default=None, description="native MON faucet (testnet)")
    token_faucet: TokenFaucetOut
    instructions: list[str] = Field(default_factory=list)


class WalletTransferIn(BaseModel):
    """`POST /wallet/tx/transfer`: `asset_id` null -> native MON, otherwise an ERC-20 `transfer`."""

    to: Address = Field(description="recipient 0x address (lower-case or valid EIP-55)")
    asset_id: uuid.UUID | None = None
    amount: AmountIn


class FaucetIn(BaseModel):
    asset_id: uuid.UUID


class FaucetOut(BaseModel):
    tx_hash: TxHash
    status: Literal["confirmed", "submitted"] = Field(
        description="confirmed = receipt seen within the wait window; submitted = still pending"
    )
    asset_id: uuid.UUID
    symbol: str
    amount: Amount
    amount_raw: str
    next_allowed_at: datetime | None = None
    explorer_url: str


class WalletTransferOut(BaseModel):
    """One ERC-20 `Transfer` log touching the wallet (02-api §7.4)."""

    tx_hash: str
    block_number: int
    log_index: int
    asset_id: uuid.UUID | None = Field(default=None, description="null = native MON")
    symbol: str
    direction: Literal["in", "out", "self"]
    counterparty: AddressOut
    counterparty_label: str | None = Field(default=None, description="vault | router | mint | burn when recognised")
    amount: Amount
    amount_raw: str
    at: datetime | None = None
    explorer_url: str


__all__ = [
    "DepositInfoOut",
    "FaucetIn",
    "FaucetOut",
    "NativeBalanceOut",
    "TokenBalanceOut",
    "TokenFaucetAssetOut",
    "TokenFaucetOut",
    "WalletOut",
    "WalletTransferIn",
    "WalletTransferOut",
]
