"""Shared pytest fixtures — Monad edition (03-backend-tasarim §8.1).

* Test settings: a few environment defaults are installed *before* anything imports `app` so the process-wide
  `Settings` point at the fake vault / router / deployment JSON (`os.environ.setdefault`, CI may override).
* Session-scoped async engine on `DATABASE_URL_TEST` (create_all at start, drop_all at end); `app.db.session`
  globals are pointed at it so `get_db` requests hit the test database. Every table is TRUNCATEd after each test.
* `client`: httpx.AsyncClient over the ASGI app (no lifespan). `db`: an AsyncSession (commit to make rows visible
  to API requests, which use their own sessions).
* `chain`: a `FakeChainGateway` (clock = wall clock) installed with `set_chain`; `make_user(role, **kw)` creates a
  profile for a fresh `eth_account` key (`user._account`) and returns `(User, bearer token)`; `seed_assets` seeds
  the allow-list from `tests/fixtures/deployment.fake.json`; `siwe_login(client, account)` runs the SIWE flow.
* FX is stubbed (fixed USD/TRY) unless a test is marked `real_fx`; the nonce rate limiter and the in-process
  caches are reset before every test.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

TESTS_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = TESTS_DIR / "fixtures"
DEPLOYMENT_FILE = FIXTURES_DIR / "deployment.fake.json"

# Test environment (before any `app` import; env vars beat the .env file in pydantic-settings).
_TEST_ENV = {
    "APP_ENV": "test",
    "LOG_LEVEL": "WARNING",
    "CHAIN_ID": "10143",
    "VAULT_ADDRESS": "0x00000000000000000000000000000000000000f1",
    "ROUTER_ADDRESS": "0x00000000000000000000000000000000000000f2",
    "PLATFORM_ADDRESS": "0x00000000000000000000000000000000000000a1",
    "SIWE_DOMAIN": "localhost",
    "SIWE_URI": "http://localhost",
    "DEPLOYMENTS_FILE": str(DEPLOYMENT_FILE),
    "MINTER_PRIVATE_KEY": "0x" + "11" * 32,  # the fake never interprets it; enables /wallet/faucet
    "FAUCET_AMOUNTS": json.dumps({"tUSDC": "1000", "tWETH": "0.5", "tWBTC": "0.02"}),
    "EXPO_PUSH_ENABLED": "false",
    "TX_SUBMIT_TIMEOUT_SECONDS": "2",
    "DOCS_ENABLED": "true",
}
for _k, _v in _TEST_ENV.items():
    os.environ.setdefault(_k, _v)

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from eth_account import Account  # noqa: E402
from eth_account.messages import encode_defunct  # noqa: E402
from eth_account.signers.local import LocalAccount  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

import app.db.session as db_session  # noqa: E402
from app.core.config import Settings, get_settings  # noqa: E402
from app.core.ratelimit import set_nonce_limiter  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.models import Asset, RiskLevel, RiskProfile, User, UserRole  # noqa: E402
from app.services import fx  # noqa: E402
from app.services.chain import set_chain  # noqa: E402
from app.services.chain.fake import FakeChainGateway  # noqa: E402

MakeUser = Callable[..., Awaitable[tuple[User, str]]]
API = "/api/v1"
FX_RATE = Decimal("41.5000000")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "real_fx: do not stub the USD/TRY fetch (the test drives fx itself)")


def _test_db_url(settings: Settings) -> str:
    url = os.environ.get("DATABASE_URL_TEST")
    if url:
        return url
    base, _, name = settings.database_url.rpartition("/")
    name = name.split("?", 1)[0]
    if not name.endswith("_test"):
        name = f"{name}_test"
    return f"{base}/{name}"


@pytest.fixture(scope="session")
def settings() -> Settings:
    return get_settings()


@pytest_asyncio.fixture(scope="session")
async def engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    url = _test_db_url(settings)
    assert url.endswith("_test"), f"refusing to run tests against a non-test database: {url}"
    eng = create_async_engine(url, poolclass=NullPool)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    # point the application at the test database
    db_session._engine = eng
    db_session._session_factory = async_sessionmaker(eng, expire_on_commit=False, autoflush=False)
    try:
        yield eng
    finally:
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await eng.dispose()
        db_session._engine = None
        db_session._session_factory = None


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables(engine: AsyncEngine) -> AsyncIterator[None]:
    """Truncate everything after each test (runs after the `db` session is closed)."""
    yield
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))


@pytest_asyncio.fixture
async def db(engine: AsyncEngine, _clean_tables: None) -> AsyncIterator[AsyncSession]:
    async with db_session.get_session_factory()() as session:
        try:
            yield session
        finally:
            await session.rollback()


@pytest_asyncio.fixture
async def client(engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# --- process-wide state reset ---------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_process_state() -> Iterator[None]:
    """Fresh nonce rate limiter, FX cache and contract-config cache for every test."""
    from app.routers.config import reset_contract_cache

    set_nonce_limiter(None)
    fx.reset_cache()
    reset_contract_cache()
    yield
    set_nonce_limiter(None)
    fx.reset_cache()
    reset_contract_cache()


@pytest.fixture(autouse=True)
def _fx_stub(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the USD/TRY fetch so TL equivalents never hit the network (skip with `@pytest.mark.real_fx`)."""
    if request.node.get_closest_marker("real_fx") is not None:
        return

    async def fake_fetch(settings: Any, *, client: Any = None) -> fx.FxRate:
        return fx.FxRate(rate=FX_RATE, source="test", fetched_at=datetime.now(UTC))

    monkeypatch.setattr(fx, "fetch_usd_try", fake_fetch)


