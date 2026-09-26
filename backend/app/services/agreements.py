"""Agreements (Sözleşme) lifecycle: role-aware list/get with TL equivalents, the action rules and the
unsigned transaction builders (03-backend-tasarim §4.2, §6.1; 02-api-sozlesme §3.1, §8.1).

State machine (contract = source of truth, rows = indexed mirror):

    draft ──open / open_reserved (customer)──▶ funded ──accept (trader)──▶ active ──settle──▶ settled ──claim──▶
    draft ──propose (trader)─▶ proposed ──fund / fund_reserved (customer)─▶ active
    proposed ──cancel (proposer)──▶ cancelled      funded ──cancel (customer|trader)──▶ cancelled

Every builder dry-runs through the chain gateway with `from` = the caller, records a `pending_transactions`
row and hands the calldata back (`UnsignedTxOut`); nothing here signs or submits. The reservation choice is
explicit (02-api §3.1): `open`/`fund` vs `open_reserved`/`fund_reserved` — no silent fallback.

Stable exports relied on by the indexer / wallet slices (03 §6.1): OPEN_STATUSES, CLOSED_STATUSES, now_utc,
ts_to_dt, json_safe, compute_listing_ref, listing_ref_bytes, principal_raw, build_terms, record_pending,
unsigned_out, party_out, asset_brief, is_expired, party_role, available_actions.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import ChainError, ForbiddenError, NotFoundError, StateError, ValidationError
from app.models import (
    Agreement,
    AgreementStatus,
    AgreementValueSnapshot,
    Asset,
    Listing,
    PendingTransaction,
    PendingTxKind,
    PendingTxStatus,
    Trade,
    User,
    UserRole,
)
from app.schemas.agreements import (
    STATUS_GROUPS,
    AgreementOut,
    AssetBriefOut,
    BalanceOut,
    PartyOut,
    PendingTxBriefOut,
    TlOut,
    ValueHistoryOut,
    ValuePointOut,
    ValueRange,
)
from app.schemas.tx import PreStepOut, UnsignedTxOut
from app.services import fx
from app.services.chain import amounts as money
from app.services.chain.addresses import checksum, same, short
from app.services.chain.gateway import ChainGateway
from app.services.chain.types import PreStep, Terms, UnsignedTx

log = logging.getLogger(__name__)

OPEN_STATUSES = (AgreementStatus.proposed, AgreementStatus.funded, AgreementStatus.active)
CLOSED_STATUSES = (AgreementStatus.settled, AgreementStatus.cancelled, AgreementStatus.failed)
ACTIONS = ("propose", "open", "open_reserved", "fund", "fund_reserved", "accept", "cancel", "settle", "claim")
SETTLE_GRACE = timedelta(days=7)  # 01-kontrat-spec KEEPER_GRACE: anyone may settle after end_time + 7 days
_RANGE_DELTA: dict[str, timedelta | None] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
    "all": None,
}
ZERO = Decimal("0")


# --- small helpers ------------------------------------------------------------------------------


def now_utc() -> datetime:
    return datetime.now(UTC)


def ts_to_dt(ts: int | None) -> datetime | None:
    """On-chain unix seconds -> aware datetime (0 means "unset" on-chain)."""
    if not ts:
        return None
    return datetime.fromtimestamp(int(ts), tz=UTC)


def json_safe(value: Any) -> Any:
    """JSONB / response friendly copy: Decimal, UUID, datetime, bytes -> str/hex; tuples -> lists."""
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [json_safe(v) for v in value]
    if isinstance(value, bool | int | float | str) or value is None:
        return value
    if isinstance(value, bytes):
        return "0x" + value.hex()
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def compute_listing_ref(ref_id: uuid.UUID | str) -> str:
    """`listing_ref` = sha256 of the off-chain offer (or agreement / listing) id, hex = bytes32."""
    return hashlib.sha256(str(ref_id).encode("utf-8")).hexdigest()


def listing_ref_bytes(agreement: Agreement) -> bytes:
    """32 raw bytes for `Terms.listing_ref`; repairs a missing/invalid hex value on the row."""
    raw = (agreement.listing_ref or "").strip().lower().removeprefix("0x")
    try:
        data = bytes.fromhex(raw)
    except ValueError:
        data = b""
    if len(data) != 32:
        fixed = compute_listing_ref(agreement.offer_id or agreement.id)
        agreement.listing_ref = fixed
        data = bytes.fromhex(fixed)
    return data


def principal_raw(agreement: Agreement) -> int:
    dec = agreement.base_asset.decimals
    return money.to_raw(money.quantize(agreement.principal, dec), dec)


def build_terms(agreement: Agreement) -> Terms:
    """On-chain `Terms` from the draft row (addresses from the party users, raw units from Decimal)."""
    return Terms(
        customer=agreement.customer.wallet_address,
        trader=agreement.trader.wallet_address,
        base_token=agreement.base_asset.address,
        principal=principal_raw(agreement),
        duration_seconds=int(agreement.duration_secs),
        commission_bps=int(agreement.commission_bps),
        max_drawdown_bps=int(agreement.max_drawdown_bps),
        listing_ref=listing_ref_bytes(agreement),
    )


def party_role(agreement: Agreement, user: User | None) -> UserRole | None:
    return agreement.party_role(user.id) if user is not None else None


def is_expired(agreement: Agreement, now: datetime | None = None) -> bool:
    return agreement.end_time is not None and (now or now_utc()) >= agreement.end_time


def pnl_bps(value: Decimal | None, principal: Decimal | None) -> int | None:
    if value is None or principal is None or principal <= 0:
        return None
    return int(((Decimal(value) - Decimal(principal)) * 10_000 / Decimal(principal)).to_integral_value())


def drawdown_bps(value: Decimal | None, principal: Decimal) -> int:
    """Current drawdown from principal in bps (0 when at or above principal)."""
    if value is None or principal <= 0 or value >= principal:
        return 0
    return int(((principal - Decimal(value)) * 10_000 / principal).to_integral_value())


def agreement_label(agreement: Agreement) -> str:
    """"#12" once on-chain, otherwise the short row id — for descriptions / notifications."""
    if agreement.onchain_id is not None:
        return f"#{int(agreement.onchain_id)}"
    return f"#{str(agreement.id)[:8]}"


def fmt_principal(agreement: Agreement) -> str:
    base = agreement.base_asset
    return f"{money.format_amount(agreement.principal, base.decimals)} {base.symbol}"


# --- lookups ------------------------------------------------------------------------------------


async def get_agreement_row(db: AsyncSession, agreement_id: uuid.UUID) -> Agreement:
    ag = await db.get(Agreement, agreement_id)
    if ag is None:
        raise NotFoundError("Agreement not found", code="agreement_not_found")
    return ag


async def get_agreement_for(db: AsyncSession, agreement_id: uuid.UUID, user: User) -> Agreement:
    """Agreement the user may see: a party (customer/trader) or an admin. Others get 403."""
    ag = await get_agreement_row(db, agreement_id)
    if party_role(ag, user) is None and not user.is_admin:
        raise ForbiddenError("You are not a party of this agreement", code="not_party")
    return ag


def parse_status_filter(status: str | None) -> list[AgreementStatus] | None:
    if status is None or status == "":
        return None
    if status in STATUS_GROUPS:
        return list(OPEN_STATUSES) if status == "open" else list(CLOSED_STATUSES)
    try:
        return [AgreementStatus(status)]
    except ValueError as e:
        raise ValidationError(
            f"unknown status {status!r}", code="invalid_status", details={"allowed": [*AgreementStatus, *STATUS_GROUPS]}
        ) from e


async def list_agreements(
    db: AsyncSession,
    user: User,
    *,
    role: UserRole | None = None,
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Agreement], int]:
    """Agreements the user is party to. `role` narrows to the side they play (customer|trader)."""
    if role is UserRole.customer:
        where = [Agreement.customer_id == user.id]
    elif role is UserRole.trader:
        where = [Agreement.trader_id == user.id]
    else:
        where = [or_(Agreement.customer_id == user.id, Agreement.trader_id == user.id)]
    statuses = parse_status_filter(status)
    if statuses:
        where.append(Agreement.status.in_(statuses))
    total = int((await db.execute(select(func.count()).select_from(Agreement).where(*where))).scalar_one())
    rows = (
        await db.execute(
            select(Agreement)
            .where(*where)
            .order_by(Agreement.updated_at.desc(), Agreement.created_at.desc(), Agreement.id)
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return list(rows), total


async def latest_pending(db: AsyncSession, agreement: Agreement, user: User) -> PendingTransaction | None:
    """The viewer's most recent unfinished (pending / submitted) transaction for this agreement."""
    q = (
        select(PendingTransaction)
        .where(
            PendingTransaction.agreement_id == agreement.id,
            PendingTransaction.user_id == user.id,
            PendingTransaction.status.in_([PendingTxStatus.pending, PendingTxStatus.submitted]),
        )
        .order_by(PendingTransaction.created_at.desc())
        .limit(1)
    )
    return (await db.execute(q)).scalar_one_or_none()


