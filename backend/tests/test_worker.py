"""Worker supervisor (app.worker.main): job registry, one tick per job against the fakes / test DB,
error isolation with backoff, graceful stop of the supervisor and the CLI surface."""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.models import (
    AuthNonce,
    Listing,
    ListingKind,
    ListingStatus,
    Notification,
    NotificationCategory,
    Offer,
    OfferDirection,
    OfferStatus,
    PendingTransaction,
    PendingTxKind,
    PendingTxStatus,
)
from app.services import fx
from app.worker import main as worker

# --- registry ---------------------------------------------------------------------------------------------


def test_registry_matches_design(settings):
    assert worker.JOB_NAMES == ("indexer", "reconciler", "anchor_sync", "expiry", "push", "fx")
    for job in worker.JOBS:
        assert asyncio.iscoroutinefunction(job.body)
        assert job.interval(settings) >= 1
    assert worker.get_job("indexer").interval(settings) == settings.indexer_poll_seconds
    assert worker.get_job("reconciler").interval(settings) == settings.reconcile_seconds
    assert worker.get_job("anchor_sync").interval(settings) == settings.anchor_sync_seconds
    with pytest.raises(ValueError, match="unknown job"):
        worker.get_job("nope")
    assert [j.name for j in worker.select_jobs("fx,indexer")] == ["indexer", "fx"]
    assert [j.name for j in worker.select_jobs(["expiry"])] == ["expiry"]
    assert len(worker.select_jobs(None)) == len(worker.JOBS)
    with pytest.raises(ValueError):
        worker.select_jobs("indexer,bogus")


def test_push_interval_depends_on_flag(settings):
    on = settings.model_copy(update={"expo_push_enabled": True})
    off = settings.model_copy(update={"expo_push_enabled": False})
    assert worker.get_job("push").interval(on) == worker.PUSH_SECONDS
    assert worker.get_job("push").interval(off) == worker.PUSH_DISABLED_SECONDS


# --- ticks ----------------------------------------------------------------------------------------------


async def _offer(listing: Listing, trader_id, customer_id, asset_id, expires_at: datetime) -> Offer:
    return Offer(
        listing_id=listing.id,
        from_user_id=trader_id,
        to_user_id=customer_id,
        direction=OfferDirection.trader_to_customer,
        amount=Decimal("100"),
        base_asset_id=asset_id,
        duration_days=7,
        commission_bps=2000,
        max_drawdown_bps=1000,
        status=OfferStatus.pending,
        expires_at=expires_at,
    )


async def test_expiry_tick(db, settings, make_user, seed_assets, soroban):
    customer, _ = await make_user("customer")
    trader, _ = await make_user("trader")
    xlm = seed_assets["XLM"]
    now = datetime.now(UTC)
    listing = Listing(
        owner_id=customer.id, kind=ListingKind.capital, title="Sermaye ilanı", description="",
        markets=["crypto"], status=ListingStatus.active, amount=Decimal("100"), base_asset_id=xlm.id, duration_days=7,
    )
    db.add(listing)
    await db.flush()
    stale = await _offer(listing, trader.id, customer.id, xlm.id, now - timedelta(minutes=1))
    fresh = await _offer(listing, trader.id, customer.id, xlm.id, now + timedelta(hours=1))
    old_pending = PendingTransaction(
        user_id=customer.id, kind=PendingTxKind.open, unsigned_xdr="AAAA", status=PendingTxStatus.built,
        expires_at=now - timedelta(minutes=5),
    )
    live_pending = PendingTransaction(
        user_id=customer.id, kind=PendingTxKind.open, unsigned_xdr="AAAA", status=PendingTxStatus.built,
        expires_at=now + timedelta(minutes=5),
    )
    purged_nonce = AuthNonce(
        public_key=customer.stellar_address, nonce=uuid.uuid4().hex, expires_at=now - timedelta(hours=2),
        created_at=now - timedelta(hours=2),
    )
    kept_nonce = AuthNonce(  # expired, but inside the retention window (login errors stay explainable)
        public_key=customer.stellar_address, nonce=uuid.uuid4().hex, expires_at=now - timedelta(minutes=10),
        created_at=now - timedelta(minutes=15),
    )
    db.add_all([stale, fresh, old_pending, live_pending, purged_nonce, kept_nonce])
    await db.commit()

    result = await worker.run_once("expiry", db=db, settings=settings, soroban=soroban)
    await db.commit()

    assert result == {"offers": 1, "pending_transactions": 1, "nonces": 1}
    for row in (stale, fresh, old_pending, live_pending):
        await db.refresh(row)
    assert stale.status is OfferStatus.expired and stale.responded_at is not None
    assert fresh.status is OfferStatus.pending
    assert old_pending.status is PendingTxStatus.expired and old_pending.result["status"] == "EXPIRED"
    assert live_pending.status is PendingTxStatus.built
    nonces = set((await db.execute(select(AuthNonce.nonce))).scalars().all())
    assert nonces == {kept_nonce.nonce}
    types = (await db.execute(select(Notification.type).where(Notification.user_id == trader.id))).scalars().all()
    assert types == ["offer_expired"]


