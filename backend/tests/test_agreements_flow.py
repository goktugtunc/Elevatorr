"""Agreement lifecycle through the API with the FakeSorobanGateway: draft -> open -> accept -> trade ->
settle (incl. /tx/submit), permission / state negatives, failed and expired pending transactions,
propose/fund/cancel path and the permissionless post-expiry settle."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from stellar_sdk import Keypair, TransactionEnvelope

from app.models import Agreement, AgreementStatus, Notification, PendingTransaction, Trade, User
from app.services import amounts as money
from app.services.agreements import build_terms
from app.services.indexer import run_indexer_once
from app.services.stellar.fake import TESTNET_USDC, TESTNET_XLM
from tests.helpers_agreements import (  # noqa: F401 - fixtures
    API,
    _fx_stub,
    _mounted,
    api_action,
    api_submit,
    make_agreement,
    sign_xdr,
    soroban_gateway,
)


@pytest.fixture
def soroban():
    yield from soroban_gateway()


async def _notif_types(db, user_id) -> list[str]:
    rows = (await db.execute(select(Notification.type).where(Notification.user_id == user_id).order_by(Notification.created_at))).scalars().all()
    return list(rows)


async def test_open_accept_trade_settle_happy_path(client, db, settings, make_user, auth_headers, seed_assets, soroban):
    ckp, tkp = Keypair.random(), Keypair.random()
    customer, ctok = await make_user("customer", keypair=ckp)
    trader, ttok = await make_user("trader", keypair=tkp)
    ch, th = auth_headers(ctok), auth_headers(ttok)
    xlm, usdc = seed_assets["XLM"], seed_assets["USDC_SOROSWAP"]
    ag = await make_agreement(db, customer, trader, xlm, principal="1000", max_drawdown_bps=1000)

    # --- list: role-aware actions + TL equivalents (XLM priced through the router: 0.29 USDC) ----------
    r = await client.get(f"{API}/agreements", headers=ch)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["status"] == "draft" and item["my_role"] == "customer" and item["available_actions"] == ["open"]
    assert item["principal"] == "1000.0000000" and item["drawdown_floor"] == "900.0000000"
    assert item["tl"]["base_usd_price"] == "0.2900000" and item["tl"]["principal_try"] == "12035.00"
    r = await client.get(f"{API}/agreements", headers=th, params={"role": "trader", "status": "draft"})
    assert r.json()["items"][0]["available_actions"] == ["propose"]
    assert (await client.get(f"{API}/agreements", headers=th, params={"status": "open"})).json()["total"] == 0
    assert (await client.get(f"{API}/agreements", headers=th, params={"status": "bogus"})).status_code == 422

    # --- customer opens (escrows principal) -------------------------------------------------------
    r = await client.post(f"{API}/agreements/{ag.id}/tx/open", headers=ch)
    assert r.status_code == 200, r.text
    built = r.json()
    assert built["kind"] == "open" and built["source"] == ckp.public_key and built["agreement_id"] == str(ag.id)
    assert built["summary"]["function"] == "open" and built["summary"]["terms"]["principal"] == 1000_0000000
    env = TransactionEnvelope.from_xdr(built["unsigned_xdr"], built["network_passphrase"])
    assert env.transaction.source.account_id == ckp.public_key and not env.signatures
    sub = await api_submit(client, ch, built, ckp)
    assert sub["status"] == "SUCCESS" and sub["pending_status"] == "success"
    assert sub["agreement_status"] == "funded" and sub["onchain_id"] == 1 and sub["tx_hash"] == built["tx_hash"]
    assert [e["event"] for e in sub["events"]] == ["opened"]

    r = await client.get(f"{API}/agreements/{ag.id}", headers=ch)
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["status"] == "funded" and detail["onchain_id"] == 1 and detail["created_tx"] == built["tx_hash"]
    assert [(b["asset"]["code"], b["balance"]) for b in detail["balances"]] == [("XLM", "1000.0000000")]
    assert detail["available_actions"] == ["cancel"] and detail["pending_tx"] is None
    # trader was told the capital is locked
    assert "agreement_funded" in await _notif_types(db, trader.id)

    # --- trader accepts -> active ---------------------------------------------------------------------
    sub = await api_action(client, th, ag.id, "accept", tkp)
    assert sub["status"] == "SUCCESS" and sub["agreement_status"] == "active"
    detail = (await client.get(f"{API}/agreements/{ag.id}", headers=th)).json()
    assert detail["status"] == "active" and detail["activate_tx"] == sub["tx_hash"]
    assert detail["start_time"] and detail["end_time"] and detail["seconds_remaining"] > 6 * 86_400
    assert detail["available_actions"] == ["trade", "settle"] and detail["my_role"] == "trader"
    assert detail["current_value"] == "1000.0000000" and detail["pnl_bps"] == 0
    assert "agreement_activated" in await _notif_types(db, customer.id)
    assert "agreement_activated" not in await _notif_types(db, trader.id)  # actor is not notified
    await db.refresh(trader)
    assert trader.active_agreements == 1 and trader.managed_capital == Decimal("1000")

    # --- quote + trade XLM -> USDC ---------------------------------------------------------------------
    r = await client.get(
        f"{API}/agreements/{ag.id}/quote",
        headers=th,
        params={"token_in": str(xlm.id), "token_out": usdc.contract_id, "amount_in": "100"},
    )
    assert r.status_code == 200, r.text
    q = r.json()
    assert q["amount_out"] == "29.0000000" and q["min_out"] == "28.7100000" and q["slippage_bps"] == 100
    assert q["allowed"] is True and q["reason"] is None and q["headroom"] == "100.0000000"
    assert q["value_after_estimate"] == "1000.0000000" and q["balance_in"] == "1000.0000000"

    r = await client.post(
        f"{API}/agreements/{ag.id}/tx/trade",
        headers=th,
        json={"token_in": "XLM", "token_out": str(usdc.id), "amount_in": "100", "note": "ilk işlem", "notify_investors": True},
    )
    assert r.status_code == 200, r.text
    built = r.json()
    assert built["kind"] == "trade" and built["summary"]["min_out"] == 28_7100000 and built["summary"]["amount_in"] == 100_0000000
    sub = await api_submit(client, th, built, tkp)
    assert sub["status"] == "SUCCESS" and sub["trade_id"] and [e["event"] for e in sub["events"]] == ["traded"]

    r = await client.get(f"{API}/agreements/{ag.id}/trades", headers=ch)
    trades = r.json()
    assert trades["total"] == 1
    t = trades["items"][0]
    assert t["id"] == sub["trade_id"] and t["note"] == "ilk işlem" and t["symbol_label"] == "USDC/XLM · Alış"
    assert t["amount_in"] == "100.0000000" and t["amount_out"] == "29.0000000" and t["tx_hash"] == sub["tx_hash"]
    assert t["price"] == "0.2900000" and t["value_after"] == "1000.0000000" and t["onchain_seq"] == 0
    detail = (await client.get(f"{API}/agreements/{ag.id}", headers=ch)).json()
    assert {b["asset"]["code"]: b["balance"] for b in detail["balances"]} == {"XLM": "900.0000000", "USDC": "29.0000000"}
    assert detail["current_value"] == "1000.0000000"
    assert "trade_executed" in await _notif_types(db, customer.id)
    hist = (await client.get(f"{API}/agreements/{ag.id}/value-history", headers=ch, params={"range": "24h"})).json()
    assert len(hist["points"]) >= 2 and hist["principal"] == "1000.0000000"

    # --- note editing: trader only ----------------------------------------------------------------
    r = await client.patch(f"{API}/trades/{t['id']}", headers=th, json={"note": "güncellendi"})
    assert r.status_code == 200 and r.json()["note"] == "güncellendi"
    r = await client.patch(f"{API}/trades/{t['id']}", headers=ch, json={"note": "x"})
    assert r.status_code == 403 and r.json()["code"] == "wrong_party"

    # --- activity: party sees the trade, filters work -------------------------------------------
    act = (await client.get(f"{API}/activity", headers=ch)).json()
    assert act["total"] == 1 and act["items"][0]["relation"] == "party" and act["items"][0]["trade"]["note"] == "güncellendi"
    assert (await client.get(f"{API}/activity", headers=ch, params={"state": "closed"})).json()["total"] == 0
    assert (await client.get(f"{API}/activity", headers=ch, params={"trader_id": str(customer.id)})).json()["total"] == 0

    # --- price moves in favour, customer settles any time ------------------------------------------
    soroban.set_price(TESTNET_XLM, TESTNET_USDC, "0.20")  # 1 USDC now 5 XLM
    r = await client.post(f"{API}/agreements/{ag.id}/tx/settle", headers=ch, json={"slippage_bps": 50})
    assert r.status_code == 200, r.text
    built = r.json()
    usdc_back = 29_0000000 * 5
    assert built["summary"]["min_outs"] == [money.min_out_for_slippage(usdc_back, 50)]
    sub = await api_submit(client, ch, built, ckp)
    assert sub["status"] == "SUCCESS" and sub["agreement_status"] == "settled"
    detail = (await client.get(f"{API}/agreements/{ag.id}", headers=ch)).json()
    final_raw = 900_0000000 + usdc_back
    exp = money.settle_math(final_raw, 1000_0000000, 2000, 0)
    assert detail["status"] == "settled" and detail["settle_tx"] == sub["tx_hash"] and detail["settled_by"] == ckp.public_key
    assert detail["final_value"] == money.format_amount(money.from_stroops(final_raw))
    assert detail["profit"] == money.format_amount(money.from_stroops(exp.profit))
    assert detail["trader_fee"] == money.format_amount(money.from_stroops(exp.trader_fee))
    assert detail["customer_payout"] == money.format_amount(money.from_stroops(exp.customer_payout))
    assert detail["pnl_bps"] == 450 and detail["balances"] == [] and detail["available_actions"] == []  # 1045 / 1000
    assert detail["tl"]["final_value_try"] is not None
    assert "agreement_settled" in await _notif_types(db, trader.id)
    await db.refresh(trader)
    assert trader.active_agreements == 0 and trader.win_rate_bps == 10_000 and trader.total_return_bps == 450

    # --- the indexer sees the same events later and applies nothing twice --------------------------
    trades_before = (await db.execute(select(func.count()).select_from(Trade))).scalar_one()
    notifs_before = (await db.execute(select(func.count()).select_from(Notification))).scalar_one()
    db.expire_all()  # this session still holds the pre-API `draft` row; the worker starts from a fresh session
    out = await run_indexer_once(db, soroban, settings)
    await db.commit()
    assert out.events_seen == 4 and out.events_applied == 0 and out.errors == [] and out.cursor
    assert (await db.execute(select(func.count()).select_from(Trade))).scalar_one() == trades_before
    assert (await db.execute(select(func.count()).select_from(Notification))).scalar_one() == notifs_before
    await db.refresh(ag)
    assert ag.status is AgreementStatus.settled and ag.balances == []


async def test_permission_and_state_negatives(client, db, make_user, auth_headers, seed_assets, soroban):
    ckp, tkp, okp = Keypair.random(), Keypair.random(), Keypair.random()
    customer, ctok = await make_user("customer", keypair=ckp)
    trader, ttok = await make_user("trader", keypair=tkp)
    _, otok = await make_user("customer", keypair=okp)
    ch, th, oh = auth_headers(ctok), auth_headers(ttok), auth_headers(otok)
    xlm, usdc = seed_assets["XLM"], seed_assets["USDC_SOROSWAP"]
    ag = await make_agreement(db, customer, trader, xlm)

    async def expect(headers, action, code, status, body=None):
        r = await client.post(f"{API}/agreements/{ag.id}/tx/{action}", headers=headers, json=body)
        assert r.status_code == status and r.json()["code"] == code, (action, r.text)

    await expect(th, "open", "wrong_party", 403)  # trader cannot open
    await expect(ch, "propose", "wrong_party", 403)  # customer cannot propose
    await expect(oh, "open", "wrong_party", 403)  # third party
    await expect(ch, "accept", "invalid_state", 409)  # draft cannot be accepted
    await expect(ch, "settle", "invalid_state", 409)
    r = await client.post(f"{API}/agreements/{ag.id}/tx/bogus", headers=ch)
    assert r.status_code == 422
    r = await client.get(f"{API}/agreements/{ag.id}", headers=oh)
    assert r.status_code == 403 and r.json()["code"] == "not_party"
    assert (await client.get(f"{API}/agreements", headers=oh)).json()["total"] == 0
    r = await client.post(f"{API}/agreements/{ag.id}/tx/trade", headers=ch, json={"token_in": "XLM", "token_out": str(usdc.id), "amount_in": "1"})
    assert r.status_code == 403 and r.json()["code"] == "wrong_party"
    r = await client.post(f"{API}/agreements/{ag.id}/tx/trade", headers=th, json={"token_in": "XLM", "token_out": str(usdc.id), "amount_in": "1"})
    assert r.status_code == 409 and r.json()["code"] == "invalid_state"  # not active yet

    # open + accept
    open_built = (await client.post(f"{API}/agreements/{ag.id}/tx/open", headers=ch)).json()
    # /tx/submit guards: owner, signature, hash
    signed = sign_xdr(open_built["unsigned_xdr"], open_built["network_passphrase"], ckp)
    r = await client.post(f"{API}/tx/submit", json={"pending_tx_id": open_built["pending_tx_id"], "signed_xdr": signed}, headers=th)
    assert r.status_code == 403 and r.json()["code"] == "not_owner"
    r = await client.post(f"{API}/tx/submit", json={"pending_tx_id": open_built["pending_tx_id"], "signed_xdr": open_built["unsigned_xdr"]}, headers=ch)
    assert r.status_code == 422 and r.json()["code"] == "unsigned_xdr"
    other = await soroban.build_open(ckp.public_key, build_terms(ag))
    r = await client.post(f"{API}/tx/submit", json={"pending_tx_id": open_built["pending_tx_id"], "signed_xdr": sign_xdr(other.xdr, other.network_passphrase, ckp)}, headers=ch)
    assert r.status_code == 422 and r.json()["code"] == "xdr_mismatch"
    r = await client.post(f"{API}/tx/submit", json={"pending_tx_id": open_built["pending_tx_id"], "signed_xdr": "not-xdr"}, headers=ch)
    assert r.status_code == 422 and r.json()["code"] == "invalid_xdr"
    r = await client.get(f"{API}/tx/{open_built['pending_tx_id']}", headers=th)
    assert r.status_code == 403
    sub = await api_submit(client, ch, open_built, ckp)
    assert sub["status"] == "SUCCESS"
    # replaying a finished pending tx is idempotent
    again = await api_submit(client, ch, open_built, ckp)
    assert again["status"] == "SUCCESS" and again["tx_hash"] == sub["tx_hash"]
    await expect(ch, "accept", "wrong_party", 403)  # customer cannot accept
    await expect(ch, "fund", "invalid_state", 409)  # funded, not proposed
    await api_action(client, th, ag.id, "accept", tkp)

    await expect(oh, "settle", "not_expired", 403)  # third party before end_time
    await expect(ch, "cancel", "invalid_state", 409)  # active cannot be cancelled
    r = await client.get(f"{API}/agreements/{ag.id}/quote", headers=oh, params={"token_in": "XLM", "token_out": str(usdc.id), "amount_in": "1"})
    assert r.status_code == 403 and r.json()["code"] == "not_party"
    r = await client.get(f"{API}/agreements/{ag.id}/quote", headers=ch, params={"token_in": "XLM", "token_out": str(usdc.id), "amount_in": "1"})
    assert r.status_code == 200  # the customer may look at quotes
    r = await client.get(f"{API}/agreements/{ag.id}/trades", headers=oh)
    assert r.status_code == 403
    r = await client.get(f"{API}/agreements", headers=ch)
    assert r.json()["items"][0]["status"] == "active"


async def test_expired_pending_transaction(client, db, make_user, auth_headers, seed_assets, soroban):
    ckp, tkp = Keypair.random(), Keypair.random()
    customer, ctok = await make_user("customer", keypair=ckp)
    trader, _ = await make_user("trader", keypair=tkp)
    ch = auth_headers(ctok)
    ag = await make_agreement(db, customer, trader, seed_assets["XLM"])
    built = (await client.post(f"{API}/agreements/{ag.id}/tx/open", headers=ch)).json()
    pending = await db.get(PendingTransaction, built["pending_tx_id"])
    assert pending is not None and pending.status.value == "built" and pending.tx_hash == built["tx_hash"]
    pending.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await db.commit()

    signed = sign_xdr(built["unsigned_xdr"], built["network_passphrase"], ckp)
    r = await client.post(f"{API}/tx/submit", json={"pending_tx_id": built["pending_tx_id"], "signed_xdr": signed}, headers=ch)
    assert r.status_code == 409 and r.json()["code"] == "pending_tx_expired"
    r = await client.get(f"{API}/tx/{built['pending_tx_id']}", headers=ch)
    assert r.status_code == 200 and r.json()["status"] == "expired" and r.json()["is_expired"] is True
    # the agreement is untouched; a fresh build supersedes nothing and works
    assert (await client.get(f"{API}/agreements/{ag.id}", headers=ch)).json()["status"] == "draft"
    built2 = (await client.post(f"{API}/agreements/{ag.id}/tx/open", headers=ch)).json()
    assert built2["pending_tx_id"] != built["pending_tx_id"]
    # building again supersedes the previous un-submitted row of the same kind
    built3 = (await client.post(f"{API}/agreements/{ag.id}/tx/open", headers=ch)).json()
    assert (await client.get(f"{API}/tx/{built2['pending_tx_id']}", headers=ch)).json()["status"] == "expired"
    detail = (await client.get(f"{API}/agreements/{ag.id}", headers=ch)).json()
    assert detail["pending_tx"]["id"] == built3["pending_tx_id"] and detail["pending_tx"]["kind"] == "open"
    sub = await api_submit(client, ch, built3, ckp)
    assert sub["status"] == "SUCCESS" and sub["agreement_status"] == "funded"


async def test_propose_fund_and_cancel_paths(client, db, make_user, auth_headers, seed_assets, soroban):
    ckp, tkp = Keypair.random(), Keypair.random()
    customer, ctok = await make_user("customer", keypair=ckp)
    trader, ttok = await make_user("trader", keypair=tkp)
    ch, th = auth_headers(ctok), auth_headers(ttok)
    xlm = seed_assets["XLM"]
    ag = await make_agreement(db, customer, trader, xlm, proposer_role="trader")

    sub = await api_action(client, th, ag.id, "propose", tkp)
    assert sub["status"] == "SUCCESS" and sub["agreement_status"] == "proposed" and sub["onchain_id"] == 1
    assert (await client.get(f"{API}/agreements/{ag.id}", headers=ch)).json()["available_actions"] == ["fund"]
    assert (await client.get(f"{API}/agreements/{ag.id}", headers=th)).json()["available_actions"] == ["cancel"]
    assert "agreement_proposed" in await _notif_types(db, customer.id)
    r = await client.post(f"{API}/agreements/{ag.id}/tx/cancel", headers=ch)
    assert r.status_code == 403 and r.json()["code"] == "wrong_party"  # only the proposer may cancel a proposal
    sub = await api_action(client, th, ag.id, "cancel", tkp)
    assert sub["agreement_status"] == "cancelled" and [e["event"] for e in sub["events"]] == ["cancelled"]
    assert "agreement_cancelled" in await _notif_types(db, customer.id)

    ag2 = await make_agreement(db, customer, trader, xlm, proposer_role="trader")
    await api_action(client, th, ag2.id, "propose", tkp)
    sub = await api_action(client, ch, ag2.id, "fund", ckp)
    assert sub["agreement_status"] == "active" and [e["event"] for e in sub["events"]] == ["activated"]
    detail = (await client.get(f"{API}/agreements/{ag2.id}", headers=ch)).json()
    assert detail["onchain_id"] == 2 and {b["asset"]["code"]: b["balance"] for b in detail["balances"]} == {"XLM": "1000.0000000"}

    # funded agreement: either party may cancel (refund to the customer)
    ag3 = await make_agreement(db, customer, trader, xlm)
    await api_action(client, ch, ag3.id, "open", ckp)
    sub = await api_action(client, th, ag3.id, "cancel", tkp)
    assert sub["agreement_status"] == "cancelled" and sub["events"][0]["refunded"] == 1000_0000000
    assert (await client.get(f"{API}/agreements/{ag3.id}", headers=ch)).json()["balances"] == []


async def test_failed_transaction_and_permissionless_settle_after_expiry(client, db, make_user, auth_headers, seed_assets, soroban):
    ckp, tkp, okp = Keypair.random(), Keypair.random(), Keypair.random()
    customer, ctok = await make_user("customer", keypair=ckp)
    trader, ttok = await make_user("trader", keypair=tkp)
    _, otok = await make_user("trader", keypair=okp)
    ch, th, oh = auth_headers(ctok), auth_headers(ttok), auth_headers(otok)
    xlm = seed_assets["XLM"]

    # accept built while funded, cancel lands first -> the accept fails on-chain (WrongStatus)
    ag = await make_agreement(db, customer, trader, xlm)
    await api_action(client, ch, ag.id, "open", ckp)
    accept_built = (await client.post(f"{API}/agreements/{ag.id}/tx/accept", headers=th)).json()
    await api_action(client, ch, ag.id, "cancel", ckp)
    sub = await api_submit(client, th, accept_built, tkp)
    assert sub["status"] == "FAILED" and sub["pending_status"] == "failed"
    assert sub["contract_error"] == "WrongStatus" and sub["contract_error_code"] == 7
    assert sub["agreement_status"] == "cancelled"
    assert "tx_failed" in await _notif_types(db, trader.id)
    r = await client.get(f"{API}/tx/{accept_built['pending_tx_id']}", headers=th)
    assert r.json()["status"] == "failed" and r.json()["result"]["contract_error"] == "WrongStatus"

    # anyone may settle after end_time (no min_outs; floors computed in-contract)
    ag2 = await make_agreement(db, customer, trader, xlm)
    await api_action(client, ch, ag2.id, "open", ckp)
    await api_action(client, th, ag2.id, "accept", tkp)
    r = await client.post(f"{API}/agreements/{ag2.id}/tx/settle", headers=oh)
    assert r.status_code == 403 and r.json()["code"] == "not_expired"
    row = await db.get(Agreement, ag2.id)
    row.end_time = datetime.now(UTC) - timedelta(days=7, seconds=5)  # past end_time + SETTLE_GRACE
    await db.commit()
    soroban.advance(8 * 86_400)
    r = await client.post(f"{API}/agreements/{ag2.id}/tx/settle", headers=oh)
    assert r.status_code == 200, r.text
    built = r.json()
    assert built["summary"]["min_outs"] == [] and built["summary"]["context"]["settle_mode"] == "permissionless"
    sub = await api_submit(client, oh, built, okp)
    assert sub["status"] == "SUCCESS" and sub["agreement_status"] == "settled"
    detail = (await client.get(f"{API}/agreements/{ag2.id}", headers=ch)).json()
    assert detail["settled_by"] == okp.public_key and detail["customer_payout"] == "1000.0000000" and detail["profit"] == "0.0000000"
    assert detail["is_expired"] is True
    users = (await db.execute(select(User).where(User.id == trader.id))).scalar_one()
    assert users.active_agreements == 0