# --- reservation / claim context ----------------------------------------------------------------


async def listing_reservation(db: AsyncSession, agreement: Agreement) -> tuple[int | None, Decimal | None]:
    """`(reservation_id, remaining)` of the capital listing backing this agreement; `(None, None)` when the
    agreement has no listing (e.g. a direct offer from a chat) or the listing never deposited."""
    if agreement.listing_id is None:
        return None, None
    listing = await db.get(Listing, agreement.listing_id)
    return _reservation_of(listing)


def _reservation_of(listing: Listing | None) -> tuple[int | None, Decimal | None]:
    if listing is None or listing.reservation_id is None:
        return None, None
    return int(listing.reservation_id), Decimal(listing.reserved_amount or 0)


def reservation_covers(reservation: tuple[int | None, Decimal | None], principal: Decimal) -> bool:
    res_id, remaining = reservation
    return res_id is not None and remaining is not None and remaining >= Decimal(principal)


def claimable_asset_ids(agreement: Agreement) -> list[uuid.UUID]:
    """Assets `claim(id, token)` can still withdraw: settled agreement with a positive mirrored balance."""
    if agreement.status is not AgreementStatus.settled:
        return []
    return [b.asset_id for b in agreement.balances if b.balance is not None and b.balance > 0]


# --- action rules -------------------------------------------------------------------------------


