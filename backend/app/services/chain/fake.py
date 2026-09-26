"""``FakeChainGateway`` — in-memory ``ChainGateway`` for tests (03-backend-tasarim §1.9). No network.

It replays ``TraderVault.sol`` (01-kontrat-spec §4) as a Python state machine: agreements, per-agreement token
balances, reservations, the token allow-list, a ``MockRouter`` price table, ERC-20 wallets/allowances and native
MON balances. ``build_*`` produce the **real ABI calldata** (``abi.encode_call``) and dry-run the action so contract
rule violations surface at build time exactly like ``estimateGas`` would (``ContractRevertError``). ``send()`` stands
in for the wallet: it queues a transaction; ``mine()`` applies queued transactions in a new block, producing a
``TxReceiptResult`` whose logs are **ABI-encoded and decoded again** through ``abi.encode_log``/``abi.decode_log`` so
the production decode path is what tests exercise. ``get_logs`` serves those logs; ``reorg(depth)`` drops the last
blocks (receipts, logs, hashes) and restores the state snapshot taken before them.

Semantics deliberately mirror Solidity check order and OZ behaviour (``None = 0`` enum sentinel, ``release(0)`` ->
``ZeroAmount``, missing allowance -> ``SafeERC20FailedOperation`` = ``TransferFailed`` 29 as 01-spec §5 states).
"""
from __future__ import annotations

import copy
import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Any

from eth_utils import keccak

