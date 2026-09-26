"""``MonadGateway`` — the real ``ChainGateway`` over web3.py v7 ``AsyncWeb3`` + ``AsyncHTTPProvider``
(03-backend-tasarim §1.8).

Rules: every RPC call goes through ``_call`` (token bucket, retry/backoff, error classification); reverts become
``ContractRevertError`` (never retried); ``build_*`` return wallet-ready calldata with ``estimateGas × 1.2`` and
ERC-20 ``approve`` pre-steps when the vault will ``transferFrom`` the caller; ``get_logs`` halves its window on
"range too large" answers; ``faucet_mint`` is the only place the backend signs a transaction itself.
"""
from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from time import monotonic
from typing import Any, TypeVar

import aiohttp
from eth_account import Account
from web3 import AsyncHTTPProvider, AsyncWeb3
from web3.exceptions import (
    BlockNotFound,
    ContractLogicError,
    ProviderConnectionError,
    TransactionNotFound,
    Web3RPCError,
)

from app.core.errors import AppError, ChainError
from app.services.chain import abi
from app.services.chain.abi import VaultError
from app.services.chain.addresses import checksum, normalize, short
from app.services.chain.errors import ChainUnavailableError, ContractRevertError, RpcRateLimitedError
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

log = logging.getLogger(__name__)
T = TypeVar("T")

RETRY_DELAYS = (0.5, 1.0, 2.0)
NATIVE_TRANSFER_GAS = 21_000
APPROVE_GAS_FALLBACK = 60_000
MIN_LOG_WINDOW = 50
# 03 §1.8 rule 2: when the caller has no allowance yet, estimateGas reverts inside transferFrom; use a fixed cap.
GAS_FALLBACK: dict[str, int] = {
    "open": 320_000,
    "fund": 260_000,
    "reserve": 200_000,
    "openReserved": 300_000,
    "fundReserved": 240_000,
}
_RATE_LIMIT_CODES = frozenset({-32005, 429})
_RANGE_CODES = frozenset({-32602, -32005, -32000})
_RANGE_HINTS = ("range", "limit", "too many", "too large", "exceed")


class ContractNotConfiguredError(AppError):
    """``settings.vault_address`` (or ``router_address``) is empty; builds and vault reads cannot run."""

    status_code = 503
    code = "contract_not_configured"


class TokenBucket:
    """Process-local rate limiter: ``rate_per_second`` refills, ``burst`` capacity; ``acquire`` awaits a slot."""

    def __init__(self, rate_per_second: float, burst: int | float | None = None) -> None:
        self.rate = max(float(rate_per_second), 0.001)
        self.capacity = float(max(1, int(burst if burst is not None else rate_per_second)))
        self.tokens = self.capacity
        self.updated = monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                wait = (1 - self.tokens) / self.rate
            await asyncio.sleep(wait)


def _rpc_error_code(e: Web3RPCError) -> int | None:
    resp = getattr(e, "rpc_response", None) or {}
    err = resp.get("error") if isinstance(resp, dict) else None
    if isinstance(err, dict):
        code = err.get("code")
        return int(code) if isinstance(code, int) else None
    return None


def _rpc_error_message(e: Web3RPCError) -> str:
    resp = getattr(e, "rpc_response", None) or {}
    err = resp.get("error") if isinstance(resp, dict) else None
    if isinstance(err, dict) and err.get("message"):
        return str(err["message"])
    return str(e)


def _to_hex(v: Any) -> str:
    if v is None:
        return "0x"
    if isinstance(v, bytes | bytearray):
        return "0x" + bytes(v).hex()
    s = str(v)
    return s if s.startswith("0x") else "0x" + s


def _now() -> datetime:
    return datetime.now(UTC)