def available_actions(
    agreement: Agreement,
    user: User | None,
    now: datetime | None = None,
    *,
    reservation_covers: bool = False,
    claimable: bool = False,
) -> list[str]:
    """Which `POST /agreements/{id}/tx/{action}` calls the viewer may make right now (02-api §8.1).

    `reservation_covers`: the listing's reservation still holds >= principal (then `open_reserved` /
    `fund_reserved` replace `open` / `fund`); `claimable`: settled with a positive balance left."""
    role = party_role(agreement, user)
    st = agreement.status
    now = now or now_utc()
    if st is AgreementStatus.draft:
        if role is UserRole.customer:
            return ["open_reserved" if reservation_covers else "open"]
        if role is UserRole.trader:
            return ["propose"]
        return []
    if st is AgreementStatus.proposed:
        if role is UserRole.customer:
            fund = "fund_reserved" if reservation_covers else "fund"
            return [fund] + (["cancel"] if agreement.proposer_role is UserRole.customer else [])
        if role is UserRole.trader:
            return ["cancel"] if agreement.proposer_role is UserRole.trader else []
        return []
    if st is AgreementStatus.funded:
        if role is UserRole.trader:
            return ["accept", "cancel"]
        if role is UserRole.customer:
            return ["cancel"]
        return []
    if st is AgreementStatus.active:
        expired = is_expired(agreement, now)
        if role is UserRole.trader:
            return (["trade"] if not expired else []) + ["settle"]
        if role is UserRole.customer:
            return ["settle"]
        if agreement.end_time is None:
            return []
        grace = timedelta(0) if getattr(user, "is_admin", False) else SETTLE_GRACE
        return ["settle"] if now >= agreement.end_time + grace else []
    if st is AgreementStatus.settled:
        return ["claim"] if role is UserRole.customer and claimable else []
    return []


def _require_status(agreement: Agreement, *allowed: AgreementStatus, action: str) -> None:
    if agreement.status not in allowed:
        raise StateError(
            f"cannot {action}: agreement is {agreement.status.value} (needs {' | '.join(s.value for s in allowed)})",
            code="invalid_state",
            details={"status": agreement.status.value, "action": action},
        )


def _require_role(role: UserRole | None, *allowed: UserRole, action: str) -> None:
    if role not in allowed:
        raise ForbiddenError(
            f"only the {' or '.join(r.value for r in allowed)} may {action} this agreement",
            code="wrong_party",
            details={"action": action},
        )


def _require_onchain(agreement: Agreement) -> int:
    if agreement.onchain_id is None:
        raise StateError("agreement is not on-chain yet", code="not_onchain")
    return int(agreement.onchain_id)


def _require_reserved(action: str, reservation: tuple[int | None, Decimal | None], principal: Decimal) -> int:
    """`open_reserved` / `fund_reserved` need a reservation that still covers the principal (409 otherwise)."""
    res_id, remaining = reservation
    if not reservation_covers(reservation, principal):
        raise StateError(
            f"cannot {action}: the listing's reservation does not cover the principal",
            code="reservation_insufficient",
            details={
                "reservation_id": res_id,
                "reserved_amount": money.format_amount(remaining) if remaining is not None else None,
                "principal": money.format_amount(principal),
            },
        )
    return int(res_id)  # type: ignore[arg-type]


def _reject_wallet_path(action: str, reservation: tuple[int | None, Decimal | None], principal: Decimal) -> None:
    """`open` / `fund` while the reservation covers the principal -> 409 `use_reserved_action` (02-api §3.1)."""
    if reservation_covers(reservation, principal):
        raise StateError(
            f"the listing's reservation covers the principal; use {action}_reserved",
            code="use_reserved_action",
            details={"action": f"{action}_reserved", "reservation_id": reservation[0]},
        )