# --- chain --------------------------------------------------------------------------------------------------------


@pytest.fixture
def chain() -> Iterator[FakeChainGateway]:
    """`FakeChainGateway` whose clock starts at the wall clock (deadlines / expiry computed from `time.time()`
    by the services line up with the fake contract), installed as the app's gateway."""
    gw = FakeChainGateway(now=int(time.time()))
    set_chain(gw)
    try:
        yield gw
    finally:
        set_chain(None)


# --- users / auth -----------------------------------------------------------------------------------------------


@pytest.fixture
def make_user(db: AsyncSession, settings: Settings) -> MakeUser:
    """`await make_user("trader", username="ali", commission_bps=1500)` -> (User, bearer token).

    Pass `account=LocalAccount` to control the wallet; otherwise a fresh key is generated. The account is attached
    to the row as `user._account` so tests can sign SIWE messages / send fake transactions. Rows are committed.
    """

    async def _make(role: UserRole | str = UserRole.customer, **kw: Any) -> tuple[User, str]:
        role = UserRole(role)
        account: LocalAccount = kw.pop("account", None) or Account.create()
        username = kw.pop("username", None) or f"{role.value[:4]}_{uuid.uuid4().hex[:8]}"
        data: dict[str, Any] = {
            "wallet_address": account.address.lower(),
            "role": role,
            "username": username,
            "display_name": kw.pop("display_name", None) or username.replace("_", " ").title(),
            "markets": ["crypto", "stable_fx"],
        }
        if role is UserRole.customer:
            data.update(budget_amount=Decimal("1000"), risk_profile=RiskProfile.balanced)
        else:
            data.update(
                commission_bps=2000,
                min_capital=Decimal("100"),
                risk_level=RiskLevel.medium,
                strategy_summary="tWETH/tUSDC momentum on the vault router",
            )
        data.update(kw)
        user = User(**data)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        user._account = account  # type: ignore[attr-defined]
        token = create_access_token(
            settings,
            address=user.wallet_address,
            user_id=user.id,
            role=user.role.value,
            auth_time=int(time.time()),
        )
        return user, token

    return _make


def wallet_token(settings: Settings, account: LocalAccount | None = None) -> tuple[LocalAccount, str]:
    """A bare wallet token (authenticated, not registered) for a fresh or given key."""
    account = account or Account.create()
    return account, create_access_token(settings, address=account.address, user_id=None, role=None)


@pytest.fixture
def auth_headers() -> Callable[[str], dict[str, str]]:
    def _headers(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    return _headers


@pytest.fixture
def admin_headers(settings: Settings) -> dict[str, str]:
    return {"X-Admin-Key": settings.admin_key}


def sign_siwe(account: LocalAccount, message: str) -> str:
    """EIP-191 `personal_sign` of the SIWE text, `0x` + 130 hex."""
    signed = account.sign_message(encode_defunct(text=message))
    return "0x" + bytes(signed.signature).hex()


async def siwe_login(client: AsyncClient, account: LocalAccount) -> dict[str, Any]:
    """`POST /auth/nonce` -> sign -> `POST /auth/verify`; returns the `LoginOut` body (asserts HTTP 200)."""
    r = await client.post(f"{API}/auth/nonce", json={"address": account.address})
    assert r.status_code == 200, r.text
    nonce = r.json()
    r = await client.post(
        f"{API}/auth/verify", json={"message": nonce["message"], "signature": sign_siwe(account, nonce["message"])}
    )
    assert r.status_code == 200, r.text
    return r.json()


# --- assets -----------------------------------------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seed_assets(db: AsyncSession, settings: Settings) -> dict[str, Asset]:
    """Seed the fake deployment's allow-list (tUSDC 6dp base, tWETH 18dp, tWBTC 8dp); rows keyed by symbol."""
    from scripts.seed_assets import parse_deployment_json, seed_assets

    deployment = parse_deployment_json(json.loads(DEPLOYMENT_FILE.read_text(encoding="utf-8")), source="test")
    await seed_assets(db, deployment)
    await db.commit()
    rows = (await db.execute(select(Asset).where(Asset.chain_id == deployment.chain_id))).scalars().all()
    return {a.symbol: a for a in rows}
