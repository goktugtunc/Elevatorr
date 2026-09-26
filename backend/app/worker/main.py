"""Background worker — `python -m app.worker.main` (03-backend-tasarim §5.7).

One asyncio process supervises independent periodic jobs. Every tick runs in its own database session
and commits on success; a failing tick is logged and retried after a backed-off interval — a job can
never bring the process down. SIGTERM / SIGINT stop the loops gracefully (running ticks finish, then
the RPC client is closed). A heartbeat line summarises every job periodically.

| job             | interval (settings)          | body                                                              |
|-----------------|------------------------------|-------------------------------------------------------------------|
| indexer         | indexer_poll_seconds (5 s)   | services.indexer.run_indexer_once — vault events → DB mirror      |
| pending_tracker | pending_tracker_seconds (5 s)| expire_pending + track_submitted + recheck_recent_confirmed (§4.6)|
| reconciler      | reconcile_seconds (60 s)     | services.indexer.run_reconcile_once — live values, alerts         |
| expiry          | worker_offer_expiry_seconds  | offers.expire_stale + login nonce purge                           |
| push            | 10 s (60 s when disabled)    | notifications.deliver_pending_pushes — Expo push                  |
| fx              | fx_cache_seconds (600 s)     | fx.get_usd_try(force=True) — USD/TRY refresh + persist            |

`run_once("indexer")` runs a single tick in-process (tests; ops: `python -m app.worker.main --once
indexer fx`). Run exactly one worker replica: jobs are not leader-elected (row locks make a double run
safe, not useful).
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import signal
import sys
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import setup_logging
from app.services import fx
from app.services.auth import purge_expired_nonces
from app.services.chain import get_chain
from app.services.indexer import (
    IndexContext,
    expire_pending,
    recheck_recent_confirmed,
    run_indexer_once,
    run_reconcile_once,
    track_submitted,
)
from app.services.notifications import deliver_pending_pushes
from app.services.offers import expire_stale

log = logging.getLogger("app.worker")

PUSH_SECONDS = 10.0
PUSH_DISABLED_SECONDS = 60.0
HEARTBEAT_SECONDS = 30.0
SHUTDOWN_GRACE_SECONDS = 30.0
MIN_INTERVAL_SECONDS = 0.05
MAX_BACKOFF_SECONDS = 300.0
NONCE_RETENTION = timedelta(hours=1)


# --- job model ------------------------------------------------------------------------------------


@dataclass
class JobContext:
    """What a job body receives: a session (flush only — the runner commits), settings, the chain
    gateway (Monad or fake) and the tick's wall-clock time."""

    db: AsyncSession
    settings: Settings
    chain: Any
    now: datetime


JobBody = Callable[[JobContext], Awaitable[Any]]


@dataclass(frozen=True)
class Job:
    name: str
    body: JobBody
    interval: Callable[[Settings], float]
    description: str = ""


@dataclass
class JobStats:
    runs: int = 0
    errors: int = 0
    consecutive_errors: int = 0
    last_started_at: datetime | None = None
    last_ok_at: datetime | None = None
    last_duration_ms: float = 0.0
    last_error: str | None = None
    last_result: Any = None

    def summary(self) -> str:
        s = f"runs={self.runs} err={self.errors} last={self.last_duration_ms:.0f}ms"
        if self.consecutive_errors:
            s += f" failing={self.consecutive_errors} last_error={self.last_error!r}"
        return s


@dataclass
class WorkerDeps:
    settings: Settings
    chain: Any
    extra: dict[str, Any] = field(default_factory=dict)


# --- job bodies -----------------------------------------------------------------------------------


async def job_indexer(ctx: JobContext) -> Any:
    """Vault events (eth_getLogs, `confirmations` deep) → agreements / trades / balances / listings mirror."""
    return await run_indexer_once(ctx.db, ctx.chain, ctx.settings)


async def job_pending_tracker(ctx: JobContext) -> dict[str, int]:
    """Pending transactions: never-signed → expired; submitted → receipt → finalize; confirmed near the head
    re-checked against the block hash (reorg)."""
    ictx = IndexContext(ctx.db, ctx.settings, ctx.chain)
    expired = await expire_pending(ictx)
    tracked = await track_submitted(ictx)
    rechecked = await recheck_recent_confirmed(ictx)
    return {"expired": expired, "tracked": tracked, "rechecked": rechecked}