async def settle_min_outs(
    chain: ChainGateway, agreement: Agreement, slippage_bps: int
) -> tuple[list[int], list[dict[str, Any]]]:
    """One `min_out` per non-base token in the on-chain `tokens` order (tokens[0] is the base): router quote
    of the full balance back to base minus `slippage_bps`; 0 for empty / unquotable balances (the contract
    then applies its own floors). Uses `previewSettle` when the ABI has it."""
    onchain_id = _require_onchain(agreement)
    view = await chain.read_agreement(onchain_id)
    base = view.terms.base_token
    balances = {t: int(b) for t, b in await chain.read_balances(onchain_id)}
    tokens = [t for t in view.tokens if not same(t, base)]
    preview = None
    try:
        preview = await chain.preview_settle(onchain_id)
    except ChainError as e:
        log.debug("previewSettle unavailable for %s: %s", onchain_id, e.message)
    quotes: dict[str, int] = {}
    if preview is not None:
        p_tokens, p_quotes = list(preview.tokens), list(preview.quotes)
        if len(p_quotes) == len(p_tokens) - 1:  # quotes only for the non-base tokens (tokens[0] is the base)
            p_tokens = p_tokens[1:]
        if len(p_quotes) == len(p_tokens):
            quotes = {t.lower(): int(q) for t, q in zip(p_tokens, p_quotes, strict=True) if not same(t, base)}
    min_outs: list[int] = []
    legs: list[dict[str, Any]] = []
    for token in tokens:
        bal = balances.get(token.lower(), 0)
        quote = quotes.get(token.lower())
        if quote is None:
            quote = 0
            if bal > 0:
                try:
                    quote = int((await chain.quote([token, base], bal)).amount_out)
                except ChainError as e:  # no route: let the contract decide (RouterError), floor 0
                    log.warning("settle quote %s -> %s failed: %s", short(token), short(base), e.message)
        min_out = money.min_out_for_slippage(quote, slippage_bps) if quote > 0 else 0
        min_outs.append(min_out)
        legs.append({"token": checksum(token), "balance": bal, "quote": quote, "min_out": min_out})
    return min_outs, legs


async def approval_pre_steps(
    chain: ChainGateway, owner: str, asset: Asset, spender: str, amount_raw: int
) -> list[PreStep]:
    """`[approve(spender, amount_raw)]` when the current allowance is short, else `[]` (exact amount, K4)."""
    current = await chain.allowance(asset.address, owner, spender)
    if current >= int(amount_raw):
        return []
    return [await chain.build_approve(owner, asset.address, spender, int(amount_raw))]


# --- pending rows / unsigned output -------------------------------------------------------------


async def record_pending(
    db: AsyncSession,
    user: User,
    kind: PendingTxKind,
    agreement: Agreement | None,
    unsigned: UnsignedTx,
    payload: dict[str, Any] | None = None,
    *,
    listing: Listing | None = None,
    description: str | None = None,
) -> PendingTransaction:
    """Persist the calldata handed to the wallet (03 §4.2). `payload` (builder context: trade note / settle
    legs / reservation id) is stored under `payload["context"]`; the gateway summary keys stay at the top
    level. Older `pending` rows of the same (user, kind, agreement|listing) are marked `expired` (superseded)."""
    where = [
        PendingTransaction.user_id == user.id,
        PendingTransaction.kind == kind,
        PendingTransaction.status == PendingTxStatus.pending,
    ]
    if agreement is not None:
        where.append(PendingTransaction.agreement_id == agreement.id)
    elif listing is not None:
        where.append(PendingTransaction.listing_id == listing.id)
    else:
        where.append(PendingTransaction.agreement_id.is_(None))
        where.append(PendingTransaction.listing_id.is_(None))
    stale = (await db.execute(select(PendingTransaction).where(*where))).scalars().all()
    for row in stale:
        row.status = PendingTxStatus.expired
        row.error_code = "expired"
        row.error_message = "superseded by a newer build"
        row.result = {"reason": "superseded"}
    data: dict[str, Any] = {"action": unsigned.action}
    data.update(json_safe(unsigned.summary))
    data["context"] = json_safe(payload or {})
    if description:
        data["description"] = description
    pending = PendingTransaction(
        user_id=user.id,
        kind=kind,
        action=unsigned.action,
        agreement_id=agreement.id if agreement is not None else None,
        listing_id=listing.id if listing is not None else None,
        from_address=user.wallet_address,
        to_address=unsigned.to,
        calldata=(unsigned.data or "0x").lower(),
        value=Decimal(int(unsigned.value)),
        gas=int(unsigned.gas) if unsigned.gas is not None else None,
        chain_id=int(unsigned.chain_id),
        status=PendingTxStatus.pending,
        payload=data,
        expires_at=unsigned.expires_at,
        created_at=now_utc(),
    )
    db.add(pending)
    await db.flush()
    return pending


