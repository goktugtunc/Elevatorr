"""Trading slice: router quote math, min_out from slippage, drawdown headroom / rejection reasons, the
trade tx payload and the symbol label."""
from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

import pytest
from stellar_sdk import Keypair

from app.models import PendingTransaction
from app.services import amounts as money
from app.services.indexer import run_indexer_once
from app.services.stellar.fake import TESTNET_EURC, TESTNET_XLM
from app.services.trading import symbol_label
from tests.helpers_agreements import (  # noqa: F401 - fixtures
    API,
    _fx_stub,
    _mounted,
    chain_open_and_accept,
    make_agreement,
    soroban_gateway,
)


@pytest.fixture
def soroban():
    yield from soroban_gateway()


async def _active_agreement(db, settings, soroban, make_user, xlm, **kw):
    ckp, tkp = Keypair.random(), Keypair.random()
    customer, ctok = await make_user("customer", keypair=ckp)
    trader, ttok = await make_user("trader", keypair=tkp)
    ag = await make_agreement(db, customer, trader, xlm, **kw)
    await chain_open_and_accept(soroban, ag, ckp, tkp)
    out = await run_indexer_once(db, soroban, settings)
    await db.commit()
    assert out.events_applied == 2
    await db.refresh(ag)
    return ag, (customer, ctok, ckp), (trader, ttok, tkp)


async def test_quote_math_min_out_and_headroom(client, db, settings, make_user, auth_headers, seed_assets, soroban):
    xlm, usdc, eurc = seed_assets["XLM"], seed_assets["USDC_SOROSWAP"], seed_assets["EURC_SOROSWAP"]
    ag, _, (trader, ttok, _) = await _active_agreement(db, settings, soroban, make_user, xlm, principal="1000", max_drawdown_bps=1000)
    th = auth_headers(ttok)
    url = f"{API}/agreements/{ag.id}/quote"

    r = await client.get(url, headers=th, params={"token_in": "XLM", "token_out": "USDC", "amount_in": "1"})
    assert r.status_code == 422 and r.json()["code"] == "ambiguous_token"  # two USDC rows on testnet
    r = await client.get(url, headers=th, params={"token_in": "XLM", "token_out": usdc.contract_id, "amount_in": "123.4567891"})
    assert r.status_code == 200, r.text
    q = r.json()
    amount_in_raw = 123_4567891
    out_raw = int(amount_in_raw * Decimal("0.29"))  # fake router: floor(amount × price)
    assert q["amount_in"] == "123.4567891" and q["amount_out"] == money.format_amount(money.from_stroops(out_raw))
    assert q["min_out"] == money.format_amount(money.from_stroops(money.min_out_for_slippage(out_raw, settings.default_trade_slippage_bps)))
    assert q["slippage_bps"] == settings.default_trade_slippage_bps and q["price"] == "0.2900000"
    assert q["token_in"]["contract_id"] == TESTNET_XLM and q["token_out"]["id"] == str(usdc.id)
    assert q["value_before"] == "1000.0000000" and q["principal"] == "1000.0000000" and q["drawdown_floor"] == "900.0000000"
    # value_after = value_before − in-leg + out-leg valued back in base (floor twice, like the router)
    back_raw = int(Fraction(out_raw) / Fraction(Decimal("0.29")))
    value_after_raw = 1000_0000000 - amount_in_raw + back_raw
    assert q["value_after_estimate"] == money.format_amount(money.from_stroops(value_after_raw))
    assert q["headroom"] == money.format_amount(money.from_stroops(value_after_raw - 900_0000000))
    assert q["headroom_bps"] == (value_after_raw - 900_0000000) * 10_000 // 1000_0000000
    assert q["allowed"] is True and q["reason"] is None and q["source"] == "fake" and q["api_amount_out"] is None

    r = await client.get(url, headers=th, params={"token_in": "XLM", "token_out": str(usdc.id), "amount_in": "10", "slippage_bps": 50})
    assert r.json()["min_out"] == money.format_amount(money.from_stroops(money.min_out_for_slippage(2_9000000, 50)))
    soroban.api_quotes[(TESTNET_XLM, usdc.contract_id)] = 2_9500000  # optional Soroswap API enrichment
    r = await client.get(url, headers=th, params={"token_in": "XLM", "token_out": str(usdc.id), "amount_in": "10"})
    assert r.json()["api_amount_out"] == "2.9500000"

    # rejection reasons mirror the contract checks
    r = await client.get(url, headers=th, params={"token_in": "XLM", "token_out": str(usdc.id), "amount_in": "2000"})
    assert r.json()["allowed"] is False and r.json()["reason"] == "insufficient_balance"
    r = await client.get(url, headers=th, params={"token_in": "XLM", "token_out": "XLM", "amount_in": "1"})
    assert r.status_code == 422 and r.json()["code"] == "same_token"
    r = await client.get(url, headers=th, params={"token_in": "XLM", "token_out": seed_assets["USDC"].contract_id, "amount_in": "1"})
    assert r.status_code == 409 and r.json()["code"] == "no_route"  # Circle USDC: allow-listed, no AMM liquidity
    r = await client.get(url, headers=th, params={"token_in": "XLM", "token_out": "nope", "amount_in": "1"})
    assert r.status_code == 404
    r = await client.get(url, headers=th, params={"token_in": "XLM", "token_out": str(usdc.id), "amount_in": "0"})
    assert r.status_code == 422

    # drawdown headroom: EURC quotes poorly on the way back (asymmetric prices) -> below the 90% floor
    soroban.set_price(TESTNET_XLM, TESTNET_EURC, "0.25", both_ways=False)
    soroban.set_price(TESTNET_EURC, TESTNET_XLM, "2", both_ways=False)
    r = await client.get(url, headers=th, params={"token_in": "XLM", "token_out": str(eurc.id), "amount_in": "500"})
    q = r.json()
    assert q["amount_out"] == "125.0000000" and q["value_after_estimate"] == "750.0000000"
    assert q["headroom"] == "-150.0000000" and q["headroom_bps"] == -1500
    assert q["allowed"] is False and q["reason"] == "drawdown_breached"
    r = await client.post(f"{API}/agreements/{ag.id}/tx/trade", headers=th, json={"token_in": "XLM", "token_out": str(eurc.id), "amount_in": "500"})
    assert r.status_code == 409 and r.json()["code"] == "drawdown_breached" and r.json()["details"]["quote"]["headroom_bps"] == -1500
    # a smaller position stays above the floor
    r = await client.get(url, headers=th, params={"token_in": "XLM", "token_out": str(eurc.id), "amount_in": "100"})
    assert r.json()["allowed"] is True and r.json()["value_after_estimate"] == "950.0000000"