async def job_reconciler(ctx: JobContext) -> Any:
    """Every open agreement: status + value from the contract, snapshots, drawdown / expiry alerts."""
    return await run_reconcile_once(ctx.db, ctx.chain, ctx.settings)


async def job_expiry(ctx: JobContext) -> dict[str, int]:
    """Offers past `expires_at` and stale login nonces."""
    offers = await expire_stale(ctx.db, ctx.now)
    nonces = await purge_expired_nonces(ctx.db, older_than=ctx.now - NONCE_RETENTION)
    return {"offers": offers, "nonces": nonces}


async def job_push(ctx: JobContext) -> dict[str, int]:
    """Unsent notifications → Expo push (the service marks rows `disabled` when pushes are off)."""
    return await deliver_pending_pushes(ctx.db, ctx.settings)


async def job_fx(ctx: JobContext) -> dict[str, Any]:
    """Refresh the USD→TRY rate (primary/secondary sources) and persist it as the warm-start fallback."""
    rate = await fx.get_usd_try(ctx.settings, ctx.db, force=True)
    return {"pair": rate.pair, "rate": str(rate.rate), "source": rate.source, "stale": rate.stale}


def _push_interval(s: Settings) -> float:
    return PUSH_SECONDS if s.expo_push_enabled else PUSH_DISABLED_SECONDS


JOBS: tuple[Job, ...] = (
    Job("indexer", job_indexer, lambda s: s.indexer_poll_seconds, "vault events → DB mirror"),
    Job("pending_tracker", job_pending_tracker, lambda s: s.pending_tracker_seconds, "pending tx receipts"),
    Job("reconciler", job_reconciler, lambda s: s.reconcile_seconds, "live values, snapshots, alerts"),
    Job("expiry", job_expiry, lambda s: s.worker_offer_expiry_seconds, "offers, nonces"),
    Job("push", job_push, _push_interval, "Expo push delivery"),
    Job("fx", job_fx, lambda s: max(60.0, float(s.fx_cache_seconds)), "USD/TRY refresh"),
)
JOB_NAMES: tuple[str, ...] = tuple(j.name for j in JOBS)


def get_job(name: str) -> Job:
    for job in JOBS:
        if job.name == name:
            return job
    raise ValueError(f"unknown job {name!r}; known jobs: {', '.join(JOB_NAMES)}")


def select_jobs(spec: str | Iterable[str] | None) -> list[Job]:
    """`"indexer,fx"` / `["indexer", "fx"]` → jobs in registry order; None/empty → all."""
    if not spec:
        return list(JOBS)
    names = [n.strip() for n in (spec.split(",") if isinstance(spec, str) else spec) if n and n.strip()]
    for n in names:
        get_job(n)  # raises for unknown names
    return [j for j in JOBS if j.name in names]


# --- running a tick ---------------------------------------------------------------------------------


async def run_job(job: Job, deps: WorkerDeps, *, db: AsyncSession | None = None) -> Any:
    """One tick of `job`. With `db` the caller owns the transaction (flush only); without it the tick
    gets its own session, committed on success and rolled back on error. Exceptions propagate."""
    now = datetime.now(UTC)
    if db is not None:
        return await job.body(JobContext(db, deps.settings, deps.chain, now))
    from app.db.session import get_session_factory

    async with get_session_factory()() as session:
        try:
            result = await job.body(JobContext(session, deps.settings, deps.chain, now))
            await session.commit()
            return result
        except BaseException:
            await session.rollback()
            raise