def pre_step_out(step: PreStep, asset: Asset | None = None) -> PreStepOut:
    """`PreStepOut` (02-api §2.2); `asset` (the token being approved) fills id / symbol / human amount."""
    known = asset is not None and same(asset.address, step.token)
    amount = money.from_raw(step.amount_raw, asset.decimals) if known else None
    description = step.description
    if known:
        description = f"Kasaya {money.format_amount(amount, asset.decimals)} {asset.symbol} harcama izni ver"
    return PreStepOut(
        kind="approve",
        to=checksum(step.to),
        data=step.data,
        value=str(int(step.value)),
        gas=str(int(step.gas)) if step.gas is not None else None,
        description=description,
        spender=checksum(step.spender),
        asset_id=asset.id if known else None,
        symbol=asset.symbol if known else None,
        amount=amount,
        amount_raw=str(int(step.amount_raw)),
    )


def unsigned_out(
    pending: PendingTransaction | None,
    unsigned: UnsignedTx,
    *,
    description: str | None = None,
    asset: Asset | None = None,
) -> UnsignedTxOut:
    """`UnsignedTxOut` (02-api §2.1). `summary` = gateway summary merged with the builder context stored on
    the pending row (settle legs / slippage, trade note, reservation id) — everything the app can show
    before sending. `asset` is the token of the `approve` pre-steps (base asset), when any."""
    payload = dict(pending.payload or {}) if pending is not None else {}
    if description is None:
        description = str(payload.get("description") or "")
    summary = payload or json_safe(unsigned.summary)
    summary = {k: v for k, v in summary.items() if k != "description"}
    return UnsignedTxOut(
        pending_tx_id=pending.id if pending is not None else None,
        kind=PendingTxKind(pending.kind if pending is not None else unsigned.kind),
        action=unsigned.action,
        agreement_id=pending.agreement_id if pending is not None else None,
        listing_id=pending.listing_id if pending is not None else None,
        chain_id=int(unsigned.chain_id),
        from_address=checksum(unsigned.from_address),
        to=checksum(unsigned.to),
        data=unsigned.data or "0x",
        value=str(int(unsigned.value)),
        gas=str(int(unsigned.gas)) if unsigned.gas is not None else None,
        description=description,
        pre_steps=[pre_step_out(s, asset) for s in unsigned.pre_steps],
        summary=summary,
        expires_at=pending.expires_at if pending is not None else unsigned.expires_at,
    )


# --- builders -------------------------------------------------------------------------------------


async def _is_contract_owner(chain: ChainGateway, user: User) -> bool:
    try:
        cfg = await chain.read_config()
    except ChainError as e:
        log.warning("read_config failed while checking the settle caller: %s", e.message)
        return False
    return same(cfg.owner, user.wallet_address)


