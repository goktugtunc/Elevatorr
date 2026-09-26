"""Trading (Figma 4a/4b "Yeni İşlem"): router quotes with drawdown headroom, the unsigned `trade`
calldata (min_out from the quote) and the trader's off-chain note on an indexed trade (03 §6.2; 02-api §3.2).

The contract enforces `value_after >= principal × (1 − max_drawdown)`; the quote here re-computes that
check from the same router quotes so the app can show the headroom before sending.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import ChainError, ForbiddenError, NotFoundError, StateError, ValidationError
from app.models import Agreement, AgreementStatus, Asset, PendingTxKind, Trade, User, UserRole
from app.schemas.trades import QuoteOut, TradeNoteIn, TradeOut, TradeTxIn
from app.schemas.tx import UnsignedTxOut
from app.services.agreements import (
    agreement_label,
    asset_brief,
    get_agreement_row,
    is_expired,
    now_utc,
    party_role,
    principal_raw,
    record_pending,
    unsigned_out,
)
from app.services.chain import amounts as money
from app.services.chain.abi import MAX_TOKENS
from app.services.chain.addresses import is_evm_address, normalize, same
from app.services.chain.gateway import ChainGateway

log = logging.getLogger(__name__)

REASON_MESSAGES = {
    "expired": "the agreement has reached its end time; only settle is possible",
    "token_not_allowed": "one of the tokens is not allow-listed on the vault",
    "same_token": "token_in and token_out must differ",
    "insufficient_balance": "the agreement holds less of token_in than amount_in",
    "too_many_tokens": "the agreement already holds the maximum number of tokens",
    "no_liquidity": "the router returns nothing for this amount",
    "drawdown_breached": "the trade would push the portfolio value below the max-drawdown floor",
}


# --- input parsing ------------------------------------------------------------------------------


def parse_amount(value: Decimal | str, decimals: int) -> Decimal:
    """Amount from the API (decimal string): finite, > 0, at most `decimals` places (422 otherwise)."""
    return money.parse_amount(value, decimals)


# --- asset resolution ---------------------------------------------------------------------------


async def resolve_asset(db: AsyncSession, settings: Settings, ref: str) -> Asset:
    """`ref` is an asset uuid, a 0x token address or a symbol (case-insensitive) of an active asset on the
    configured chain (02-api §3.2)."""
    ref = (ref or "").strip()
    if not ref:
        raise ValidationError("token is required", code="invalid_token")
    asset: Asset | None = None
    try:
        asset = await db.get(Asset, uuid.UUID(ref))
    except ValueError:
        pass
    if asset is None and is_evm_address(ref):
        asset = (
            await db.execute(select(Asset).where(Asset.chain_id == settings.chain_id, Asset.address == normalize(ref)))
        ).scalar_one_or_none()
    if asset is None and not is_evm_address(ref):
        rows = (
            await db.execute(
                select(Asset).where(
                    Asset.chain_id == settings.chain_id,
                    Asset.is_active.is_(True),
                    func.lower(Asset.symbol) == ref.lower(),
                )
            )
        ).scalars().all()
        if len(rows) == 1:
            asset = rows[0]
        elif len(rows) > 1:
            raise ValidationError(
                f"asset symbol {ref!r} is ambiguous; use the asset id or address",
                code="ambiguous_token",
                details={"candidates": [str(a.id) for a in rows]},
            )
    if asset is None or asset.chain_id != settings.chain_id:
        raise NotFoundError(f"asset {ref!r} not found", code="asset_not_found")
    return asset


def symbol_label(token_in: Asset, token_out: Asset, base: Asset) -> str:
    """Trade label, e.g. "tWETH/tUSDC · Buy". Spending the base token is a Buy, going back to the base is
    a Sell, anything else is a Swap."""
    if same(token_in.address, base.address):
        return f"{token_out.symbol}/{token_in.symbol} · Buy"
    if same(token_out.address, base.address):
        return f"{token_in.symbol}/{token_out.symbol} · Sell"
    return f"{token_out.symbol}/{token_in.symbol} · Swap"


# --- quote --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Quote:
    out: QuoteOut
    amount_in_raw: int
    amount_out_raw: int
    min_out_raw: int
    token_in: Asset
    token_out: Asset


async def _in_base(chain: ChainGateway, token: str, amount: int, base: str) -> int:
    """Value of `amount` of `token` in base units via the router (0 when unquotable, like the contract)."""
    if amount <= 0:
        return 0
    if same(token, base):
        return int(amount)
    try:
        return int((await chain.quote([token, base], amount)).amount_out)
    except ChainError:
        return 0


def _price(amount_in: Decimal, amount_out: Decimal) -> Decimal:
    if amount_in <= 0:
        return Decimal("0")
    return (amount_out / amount_in).quantize(Decimal("0.000000000000000001"))


async def compute_quote(
    db: AsyncSession,
    settings: Settings,
    chain: ChainGateway,
    agreement: Agreement,
    user: User,
    *,
    token_in: str,
    token_out: str,
    amount_in: Decimal | str,
    slippage_bps: int | None = None,
    deadline_seconds: int = 300,
) -> Quote:
    """Router quote + drawdown headroom for a party of an *active* agreement."""
    if party_role(agreement, user) is None and not user.is_admin:
        raise ForbiddenError("You are not a party of this agreement", code="not_party")
    if agreement.status is not AgreementStatus.active:
        raise StateError(
            f"agreement is {agreement.status.value}; trading needs an active agreement", code="invalid_state"
        )
    if agreement.onchain_id is None:
        raise StateError("agreement is not on-chain yet", code="not_onchain")
    a_in = await resolve_asset(db, settings, token_in)
    a_out = await resolve_asset(db, settings, token_out)
    if a_in.id == a_out.id:
        raise ValidationError(REASON_MESSAGES["same_token"], code="same_token")
    bps = settings.default_trade_slippage_bps if slippage_bps is None else int(slippage_bps)
    amount_in_d = parse_amount(amount_in, a_in.decimals)
    amount_in_raw = money.to_raw(amount_in_d, a_in.decimals)

    onchain_id = int(agreement.onchain_id)
    base = agreement.base_asset
    view = await chain.read_agreement(onchain_id)
    balances = {t.lower(): int(b) for t, b in await chain.read_balances(onchain_id)}
    value_before = int(await chain.read_value_in_base(onchain_id))
    in_addr, out_addr, base_addr = a_in.address, a_out.address, base.address
    balance_in = balances.get(in_addr.lower(), 0)

    try:
        amount_out_raw = int((await chain.quote([in_addr, out_addr], amount_in_raw)).amount_out)
    except ChainError as e:
        if e.code == "quote_failed":
            raise StateError(f"no route for {a_in.symbol} -> {a_out.symbol} on the router", code="no_route") from e
        raise
    in_leg = await _in_base(chain, in_addr, amount_in_raw, base_addr)
    out_leg = await _in_base(chain, out_addr, amount_out_raw, base_addr)
    value_after = value_before - in_leg + out_leg
    p_raw = principal_raw(agreement)
    floor = money.drawdown_floor(p_raw, agreement.max_drawdown_bps)
    headroom = value_after - floor
    headroom_bps = int(headroom * 10_000 // p_raw) if p_raw > 0 else 0

    reason: str | None = None
    if is_expired(agreement) or (view.end_time and int(time.time()) >= view.end_time):
        reason = "expired"
    else:
        allowed_in = await chain.is_token_allowed(in_addr)
        allowed_out = await chain.is_token_allowed(out_addr)
        held = {t.lower() for t in view.tokens}
        if not (allowed_in.allowed and allowed_out.allowed):
            reason = "token_not_allowed"
        elif balance_in < amount_in_raw:
            reason = "insufficient_balance"
        elif out_addr.lower() not in held and len(held) >= MAX_TOKENS:
            reason = "too_many_tokens"
        elif amount_out_raw <= 0:
            reason = "no_liquidity"
        elif headroom < 0:
            reason = "drawdown_breached"

    min_out_raw = money.min_out_for_slippage(amount_out_raw, bps)
    amount_out_d = money.from_raw(amount_out_raw, a_out.decimals)
    out = QuoteOut(
        agreement_id=agreement.id,
        onchain_id=onchain_id,
        token_in=asset_brief(a_in),
        token_out=asset_brief(a_out),
        amount_in=amount_in_d,
        amount_out=amount_out_d,
        min_out=money.from_raw(min_out_raw, a_out.decimals),
        slippage_bps=bps,
        price=_price(amount_in_d, amount_out_d),
        source="router" if getattr(chain, "kind", "fake") == "monad" else "fake",
        api_amount_out=None,
        price_impact_pct=None,
        balance_in=money.from_raw(balance_in, a_in.decimals),
        value_before=money.from_raw(value_before, base.decimals),
        value_after_estimate=money.from_raw(value_after, base.decimals),
        principal=agreement.principal,
        max_drawdown_bps=agreement.max_drawdown_bps,
        drawdown_floor=money.from_raw(floor, base.decimals),
        headroom=money.from_raw(headroom, base.decimals),
        headroom_bps=headroom_bps,
        allowed=reason is None,
        reason=reason,
        deadline_seconds=int(deadline_seconds),
        quoted_at=now_utc(),
    )
    return Quote(out=out, amount_in_raw=amount_in_raw, amount_out_raw=amount_out_raw, min_out_raw=min_out_raw, token_in=a_in, token_out=a_out)


# --- trade tx -----------------------------------------------------------------------------------


async def build_trade_tx(
    db: AsyncSession, settings: Settings, chain: ChainGateway, agreement: Agreement, user: User, body: TradeTxIn
) -> UnsignedTxOut:
    """`POST /agreements/{id}/tx/trade` (trader only, active, before end_time). No pre-steps: the vault
    approves the router itself."""
    if party_role(agreement, user) is not UserRole.trader:
        raise ForbiddenError("only the trader may trade on this agreement", code="wrong_party")
    q = await compute_quote(
        db,
        settings,
        chain,
        agreement,
        user,
        token_in=body.token_in,
        token_out=body.token_out,
        amount_in=body.amount_in,
        slippage_bps=body.slippage_bps,
        deadline_seconds=body.deadline_seconds,
    )
    if not q.out.allowed:
        reason = q.out.reason or "rejected"
        raise StateError(REASON_MESSAGES.get(reason, reason), code=reason, details={"quote": q.out.model_dump(mode="json")})
    if q.min_out_raw <= 0:
        raise StateError("min_out would be zero; increase amount_in or lower slippage", code="min_out_zero")
    deadline = int(time.time()) + int(body.deadline_seconds)
    unsigned = await chain.build_trade(
        user.wallet_address,
        int(agreement.onchain_id),
        q.token_in.address,
        q.token_out.address,
        q.amount_in_raw,
        q.min_out_raw,
        deadline,
    )
    label = symbol_label(q.token_in, q.token_out, agreement.base_asset)
    payload = {
        # 02-api §3.2 summary keys
        "token_in": str(q.token_in.id),
        "token_out": str(q.token_out.id),
        "amount_in": money.format_amount(q.out.amount_in, q.token_in.decimals),
        "min_out": money.format_amount(q.out.min_out, q.token_out.decimals),
        "min_out_raw": str(q.min_out_raw),
        "slippage_bps": q.out.slippage_bps,
        "deadline": deadline,
        "note": body.note,
        "notify_investors": bool(body.notify_investors),
        # indexer context (`Traded` handler copies these onto the trade row)
        "symbol_label": label,
        "token_in_id": str(q.token_in.id),
        "token_out_id": str(q.token_out.id),
        "token_in_symbol": q.token_in.symbol,
        "token_out_symbol": q.token_out.symbol,
        "amount_out_quote": money.format_amount(q.out.amount_out, q.token_out.decimals),
        "value_after_estimate": money.format_amount(q.out.value_after_estimate, agreement.base_asset.decimals),
        "headroom_bps": q.out.headroom_bps,
    }
    description = (
        f"{money.format_amount(q.out.amount_in, q.token_in.decimals)} {q.token_in.symbol} → {q.token_out.symbol} "
        f"takası (sözleşme {agreement_label(agreement)})"
    )
    pending = await record_pending(db, user, PendingTxKind.trade, agreement, unsigned, payload, description=description)
    log.info(
        "trade tx built agreement=%s %s %s -> %s min_out=%s pending=%s",
        agreement.id, q.amount_in_raw, q.token_in.symbol, q.token_out.symbol, q.min_out_raw, pending.id,
    )
    return unsigned_out(pending, unsigned)


# --- trades -------------------------------------------------------------------------------------


def trade_out(trade: Trade, agreement: Agreement | None = None, *, explorer_url: str | None = None) -> TradeOut:
    """`TradeOut` (02-api §8.2). `explorer_url` defaults to `settings.explorer_url`."""
    base = (explorer_url or get_settings().explorer_url).rstrip("/")
    return TradeOut(
        id=trade.id,
        agreement_id=trade.agreement_id,
        onchain_id=agreement.onchain_id if agreement is not None else None,
        log_index=trade.log_index,
        tx_hash=trade.tx_hash,
        block_number=trade.block_number,
        explorer_url=f"{base}/tx/{trade.tx_hash}" if trade.tx_hash else None,
        trader_id=trade.trader_id,
        token_in=asset_brief(trade.token_in),
        token_out=asset_brief(trade.token_out),
        amount_in=trade.amount_in,
        amount_out=trade.amount_out,
        price=_price(trade.amount_in, trade.amount_out),
        value_after=trade.value_after,
        note=trade.note,
        symbol_label=trade.symbol_label,
        notify_investors=trade.notify_investors,
        created_at=trade.created_at,
    )


async def get_trade(db: AsyncSession, trade_id: uuid.UUID) -> Trade:
    trade = await db.get(Trade, trade_id)
    if trade is None:
        raise NotFoundError("Trade not found", code="trade_not_found")
    return trade


async def update_trade_note(db: AsyncSession, user: User, trade_id: uuid.UUID, body: TradeNoteIn) -> tuple[Trade, Agreement]:
    """`PATCH /trades/{id}`: only the agreement's trader may edit the note / notify flag."""
    trade = await get_trade(db, trade_id)
    agreement = await get_agreement_row(db, trade.agreement_id)
    if agreement.trader_id != user.id and not user.is_admin:
        raise ForbiddenError("only the trader may edit this trade", code="wrong_party")
    changes = body.model_dump(exclude_unset=True)
    if "note" in changes:
        trade.note = changes["note"]
    if changes.get("notify_investors") is not None:
        trade.notify_investors = bool(changes["notify_investors"])
    if changes:
        await db.flush()
    return trade, agreement


__all__ = [
    "REASON_MESSAGES",
    "Quote",
    "build_trade_tx",
    "compute_quote",
    "get_trade",
    "parse_amount",
    "resolve_asset",
    "symbol_label",
    "trade_out",
    "update_trade_note",
]