async def run_guarded(job: Job, deps: WorkerDeps, stats: JobStats) -> Any | None:
    """`run_job` with bookkeeping; never raises (cancellation excepted). Returns None on failure."""
    stats.runs += 1
    stats.last_started_at = datetime.now(UTC)
    started = time.perf_counter()
    try:
        result = await run_job(job, deps)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 - the whole point: a job error is never fatal
        stats.errors += 1
        stats.consecutive_errors += 1
        stats.last_error = f"{type(e).__name__}: {str(e)[:200]}"
        stats.last_duration_ms = (time.perf_counter() - started) * 1000
        level = (
            logging.ERROR
            if stats.consecutive_errors in (1, 5, 20) or stats.consecutive_errors % 100 == 0
            else logging.WARNING
        )
        log.log(
            level, "job %s failed (%d in a row): %s", job.name, stats.consecutive_errors, stats.last_error,
            exc_info=stats.consecutive_errors == 1,
        )
        return None
    stats.consecutive_errors = 0
    stats.last_error = None
    stats.last_ok_at = datetime.now(UTC)
    stats.last_duration_ms = (time.perf_counter() - started) * 1000
    stats.last_result = result
    log.debug("job %s ok in %.0fms: %s", job.name, stats.last_duration_ms, describe(result))
    return result


async def run_once(
    job: str | Job, *, db: AsyncSession | None = None, settings: Settings | None = None, chain: Any | None = None
) -> Any:
    """Run a single tick of `job` (name or Job) and return its result. Tests pass their session (flush
    only); without `db` the tick commits its own session. Errors propagate."""
    j = job if isinstance(job, Job) else get_job(job)
    deps = WorkerDeps(settings or get_settings(), chain if chain is not None else get_chain())
    return await run_job(j, deps, db=db)


def describe(result: Any) -> Any:
    """JSON-friendly view of a job result (dataclasses with `as_dict`, plain dataclasses, dicts, scalars)."""
    if result is None:
        return None
    as_dict = getattr(result, "as_dict", None)
    if callable(as_dict):
        return as_dict()
    if dataclasses.is_dataclass(result) and not isinstance(result, type):
        return {k: describe(v) for k, v in dataclasses.asdict(result).items()}
    if isinstance(result, dict):
        return {str(k): describe(v) for k, v in result.items()}
    if isinstance(result, list | tuple):
        return [describe(v) for v in result]
    if isinstance(result, str | int | float | bool):
        return result
    return str(result)


# --- loops --------------------------------------------------------------------------------------------


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=max(0.0, seconds))
    except TimeoutError:
        pass


def next_delay(job: Job, settings: Settings, stats: JobStats) -> float:
    """Configured interval, doubled per consecutive failure (capped) so a dead RPC is not hammered."""
    base = max(MIN_INTERVAL_SECONDS, float(job.interval(settings)))
    if stats.consecutive_errors == 0:
        return base
    return min(base * (2 ** min(stats.consecutive_errors - 1, 6)), MAX_BACKOFF_SECONDS)


async def job_loop(job: Job, deps: WorkerDeps, stats: JobStats, stop: asyncio.Event) -> None:
    log.info("job %s started (every %ss)", job.name, job.interval(deps.settings))
    while not stop.is_set():
        await run_guarded(job, deps, stats)
        await _sleep_or_stop(stop, next_delay(job, deps.settings, stats))
    log.info("job %s stopped after %d runs (%d errors)", job.name, stats.runs, stats.errors)


async def heartbeat_loop(stats: dict[str, JobStats], stop: asyncio.Event, interval: float) -> None:
    while not stop.is_set():
        await _sleep_or_stop(stop, interval)
        log.info("heartbeat %s", " ".join(f"{name}[{s.summary()}]" for name, s in stats.items()))


