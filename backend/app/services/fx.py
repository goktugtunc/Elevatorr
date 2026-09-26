"""USD -> TRY rate for TL display (DESIGN §0 "Money", §2.3 `GET /fx`).

* `get_usd_try(settings, db=None)`: in-process cache (`fx_cache_seconds`), primary then secondary
  HTTP source, and an `indexer_state` row (`key="fx_usd_try"`, rate kept in `last_block_hash`) as the
  persisted fallback / warm start. When every source fails the last known value is returned with `stale=True`; with no value at
  all `FxUnavailableError` (503) is raised.
* `to_try(amount_usd, rate)`: TL amount (2 dp, ROUND_DOWN).
* `usd_price(symbol)` / `usd_price_map(symbols)`: INDICATIVE USD prices per asset symbol — the MockRouter
  deploy prices (tUSDC 1, tWETH 3000, tWBTC 60000) overridable with `settings.usd_prices_json`; the native
  coin (MON) is `settings.mon_usd_price` (None = unknown, BE-14). Nothing here touches the chain.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.models import IndexerState

log = logging.getLogger(__name__)

FX_STATE_KEY = "fx_usd_try"
RATE_UNIT = Decimal("0.0000001")
TRY_UNIT = Decimal("0.01")
HTTP_TIMEOUT = httpx.Timeout(8.0)

# Indicative USD prices per asset symbol (MockRouter deploy prices, NOT market data); `settings.usd_prices_json`
# overrides the table at runtime (see `usd_price`).
INDICATIVE_USD_PRICES: dict[str, Decimal] = {
    "tUSDC": Decimal("1"),
    "tWETH": Decimal("3000"),
    "tWBTC": Decimal("60000"),
}
INDICATIVE_NOTE = (
    "usd_prices are indicative placeholders (Monad Testnet MockRouter prices: tUSDC=1, tWETH=3000, "
    "tWBTC=60000); MON has no off-chain USD price unless MON_USD_PRICE is configured (null otherwise). "
    "USD->TRY comes from the configured FX sources."
)


class FxUnavailableError(AppError):
    status_code = 503
    code = "fx_unavailable"


@dataclass(frozen=True)
class FxRate:
    rate: Decimal  # TRY per 1 USD, 7 dp
    source: str  # "primary" | "secondary" | "db" | "cache"
    fetched_at: datetime
    stale: bool = False

    @property
    def pair(self) -> str:
        return "USDTRY"


_cache: FxRate | None = None


def reset_cache() -> None:
    """Test hook."""
    global _cache
    _cache = None


def cached_rate() -> FxRate | None:
    return _cache


def _parse_try(payload: Any) -> Decimal:
    """Both configured sources answer `{"rates": {"TRY": <number>}}`."""
    if not isinstance(payload, dict):
        raise ValueError("payload is not an object")
    rates = payload.get("rates")
    if not isinstance(rates, dict) or "TRY" not in rates:
        raise ValueError("no rates.TRY in payload")
    try:
        rate = Decimal(str(rates["TRY"])).quantize(RATE_UNIT, rounding=ROUND_DOWN)
    except (InvalidOperation, ValueError) as e:
        raise ValueError(f"rates.TRY is not a number: {rates['TRY']!r}") from e
    if not rate.is_finite() or rate <= 0:
        raise ValueError(f"rates.TRY out of range: {rate}")
    return rate


async def _fetch_one(client: httpx.AsyncClient, url: str) -> Decimal:
    resp = await client.get(url, headers={"Accept": "application/json", "User-Agent": "traderkirala-api/2.0"})
    resp.raise_for_status()
    return _parse_try(resp.json())


async def fetch_usd_try(settings: Settings, *, client: httpx.AsyncClient | None = None) -> FxRate:
    """Primary source, then secondary. Raises FxUnavailableError when both fail."""
    sources = [("primary", settings.fx_primary_url), ("secondary", settings.fx_secondary_url)]
    errors: dict[str, str] = {}
    own = client is None
    http = client or httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True)
    try:
        for name, url in sources:
            if not url:
                continue
            try:
                rate = await _fetch_one(http, url)
            except (httpx.HTTPError, ValueError) as e:
                errors[name] = f"{e.__class__.__name__}: {str(e)[:120]}"
                log.warning("fx %s source failed: %s", name, errors[name])
                continue
            return FxRate(rate=rate, source=name, fetched_at=datetime.now(UTC))
    finally:
        if own:
            await http.aclose()
    raise FxUnavailableError("USD/TRY rate unavailable from all sources", details={"errors": errors})


# The persisted rate lives in `indexer_state.last_block_hash` (String(66)) as "<rate>|<source>": the Monad cutover
# dropped the free-text `cursor` column and a dedicated table is not worth it for one cached number (KARAR).
async def _load_db_rate(db: AsyncSession) -> FxRate | None:
    row = await db.get(IndexerState, FX_STATE_KEY)
    if row is None or not row.last_block_hash:
        return None
    raw, _, source = row.last_block_hash.partition("|")
    try:
        rate = Decimal(raw).quantize(RATE_UNIT, rounding=ROUND_DOWN)
    except (InvalidOperation, ValueError):
        return None
    if rate <= 0:
        return None
    return FxRate(rate=rate, source=f"db:{source or 'unknown'}", fetched_at=row.updated_at, stale=True)


async def _store_db_rate(db: AsyncSession, fx: FxRate) -> None:
    row = await db.get(IndexerState, FX_STATE_KEY)
    stored = f"{fx.rate}|{fx.source}"[:66]
    if row is None:
        db.add(IndexerState(key=FX_STATE_KEY, block_number=None, last_block_hash=stored, updated_at=fx.fetched_at))
    else:
        row.last_block_hash = stored
        row.updated_at = fx.fetched_at
    await db.flush()


def _is_fresh(fx: FxRate | None, ttl_seconds: int, now: datetime) -> bool:
    return fx is not None and not fx.stale and (now - fx.fetched_at).total_seconds() < ttl_seconds


async def get_usd_try(
    settings: Settings,
    db: AsyncSession | None = None,
    *,
    force: bool = False,
    client: httpx.AsyncClient | None = None,
) -> FxRate:
    """Cached USD->TRY rate. Order: fresh in-process cache -> HTTP sources (persisted to
    `indexer_state` when `db` is given) -> stale cache -> `indexer_state` row -> 503."""
    global _cache
    now = datetime.now(UTC)
    if not force and _is_fresh(_cache, settings.fx_cache_seconds, now):
        return _cache  # type: ignore[return-value]
    try:
        fx = await fetch_usd_try(settings, client=client)
    except FxUnavailableError as e:
        if _cache is not None:
            stale = replace(_cache, stale=True)
            log.warning("fx: serving stale cached rate from %s", _cache.fetched_at.isoformat())
            return stale
        if db is not None:
            from_db = await _load_db_rate(db)
            if from_db is not None:
                log.warning("fx: serving persisted rate from %s", from_db.fetched_at.isoformat())
                _cache = from_db
                return from_db
        raise e
    _cache = fx
    if db is not None:
        try:
            await _store_db_rate(db, fx)
        except Exception:  # persistence is best-effort; the rate itself is what matters
            log.exception("fx: could not persist rate")
    return fx


def to_try(amount_usd: Decimal | int | str, rate: FxRate | Decimal) -> Decimal:
    """USD amount -> TL amount (2 dp, ROUND_DOWN)."""
    r = rate.rate if isinstance(rate, FxRate) else Decimal(rate)
    return (Decimal(amount_usd) * r).quantize(TRY_UNIT, rounding=ROUND_DOWN)


def _price_table(settings: Settings | None) -> dict[str, Decimal]:
    """Symbol (upper-cased key) -> USD price; `settings.usd_prices` overrides the module defaults."""
    table: dict[str, Decimal] = {k.upper(): v for k, v in INDICATIVE_USD_PRICES.items()}
    if settings is not None:
        for sym, raw in settings.usd_prices.items():
            try:
                table[sym.upper()] = Decimal(str(raw))
            except (InvalidOperation, ValueError):
                log.warning("fx: ignoring non-numeric usd price for %s: %r", sym, raw)
    return table


def usd_price(symbol: str, settings: Settings | None = None) -> Decimal | None:
    """Indicative USD price of an asset symbol (case-insensitive); None when unknown.

    The native coin (`settings.native_symbol`, MON) comes from `settings.mon_usd_price` and is None unless
    configured (BE-14). `settings` defaults to the process settings.
    """
    if settings is None:
        settings = get_settings()
    sym = (symbol or "").strip()
    if not sym:
        return None
    if sym.upper() == settings.native_symbol.upper():
        return Decimal(settings.mon_usd_price) if settings.mon_usd_price is not None else None
    return _price_table(settings).get(sym.upper())


def usd_price_map(symbols: Iterable[str], settings: Settings | None = None) -> dict[str, Decimal | None]:
    if settings is None:
        settings = get_settings()
    return {sym: usd_price(sym, settings) for sym in symbols}


def try_value(
    amount: Decimal, symbol: str, rate: FxRate | Decimal, settings: Settings | None = None
) -> Decimal | None:
    """TL equivalent of `amount` of asset `symbol` using the indicative USD price; None when unknown."""
    p = usd_price(symbol, settings)
    if p is None:
        return None
    return to_try(Decimal(amount) * p, rate)


__all__ = [
    "FX_STATE_KEY",
    "INDICATIVE_NOTE",
    "INDICATIVE_USD_PRICES",
    "FxRate",
    "FxUnavailableError",
    "cached_rate",
    "fetch_usd_try",
    "get_usd_try",
    "reset_cache",
    "to_try",
    "try_value",
    "usd_price",
    "usd_price_map",
]
