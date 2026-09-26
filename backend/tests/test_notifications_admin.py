"""Notifications (+ Expo push), FX, /config, /assets and the admin endpoints."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models import (
    Agreement,
    AgreementStatus,
    Asset,
    IndexerState,
    NotificationCategory,
    UserRole,
)
from app.services import fx as fx_service
from app.services import notifications as notif
from app.services.stellar.admin_tx import platform_public_key
from app.services.stellar.sep10 import server_public_key
from tests.test_auth import mount_routers

VAULT = "CCJUD55AG6W5HAI5LRVNKAE5WDP5XGZBUDS5WNTIVDU7O264UZZE7BRD"  # any valid C... for tests


def invoked_fn(envelope) -> str:  # noqa: ANN001 - stellar_sdk TransactionEnvelope
    """Contract function name of the first invoke_host_function op of an envelope."""
    return envelope.transaction.operations[0].host_function.invoke_contract.function_name.sc_symbol.decode()


@pytest.fixture(scope="module", autouse=True)
def _mounted() -> None:
    mount_routers()


@pytest.fixture(autouse=True)
def _reset_caches():
    fx_service.reset_cache()
    from app.routers.config import reset_contract_cache

    reset_contract_cache()
    yield
    fx_service.reset_cache()
    reset_contract_cache()


# --- notifications --------------------------------------------------------------------------------------


async def test_notifications_list_unread_mark_read(client, db, make_user, auth_headers):
    user, token = await make_user("customer")
    other, otoken = await make_user("trader")
    n1 = await notif.notify(db, user.id, "offer_received", "Yeni teklif", "Ali teklif verdi", {"offer_id": uuid.uuid4()},
                            category=NotificationCategory.offer)
    n2 = await notif.notify(db, user.id, "agreement_activated", "Sözleşme aktif", category="agreement")
    n3 = await notif.notify(db, other.id, "system", "Hoş geldin")
    await db.commit()
    assert isinstance(n1.data["offer_id"], str)  # JSON-safe

    r = await client.get("/api/v1/notifications", headers=auth_headers(token))
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 2 and [n["type"] for n in r.json()["items"]] == ["agreement_activated", "offer_received"]
    r = await client.get("/api/v1/notifications", params={"category": "offer"}, headers=auth_headers(token))
    assert r.json()["total"] == 1 and r.json()["items"][0]["id"] == str(n1.id)
    r = await client.get("/api/v1/notifications/unread-count", headers=auth_headers(token))
    assert r.json() == {"unread": 2, "by_category": {"offer": 1, "agreement": 1}}

    r = await client.post(f"/api/v1/notifications/{n3.id}/read", headers=auth_headers(token))
    assert r.status_code == 404  # not mine
    r = await client.get(f"/api/v1/notifications/{n3.id}", headers=auth_headers(token))
    assert r.status_code == 404
    r = await client.post(f"/api/v1/notifications/{n1.id}/read", headers=auth_headers(token))
    assert r.json() == {"updated": 1}
    r = await client.post(f"/api/v1/notifications/{n1.id}/read", headers=auth_headers(token))
    assert r.json() == {"updated": 0}
    r = await client.post("/api/v1/notifications/read", json={"ids": [str(n2.id), str(n3.id)]}, headers=auth_headers(token))
    assert r.json() == {"updated": 1}  # n3 belongs to someone else
    r = await client.get("/api/v1/notifications", params={"unread_only": "true"}, headers=auth_headers(token))
    assert r.json()["total"] == 0
    r = await client.post("/api/v1/notifications/read", json={}, headers=auth_headers(token))
    assert r.status_code == 422
    r = await client.post("/api/v1/notifications/read", json={"all": True, "category": "system"}, headers=auth_headers(otoken))
    assert r.json() == {"updated": 1}

    r = await client.put("/api/v1/notifications/push-token", json={"expo_push_token": "nope"}, headers=auth_headers(token))
    assert r.status_code == 422
    r = await client.put(
        "/api/v1/notifications/push-token", json={"expo_push_token": "ExponentPushToken[dev1]"}, headers=auth_headers(token)
    )
    assert r.status_code == 200 and r.json()["expo_push_token"] == "ExponentPushToken[dev1]"


def expo_transport(responses: list[dict]) -> tuple[httpx.AsyncClient, list[dict]]:
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        calls.append(json.loads(request.content))
        return httpx.Response(200, json=responses[min(len(calls) - 1, len(responses) - 1)])

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls


async def test_send_expo_push_results():
    settings = get_settings()
    client, calls = expo_transport([{"data": [{"status": "ok", "id": "ticket-1"}]}])
    res = await notif.send_expo_push(settings, "ExponentPushToken[abc]", "Başlık", "Gövde", {"k": 1}, client=client)
    assert res.ok and res.ticket_id == "ticket-1"
    assert calls[0]["to"] == "ExponentPushToken[abc]" and calls[0]["data"] == {"k": 1}
    client, _ = expo_transport([{"data": [{"status": "error", "details": {"error": "DeviceNotRegistered"}}]}])
    res = await notif.send_expo_push(settings, "ExponentPushToken[abc]", "t", "b", client=client)
    assert not res.ok and res.permanent and res.error == "DeviceNotRegistered"
    res = await notif.send_expo_push(settings, "bad-token", "t", "b", client=client)
    assert res.error == "invalid_expo_token" and res.permanent


async def test_deliver_pending_pushes(db, make_user):
    settings = get_settings()
    user, _ = await make_user("customer", expo_push_token="ExponentPushToken[dev]")
    gone, _ = await make_user("trader", expo_push_token="ExponentPushToken[gone]")
    silent, _ = await make_user("trader")  # no token -> never selected
    n_ok = await notif.notify(db, user.id, "t", "Merhaba", category="wallet")
    n_gone = await notif.notify(db, gone.id, "t", "Merhaba")
    await notif.notify(db, silent.id, "t", "Merhaba")
    await db.commit()

    disabled = settings.model_copy(update={"expo_push_enabled": False})
    counters = await notif.deliver_pending_pushes(db, disabled)
    await db.commit()
    assert counters["disabled"] == 2 and counters["sent"] == 0
    await db.refresh(n_ok)
    assert n_ok.data["push_sent"] is False and n_ok.data["push_error"] == "disabled"

    n_ok2 = await notif.notify(db, user.id, "t2", "Tekrar", category="wallet")
    n_gone2 = await notif.notify(db, gone.id, "t2", "Tekrar")
    await db.commit()
    enabled = settings.model_copy(update={"expo_push_enabled": True})

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        to = json.loads(request.content)["to"]
        if "gone" in to:
            return httpx.Response(200, json={"data": [{"status": "error", "details": {"error": "DeviceNotRegistered"}}]})
        return httpx.Response(200, json={"data": [{"status": "ok", "id": "x"}]})

    counters = await notif.deliver_pending_pushes(db, enabled, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await db.commit()
    assert counters == {"sent": 1, "failed": 1, "retry": 0, "disabled": 0}
    await db.refresh(n_ok2)
    await db.refresh(n_gone2)
    await db.refresh(gone)
    assert n_ok2.data["push_sent"] is True and n_gone2.data["push_error"] == "DeviceNotRegistered"
    assert gone.expo_push_token is None  # unregistered device token dropped
    assert await notif.pending_push_notifications(db) == []
    del n_gone


# --- FX ---------------------------------------------------------------------------------------------------


def fx_transport(primary: dict | int, secondary: dict | int) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        body = primary if "open.er-api" in str(request.url) else secondary
        return httpx.Response(body, json={}) if isinstance(body, int) else httpx.Response(200, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_fx_sources_cache_and_db_fallback(db):
    settings = get_settings()
    fx = await fx_service.get_usd_try(settings, db, client=fx_transport({"rates": {"TRY": 48.782361}}, 500))
    assert fx.rate == Decimal("48.7823610") and fx.source == "primary" and not fx.stale
    assert fx_service.to_try(Decimal("10"), fx) == Decimal("487.82")
    row = await db.get(IndexerState, fx_service.FX_STATE_KEY)
    assert row is not None and row.cursor == "48.7823610|primary"

    # fresh cache: no HTTP call at all (transport would 500)
    fx2 = await fx_service.get_usd_try(settings, db, client=fx_transport(500, 500))
    assert fx2 == fx
    # forced refresh, primary down -> secondary
    fx3 = await fx_service.get_usd_try(settings, db, client=fx_transport(500, {"rates": {"TRY": "49.1"}}), force=True)
    assert fx3.source == "secondary" and fx3.rate == Decimal("49.1000000")
    # everything down -> stale cache
    fx4 = await fx_service.get_usd_try(settings, db, client=fx_transport(500, 500), force=True)
    assert fx4.stale and fx4.rate == Decimal("49.1000000")
    # cold process, everything down -> persisted row
    fx_service.reset_cache()
    await db.commit()
    fx5 = await fx_service.get_usd_try(settings, db, client=fx_transport(500, {"rates": {}}), force=True)
    assert fx5.stale and fx5.source == "db:secondary" and fx5.rate == Decimal("49.1000000")
    assert fx_service.usd_price_map(["XLM", "USDC", "EURC"]) == {"XLM": None, "USDC": Decimal("1.00"), "EURC": Decimal("1.08")}
    assert fx_service.try_value(Decimal("2"), "EURC", fx5) == Decimal("106.05")


async def test_fx_endpoint(client, db, seed_assets, monkeypatch):
    async def fake_fetch(settings, *, client=None):
        return fx_service.FxRate(rate=Decimal("48.5"), source="primary", fetched_at=datetime.now(UTC))

    monkeypatch.setattr(fx_service, "fetch_usd_try", fake_fetch)
    r = await client.get("/api/v1/fx")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pair"] == "USDTRY" and body["rate"] == "48.5000000" and body["stale"] is False
    assert body["usd_prices"]["USDC"] == "1.00" and body["usd_prices"]["XLM"] is None and body["usd_prices_indicative"]
    r = await client.get("/api/v1/fx/convert", params={"amount_usd": "3"})
    assert r.json()["amount_try"] == "145.50"

    async def down(settings, *, client=None):
        raise fx_service.FxUnavailableError("down")

    monkeypatch.setattr(fx_service, "fetch_usd_try", down)
    fx_service.reset_cache()
    r = await client.get("/api/v1/fx")  # persisted row from the first call
    assert r.status_code == 200 and r.json()["stale"] is True and r.json()["source"] == "db:primary"
    fx_service.reset_cache()
    await db.execute(IndexerState.__table__.delete())
    await db.commit()
    r = await client.get("/api/v1/fx")
    assert r.status_code == 503 and r.json()["code"] == "fx_unavailable"


# --- /config and /assets -------------------------------------------------------------------------------------


async def test_config_and_assets(client, seed_assets, soroban, monkeypatch):
    settings = get_settings()
    r = await client.get("/api/v1/config")
    assert r.status_code == 200, r.text
    cfg = r.json()
    assert cfg["network"] == settings.stellar_network and cfg["network_passphrase"] == settings.network_passphrase
    assert cfg["vault_contract_id"] == soroban.vault  # the gateway's vault (fake: FAKE_VAULT_ID)
    assert cfg["soroswap_router_id"] == settings.effective_soroswap_router_id
    assert cfg["signing_key"] == server_public_key(settings) and cfg["platform_account"] == platform_public_key(settings)
    assert cfg["web_auth_endpoint"].endswith("/api/v1/auth/sep10")
    # live contract config read through the gateway (fake vault: fee 0, router = Soroswap testnet)
    assert cfg["contract"]["router"] == settings.effective_soroswap_router_id and cfg["contract"]["paused"] is False
    assert cfg["platform_fee_bps"] == 0 and cfg["contract_error"] is None
    assert cfg["limits"]["max_commission_bps"] == 5000 and cfg["anchor"]["home_domain"] == settings.anchor_home_domain
    assert cfg["auth"]["login_message_prefix"] == "traderkirala-login:"
    codes = {a["code"] for a in cfg["assets"]}
    assert {"XLM", "USDC"} <= codes and cfg["assets"][0]["is_base_allowed"] is True
    assert cfg["usd_prices"]["USDC"] == "1.00" and cfg["usd_prices"]["XLM"] is None

    # RPC failure -> contract null + contract_error, config still served
    async def boom():
        raise RuntimeError("rpc down")

    soroban.get_config = boom
    from app.routers.config import reset_contract_cache

    reset_contract_cache()
    r = await client.get("/api/v1/config")
    assert r.status_code == 200 and r.json()["contract"] is None and r.json()["platform_fee_bps"] is None
    assert r.json()["contract_error"].startswith("RuntimeError")

    r = await client.get("/api/v1/assets")
    assert r.status_code == 200 and len(r.json()) == len(seed_assets)
    first = r.json()[0]
    assert first["is_base_allowed"] and first["contract_id"].startswith("C") and "canonical" in first
    r = await client.get("/api/v1/assets", params={"base_only": "true"})
    assert all(a["is_base_allowed"] for a in r.json()) and len(r.json()) == 3
    r = await client.get(f"/api/v1/assets/{seed_assets['XLM'].id}")
    assert r.json()["canonical"] == "native" and r.json()["is_native"] is True
    r = await client.get(f"/api/v1/assets/{uuid.uuid4()}")
    assert r.status_code == 404


# --- admin ------------------------------------------------------------------------------------------------------


async def test_admin_guard_stats_users(client, db, make_user, admin_headers):
    r = await client.get("/api/v1/admin/stats")
    assert r.status_code == 403 and r.json()["code"] == "admin_key_invalid"
    r = await client.get("/api/v1/admin/stats", headers={"X-Admin-Key": "wrong"})
    assert r.status_code == 403

    trader, _ = await make_user("trader", username="t1")
    customer, _ = await make_user("customer", username="c1")
    await notif.notify(db, customer.id, "x", "y")
    await db.commit()
    r = await client.get("/api/v1/admin/stats", headers=admin_headers)
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["users_total"] == 2 and s["users_by_role"] == {"trader": 1, "customer": 1} and s["notifications_unread"] == 1
    assert s["managed_capital_active"] == "0.0000000" and s["network"] == get_settings().stellar_network

    r = await client.get("/api/v1/admin/users", params={"q": "t1"}, headers=admin_headers)
    assert r.json()["total"] == 1 and r.json()["items"][0]["id"] == str(trader.id)
    r = await client.get("/api/v1/admin/users", params={"role": "customer"}, headers=admin_headers)
    assert r.json()["total"] == 1
    r = await client.patch(f"/api/v1/admin/users/{trader.id}", json={"is_active": False, "is_admin": True}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["is_active"] is False and r.json()["is_admin"] is True
    r = await client.get(f"/api/v1/admin/users/{uuid.uuid4()}", headers=admin_headers)
    assert r.status_code == 404


async def test_admin_assets_and_sync_onchain(client, db, seed_assets, soroban, admin_headers):
    asked: list[str] = []
    real = soroban.is_token_allowed

    async def spy(contract_id: str):
        asked.append(contract_id)
        return await real(contract_id)

    soroban.is_token_allowed = spy
    r = await client.post("/api/v1/admin/assets/sync-onchain", headers=admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["vault_contract_id"] == soroban.vault
    assert body["checked"] == len(seed_assets) == len(asked)
    by_id = {row["contract_id"]: row for row in body["rows"]}
    expected = {}
    for key, asset in seed_assets.items():
        info = await real(asset.contract_id)
        expected[key] = (info.allowed, info.is_base)
        assert by_id[asset.contract_id]["onchain_allowed"] is info.allowed
        assert by_id[asset.contract_id]["onchain_is_base"] is info.is_base
        assert by_id[asset.contract_id]["error"] is None
    assert expected["XLM"] == (True, True) and expected["EURC_SOROSWAP"] == (True, False) and expected["SRT"] == (False, False)
    assert body["changed"] == sum(1 for a, _ in expected.values() if a)
    db.expire_all()
    rows = (await db.execute(select(Asset).where(Asset.onchain_allowed.is_(True)))).scalars().all()
    assert {a.contract_id for a in rows} == {a.contract_id for k, a in seed_assets.items() if expected[k][0]}
    r = await client.post("/api/v1/admin/assets/sync-onchain", headers=admin_headers)
    assert r.json()["changed"] == 0  # idempotent
    soroban.set_token(seed_assets["EURC_SOROSWAP"].contract_id, False, False)  # de-listed on chain
    r = await client.post("/api/v1/admin/assets/sync-onchain", headers=admin_headers)
    assert r.json()["changed"] == 1 and by_id  # only EURC flipped
    r = await client.get("/api/v1/assets", params={"onchain_only": "true"})
    assert {a["code"] for a in r.json()} == {"XLM", "USDC"}

    r = await client.get("/api/v1/admin/assets", headers=admin_headers)
    assert len(r.json()) == len(seed_assets)
    bad_id = "CDLZFC3SYJYDZT7K67VZ75HPJVIEUVNIXF47ZG2FB2RMQQVU2HHGCYSD"  # malformed checksum
    r = await client.post("/api/v1/admin/assets", json={"contract_id": bad_id, "code": "AQUA", "name": "Aquarius"}, headers=admin_headers)
    assert r.status_code == 422 and r.json()["code"] == "invalid_contract_id"
    r = await client.post(
        "/api/v1/admin/assets",
        json={"contract_id": seed_assets["XLM"].contract_id, "code": "XLM", "name": "dup"},
        headers=admin_headers,
    )
    assert r.status_code == 409
    r = await client.post(
        "/api/v1/admin/assets",
        json={"contract_id": VAULT, "code": "AQUA", "name": "Aquarius", "category": "defi", "is_base_allowed": False},
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()["category"] == "defi" and r.json()["onchain_allowed"] is False
    aid = r.json()["id"]
    r = await client.patch(f"/api/v1/admin/assets/{aid}", json={"is_active": False, "name": None}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["is_active"] is False and r.json()["name"] == "Aquarius"
    r = await client.get("/api/v1/assets")
    assert all(a["id"] != aid for a in r.json())


async def test_admin_agreements_indexer_and_contract_tx(client, db, seed_assets, make_user, admin_headers, monkeypatch):
    from stellar_sdk import Keypair, TransactionEnvelope

    from app.services.stellar import set_soroban
    from app.services.stellar.fake import FakeSorobanGateway

    settings = get_settings()
    admin_pk = platform_public_key(settings)
    soroban = FakeSorobanGateway(admin=admin_pk)  # the platform account is the vault admin
    set_soroban(soroban)
    try:
        trader, _ = await make_user("trader")
        customer, _ = await make_user("customer")
        now = datetime.now(UTC)
        ag = Agreement(
            customer_id=customer.id, trader_id=trader.id, base_asset_id=seed_assets["XLM"].id, principal=Decimal("500"),
            duration_secs=86_400, commission_bps=1000, max_drawdown_bps=10_000, listing_ref="00" * 32,
            status=AgreementStatus.active, proposer_role=UserRole.trader, onchain_id=3, start_time=now,
            end_time=now + timedelta(days=1),
        )
        db.add(ag)
        db.add(IndexerState(key="vault_events", cursor="0000001-0000000000", ledger=900))
        await db.commit()

        r = await client.get("/api/v1/admin/agreements", params={"status": "active"}, headers=admin_headers)
        assert r.status_code == 200, r.text
        assert r.json()["total"] == 1 and r.json()["items"][0]["onchain_id"] == 3
        r = await client.get("/api/v1/admin/agreements", params={"trader_id": str(uuid.uuid4())}, headers=admin_headers)
        assert r.json()["total"] == 0
        r = await client.get("/api/v1/admin/stats", headers=admin_headers)
        assert r.json()["managed_capital_active"] == "500.0000000" and r.json()["agreements_by_status"] == {"active": 1}

        latest = await soroban.latest_ledger()
        r = await client.get("/api/v1/admin/indexer", headers=admin_headers)
        assert r.status_code == 200, r.text
        assert r.json()["states"][0]["ledger"] == 900 and r.json()["latest_ledger"] == latest
        assert r.json()["lag_ledgers"] == latest - 900 and r.json()["rpc_error"] is None
        assert r.json()["vault_contract_id"] == soroban.vault
        r = await client.post("/api/v1/admin/indexer/reset", json={"key": "vault_events", "ledger": 800}, headers=admin_headers)
        assert r.status_code == 200 and r.json()["ledger"] == 800 and r.json()["cursor"] is None
        r = await client.post("/api/v1/admin/indexer/reset", json={"key": "vault_events"}, headers=admin_headers)
        assert r.status_code == 200 and r.json() is None
        r = await client.post("/api/v1/admin/indexer/reset", json={"key": "vault_events"}, headers=admin_headers)
        assert r.status_code == 404

        # --- admin tx: build (unsigned, source = platform) -> sign with the platform key -> submit
        srt = seed_assets["SRT"]
        assert (await soroban.is_token_allowed(srt.contract_id)).allowed is False
        r = await client.post(
            "/api/v1/admin/contract/tx/set_token",
            json={"token": str(srt.id), "allowed": True, "is_base": False},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["source"] == admin_pk and body["contract_id"] == soroban.vault
        assert body["args"] == {"token": srt.contract_id, "allowed": True, "is_base": False}
        assert body["network_passphrase"] == settings.network_passphrase and body["tx_hash"]
        env = TransactionEnvelope.from_xdr(body["unsigned_xdr"], settings.network_passphrase)
        assert env.transaction.source.account_id == admin_pk and not env.signatures
        assert invoked_fn(env) == "set_token"
        env.sign(Keypair.from_secret(settings.platform_secret))
        r = await client.post("/api/v1/admin/contract/tx/submit", json={"signed_xdr": env.to_xdr()}, headers=admin_headers)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "SUCCESS" and r.json()["tx_hash"] == body["tx_hash"] and r.json()["error"] is None
        info = await soroban.is_token_allowed(srt.contract_id)
        assert info.allowed is True and info.is_base is False  # applied on the (fake) chain

        r = await client.post("/api/v1/admin/contract/tx/set_token", json={"token": "nope"}, headers=admin_headers)
        assert r.status_code == 422 and r.json()["code"] == "invalid_token"
        r = await client.post("/api/v1/admin/contract/tx/set_paused", json={"paused": True}, headers=admin_headers)
        assert r.status_code == 200, r.text
        env = TransactionEnvelope.from_xdr(r.json()["unsigned_xdr"], settings.network_passphrase)
        assert invoked_fn(env) == "set_paused"
        r = await client.post("/api/v1/admin/contract/tx/set_fees", json={"platform_fee_bps": 100}, headers=admin_headers)
        assert r.status_code == 200, r.text
        assert r.json()["args"] == {"platform_fee_bps": 100, "fee_recipient": admin_pk}
        r = await client.post("/api/v1/admin/contract/tx/set_fees", json={"platform_fee_bps": 5000}, headers=admin_headers)
        assert r.status_code == 422
        # note: signature validity is the network's job (tx_bad_auth on the real RPC); the in-memory fake
        # applies any envelope whose hash it registered, so it is not asserted here.

        # set_router is not wrapped by the gateway -> direct ContractClient builder (monkeypatched here)
        from app.services.stellar import admin_tx

        async def fake_build(settings_, function, values, source):
            return f"XDR:{function}:{source}"

        monkeypatch.setattr(admin_tx, "build_admin_invoke_xdr", fake_build)
        r = await client.post("/api/v1/admin/contract/tx/set_router", json={"router": VAULT}, headers=admin_headers)
        assert r.status_code == 200 and r.json()["unsigned_xdr"] == f"XDR:set_router:{admin_pk}"
    finally:
        set_soroban(None)


async def test_admin_tx_encoding_and_config_read(soroban, monkeypatch):
    from stellar_sdk import scval

    from app.core.errors import StellarError, ValidationError
    from app.services.stellar import admin_tx

    args = admin_tx.encode_args("set_token", {"token": VAULT, "allowed": True, "is_base": False})
    assert [scval.to_native(a) for a in args][1:] == [True, False]
    assert scval.from_address(args[0]).address == VAULT
    with pytest.raises(ValidationError):
        admin_tx.encode_args("set_fees", {"platform_fee_bps": -1, "fee_recipient": VAULT})
    with pytest.raises(ValidationError):
        admin_tx.encode_args("set_token", {"token": "G-not-valid", "allowed": True, "is_base": False})
    with pytest.raises(ValidationError):
        admin_tx.encode_args("upgrade", {})
    settings = get_settings()
    monkeypatch.setattr(settings, "vault_contract_id", None)
    with pytest.raises(StellarError):
        await admin_tx.build_admin_invoke_xdr(settings, "set_paused", {"paused": True}, platform_public_key(settings))

    from app.services.admin import contract_config

    cfg, err = await contract_config(soroban)
    assert err is None and cfg["router"] == settings.effective_soroswap_router_id and cfg["paused"] is False

    async def boom():
        raise RuntimeError("rpc down")

    soroban.get_config = boom
    cfg, err = await contract_config(soroban)
    assert cfg is None and err.startswith("RuntimeError")