def _on_task_done(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:  # a loop itself died — should not happen; make it visible
        log.error("task %s crashed: %r", task.get_name(), exc, exc_info=exc)


async def _close(deps: WorkerDeps) -> None:
    close = getattr(deps.chain, "close", None)
    if callable(close):
        try:
            await close()
        except Exception:  # pragma: no cover - best effort
            log.debug("chain gateway close failed", exc_info=True)


async def serve(
    deps: WorkerDeps | None = None,
    *,
    jobs: Iterable[Job] | None = None,
    stop: asyncio.Event | None = None,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
    install_signal_handlers: bool = True,
    grace_seconds: float = SHUTDOWN_GRACE_SECONDS,
) -> dict[str, JobStats]:
    """Supervise the job loops until `stop` is set (SIGTERM/SIGINT set it). Returns the per-job stats."""
    deps = deps or WorkerDeps(get_settings(), get_chain())
    jobs = list(JOBS if jobs is None else jobs)
    stop = stop or asyncio.Event()
    stats = {j.name: JobStats() for j in jobs}
    if not jobs:
        log.warning("no jobs selected; nothing to do")
        return stats

    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    if install_signal_handlers:
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _request_stop, stop, sig)
                installed.append(sig)
            except (NotImplementedError, RuntimeError):  # pragma: no cover - non-unix / nested loops
                pass

    s = deps.settings
    log.info(
        "worker starting jobs=%s chain_id=%s rpc=%s vault=%s push=%s",
        ",".join(j.name for j in jobs), s.chain_id, s.rpc_url,
        getattr(deps.chain, "vault_address", None) or s.vault_address or "-", s.expo_push_enabled,
    )
    tasks = [asyncio.create_task(job_loop(j, deps, stats[j.name], stop), name=f"job:{j.name}") for j in jobs]
    if heartbeat_seconds > 0:
        tasks.append(asyncio.create_task(heartbeat_loop(stats, stop, heartbeat_seconds), name="heartbeat"))
    for t in tasks:
        t.add_done_callback(_on_task_done)
    try:
        await stop.wait()
        log.info("worker stopping: waiting up to %.0fs for running ticks", grace_seconds)
        _, pending = await asyncio.wait(tasks, timeout=grace_seconds)
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    finally:
        for sig in installed:
            loop.remove_signal_handler(sig)
        await _close(deps)
    log.info("worker stopped: %s", " ".join(f"{n}[{st.summary()}]" for n, st in stats.items()))
    return stats


def _request_stop(stop: asyncio.Event, sig: signal.Signals) -> None:
    if not stop.is_set():
        log.info("received %s: shutting down gracefully", sig.name)
        stop.set()


# --- CLI ------------------------------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m app.worker.main",
        description="TraderKirala background worker: indexer, pending_tracker, reconciler, expiry, push, fx.",
    )
    p.add_argument("--once", nargs="+", metavar="JOB", help="run the given job(s) one time and exit (non-zero on error)")
    p.add_argument("--jobs", metavar="A,B", help="comma-separated subset of jobs to supervise (default: all)")
    p.add_argument("--list", action="store_true", help="print the job table and exit")
    p.add_argument(
        "--heartbeat", type=float, default=HEARTBEAT_SECONDS, metavar="SECONDS", help="heartbeat log interval (0 = off)"
    )
    return p.parse_args(argv)


def job_table(settings: Settings) -> str:
    rows = [f"{j.name:<16} every {j.interval(settings):>6.0f}s  {j.description}" for j in JOBS]
    return "\n".join(rows)


async def _once_cli(names: list[str]) -> int:
    deps = WorkerDeps(get_settings(), get_chain())
    rc = 0
    try:
        for name in names:
            job = get_job(name)
            started = time.perf_counter()
            try:
                result = await run_job(job, deps)
            except Exception as e:  # noqa: BLE001 - report and continue with the next job
                rc = 1
                log.exception("job %s failed", name)
                print(json.dumps({"job": name, "ok": False, "error": f"{type(e).__name__}: {e}"}, default=str))
                continue
            print(json.dumps(
                {"job": name, "ok": True, "ms": round((time.perf_counter() - started) * 1000), "result": describe(result)},
                default=str,
            ))
    finally:
        await _close(deps)
        from app.db.session import dispose_engine

        await dispose_engine()
    return rc


async def _serve_cli(jobs: list[Job], heartbeat: float) -> int:
    try:
        await serve(jobs=jobs, heartbeat_seconds=heartbeat)
    finally:
        from app.db.session import dispose_engine

        await dispose_engine()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    setup_logging(settings.log_level)
    if args.list:
        print(job_table(settings))
        return 0
    try:
        jobs = select_jobs(args.jobs)
        if args.once:
            for n in args.once:
                get_job(n)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    if args.once:
        return asyncio.run(_once_cli(args.once))
    return asyncio.run(_serve_cli(jobs, args.heartbeat))


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "JOBS",
    "JOB_NAMES",
    "Job",
    "JobContext",
    "JobStats",
    "WorkerDeps",
    "describe",
    "get_job",
    "job_loop",
    "main",
    "next_delay",
    "parse_args",
    "run_guarded",
    "run_job",
    "run_once",
    "select_jobs",
    "serve",
]