async def test_trade_tx_payload_and_min_out(client, db, settings, make_user, auth_headers, seed_assets, soroban):
    xlm, usdc = seed_assets["XLM"], seed_assets["USDC_SOROSWAP"]
    ag, _, (trader, ttok, tkp) = await _active_agreement(db, settings, soroban, make_user, xlm)
    th = auth_headers(ttok)
    body = {"token_in": str(xlm.id), "token_out": usdc.contract_id, "amount_in": "40", "slippage_bps": 200, "note": "momentum", "notify_investors": False, "deadline_seconds": 120}
    r = await client.post(f"{API}/agreements/{ag.id}/tx/trade", headers=th, json=body)
    assert r.status_code == 200, r.text
    built = r.json()
    quote_raw = 40_0000000 * 29 // 100
    assert built["summary"]["function"] == "trade" and built["summary"]["agreement_id"] == 1
    assert built["summary"]["amount_in"] == 40_0000000 and built["summary"]["min_out"] == money.min_out_for_slippage(quote_raw, 200)
    assert built["summary"]["token_in"] == TESTNET_XLM and built["summary"]["token_out"] == usdc.contract_id
    assert built["summary"]["simulated_result"] == quote_raw  # the fake dry-runs the swap like a simulation
    pending = await db.get(PendingTransaction, built["pending_tx_id"])
    assert pending.kind.value == "trade" and pending.agreement_id == ag.id and pending.user_id == trader.id
    assert pending.payload["context"]["note"] == "momentum" and pending.payload["context"]["notify_investors"] is False
    assert pending.payload["context"]["symbol_label"] == "USDC/XLM · Alış" and pending.payload["context"]["slippage_bps"] == 200
    assert pending.payload["context"]["min_out"] == money.format_amount(money.from_stroops(money.min_out_for_slippage(quote_raw, 200)))
    assert Decimal(pending.payload["context"]["amount_out_quote"]) == money.from_stroops(quote_raw)
    # sign + submit -> the trade row carries the note and the flag from the payload
    from tests.helpers_agreements import api_submit

    sub = await api_submit(client, th, built, tkp)
    assert sub["status"] == "SUCCESS" and sub["trade_id"]
    r = await client.get(f"{API}/agreements/{ag.id}/trades", headers=th)
    t = r.json()["items"][0]
    assert t["note"] == "momentum" and t["notify_investors"] is False and t["symbol_label"] == "USDC/XLM · Alış"
    assert t["amount_out"] == money.format_amount(money.from_stroops(quote_raw))
    # selling the position back is labelled Satış
    r = await client.post(f"{API}/agreements/{ag.id}/tx/trade", headers=th, json={"token_in": str(usdc.id), "token_out": "XLM", "amount_in": "1"})
    assert r.status_code == 200, r.text
    pending2 = await db.get(PendingTransaction, r.json()["pending_tx_id"])
    assert pending2.payload["context"]["symbol_label"] == "USDC/XLM · Satış"


async def test_symbol_label(seed_assets):
    xlm, usdc, eurc = seed_assets["XLM"], seed_assets["USDC_SOROSWAP"], seed_assets["EURC_SOROSWAP"]
    assert symbol_label(xlm, usdc, xlm) == "USDC/XLM · Alış"
    assert symbol_label(usdc, xlm, xlm) == "USDC/XLM · Satış"
    assert symbol_label(usdc, eurc, xlm) == "EURC/USDC · Takas"
    assert symbol_label(usdc, xlm, usdc) == "XLM/USDC · Alış"