async def build_action_tx(
    db: AsyncSession,
    settings: Settings,
    chain: ChainGateway,
    agreement: Agreement,
    user: User,
    action: str,
    *,
    slippage_bps: int | None = None,
    asset_id: uuid.UUID | None = None,
) -> UnsignedTxOut:
    """`POST /agreements/{id}/tx/{action}` for action ∈ ACTIONS (02-api §3.1 rules)."""
    if action not in ACTIONS:
        raise ValidationError(f"unknown action {action!r}", code="invalid_action", details={"allowed": list(ACTIONS)})
    role = party_role(agreement, user)
    addr = user.wallet_address
    base = agreement.base_asset
    label = agreement_label(agreement)
    payload: dict[str, Any] = {}
    description = ""
    approve_asset: Asset | None = None

    if action == "propose":
        _require_status(agreement, AgreementStatus.draft, action=action)
        _require_role(role, UserRole.trader, action=action)
        unsigned = await chain.build_propose(addr, build_terms(agreement))
        description = f"Sözleşme {label} teklifini zincire yaz"
    elif action == "open":
        _require_status(agreement, AgreementStatus.draft, action=action)
        _require_role(role, UserRole.customer, action=action)
        _reject_wallet_path("open", await listing_reservation(db, agreement), agreement.principal)
        unsigned = await chain.build_open(addr, build_terms(agreement))
        approve_asset = base
        description = f"{fmt_principal(agreement)} anaparayı kasaya kilitle (sözleşme {label})"
    elif action == "open_reserved":
        _require_status(agreement, AgreementStatus.draft, action=action)
        _require_role(role, UserRole.customer, action=action)
        res_id = _require_reserved(action, await listing_reservation(db, agreement), agreement.principal)
        unsigned = await chain.build_open_reserved(addr, build_terms(agreement), res_id)
        payload["reservation_id"] = res_id
        description = f"Rezervasyon #{res_id}'den {fmt_principal(agreement)} ile sözleşmeyi aç"
    elif action == "fund":
        _require_status(agreement, AgreementStatus.proposed, action=action)
        _require_role(role, UserRole.customer, action=action)
        onchain_id = _require_onchain(agreement)
        _reject_wallet_path("fund", await listing_reservation(db, agreement), agreement.principal)
        unsigned = await chain.build_fund(addr, onchain_id)
        approve_asset = base
        description = f"Sözleşme {label} için {fmt_principal(agreement)} yatır"
    elif action == "fund_reserved":
        _require_status(agreement, AgreementStatus.proposed, action=action)
        _require_role(role, UserRole.customer, action=action)
        onchain_id = _require_onchain(agreement)
        res_id = _require_reserved(action, await listing_reservation(db, agreement), agreement.principal)
        unsigned = await chain.build_fund_reserved(addr, onchain_id, res_id)
        payload["reservation_id"] = res_id
        description = f"Rezervasyon #{res_id}'den sözleşme {label} için {fmt_principal(agreement)} yatır"
    elif action == "accept":
        _require_status(agreement, AgreementStatus.funded, action=action)
        _require_role(role, UserRole.trader, action=action)
        unsigned = await chain.build_accept(addr, _require_onchain(agreement))
        description = f"Sözleşme {label}'yi kabul et ve başlat"
    elif action == "cancel":
        _require_status(agreement, AgreementStatus.proposed, AgreementStatus.funded, action=action)
        if agreement.status is AgreementStatus.proposed:
            _require_role(role, agreement.proposer_role, action=action)  # only the proposer
        else:
            _require_role(role, UserRole.customer, UserRole.trader, action=action)
        unsigned = await chain.build_cancel(addr, _require_onchain(agreement))
        description = f"Sözleşme {label}'yi iptal et"
    elif action == "settle":
        _require_status(agreement, AgreementStatus.active, action=action)
        onchain_id = _require_onchain(agreement)
        if role is None:
            # Contract tiers: parties any time; the owner from end_time; anyone from end_time + 7 days.
            is_owner = user.is_admin or await _is_contract_owner(chain, user)
            grace = timedelta(0) if is_owner else SETTLE_GRACE
            if agreement.end_time is None or now_utc() < agreement.end_time + grace:
                raise ForbiddenError(
                    "only the customer or the trader may settle before the end time"
                    + ("" if is_owner else " plus the 7-day grace period"),
                    code="not_expired",
                )
            min_outs: list[int] = []  # keeper path: floors are computed in-contract
            payload["settle_mode"] = "owner" if is_owner else "keeper"
        else:
            bps = settings.settle_slippage_bps if slippage_bps is None else int(slippage_bps)
            min_outs, legs = await settle_min_outs(chain, agreement, bps)
            payload.update({"settle_mode": "party", "slippage_bps": bps, "legs": legs})
        unsigned = await chain.build_settle(addr, onchain_id, min_outs)
        description = f"Sözleşme {label}'yi kapat ve dağıt"
    else:  # claim
        _require_status(agreement, AgreementStatus.settled, action=action)
        _require_role(role, UserRole.customer, action=action)
        onchain_id = _require_onchain(agreement)
        if asset_id is None:
            raise ValidationError("asset_id is required for claim", code="asset_required")
        asset = await db.get(Asset, asset_id)
        if asset is None:
            raise NotFoundError("Asset not found", code="asset_not_found")
        bal = next((b for b in agreement.balances if b.asset_id == asset.id), None)
        if bal is None or bal.balance is None or bal.balance <= 0:
            raise StateError(
                f"nothing to claim in {asset.symbol}", code="nothing_to_claim", details={"asset_id": str(asset.id)}
            )
        unsigned = await chain.build_claim(addr, onchain_id, asset.address)
        payload.update({"asset_id": str(asset.id), "symbol": asset.symbol, "balance": money.format_amount(bal.balance, asset.decimals)})
        description = f"{asset.symbol} bakiyesini talep et (sözleşme {label})"

    payload["role"] = role.value if role else None
    pending = await record_pending(db, user, PendingTxKind(action), agreement, unsigned, payload, description=description)
    log.info(
        "tx built action=%s agreement=%s onchain=%s user=%s pending=%s pre_steps=%d",
        action, agreement.id, agreement.onchain_id, user.id, pending.id, len(unsigned.pre_steps),
    )
    return unsigned_out(pending, unsigned, asset=approve_asset)


# --- TL equivalents -------------------------------------------------------------------------------


@dataclass
class TlConverter:
    """Per-request TL conversion context (USD/TRY rate + indicative USD price per asset symbol)."""

    rate: fx.FxRate | None
    prices: dict[str, Decimal | None] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)

    def usd_price(self, symbol: str) -> Decimal | None:
        return self.prices.get(symbol.upper())

    def to_try(self, amount: Decimal | None, symbol: str) -> Decimal | None:
        price = self.usd_price(symbol)
        if amount is None or price is None or self.rate is None:
            return None
        return fx.to_try(Decimal(amount) * price, self.rate)


