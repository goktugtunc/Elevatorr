"""Indexer + reconciler against synthetic vault events from the FakeSorobanGateway (no API): events ->
rows (agreements / balances / trades / snapshots / notifications / trader stats), cursor persistence,
idempotency, rows mirrored from chain state, missed-status sync, drawdown / expiry alerts and the
pending-transaction housekeeping."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from stellar_sdk import Keypair

from app.models import (
    Agreement,
    AgreementStatus,
    AgreementValueSnapshot,
    Follow,
    IndexerState,
    Notification,
    PendingTransaction,
    PendingTxKind,
    PendingTxStatus,
    Trade,
    User,
)
from app.services import amounts as money
from app.services.agreements import build_terms
from app.services.indexer import (
    VAULT_EVENTS_KEY,
    IndexContext,
    check_alerts,
    run_indexer_once,
    run_reconcile_once,
)
from app.services.stellar.fake import TESTNET_USDC, TESTNET_XLM
from tests.helpers_agreements import (  # noqa: F401 - fixtures
    _fx_stub,
    _mounted,
    chain_open_and_accept,
    chain_submit,
    make_agreement,
    soroban_gateway,
)


@pytest.fixture
def soroban():
    yield from soroban_gateway()


async def _count(db, model) -> int:
    return int((await db.execute(select(func.count()).select_from(model))).scalar_one())


async def _types(db, user_id) -> list[str]:
    q = select(Notification.type).where(Notification.user_id == user_id).order_by(Notification.created_at)
    return list((await db.execute(q)).scalars().all())


async def _run(db, soroban, settings):
    out = await run_indexer_once(db, soroban, settings)
    await db.commit()
    assert out.errors == [], out.errors
    return out


async def test_events_become_rows(db, settings, make_user, seed_assets, soroban):
    ckp, tkp = Keypair.random(), Keypair.random()
    customer, _ = await make_user("customer", keypair=ckp)
    trader, _ = await make_user("trader", keypair=tkp)
    follower, _ = await make_user("customer")
    db.add(Follow(follower_id=follower.id, trader_id=trader.id))
    await db.commit()
    xlm = seed_assets["XLM"]
    ag = await make_agreement(db, customer, trader, xlm, principal="1000", max_drawdown_bps=1000)

    out = await _run(db, soroban, settings)  # nothing on-chain yet
    assert out.events_seen == 0 and out.skipped is False
    state = await db.get(IndexerState, VAULT_EVENTS_KEY)
    assert state is not None and state.ledger is not None

    # open (no pending row: the draft is matched through the chain's listing_ref)
    res = await chain_submit(soroban, await soroban.build_open(ckp.public_key, build_terms(ag)), ckp)
    out = await _run(db, soroban, settings)
    assert out.events_seen == 1 and out.events_applied == 1
    await db.refresh(ag)
    assert ag.onchain_id == 1 and ag.status is AgreementStatus.funded and ag.created_tx == res.hash
    assert ag.last_event_ledger == res.ledger and [(b.asset.code, b.balance) for b in ag.balances] == [("XLM", Decimal("1000"))]
    assert (await db.get(IndexerState, VAULT_EVENTS_KEY)).cursor == res.events[0].id
    assert await _types(db, trader.id) == ["agreement_funded"]

    # accept
    res = await chain_submit(soroban, await soroban.build_accept(tkp.public_key, 1), tkp)
    await _run(db, soroban, settings)
    await db.refresh(ag)
    assert ag.status is AgreementStatus.active and ag.activate_tx == res.hash
    assert ag.start_time == datetime.fromtimestamp(soroban.now, tz=UTC) and ag.end_time == ag.start_time + timedelta(days=7)
    assert ag.current_value == Decimal("1000") and ag.high_water_value == Decimal("1000")
    assert await _count(db, AgreementValueSnapshot) == 1
    await db.refresh(trader)
    assert trader.active_agreements == 1 and trader.managed_capital == Decimal("1000")
    assert "agreement_activated" in await _types(db, customer.id) and "agreement_activated" in await _types(db, trader.id)

    # trade XLM -> USDC (no pending payload: label computed, note empty)
    quote = await soroban.router_quote(TESTNET_XLM, TESTNET_USDC, 200_0000000)
    res = await chain_submit(soroban, await soroban.build_trade(tkp.public_key, 1, TESTNET_XLM, TESTNET_USDC, 200_0000000, quote, soroban.now + 60), tkp)
    await _run(db, soroban, settings)
    trade = (await db.execute(select(Trade))).scalar_one()
    assert trade.agreement_id == ag.id and trade.tx_hash == res.hash and trade.onchain_seq == 0 and trade.ledger == res.ledger
    assert trade.amount_in == Decimal("200") and trade.amount_out == Decimal("58") and trade.value_after == Decimal("1000")
    assert trade.symbol_label == "USDC/XLM · Alış" and trade.note is None and trade.trader_id == trader.id
    await db.refresh(ag)
    assert {b.asset.code: b.balance for b in ag.balances} == {"XLM": Decimal("800"), "USDC": Decimal("58")}
    assert ag.current_value == Decimal("1000") and await _count(db, AgreementValueSnapshot) == 2
    assert "trade_executed" in await _types(db, customer.id)
    assert await _types(db, follower.id) == ["followed_trade"]

    # settle by the trader
    usdc_bal = dict(await soroban.get_balances(1))[TESTNET_USDC]
    back = await soroban.router_quote(TESTNET_USDC, TESTNET_XLM, usdc_bal)
    res = await chain_submit(soroban, await soroban.build_settle(tkp.public_key, 1, [back]), tkp)
    await _run(db, soroban, settings)
    await db.refresh(ag)
    assert ag.status is AgreementStatus.settled and ag.settle_tx == res.hash and ag.settled_by == tkp.public_key
    assert ag.final_value == Decimal("1000") and ag.profit == Decimal("0") and ag.customer_payout == Decimal("1000")
    assert ag.balances == [] and ag.current_value == Decimal("1000")
    assert "agreement_settled" in await _types(db, customer.id)
    await db.refresh(trader)
    assert trader.active_agreements == 0 and trader.win_rate_bps == 0

    # idempotent: a cursor reset replays every event without changing anything
    counts = (await _count(db, Trade), await _count(db, Notification), await _count(db, AgreementValueSnapshot))
    state = await db.get(IndexerState, VAULT_EVENTS_KEY)
    state.cursor, state.ledger = None, None
    await db.commit()
    out = await _run(db, soroban, settings)
    assert out.events_seen == 4 and out.events_applied == 0
    assert counts == (await _count(db, Trade), await _count(db, Notification), await _count(db, AgreementValueSnapshot))


async def test_agreement_mirrored_from_chain_when_no_draft_exists(db, settings, make_user, seed_assets, soroban):
    ckp, tkp = Keypair.random(), Keypair.random()
    customer, _ = await make_user("customer", keypair=ckp)
    trader, _ = await make_user("trader", keypair=tkp)
    xlm = seed_assets["XLM"]
    # an agreement created straight on the contract (e.g. from the CLI) with terms the app never saw
    tmp = await make_agreement(db, customer, trader, xlm, principal="250", duration_days=2, commission_bps=1000, max_drawdown_bps=2000)
    terms = build_terms(tmp)
    await db.delete(tmp)
    await db.commit()
    await chain_submit(soroban, await soroban.build_propose(tkp.public_key, terms), tkp)
    out = await _run(db, soroban, settings)
    assert out.events_applied == 1
    ag = (await db.execute(select(Agreement))).scalar_one()
    assert ag.onchain_id == 1 and ag.status is AgreementStatus.proposed and ag.proposer_role.value == "trader"
    assert ag.principal == Decimal("250") and ag.duration_secs == 2 * 86_400 and ag.commission_bps == 1000
    assert ag.listing_ref == terms.listing_ref_hex and ag.customer_id == customer.id and ag.trader_id == trader.id
    assert await _types(db, customer.id) == ["agreement_proposed"]
    await chain_submit(soroban, await soroban.build_fund(ckp.public_key, 1), ckp)
    await _run(db, soroban, settings)
    await db.refresh(ag)
    assert ag.status is AgreementStatus.active and [(b.asset.code, b.balance) for b in ag.balances] == [("XLM", Decimal("250"))]


async def test_reconcile_snapshots_drawdown_and_expiry_alerts(db, settings, make_user, seed_assets, soroban):
    ckp, tkp = Keypair.random(), Keypair.random()
    customer, _ = await make_user("customer", keypair=ckp)
    trader, _ = await make_user("trader", keypair=tkp)
    xlm = seed_assets["XLM"]
    ag = await make_agreement(db, customer, trader, xlm, principal="1000", max_drawdown_bps=1000)
    await chain_open_and_accept(soroban, ag, ckp, tkp)
    quote = await soroban.router_quote(TESTNET_XLM, TESTNET_USDC, 500_0000000)  # 145 USDC
    await chain_submit(soroban, await soroban.build_trade(tkp.public_key, 1, TESTNET_XLM, TESTNET_USDC, 500_0000000, quote, soroban.now + 60), tkp)
    await _run(db, soroban, settings)
    await db.refresh(ag)
    assert ag.status is AgreementStatus.active and ag.current_value == Decimal("1000")
    snapshots = await _count(db, AgreementValueSnapshot)

    out = await run_reconcile_once(db, soroban, settings)
    await db.commit()
    assert out.checked == 1 and out.valued == 1 and out.snapshots == 1 and out.alerts == 0 and out.errors == []
    assert await _count(db, AgreementValueSnapshot) == snapshots + 1

    # USDC loses value against XLM: 1 USDC = 1/0.35 XLM -> value 500 + 145/0.35 = 914.28 -> 8.57% drawdown (>= 80% of 10%)
    soroban.set_price(TESTNET_XLM, TESTNET_USDC, "0.35")
    out = await run_reconcile_once(db, soroban, settings)
    await db.commit()
    await db.refresh(ag)
    assert out.alerts == 2 and ag.current_value == money.from_stroops(await soroban.value_in_base(1))
    assert ag.current_value < Decimal("915") and ag.high_water_value == Decimal("1000")
    assert {b.asset.code: b.balance for b in ag.balances} == {"XLM": Decimal("500"), "USDC": Decimal("145")}
    assert await _types(db, customer.id) == ["agreement_activated", "trade_executed", "drawdown_80"]
    assert "drawdown_80" in await _types(db, trader.id)
    out = await run_reconcile_once(db, soroban, settings)  # same condition -> no duplicate alert
    await db.commit()
    assert out.alerts == 0

    soroban.set_price(TESTNET_XLM, TESTNET_USDC, "0.40")  # value 500 + 362.5 = 862.5 -> 13.75% >= max drawdown
    out = await run_reconcile_once(db, soroban, settings)
    await db.commit()
    assert out.alerts == 2 and (await _types(db, customer.id))[-1] == "drawdown_100"

    # expiry reminders: 24h -> 1h -> overdue, each once
    ctx = IndexContext(db, settings, soroban)
    ag.end_time = datetime.now(UTC) + timedelta(hours=20)
    assert await check_alerts(ctx, ag, ag.current_value) == 2
    assert await check_alerts(ctx, ag, ag.current_value) == 0
    ag.end_time = datetime.now(UTC) + timedelta(minutes=30)
    assert await check_alerts(ctx, ag, ag.current_value) == 2
    ag.end_time = datetime.now(UTC) - timedelta(minutes=1)
    assert await check_alerts(ctx, ag, ag.current_value) == 2
    assert await check_alerts(ctx, ag, ag.current_value) == 0
    await db.commit()
    assert (await _types(db, trader.id))[-3:] == ["expiry_24h", "expiry_1h", "settle_now"]


async def test_reconcile_syncs_a_missed_settlement(db, settings, make_user, seed_assets, soroban):
    ckp, tkp = Keypair.random(), Keypair.random()
    customer, _ = await make_user("customer", keypair=ckp)
    trader, _ = await make_user("trader", keypair=tkp)
    ag = await make_agreement(db, customer, trader, seed_assets["XLM"])
    await chain_open_and_accept(soroban, ag, ckp, tkp)
    await _run(db, soroban, settings)
    await db.refresh(ag)
    assert ag.status is AgreementStatus.active
    # settlement happens on-chain but the events are never indexed (cursor moved past them)
    await chain_submit(soroban, await soroban.build_settle(ckp.public_key, 1, []), ckp)
    state = await db.get(IndexerState, VAULT_EVENTS_KEY)
    state.cursor = soroban.events[-1].id
    await db.commit()
    out = await run_reconcile_once(db, soroban, settings)
    await db.commit()
    assert out.status_synced == 1
    await db.refresh(ag)
    assert ag.status is AgreementStatus.settled and ag.final_value == Decimal("1000") and ag.customer_payout == Decimal("1000")
    assert ag.balances == [] and ag.settled_at is not None
    trader_row = await db.get(User, trader.id)
    assert trader_row.active_agreements == 0


async def test_pending_housekeeping(db, settings, make_user, seed_assets, soroban):
    ckp, tkp = Keypair.random(), Keypair.random()
    customer, _ = await make_user("customer", keypair=ckp)
    trader, _ = await make_user("trader", keypair=tkp)
    ag = await make_agreement(db, customer, trader, seed_assets["XLM"])
    unsigned = await soroban.build_open(ckp.public_key, build_terms(ag))
    stale = PendingTransaction(
        user_id=customer.id, kind=PendingTxKind.propose, agreement_id=ag.id, unsigned_xdr="AAAA", tx_hash=None,
        status=PendingTxStatus.built, payload={}, expires_at=datetime.now(UTC) - timedelta(minutes=2),
    )
    # submitted by the app but the API never saw the result (client disconnected)
    submitted = PendingTransaction(
        user_id=customer.id, kind=PendingTxKind.open, agreement_id=ag.id, unsigned_xdr=unsigned.xdr, tx_hash=unsigned.hash,
        status=PendingTxStatus.submitted, payload={"action": "open"}, expires_at=datetime.now(UTC) + timedelta(minutes=3),
        submitted_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    # submitted long ago, never included on-chain
    dropped = PendingTransaction(
        user_id=customer.id, kind=PendingTxKind.open, agreement_id=None, unsigned_xdr="AAAA", tx_hash="00" * 32,
        status=PendingTxStatus.submitted, payload={}, expires_at=datetime.now(UTC) - timedelta(minutes=30),
        submitted_at=datetime.now(UTC) - timedelta(minutes=40),
    )
    db.add_all([stale, submitted, dropped])
    await db.commit()
    await chain_submit(soroban, unsigned, ckp)  # lands on-chain while the API was not looking

    out = await _run(db, soroban, settings)
    # the `opened` event already finalised the submitted row (matched by tx hash); only the dropped one is left
    assert out.pending_expired == 1 and out.pending_finalized == 1 and out.events_applied == 1
    for row in (stale, submitted, dropped):
        await db.refresh(row)
    assert stale.status is PendingTxStatus.expired
    assert submitted.status is PendingTxStatus.success and submitted.result["status"] == "SUCCESS"
    assert dropped.status is PendingTxStatus.failed and dropped.result["error"] == "not_included"
    await db.refresh(ag)
    assert ag.status is AgreementStatus.funded and ag.onchain_id == 1
