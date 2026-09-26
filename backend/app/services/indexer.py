"""Vault event indexer, pending-transaction tracker and reconciler (03-backend-tasarim §4.3–4.7, §5).

* `apply_event(ctx, record)` maps one decoded `TraderVault` event (`EventRecord`, Solidity names from the ABI)
  onto the mirror rows (agreements, agreement_balances, trades, value snapshots, listings, assets,
  notifications, trader stats). It is **idempotent** — guarded by `*_tx` hashes, `(tx_hash, log_index)` and
  status checks — so the same event may arrive twice: once from a receipt (`POST /tx/submit` /
  `pending_tracker`, 0 confirmations) and again from the `eth_getLogs` poll (`confirmations` deep).
* `finalize_receipt` / `track_submitted` / `expire_pending` / `recheck_recent_confirmed`: the pending
  transaction life cycle (§4.3 step 7, §4.6).
* `run_indexer_once(db, chain, settings)`: `eth_getLogs` windows up to `latest - confirmations`, cursor
  `indexer_state[vault_events].{block_number,last_block_hash}`, reorg detection (§5.4), `failed_events`
  (§5.3): a failing event is retried in place a few times (cursor does not advance), then recorded and
  retried with backoff so a poison event never stalls the chain.
* `run_reconcile_once(db, chain, settings)`: every open agreement → `read_agreement` / `read_balances` /
  `read_value_in_base` → status resync, current value, snapshots, drawdown / expiry alerts.

Everything is flush-only except `finalize_receipt` on a receipt mismatch (§4.3/7b, explicit commit so the
`failed` row survives the request rollback). The worker / request session owns the commit otherwise.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ChainError, NotFoundError
from app.models import (
    Agreement,
    AgreementBalance,
    AgreementStatus,
    AgreementValueSnapshot,
    Asset,
    FailedEvent,
    Follow,
    IndexerState,
    Listing,
    ListingStatus,
    Notification,
    NotificationCategory,
    PendingTransaction,
    PendingTxKind,
    PendingTxStatus,
    Trade,
    User,
    UserRole,
)
from app.services.agreements import OPEN_STATUSES, compute_listing_ref, json_safe, now_utc, ts_to_dt
from app.services.chain import abi
from app.services.chain import amounts as money
from app.services.chain.addresses import checksum, is_evm_address, same, short
from app.services.chain.errors import ContractRevertError, ReceiptMismatchError
from app.services.chain.types import AgreementView, EventRecord, TxReceiptResult
from app.services.notifications import notify, notify_many
from app.services.trader_stats import refresh_trader_stats

log = logging.getLogger(__name__)

VAULT_EVENTS_KEY = "vault_events"  # same key app.services.admin reports as the indexer position
VAULT_CONFIG_KEY = "vault_config"  # block of the last ConfigChanged (config cache invalidation marker)
RECONCILE_KEY = "reconcile"
FIRST_RUN_LOOKBACK = 5_000  # blocks, when neither indexer_start_block nor deployments blockNumber exist
SUBMITTED_STALE_SECONDS = 10  # pending_tracker re-polls submitted rows older than this
INLINE_RETRIES = 3  # a failing event is retried in place this many ticks before the cursor moves past it
ALERT_ATTEMPTS = 10  # failed_events rows with this many attempts are logged at ERROR (alert)
MAX_RETRY_BACKOFF = 3_600  # seconds
REORG_MAX_FACTOR = 8  # walk back at most confirmations * 8 blocks
RECHECK_FACTOR = 10  # recheck confirmed rows within confirmations * 10 blocks of the head
MAX_FOLLOWER_NOTIFICATIONS = 500
DRAWDOWN_WARN_PCT = 80
CREATE_KINDS = (PendingTxKind.open, PendingTxKind.open_reserved, PendingTxKind.propose)
AGREEMENT_RELATIONSHIPS = frozenset({"customer", "trader", "base_asset", "balances"})
BACKEND_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class IndexerRunResult:
    skipped: bool = False
    reason: str | None = None
    from_block: int | None = None
    to_block: int | None = None
    latest_block: int | None = None
    windows: int = 0
    events_seen: int = 0
    events_applied: int = 0
    events_failed: int = 0
    failed_retried: int = 0
    reorg_depth: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return json_safe(self.__dict__)


@dataclass
class ReconcileResult:
    skipped: bool = False
    checked: int = 0
    valued: int = 0
    snapshots: int = 0
    alerts: int = 0
    status_synced: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return json_safe(self.__dict__)


class IndexContext:
    """Session + settings + chain gateway with per-run caches (assets by address, users by wallet, agreements)."""

    def __init__(self, db: AsyncSession, settings: Settings, chain: Any) -> None:
        self.db = db
        self.settings = settings
        self.chain = chain
        self._assets: dict[str, Asset | None] = {}
        self._users: dict[str, User | None] = {}
        self._agreements: dict[int, Agreement] = {}
        self.failed_keys: set[tuple[str, int]] = set()  # unresolved failed_events (tx_hash, log_index)

    @property
    def vault_address(self) -> str | None:
        v = getattr(self.chain, "vault_address", None) or self.settings.vault_address
        return str(v).lower() if v else None

    async def asset_by_address(self, address: str | None) -> Asset | None:
        if not address:
            return None
        key = address.lower()
        if key not in self._assets:
            self._assets[key] = (
                await self.db.execute(
                    select(Asset).where(Asset.chain_id == self.settings.chain_id, Asset.address == key)
                )
            ).scalar_one_or_none()
        return self._assets[key]

    async def user_by_address(self, address: str | None) -> User | None:
        if not address:
            return None
        key = address.lower()
        if key not in self._users:
            self._users[key] = (
                await self.db.execute(select(User).where(User.wallet_address == key))
            ).scalar_one_or_none()
        return self._users[key]

    async def agreement_by_onchain(self, onchain_id: int) -> Agreement | None:
        ag = self._agreements.get(int(onchain_id))
        if ag is None:
            ag = (
                await self.db.execute(select(Agreement).where(Agreement.onchain_id == int(onchain_id)))
            ).scalar_one_or_none()
            if ag is not None:
                await self.loaded(ag)
                self._agreements[int(onchain_id)] = ag
        return ag

    async def loaded(self, ag: Agreement) -> Agreement:
        """Make sure the relationships the handlers touch are loaded (an identity-map hit — e.g. the row was
        created earlier in the same request session — has none of them, and a lazy load would raise in async)."""
        unloaded = sa_inspect(ag).unloaded & AGREEMENT_RELATIONSHIPS
        if unloaded:
            await self.db.refresh(ag, list(unloaded))
        return ag

    def remember(self, ag: Agreement) -> None:
        if ag.onchain_id is not None:
            self._agreements[int(ag.onchain_id)] = ag

    def forget_agreements(self) -> None:
        self._agreements.clear()

    async def pending_by_hash(self, tx_hash: str | None) -> PendingTransaction | None:
        if not tx_hash:
            return None
        return (
            await self.db.execute(
                select(PendingTransaction)
                .where(PendingTransaction.tx_hash == tx_hash.lower())
                .order_by(PendingTransaction.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()


# --- small helpers -------------------------------------------------------------------------------


def _dec(raw: int, asset: Asset | None, default_decimals: int = 18) -> Decimal:
    return money.from_raw(int(raw), asset.decimals if asset is not None else default_decimals)


def _fmt(raw: int, asset: Asset) -> str:
    return money.format_amount(money.from_raw(int(raw), asset.decimals), asset.decimals)


def _fmt_dec(value: Decimal, asset: Asset) -> str:
    return money.format_amount(value, asset.decimals)


def _event_time(record: EventRecord) -> datetime:
    return record.block_timestamp or now_utc()


def _hex32(value: Any) -> str:
    """bytes32 event arg -> 64-char lower hex (no 0x); strings are accepted as hex too."""
    if isinstance(value, bytes | bytearray):
        return bytes(value).hex()
    s = str(value or "")
    return s[2:].lower() if s.startswith("0x") else s.lower()


def _api_value(v: Any, *, checksum_addresses: bool) -> Any:
    """JSON view of an event arg: ints -> str, addresses -> checksum (API) / as-is (storage), bytes -> 0x hex."""
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return str(v)
    if isinstance(v, bytes | bytearray):
        return "0x" + bytes(v).hex()
    if isinstance(v, str) and checksum_addresses and is_evm_address(v):
        return checksum(v)
    if isinstance(v, list | tuple):
        return [_api_value(x, checksum_addresses=checksum_addresses) for x in v]
    if isinstance(v, dict):
        return {str(k): _api_value(x, checksum_addresses=checksum_addresses) for k, x in v.items()}
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def event_out(record: EventRecord) -> dict[str, Any]:
    """02-api §2.3 `events[]` item: `{"name", "args"}` — ints as strings, addresses checksummed, bytes hex."""
    return {"name": record.name, "args": _api_value(dict(record.args), checksum_addresses=True)}


_EVENT_INPUTS: dict[str, list[dict]] = {e["name"]: list(e.get("inputs", [])) for e in abi.EVENT_TOPICS.values()}


def _args_to_json(record: EventRecord) -> dict[str, Any]:
    data = _api_value(dict(record.args), checksum_addresses=False)
    data["__meta"] = {
        "block_hash": record.block_hash,
        "tx_index": record.tx_index,
        "topic0": record.topic0,
        "address": record.address,
        "block_timestamp": record.block_timestamp.isoformat() if record.block_timestamp else None,
    }
    return data


def _arg_from_json(sol_type: str, value: Any) -> Any:
    if sol_type == "bool":
        return bool(value)
    if sol_type.startswith("uint") or sol_type.startswith("int"):
        return int(value)
    if sol_type == "address":
        return str(value).lower()
    if sol_type.startswith("bytes") and isinstance(value, str):
        s = value[2:] if value.startswith("0x") else value
        try:
            return bytes.fromhex(s)
        except ValueError:
            return value
    return value


def _record_from_failed(row: FailedEvent) -> EventRecord:
    """Rebuild the `EventRecord` of a `failed_events` row (types restored from the ABI input list)."""
    data = dict(row.args or {})
    meta = dict(data.pop("__meta", None) or {})
    inputs = _EVENT_INPUTS.get(row.event_name, [])
    args: dict[str, Any] = {}
    for inp in inputs:
        name = inp["name"]
        if name in data:
            args[name] = _arg_from_json(inp["type"], data[name])
    for k, v in data.items():  # unknown / extra keys survive as they were stored
        args.setdefault(k, v)
    ts = meta.get("block_timestamp")
    return EventRecord(
        block_number=int(row.block_number),
        block_hash=str(meta.get("block_hash") or ""),
        log_index=int(row.log_index),
        tx_hash=row.tx_hash,
        tx_index=int(meta.get("tx_index") or 0),
        address=str(meta.get("address") or ""),
        name=row.event_name,
        args=args,
        topic0=str(meta.get("topic0") or ""),
        block_timestamp=datetime.fromisoformat(ts) if ts else None,
    )


async def _upsert_balances(ctx: IndexContext, ag: Agreement, balances: list[tuple[str, int]]) -> None:
    """Mirror the on-chain `getBalances` vector; zero balances and unknown tokens are not mirrored."""
    by_asset = {b.asset_id: b for b in ag.balances}
    keep: set[uuid.UUID] = set()
    for token, raw in balances:
        if int(raw) <= 0:
            continue
        asset = await ctx.asset_by_address(token)
        if asset is None:
            log.warning("agreement %s holds unknown token %s (not in assets)", ag.id, token)
            continue
        value = money.from_raw(int(raw), asset.decimals)
        row = by_asset.get(asset.id)
        if row is None:
            row = AgreementBalance(agreement_id=ag.id, asset_id=asset.id, balance=value, asset=asset)
            ag.balances.append(row)
        else:
            row.balance = value
        keep.add(asset.id)
    for row in list(ag.balances):
        if row.asset_id not in keep:
            ag.balances.remove(row)


async def _set_balance(ctx: IndexContext, ag: Agreement, token: str, raw: int) -> bool:
    """Set (or create) the balance row of one token; returns True when something changed."""
    asset = await ctx.asset_by_address(token)
    if asset is None:
        return False
    value = money.from_raw(int(raw), asset.decimals)
    row = next((b for b in ag.balances if b.asset_id == asset.id), None)
    if row is None:
        if value <= 0:
            return False
        ag.balances.append(AgreementBalance(agreement_id=ag.id, asset_id=asset.id, balance=value, asset=asset))
        return True
    if row.balance == value:
        return False
    row.balance = value
    return True


async def _adjust_balance(ctx: IndexContext, ag: Agreement, token: str, delta_raw: int) -> None:
    asset = await ctx.asset_by_address(token)
    if asset is None:
        return
    delta = money.from_raw(abs(int(delta_raw)), asset.decimals) * (1 if delta_raw >= 0 else -1)
    row = next((b for b in ag.balances if b.asset_id == asset.id), None)
    if row is None:
        if delta <= 0:
            return
        ag.balances.append(AgreementBalance(agreement_id=ag.id, asset_id=asset.id, balance=delta, asset=asset))
        return
    row.balance = row.balance + delta
    if row.balance <= 0:
        ag.balances.remove(row)


async def _remove_balance(ctx: IndexContext, ag: Agreement, token: str) -> bool:
    asset = await ctx.asset_by_address(token)
    if asset is None:
        return False
    row = next((b for b in ag.balances if b.asset_id == asset.id), None)
    if row is None:
        return False
    ag.balances.remove(row)
    return True


def _clear_balances(ag: Agreement) -> None:
    for row in list(ag.balances):
        ag.balances.remove(row)


def _set_value(ag: Agreement, value: Decimal, at: datetime) -> None:
    ag.current_value = value
    ag.value_updated_at = at
    if ag.high_water_value is None or value > ag.high_water_value:
        ag.high_water_value = value


def _snapshot(ctx: IndexContext, ag: Agreement, value: Decimal, at: datetime) -> None:
    ctx.db.add(AgreementValueSnapshot(agreement_id=ag.id, value=value, at=at))


async def _notify_parties(
    ctx: IndexContext,
    ag: Agreement,
    type_: str,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
    *,
    exclude: uuid.UUID | None = None,
    only: UserRole | None = None,
) -> int:
    payload = {"agreement_id": str(ag.id), "onchain_id": ag.onchain_id, **(data or {})}
    targets = []
    if only in (None, UserRole.customer):
        targets.append(ag.customer_id)
    if only in (None, UserRole.trader):
        targets.append(ag.trader_id)
    n = 0
    for uid in targets:
        if uid == exclude:
            continue
        await notify(ctx.db, uid, type_, title, body, payload, category=NotificationCategory.agreement)
        n += 1
    return n


async def _already_notified(
    db: AsyncSession,
    user_id: uuid.UUID,
    type_: str,
    agreement_id: uuid.UUID,
    *,
    tx_hash: str | None = None,
    token: str | None = None,
) -> bool:
    q = select(Notification.id).where(
        Notification.user_id == user_id,
        Notification.type == type_,
        Notification.data["agreement_id"].astext == str(agreement_id),
    )
    if tx_hash is not None:
        q = q.where(Notification.data["tx_hash"].astext == tx_hash)
    if token is not None:
        q = q.where(Notification.data["token"].astext == token)
    return (await db.execute(q.limit(1))).scalar_one_or_none() is not None


# --- agreement discovery ---------------------------------------------------------------------------


async def _chain_agreement(ctx: IndexContext, onchain_id: int) -> AgreementView | None:
    """`read_agreement`; NotFound / RPC trouble -> None (callers fall back to event data)."""
    try:
        view = await ctx.chain.read_agreement(int(onchain_id))
    except ContractRevertError as e:
        if e.revert is not None and e.revert.code == int(abi.VaultError.NotFound):
            return None
        log.warning("read_agreement(%s) reverted: %s", onchain_id, e.message)
        return None
    except ChainError as e:
        log.warning("read_agreement(%s) failed: %s", onchain_id, e.message)
        return None
    if int(view.status) == 0:
        return None
    return view


async def _match_draft(
    ctx: IndexContext, *, listing_ref: str | None, customer: str, trader: str, principal_raw: int, base_token: str
) -> Agreement | None:
    """A draft row for these terms: by listing_ref first, then by parties + principal + base token."""
    base = await ctx.asset_by_address(base_token)
    cust = await ctx.user_by_address(customer)
    trd = await ctx.user_by_address(trader)
    if listing_ref:
        rows = (
            await ctx.db.execute(
                select(Agreement)
                .where(Agreement.listing_ref == listing_ref, Agreement.onchain_id.is_(None))
                .order_by(Agreement.created_at.asc())
            )
        ).scalars().all()
        for ag in rows:
            if cust is None or trd is None or (ag.customer_id == cust.id and ag.trader_id == trd.id):
                return ag
    if cust is None or trd is None or base is None:
        return None
    principal = money.from_raw(int(principal_raw), base.decimals)
    return (
        await ctx.db.execute(
            select(Agreement)
            .where(
                Agreement.onchain_id.is_(None),
                Agreement.status == AgreementStatus.draft,
                Agreement.customer_id == cust.id,
                Agreement.trader_id == trd.id,
                Agreement.base_asset_id == base.id,
                Agreement.principal == principal,
            )
            .order_by(Agreement.created_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _create_from_chain(ctx: IndexContext, view: AgreementView) -> Agreement | None:
    """Mirror row for an agreement created outside the app flow (both parties must be registered)."""
    t = view.terms
    cust = await ctx.user_by_address(t.customer)
    trd = await ctx.user_by_address(t.trader)
    base = await ctx.asset_by_address(t.base_token)
    if cust is None or trd is None or base is None:
        log.warning(
            "cannot mirror on-chain agreement %s: unknown customer/trader/base (%s/%s/%s)",
            view.id, cust is not None, trd is not None, base is not None,
        )
        return None
    status = AgreementStatus.from_onchain(int(view.status)) or AgreementStatus.proposed
    ag = Agreement(
        onchain_id=int(view.id),
        customer_id=cust.id,
        trader_id=trd.id,
        base_asset_id=base.id,
        principal=money.from_raw(int(t.principal), base.decimals),
        duration_secs=int(t.duration_seconds),
        commission_bps=int(t.commission_bps),
        max_drawdown_bps=int(t.max_drawdown_bps),
        listing_ref=_hex32(t.listing_ref),
        platform_fee_bps=int(view.platform_fee_bps),
        vault_address=ctx.vault_address,
        status=status,
        proposer_role=UserRole.trader if same(view.proposer, t.trader) else UserRole.customer,
        start_time=ts_to_dt(view.start_time),
        end_time=ts_to_dt(view.end_time),
        customer=cust,
        trader=trd,
        base_asset=base,
    )
    ctx.db.add(ag)
    await ctx.db.flush()
    await ctx.db.refresh(ag)
    ctx.remember(ag)
    log.info("mirrored on-chain agreement %s as %s", view.id, ag.id)
    return ag


async def _resolve_agreement(ctx: IndexContext, record: EventRecord, onchain_id: int) -> Agreement | None:
    """Row for `onchain_id`: existing mirror -> pending tx of this hash -> draft matched via chain terms /
    event fields -> new row mirrored from the chain."""
    ag = await ctx.agreement_by_onchain(onchain_id)
    if ag is not None:
        return ag
    pending = await ctx.pending_by_hash(record.tx_hash)
    if pending is not None and pending.agreement_id is not None and pending.kind in CREATE_KINDS:
        ag = await ctx.db.get(Agreement, pending.agreement_id)
        if ag is not None and ag.onchain_id in (None, int(onchain_id)):
            return await ctx.loaded(ag)
    view = await _chain_agreement(ctx, onchain_id)
    if view is not None:
        t = view.terms
        ag = await _match_draft(
            ctx, listing_ref=_hex32(t.listing_ref), customer=t.customer, trader=t.trader,
            principal_raw=t.principal, base_token=t.base_token,
        )
        if ag is not None:
            return await ctx.loaded(ag)
        return await _create_from_chain(ctx, view)
    if record.name in ("Proposed", "Opened"):
        a = record.args
        ag = await _match_draft(
            ctx, listing_ref=None, customer=a["customer"], trader=a["trader"],
            principal_raw=int(a["principal"]), base_token=a["baseToken"],
        )
        return await ctx.loaded(ag) if ag is not None else None
    return None


# --- event handlers -------------------------------------------------------------------------------


async def _on_created(ctx: IndexContext, record: EventRecord, *, actor: uuid.UUID | None) -> bool:
    opened = record.name == "Opened"
    a = record.args
    onchain_id = int(a["id"])
    ag = await _resolve_agreement(ctx, record, onchain_id)
    if ag is None:
        log.warning("%s for unknown agreement %s (tx %s) — skipped", record.name, onchain_id, record.tx_hash[:10])
        return False
    if ag.onchain_id is not None and int(ag.onchain_id) != onchain_id:
        log.warning("agreement %s already bound to onchain %s, event says %s", ag.id, ag.onchain_id, onchain_id)
        return False
    if ag.onchain_id == onchain_id and ag.created_tx == record.tx_hash:
        return False  # already applied
    ag.onchain_id = onchain_id
    ctx.remember(ag)
    if ag.status in (AgreementStatus.draft, AgreementStatus.proposed, AgreementStatus.funded, AgreementStatus.failed):
        ag.status = AgreementStatus.funded if opened else AgreementStatus.proposed
    ag.proposer_role = UserRole.customer if opened else UserRole.trader
    ag.created_tx = record.tx_hash
    ag.last_event_block_number = record.block_number
    ag.vault_address = ag.vault_address or ctx.vault_address
    if not ag.listing_ref or len(ag.listing_ref) != 64:
        ag.listing_ref = compute_listing_ref(ag.offer_id or ag.id)
    if ag.platform_fee_bps is None:
        view = await _chain_agreement(ctx, onchain_id)  # best effort: fee snapshot taken by the contract
        if view is not None:
            ag.platform_fee_bps = int(view.platform_fee_bps)
    if opened:
        await _upsert_balances(ctx, ag, [(a["baseToken"], int(a["principal"]))])
    await ctx.db.flush()
    base = ag.base_asset
    amount = f"{_fmt_dec(ag.principal, base)} {base.symbol}"
    if opened:
        await _notify_parties(
            ctx, ag, "agreement_funded", "Anapara kilitlendi",
            f"{ag.customer.display_name} {amount} kilitledi. Sözleşmeyi başlatmak için kabul edin.",
            {"tx_hash": record.tx_hash}, exclude=actor, only=UserRole.trader,
        )
    else:
        await _notify_parties(
            ctx, ag, "agreement_proposed", "Yeni sözleşme teklifi",
            f"{ag.trader.display_name} {amount} için bir sözleşme önerdi. Başlatmak için anaparayı yatırın.",
            {"tx_hash": record.tx_hash}, exclude=actor, only=UserRole.customer,
        )
    return True


async def _on_activated(ctx: IndexContext, record: EventRecord, *, actor: uuid.UUID | None) -> bool:
    a = record.args
    ag = await _resolve_agreement(ctx, record, int(a["id"]))
    if ag is None:
        log.warning("Activated for unknown agreement %s — skipped", a["id"])
        return False
    if ag.activate_tx == record.tx_hash:
        return False  # a tx activates at most once
    if ag.onchain_id is None:
        ag.onchain_id = int(a["id"])
        ctx.remember(ag)
    if ag.status not in (AgreementStatus.settled, AgreementStatus.cancelled):
        ag.status = AgreementStatus.active
    ag.start_time = ts_to_dt(int(a["startTime"]))
    ag.end_time = ts_to_dt(int(a["endTime"]))
    ag.activate_tx = record.tx_hash
    ag.last_event_block_number = record.block_number
    if not ag.balances:
        base = ag.base_asset
        await _upsert_balances(ctx, ag, [(base.address, money.to_raw(ag.principal, base.decimals))])
    at = _event_time(record)
    if ag.current_value is None:
        _set_value(ag, ag.principal, at)
        _snapshot(ctx, ag, ag.principal, at)
    await ctx.db.flush()
    await refresh_trader_stats(ctx.db, ag.trader_id)
    end = ag.end_time.strftime("%d.%m.%Y %H:%M") if ag.end_time else "-"
    base = ag.base_asset
    await _notify_parties(
        ctx, ag, "agreement_activated", "Sözleşme başladı",
        f"{_fmt_dec(ag.principal, base)} {base.symbol} ile başladı. Bitiş: {end}.",
        {"tx_hash": record.tx_hash, "end_time": ag.end_time.isoformat() if ag.end_time else None}, exclude=actor,
    )
    return True


async def _on_cancelled(ctx: IndexContext, record: EventRecord, *, actor: uuid.UUID | None) -> bool:
    a = record.args
    ag = await _resolve_agreement(ctx, record, int(a["id"]))
    if ag is None:
        log.warning("Cancelled for unknown agreement %s — skipped", a["id"])
        return False
    if ag.cancel_tx == record.tx_hash:
        return False
    ag.status = AgreementStatus.cancelled
    ag.cancel_tx = record.tx_hash
    ag.last_event_block_number = record.block_number
    _clear_balances(ag)
    await ctx.db.flush()
    await refresh_trader_stats(ctx.db, ag.trader_id)
    base = ag.base_asset
    refunded = int(a["refunded"])
    refund = _fmt(refunded, base)
    body = f"Sözleşme iptal edildi. İade: {refund} {base.symbol}." if refunded > 0 else "Sözleşme iptal edildi."
    await _notify_parties(
        ctx, ag, "agreement_cancelled", "Sözleşme iptal edildi", body,
        {"tx_hash": record.tx_hash, "refunded": refund}, exclude=actor,
    )
    return True


def _symbol_label(a_in: Asset, a_out: Asset, base: Asset) -> str:
    """`trading.symbol_label` when importable (Dalga 2e), else the same rule locally."""
    try:
        from app.services.trading import symbol_label

        return symbol_label(a_in, a_out, base)
    except Exception:  # noqa: BLE001 - trading.py mid-rewrite / import error must not break indexing
        if same(a_in.address, base.address):
            return f"{a_out.symbol}/{a_in.symbol} · Alış"
        if same(a_out.address, base.address):
            return f"{a_in.symbol}/{a_out.symbol} · Satış"
        return f"{a_out.symbol}/{a_in.symbol} · Takas"


async def _on_traded(ctx: IndexContext, record: EventRecord, *, actor: uuid.UUID | None) -> bool:
    a = record.args
    ag = await _resolve_agreement(ctx, record, int(a["id"]))
    if ag is None:
        log.warning("Traded for unknown agreement %s — skipped", a["id"])
        return False
    existing = (
        await ctx.db.execute(
            select(Trade.id).where(Trade.tx_hash == record.tx_hash, Trade.log_index == int(record.log_index))
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False
    a_in = await ctx.asset_by_address(a["tokenIn"])
    a_out = await ctx.asset_by_address(a["tokenOut"])
    if a_in is None or a_out is None:
        log.warning(
            "Traded %s:%s uses unknown tokens (%s -> %s) — skipped",
            record.tx_hash[:10], record.log_index, short(a["tokenIn"]), short(a["tokenOut"]),
        )
        return False
    pending = await ctx.pending_by_hash(record.tx_hash)
    payload: dict[str, Any] = {}
    if pending is not None and pending.kind is PendingTxKind.trade:
        payload = dict((pending.payload or {}).get("context") or {})
    at = _event_time(record)
    base = ag.base_asset
    amount_in, amount_out, value_after_raw = int(a["amountIn"]), int(a["amountOut"]), int(a["valueAfter"])
    value_after = money.from_raw(value_after_raw, base.decimals)
    trade = Trade(
        agreement_id=ag.id,
        log_index=int(record.log_index),
        tx_hash=record.tx_hash,
        block_number=record.block_number,
        trader_id=ag.trader_id,
        token_in_id=a_in.id,
        token_out_id=a_out.id,
        amount_in=money.from_raw(amount_in, a_in.decimals),
        amount_out=money.from_raw(amount_out, a_out.decimals),
        value_after=value_after,
        note=payload.get("note"),
        symbol_label=payload.get("symbol_label") or _symbol_label(a_in, a_out, base),
        notify_investors=bool(payload.get("notify_investors", True)),
        created_at=at,
        token_in=a_in,
        token_out=a_out,
    )
    ctx.db.add(trade)
    if ag.onchain_id is not None:
        try:
            await _upsert_balances(ctx, ag, await ctx.chain.read_balances(int(ag.onchain_id)))
        except ChainError as e:  # chain unreachable: keep the mirror consistent arithmetically
            log.warning("read_balances(%s) failed after trade, adjusting locally: %s", ag.onchain_id, e.message)
            await _adjust_balance(ctx, ag, a["tokenIn"], -amount_in)
            await _adjust_balance(ctx, ag, a["tokenOut"], amount_out)
    _set_value(ag, value_after, at)
    _snapshot(ctx, ag, value_after, at)
    ag.last_event_block_number = record.block_number
    await ctx.db.flush()
    label = trade.symbol_label or f"{a_in.symbol}/{a_out.symbol}"
    body = f"{label}: {_fmt(amount_in, a_in)} {a_in.symbol} → {_fmt(amount_out, a_out)} {a_out.symbol}"
    if trade.note:
        body += f" — {trade.note[:120]}"
    data = {"trade_id": str(trade.id), "tx_hash": record.tx_hash, "value_after": str(value_after)}
    await _notify_parties(ctx, ag, "trade_executed", "Yeni işlem", body, data, exclude=actor, only=UserRole.customer)
    if trade.notify_investors:
        followers = (
            await ctx.db.execute(
                select(Follow.follower_id).where(Follow.trader_id == ag.trader_id).limit(MAX_FOLLOWER_NOTIFICATIONS)
            )
        ).scalars().all()
        targets = [f for f in followers if f not in (ag.customer_id, ag.trader_id, actor)]
        if targets:
            await notify_many(
                ctx.db, list(targets), "followed_trade", f"{ag.trader.display_name} yeni bir işlem yaptı", body,
                {"agreement_id": str(ag.id), "trader_id": str(ag.trader_id), **data},
                category=NotificationCategory.agreement,
            )
    return True


async def _on_settled(ctx: IndexContext, record: EventRecord, *, actor: uuid.UUID | None) -> bool:
    a = record.args
    ag = await _resolve_agreement(ctx, record, int(a["id"]))
    if ag is None:
        log.warning("Settled for unknown agreement %s — skipped", a["id"])
        return False
    if ag.settle_tx == record.tx_hash:
        return False
    base = ag.base_asset
    at = _event_time(record)
    by = str(a.get("by") or "").lower() or None
    ag.status = AgreementStatus.settled
    ag.settle_tx = record.tx_hash
    ag.final_value = money.from_raw(int(a["finalValue"]), base.decimals)
    ag.profit = money.from_raw(int(a["profit"]), base.decimals)
    ag.trader_fee = money.from_raw(int(a["traderFee"]), base.decimals)
    ag.platform_fee = money.from_raw(int(a["platformFee"]), base.decimals)
    ag.customer_payout = money.from_raw(int(a["customerPayout"]), base.decimals)
    ag.settled_at = at
    ag.settled_by = by
    ag.last_event_block_number = record.block_number
    _set_value(ag, ag.final_value, at)
    _snapshot(ctx, ag, ag.final_value, at)
    # balances are NOT cleared: unliquidated (in-kind) legs stay claimable — mirror what the vault still holds
    if ag.onchain_id is not None:
        try:
            await _upsert_balances(ctx, ag, await ctx.chain.read_balances(int(ag.onchain_id)))
        except ChainError as e:
            log.warning("read_balances(%s) failed after settle: %s", ag.onchain_id, e.message)
            await _remove_balance(ctx, ag, base.address)  # the base leg was paid out; keep in-kind rows
    await ctx.db.flush()
    await refresh_trader_stats(ctx.db, ag.trader_id)
    pnl = ag.final_value - ag.principal
    sign = "+" if pnl >= 0 else ""
    body = (
        f"Son değer {_fmt_dec(ag.final_value, base)} {base.symbol} ({sign}{money.format_amount(pnl, base.decimals)}). "
        f"Müşteri: {_fmt_dec(ag.customer_payout, base)}, trader komisyonu: {_fmt_dec(ag.trader_fee, base)} {base.symbol}."
    )
    data = {"tx_hash": record.tx_hash, "final_value": str(ag.final_value), "profit": str(ag.profit), "by": by}
    await _notify_parties(ctx, ag, "agreement_settled", "Sözleşme kapandı", body, data, exclude=actor)
    return True


async def _on_unliquidated(ctx: IndexContext, record: EventRecord) -> bool:
    """Settle could not sell a leg: `delivered=false` -> stays in the vault (claim), `true` -> sent in kind."""
    a = record.args
    ag = await _resolve_agreement(ctx, record, int(a["id"]))
    if ag is None:
        log.warning("Unliquidated for unknown agreement %s — skipped", a["id"])
        return False
    token = str(a["token"]).lower()
    amount = int(a["amount"])
    delivered = bool(a["delivered"])
    asset = await ctx.asset_by_address(token)
    changed = False
    if not delivered:
        changed = await _set_balance(ctx, ag, token, amount)
    elif await _remove_balance(ctx, ag, token):
        changed = True
    ag.last_event_block_number = max(int(ag.last_event_block_number or 0), record.block_number)
    if changed:
        await ctx.db.flush()
    type_ = "token_delivered" if delivered else "claim_pending"
    if await _already_notified(ctx.db, ag.customer_id, type_, ag.id, tx_hash=record.tx_hash, token=token):
        return changed
    sym = asset.symbol if asset is not None else short(token)
    amount_s = _fmt(amount, asset) if asset is not None else str(amount)
    if delivered:
        title, body = "Token olarak teslim edildi", f"{amount_s} {sym} satılamadı; cüzdanınıza token olarak gönderildi."
    else:
        title, body = "Talep bekleyen bakiye", f"{amount_s} {sym} satılamadı; kasadan talep edebilirsiniz."
    data = {"tx_hash": record.tx_hash, "token": token, "amount": amount_s, "delivered": delivered}
    if asset is not None:
        data["asset_id"] = str(asset.id)
    await _notify_parties(ctx, ag, type_, title, body, data, only=UserRole.customer)
    return True


async def _on_claimed(ctx: IndexContext, record: EventRecord) -> bool:
    a = record.args
    ag = await _resolve_agreement(ctx, record, int(a["id"]))
    if ag is None:
        log.warning("Claimed for unknown agreement %s — skipped", a["id"])
        return False
    token = str(a["token"]).lower()
    amount = int(a["amount"])
    asset = await ctx.asset_by_address(token)
    removed = await _remove_balance(ctx, ag, token)
    ag.last_event_block_number = max(int(ag.last_event_block_number or 0), record.block_number)
    if removed:
        await ctx.db.flush()
    if await _already_notified(ctx.db, ag.customer_id, "claimed", ag.id, tx_hash=record.tx_hash, token=token):
        return removed
    sym = asset.symbol if asset is not None else short(token)
    amount_s = _fmt(amount, asset) if asset is not None else str(amount)
    data: dict[str, Any] = {"tx_hash": record.tx_hash, "token": token, "amount": amount_s}
    if asset is not None:
        data["asset_id"] = str(asset.id)
    await _notify_parties(
        ctx, ag, "claimed", "Bakiye talep edildi", f"{amount_s} {sym} cüzdanınıza aktarıldı.", data,
        only=UserRole.customer,
    )
    return True


async def _on_token_set(ctx: IndexContext, record: EventRecord) -> bool:
    a = record.args
    asset = await ctx.asset_by_address(a["token"])
    allowed = bool(a["allowed"])
    if asset is None:
        log.info("TokenSet for unknown token %s (allowed=%s, isBase=%s)", short(a["token"]), allowed, a.get("isBase"))
        return False
    changed = False
    if asset.onchain_allowed != allowed:
        asset.onchain_allowed = allowed
        changed = True
    is_base = bool(a.get("isBase", False))
    if allowed and asset.is_base_allowed != is_base:
        asset.is_base_allowed = is_base
        changed = True
    if changed:
        await ctx.db.flush()
    return changed


async def _on_config_changed(ctx: IndexContext, record: EventRecord) -> bool:
    """The event carries the full config; we only mark the block so cached config readers can refresh."""
    a = record.args
    key = abi.config_key_of(a["key"]) if a.get("key") is not None else "?"
    log.info(
        "vault ConfigChanged key=%s router=%s fee_bps=%s recipient=%s paused=%s slippage_bps=%s (block %s)",
        key, short(str(a.get("router") or "")), a.get("platformFeeBps"), short(str(a.get("feeRecipient") or "")),
        a.get("paused"), a.get("settleSlippageBps"), record.block_number,
    )
    state = await _state(ctx.db, VAULT_CONFIG_KEY)
    if (state.block_number or -1) >= record.block_number and state.last_block_hash == record.block_hash:
        return False
    state.block_number = record.block_number
    state.last_block_hash = record.block_hash
    state.updated_at = now_utc()
    await ctx.db.flush()
    return True


async def _on_upgraded(ctx: IndexContext, record: EventRecord) -> bool:
    impl = str(record.args.get("implementation") or "")
    log.error("vault Upgraded: implementation=%s tx=%s block=%s (unexpected upgrade alarm)", impl, record.tx_hash, record.block_number)
    admins = (await ctx.db.execute(select(User.id).where(User.is_admin.is_(True)))).scalars().all()
    if not admins:
        return False
    already = (
        await ctx.db.execute(
            select(Notification.id)
            .where(Notification.type == "vault_upgraded", Notification.data["tx_hash"].astext == record.tx_hash)
            .limit(1)
        )
    ).scalar_one_or_none()
    if already is not None:
        return False
    await notify_many(
        ctx.db, list(admins), "vault_upgraded", "Kasa kontratı yükseltildi",
        f"Yeni implementasyon: {impl}. Beklenmiyorsa hemen inceleyin.",
        {"tx_hash": record.tx_hash, "implementation": impl, "block_number": record.block_number},
        category=NotificationCategory.system,
    )
    return True


async def _on_admin_event(ctx: IndexContext, record: EventRecord) -> bool:
    log.info("vault %s %s (tx %s)", record.name, _api_value(dict(record.args), checksum_addresses=True), record.tx_hash[:10])
    return False


async def _listing_by_reservation(ctx: IndexContext, reservation_id: int) -> Listing | None:
    return (
        await ctx.db.execute(select(Listing).where(Listing.reservation_id == int(reservation_id)))
    ).scalars().first()


async def _listing_for_reserved(ctx: IndexContext, customer: str, listing_ref: str) -> Listing | None:
    """The draft listing this `reserve` was signed for: `listingRef` = sha256(listing id)."""
    owner = await ctx.user_by_address(customer)
    q = select(Listing).where(Listing.reservation_id.is_(None))
    if owner is not None:
        q = q.where(Listing.owner_id == owner.id)
    for row in (await ctx.db.execute(q)).scalars().all():
        if compute_listing_ref(row.id) == listing_ref:
            return row
    return None


async def _on_reserved(ctx: IndexContext, record: EventRecord) -> bool:
    """Capital is now locked in the vault: the draft listing goes public."""
    a = record.args
    res_id = int(a["id"])
    if await _listing_by_reservation(ctx, res_id) is not None:
        return False  # already applied
    ref = _hex32(a["listingRef"])
    listing = await _listing_for_reserved(ctx, a["customer"], ref)
    if listing is None:
        log.info("Reserved %s has no matching listing (ref=%s)", res_id, ref[:12])
        return False
    asset = await ctx.asset_by_address(a["token"])
    listing.reservation_id = res_id
    listing.reserved_amount = _dec(int(a["amount"]), asset)
    listing.reserve_tx = record.tx_hash
    if listing.status is ListingStatus.draft:
        listing.status = ListingStatus.active
    await ctx.db.flush()
    return True


async def _on_released(ctx: IndexContext, record: EventRecord) -> bool:
    a = record.args
    listing = await _listing_by_reservation(ctx, int(a["id"]))
    if listing is None:
        return False
    asset = await ctx.asset_by_address(a["token"])
    remaining = _dec(int(a["remaining"]), asset)
    if listing.reserved_amount == remaining and listing.release_tx == record.tx_hash:
        return False
    listing.reserved_amount = remaining
    listing.release_tx = record.tx_hash
    await ctx.db.flush()
    return True


async def _on_reservation_drawn(ctx: IndexContext, record: EventRecord) -> bool:
    """An agreement took its principal out of the reservation."""
    a = record.args
    listing = await _listing_by_reservation(ctx, int(a["id"]))
    if listing is None:
        return False
    asset = await ctx.db.get(Asset, listing.base_asset_id) if listing.base_asset_id else None
    remaining = _dec(int(a["remaining"]), asset)
    if listing.reserved_amount == remaining:
        return False
    listing.reserved_amount = remaining
    await ctx.db.flush()
    return True


ADMIN_ONLY_EVENTS = frozenset({
    "RouterChangeProposed", "RouterChangeCancelled", "OwnershipTransferStarted", "OwnershipTransferred", "Initialized",
})


async def apply_event(ctx: IndexContext, record: EventRecord, *, actor_user_id: uuid.UUID | None = None) -> bool:
    """Apply one vault event; returns True when it changed something (False = already applied / ignored)."""
    if record.removed:
        return False
    vault = ctx.vault_address
    if vault and record.address and record.address.lower() != vault:
        return False
    name = record.name
    if name in ("Proposed", "Opened"):
        return await _on_created(ctx, record, actor=actor_user_id)
    if name == "Activated":
        return await _on_activated(ctx, record, actor=actor_user_id)
    if name == "Cancelled":
        return await _on_cancelled(ctx, record, actor=actor_user_id)
    if name == "Traded":
        return await _on_traded(ctx, record, actor=actor_user_id)
    if name == "Settled":
        return await _on_settled(ctx, record, actor=actor_user_id)
    if name == "Unliquidated":
        return await _on_unliquidated(ctx, record)
    if name == "Claimed":
        return await _on_claimed(ctx, record)
    if name == "Reserved":
        return await _on_reserved(ctx, record)
    if name == "Released":
        return await _on_released(ctx, record)
    if name == "ReservationDrawn":
        return await _on_reservation_drawn(ctx, record)
    if name == "TokenSet":
        return await _on_token_set(ctx, record)
    if name == "ConfigChanged":
        return await _on_config_changed(ctx, record)
    if name == "Upgraded":
        return await _on_upgraded(ctx, record)
    if name in ADMIN_ONLY_EVENTS:
        return await _on_admin_event(ctx, record)
    log.debug("vault event %s ignored (%s:%s)", name, record.tx_hash[:10], record.log_index)
    return False


# --- receipts: apply_tx_result / finalize_receipt / pending tracker (§4.3–4.6) -----------------------


async def apply_tx_result(
    ctx: IndexContext, pending: PendingTransaction, receipt: TxReceiptResult, *, actor_user_id: uuid.UUID | None = None
) -> dict[str, Any]:
    """Apply the vault events of a successful receipt (same path as the indexer); `{applied, events}`."""
    events: list[dict[str, Any]] = []
    applied = 0
    vault = ctx.vault_address
    for rec in sorted(receipt.events, key=lambda r: r.log_index):
        if vault and rec.address and rec.address.lower() != vault:
            continue
        events.append(event_out(rec))
        if await apply_event(ctx, rec, actor_user_id=actor_user_id):
            applied += 1
    return {"applied": applied, "events": events}


def _receipt_matches(pending: PendingTransaction, receipt: TxReceiptResult) -> str | None:
    """None when the receipt is the transaction we built; otherwise a short reason."""
    if not same(receipt.from_address, pending.from_address):
        return f"from {short(receipt.from_address)} != {short(pending.from_address)}"
    if not same(receipt.to_address, pending.to_address):
        return f"to {short(str(receipt.to_address))} != {short(pending.to_address)}"
    if (receipt.input or "0x").lower() != (pending.calldata or "0x").lower():
        return "calldata differs"
    if int(receipt.value) != int(pending.value or 0):
        return f"value {receipt.value} != {int(pending.value or 0)}"
    return None


def _failure_message(kind: PendingTxKind, reason: str) -> str:
    return f"Ağ {kind.value} işleminizi reddetti: {reason}"


async def _notify_tx_failed(ctx: IndexContext, pending: PendingTransaction, reason: str) -> None:
    data = {"pending_tx_id": str(pending.id), "tx_hash": pending.tx_hash, "error_code": pending.error_code}
    category = NotificationCategory.agreement if pending.agreement_id is not None else NotificationCategory.wallet
    if pending.agreement_id is not None:
        data["agreement_id"] = str(pending.agreement_id)
    if pending.listing_id is not None:
        data["listing_id"] = str(pending.listing_id)
    await notify(ctx.db, pending.user_id, "tx_failed", "İşlem başarısız", _failure_message(pending.kind, reason), data, category=category)


async def finalize_receipt(
    ctx: IndexContext, pending_id: uuid.UUID, receipt: TxReceiptResult, *, actor_user_id: uuid.UUID | None
) -> PendingTransaction:
    """§4.3 step 7: verify the receipt against the pending row, apply its events, record the outcome.

    Re-locks the row (`FOR UPDATE`) briefly; idempotent when the tracker / another request finished first.
    On a receipt mismatch the `failed` status is committed explicitly before `ReceiptMismatchError` (409).
    """
    pending = await ctx.db.get(PendingTransaction, pending_id, with_for_update=True)
    if pending is None:
        raise NotFoundError("pending transaction not found", code="pending_tx_not_found")
    if pending.status is not PendingTxStatus.submitted:
        return pending  # already finalised (idempotent)
    now = now_utc()
    if pending.tx_hash is None:
        pending.tx_hash = receipt.tx_hash.lower()
    mismatch = _receipt_matches(pending, receipt)
    if mismatch is not None:
        pending.status = PendingTxStatus.failed
        pending.error_code = "receipt_mismatch"
        pending.error_message = f"receipt does not match the built transaction: {mismatch}"
        pending.block_number, pending.block_hash = receipt.block_number, receipt.block_hash
        pending.confirmed_at = now
        await ctx.db.commit()
        raise ReceiptMismatchError(
            pending.error_message,
            details={"pending_tx_id": str(pending.id), "tx_hash": receipt.tx_hash, "reason": mismatch},
        )
    pending.block_number, pending.block_hash = receipt.block_number, receipt.block_hash
    pending.confirmed_at = now
    if receipt.status == 1:
        out = await apply_tx_result(ctx, pending, receipt, actor_user_id=actor_user_id)
        pending.status = PendingTxStatus.confirmed
        pending.error_code = pending.error_message = pending.contract_error_code = None
        pending.result = json_safe(
            {
                "events": out["events"],
                "gas_used": receipt.gas_used,
                "effective_gas_price": receipt.effective_gas_price,
                "applied": out["applied"],
            }
        )
    else:
        info = receipt.revert
        try:
            info = await ctx.chain.explain_failure(receipt)
        except ChainError as e:
            log.warning("explain_failure(%s) failed: %s", receipt.tx_hash[:10], e.message)
        if info is None:
            info = abi.decode_revert(None)
        code = abi.error_code_of(info)
        if code == "reverted" and receipt.gas_limit and receipt.gas_used >= receipt.gas_limit:
            code = "out_of_gas"
        message = info.message
        if info.code is not None:
            try:
                message = abi.ERROR_MESSAGES.get(abi.VaultError(int(info.code)), info.message)
            except ValueError:
                message = info.message
        pending.status = PendingTxStatus.failed
        pending.error_code = code
        pending.contract_error_code = int(info.code) if info.code is not None else None
        pending.error_message = message
        pending.result = json_safe(
            {"events": [], "gas_used": receipt.gas_used, "effective_gas_price": receipt.effective_gas_price, "applied": 0,
             "revert": {"selector": info.selector, "name": info.name, "args": _api_value(dict(info.args), checksum_addresses=True)}}
        )
        await _notify_tx_failed(ctx, pending, message)
    await ctx.db.flush()
    return pending


async def expire_pending(ctx: IndexContext, *, limit: int = 200) -> int:
    """`pending` rows past `expires_at` (never sent to the wallet) -> `expired`."""
    now = now_utc()
    rows = (
        await ctx.db.execute(
            select(PendingTransaction)
            .where(PendingTransaction.status == PendingTxStatus.pending, PendingTransaction.expires_at <= now)
            .limit(limit)
        )
    ).scalars().all()
    for row in rows:
        row.status = PendingTxStatus.expired
        row.error_code = "expired"
        row.error_message = "işlem süresi içinde imzalanmadı"
    if rows:
        await ctx.db.flush()
    return len(rows)


async def track_submitted(ctx: IndexContext, *, limit: int = 25) -> int:
    """§4.6/2: `submitted` rows the API did not see through -> receipt -> `finalize_receipt`; unknown to the
    network for `submitted_tx_timeout_minutes` -> `failed(not_included)`. Returns the number of rows settled."""
    now = now_utc()
    rows = (
        await ctx.db.execute(
            select(PendingTransaction)
            .where(
                PendingTransaction.status == PendingTxStatus.submitted,
                PendingTransaction.tx_hash.is_not(None),
                PendingTransaction.submitted_at <= now - timedelta(seconds=SUBMITTED_STALE_SECONDS),
            )
            .order_by(PendingTransaction.submitted_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    done = 0
    timeout = timedelta(minutes=int(ctx.settings.submitted_tx_timeout_minutes))
    for row in rows:
        try:
            receipt = await ctx.chain.get_receipt(row.tx_hash)
        except ChainError as e:
            log.warning("track %s: get_receipt failed: %s", row.tx_hash[:10], e.message)
            continue
        if receipt is not None:
            try:
                await finalize_receipt(ctx, row.id, receipt, actor_user_id=row.user_id)
            except ReceiptMismatchError as e:
                log.warning("track %s: %s", row.tx_hash[:10], e.message)
            done += 1
            continue
        try:
            tx = await ctx.chain.get_transaction(row.tx_hash)
        except ChainError as e:
            log.warning("track %s: get_transaction failed: %s", row.tx_hash[:10], e.message)
            continue
        if tx is None and row.submitted_at is not None and row.submitted_at + timeout <= now:
            row.status = PendingTxStatus.failed
            row.error_code = "not_included"
            row.error_message = "işlem ağ tarafından bloğa alınmadı"
            row.confirmed_at = now
            await _notify_tx_failed(ctx, row, row.error_message)
            done += 1
    if done:
        await ctx.db.flush()
    return done


async def _undo_reserve(ctx: IndexContext, pending: PendingTransaction) -> None:
    """A reorged-out `reserve`: the listing goes back to draft (the indexer re-applies if it lands again)."""
    if pending.kind is not PendingTxKind.reserve or pending.listing_id is None:
        return
    listing = await ctx.db.get(Listing, pending.listing_id)
    if listing is None or listing.reserve_tx != pending.tx_hash:
        return
    listing.reservation_id = None
    listing.reserved_amount = None
    listing.reserve_tx = None
    if listing.status is ListingStatus.active:
        listing.status = ListingStatus.draft


async def recheck_recent_confirmed(ctx: IndexContext, *, limit: int = 100) -> int:
    """§4.6/3: confirmed rows near the head whose block hash changed -> re-read the receipt or `failed(reorged)`."""
    try:
        latest = await ctx.chain.latest_block()
    except ChainError as e:
        log.warning("recheck: latest_block failed: %s", e.message)
        return 0
    floor = latest - max(1, int(ctx.settings.confirmations)) * RECHECK_FACTOR
    rows = (
        await ctx.db.execute(
            select(PendingTransaction)
            .where(
                PendingTransaction.status == PendingTxStatus.confirmed,
                PendingTransaction.block_number.is_not(None),
                PendingTransaction.block_number >= floor,
                PendingTransaction.block_hash.is_not(None),
            )
            .order_by(PendingTransaction.block_number.desc())
            .limit(limit)
        )
    ).scalars().all()
    hashes: dict[int, str | None] = {}
    changed = 0
    for row in rows:
        n = int(row.block_number)
        if n not in hashes:
            try:
                hashes[n] = (await ctx.chain.get_block(n)).hash
            except ChainError:
                hashes[n] = None
        h = hashes[n]
        if h is None or h.lower() == str(row.block_hash).lower():
            continue
        try:
            receipt = await ctx.chain.get_receipt(row.tx_hash)
        except ChainError:
            continue
        if receipt is not None:
            row.block_number, row.block_hash = receipt.block_number, receipt.block_hash
            log.warning("tx %s moved to block %s after a reorg", row.tx_hash[:10], receipt.block_number)
        else:
            row.status = PendingTxStatus.failed
            row.error_code = "reorged"
            row.error_message = "işlem bir zincir yeniden düzenlemesinde düştü"
            await _undo_reserve(ctx, row)
            log.error("tx %s (%s) reorged out of block %s", row.tx_hash[:10], row.kind.value, n)
        changed += 1
    if changed:
        await ctx.db.flush()
    return changed


# --- indexer loop -----------------------------------------------------------------------------------


async def _state(db: AsyncSession, key: str) -> IndexerState:
    row = await db.get(IndexerState, key)
    if row is None:
        row = IndexerState(key=key)
        db.add(row)
        await db.flush()
    return row


def _deployment_start_block(settings: Settings) -> int | None:
    """`blockNumber` of the deployments JSON (01-kontrat-spec §9.4) when the file exists."""
    path = Path(settings.deployments_file)
    if not path.is_absolute():
        path = BACKEND_ROOT / path
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    n = data.get("blockNumber") if isinstance(data, dict) else None
    return int(n) if n else None


async def _record_failure(ctx: IndexContext, rec: EventRecord, exc: BaseException) -> FailedEvent:
    row = (
        await ctx.db.execute(
            select(FailedEvent).where(FailedEvent.tx_hash == rec.tx_hash, FailedEvent.log_index == int(rec.log_index))
        )
    ).scalar_one_or_none()
    now = now_utc()
    error = f"{type(exc).__name__}: {str(exc)[:500]}"
    if row is None:
        row = FailedEvent(
            tx_hash=rec.tx_hash, log_index=int(rec.log_index), block_number=int(rec.block_number),
            event_name=rec.name, args=_args_to_json(rec), attempts=1, error=error,
        )
        ctx.db.add(row)
    else:
        row.attempts = int(row.attempts or 0) + 1
        row.error = error
        row.resolved_at = None
        row.block_number = int(rec.block_number)
    row.next_retry_at = now + timedelta(seconds=min(2 ** int(row.attempts), MAX_RETRY_BACKOFF))
    await ctx.db.flush()
    ctx.failed_keys.add((rec.tx_hash, int(rec.log_index)))
    level = logging.ERROR if row.attempts >= ALERT_ATTEMPTS else logging.WARNING
    log.log(
        level, "%sindexer: event %s %s:%s failed (attempt %d): %s",
        "alert " if row.attempts >= ALERT_ATTEMPTS else "", rec.name, rec.tx_hash[:10], rec.log_index, row.attempts, error,
        exc_info=row.attempts == 1,
    )
    return row


async def _mark_resolved(ctx: IndexContext, rec: EventRecord) -> None:
    key = (rec.tx_hash, int(rec.log_index))
    if key not in ctx.failed_keys:
        return
    row = (
        await ctx.db.execute(
            select(FailedEvent).where(FailedEvent.tx_hash == rec.tx_hash, FailedEvent.log_index == int(rec.log_index))
        )
    ).scalar_one_or_none()
    if row is not None and row.resolved_at is None:
        row.resolved_at = now_utc()
        await ctx.db.flush()
    ctx.failed_keys.discard(key)


async def _apply_guarded(ctx: IndexContext, rec: EventRecord, out: IndexerRunResult) -> bool:
    """`apply_event` inside a savepoint. Returns False when the window must stop here (retry in place)."""
    out.events_seen += 1
    try:
        async with ctx.db.begin_nested():
            if await apply_event(ctx, rec):
                out.events_applied += 1
    except Exception as e:  # noqa: BLE001 - recorded in failed_events; never lost
        ctx.forget_agreements()
        out.events_failed += 1
        out.errors.append(f"{rec.name} {rec.tx_hash[:10]}:{rec.log_index}: {type(e).__name__}: {str(e)[:160]}")
        row = await _record_failure(ctx, rec, e)
        return row.attempts >= INLINE_RETRIES  # give up in place -> the backoff retry takes over, cursor moves on
    await _mark_resolved(ctx, rec)
    return True


async def retry_failed_events(ctx: IndexContext, *, limit: int = 20) -> int:
    """Re-apply due `failed_events` rows; resolved on success, backed off again on failure."""
    now = now_utc()
    rows = (
        await ctx.db.execute(
            select(FailedEvent)
            .where(FailedEvent.resolved_at.is_(None), FailedEvent.next_retry_at <= now)
            .order_by(FailedEvent.block_number.asc(), FailedEvent.log_index.asc())
            .limit(limit)
        )
    ).scalars().all()
    ok = 0
    for row in rows:
        rec = _record_from_failed(row)
        try:
            async with ctx.db.begin_nested():
                await apply_event(ctx, rec)
        except Exception as e:  # noqa: BLE001
            ctx.forget_agreements()
            await _record_failure(ctx, rec, e)
            continue
        row.resolved_at = now_utc()
        ctx.failed_keys.discard((row.tx_hash, int(row.log_index)))
        ok += 1
    if ok:
        await ctx.db.flush()
    return ok


async def _known_block_hashes(db: AsyncSession, block_number: int) -> set[str]:
    rows = (
        await db.execute(
            select(PendingTransaction.block_hash).where(
                PendingTransaction.block_number == int(block_number), PendingTransaction.block_hash.is_not(None)
            )
        )
    ).scalars().all()
    return {str(h).lower() for h in rows}


async def _rewind_to(ctx: IndexContext, ancestor: int) -> None:
    """Undo mirror rows written above `ancestor` (§5.4): trades are deleted (re-indexed), agreement tx fields
    whose receipts vanished are cleared and the status re-synced from the chain, confirmed pendings re-checked."""
    db = ctx.db
    await db.execute(delete(Trade).where(Trade.block_number > int(ancestor)))
    ctx.forget_agreements()
    ags = (
        await db.execute(select(Agreement).where(Agreement.last_event_block_number > int(ancestor)))
    ).scalars().all()
    for ag in ags:
        for name in ("settle_tx", "cancel_tx", "activate_tx", "created_tx"):
            h = getattr(ag, name)
            if not h:
                continue
            try:
                receipt = await ctx.chain.get_receipt(h)
            except ChainError:
                continue
            if receipt is not None:
                continue
            setattr(ag, name, None)
            if name == "created_tx":
                ag.onchain_id = None
                if ag.status in OPEN_STATUSES:
                    ag.status = AgreementStatus.draft
                _clear_balances(ag)
        ag.last_event_block_number = int(ancestor)
        await db.flush()
        if ag.onchain_id is not None:
            try:
                await refresh_agreement_from_chain(ctx, ag, snapshot=False, alerts=False)
            except ChainError as e:
                log.warning("rewind: refresh %s failed: %s", ag.id, e.message)
    pendings = (
        await db.execute(
            select(PendingTransaction).where(
                PendingTransaction.status == PendingTxStatus.confirmed, PendingTransaction.block_number > int(ancestor)
            )
        )
    ).scalars().all()
    for row in pendings:
        try:
            receipt = await ctx.chain.get_receipt(row.tx_hash)
        except ChainError:
            continue
        if receipt is not None:
            row.block_number, row.block_hash = receipt.block_number, receipt.block_hash
            continue
        row.status = PendingTxStatus.failed
        row.error_code = "reorged"
        row.error_message = "işlem bir zincir yeniden düzenlemesinde düştü"
        await _undo_reserve(ctx, row)
    await db.flush()


async def _block_hash(ctx: IndexContext, number: int) -> str | None:
    if number < 0:
        return None
    try:
        return (await ctx.chain.get_block(int(number))).hash
    except ChainError:
        return None


async def _check_reorg(ctx: IndexContext, state: IndexerState, out: IndexerRunResult) -> bool:
    """§5.4. Returns True when this tick must be skipped (reorg deeper than confirmations*8: manual action)."""
    head = int(state.block_number)
    current = await _block_hash(ctx, head)
    if current is not None and current.lower() == str(state.last_block_hash).lower():
        return False
    confirmations = max(1, int(ctx.settings.confirmations))
    floor = head - confirmations * REORG_MAX_FACTOR
    ancestor: int | None = None
    deepest_mismatch = head
    for n in range(head - 1, max(floor, 0) - 1, -1):
        known = await _known_block_hashes(ctx.db, n)
        if not known:
            continue
        h = await _block_hash(ctx, n)
        if h is not None and h.lower() in known:
            ancestor = n
            break
        deepest_mismatch = n
    if ancestor is None:
        ancestor = max(0, deepest_mismatch - confirmations)
        if ancestor < floor:
            log.error(
                "alert indexer: reorg deeper than %d blocks at %s (cursor %s); skipping — manual reset needed",
                confirmations * REORG_MAX_FACTOR, deepest_mismatch, head,
            )
            out.errors.append(f"reorg deeper than {confirmations * REORG_MAX_FACTOR} blocks")
            return True
    await _rewind_to(ctx, ancestor)
    state.block_number = ancestor
    state.last_block_hash = await _block_hash(ctx, ancestor)
    out.reorg_depth = head - ancestor
    log.error("indexer: reorg detected at block %s (hash %s -> %s); rewound %d blocks to %s",
              head, str(state.last_block_hash)[:12], str(current)[:12], out.reorg_depth, ancestor)
    return False


async def run_indexer_once(db: AsyncSession, chain: Any, settings: Settings) -> IndexerRunResult:
    """One indexer tick (worker: every `settings.indexer_poll_seconds`). Flush only."""
    ctx = IndexContext(db, settings, chain)
    out = IndexerRunResult()
    vault = ctx.vault_address
    if not vault:
        out.skipped, out.reason = True, "vault_address not configured"
        return out
    state = await _state(db, VAULT_EVENTS_KEY)
    ctx.failed_keys = {
        (h, int(i)) for h, i in (
            await db.execute(select(FailedEvent.tx_hash, FailedEvent.log_index).where(FailedEvent.resolved_at.is_(None)))
        ).all()
    }
    latest = int(await chain.latest_block())
    out.latest_block = latest
    safe_head = latest - max(0, int(settings.confirmations))
    if state.block_number is None:
        start = settings.indexer_start_block or _deployment_start_block(settings) or max(0, latest - FIRST_RUN_LOOKBACK)
    else:
        if state.last_block_hash and await _check_reorg(ctx, state, out):
            out.skipped, out.reason = True, "reorg too deep"
            state.updated_at = now_utc()
            await db.flush()
            return out
        start = int(state.block_number) + 1
    out.from_block = start
    window = max(1, int(settings.indexer_block_window))
    windows = 0
    while start <= safe_head and windows < max(1, int(settings.indexer_max_windows_per_run)):
        end = min(start + window - 1, safe_head)
        logs = await chain.get_logs(start, end, vault)
        logs.sort(key=lambda r: (r.block_number, r.log_index))
        stalled_at: int | None = None
        for rec in logs:
            if not await _apply_guarded(ctx, rec, out):
                stalled_at = rec.block_number
                break
        if stalled_at is not None:
            # retry the failing event in place next tick: cursor stops just before its block
            end = stalled_at - 1
            if end < start:
                break
        state.block_number = end
        state.last_block_hash = await _block_hash(ctx, end)
        state.updated_at = now_utc()
        await db.flush()
        out.to_block = end
        windows += 1
        start = end + 1
        if stalled_at is not None:
            break
    out.windows = windows
    if out.to_block is None:
        out.to_block = state.block_number
    try:
        out.failed_retried = await retry_failed_events(ctx)
    except Exception as e:  # noqa: BLE001
        log.exception("indexer: retry_failed_events failed")
        out.errors.append(f"retry_failed_events: {type(e).__name__}: {str(e)[:160]}")
    state.updated_at = now_utc()
    await db.flush()
    if out.events_seen or out.errors or out.reorg_depth:
        log.info("indexer: %s", out.as_dict())
    return out


# --- reconciler ---------------------------------------------------------------------------------------


async def _alert_once(ctx: IndexContext, ag: Agreement, type_: str, title: str, body: str, data: dict[str, Any]) -> int:
    n = 0
    for uid in (ag.customer_id, ag.trader_id):
        if await _already_notified(ctx.db, uid, type_, ag.id):
            continue
        await notify(
            ctx.db, uid, type_, title, body, {"agreement_id": str(ag.id), "onchain_id": ag.onchain_id, **data},
            category=NotificationCategory.agreement,
        )
        n += 1
    return n


async def check_alerts(ctx: IndexContext, ag: Agreement, value: Decimal, now: datetime | None = None) -> int:
    """Drawdown (80 % / 100 % of max_drawdown) and expiry (24 h / 1 h / overdue) notifications, once each."""
    now = now or now_utc()
    alerts = 0
    base = ag.base_asset
    if ag.max_drawdown_bps < money.BPS_DENOM and value < ag.principal and ag.principal > 0:
        dd_bps = int((ag.principal - value) * money.BPS_DENOM / ag.principal)
        pct = money.format_amount(Decimal(dd_bps) / 100, 2)
        limit_pct = f"{ag.max_drawdown_bps / 100:g}"
        data = {"drawdown_bps": dd_bps, "max_drawdown_bps": ag.max_drawdown_bps, "value": str(value)}
        if dd_bps >= ag.max_drawdown_bps:
            alerts += await _alert_once(
                ctx, ag, "drawdown_100", "Azami kayıp sınırına ulaşıldı",
                f"Portföy %{pct} düştü (sınır %{limit_pct}). Kontrat artık yeni işlemleri reddediyor.", data,
            )
        elif dd_bps * 100 >= ag.max_drawdown_bps * DRAWDOWN_WARN_PCT:
            alerts += await _alert_once(
                ctx, ag, "drawdown_80", "Kayıp uyarısı",
                f"Portföy %{pct} düştü — %{limit_pct} azami kayıp sınırının %{DRAWDOWN_WARN_PCT}'i aşıldı.", data,
            )
    if ag.end_time is not None:
        remaining = ag.end_time - now
        data = {"end_time": ag.end_time.isoformat(), "value": str(value)}
        if remaining <= timedelta(0):
            alerts += await _alert_once(
                ctx, ag, "settle_now", "Sözleşme süresi doldu",
                f"Süre bitti — sözleşmeyi kapatın. Güncel değer: {_fmt_dec(value, base)} {base.symbol}.", data,
            )
        elif remaining <= timedelta(hours=1):
            alerts += await _alert_once(
                ctx, ag, "expiry_1h", "Sözleşme 1 saat içinde bitiyor",
                "Süre dolduğunda herkes sözleşmeyi kapatabilir.", data,
            )
        elif remaining <= timedelta(hours=24):
            alerts += await _alert_once(
                ctx, ag, "expiry_24h", "Sözleşme 24 saat içinde bitiyor",
                "Pozisyonlarınızı gözden geçirin; süre dolunca sözleşme kapatılabilir.", data,
            )
    return alerts


async def sync_status_from_chain(ctx: IndexContext, ag: Agreement, view: AgreementView) -> bool:
    """Bring a mirror row whose events were missed in line with `read_agreement` (no notifications)."""
    target = AgreementStatus.from_onchain(int(view.status))
    if target is None or ag.status is target:
        return False
    log.warning("agreement %s (onchain %s) status %s != chain %s; syncing", ag.id, ag.onchain_id, ag.status.value, target.value)
    base = ag.base_asset
    ag.status = target
    ag.start_time = ts_to_dt(view.start_time) or ag.start_time
    ag.end_time = ts_to_dt(view.end_time) or ag.end_time
    if ag.platform_fee_bps is None:
        ag.platform_fee_bps = int(view.platform_fee_bps)
    ag.vault_address = ag.vault_address or ctx.vault_address
    if target is AgreementStatus.settled:
        ag.final_value = money.from_raw(int(view.final_value), base.decimals)
        ag.trader_fee = money.from_raw(int(view.trader_fee), base.decimals)
        ag.platform_fee = money.from_raw(int(view.platform_fee), base.decimals)
        ag.customer_payout = money.from_raw(int(view.customer_payout), base.decimals)
        ag.profit = max(Decimal("0"), ag.final_value - ag.principal)
        ag.settled_at = ts_to_dt(view.settled_at) or now_utc()
        _set_value(ag, ag.final_value, ag.settled_at)
        try:
            await _upsert_balances(ctx, ag, await ctx.chain.read_balances(int(ag.onchain_id)))
        except ChainError as e:
            log.warning("read_balances(%s) failed during sync: %s", ag.onchain_id, e.message)
    elif target is AgreementStatus.cancelled:
        _clear_balances(ag)
    elif target is AgreementStatus.funded and not ag.balances:
        await _upsert_balances(ctx, ag, [(base.address, money.to_raw(ag.principal, base.decimals))])
    await ctx.db.flush()
    await refresh_trader_stats(ctx.db, ag.trader_id)
    return True


async def refresh_agreement_from_chain(
    ctx: IndexContext, ag: Agreement, *, snapshot: bool = True, alerts: bool = True, now: datetime | None = None
) -> dict[str, Any]:
    """`read_agreement` + `read_balances` + `read_value_in_base` -> mirror update for one agreement."""
    now = now or now_utc()
    result: dict[str, Any] = {"synced": False, "valued": False, "snapshot": False, "alerts": 0}
    if ag.onchain_id is None:
        return result
    await ctx.loaded(ag)
    view = await _chain_agreement(ctx, int(ag.onchain_id))
    if view is None:
        return result
    result["synced"] = await sync_status_from_chain(ctx, ag, view)
    if AgreementStatus.from_onchain(int(view.status)) is not AgreementStatus.active:
        return result
    balances = await ctx.chain.read_balances(int(ag.onchain_id))
    raw_value = int(await ctx.chain.read_value_in_base(int(ag.onchain_id)))
    value = money.from_raw(raw_value, ag.base_asset.decimals)
    await _upsert_balances(ctx, ag, balances)
    _set_value(ag, value, now)
    result["valued"] = True
    if snapshot:
        _snapshot(ctx, ag, value, now)
        result["snapshot"] = True
    if alerts:
        result["alerts"] = await check_alerts(ctx, ag, value, now)
    await ctx.db.flush()
    return result


async def run_reconcile_once(db: AsyncSession, chain: Any, settings: Settings, *, limit: int = 200) -> ReconcileResult:
    """One reconciler tick (worker: every `settings.reconcile_seconds`). Flush only."""
    ctx = IndexContext(db, settings, chain)
    out = ReconcileResult()
    if not ctx.vault_address:
        out.skipped = True
        return out
    rows = (
        await db.execute(
            select(Agreement)
            .where(Agreement.status.in_(list(OPEN_STATUSES)), Agreement.onchain_id.is_not(None))
            .order_by(Agreement.value_updated_at.asc().nulls_first())
            .limit(limit)
        )
    ).scalars().all()
    now = now_utc()
    for ag in rows:
        out.checked += 1
        try:
            async with db.begin_nested():
                r = await refresh_agreement_from_chain(ctx, ag, now=now)
        except Exception as e:  # noqa: BLE001 - keep going with the other agreements
            ctx.forget_agreements()
            log.exception("reconcile: agreement %s failed", ag.id)
            out.errors.append(f"{ag.id}: {type(e).__name__}: {str(e)[:160]}")
            continue
        out.status_synced += int(bool(r["synced"]))
        out.valued += int(bool(r["valued"]))
        out.snapshots += int(bool(r["snapshot"]))
        out.alerts += int(r["alerts"])
    state = await _state(db, RECONCILE_KEY)
    state.updated_at = now
    await db.flush()
    if out.checked:
        log.info("reconcile: %s", out.as_dict())
    return out


__all__ = [
    "ADMIN_ONLY_EVENTS",
    "INLINE_RETRIES",
    "RECONCILE_KEY",
    "VAULT_CONFIG_KEY",
    "VAULT_EVENTS_KEY",
    "IndexContext",
    "IndexerRunResult",
    "ReconcileResult",
    "apply_event",
    "apply_tx_result",
    "check_alerts",
    "event_out",
    "expire_pending",
    "finalize_receipt",
    "recheck_recent_confirmed",
    "refresh_agreement_from_chain",
    "retry_failed_events",
    "run_indexer_once",
    "run_reconcile_once",
    "sync_status_from_chain",
    "track_submitted",
]