class MonadGateway:
    kind = "monad"

    def __init__(self, settings: Any) -> None:
        self.rpc_url: str = settings.rpc_url
        self.chain_id_expected: int = int(settings.chain_id)
        self.vault_address: str = normalize(settings.vault_address) if settings.vault_address else ""
        self.router_address: str = normalize(settings.router_address) if settings.router_address else ""
        self.confirmations: int = int(getattr(settings, "confirmations", 2))
        self.gas_margin = Decimal("1.2")
        self.retries: int = max(1, int(getattr(settings, "rpc_retries", 3)))
        self.pending_tx_ttl_seconds: int = int(getattr(settings, "pending_tx_ttl_seconds", 900))
        timeout = float(getattr(settings, "rpc_timeout_seconds", 10))
        max_rps = float(getattr(settings, "rpc_max_rps", 12))
        call_rps = float(getattr(settings, "rpc_call_max_rps", 8))
        self._w3 = AsyncWeb3(
            AsyncHTTPProvider(
                self.rpc_url,
                request_kwargs={"timeout": aiohttp.ClientTimeout(total=timeout)},
                exception_retry_configuration=None,  # retries are ours (_call)
            )
        )
        self._limiter = TokenBucket(max_rps, max_rps)
        self._call_limiter = TokenBucket(call_rps, call_rps)
        self._block_cache: OrderedDict[int, BlockInfo] = OrderedDict()
        self._mint_lock = asyncio.Lock()

    # ------------------------------------------------------------------ plumbing

    async def _call(self, factory: Callable[[], Awaitable[T]], *, bucket: TokenBucket | None = None) -> T:
        """Run one RPC coroutine with rate limiting, error classification and bounded retries."""
        last: ChainError | None = None
        for attempt in range(self.retries):
            await (bucket or self._limiter).acquire()
            try:
                return await factory()
            except ContractLogicError as e:  # custom error / Error(string) / Panic — never retried
                raise self._revert_error(getattr(e, "data", None), str(e)) from e
            except Web3RPCError as e:
                code = _rpc_error_code(e)
                msg = _rpc_error_message(e)
                lowered = msg.lower()
                if code in _RATE_LIMIT_CODES or "too many requests" in lowered or "rate limit" in lowered:
                    last = RpcRateLimitedError(f"rpc rate limited: {msg}", details={"rpc_code": code, "attempts": attempt + 1})
                elif code == -32603 and "revert" not in lowered:  # node-side internal error: transient, retry
                    last = ChainUnavailableError(f"rpc internal error: {msg}", rpc=self.rpc_url, attempts=attempt + 1)
                else:  # invalid params / nonce / insufficient funds / unknown method: deterministic, no retry
                    raise ChainError(f"rpc error: {msg}", details={"rpc_code": code}) from e
            except aiohttp.ClientResponseError as e:
                if e.status == 429:
                    last = RpcRateLimitedError(f"rpc http 429: {e.message}", details={"attempts": attempt + 1})
                elif e.status >= 500:
                    last = ChainUnavailableError(f"rpc http {e.status}", rpc=self.rpc_url, attempts=attempt + 1)
                else:
                    raise ChainError(f"rpc http {e.status}: {e.message}") from e
            except (aiohttp.ClientError, TimeoutError, ProviderConnectionError, OSError) as e:
                last = ChainUnavailableError(f"rpc unreachable: {e}", rpc=self.rpc_url, attempts=attempt + 1)
            if attempt < self.retries - 1:
                await asyncio.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])
        assert last is not None
        raise last

    def _revert_error(self, data: Any, fallback_message: str = "") -> ContractRevertError:
        info = abi.decode_revert(data)
        if info.name is None and fallback_message and "revert" not in info.message:
            info = replace(info, message=f"{info.message}: {fallback_message}")
        err = VaultError(info.code) if info.code is not None else None
        return ContractRevertError(info.message, revert=info, error=err, error_code=abi.error_code_of(info))

    def _vault(self):
        if not self.vault_address:
            raise ContractNotConfiguredError("vault address is not configured")
        return abi.vault_contract(self._w3, self.vault_address)

    def _router(self):
        if not self.router_address:
            raise ContractNotConfiguredError("router address is not configured")
        return abi.router_contract(self._w3, self.router_address)

    async def _read(self, fn: Any) -> Any:
        return await self._call(lambda: fn.call(), bucket=self._call_limiter)

    def _cache_block(self, info: BlockInfo) -> None:
        self._block_cache[info.number] = info
        self._block_cache.move_to_end(info.number)
        while len(self._block_cache) > 256:
            self._block_cache.popitem(last=False)

    async def _block_time(self, number: int) -> datetime | None:
        try:
            return (await self.get_block(number)).timestamp
        except ChainError:
            return None

    # ------------------------------------------------------------------ network

    async def chain_id(self) -> int:
        return int(await self._call(lambda: self._w3.eth.chain_id))

    async def latest_block(self) -> int:
        return int(await self._call(lambda: self._w3.eth.block_number))

    async def get_block(self, number: int | str) -> BlockInfo:
        if isinstance(number, int) and number in self._block_cache:
            self._block_cache.move_to_end(number)
            return self._block_cache[number]

        async def fetch():
            try:
                return await self._w3.eth.get_block(number)
            except BlockNotFound as e:
                raise ChainError(f"block not found: {number}", details={"block": str(number)}) from e

        b = await self._call(fetch)
        info = BlockInfo(
            number=int(b["number"]),
            hash=_to_hex(b["hash"]).lower(),
            parent_hash=_to_hex(b["parentHash"]).lower(),
            timestamp=datetime.fromtimestamp(int(b["timestamp"]), tz=UTC),
        )
        self._cache_block(info)
        return info

    async def close(self) -> None:
        disconnect = getattr(self._w3.provider, "disconnect", None)
        if disconnect is not None:
            try:
                await disconnect()
            except Exception:  # noqa: BLE001 - closing is best effort
                log.debug("provider disconnect failed", exc_info=True)

    # ------------------------------------------------------------------ vault reads

    async def read_agreement(self, agreement_id: int) -> AgreementView:
        raw = await self._read(self._vault().functions.getAgreement(int(agreement_id)))
        return abi.agreement_from_raw(raw)

    async def read_balances(self, agreement_id: int) -> list[tuple[str, int]]:
        tokens, amounts = await self._read(self._vault().functions.getBalances(int(agreement_id)))
        return [(normalize(t), int(a)) for t, a in zip(tokens, amounts, strict=True)]

    async def read_value_in_base(self, agreement_id: int) -> int:
        return int(await self._read(self._vault().functions.valueInBase(int(agreement_id))))

    async def read_config(self) -> ConfigView:
        vault = self._vault()
        raw = await self._read(vault.functions.getConfig())
        owner = await self._read(vault.functions.owner())
        return abi.config_from_raw(raw, owner)

    async def read_reservation(self, reservation_id: int) -> ReservationView:
        raw = await self._read(self._vault().functions.getReservation(int(reservation_id)))
        return abi.reservation_from_raw(raw)

    async def preview_settle(self, agreement_id: int) -> SettlePreview | None:
        if not abi.has_function("TraderVault", "previewSettle"):
            return None
        raw = await self._read(self._vault().functions.previewSettle(int(agreement_id)))
        return abi.settle_preview_from_raw(raw)

    async def is_token_allowed(self, token: str) -> TokenInfoView:
        raw = await self._read(self._vault().functions.isTokenAllowed(checksum(token)))
        return abi.token_info_from_raw(raw)

    async def next_id(self) -> int:
        return int(await self._read(self._vault().functions.nextId()))

    async def next_reservation_id(self) -> int:
        return int(await self._read(self._vault().functions.nextReservationId()))

    # ------------------------------------------------------------------ router / token reads

    async def quote(self, path: list[str], amount_raw: int) -> RouterQuote:
        if len(path) < 2:
            raise ChainError("quote path needs at least two tokens", code="quote_failed")
        path_l = [normalize(p) for p in path]
        try:
            amounts = await self._read(
                self._router().functions.getAmountsOut(int(amount_raw), [checksum(p) for p in path_l])
            )
        except ContractRevertError as e:
            raise ChainError(
                f"router quote failed: {e.revert.message}",
                code="quote_failed",
                details={"error_code": e.error_code, "path": path_l, "amount_in": int(amount_raw)},
            ) from e
        return RouterQuote(path=path_l, amount_in=int(amount_raw), amounts=[int(a) for a in amounts], source="router")

    async def token_balance(self, token: str, holder: str) -> int:
        c = abi.erc20_contract(self._w3, token)
        return int(await self._read(c.functions.balanceOf(checksum(holder))))

    async def native_balance(self, address: str) -> int:
        addr = checksum(address)
        return int(await self._call(lambda: self._w3.eth.get_balance(addr)))

    async def allowance(self, token: str, owner: str, spender: str) -> int:
        c = abi.erc20_contract(self._w3, token)
        return int(await self._read(c.functions.allowance(checksum(owner), checksum(spender))))

    async def token_metadata(self, token: str) -> tuple[str, int]:
        c = abi.erc20_contract(self._w3, token)
        symbol = await self._read(c.functions.symbol())
        decimals = await self._read(c.functions.decimals())
        return str(symbol), int(decimals)

    # ------------------------------------------------------------------ builders

    async def estimate_gas(self, sender: str, to: str, data: str, value: int = 0) -> int:
        tx: dict[str, Any] = {"from": checksum(sender), "to": checksum(to), "data": data, "value": int(value)}
        return int(await self._call(lambda: self._w3.eth.estimate_gas(tx), bucket=self._call_limiter))

    async def _gas_for(self, sender: str, to: str, data: str, value: int, action: str,
                       has_pre_steps: bool) -> tuple[int | None, bool]:
        """(gas, estimated). Falls back to ``GAS_FALLBACK`` when the only failure is the missing allowance."""
        try:
            raw = await self.estimate_gas(sender, to, data, value)
        except ContractRevertError as e:
            fallback = GAS_FALLBACK.get(action)
            allowance_problem = e.revert.code == VaultError.TransferFailed or (
                e.revert.name or ""
            ).startswith("erc20:ERC20Insufficient")
            if has_pre_steps and fallback and allowance_problem:
                return fallback, False
            raise
        return int(Decimal(raw) * self.gas_margin), True

    async def _build(
        self,
        *,
        kind: str,
        action: str,
        sender: str,
        to: str,
        data: str,
        summary: dict[str, Any],
        value: int = 0,
        pre_steps: list[PreStep] | None = None,
        estimate: bool = True,
        gas: int | None = None,
    ) -> UnsignedTx:
        sender_l = normalize(sender)
        to_l = normalize(to)
        estimated = False
        if estimate and gas is None:
            gas, estimated = await self._gas_for(sender_l, to_l, data, value, action, bool(pre_steps))
        full_summary: dict[str, Any] = {"action": action, **summary, "gas_estimated": estimated}
        return UnsignedTx(
            kind=kind,
            action=action,
            from_address=sender_l,
            to=to_l,
            data=data,
            value=int(value),
            gas=gas,
            chain_id=self.chain_id_expected,
            summary=full_summary,
            expires_at=_now() + timedelta(seconds=self.pending_tx_ttl_seconds),
            pre_steps=list(pre_steps or []),
        )

    def _vault_data(self, fn: str, args: list) -> str:
        if not self.vault_address:
            raise ContractNotConfiguredError("vault address is not configured")
        return abi.encode_call("TraderVault", fn, args)

    async def _approval_steps(self, owner: str, token: str, amount_raw: int) -> list[PreStep]:
        current = await self.allowance(token, owner, self.vault_address)
        if current >= int(amount_raw):
            return []
        return [await self.build_approve(owner, token, self.vault_address, int(amount_raw))]

    async def build_approve(self, owner: str, token: str, spender: str, amount_raw: int) -> PreStep:
        data = abi.encode_call("IERC20", "approve", [spender, int(amount_raw)])
        try:
            gas = int(Decimal(await self.estimate_gas(owner, token, data)) * self.gas_margin)
        except ContractRevertError:
            gas = APPROVE_GAS_FALLBACK
        return PreStep(
            kind="approve",
            to=normalize(token),
            data=data,
            value=0,
            gas=gas,
            description=f"{short(token)} için {amount_raw} birim harcama onayı ({short(spender)})",
            spender=normalize(spender),
            token=normalize(token),
            amount_raw=int(amount_raw),
        )

    async def build_reserve(self, customer: str, token: str, amount_raw: int, listing_ref: bytes) -> UnsignedTx:
        data = self._vault_data("reserve", [token, int(amount_raw), bytes(listing_ref)])
        steps = await self._approval_steps(customer, token, amount_raw)
        return await self._build(
            kind="reserve", action="reserve", sender=customer, to=self.vault_address, data=data, pre_steps=steps,
            summary={"token": normalize(token), "amount_raw": int(amount_raw), "listing_ref": _to_hex(listing_ref)},
        )

    async def build_release(self, customer: str, reservation_id: int, amount_raw: int) -> UnsignedTx:
        data = self._vault_data("release", [int(reservation_id), int(amount_raw)])
        return await self._build(
            kind="release", action="release", sender=customer, to=self.vault_address, data=data,
            summary={"reservation_id": int(reservation_id), "amount_raw": int(amount_raw)},
        )

    async def build_release_all(self, customer: str, reservation_id: int) -> UnsignedTx:
        data = self._vault_data("releaseAll", [int(reservation_id)])
        return await self._build(
            kind="release", action="releaseAll", sender=customer, to=self.vault_address, data=data,
            summary={"reservation_id": int(reservation_id)},
        )

    async def build_propose(self, trader: str, terms: Terms) -> UnsignedTx:
        terms.validate()
        data = self._vault_data("propose", [terms])
        return await self._build(
            kind="propose", action="propose", sender=trader, to=self.vault_address, data=data,
            summary={"terms": terms.as_dict()},
        )

    async def build_open(self, customer: str, terms: Terms) -> UnsignedTx:
        terms.validate()
        data = self._vault_data("open", [terms])
        steps = await self._approval_steps(customer, terms.base_token, terms.principal)
        return await self._build(
            kind="open", action="open", sender=customer, to=self.vault_address, data=data, pre_steps=steps,
            summary={"terms": terms.as_dict()},
        )

    async def build_open_reserved(self, customer: str, terms: Terms, reservation_id: int) -> UnsignedTx:
        terms.validate()
        data = self._vault_data("openReserved", [terms, int(reservation_id)])
        return await self._build(
            kind="open_reserved", action="openReserved", sender=customer, to=self.vault_address, data=data,
            summary={"terms": terms.as_dict(), "reservation_id": int(reservation_id)},
        )

    async def build_fund(self, customer: str, agreement_id: int) -> UnsignedTx:
        ag = await self.read_agreement(agreement_id)
        data = self._vault_data("fund", [int(agreement_id)])
        steps = await self._approval_steps(customer, ag.terms.base_token, ag.terms.principal)
        return await self._build(
            kind="fund", action="fund", sender=customer, to=self.vault_address, data=data, pre_steps=steps,
            summary={"agreement_id": int(agreement_id), "principal": ag.terms.principal, "token": ag.terms.base_token},
        )

    async def build_fund_reserved(self, customer: str, agreement_id: int, reservation_id: int) -> UnsignedTx:
        data = self._vault_data("fundReserved", [int(agreement_id), int(reservation_id)])
        return await self._build(
            kind="fund_reserved", action="fundReserved", sender=customer, to=self.vault_address, data=data,
            summary={"agreement_id": int(agreement_id), "reservation_id": int(reservation_id)},
        )

    async def build_accept(self, trader: str, agreement_id: int) -> UnsignedTx:
        data = self._vault_data("accept", [int(agreement_id)])
        return await self._build(
            kind="accept", action="accept", sender=trader, to=self.vault_address, data=data,
            summary={"agreement_id": int(agreement_id)},
        )

    async def build_cancel(self, who: str, agreement_id: int) -> UnsignedTx:
        data = self._vault_data("cancel", [int(agreement_id)])
        return await self._build(
            kind="cancel", action="cancel", sender=who, to=self.vault_address, data=data,
            summary={"agreement_id": int(agreement_id)},
        )

    async def build_trade(
        self, trader: str, agreement_id: int, token_in: str, token_out: str, amount_in: int, min_out: int, deadline: int
    ) -> UnsignedTx:
        data = self._vault_data(
            "trade", [int(agreement_id), token_in, token_out, int(amount_in), int(min_out), int(deadline)]
        )
        return await self._build(
            kind="trade", action="trade", sender=trader, to=self.vault_address, data=data,
            summary={
                "agreement_id": int(agreement_id), "token_in": normalize(token_in), "token_out": normalize(token_out),
                "amount_in": int(amount_in), "min_out": int(min_out), "deadline": int(deadline),
            },
        )

    async def build_settle(self, caller: str, agreement_id: int, min_outs: list[int]) -> UnsignedTx:
        outs = [int(m) for m in min_outs]
        data = self._vault_data("settle", [int(agreement_id), outs])
        return await self._build(
            kind="settle", action="settle", sender=caller, to=self.vault_address, data=data,
            summary={"agreement_id": int(agreement_id), "min_outs": outs},
        )

    async def build_claim(self, customer: str, agreement_id: int, token: str) -> UnsignedTx:
        data = self._vault_data("claim", [int(agreement_id), token])
        return await self._build(
            kind="claim", action="claim", sender=customer, to=self.vault_address, data=data,
            summary={"agreement_id": int(agreement_id), "token": normalize(token)},
        )

    async def build_transfer(self, sender: str, to: str, amount_wei: int) -> UnsignedTx:
        return await self._build(
            kind="transfer", action="transfer", sender=sender, to=to, data="0x", value=int(amount_wei),
            estimate=False, gas=NATIVE_TRANSFER_GAS,
            summary={"to": normalize(to), "amount_wei": int(amount_wei), "native": True},
        )

    async def build_token_transfer(self, sender: str, token: str, to: str, amount_raw: int) -> UnsignedTx:
        data = abi.encode_call("IERC20", "transfer", [to, int(amount_raw)])
        return await self._build(
            kind="transfer", action="transfer", sender=sender, to=token, data=data,
            summary={"to": normalize(to), "token": normalize(token), "amount_raw": int(amount_raw), "native": False},
        )

    async def build_admin_set_token(self, owner: str, token: str, allowed: bool, is_base: bool) -> UnsignedTx:
        data = self._vault_data("setToken", [token, bool(allowed), bool(is_base)])
        return await self._build(
            kind="admin", action="setToken", sender=owner, to=self.vault_address, data=data,
            summary={"token": normalize(token), "allowed": bool(allowed), "is_base": bool(is_base)},
        )

    async def build_admin_set_paused(self, owner: str, paused: bool) -> UnsignedTx:
        data = self._vault_data("setPaused", [bool(paused)])
        return await self._build(
            kind="admin", action="setPaused", sender=owner, to=self.vault_address, data=data,
            summary={"paused": bool(paused)},
        )

    async def build_admin_set_fees(self, owner: str, platform_fee_bps: int, fee_recipient: str) -> UnsignedTx:
        data = self._vault_data("setFees", [int(platform_fee_bps), fee_recipient])
        return await self._build(
            kind="admin", action="setFees", sender=owner, to=self.vault_address, data=data,
            summary={"platform_fee_bps": int(platform_fee_bps), "fee_recipient": normalize(fee_recipient)},
        )

    async def build_admin_propose_router(self, owner: str, router: str) -> UnsignedTx:
        data = self._vault_data("proposeRouterChange", [router])
        return await self._build(
            kind="admin", action="proposeRouterChange", sender=owner, to=self.vault_address, data=data,
            summary={"router": normalize(router)},
        )

    async def build_admin_apply_router(self, owner: str) -> UnsignedTx:
        data = self._vault_data("applyRouterChange", [])
        return await self._build(
            kind="admin", action="applyRouterChange", sender=owner, to=self.vault_address, data=data, summary={},
        )

    async def build_admin_cancel_router(self, owner: str) -> UnsignedTx:
        data = self._vault_data("cancelRouterChange", [])
        return await self._build(
            kind="admin", action="cancelRouterChange", sender=owner, to=self.vault_address, data=data, summary={},
        )

    async def build_admin_set_settle_slippage(self, owner: str, bps: int) -> UnsignedTx:
        data = self._vault_data("setSettleSlippage", [int(bps)])
        return await self._build(
            kind="admin", action="setSettleSlippage", sender=owner, to=self.vault_address, data=data,
            summary={"settle_slippage_bps": int(bps)},
        )

    # ------------------------------------------------------------------ tx / receipt / logs

    async def get_transaction(self, tx_hash: str) -> TxInfo | None:
        async def fetch():
            try:
                return await self._w3.eth.get_transaction(tx_hash)
            except TransactionNotFound:
                return None

        tx = await self._call(fetch)
        if tx is None:
            return None
        return TxInfo(
            tx_hash=_to_hex(tx["hash"]).lower(),
            from_address=normalize(tx["from"]),
            to_address=normalize(tx["to"]) if tx.get("to") else None,
            input=_to_hex(tx.get("input")),
            value=int(tx.get("value", 0)),
            nonce=int(tx.get("nonce", 0)),
            gas=int(tx.get("gas", 0)),
            block_number=int(tx["blockNumber"]) if tx.get("blockNumber") is not None else None,
            block_hash=_to_hex(tx["blockHash"]).lower() if tx.get("blockHash") else None,
        )

    async def get_receipt(self, tx_hash: str) -> TxReceiptResult | None:
        async def fetch():
            try:
                return await self._w3.eth.get_transaction_receipt(tx_hash)
            except TransactionNotFound:
                return None

        rc = await self._call(fetch)
        if rc is None or rc.get("blockNumber") is None:
            return None
        tx = await self.get_transaction(tx_hash)
        block_number = int(rc["blockNumber"])
        block_time = await self._block_time(block_number)
        latest = await self.latest_block()
        logs = list(rc.get("logs") or [])
        events = abi.decode_receipt_logs(logs, self.vault_address) if self.vault_address else []
        if block_time is not None:
            events = [replace(ev, block_timestamp=block_time) for ev in events]
        return TxReceiptResult(
            tx_hash=_to_hex(rc["transactionHash"]).lower(),
            status=int(rc.get("status", 0)),
            block_number=block_number,
            block_hash=_to_hex(rc["blockHash"]).lower(),
            block_timestamp=block_time,
            from_address=normalize(rc["from"]),
            to_address=normalize(rc["to"]) if rc.get("to") else None,
            input=tx.input if tx else "0x",
            value=tx.value if tx else 0,
            gas_used=int(rc.get("gasUsed", 0)),
            gas_limit=tx.gas if tx else 0,
            effective_gas_price=int(rc.get("effectiveGasPrice", 0) or 0),
            events=events,
            raw_log_count=len(logs),
            confirmations=max(0, latest - block_number + 1),
            revert=None,
        )

    async def explain_failure(self, receipt: TxReceiptResult) -> RevertInfo:
        if receipt.status == 1:
            return RevertInfo(selector=None, name=None, code=None, args={}, message="işlem başarılı")
        if receipt.revert is not None:
            return receipt.revert
        params: dict[str, Any] = {
            "from": checksum(receipt.from_address),
            "to": checksum(receipt.to_address) if receipt.to_address else None,
            "data": receipt.input,
            "value": int(receipt.value),
        }
        try:
            await self._call(lambda: self._w3.eth.call(params, receipt.block_number), bucket=self._call_limiter)
        except ContractRevertError as e:
            return e.revert
        except ChainError as e:
            return RevertInfo(selector=None, name=None, code=None, args={"rpc": str(e)},
                              message=f"{abi.GENERIC_REVERT_MESSAGE} (neden okunamadı)")
        if receipt.gas_limit and receipt.gas_used >= receipt.gas_limit:
            return RevertInfo(selector=None, name="OutOfGas", code=None,
                              args={"gas_used": receipt.gas_used, "gas_limit": receipt.gas_limit},
                              message="işlem gaz limitine takıldı")
        return RevertInfo(selector=None, name=None, code=None, args={}, message=abi.GENERIC_REVERT_MESSAGE)

    async def get_logs(
        self, from_block: int, to_block: int, address: str | None = None, topics: list[Any] | None = None
    ) -> list[EventRecord]:
        target = normalize(address) if address else self.vault_address
        if not target:
            raise ContractNotConfiguredError("vault address is not configured")
        raw_logs = await self._get_logs_window(int(from_block), int(to_block), checksum(target), topics)
        records: list[EventRecord] = []
        times: dict[int, datetime | None] = {}
        for raw in raw_logs:
            rec = abi.decode_log(raw)
            if rec is None:
                continue
            if rec.block_number not in times:
                times[rec.block_number] = await self._block_time(rec.block_number)
            records.append(replace(rec, block_timestamp=times[rec.block_number]))
        records.sort(key=lambda r: (r.block_number, r.log_index))
        return records

    async def _get_logs_window(self, from_block: int, to_block: int, address: str, topics: list[Any] | None) -> list[Any]:
        params: dict[str, Any] = {"fromBlock": from_block, "toBlock": to_block, "address": address}
        if topics:
            params["topics"] = topics
        try:
            return list(await self._call(lambda: self._w3.eth.get_logs(params)))
        except (RpcRateLimitedError, ChainError) as e:
            code = e.details.get("rpc_code") if isinstance(e.details, dict) else None
            msg = str(e).lower()
            range_problem = code in _RANGE_CODES or any(h in msg for h in _RANGE_HINTS)
            if not range_problem or to_block - from_block + 1 <= MIN_LOG_WINDOW:
                raise
            mid = (from_block + to_block) // 2
            left = await self._get_logs_window(from_block, mid, address, topics)
            right = await self._get_logs_window(mid + 1, to_block, address, topics)
            return left + right

    # ------------------------------------------------------------------ faucet

    async def _fee_fields(self) -> dict[str, int]:
        try:
            block = await self._call(lambda: self._w3.eth.get_block("latest"))
            base_fee = int(block.get("baseFeePerGas") or 0)
            tip = int(await self._call(lambda: self._w3.eth.max_priority_fee))
            if base_fee <= 0:
                raise ValueError("no baseFeePerGas")
            return {"maxFeePerGas": base_fee * 2 + tip, "maxPriorityFeePerGas": tip}
        except (ChainError, ValueError, KeyError, TypeError):
            gas_price = int(await self._call(lambda: self._w3.eth.gas_price))
            return {"gasPrice": gas_price}

    async def faucet_mint(self, minter_key: str, token: str, to: str, amount_raw: int) -> str:
        account = Account.from_key(minter_key)
        data = abi.encode_call("TestToken", "mint", [to, int(amount_raw)])
        async with self._mint_lock:
            for attempt in range(2):
                nonce = int(await self._call(lambda: self._w3.eth.get_transaction_count(account.address, "pending")))
                gas = int(Decimal(await self.estimate_gas(account.address, token, data)) * self.gas_margin)
                tx: dict[str, Any] = {
                    "chainId": self.chain_id_expected,
                    "from": account.address,
                    "to": checksum(token),
                    "data": data,
                    "value": 0,
                    "gas": gas,
                    "nonce": nonce,
                    **(await self._fee_fields()),
                }
                signed = account.sign_transaction(tx)
                raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
                try:
                    tx_hash = await self._call(lambda raw=raw: self._w3.eth.send_raw_transaction(raw))
                except ChainError as e:
                    lowered = str(e).lower()
                    retryable = "nonce too low" in lowered or "replacement transaction underpriced" in lowered
                    if attempt == 0 and retryable:
                        log.warning("faucet_mint nonce race (%s); refreshing nonce once", e)
                        continue
                    raise
                return _to_hex(tx_hash).lower()
        raise ChainError("faucet mint could not be sent")  # pragma: no cover - loop always returns/raises


__all__ = ["GAS_FALLBACK", "ContractNotConfiguredError", "MonadGateway", "TokenBucket"]