async def tl_converter(
    db: AsyncSession, settings: Settings, chain: ChainGateway | None, symbols: set[str]
) -> TlConverter | None:
    """Build a converter for `symbols`; None when the FX rate is unavailable (TL fields are then null).
    Prices come from the indicative table (`fx.usd_price`); MON / unknown symbols stay None."""
    try:
        rate = await fx.get_usd_try(settings, db)
    except Exception as e:  # noqa: BLE001 - never fail an agreement read because of FX
        log.warning("fx unavailable, TL equivalents disabled: %s", e)
        return None
    conv = TlConverter(rate=rate)
    for symbol in {s.upper() for s in symbols}:
        price = fx.usd_price(symbol)
        conv.prices[symbol] = price
        if price is not None:
            conv.sources[symbol] = "indicative"
    return conv


# --- serialisation ------------------------------------------------------------------------------


def party_out(user: User) -> PartyOut:
    return PartyOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        wallet_address=checksum(user.wallet_address),
        role=user.role,
    )


def asset_brief(asset: Asset) -> AssetBriefOut:
    return AssetBriefOut(
        id=asset.id,
        symbol=asset.symbol,
        address=checksum(asset.address),
        decimals=asset.decimals,
        name=asset.name,
        icon_url=asset.icon_url,
        category=asset.category,
    )


def agreement_out(
    agreement: Agreement,
    user: User | None,
    *,
    tl: TlConverter | None = None,
    pending: PendingTransaction | None = None,
    now: datetime | None = None,
    reservation: tuple[int | None, Decimal | None] = (None, None),
    settings: Settings | None = None,
) -> AgreementOut:
    now = now or now_utc()
    settings = settings or get_settings()
    base = agreement.base_asset
    value = agreement.current_value
    if agreement.status is AgreementStatus.settled and agreement.final_value is not None:
        value = agreement.final_value
    pnl = (Decimal(value) - agreement.principal) if value is not None else None
    floor = money.from_raw(money.drawdown_floor(principal_raw(agreement), agreement.max_drawdown_bps), base.decimals)
    tl_out: TlOut | None = None
    if tl is not None and tl.rate is not None:
        sym = base.symbol
        tl_out = TlOut(
            rate=tl.rate.rate,
            rate_source=tl.rate.source,
            stale=tl.rate.stale,
            base_usd_price=tl.usd_price(sym),
            base_price_source=tl.sources.get(sym.upper()),
            principal_try=tl.to_try(agreement.principal, sym),
            current_value_try=tl.to_try(value, sym),
            pnl_try=tl.to_try(pnl, sym) if pnl is not None and tl.usd_price(sym) is not None else None,
            final_value_try=tl.to_try(agreement.final_value, sym),
            customer_payout_try=tl.to_try(agreement.customer_payout, sym),
        )
    balances = [
        BalanceOut(
            asset=asset_brief(b.asset),
            balance=b.balance,
            updated_at=b.updated_at,
            value_try=tl.to_try(b.balance, b.asset.symbol) if tl is not None else None,
        )
        for b in agreement.balances
    ]
    remaining: int | None = None
    if agreement.end_time is not None:
        remaining = max(0, int((agreement.end_time - now).total_seconds()))
    claimable = claimable_asset_ids(agreement)
    vault = agreement.vault_address or settings.vault_address
    return AgreementOut(
        id=agreement.id,
        onchain_id=agreement.onchain_id,
        offer_id=agreement.offer_id,
        listing_id=agreement.listing_id,
        status=agreement.status,
        proposer_role=agreement.proposer_role,
        customer=party_out(agreement.customer),
        trader=party_out(agreement.trader),
        base_asset=asset_brief(base),
        vault_address=checksum(vault) if vault else None,
        principal=agreement.principal,
        duration_secs=int(agreement.duration_secs),
        duration_days=int(agreement.duration_secs) // 86_400,
        commission_bps=agreement.commission_bps,
        max_drawdown_bps=agreement.max_drawdown_bps,
        platform_fee_bps=agreement.platform_fee_bps,
        risk_profile=agreement.risk_profile,
        listing_ref=agreement.listing_ref,
        created_tx=agreement.created_tx,
        activate_tx=agreement.activate_tx,
        cancel_tx=agreement.cancel_tx,
        settle_tx=agreement.settle_tx,
        start_time=agreement.start_time,
        end_time=agreement.end_time,
        seconds_remaining=remaining,
        is_expired=is_expired(agreement, now),
        current_value=agreement.current_value,
        value_updated_at=agreement.value_updated_at,
        high_water_value=agreement.high_water_value,
        pnl=pnl,
        pnl_bps=pnl_bps(value, agreement.principal),
        drawdown_floor=floor,
        drawdown_bps=drawdown_bps(value, agreement.principal),
        final_value=agreement.final_value,
        profit=agreement.profit,
        trader_fee=agreement.trader_fee,
        platform_fee=agreement.platform_fee,
        customer_payout=agreement.customer_payout,
        settled_at=agreement.settled_at,
        settled_by=checksum(agreement.settled_by) if agreement.settled_by else None,
        last_event_block_number=agreement.last_event_block_number,
        balances=balances,
        my_role=party_role(agreement, user),
        available_actions=available_actions(
            agreement,
            user,
            now,
            reservation_covers=reservation_covers(reservation, agreement.principal),
            claimable=bool(claimable),
        ),
        claimable_assets=claimable,
        pending_tx=PendingTxBriefOut.model_validate(pending, from_attributes=True) if pending is not None else None,
        tl=tl_out,
        created_at=agreement.created_at,
        updated_at=agreement.updated_at,
    )


