"""Agreements / Sözleşme (Figma 9d, 3a, 5c) and trading (4a/4b) endpoints — 02-api-sozlesme §3.1–3.2, §8.1."""
from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api_deps import DB, ChainDep, CurrentUser, SettingsDep
from app.api_paging import PageDep
from app.core.errors import ChainError
from app.models import AgreementStatus, UserRole
from app.schemas.agreements import AgreementOut, ValueHistoryOut, ValueRange
from app.schemas.common import Page
from app.schemas.trades import QuoteOut, TradeOut, TradeTxIn
from app.schemas.tx import TxAction, TxActionIn, UnsignedTxOut
from app.services import agreements as svc
from app.services import trading
from app.services.agreements import OPEN_STATUSES
from app.services.indexer import IndexContext, refresh_agreement_from_chain

log = logging.getLogger(__name__)

router = APIRouter(prefix="/agreements", tags=["agreements"])


@router.get("", response_model=Page[AgreementOut])
async def list_agreements(
    db: DB,
    user: CurrentUser,
    settings: SettingsDep,
    page: PageDep,
    role: Annotated[UserRole | None, Query(description="side you play: customer | trader (default both)")] = None,
    status: Annotated[
        str | None, Query(description="draft|proposed|funded|active|settled|cancelled|failed or open|closed")
    ] = None,
    chain=ChainDep,
) -> Page[AgreementOut]:
    """Agreements the caller is party to, newest activity first, with TL equivalents."""
    rows, total = await svc.list_agreements(db, user, role=role, status=status, limit=page.limit, offset=page.offset)
    items = await svc.serialize_many(db, settings, chain, rows, user)
    return Page[AgreementOut](items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/{agreement_id}", response_model=AgreementOut)
async def get_agreement(
    agreement_id: uuid.UUID,
    db: DB,
    user: CurrentUser,
    settings: SettingsDep,
    refresh: Annotated[bool, Query(description="read live value/balances from the contract (open agreements)")] = True,
    chain=ChainDep,
) -> AgreementOut:
    """Terms, status, balances, value, P&L, tx hashes, TL equivalents and the actions the caller may take."""
    ag = await svc.get_agreement_for(db, agreement_id, user)
    if refresh and ag.status in OPEN_STATUSES and ag.onchain_id is not None:
        try:
            await refresh_agreement_from_chain(IndexContext(db, settings, chain), ag, snapshot=False, alerts=False)
            await db.refresh(ag)
        except ChainError as e:  # the mirror is still served when RPC is down
            log.warning("live refresh of agreement %s skipped: %s", ag.id, e.message)
    return await svc.serialize_one(db, settings, chain, ag, user)


@router.post("/{agreement_id}/tx/trade", response_model=UnsignedTxOut)
async def build_trade(
    agreement_id: uuid.UUID, body: TradeTxIn, db: DB, user: CurrentUser, settings: SettingsDep, chain=ChainDep
) -> UnsignedTxOut:
    """Calldata for `trade(id, tokenIn, tokenOut, amountIn, minOut, deadline)` (trader, Figma 4b). `min_out`
    comes from the router quote minus `slippage_bps`; the note / notify flag are attached to the trade row
    once the `Traded` event is applied. No pre-steps."""
    ag = await svc.get_agreement_for(db, agreement_id, user)
    return await trading.build_trade_tx(db, settings, chain, ag, user, body)


@router.post("/{agreement_id}/tx/{action}", response_model=UnsignedTxOut)
async def build_action(
    agreement_id: uuid.UUID,
    action: TxAction,
    db: DB,
    user: CurrentUser,
    settings: SettingsDep,
    body: TxActionIn | None = None,
    chain=ChainDep,
) -> UnsignedTxOut:
    """Calldata (`from` = caller) for propose | open | open_reserved | fund | fund_reserved | accept | cancel |
    settle | claim (02-api §3.1). `open`/`fund` carry an `approve` pre-step when the allowance is short;
    when the listing's reservation covers the principal the client must use `open_reserved`/`fund_reserved`
    (409 `use_reserved_action`). `claim` needs `asset_id`. Send it, then `POST /tx/submit`."""
    ag = await svc.get_agreement_row(db, agreement_id)  # non-parties may settle after expiry (+ grace)
    return await svc.build_action_tx(
        db,
        settings,
        chain,
        ag,
        user,
        action,
        slippage_bps=body.slippage_bps if body else None,
        asset_id=body.asset_id if body else None,
    )


@router.get("/{agreement_id}/quote", response_model=QuoteOut)
async def quote(
    agreement_id: uuid.UUID,
    db: DB,
    user: CurrentUser,
    settings: SettingsDep,
    token_in: Annotated[str, Query(description="asset id, 0x address or symbol")],
    token_out: Annotated[str, Query(description="asset id, 0x address or symbol")],
    amount_in: Annotated[str, Query(description="decimal string, > 0, at most token_in.decimals places")],
    slippage_bps: Annotated[int | None, Query(ge=0, le=5_000)] = None,
    deadline_seconds: Annotated[int, Query(ge=30, le=3_600)] = 300,
    chain=ChainDep,
) -> QuoteOut:
    """Router quote via the vault's router (`getAmountsOut`) + drawdown headroom (Figma 4a)."""
    ag = await svc.get_agreement_for(db, agreement_id, user)
    q = await trading.compute_quote(
        db, settings, chain, ag, user,
        token_in=token_in, token_out=token_out, amount_in=amount_in, slippage_bps=slippage_bps, deadline_seconds=deadline_seconds,
    )
    return q.out


@router.get("/{agreement_id}/trades", response_model=Page[TradeOut])
async def list_trades(agreement_id: uuid.UUID, db: DB, user: CurrentUser, settings: SettingsDep, page: PageDep) -> Page[TradeOut]:
    ag = await svc.get_agreement_for(db, agreement_id, user)
    rows, total = await svc.list_trades(db, ag, limit=page.limit, offset=page.offset)
    items = [trading.trade_out(t, ag, explorer_url=settings.explorer_url) for t in rows]
    return Page[TradeOut](items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/{agreement_id}/value-history", response_model=ValueHistoryOut)
async def value_history(
    agreement_id: uuid.UUID,
    db: DB,
    user: CurrentUser,
    range: Annotated[ValueRange, Query(description="24h | 7d | 30d | 90d | all")] = "7d",  # noqa: A002
) -> ValueHistoryOut:
    """Value snapshots (reconciler + event-time values) for the agreement chart."""
    ag = await svc.get_agreement_for(db, agreement_id, user)
    return await svc.value_history(db, ag, range)


__all__ = ["router", "AgreementStatus"]
