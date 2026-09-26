"""Wallet slice (Figma 8c): balances from the (fake) Horizon + Soroban gateways, TL equivalents, missing
trustlines for the anchor's assets, merged movements, deposit info and the unsigned payment / trustline
builders round-tripped through POST /tx/submit."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qsl, urlparse

import pytest
import respx
from sqlalchemy import select
from stellar_sdk import Keypair, TransactionEnvelope
from stellar_sdk.memo import TextMemo
from stellar_sdk.operation import ChangeTrust, CreateAccount, Payment

from app.models import (
    Agreement,
    AgreementStatus,
    AnchorTransaction,
    AnchorTxKind,
    PendingTransaction,
    UserRole,
)
from app.services import anchor as anchor_service
from app.services import fx
from app.services.agreements import compute_listing_ref, reset_price_cache
from app.services.anchor import AnchorClient, set_anchor_client
from app.services.stellar.fake import TESTNET_USDC
from app.services.stellar.types import AssetRef
from tests.test_anchor import DOMAIN, USDC_ISSUER, FakeAnchor
from tests.test_auth import mount_routers

API = "/api/v1"
FX_RATE = Decimal("41.5")


@pytest.fixture(scope="module", autouse=True)
def _mounted() -> None:
    mount_routers(("anchor", "wallet", "tx"))


@pytest.fixture(autouse=True)
def _fx_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    rate = fx.FxRate(rate=FX_RATE, source="test", fetched_at=datetime.now(UTC))

    async def fake_get_usd_try(settings: Any, db: Any = None, **kw: Any) -> fx.FxRate:
        return rate

    monkeypatch.setattr(fx, "get_usd_try", fake_get_usd_try)
    reset_price_cache()


@pytest.fixture
def anchor(settings):
    anchor_service.reset_cache()
    anchor_service.reset_rate_limits()
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as mock:
        fake = FakeAnchor(mock)
        set_anchor_client(AnchorClient(settings, domain=DOMAIN))
        try:
            yield fake
        finally:
            set_anchor_client(None)
            anchor_service.reset_cache()


async def _user(make_user, horizon, *, fund: str | None = "100", trustline: bool = False):
    kp = Keypair.random()
    user, tok = await make_user("customer", keypair=kp)
    if fund is not None:
        horizon.fund(kp.public_key, fund)
    if trustline:
        horizon.add_trustline(kp.public_key, "USDC", USDC_ISSUER)
    return user, tok, kp


# --- GET /wallet -------------------------------------------------------------------------------------------


async def test_wallet_unfunded_account(client, make_user, auth_headers, seed_assets, horizon, soroban, anchor: FakeAnchor):
    user, tok, kp = await _user(make_user, horizon, fund=None)
    r = await client.get(f"{API}/wallet", headers=auth_headers(tok))
    assert r.status_code == 200, r.text
    w = r.json()
    assert w["address"] == kp.public_key and w["network"] == "testnet" and w["funded"] is False
    assert w["balances"] == [] and w["movements"] == [] and w["total_try"] is None
    assert w["friendbot_url"] and dict(parse_qsl(urlparse(w["friendbot_url"]).query)) == {"addr": kp.public_key}
    assert w["anchor_enabled"] is True and w["anchor_home_domain"]
    assert w["missing_trustlines"] == [
        {
            "asset_code": "USDC", "issuer": USDC_ISSUER, "asset_id": str(seed_assets["USDC"].id),
            "reason": "account_not_funded", "build_endpoint": "/wallet/tx/trustline",
        }
    ]


async def test_wallet_balances_tl_and_movements(client, db, make_user, auth_headers, seed_assets, horizon, soroban, anchor: FakeAnchor):
    user, tok, kp = await _user(make_user, horizon, fund="100", trustline=True)
    addr = kp.public_key
    horizon.deposit_from_anchor(addr, "USDC", USDC_ISSUER, "25", memo="anchor deposit")
    soroban.fund_wallet(addr, TESTNET_USDC, 5_0000000)  # Soroswap test USDC: only visible through RPC
    # an anchor deposit row (completed) + an agreement the user funded and that settled with profit
    trader, _ = await make_user("trader")
    now = datetime.now(UTC)
    anchor_row = AnchorTransaction(
        user_id=user.id, anchor_domain=DOMAIN, anchor_tx_id="dep-9", kind=AnchorTxKind.deposit, asset_code="USDC",
        asset_issuer=USDC_ISSUER, amount_in=Decimal("25"), amount_out=Decimal("24.9"), status="completed",
        stellar_tx_hash=horizon.payments[-1].tx_hash, completed_at=now - timedelta(minutes=5), raw={},
    )
    ag_id = uuid.uuid4()
    ag = Agreement(
        id=ag_id, customer_id=user.id, trader_id=trader.id, base_asset_id=seed_assets["XLM"].id, principal=Decimal("40"),
        duration_secs=7 * 86_400, commission_bps=2000, max_drawdown_bps=1000, listing_ref=compute_listing_ref(ag_id),
        status=AgreementStatus.settled, proposer_role=UserRole.customer, created_tx="c" * 64, settle_tx="s" * 64,
        start_time=now - timedelta(days=3), settled_at=now - timedelta(minutes=1), final_value=Decimal("50"),
        profit=Decimal("10"), trader_fee=Decimal("2"), platform_fee=Decimal("0"), customer_payout=Decimal("48"),
    )
    db.add_all([anchor_row, ag])
    await db.commit()

    r = await client.get(f"{API}/wallet", headers=auth_headers(tok))
    assert r.status_code == 200, r.text
    w = r.json()
    assert w["funded"] is True and w["friendbot_url"] is None and w["missing_trustlines"] == []
    bal = {(b["code"], b["source"]): b for b in w["balances"]}
    assert set(bal) == {("XLM", "horizon"), ("USDC", "horizon"), ("USDC", "soroban")}
    assert w["balances"][0]["code"] == "XLM"  # native first
    xlm, usdc, usdc_sw = bal[("XLM", "horizon")], bal[("USDC", "horizon")], bal[("USDC", "soroban")]
    assert xlm["balance"] == "100.0000000" and xlm["asset"]["contract_id"] == seed_assets["XLM"].contract_id
    assert xlm["is_base_allowed"] is True and xlm["is_anchor_asset"] is True
    assert usdc["balance"] == "25.0000000" and usdc["issuer"] == USDC_ISSUER and usdc["is_anchor_asset"] is True
    assert usdc["asset"]["id"] == str(seed_assets["USDC"].id) and usdc["contract_id"] == seed_assets["USDC"].contract_id
    assert usdc["value_try"] == "1037.50"  # 25 USDC × 1.00 USD × 41.5
    assert usdc_sw["balance"] == "5.0000000" and usdc_sw["contract_id"] == TESTNET_USDC and usdc_sw["issuer"] is None
    assert usdc_sw["is_anchor_asset"] is False and usdc_sw["value_try"] == "207.50"
    assert w["fx"] == {"rate": "41.5000000", "source": "test", "stale": False}
    assert Decimal(w["total_try"]) >= Decimal("1245.00")

    kinds = [(m["kind"], m["direction"]) for m in w["movements"]]
    assert ("agreement_payout", "in") in kinds and ("agreement_escrow", "out") in kinds
    assert ("anchor_deposit", "in") in kinds and ("create_account", "in") in kinds
    # the anchor's on-chain payment is folded into the anchor row (no duplicate movement for the same tx hash)
    assert ("payment_in", "in") not in kinds
    ats = [m["at"] for m in w["movements"]]
    assert ats == sorted(ats, reverse=True)
    payout = next(m for m in w["movements"] if m["kind"] == "agreement_payout")
    assert payout["amount"] == "48.0000000" and payout["tx_hash"] == "s" * 64 and payout["ref_id"] == str(ag_id)
    escrow = next(m for m in w["movements"] if m["kind"] == "agreement_escrow")
    assert escrow["amount"] == "40.0000000" and escrow["tx_hash"] == "c" * 64 and escrow["asset_code"] == "XLM"
    dep = next(m for m in w["movements"] if m["kind"] == "anchor_deposit")
    assert dep["amount"] == "24.9000000" and dep["status"] == "completed" and dep["counterparty"] == DOMAIN
    assert dep["value_try"] == "1033.35"
    r = await client.get(f"{API}/wallet", headers=auth_headers(tok), params={"movements": 2})
    assert len(r.json()["movements"]) == 2


async def test_deposit_info(client, make_user, auth_headers, seed_assets, horizon, anchor: FakeAnchor):
    user, tok, kp = await _user(make_user, horizon, fund="10")
    r = await client.get(f"{API}/wallet/deposit-info", headers=auth_headers(tok))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["address"] == kp.public_key and d["funded"] is True and d["friendbot_url"] is None
    assert d["pay_uri"].startswith("web+stellar:pay?destination=" + kp.public_key)
    assets = {a["code"]: a for a in d["anchor_assets"]}
    assert assets["native"]["has_trustline"] is True and assets["native"]["deposit_max"] == "10"
    assert assets["USDC"]["has_trustline"] is False and assets["USDC"]["needs_trustline"] is True
    assert any("trustline" in line for line in d["instructions"]) and d["anchor_error"] is None
    # unfunded: friendbot link + null trustline state
    user2, tok2, kp2 = await _user(make_user, horizon, fund=None)
    d = (await client.get(f"{API}/wallet/deposit-info", headers=auth_headers(tok2))).json()
    assert d["funded"] is False and "addr=" + kp2.public_key in d["friendbot_url"]
    assert {a["code"]: a["has_trustline"] for a in d["anchor_assets"]} == {"native": None, "USDC": None}
    assert d["instructions"][0].startswith("Hesap henüz ağda yok")


# --- unsigned builders -> POST /tx/submit ------------------------------------------------------------------------


async def test_payment_builder_roundtrip(client, db, make_user, auth_headers, seed_assets, horizon, soroban, anchor: FakeAnchor):
    user, tok, kp = await _user(make_user, horizon, fund="100")
    headers = auth_headers(tok)
    soroban.link_horizon(horizon)
    dest = Keypair.random().public_key
    horizon.fund(dest, "5")

    r = await client.post(f"{API}/wallet/tx/payment", headers=headers, json={"to": kp.public_key, "asset_code": "XLM", "amount": "1"})
    assert r.status_code == 422 and r.json()["code"] == "invalid_destination"
    r = await client.post(f"{API}/wallet/tx/payment", headers=headers, json={"to": dest, "asset_code": "USDC", "amount": "1"})
    assert r.status_code == 422 and r.json()["code"] == "asset_issuer_required"
    r = await client.post(f"{API}/wallet/tx/payment", headers=headers, json={"to": dest, "amount": "1"})
    assert r.status_code == 422 and r.json()["code"] == "validation_error"
    r = await client.post(
        f"{API}/wallet/tx/payment", headers=headers, json={"to": dest, "asset_id": str(seed_assets["USDC_SOROSWAP"].id), "amount": "1"}
    )
    assert r.status_code == 422 and r.json()["code"] == "asset_not_classic"

    r = await client.post(
        f"{API}/wallet/tx/payment", headers=headers,
        json={"to": dest, "asset_id": str(seed_assets["XLM"].id), "amount": "12.5", "memo": "kira", "memo_type": "text"},
    )
    assert r.status_code == 200, r.text
    built = r.json()
    assert built["kind"] == "payment" and built["source"] == kp.public_key and built["summary"]["destination"] == dest
    env = TransactionEnvelope.from_xdr(built["unsigned_xdr"], built["network_passphrase"])
    assert env.transaction.source.account_id == kp.public_key and not env.signatures
    op = env.transaction.operations[0]
    assert isinstance(op, Payment) and op.asset.is_native() and Decimal(op.amount) == Decimal("12.5") and op.destination.account_id == dest
    assert isinstance(env.transaction.memo, TextMemo) and env.transaction.memo.memo_text == b"kira"
    pending = await db.get(PendingTransaction, uuid.UUID(built["pending_tx_id"]))
    assert pending.kind.value == "payment" and pending.payload["context"]["purpose"] == "wallet_payment"

    env.sign(kp)
    r = await client.post(f"{API}/tx/submit", headers=headers, json={"pending_tx_id": built["pending_tx_id"], "signed_xdr": env.to_xdr()})
    assert r.status_code == 200 and r.json()["status"] == "SUCCESS", r.text
    assert (await horizon.get_account(kp.public_key)).balance_of(AssetRef.native()).balance == Decimal("87.5")
    assert (await horizon.get_account(dest)).balance_of(AssetRef.native()).balance == Decimal("17.5")
    w = (await client.get(f"{API}/wallet", headers=headers)).json()
    out = next(m for m in w["movements"] if m["kind"] == "payment_out")
    assert out["amount"] == "12.5000000" and out["counterparty"] == dest and out["memo"] == "kira" and out["tx_hash"] == env.hash_hex()

    # XLM to an account that does not exist yet becomes create_account
    fresh = Keypair.random().public_key
    r = await client.post(f"{API}/wallet/tx/payment", headers=headers, json={"to": fresh, "asset_code": "native", "amount": "2"})
    assert r.status_code == 200 and r.json()["summary"]["operation"] == "create_account"
    env = TransactionEnvelope.from_xdr(r.json()["unsigned_xdr"], r.json()["network_passphrase"])
    assert isinstance(env.transaction.operations[0], CreateAccount)


async def test_trustline_builder_roundtrip(client, db, make_user, auth_headers, seed_assets, horizon, soroban, anchor: FakeAnchor):
    user, tok, kp = await _user(make_user, horizon, fund="20")
    headers = auth_headers(tok)
    soroban.link_horizon(horizon)
    w = (await client.get(f"{API}/wallet", headers=headers)).json()
    assert [t["asset_code"] for t in w["missing_trustlines"]] == ["USDC"] and w["missing_trustlines"][0]["reason"] == "anchor_deposit"

    r = await client.post(f"{API}/wallet/tx/trustline", headers=headers, json={"asset_code": "XLM"})
    assert r.status_code == 422  # native needs no trustline (schema: code + issuer required)
    r = await client.post(f"{API}/wallet/tx/trustline", headers=headers, json={"asset_id": str(seed_assets["XLM"].id)})
    assert r.status_code == 422 and r.json()["code"] == "invalid_asset"
    r = await client.post(f"{API}/wallet/tx/trustline", headers=headers, json={"asset_code": "USDC", "issuer": USDC_ISSUER})
    assert r.status_code == 200, r.text
    built = r.json()
    assert built["kind"] == "trustline" and built["summary"]["asset"] == f"USDC:{USDC_ISSUER}"
    env = TransactionEnvelope.from_xdr(built["unsigned_xdr"], built["network_passphrase"])
    op = env.transaction.operations[0]
    assert isinstance(op, ChangeTrust) and op.asset.code == "USDC" and op.asset.issuer == USDC_ISSUER
    pending = (await db.execute(select(PendingTransaction).where(PendingTransaction.user_id == user.id))).scalar_one()
    assert pending.kind.value == "trustline" and pending.payload["context"]["asset_id"] == str(seed_assets["USDC"].id)

    env.sign(kp)
    r = await client.post(f"{API}/tx/submit", headers=headers, json={"pending_tx_id": built["pending_tx_id"], "signed_xdr": env.to_xdr()})
    assert r.status_code == 200 and r.json()["status"] == "SUCCESS", r.text
    assert await horizon.has_trustline(kp.public_key, "USDC", USDC_ISSUER)
    w = (await client.get(f"{API}/wallet", headers=headers)).json()
    assert w["missing_trustlines"] == []
    assert {(b["code"], b["balance"]) for b in w["balances"] if b["source"] == "horizon"} == {("XLM", "20.0000000"), ("USDC", "0.0000000")}
    # the anchor may now deliver the deposit
    d = (await client.get(f"{API}/wallet/deposit-info", headers=headers)).json()
    assert {a["code"]: a["has_trustline"] for a in d["anchor_assets"]} == {"native": True, "USDC": True}
