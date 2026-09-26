"""``ChainGateway`` Protocol (03-backend-tasarim §1.6) — the only surface services and routers see.

Implementations: ``app.services.chain.monad.MonadGateway`` (web3.py v7, Monad RPC) and
``app.services.chain.fake.FakeChainGateway`` (in-memory vault state machine for tests).

Conventions: every address parameter/attribute is a lower-case ``0x`` string; every amount is an ``int`` in
raw token units (or wei); every ``build_*`` returns a wallet-ready ``UnsignedTx`` (calldata + gas estimate ×1.2,
plus ERC-20 ``approve`` pre-steps when the vault has to pull tokens) and raises ``ContractRevertError`` when the
dry run (``estimateGas``) reverts.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from app.services.chain.types import (
    AgreementView,
    BlockInfo,
    ConfigView,
    EventRecord,
    PreStep,
    ReservationView,
    RevertInfo,
    RouterQuote,
    SettlePreview,
    Terms,
    TokenInfoView,
    TxInfo,
    TxReceiptResult,
    UnsignedTx,
)


@runtime_checkable
class ChainGateway(Protocol):
    kind: str  # "monad" | "fake" (QuoteOut.source, logs)
    chain_id_expected: int  # settings.chain_id
    vault_address: str  # lower-case
    router_address: str  # lower-case

    # --- network -------------------------------------------------------------------------------
    async def chain_id(self) -> int: ...

    async def latest_block(self) -> int: ...

    async def get_block(self, number: int | str) -> BlockInfo: ...  # accepts "latest"

    async def close(self) -> None: ...

    # --- vault reads (eth_call; NotFound -> ContractRevertError(VaultError.NotFound)) ----------
    async def read_agreement(self, agreement_id: int) -> AgreementView: ...

    async def read_balances(self, agreement_id: int) -> list[tuple[str, int]]: ...  # getBalances, tokens order

    async def read_value_in_base(self, agreement_id: int) -> int: ...

    async def read_config(self) -> ConfigView: ...  # getConfig() + owner()

    async def read_reservation(self, reservation_id: int) -> ReservationView: ...

    async def preview_settle(self, agreement_id: int) -> SettlePreview | None: ...  # None when absent from ABI

    async def is_token_allowed(self, token: str) -> TokenInfoView: ...

    async def next_id(self) -> int: ...

    async def next_reservation_id(self) -> int: ...

    # --- router / token reads ------------------------------------------------------------------
    async def quote(self, path: list[str], amount_raw: int) -> RouterQuote: ...  # revert -> ChainError(quote_failed)

    async def token_balance(self, token: str, holder: str) -> int: ...

    async def native_balance(self, address: str) -> int: ...  # wei

    async def allowance(self, token: str, owner: str, spender: str) -> int: ...

    async def token_metadata(self, token: str) -> tuple[str, int]: ...  # (symbol, decimals)

    # --- transaction builders (calldata + estimate_gas×1.2; revert -> ContractRevertError) ----
    async def build_approve(self, owner: str, token: str, spender: str, amount_raw: int) -> PreStep: ...

    async def build_reserve(self, customer: str, token: str, amount_raw: int, listing_ref: bytes) -> UnsignedTx: ...

    async def build_release(self, customer: str, reservation_id: int, amount_raw: int) -> UnsignedTx: ...

    async def build_release_all(self, customer: str, reservation_id: int) -> UnsignedTx: ...

    async def build_propose(self, trader: str, terms: Terms) -> UnsignedTx: ...

    async def build_open(self, customer: str, terms: Terms) -> UnsignedTx: ...

    async def build_open_reserved(self, customer: str, terms: Terms, reservation_id: int) -> UnsignedTx: ...

    async def build_fund(self, customer: str, agreement_id: int) -> UnsignedTx: ...

    async def build_fund_reserved(self, customer: str, agreement_id: int, reservation_id: int) -> UnsignedTx: ...

    async def build_accept(self, trader: str, agreement_id: int) -> UnsignedTx: ...

    async def build_cancel(self, who: str, agreement_id: int) -> UnsignedTx: ...

    async def build_trade(
        self,
        trader: str,
        agreement_id: int,
        token_in: str,
        token_out: str,
        amount_in: int,
        min_out: int,
        deadline: int,
    ) -> UnsignedTx: ...

    async def build_settle(self, caller: str, agreement_id: int, min_outs: list[int]) -> UnsignedTx: ...

    async def build_claim(self, customer: str, agreement_id: int, token: str) -> UnsignedTx: ...

    async def build_transfer(self, sender: str, to: str, amount_wei: int) -> UnsignedTx: ...  # native MON

    async def build_token_transfer(self, sender: str, token: str, to: str, amount_raw: int) -> UnsignedTx: ...

    async def build_admin_set_token(self, owner: str, token: str, allowed: bool, is_base: bool) -> UnsignedTx: ...

    async def build_admin_set_paused(self, owner: str, paused: bool) -> UnsignedTx: ...

    async def build_admin_set_fees(self, owner: str, platform_fee_bps: int, fee_recipient: str) -> UnsignedTx: ...

    async def build_admin_propose_router(self, owner: str, router: str) -> UnsignedTx: ...

    async def build_admin_apply_router(self, owner: str) -> UnsignedTx: ...

    async def build_admin_cancel_router(self, owner: str) -> UnsignedTx: ...

    async def build_admin_set_settle_slippage(self, owner: str, bps: int) -> UnsignedTx: ...

    # --- transaction / receipt / logs ----------------------------------------------------------
    async def estimate_gas(self, sender: str, to: str, data: str, value: int = 0) -> int: ...  # raw estimate

    async def get_transaction(self, tx_hash: str) -> TxInfo | None: ...

    async def get_receipt(self, tx_hash: str) -> TxReceiptResult | None: ...  # None = not mined / unknown

    async def explain_failure(self, receipt: TxReceiptResult) -> RevertInfo: ...  # status 0 -> replay + decode

    async def get_logs(
        self,
        from_block: int,
        to_block: int,
        address: str | None = None,
        topics: list[Any] | None = None,
    ) -> list[EventRecord]: ...  # address None -> vault

    async def faucet_mint(self, minter_key: str, token: str, to: str, amount_raw: int) -> str: ...  # tx_hash


__all__ = ["ChainGateway"]