from app.core.errors import ChainError
from app.services.chain import abi
from app.services.chain.abi import KEEPER_GRACE, MAX_TOKENS, VaultError
from app.services.chain.addresses import ZERO_ADDRESS, normalize, short
from app.services.chain.amounts import UINT256_MAX, drawdown_floor, min_out_for_slippage, settle_math
from app.services.chain.errors import ContractRevertError
from app.services.chain.types import (
    RES_CONSUMED,
    RES_NONE,
    RES_OPEN,
    RES_RELEASED,
    STATUS_ACTIVE,
    STATUS_CANCELLED,
    STATUS_FUNDED,
    STATUS_NONE,
    STATUS_PROPOSED,
    STATUS_SETTLED,
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

# Fixed fake addresses (tests/conftest + scripts/seed_assets test JSON use the same ones; lower-case).
FAKE_VAULT = "0x00000000000000000000000000000000000000f1"
FAKE_ROUTER = "0x00000000000000000000000000000000000000f2"
FAKE_OWNER = "0x00000000000000000000000000000000000000a0"
FAKE_FEE_RECIPIENT = "0x00000000000000000000000000000000000000a1"
FAKE_MINTER = "0x00000000000000000000000000000000000000a2"
FAKE_TUSDC = "0x00000000000000000000000000000000000000c1"
FAKE_TWETH = "0x00000000000000000000000000000000000000c2"
FAKE_TWBTC = "0x00000000000000000000000000000000000000c3"
FAKE_CHAIN_ID = 10143
DEFAULT_NOW = 1_800_000_000  # 2027-01-15, a fixed fake clock
DEFAULT_BLOCK = 1000
DEFAULT_ROUTER_DELAY = 600
BLOCK_INTERVAL = 1  # seconds added to the clock per mined block
GAS_PRICE = 52_000_000_000  # 52 gwei (Monad testnet base fee)
GAS_TABLE: dict[str, int] = {
    "native": 21_000,
    "approve": 46_000,
    "transfer": 52_000,
    "transferFrom": 60_000,
    "mint": 55_000,
    "reserve": 120_000,
    "release": 60_000,
    "releaseAll": 60_000,
    "propose": 180_000,
    "open": 220_000,
    "openReserved": 200_000,
    "fund": 130_000,
    "fundReserved": 120_000,
    "accept": 70_000,
    "cancel": 60_000,
    "trade": 250_000,
    "settle": 300_000,
    "claim": 70_000,
    "setToken": 50_000,
    "setPaused": 30_000,
    "setFees": 35_000,
    "proposeRouterChange": 50_000,
    "applyRouterChange": 40_000,
    "cancelRouterChange": 30_000,
    "setSettleSlippage": 30_000,
}
MINTER_ROLE = keccak(text="MINTER_ROLE")

Event = tuple[str, str, dict[str, Any]]  # (emitting address, event name, ABI-named args)


# --- state ------------------------------------------------------------------------------------------


@dataclass
class _Token:
    symbol: str
    decimals: int
    allowed: bool = False
    is_base: bool = False


@dataclass
class _Agreement:
    id: int
    terms: Terms
    status: int
    proposer: str
    created_at: int
    platform_fee_bps: int
    tokens: list[str]
    last_value: int
    start_time: int = 0
    end_time: int = 0
    settled_at: int = 0
    final_value: int = 0
    trader_fee: int = 0
    platform_fee: int = 0
    customer_payout: int = 0

    def view(self) -> AgreementView:
        return AgreementView(
            id=self.id, terms=self.terms, status=self.status, proposer=self.proposer, created_at=self.created_at,
            start_time=self.start_time, end_time=self.end_time, settled_at=self.settled_at,
            platform_fee_bps=self.platform_fee_bps, tokens=list(self.tokens), final_value=self.final_value,
            trader_fee=self.trader_fee, platform_fee=self.platform_fee, customer_payout=self.customer_payout,
            last_value=self.last_value,
        )


@dataclass
class _Reservation:
    id: int
    customer: str
    token: str
    amount: int
    original: int
    status: int
    created_at: int
    listing_ref: bytes

    def view(self) -> ReservationView:
        return ReservationView(
            id=self.id, customer=self.customer, token=self.token, amount=self.amount, original=self.original,
            status=self.status, created_at=self.created_at, listing_ref=self.listing_ref,
        )


@dataclass
class _Config:
    router: str
    fee_recipient: str
    platform_fee_bps: int = 100
    settle_slippage_bps: int = 100
    paused: bool = False
    pending_router: str = ZERO_ADDRESS
    router_activation_time: int = 0
    router_delay: int = DEFAULT_ROUTER_DELAY


@dataclass
class _State:
    config: _Config
    owner: str
    tokens: dict[str, _Token] = field(default_factory=dict)
    agreements: dict[int, _Agreement] = field(default_factory=dict)
    balances: dict[tuple[int, str], int] = field(default_factory=dict)  # (agreement id, token) -> raw
    reservations: dict[int, _Reservation] = field(default_factory=dict)
    wallets: dict[tuple[str, str], int] = field(default_factory=dict)  # (holder, token) -> raw
    allowances: dict[tuple[str, str, str], int] = field(default_factory=dict)  # (owner, token, spender) -> raw
    native: dict[str, int] = field(default_factory=dict)  # holder -> wei
    minters: set[str] = field(default_factory=set)
    prices: dict[tuple[str, str], Fraction] = field(default_factory=dict)  # (in, out) -> out per in (raw)
    blocked: set[tuple[str, str]] = field(default_factory=set)  # (token, address) transfers to address fail
    strict_router: bool = True
    next_id: int = 1
    next_reservation_id: int = 1


@dataclass
class _QueuedTx:
    tx_hash: str
    sender: str
    to: str
    data: str
    value: int
    nonce: int
    gas: int | None


def _terms_from_abi(d: dict[str, Any]) -> Terms:
    return Terms(
        customer=normalize(d["customer"]),
        trader=normalize(d["trader"]),
        base_token=normalize(d["baseToken"]),
        principal=int(d["principal"]),
        duration_seconds=int(d["durationSeconds"]),
        commission_bps=int(d["commissionBps"]),
        max_drawdown_bps=int(d["maxDrawdownBps"]),
        listing_ref=bytes(d["listingRef"]),
    )


def _ts(seconds: int) -> datetime:
    return datetime.fromtimestamp(int(seconds), tz=UTC)


class FakeChainGateway:
    kind = "fake"

    def __init__(
        self,
        *,
        now: int = DEFAULT_NOW,
        block_number: int = DEFAULT_BLOCK,
        chain_id: int = FAKE_CHAIN_ID,
        vault: str = FAKE_VAULT,
        router: str = FAKE_ROUTER,
        owner: str = FAKE_OWNER,
        fee_recipient: str = FAKE_FEE_RECIPIENT,
        minter_address: str = FAKE_MINTER,
        auto_mine: bool = True,
        install_defaults: bool = True,
        pending_tx_ttl_seconds: int = 900,
    ) -> None:
        self.chain_id_expected = int(chain_id)
        self.vault_address = normalize(vault)
        self.router_address = normalize(router)
        self.owner_address = normalize(owner)
        self.fee_recipient_address = normalize(fee_recipient)
        self.minter_address = normalize(minter_address)
        self.auto_mine = auto_mine
        self.pending_tx_ttl_seconds = pending_tx_ttl_seconds
        self._current_token = ""  # token contract being executed (set by _dispatch for ERC-20 calls)
        self._initial = dict(now=now, block_number=block_number, install_defaults=install_defaults)
        self.reset()

    # ------------------------------------------------------------------ setup / test controls

    def reset(self) -> None:
        """Back to the constructor state (defaults re-installed when requested)."""
        self.now: int = int(self._initial["now"])
        self._state = _State(
            config=_Config(router=self.router_address, fee_recipient=self.fee_recipient_address),
            owner=self.owner_address,
            minters={self.minter_address, self.owner_address},
        )
        self.blocks: list[BlockInfo] = []
        self._reorg_salt = 0
        self._append_block(int(self._initial["block_number"]))
        self.txs: dict[str, TxInfo] = {}
        self.receipts: dict[str, TxReceiptResult] = {}
        self.logs: list[EventRecord] = []
        self._raw_logs: dict[tuple[str, int], dict[str, Any]] = {}
        self.queue: list[_QueuedTx] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._nonces: dict[str, int] = {}
        self._snapshots: dict[int, _State] = {self.blocks[-1].number: copy.deepcopy(self._state)}
        if self._initial["install_defaults"]:
            self.install_defaults()

    def install_defaults(self) -> None:
        """tUSDC(6, base) / tWETH(18) / tWBTC(8), 01-spec §9.1 prices, router liquidity, fees 100 bps."""
        self.register_token(FAKE_TUSDC, "tUSDC", 6, allowed=True, is_base=True)
        self.register_token(FAKE_TWETH, "tWETH", 18, allowed=True)
        self.register_token(FAKE_TWBTC, "tWBTC", 8, allowed=True)
        self.set_price(FAKE_TWETH, FAKE_TUSDC, 3000)
        self.set_price(FAKE_TWBTC, FAKE_TUSDC, 60_000)
        self.set_price(FAKE_TWBTC, FAKE_TWETH, 20)
        self.fund_wallet(self.router_address, FAKE_TUSDC, 10_000_000 * 10**6)
        self.fund_wallet(self.router_address, FAKE_TWETH, 5_000 * 10**18)
        self.fund_wallet(self.router_address, FAKE_TWBTC, 250 * 10**8)
        self._state.config.platform_fee_bps = 100
        self._state.config.settle_slippage_bps = 100
        self._state.config.router_delay = DEFAULT_ROUTER_DELAY
        self._snapshot()

    def register_token(self, address: str, symbol: str, decimals: int, *, allowed: bool = False,
                       is_base: bool = False) -> None:
        addr = normalize(address)
        tok = self._state.tokens.get(addr)
        if tok is None:
            self._state.tokens[addr] = _Token(symbol=symbol, decimals=int(decimals), allowed=allowed,
                                              is_base=allowed and is_base)
        else:
            tok.symbol, tok.decimals = symbol, int(decimals)
            if allowed:
                tok.allowed, tok.is_base = True, is_base

    def set_token(self, token: str, allowed: bool, is_base: bool = False, *, symbol: str | None = None,
                  decimals: int | None = None) -> None:
        """Direct allow-list edit (no tx). Unknown tokens are registered (18 decimals unless given)."""
        addr = normalize(token)
        tok = self._state.tokens.get(addr)
        if tok is None:
            tok = _Token(symbol=symbol or short(addr), decimals=decimals if decimals is not None else 18)
            self._state.tokens[addr] = tok
        elif symbol or decimals is not None:
            tok.symbol = symbol or tok.symbol
            tok.decimals = decimals if decimals is not None else tok.decimals
        tok.allowed = bool(allowed)
        tok.is_base = bool(allowed and is_base)

    def set_paused(self, paused: bool) -> None:
        self._state.config.paused = bool(paused)

    def set_fees(self, platform_fee_bps: int, fee_recipient: str | None = None) -> None:
        self._state.config.platform_fee_bps = int(platform_fee_bps)
        if fee_recipient:
            self._state.config.fee_recipient = normalize(fee_recipient)

    def set_settle_slippage(self, bps: int) -> None:
        self._state.config.settle_slippage_bps = int(bps)

    def set_owner(self, owner: str) -> None:
        self._state.owner = normalize(owner)
        self._state.minters.add(self._state.owner)

    def set_price(self, token_in: str, token_out: str, price: Fraction | Decimal | int | str, *,
                  both_ways: bool = True) -> None:
        """Human price: 1 ``token_in`` = ``price`` ``token_out`` (decimals from the registry)."""
        a, b = normalize(token_in), normalize(token_out)
        p = Fraction(str(price)) if not isinstance(price, Fraction) else price
        if p <= 0:
            raise ValueError("price must be positive")
        din, dout = self._decimals(a), self._decimals(b)
        raw = p * Fraction(10**dout, 10**din)
        self._state.prices[(a, b)] = raw
        if both_ways:
            self._state.prices[(b, a)] = 1 / raw

    def set_raw_price(self, token_in: str, token_out: str, num: int, den: int) -> None:
        """MockRouter semantics: ``out = in * num / den`` (raw units), one direction."""
        if num <= 0 or den <= 0:
            raise ValueError("num/den must be positive")
        self._state.prices[(normalize(token_in), normalize(token_out))] = Fraction(int(num), int(den))

    def remove_route(self, token_in: str, token_out: str, *, both_ways: bool = True) -> None:
        a, b = normalize(token_in), normalize(token_out)
        self._state.prices.pop((a, b), None)
        if both_ways:
            self._state.prices.pop((b, a), None)

    def set_strict_router(self, strict: bool) -> None:
        self._state.strict_router = bool(strict)

    def fund_wallet(self, address: str, token: str, raw: int) -> None:
        key = (normalize(address), normalize(token))
        self._state.wallets[key] = self._state.wallets.get(key, 0) + int(raw)

    def fund_native(self, address: str, wei: int) -> None:
        addr = normalize(address)
        self._state.native[addr] = self._state.native.get(addr, 0) + int(wei)

    def approve(self, owner: str, token: str, spender: str, raw: int) -> None:
        self._state.allowances[(normalize(owner), normalize(token), normalize(spender))] = int(raw)

    def set_blocked(self, token: str, address: str, blocked: bool = True) -> None:
        """Make ``token`` transfers to ``address`` fail (in-kind delivery / fee leg tests)."""
        key = (normalize(token), normalize(address))
        if blocked:
            self._state.blocked.add(key)
        else:
            self._state.blocked.discard(key)

    def add_minter(self, address: str) -> None:
        self._state.minters.add(normalize(address))

    def advance(self, seconds: int) -> None:
        self.now += int(seconds)

    def set_time(self, timestamp: int) -> None:
        self.now = int(timestamp)

    def reorg(self, depth: int) -> None:
        """Drop the last ``depth`` blocks (their receipts, txs and logs), restore the state snapshot from before them
        and re-mine ``depth`` empty blocks with different hashes — what an indexer sees after a chain reorg."""
        depth = int(depth)
        if depth <= 0 or depth >= len(self.blocks):
            raise ValueError("reorg depth must be in 1..len(blocks)-1")
        removed = self.blocks[-depth:]
        self.blocks = self.blocks[:-depth]
        gone = {b.number for b in removed}
        for h, rc in list(self.receipts.items()):
            if rc.block_number in gone:
                del self.receipts[h]
                self.txs.pop(h, None)
        self.logs = [ev for ev in self.logs if ev.block_number not in gone]
        self._raw_logs = {k: v for k, v in self._raw_logs.items() if v["blockNumber"] not in gone}
        snap = self._snapshots.get(self.blocks[-1].number)
        if snap is not None:
            self._state = copy.deepcopy(snap)
        for n in gone:
            self._snapshots.pop(n, None)
        self._reorg_salt += 1
        for _ in range(depth):
            self._append_block(self.blocks[-1].number + 1)
            self.now += BLOCK_INTERVAL
            self._snapshot()

    # ------------------------------------------------------------------ internals: blocks / hashes

    def _append_block(self, number: int) -> BlockInfo:
        parent = self.blocks[-1].hash if self.blocks else "0x" + "00" * 32
        h = "0x" + keccak(f"{number}:{parent}:{self._reorg_salt}".encode()).hex()
        info = BlockInfo(number=number, hash=h, parent_hash=parent, timestamp=_ts(self.now))
        self.blocks.append(info)
        return info

    def _snapshot(self) -> None:
        self._snapshots[self.blocks[-1].number] = copy.deepcopy(self._state)
        while len(self._snapshots) > 64:
            del self._snapshots[min(self._snapshots)]

    def _decimals(self, token: str) -> int:
        tok = self._state.tokens.get(normalize(token))
        return tok.decimals if tok else 18

    # ------------------------------------------------------------------ internals: reverts

    @staticmethod
    def _fail(name: str | VaultError, args: list | tuple | None = None) -> None:
        sol_name = name.name if isinstance(name, VaultError) else name
        info = abi.revert_info_for(sol_name, args)
        err = VaultError(info.code) if info.code is not None else None
        raise ContractRevertError(info.message, revert=info, error=err, error_code=abi.error_code_of(info))

    def _router_fail(self, inner_name: str, args: list | tuple | None = None) -> None:
        self._fail("RouterError", [abi.encode_revert(inner_name, args)])

    # ------------------------------------------------------------------ internals: ERC-20 / native

    def _bal(self, st: _State, holder: str, token: str) -> int:
        return st.wallets.get((holder, token), 0)

    def _erc20_transfer(self, st: _State, token: str, frm: str, to: str, amount: int, events: list[Event]) -> None:
        if int(to, 16) == 0:
            self._fail("ERC20InvalidReceiver", [to])
        have = self._bal(st, frm, token)
        if have < amount:
            self._fail("ERC20InsufficientBalance", [frm, have, amount])
        st.wallets[(frm, token)] = have - amount
        st.wallets[(to, token)] = self._bal(st, to, token) + amount
        events.append((token, "Transfer", {"from": frm, "to": to, "value": amount}))

    def _erc20_transfer_from(self, st: _State, token: str, spender: str, frm: str, to: str, amount: int,
                             events: list[Event]) -> None:
        key = (frm, token, spender)
        allowed = st.allowances.get(key, 0)
        if allowed < amount:
            self._fail("ERC20InsufficientAllowance", [spender, allowed, amount])
        if allowed != UINT256_MAX:
            st.allowances[key] = allowed - amount
        self._erc20_transfer(st, token, frm, to, amount, events)

    def _safe_transfer_from(self, st: _State, token: str, frm: str, amount: int, events: list[Event]) -> None:
        """Vault ``safeTransferFrom(msg.sender, this, amount)``: missing allowance -> ``TransferFailed`` (29, 01-spec §5)."""
        allowed = st.allowances.get((frm, token, self.vault_address), 0)
        if allowed < amount:
            self._fail("SafeERC20FailedOperation", [token])
        self._erc20_transfer_from(st, token, self.vault_address, frm, self.vault_address, amount, events)

    def _try_transfer(self, st: _State, token: str, to: str, amount: int, events: list[Event]) -> bool:
        if amount == 0:
            return True
        if (token, to) in st.blocked or self._bal(st, self.vault_address, token) < amount:
            return False
        self._erc20_transfer(st, token, self.vault_address, to, amount, events)
        return True

    def _do_approve(self, st: _State, sender: str, spender: str, value: int, events: list[Event]) -> bool:
        # msg.sender approves `spender` on the token being called (`self._current_token`)
        token = self._current_token
        st.allowances[(sender, token, spender)] = int(value)
        events.append((token, "Approval", {"owner": sender, "spender": spender, "value": int(value)}))
        return True

    def _do_transfer(self, st: _State, sender: str, to: str, value: int, events: list[Event]) -> bool:
        self._erc20_transfer(st, self._current_token, sender, to, int(value), events)
        return True

    def _do_transfer_from(self, st: _State, sender: str, frm: str, to: str, value: int, events: list[Event]) -> bool:
        self._erc20_transfer_from(st, self._current_token, sender, frm, to, int(value), events)
        return True

    def _do_mint(self, st: _State, sender: str, to: str, amount: int, events: list[Event]) -> None:
        if sender not in st.minters:
            self._fail("AccessControlUnauthorizedAccount", [sender, MINTER_ROLE])
        token = self._current_token
        st.wallets[(to, token)] = self._bal(st, to, token) + int(amount)
        events.append((token, "Transfer", {"from": ZERO_ADDRESS, "to": to, "value": int(amount)}))

    def _do_native(self, st: _State, sender: str, to: str, value: int) -> None:
        have = st.native.get(sender, 0)
        if have < value:
            raise ChainError("insufficient funds for transfer", details={"balance": have, "value": value})
        st.native[sender] = have - value
        st.native[to] = st.native.get(to, 0) + value

    # ------------------------------------------------------------------ internals: vault helpers

    def _agreement(self, st: _State, agreement_id: int) -> _Agreement:
        ag = st.agreements.get(int(agreement_id))
        if ag is None or ag.status == STATUS_NONE:
            self._fail(VaultError.NotFound)
        return ag  # type: ignore[return-value]

    def _token_info(self, st: _State, token: str) -> _Token:
        return st.tokens.get(token) or _Token(symbol="?", decimals=18)

    def _only_owner(self, st: _State, sender: str) -> None:
        if sender != st.owner:
            self._fail("OwnableUnauthorizedAccount", [sender])

    def _validate_terms(self, st: _State, t: Terms) -> None:
        if int(t.customer, 16) == 0 or int(t.trader, 16) == 0:
            self._fail(VaultError.ZeroAddress)
        if t.principal == 0:
            self._fail(VaultError.InvalidTerms)
        if t.duration_seconds < abi.MIN_DURATION or t.duration_seconds > abi.MAX_DURATION:
            self._fail(VaultError.InvalidTerms)
        if t.commission_bps > abi.MAX_COMMISSION_BPS:
            self._fail(VaultError.InvalidTerms)
        if t.max_drawdown_bps < abi.MIN_DRAWDOWN_BPS or t.max_drawdown_bps > abi.BPS_DENOM:
            self._fail(VaultError.InvalidTerms)
        if t.customer == t.trader:
            self._fail(VaultError.InvalidTerms)
        info = self._token_info(st, t.base_token)
        if not (info.allowed and info.is_base):
            self._fail(VaultError.TokenNotAllowed)

    def _new_agreement(self, st: _State, ag_id: int, terms: Terms, status: int, proposer: str) -> _Agreement:
        ag = _Agreement(
            id=ag_id, terms=terms, status=status, proposer=proposer, created_at=self.now,
            platform_fee_bps=st.config.platform_fee_bps, tokens=[terms.base_token], last_value=terms.principal,
        )
        st.agreements[ag_id] = ag
        return ag

    def _activate(self, ag: _Agreement, events: list[Event]) -> None:
        ag.start_time = self.now
        ag.end_time = self.now + ag.terms.duration_seconds
        ag.status = STATUS_ACTIVE
        events.append((self.vault_address, "Activated", {"id": ag.id, "startTime": ag.start_time, "endTime": ag.end_time}))

    def _draw_reservation(self, st: _State, res_id: int, ag: _Agreement, events: list[Event]) -> None:
        res = st.reservations.get(int(res_id))
        if res is None or res.status == RES_NONE:
            self._fail(VaultError.ReservationNotFound)
        assert res is not None
        if res.status != RES_OPEN:
            self._fail(VaultError.ReservationClosed)
        if res.customer != ag.terms.customer or res.token != ag.terms.base_token:
            self._fail(VaultError.ReservationMismatch)
        principal = ag.terms.principal
        if res.amount < principal:
            self._fail(VaultError.ReservationInsufficient)
        remaining = res.amount - principal
        res.amount = remaining
        if remaining == 0:
            res.status = RES_CONSUMED
        st.balances[(ag.id, ag.terms.base_token)] = principal
        events.append((self.vault_address, "ReservationDrawn",
                       {"id": res.id, "agreementId": ag.id, "amount": principal, "remaining": remaining}))

    def _quote_raw(self, st: _State, token: str, base: str, amount: int) -> int:
        """``_quote``: 0 when no route / zero amount (router revert is swallowed on-chain)."""
        if amount == 0:
            return 0
        price = st.prices.get((token, base))
        if price is None:
            return 0
        return int(amount * price.numerator // price.denominator)

    def _valuation(self, st: _State, ag: _Agreement, probe: str) -> tuple[int, int]:
        base = ag.terms.base_token
        total = st.balances.get((ag.id, base), 0)
        probe_quote = 0
        for token in ag.tokens[1:]:
            bal = st.balances.get((ag.id, token), 0)
            if bal == 0:
                continue
            q = self._quote_raw(st, token, base, bal)
            if token == probe:
                probe_quote = q
            total += q
        return total, probe_quote

    def _swap_via_router(self, st: _State, token_in: str, token_out: str, amount_in: int, min_out: int,
                         deadline: int, events: list[Event]) -> int:
        """MockRouter.swapExactTokensForTokens as seen from the vault (errors wrapped in ``RouterError``)."""
        router = st.config.router
        if self.now > deadline:
            self._router_fail("DeadlineExpired")
        if amount_in == 0:
            self._router_fail("InvalidAmount")
        price = st.prices.get((token_in, token_out))
        if price is None:
            self._router_fail("NoPrice", [token_in, token_out])
        assert price is not None
        out = int(amount_in * price.numerator // price.denominator)
        if st.strict_router and out < min_out:
            self._router_fail("InsufficientOutput", [out, min_out])
        have_in = self._bal(st, self.vault_address, token_in)
        if have_in < amount_in:
            self._router_fail("ERC20InsufficientBalance", [self.vault_address, have_in, amount_in])
        have_out = self._bal(st, router, token_out)
        if have_out < out:
            self._router_fail("ERC20InsufficientBalance", [router, have_out, out])
        self._erc20_transfer(st, token_in, self.vault_address, router, amount_in, events)
        self._erc20_transfer(st, token_out, router, self.vault_address, out, events)
        if out == 0 or out < min_out:
            self._fail(VaultError.SlippageExceeded)
        return out

    @staticmethod
    def _remove_token(tokens: list[str], token: str) -> None:
        for i in range(1, len(tokens)):
            if tokens[i] == token:
                del tokens[i]
                return

    # ------------------------------------------------------------------ vault state machine (01-spec §4)

    def _do_set_token(self, st: _State, sender: str, token: str, allowed: bool, is_base: bool, ev: list[Event]) -> None:
        self._only_owner(st, sender)
        if int(token, 16) == 0:
            self._fail(VaultError.ZeroAddress)
        if allowed and token not in st.tokens:
            self._fail(VaultError.InvalidToken)
        base = bool(allowed and is_base)
        tok = st.tokens.get(token)
        if tok is None:
            tok = _Token(symbol=short(token), decimals=18)
            st.tokens[token] = tok
        tok.allowed, tok.is_base = bool(allowed), base
        ev.append((self.vault_address, "TokenSet", {"token": token, "allowed": bool(allowed), "isBase": base}))

    def _config_changed(self, st: _State, key: str, ev: list[Event]) -> None:
        c = st.config
        ev.append((self.vault_address, "ConfigChanged", {
            "key": abi.config_key_bytes(key), "router": c.router, "platformFeeBps": c.platform_fee_bps,
            "feeRecipient": c.fee_recipient, "paused": c.paused, "settleSlippageBps": c.settle_slippage_bps,
        }))

    def _do_propose_router(self, st: _State, sender: str, new_router: str, ev: list[Event]) -> None:
        self._only_owner(st, sender)
        if int(new_router, 16) == 0:
            self._fail(VaultError.ZeroAddress)
        activation = self.now + st.config.router_delay
        st.config.pending_router = new_router
        st.config.router_activation_time = activation
        ev.append((self.vault_address, "RouterChangeProposed", {"newRouter": new_router, "activationTime": activation}))

    def _do_apply_router(self, st: _State, sender: str, ev: list[Event]) -> None:
        self._only_owner(st, sender)
        pending = st.config.pending_router
        if int(pending, 16) == 0:
            self._fail(VaultError.NoPendingRouterChange)
        if self.now < st.config.router_activation_time:
            self._fail("RouterChangeNotReady", [st.config.router_activation_time])
        st.config.router = pending
        st.config.pending_router = ZERO_ADDRESS
        st.config.router_activation_time = 0
        self._config_changed(st, "router", ev)

    def _do_cancel_router(self, st: _State, sender: str, ev: list[Event]) -> None:
        self._only_owner(st, sender)
        pending = st.config.pending_router
        if int(pending, 16) == 0:
            self._fail(VaultError.NoPendingRouterChange)
        st.config.pending_router = ZERO_ADDRESS
        st.config.router_activation_time = 0
        ev.append((self.vault_address, "RouterChangeCancelled", {"cancelledRouter": pending}))

    def _do_set_fees(self, st: _State, sender: str, bps: int, recipient: str, ev: list[Event]) -> None:
        self._only_owner(st, sender)
        if bps > abi.MAX_PLATFORM_FEE_BPS:
            self._fail(VaultError.InvalidTerms)
        if int(recipient, 16) == 0:
            self._fail(VaultError.ZeroAddress)
        st.config.platform_fee_bps = int(bps)
        st.config.fee_recipient = recipient
        self._config_changed(st, "fees", ev)

    def _do_set_paused(self, st: _State, sender: str, paused: bool, ev: list[Event]) -> None:
        self._only_owner(st, sender)
        st.config.paused = bool(paused)
        self._config_changed(st, "paused", ev)

    def _do_set_settle_slippage(self, st: _State, sender: str, bps: int, ev: list[Event]) -> None:
        self._only_owner(st, sender)
        if bps > abi.MAX_SETTLE_SLIPPAGE_BPS:
            self._fail(VaultError.InvalidTerms)
        st.config.settle_slippage_bps = int(bps)
        self._config_changed(st, "slippage", ev)

    def _do_reserve(self, st: _State, sender: str, token: str, amount: int, listing_ref: bytes, ev: list[Event]) -> int:
        if st.config.paused:
            self._fail(VaultError.Paused)
        if amount == 0:
            self._fail(VaultError.ZeroAmount)
        info = self._token_info(st, token)
        if not (info.allowed and info.is_base):
            self._fail(VaultError.TokenNotAllowed)
        res_id = st.next_reservation_id
        st.next_reservation_id += 1
        st.reservations[res_id] = _Reservation(
            id=res_id, customer=sender, token=token, amount=amount, original=amount, status=RES_OPEN,
            created_at=self.now, listing_ref=bytes(listing_ref),
        )
        ev.append((self.vault_address, "Reserved",
                   {"id": res_id, "customer": sender, "token": token, "amount": amount, "listingRef": bytes(listing_ref)}))
        self._safe_transfer_from(st, token, sender, amount, ev)
        return res_id

    def _open_reservation_of(self, st: _State, sender: str, res_id: int) -> _Reservation:
        res = st.reservations.get(int(res_id))
        if res is None or res.status == RES_NONE:
            self._fail(VaultError.ReservationNotFound)
        assert res is not None
        if sender != res.customer:
            self._fail(VaultError.Unauthorized)
        if res.status != RES_OPEN:
            self._fail(VaultError.ReservationClosed)
        return res

    def _release(self, st: _State, res: _Reservation, amount: int, ev: list[Event]) -> int:
        if amount == 0:
            self._fail(VaultError.ZeroAmount)
        if amount > res.amount:
            self._fail(VaultError.ReservationInsufficient)
        remaining = res.amount - amount
        res.amount = remaining
        if remaining == 0:
            res.status = RES_RELEASED
        ev.append((self.vault_address, "Released",
                   {"id": res.id, "customer": res.customer, "token": res.token, "amount": amount, "remaining": remaining}))
        self._erc20_transfer(st, res.token, self.vault_address, res.customer, amount, ev)
        return amount

    def _do_release(self, st: _State, sender: str, res_id: int, amount: int, ev: list[Event]) -> int:
        res = self._open_reservation_of(st, sender, res_id)
        return self._release(st, res, int(amount), ev)

    def _do_release_all(self, st: _State, sender: str, res_id: int, ev: list[Event]) -> int:
        res = self._open_reservation_of(st, sender, res_id)
        return self._release(st, res, res.amount, ev)

    def _do_propose(self, st: _State, sender: str, terms: Terms, ev: list[Event]) -> int:
        if st.config.paused:
            self._fail(VaultError.Paused)
        if sender != terms.trader:
            self._fail(VaultError.Unauthorized)
        self._validate_terms(st, terms)
        ag_id = st.next_id
        st.next_id += 1
        self._new_agreement(st, ag_id, terms, STATUS_PROPOSED, sender)
        ev.append((self.vault_address, "Proposed", {
            "id": ag_id, "trader": terms.trader, "customer": terms.customer, "principal": terms.principal,
            "baseToken": terms.base_token,
        }))
        return ag_id

    def _do_open(self, st: _State, sender: str, terms: Terms, ev: list[Event]) -> int:
        if st.config.paused:
            self._fail(VaultError.Paused)
        if sender != terms.customer:
            self._fail(VaultError.Unauthorized)
        self._validate_terms(st, terms)
        ag_id = st.next_id
        st.next_id += 1
        self._new_agreement(st, ag_id, terms, STATUS_FUNDED, sender)
        st.balances[(ag_id, terms.base_token)] = terms.principal
        ev.append((self.vault_address, "Opened", {
            "id": ag_id, "trader": terms.trader, "customer": terms.customer, "principal": terms.principal,
            "baseToken": terms.base_token,
        }))
        self._safe_transfer_from(st, terms.base_token, sender, terms.principal, ev)
        return ag_id

    def _do_open_reserved(self, st: _State, sender: str, terms: Terms, res_id: int, ev: list[Event]) -> int:
        if st.config.paused:
            self._fail(VaultError.Paused)
        if sender != terms.customer:
            self._fail(VaultError.Unauthorized)
        self._validate_terms(st, terms)
        ag_id = st.next_id
        st.next_id += 1
        ag = self._new_agreement(st, ag_id, terms, STATUS_FUNDED, sender)
        self._draw_reservation(st, res_id, ag, ev)
        ev.append((self.vault_address, "Opened", {
            "id": ag_id, "trader": terms.trader, "customer": terms.customer, "principal": terms.principal,
            "baseToken": terms.base_token,
        }))
        return ag_id

    def _fund_checks(self, st: _State, sender: str, ag_id: int) -> _Agreement:
        ag = self._agreement(st, ag_id)
        if sender != ag.terms.customer:
            self._fail(VaultError.Unauthorized)
        if st.config.paused:
            self._fail(VaultError.Paused)
        if ag.status != STATUS_PROPOSED:
            self._fail(VaultError.WrongStatus)
        info = self._token_info(st, ag.terms.base_token)
        if not (info.allowed and info.is_base):
            self._fail(VaultError.TokenNotAllowed)
        return ag

    def _do_fund(self, st: _State, sender: str, ag_id: int, ev: list[Event]) -> None:
        ag = self._fund_checks(st, sender, ag_id)
        st.balances[(ag.id, ag.terms.base_token)] = ag.terms.principal
        self._activate(ag, ev)
        self._safe_transfer_from(st, ag.terms.base_token, sender, ag.terms.principal, ev)

    def _do_fund_reserved(self, st: _State, sender: str, ag_id: int, res_id: int, ev: list[Event]) -> None:
        ag = self._fund_checks(st, sender, ag_id)
        self._draw_reservation(st, res_id, ag, ev)
        self._activate(ag, ev)

    def _do_accept(self, st: _State, sender: str, ag_id: int, ev: list[Event]) -> None:
        ag = self._agreement(st, ag_id)
        if sender != ag.terms.trader:
            self._fail(VaultError.Unauthorized)
        if st.config.paused:
            self._fail(VaultError.Paused)
        if ag.status != STATUS_FUNDED:
            self._fail(VaultError.WrongStatus)
        self._activate(ag, ev)

    def _do_cancel(self, st: _State, sender: str, ag_id: int, ev: list[Event]) -> None:
        ag = self._agreement(st, ag_id)
        if ag.status == STATUS_PROPOSED:
            if sender != ag.proposer:
                self._fail(VaultError.NotParty)
        elif ag.status == STATUS_FUNDED:
            if sender not in (ag.terms.customer, ag.terms.trader):
                self._fail(VaultError.NotParty)
        else:
            self._fail(VaultError.WrongStatus)
        refunded = 0
        base = ag.terms.base_token
        if ag.status == STATUS_FUNDED:
            refunded = st.balances.pop((ag.id, base), 0)
        ag.status = STATUS_CANCELLED
        ev.append((self.vault_address, "Cancelled", {"id": ag.id, "refunded": refunded}))
        if refunded > 0:
            self._erc20_transfer(st, base, self.vault_address, ag.terms.customer, refunded, ev)

    def _do_trade(self, st: _State, sender: str, ag_id: int, token_in: str, token_out: str, amount_in: int,
                  min_out: int, deadline: int, ev: list[Event]) -> int:
        ag = self._agreement(st, ag_id)
        if sender != ag.terms.trader:
            self._fail(VaultError.Unauthorized)
        if st.config.paused:
            self._fail(VaultError.Paused)
        if ag.status != STATUS_ACTIVE:
            self._fail(VaultError.WrongStatus)
        if self.now >= ag.end_time or deadline < self.now:
            self._fail(VaultError.Expired)
        if amount_in == 0 or min_out == 0:
            self._fail(VaultError.ZeroAmount)
        if token_in == token_out or not self._token_info(st, token_in).allowed or not self._token_info(st, token_out).allowed:
            self._fail(VaultError.TokenNotAllowed)
        bal_in = st.balances.get((ag.id, token_in), 0)
        if bal_in < amount_in:
            self._fail(VaultError.InsufficientBalance)
        # effects (steps 9–10)
        if token_out not in ag.tokens:
            if len(ag.tokens) >= MAX_TOKENS:
                self._fail(VaultError.TooManyTokens)
            ag.tokens.append(token_out)
        new_in = bal_in - amount_in
        if new_in == 0 and token_in != ag.terms.base_token:
            st.balances.pop((ag.id, token_in), None)
            self._remove_token(ag.tokens, token_in)
        else:
            st.balances[(ag.id, token_in)] = new_in
        # interaction (step 11)
        before = self._bal(st, self.vault_address, token_out)
        reported = self._swap_via_router(st, token_in, token_out, amount_in, min_out, deadline, ev)
        received = max(0, self._bal(st, self.vault_address, token_out) - before)
        amount_out = min(reported, received)
        if amount_out < min_out:
            self._fail(VaultError.SlippageExceeded)
        # steps 12–15
        st.balances[(ag.id, token_out)] = st.balances.get((ag.id, token_out), 0) + amount_out
        value_after, out_quote = self._valuation(st, ag, token_out)
        if token_out != ag.terms.base_token and out_quote == 0:
            self._fail(VaultError.TokenNotAllowed)
        if value_after < drawdown_floor(ag.terms.principal, ag.terms.max_drawdown_bps):
            self._fail(VaultError.DrawdownBreached)
        ag.last_value = value_after
        ev.append((self.vault_address, "Traded", {
            "id": ag.id, "trader": ag.terms.trader, "tokenIn": token_in, "tokenOut": token_out,
            "amountIn": amount_in, "amountOut": amount_out, "valueAfter": value_after,
        }))
        return amount_out

    def _do_settle(self, st: _State, sender: str, ag_id: int, min_outs: list[int], ev: list[Event]) -> None:
        ag = self._agreement(st, ag_id)
        if ag.status != STATUS_ACTIVE:
            self._fail(VaultError.WrongStatus)
        non_base = len(ag.tokens) - 1
        is_customer = sender == ag.terms.customer
        is_trader = sender == ag.terms.trader
        is_party = is_customer or is_trader
        if is_party:
            if len(min_outs) != non_base:
                self._fail(VaultError.InvalidAmount)
        else:
            if self.now < ag.end_time:
                self._fail(VaultError.NotExpired)
            is_admin = sender == st.owner
            if not is_admin and self.now < ag.end_time + KEEPER_GRACE:
                self._fail(VaultError.NotExpired)
            if len(min_outs) not in (0, non_base):
                self._fail(VaultError.InvalidAmount)
        use_supplied = len(min_outs) != 0
        slip = st.config.settle_slippage_bps

        # effects first
        ag.status = STATUS_SETTLED
        ag.settled_at = self.now
        base = ag.terms.base_token
        held = list(ag.tokens)
        ag.tokens = [base]
        base_before = self._bal(st, self.vault_address, base)
        reported_total = 0
        for i in range(1, len(held)):
            token = held[i]
            supplied = int(min_outs[i - 1]) if use_supplied else 0
            bal = st.balances.get((ag.id, token), 0)
            if bal == 0:
                continue
            st.balances.pop((ag.id, token), None)  # zeroed BEFORE the swap
            q = self._quote_raw(st, token, base, bal)
            if q == 0:
                delivered = self._try_transfer(st, token, ag.terms.customer, bal, ev)
                if not delivered:
                    st.balances[(ag.id, token)] = bal
                    ag.tokens.append(token)
                ev.append((self.vault_address, "Unliquidated",
                           {"id": ag.id, "token": token, "amount": bal, "delivered": delivered}))
                continue
            min_out = supplied if is_party else max(supplied, min_out_for_slippage(q, slip))
            reported_total += self._swap_via_router(st, token, base, bal, min_out, self.now, ev)
        received = max(0, self._bal(st, self.vault_address, base) - base_before)
        liquidated = min(reported_total, received)
        final_value = st.balances.get((ag.id, base), 0) + liquidated

        if is_trader and final_value < drawdown_floor(ag.terms.principal, ag.terms.max_drawdown_bps):
            self._fail(VaultError.DrawdownBreached)
        if not is_party and final_value < min_out_for_slippage(ag.last_value, slip):
            self._fail(VaultError.SlippageExceeded)

        split = settle_math(final_value, ag.terms.principal, ag.terms.commission_bps, ag.platform_fee_bps)
        trader_fee, platform_fee, customer_payout = split.trader_fee, split.platform_fee, split.customer_payout
        st.balances.pop((ag.id, base), None)
        ag.final_value = final_value
        ag.last_value = final_value
        if not self._try_transfer(st, base, ag.terms.trader, trader_fee, ev):
            customer_payout += trader_fee
            trader_fee = 0
        if not self._try_transfer(st, base, st.config.fee_recipient, platform_fee, ev):
            customer_payout += platform_fee
            platform_fee = 0
        ag.trader_fee, ag.platform_fee, ag.customer_payout = trader_fee, platform_fee, customer_payout
        if customer_payout > 0:
            self._erc20_transfer(st, base, self.vault_address, ag.terms.customer, customer_payout, ev)
        ev.append((self.vault_address, "Settled", {
            "id": ag.id, "finalValue": final_value, "profit": split.profit, "traderFee": trader_fee,
            "platformFee": platform_fee, "customerPayout": customer_payout, "by": sender,
        }))

    def _do_claim(self, st: _State, sender: str, ag_id: int, token: str, ev: list[Event]) -> int:
        ag = self._agreement(st, ag_id)
        if sender != ag.terms.customer:
            self._fail(VaultError.Unauthorized)
        if ag.status != STATUS_SETTLED:
            self._fail(VaultError.WrongStatus)
        amount = st.balances.get((ag.id, token), 0)
        if amount == 0:
            self._fail(VaultError.InsufficientBalance)
        st.balances.pop((ag.id, token), None)
        self._remove_token(ag.tokens, token)
        ev.append((self.vault_address, "Claimed", {"id": ag.id, "token": token, "amount": amount}))
        self._erc20_transfer(st, token, self.vault_address, ag.terms.customer, amount, ev)
        return amount

    # ------------------------------------------------------------------ dispatch

    def _vault_handlers(self) -> dict[str, Callable[[_State, str, dict[str, Any], list[Event]], Any]]:
        return {
            "setToken": lambda st, s, a, ev: self._do_set_token(st, s, a["token"], a["allowed"], a["isBase"], ev),
            "proposeRouterChange": lambda st, s, a, ev: self._do_propose_router(st, s, a["newRouter"], ev),
            "applyRouterChange": lambda st, s, a, ev: self._do_apply_router(st, s, ev),
            "cancelRouterChange": lambda st, s, a, ev: self._do_cancel_router(st, s, ev),
            "setFees": lambda st, s, a, ev: self._do_set_fees(st, s, a["platformFeeBps"], a["feeRecipient"], ev),
            "setPaused": lambda st, s, a, ev: self._do_set_paused(st, s, a["paused_"], ev),
            "setSettleSlippage": lambda st, s, a, ev: self._do_set_settle_slippage(st, s, a["bps"], ev),
            "reserve": lambda st, s, a, ev: self._do_reserve(st, s, a["token"], a["amount"], a["listingRef"], ev),
            "release": lambda st, s, a, ev: self._do_release(st, s, a["id"], a["amount"], ev),
            "releaseAll": lambda st, s, a, ev: self._do_release_all(st, s, a["id"], ev),
            "propose": lambda st, s, a, ev: self._do_propose(st, s, _terms_from_abi(a["terms"]), ev),
            "open": lambda st, s, a, ev: self._do_open(st, s, _terms_from_abi(a["terms"]), ev),
            "openReserved": lambda st, s, a, ev: self._do_open_reserved(
                st, s, _terms_from_abi(a["terms"]), a["reservationId"], ev),
            "fund": lambda st, s, a, ev: self._do_fund(st, s, a["id"], ev),
            "fundReserved": lambda st, s, a, ev: self._do_fund_reserved(st, s, a["id"], a["reservationId"], ev),
            "accept": lambda st, s, a, ev: self._do_accept(st, s, a["id"], ev),
            "cancel": lambda st, s, a, ev: self._do_cancel(st, s, a["id"], ev),
            "trade": lambda st, s, a, ev: self._do_trade(
                st, s, a["id"], a["tokenIn"], a["tokenOut"], a["amountIn"], a["minOut"], a["deadline"], ev),
            "settle": lambda st, s, a, ev: self._do_settle(st, s, a["id"], [int(m) for m in a["minOuts"]], ev),
            "claim": lambda st, s, a, ev: self._do_claim(st, s, a["id"], a["token"], ev),
        }

    def _dispatch(self, st: _State, sender: str, to: str, data: str, value: int) -> tuple[Any, list[Event], str]:
        """Apply one transaction to ``st``; returns ``(result, events, action)``. Raises ``ContractRevertError``."""
        events: list[Event] = []
        if not data or data == "0x":
            self._do_native(st, sender, to, int(value))
            return None, events, "native"
        if to == self.vault_address:
            fn, args = self._decode("TraderVault", data)
            handler = self._vault_handlers().get(fn)
            if handler is None:
                self._fail("Error", [f"unsupported vault function {fn}"])
            assert handler is not None
            return handler(st, sender, args, events), events, fn
        if to in st.tokens:
            fn, args = self._decode("TestToken", data)
            self._current_token = to
            if fn == "approve":
                return self._do_approve(st, sender, args["spender"], args["value"], events), events, fn
            if fn == "transfer":
                return self._do_transfer(st, sender, args["to"], args["value"], events), events, fn
            if fn == "transferFrom":
                return self._do_transfer_from(st, sender, args["from"], args["to"], args["value"], events), events, fn
            if fn == "mint":
                return self._do_mint(st, sender, args["to"], args["amount"], events), events, fn
            self._fail("Error", [f"unsupported token function {fn}"])
        self._fail("Error", [f"no contract at {to}"])
        raise AssertionError("unreachable")

    def _decode(self, abi_name: str, data: str) -> tuple[str, dict[str, Any]]:
        try:
            return abi.decode_call(abi_name, data)
        except Exception:  # noqa: BLE001 - unknown selector => the contract's fallback reverts
            info = RevertInfo(selector=data[:10] if len(data) >= 10 else None, name=None, code=None,
                              args={"data": data}, message=f"{abi.GENERIC_REVERT_MESSAGE} (bilinmeyen fonksiyon)")
            raise ContractRevertError(info.message, revert=info, error_code="reverted") from None

    def _dry_run(self, sender: str, to: str, data: str, value: int, pre_steps: list[PreStep] | None = None) -> Any:
        """Simulate ``pre_steps`` (wallet approvals) then the call on a copy; raises like ``estimateGas`` would."""
        working = copy.deepcopy(self._state)
        for step in pre_steps or []:
            self._dispatch(working, sender, step.to, step.data, step.value)
        result, _, _ = self._dispatch(working, sender, normalize(to), data, int(value))
        return result

    # ------------------------------------------------------------------ wallet stand-in: send / mine

    def _enqueue(self, sender: str, to: str, data: str, value: int, gas: int | None) -> str:
        sender_l, to_l = normalize(sender), normalize(to)
        nonce = self._nonces.get(sender_l, 0)
        self._nonces[sender_l] = nonce + 1
        tx_hash = "0x" + keccak(f"{sender_l}:{nonce}:{to_l}:{data}:{value}:{self._reorg_salt}".encode()).hex()
        self.queue.append(_QueuedTx(tx_hash, sender_l, to_l, data or "0x", int(value), nonce, gas))
        self.txs[tx_hash] = TxInfo(
            tx_hash=tx_hash, from_address=sender_l, to_address=to_l, input=data or "0x", value=int(value),
            nonce=nonce, gas=gas or 0, block_number=None, block_hash=None,
        )
        return tx_hash

    def send(self, sender: str, to: str, data: str = "0x", value: int = 0, *, gas: int | None = None) -> str:
        """The wallet's ``eth_sendTransaction``: queue (and, with ``auto_mine``, mine) one transaction; returns its hash."""
        tx_hash = self._enqueue(sender, to, data, value, gas)
        if self.auto_mine:
            self.mine()
        return tx_hash

    def wallet_send(self, unsigned: UnsignedTx, sender: str | None = None) -> str:
        """Send ``pre_steps`` in order, then the main transaction; returns the main tx hash."""
        frm = normalize(sender or unsigned.from_address)
        for step in unsigned.pre_steps:
            self.send(frm, step.to, step.data, step.value, gas=step.gas)
        return self.send(frm, unsigned.to, unsigned.data, unsigned.value, gas=unsigned.gas)

    def mine(self, tx: UnsignedTx | PreStep | None = None, sender: str | None = None) -> TxReceiptResult | None:
        """Mine one block with the queued transactions (plus ``tx`` when given). Returns ``tx``'s receipt, else the
        last receipt in the block (None for an empty block)."""
        target: str | None = None
        if tx is not None:
            frm = sender or getattr(tx, "from_address", None)
            if frm is None:
                raise ValueError("sender is required for a PreStep")
            target = self._enqueue(frm, tx.to, tx.data, tx.value, tx.gas)
        batch, self.queue = self.queue, []
        self.now += BLOCK_INTERVAL
        block = self._append_block(self.blocks[-1].number + 1)
        receipts = [self._apply(q, block, idx) for idx, q in enumerate(batch)]
        self._snapshot()
        if target is not None:
            return self.receipts[target]
        return receipts[-1] if receipts else None

    def _apply(self, q: _QueuedTx, block: BlockInfo, tx_index: int) -> TxReceiptResult:
        working = copy.deepcopy(self._state)
        revert: RevertInfo | None = None
        events: list[Event] = []
        action = "native"
        try:
            _, events, action = self._dispatch(working, q.sender, q.to, q.data, q.value)
        except ContractRevertError as e:
            revert, events = e.revert, []
        except ChainError as e:
            revert, events = RevertInfo(selector=None, name=None, code=None, args=dict(e.details), message=str(e)), []
        else:
            self._state = working
        gas_used = GAS_TABLE.get(action, 100_000)
        raw_logs = [
            abi.encode_log(name, args, address=addr, block_number=block.number, block_hash=block.hash, log_index=i,
                           tx_hash=q.tx_hash, tx_index=tx_index)
            for i, (addr, name, args) in enumerate(events)
        ]
        records: list[EventRecord] = []
        for raw in raw_logs:
            rec = abi.decode_log(raw)
            if rec is None:  # pragma: no cover - every fake event is in EVENT_TOPICS
                continue
            rec = replace(rec, block_timestamp=block.timestamp)
            records.append(rec)
            self._raw_logs[(rec.tx_hash, rec.log_index)] = raw
        self.logs.extend(records)
        receipt = TxReceiptResult(
            tx_hash=q.tx_hash,
            status=0 if revert is not None else 1,
            block_number=block.number,
            block_hash=block.hash,
            block_timestamp=block.timestamp,
            from_address=q.sender,
            to_address=q.to,
            input=q.data,
            value=q.value,
            gas_used=gas_used,
            gas_limit=q.gas or gas_used,
            effective_gas_price=GAS_PRICE,
            events=[r for r in records if r.address == self.vault_address],
            raw_log_count=len(raw_logs),
            confirmations=1,
            revert=revert,
        )
        self.receipts[q.tx_hash] = receipt
        self.txs[q.tx_hash] = replace(self.txs[q.tx_hash], block_number=block.number, block_hash=block.hash,
                                      gas=receipt.gas_limit)
        return receipt

    # ------------------------------------------------------------------ Protocol: network

    async def chain_id(self) -> int:
        return self.chain_id_expected

    async def latest_block(self) -> int:
        return self.blocks[-1].number

    async def get_block(self, number: int | str) -> BlockInfo:
        if number == "latest":
            return self.blocks[-1]
        for b in reversed(self.blocks):
            if b.number == int(number):
                return b
        raise ChainError(f"block not found: {number}", details={"block": str(number)})

    async def close(self) -> None:
        return None

    # ------------------------------------------------------------------ Protocol: vault reads

    async def read_agreement(self, agreement_id: int) -> AgreementView:
        return self._agreement(self._state, agreement_id).view()

    async def read_balances(self, agreement_id: int) -> list[tuple[str, int]]:
        ag = self._agreement(self._state, agreement_id)
        return [(t, self._state.balances.get((ag.id, t), 0)) for t in ag.tokens]

    async def read_value_in_base(self, agreement_id: int) -> int:
        ag = self._agreement(self._state, agreement_id)
        return self._valuation(self._state, ag, ag.terms.base_token)[0]

    async def read_config(self) -> ConfigView:
        c = self._state.config
        return ConfigView(
            owner=self._state.owner, router=c.router, fee_recipient=c.fee_recipient,
            platform_fee_bps=c.platform_fee_bps, settle_slippage_bps=c.settle_slippage_bps, paused=c.paused,
            pending_router=None if int(c.pending_router, 16) == 0 else c.pending_router,
            router_activation_time=c.router_activation_time, router_delay=c.router_delay,
        )

    async def read_reservation(self, reservation_id: int) -> ReservationView:
        res = self._state.reservations.get(int(reservation_id))
        if res is None or res.status == RES_NONE:
            self._fail(VaultError.ReservationNotFound)
        assert res is not None
        return res.view()

    async def preview_settle(self, agreement_id: int) -> SettlePreview | None:
        st = self._state
        ag = self._agreement(st, agreement_id)
        base = ag.terms.base_token
        tokens = list(ag.tokens[1:])
        balances = [st.balances.get((ag.id, t), 0) for t in tokens]
        quotes = [self._quote_raw(st, t, base, b) for t, b in zip(tokens, balances, strict=True)]
        return SettlePreview(
            tokens=tokens, balances=balances, quotes=quotes,
            estimated_final_value=st.balances.get((ag.id, base), 0) + sum(quotes),
            drawdown_floor=drawdown_floor(ag.terms.principal, ag.terms.max_drawdown_bps),
            keeper_floor=min_out_for_slippage(ag.last_value, st.config.settle_slippage_bps),
        )

    async def is_token_allowed(self, token: str) -> TokenInfoView:
        tok = self._state.tokens.get(normalize(token))
        return TokenInfoView(allowed=tok.allowed, is_base=tok.is_base) if tok else TokenInfoView()

    async def next_id(self) -> int:
        return self._state.next_id

    async def next_reservation_id(self) -> int:
        return self._state.next_reservation_id

    # ------------------------------------------------------------------ Protocol: router / token reads

    async def quote(self, path: list[str], amount_raw: int) -> RouterQuote:
        path_l = [normalize(p) for p in path]
        if int(amount_raw) == 0:
            raise ChainError("router quote failed: InvalidAmount", code="quote_failed",
                             details={"error_code": "router:InvalidAmount", "path": path_l})
        if len(path_l) < 2:
            raise ChainError("router quote failed: InvalidPath", code="quote_failed",
                             details={"error_code": "router:InvalidPath", "path": path_l})
        amounts = [int(amount_raw)]
        for a, b in zip(path_l, path_l[1:], strict=False):
            price = self._state.prices.get((a, b))
            if price is None:
                raise ChainError(f"router quote failed: NoPrice {short(a)}->{short(b)}", code="quote_failed",
                                 details={"error_code": "router:NoPrice", "path": path_l, "amount_in": int(amount_raw)})
            amounts.append(int(amounts[-1] * price.numerator // price.denominator))
        return RouterQuote(path=path_l, amount_in=int(amount_raw), amounts=amounts, source="fake")

    async def token_balance(self, token: str, holder: str) -> int:
        return self._bal(self._state, normalize(holder), normalize(token))

    async def native_balance(self, address: str) -> int:
        return self._state.native.get(normalize(address), 0)

    async def allowance(self, token: str, owner: str, spender: str) -> int:
        return self._state.allowances.get((normalize(owner), normalize(token), normalize(spender)), 0)

    async def token_metadata(self, token: str) -> tuple[str, int]:
        tok = self._state.tokens.get(normalize(token))
        if tok is None:
            raise ChainError(f"unknown token {token}", details={"token": normalize(token)})
        return tok.symbol, tok.decimals

    # ------------------------------------------------------------------ Protocol: builders

    async def estimate_gas(self, sender: str, to: str, data: str, value: int = 0) -> int:
        self._dry_run(normalize(sender), to, data, value)
        if not data or data == "0x":
            return GAS_TABLE["native"]
        try:
            fn, _ = abi.decode_call("TraderVault" if normalize(to) == self.vault_address else "TestToken", data)
        except Exception:  # noqa: BLE001
            return 100_000
        return GAS_TABLE.get(fn, 100_000)

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
        sender_l, to_l = normalize(sender), normalize(to)
        if estimate:
            self._dry_run(sender_l, to_l, data, value, pre_steps)
        if gas is None:
            gas = GAS_TABLE.get(action, 100_000)
        full_summary: dict[str, Any] = {"action": action, **summary, "gas_estimated": estimate and not pre_steps,
                                        "fake": True}
        self.calls.append((f"build_{action}", dict(full_summary)))
        return UnsignedTx(
            kind=kind, action=action, from_address=sender_l, to=to_l, data=data, value=int(value), gas=gas,
            chain_id=self.chain_id_expected, summary=full_summary,
            expires_at=datetime.now(UTC) + timedelta(seconds=self.pending_tx_ttl_seconds),
            pre_steps=list(pre_steps or []),
        )

    def _vault_data(self, fn: str, args: list) -> str:
        return abi.encode_call("TraderVault", fn, args)

    async def _approval_steps(self, owner: str, token: str, amount_raw: int) -> list[PreStep]:
        if await self.allowance(token, owner, self.vault_address) >= int(amount_raw):
            return []
        return [await self.build_approve(owner, token, self.vault_address, int(amount_raw))]

    async def build_approve(self, owner: str, token: str, spender: str, amount_raw: int) -> PreStep:
        data = abi.encode_call("IERC20", "approve", [spender, int(amount_raw)])
        return PreStep(
            kind="approve", to=normalize(token), data=data, value=0, gas=GAS_TABLE["approve"],
            description=f"{short(token)} için {amount_raw} birim harcama onayı ({short(spender)})",
            spender=normalize(spender), token=normalize(token), amount_raw=int(amount_raw),
        )

    async def build_reserve(self, customer: str, token: str, amount_raw: int, listing_ref: bytes) -> UnsignedTx:
        data = self._vault_data("reserve", [token, int(amount_raw), bytes(listing_ref)])
        steps = await self._approval_steps(customer, token, amount_raw)
        return await self._build(
            kind="reserve", action="reserve", sender=customer, to=self.vault_address, data=data, pre_steps=steps,
            summary={"token": normalize(token), "amount_raw": int(amount_raw), "listing_ref": "0x" + bytes(listing_ref).hex()},
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
        return await self._build(
            kind="propose", action="propose", sender=trader, to=self.vault_address,
            data=self._vault_data("propose", [terms]), summary={"terms": terms.as_dict()},
        )

    async def build_open(self, customer: str, terms: Terms) -> UnsignedTx:
        terms.validate()
        steps = await self._approval_steps(customer, terms.base_token, terms.principal)
        return await self._build(
            kind="open", action="open", sender=customer, to=self.vault_address, data=self._vault_data("open", [terms]),
            pre_steps=steps, summary={"terms": terms.as_dict()},
        )

    async def build_open_reserved(self, customer: str, terms: Terms, reservation_id: int) -> UnsignedTx:
        terms.validate()
        return await self._build(
            kind="open_reserved", action="openReserved", sender=customer, to=self.vault_address,
            data=self._vault_data("openReserved", [terms, int(reservation_id)]),
            summary={"terms": terms.as_dict(), "reservation_id": int(reservation_id)},
        )

    async def build_fund(self, customer: str, agreement_id: int) -> UnsignedTx:
        ag = await self.read_agreement(agreement_id)
        steps = await self._approval_steps(customer, ag.terms.base_token, ag.terms.principal)
        return await self._build(
            kind="fund", action="fund", sender=customer, to=self.vault_address,
            data=self._vault_data("fund", [int(agreement_id)]), pre_steps=steps,
            summary={"agreement_id": int(agreement_id), "principal": ag.terms.principal, "token": ag.terms.base_token},
        )

    async def build_fund_reserved(self, customer: str, agreement_id: int, reservation_id: int) -> UnsignedTx:
        return await self._build(
            kind="fund_reserved", action="fundReserved", sender=customer, to=self.vault_address,
            data=self._vault_data("fundReserved", [int(agreement_id), int(reservation_id)]),
            summary={"agreement_id": int(agreement_id), "reservation_id": int(reservation_id)},
        )

    async def build_accept(self, trader: str, agreement_id: int) -> UnsignedTx:
        return await self._build(
            kind="accept", action="accept", sender=trader, to=self.vault_address,
            data=self._vault_data("accept", [int(agreement_id)]), summary={"agreement_id": int(agreement_id)},
        )

    async def build_cancel(self, who: str, agreement_id: int) -> UnsignedTx:
        return await self._build(
            kind="cancel", action="cancel", sender=who, to=self.vault_address,
            data=self._vault_data("cancel", [int(agreement_id)]), summary={"agreement_id": int(agreement_id)},
        )

    async def build_trade(
        self, trader: str, agreement_id: int, token_in: str, token_out: str, amount_in: int, min_out: int, deadline: int
    ) -> UnsignedTx:
        data = self._vault_data("trade", [int(agreement_id), token_in, token_out, int(amount_in), int(min_out), int(deadline)])
        return await self._build(
            kind="trade", action="trade", sender=trader, to=self.vault_address, data=data,
            summary={
                "agreement_id": int(agreement_id), "token_in": normalize(token_in), "token_out": normalize(token_out),
                "amount_in": int(amount_in), "min_out": int(min_out), "deadline": int(deadline),
            },
        )

    async def build_settle(self, caller: str, agreement_id: int, min_outs: list[int]) -> UnsignedTx:
        outs = [int(m) for m in min_outs]
        return await self._build(
            kind="settle", action="settle", sender=caller, to=self.vault_address,
            data=self._vault_data("settle", [int(agreement_id), outs]),
            summary={"agreement_id": int(agreement_id), "min_outs": outs},
        )

    async def build_claim(self, customer: str, agreement_id: int, token: str) -> UnsignedTx:
        return await self._build(
            kind="claim", action="claim", sender=customer, to=self.vault_address,
            data=self._vault_data("claim", [int(agreement_id), token]),
            summary={"agreement_id": int(agreement_id), "token": normalize(token)},
        )

    async def build_transfer(self, sender: str, to: str, amount_wei: int) -> UnsignedTx:
        return await self._build(
            kind="transfer", action="transfer", sender=sender, to=to, data="0x", value=int(amount_wei), estimate=False,
            gas=GAS_TABLE["native"], summary={"to": normalize(to), "amount_wei": int(amount_wei), "native": True},
        )

    async def build_token_transfer(self, sender: str, token: str, to: str, amount_raw: int) -> UnsignedTx:
        data = abi.encode_call("IERC20", "transfer", [to, int(amount_raw)])
        return await self._build(
            kind="transfer", action="transfer", sender=sender, to=token, data=data,
            summary={"to": normalize(to), "token": normalize(token), "amount_raw": int(amount_raw), "native": False},
        )

    async def build_admin_set_token(self, owner: str, token: str, allowed: bool, is_base: bool) -> UnsignedTx:
        return await self._build(
            kind="admin", action="setToken", sender=owner, to=self.vault_address,
            data=self._vault_data("setToken", [token, bool(allowed), bool(is_base)]),
            summary={"token": normalize(token), "allowed": bool(allowed), "is_base": bool(is_base)},
        )

    async def build_admin_set_paused(self, owner: str, paused: bool) -> UnsignedTx:
        return await self._build(
            kind="admin", action="setPaused", sender=owner, to=self.vault_address,
            data=self._vault_data("setPaused", [bool(paused)]), summary={"paused": bool(paused)},
        )

    async def build_admin_set_fees(self, owner: str, platform_fee_bps: int, fee_recipient: str) -> UnsignedTx:
        return await self._build(
            kind="admin", action="setFees", sender=owner, to=self.vault_address,
            data=self._vault_data("setFees", [int(platform_fee_bps), fee_recipient]),
            summary={"platform_fee_bps": int(platform_fee_bps), "fee_recipient": normalize(fee_recipient)},
        )

    async def build_admin_propose_router(self, owner: str, router: str) -> UnsignedTx:
        return await self._build(
            kind="admin", action="proposeRouterChange", sender=owner, to=self.vault_address,
            data=self._vault_data("proposeRouterChange", [router]), summary={"router": normalize(router)},
        )

    async def build_admin_apply_router(self, owner: str) -> UnsignedTx:
        return await self._build(
            kind="admin", action="applyRouterChange", sender=owner, to=self.vault_address,
            data=self._vault_data("applyRouterChange", []), summary={},
        )

    async def build_admin_cancel_router(self, owner: str) -> UnsignedTx:
        return await self._build(
            kind="admin", action="cancelRouterChange", sender=owner, to=self.vault_address,
            data=self._vault_data("cancelRouterChange", []), summary={},
        )

    async def build_admin_set_settle_slippage(self, owner: str, bps: int) -> UnsignedTx:
        return await self._build(
            kind="admin", action="setSettleSlippage", sender=owner, to=self.vault_address,
            data=self._vault_data("setSettleSlippage", [int(bps)]), summary={"settle_slippage_bps": int(bps)},
        )

    # ------------------------------------------------------------------ Protocol: tx / receipt / logs

    async def get_transaction(self, tx_hash: str) -> TxInfo | None:
        return self.txs.get(tx_hash.lower())

    async def get_receipt(self, tx_hash: str) -> TxReceiptResult | None:
        rc = self.receipts.get(tx_hash.lower())
        if rc is None:
            return None
        return replace(rc, confirmations=self.blocks[-1].number - rc.block_number + 1)

    async def explain_failure(self, receipt: TxReceiptResult) -> RevertInfo:
        if receipt.status == 1:
            return RevertInfo(selector=None, name=None, code=None, args={}, message="işlem başarılı")
        stored = self.receipts.get(receipt.tx_hash)
        if stored is not None and stored.revert is not None:
            return stored.revert
        if receipt.revert is not None:
            return receipt.revert
        return RevertInfo(selector=None, name=None, code=None, args={}, message=abi.GENERIC_REVERT_MESSAGE)

    async def get_logs(
        self, from_block: int, to_block: int, address: str | None = None, topics: list[Any] | None = None
    ) -> list[EventRecord]:
        target = normalize(address) if address else self.vault_address
        out: list[EventRecord] = []
        for rec in self.logs:
            if rec.block_number < int(from_block) or rec.block_number > int(to_block) or rec.address != target:
                continue
            if topics and not self._topics_match(self._raw_logs[(rec.tx_hash, rec.log_index)]["topics"], topics):
                continue
            out.append(rec)
        out.sort(key=lambda r: (r.block_number, r.log_index))
        return out

    @staticmethod
    def _topics_match(log_topics: list[str], wanted: list[Any]) -> bool:
        for i, w in enumerate(wanted):
            if w is None:
                continue
            if i >= len(log_topics):
                return False
            options = [w] if isinstance(w, str) else list(w)
            if log_topics[i].lower() not in {str(o).lower() for o in options}:
                return False
        return True

    async def faucet_mint(self, minter_key: str, token: str, to: str, amount_raw: int) -> str:
        """``TestToken.mint`` sent from ``minter_address`` (the key is not interpreted by the fake)."""
        data = abi.encode_call("TestToken", "mint", [to, int(amount_raw)])
        self.calls.append(("faucet_mint", {"token": normalize(token), "to": normalize(to), "amount_raw": int(amount_raw)}))
        return self.send(self.minter_address, token, data)


__all__ = [
    "DEFAULT_BLOCK",
    "DEFAULT_NOW",
    "FAKE_CHAIN_ID",
    "FAKE_FEE_RECIPIENT",
    "FAKE_MINTER",
    "FAKE_OWNER",
    "FAKE_ROUTER",
    "FAKE_TUSDC",
    "FAKE_TWBTC",
    "FAKE_TWETH",
    "FAKE_VAULT",
    "GAS_TABLE",
    "FakeChainGateway",
]