async def test_run_once_without_db_commits_its_own_session(db, settings, make_user, soroban):
    customer, _ = await make_user("customer")
    row = PendingTransaction(
        user_id=customer.id, kind=PendingTxKind.trade, unsigned_xdr="AAAA", status=PendingTxStatus.built,
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    db.add(row)
    await db.commit()

    result = await worker.run_once("expiry", settings=settings, soroban=soroban)

    assert result["pending_transactions"] == 1
    await db.refresh(row)
    assert row.status is PendingTxStatus.expired


async def test_indexer_and_reconciler_ticks_with_fake_gateway(db, settings, soroban):
    out = await worker.run_once("indexer", db=db, settings=settings, soroban=soroban)
    assert out.skipped is False and out.errors == [] and out.events_seen == 0
    assert worker.describe(out)["events_seen"] == 0
    rec = await worker.run_once("reconciler", db=db, settings=settings, soroban=soroban)
    assert rec.checked == 0 and rec.errors == []


async def test_anchor_sync_tick_without_rows(db, settings, soroban):
    out = await worker.run_once("anchor_sync", db=db, settings=settings, soroban=soroban)
    assert out.checked == 0 and out.errors == []


async def test_push_tick_marks_rows_when_disabled(db, settings, make_user, soroban):
    user, _ = await make_user("customer", expo_push_token="ExponentPushToken[worker-test]")
    n = Notification(user_id=user.id, category=NotificationCategory.agreement, type="t", title="Merhaba", body="", data={})
    db.add(n)
    await db.commit()

    off = settings.model_copy(update={"expo_push_enabled": False})
    counters = await worker.run_once("push", db=db, settings=off, soroban=soroban)
    await db.commit()

    assert counters == {"sent": 0, "failed": 0, "retry": 0, "disabled": 1}
    await db.refresh(n)
    assert n.data["push_sent"] is False and n.data["push_error"] == "disabled"
    # nothing left to deliver on the next tick
    assert await worker.run_once("push", db=db, settings=off, soroban=soroban) == {
        "sent": 0, "failed": 0, "retry": 0, "disabled": 0,
    }


async def test_push_tick_sends_when_enabled(db, settings, make_user, soroban):
    respx = pytest.importorskip("respx")
    user, _ = await make_user("trader", expo_push_token="ExponentPushToken[worker-test]")
    n = Notification(user_id=user.id, category=NotificationCategory.offer, type="offer_received", title="Yeni teklif", body="x", data={})
    db.add(n)
    await db.commit()
    on = settings.model_copy(update={"expo_push_enabled": True})

    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(on.expo_push_url).mock(
            return_value=httpx.Response(200, json={"data": [{"status": "ok", "id": "ticket-1"}]})
        )
        counters = await worker.run_once("push", db=db, settings=on, soroban=soroban)
    await db.commit()

    assert counters == {"sent": 1, "failed": 0, "retry": 0, "disabled": 0}
    assert route.called
    await db.refresh(n)
    assert n.data["push_sent"] is True


async def test_fx_tick_forces_a_refresh(db, settings, soroban, monkeypatch):
    calls: list[dict] = []
    rate = fx.FxRate(rate=Decimal("41.5000000"), source="test", fetched_at=datetime.now(UTC))

    async def fake_get_usd_try(settings_, db_=None, **kw):
        calls.append({"db": db_ is not None, **kw})
        return rate

    monkeypatch.setattr(fx, "get_usd_try", fake_get_usd_try)
    out = await worker.run_once("fx", db=db, settings=settings, soroban=soroban)
    assert out == {"pair": "USDTRY", "rate": "41.5000000", "source": "test", "stale": False}
    assert calls == [{"db": True, "force": True}]


# --- supervisor -----------------------------------------------------------------------------------------


async def test_errors_are_isolated_and_backed_off(settings, soroban):
    async def boom(ctx):
        raise RuntimeError("kaboom")

    async def ok(ctx):
        return {"ok": True}

    deps = worker.WorkerDeps(settings, soroban)
    job = worker.Job("boom", boom, lambda s: 5.0)
    stats = worker.JobStats()
    assert await worker.run_guarded(job, deps, stats) is None
    assert await worker.run_guarded(job, deps, stats) is None
    assert stats.runs == 2 and stats.errors == 2 and stats.consecutive_errors == 2
    assert "RuntimeError: kaboom" in (stats.last_error or "")
    assert worker.next_delay(job, settings, stats) == 10.0  # 5s doubled once
    stats.consecutive_errors = 50
    assert worker.next_delay(job, settings, stats) == worker.MAX_BACKOFF_SECONDS

    fine = worker.Job("fine", ok, lambda s: 5.0)
    assert await worker.run_guarded(fine, deps, stats) == {"ok": True}
    assert stats.consecutive_errors == 0 and stats.last_error is None and stats.last_ok_at is not None
    assert worker.next_delay(fine, settings, stats) == 5.0
    assert "runs=3 err=2" in stats.summary()


async def test_serve_runs_loops_and_stops_gracefully(settings, soroban):
    stop = asyncio.Event()
    ticks = {"a": 0, "b": 0}

    async def tick_a(ctx):
        ticks["a"] += 1
        if ticks["a"] >= 3:
            stop.set()
        return ticks["a"]

    async def tick_b(ctx):
        ticks["b"] += 1
        raise RuntimeError("b always fails")

    jobs = [worker.Job("a", tick_a, lambda s: 0.01), worker.Job("b", tick_b, lambda s: 0.01)]
    deps = worker.WorkerDeps(settings, soroban)
    stats = await asyncio.wait_for(
        worker.serve(deps, jobs=jobs, stop=stop, heartbeat_seconds=0.02, install_signal_handlers=False, grace_seconds=5),
        timeout=15,
    )
    assert stats["a"].runs == 3 and stats["a"].errors == 0 and stats["a"].last_result == 3
    assert stats["b"].runs >= 1 and stats["b"].errors == stats["b"].runs
    assert stop.is_set()


async def test_serve_with_no_jobs_returns_immediately(settings, soroban):
    stats = await asyncio.wait_for(
        worker.serve(worker.WorkerDeps(settings, soroban), jobs=[], install_signal_handlers=False), timeout=5
    )
    assert stats == {}


# --- CLI ------------------------------------------------------------------------------------------------


def test_cli_parsing_and_list(capsys):
    args = worker.parse_args(["--once", "expiry", "fx", "--heartbeat", "0"])
    assert args.once == ["expiry", "fx"] and args.heartbeat == 0
    assert worker.parse_args(["--jobs", "indexer,fx"]).jobs == "indexer,fx"
    assert worker.main(["--list"]) == 0
    out = capsys.readouterr().out
    for name in worker.JOB_NAMES:
        assert name in out
    assert worker.main(["--jobs", "bogus"]) == 2
    assert worker.main(["--once", "bogus"]) == 2


def test_describe_is_json_friendly():
    from app.services.indexer import IndexerRunResult

    assert worker.describe(None) is None
    assert worker.describe({"a": Decimal("1")}) == {"a": "1"}
    assert worker.describe(IndexerRunResult(skipped=True, reason="x"))["reason"] == "x"
    assert worker.describe(worker.JobStats(runs=2))["runs"] == 2
