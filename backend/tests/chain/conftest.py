"""Fixtures for the chain unit tests (03-backend-tasarim §8.3): no database, no network.

The root ``tests/conftest.py`` opens a session-scoped database engine and truncates tables after every test;
those fixtures are overridden here with no-ops so ``pytest tests/chain`` runs anywhere. Until the root conftest is
rewritten for Monad (Dalga 3h) it still imports removed Stellar modules, so run this directory with
``pytest tests/chain -q --confcutdir=tests/chain``.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from app.services.chain import set_chain
from app.services.chain.fake import FAKE_TUSDC, FakeChainGateway
from app.services.chain.types import Terms

# --- root-conftest overrides (no DB) -----------------------------------------------------------------


@pytest.fixture(scope="session")
def engine() -> Iterator[None]:
    yield None


@pytest.fixture(autouse=True)
def _clean_tables() -> Iterator[None]:
    yield


@pytest.fixture
def db() -> Iterator[None]:
    yield None


@pytest.fixture
def client() -> Iterator[None]:
    yield None


# --- chain fixtures ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Accounts:
    customer: str = "0x1111111111111111111111111111111111111111"
    trader: str = "0x2222222222222222222222222222222222222222"
    other: str = "0x3333333333333333333333333333333333333333"


@pytest.fixture
def accounts() -> Accounts:
    return Accounts()


@pytest.fixture
def chain() -> Iterator[FakeChainGateway]:
    gw = FakeChainGateway()
    set_chain(gw)
    try:
        yield gw
    finally:
        set_chain(None)


@pytest.fixture
def terms(accounts: Accounts) -> Terms:
    """1000 tUSDC, 30 days, 20% commission, 20% max drawdown."""
    return Terms(
        customer=accounts.customer,
        trader=accounts.trader,
        base_token=FAKE_TUSDC,
        principal=1_000 * 10**6,
        duration_seconds=30 * 86_400,
        commission_bps=2_000,
        max_drawdown_bps=2_000,
        listing_ref=b"\x01" * 32,
    )