async def _reservations_for(db: AsyncSession, rows: list[Agreement]) -> dict[uuid.UUID, tuple[int | None, Decimal | None]]:
    """Reservation context for the agreements that may still draw on a listing (draft / proposed)."""
    ids = {ag.listing_id for ag in rows if ag.listing_id is not None and ag.status in (AgreementStatus.draft, AgreementStatus.proposed)}
    if not ids:
        return {}
    listings = (await db.execute(select(Listing).where(Listing.id.in_(list(ids))))).scalars().all()
    by_id = {x.id: x for x in listings}
    return {ag.id: _reservation_of(by_id.get(ag.listing_id)) for ag in rows if ag.listing_id in by_id}


async def serialize_many(
    db: AsyncSession, settings: Settings, chain: ChainGateway | None, rows: list[Agreement], user: User | None
) -> list[AgreementOut]:
    if not rows:
        return []
    symbols = {ag.base_asset.symbol for ag in rows} | {b.asset.symbol for ag in rows for b in ag.balances}
    tl = await tl_converter(db, settings, chain, symbols)
    reservations = await _reservations_for(db, rows)
    return [agreement_out(ag, user, tl=tl, reservation=reservations.get(ag.id, (None, None)), settings=settings) for ag in rows]


async def serialize_one(
    db: AsyncSession, settings: Settings, chain: ChainGateway | None, agreement: Agreement, user: User
) -> AgreementOut:
    symbols = {agreement.base_asset.symbol} | {b.asset.symbol for b in agreement.balances}
    tl = await tl_converter(db, settings, chain, symbols)
    pending = await latest_pending(db, agreement, user)
    reservation = (None, None)
    if agreement.status in (AgreementStatus.draft, AgreementStatus.proposed):
        reservation = await listing_reservation(db, agreement)
    return agreement_out(agreement, user, tl=tl, pending=pending, reservation=reservation, settings=settings)


# --- trades / history ---------------------------------------------------------------------------


async def list_trades(db: AsyncSession, agreement: Agreement, *, limit: int = 20, offset: int = 0) -> tuple[list[Trade], int]:
    where = [Trade.agreement_id == agreement.id]
    total = int((await db.execute(select(func.count()).select_from(Trade).where(*where))).scalar_one())
    rows = (
        await db.execute(
            select(Trade).where(*where).order_by(Trade.created_at.desc(), Trade.id.desc()).limit(limit).offset(offset)
        )
    ).scalars().all()
    return list(rows), total


async def value_history(db: AsyncSession, agreement: Agreement, range_: ValueRange = "7d") -> ValueHistoryOut:
    """`agreement_value_snapshots` of the agreement (reconciler samples + event-time values), oldest first."""
    delta = _RANGE_DELTA[range_]
    where = [AgreementValueSnapshot.agreement_id == agreement.id]
    if delta is not None:
        where.append(AgreementValueSnapshot.at >= now_utc() - delta)
    rows = (
        await db.execute(
            select(AgreementValueSnapshot).where(*where).order_by(AgreementValueSnapshot.at.asc()).limit(2000)
        )
    ).scalars().all()
    principal = agreement.principal
    points = [ValuePointOut(at=r.at, value=r.value, return_bps=pnl_bps(r.value, principal) or 0) for r in rows]
    return ValueHistoryOut(
        agreement_id=agreement.id,
        range=range_,
        principal=principal,
        current_value=agreement.current_value,
        high_water_value=agreement.high_water_value,
        points=points,
    )


__all__ = [
    "ACTIONS",
    "CLOSED_STATUSES",
    "OPEN_STATUSES",
    "SETTLE_GRACE",
    "TlConverter",
    "agreement_label",
    "agreement_out",
    "approval_pre_steps",
    "asset_brief",
    "available_actions",
    "build_action_tx",
    "build_terms",
    "claimable_asset_ids",
    "compute_listing_ref",
    "drawdown_bps",
    "fmt_principal",
    "get_agreement_for",
    "get_agreement_row",
    "is_expired",
    "json_safe",
    "latest_pending",
    "list_agreements",
    "list_trades",
    "listing_ref_bytes",
    "listing_reservation",
    "now_utc",
    "parse_status_filter",
    "party_out",
    "party_role",
    "pnl_bps",
    "pre_step_out",
    "principal_raw",
    "record_pending",
    "reservation_covers",
    "serialize_many",
    "serialize_one",
    "settle_min_outs",
    "tl_converter",
    "ts_to_dt",
    "unsigned_out",
    "value_history",
]
