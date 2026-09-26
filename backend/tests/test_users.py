"""Users slice: registration with role-specific blocks, profile update rules, public profiles,
trader profile aggregate (3e), trader list, follows and ratings."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from stellar_sdk import Keypair

from app.core.config import get_settings
from app.core.security import create_access_token, decode_access_token
from app.models import (
    Agreement,
    AgreementBalance,
    AgreementStatus,
    AgreementValueSnapshot,
    Follow,
    Notification,
    Rating,
    Trade,
    UserRole,
)
from tests.test_auth import mount_routers


@pytest.fixture(scope="module", autouse=True)
def _mounted() -> None:
    mount_routers()


def wallet_token() -> tuple[Keypair, str]:
    kp = Keypair.random()
    return kp, create_access_token(get_settings(), public_key=kp.public_key, user_id=None, role=None)


CUSTOMER_BODY = {
    "role": "customer",
    "username": "Ayse_Yilmaz",
    "display_name": "Ayşe Yılmaz",
    "customer": {"budget_amount": "2500.50", "risk_profile": "balanced", "markets": ["crypto", "stable_fx", "crypto"]},
}
TRADER_BODY = {
    "role": "trader",
    "username": "ali_trader",
    "display_name": "Ali",
    "bio": "10 yıl piyasa",
    "trader": {
        "markets": ["crypto"],
        "strategy_summary": "XLM/USDC momentum on Soroswap",
        "commission_bps": 2000,
        "min_capital": "100",
        "risk_level": "medium",
    },
}


# --- registration ---------------------------------------------------------------------------------------


async def test_register_customer(client, auth_headers):
    kp, token = wallet_token()
    r = await client.post("/api/v1/users/register", json=CUSTOMER_BODY, headers=auth_headers(token))
    assert r.status_code == 201, r.text
    body = r.json()
    u = body["user"]
    assert u["username"] == "ayse_yilmaz" and u["role"] == "customer" and u["stellar_address"] == kp.public_key
    assert u["budget_amount"] == "2500.5000000" and u["risk_profile"] == "balanced"
    assert u["markets"] == ["crypto", "stable_fx"]  # deduped, order kept
    assert u["stats"] is None and u["is_admin"] is False
    claims = decode_access_token(get_settings(), body["token"])
    assert claims.role == "customer" and str(claims.user_id) == u["id"]

    r = await client.get("/api/v1/users/me", headers=auth_headers(body["token"]))
    assert r.status_code == 200 and r.json()["id"] == u["id"]

    # second registration with the same wallet
    r = await client.post("/api/v1/users/register", json=TRADER_BODY, headers=auth_headers(token))
    assert r.status_code == 409 and r.json()["code"] == "already_registered"


async def test_register_trader_and_validation(client, auth_headers):
    _, token = wallet_token()
    r = await client.post("/api/v1/users/register", json=TRADER_BODY, headers=auth_headers(token))
    assert r.status_code == 201, r.text
    u = r.json()["user"]
    assert u["commission_bps"] == 2000 and u["risk_level"] == "medium" and u["min_capital"] == "100.0000000"
    assert u["stats"]["rating_count"] == 0 and u["stats"]["managed_capital"] == "0.0000000"
    assert u["budget_amount"] is None

    # username taken
    _, token2 = wallet_token()
    r = await client.post("/api/v1/users/register", json=TRADER_BODY, headers=auth_headers(token2))
    assert r.status_code == 409 and r.json()["code"] == "username_taken"

    # missing role block / wrong block / bad commission / bad username
    bad = {**TRADER_BODY, "username": "x_y_z", "trader": None}
    r = await client.post("/api/v1/users/register", json=bad, headers=auth_headers(token2))
    assert r.status_code == 422
    bad = {**CUSTOMER_BODY, "username": "x_y_z", "trader": TRADER_BODY["trader"]}
    r = await client.post("/api/v1/users/register", json=bad, headers=auth_headers(token2))
    assert r.status_code == 422
    bad = {**TRADER_BODY, "username": "x_y_z", "trader": {**TRADER_BODY["trader"], "commission_bps": 6000}}
    r = await client.post("/api/v1/users/register", json=bad, headers=auth_headers(token2))
    assert r.status_code == 422
    bad = {**TRADER_BODY, "username": "bad name!"}
    r = await client.post("/api/v1/users/register", json=bad, headers=auth_headers(token2))
    assert r.status_code == 422

    # no token at all
    r = await client.post("/api/v1/users/register", json=TRADER_BODY)
    assert r.status_code == 401


# --- profile update ---------------------------------------------------------------------------------------


async def test_patch_me_role_rules(client, make_user, auth_headers):
    _, ctoken = await make_user("customer")
    r = await client.patch("/api/v1/users/me", json={"commission_bps": 100}, headers=auth_headers(ctoken))
    assert r.status_code == 422 and r.json()["code"] == "field_not_allowed_for_role"
    r = await client.patch("/api/v1/users/me", json={"budget_amount": None}, headers=auth_headers(ctoken))
    assert r.status_code == 422 and r.json()["code"] == "field_required"
    r = await client.patch(
        "/api/v1/users/me",
        json={"budget_amount": "5000", "risk_profile": "aggressive", "markets": ["defi"], "bio": "merhaba"},
        headers=auth_headers(ctoken),
    )
    assert r.status_code == 200, r.text
    assert r.json()["budget_amount"] == "5000.0000000" and r.json()["markets"] == ["defi"] and r.json()["bio"] == "merhaba"

    _, ttoken = await make_user("trader")
    r = await client.patch("/api/v1/users/me", json={"risk_profile": "balanced"}, headers=auth_headers(ttoken))
    assert r.status_code == 422 and r.json()["code"] == "field_not_allowed_for_role"
    r = await client.patch("/api/v1/users/me", json={"expo_push_token": "garbage"}, headers=auth_headers(ttoken))
    assert r.status_code == 422 and r.json()["code"] == "invalid_expo_token"
    r = await client.patch(
        "/api/v1/users/me",
        json={"expo_push_token": "ExponentPushToken[abc123]", "commission_bps": 1500, "avatar_url": None},
        headers=auth_headers(ttoken),
    )
    assert r.status_code == 200, r.text
    assert r.json()["expo_push_token"] == "ExponentPushToken[abc123]" and r.json()["commission_bps"] == 1500


async def test_public_profile_hides_private_fields(client, make_user, auth_headers):
    trader, _ = await make_user("trader", expo_push_token="ExponentPushToken[zzz]")
    customer, ctoken = await make_user("customer")
    r = await client.get(f"/api/v1/users/{trader.id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "expo_push_token" not in body and "is_admin" not in body
    assert body["stats"]["total_return_bps"] == 0 and body["strategy_summary"]
    r = await client.get(f"/api/v1/users/by-username/{trader.username.upper()}")
    assert r.status_code == 200 and r.json()["id"] == str(trader.id)
    r = await client.get(f"/api/v1/users/{customer.id}")
    assert r.status_code == 200 and r.json()["stats"] is None
    disabled, _ = await make_user("customer", is_active=False)
    r = await client.get(f"/api/v1/users/{disabled.id}")
    assert r.status_code == 404


# --- follows ------------------------------------------------------------------------------------------------


async def test_follow_unfollow(client, db, make_user, auth_headers):
    trader, ttoken = await make_user("trader")
    customer, ctoken = await make_user("customer")
    r = await client.post(f"/api/v1/traders/{trader.id}/follow", headers=auth_headers(ctoken))
    assert r.status_code == 200, r.text
    assert r.json() == {"trader_id": str(trader.id), "following": True, "follower_count": 1}
    r = await client.post(f"/api/v1/traders/{trader.id}/follow", headers=auth_headers(ctoken))
    assert r.json()["follower_count"] == 1  # idempotent
    assert (await db.get(Follow, (customer.id, trader.id))) is not None
    notes = (await db.execute(select(Notification).where(Notification.user_id == trader.id))).scalars().all()
    assert len(notes) == 1 and notes[0].type == "new_follower" and notes[0].category.value == "system"

    r = await client.post(f"/api/v1/traders/{trader.id}/follow", headers=auth_headers(ttoken))
    assert r.status_code == 422 and r.json()["code"] == "cannot_follow_self"
    r = await client.post(f"/api/v1/traders/{customer.id}/follow", headers=auth_headers(ttoken))
    assert r.status_code == 404 and r.json()["code"] == "trader_not_found"

    r = await client.get(f"/api/v1/traders/{trader.id}/profile", headers=auth_headers(ctoken))
    assert r.json()["is_following"] is True and r.json()["follower_count"] == 1
    r = await client.get(f"/api/v1/traders/{trader.id}/profile")
    assert r.json()["is_following"] is None

    r = await client.delete(f"/api/v1/traders/{trader.id}/follow", headers=auth_headers(ctoken))
    assert r.status_code == 200 and r.json()["following"] is False and r.json()["follower_count"] == 0


# --- trader profile aggregate -------------------------------------------------------------------------------


async def seed_trader_activity(db, seed_assets, trader, customer):
    now = datetime.now(UTC)
    xlm, usdc = seed_assets["XLM"], seed_assets["USDC_SOROSWAP"]
    ag = Agreement(
        onchain_id=7,
        customer_id=customer.id,
        trader_id=trader.id,
        base_asset_id=xlm.id,
        principal=Decimal("1000"),
        duration_secs=30 * 86_400,
        commission_bps=2000,
        max_drawdown_bps=2000,
        listing_ref="ab" * 32,
        status=AgreementStatus.active,
        proposer_role=UserRole.customer,
        start_time=now - timedelta(days=2),
        end_time=now + timedelta(days=28),
        current_value=Decimal("1100"),
        value_updated_at=now,
    )
    db.add(ag)
    await db.flush()
    db.add_all(
        [
            AgreementBalance(agreement_id=ag.id, asset_id=xlm.id, balance=Decimal("800")),
            AgreementBalance(agreement_id=ag.id, asset_id=usdc.id, balance=Decimal("75.5")),
            AgreementValueSnapshot(agreement_id=ag.id, value=Decimal("1050"), at=now - timedelta(hours=3)),
            AgreementValueSnapshot(agreement_id=ag.id, value=Decimal("1100"), at=now - timedelta(hours=1)),
            AgreementValueSnapshot(agreement_id=ag.id, value=Decimal("990"), at=now - timedelta(days=40)),
            Trade(
                agreement_id=ag.id,
                onchain_seq=0,
                tx_hash="cd" * 32,
                ledger=123456,
                trader_id=trader.id,
                token_in_id=xlm.id,
                token_out_id=usdc.id,
                amount_in=Decimal("200"),
                amount_out=Decimal("75.5"),
                value_after=Decimal("1100"),
                note="XLM düşüşünde USDC'ye geçtim",
                symbol_label="XLM/USDC · Alış",
            ),
            Rating(agreement_id=ag.id, customer_id=customer.id, trader_id=trader.id, score=4, comment="İyi"),
        ]
    )
    await db.commit()
    return ag


async def test_trader_profile_aggregate(client, db, seed_assets, make_user, auth_headers):
    trader, _ = await make_user("trader", username="pro_trader", rating_avg=Decimal("4.00"), rating_count=1)
    customer, ctoken = await make_user("customer", username="musteri1")
    ag = await seed_trader_activity(db, seed_assets, trader, customer)

    r = await client.get(f"/api/v1/traders/{trader.id}/profile", headers=auth_headers(ctoken))
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["user"]["username"] == "pro_trader" and p["stats"]["rating_avg"] == "4.00"
    assert p["performance_range"] == "30d"
    # 30d window, daily buckets: the two recent snapshots collapse into one bucket with the LATEST value
    assert len(p["performance"]) == 1
    point = p["performance"][0]
    assert point["value"] == "1100.0000000" and point["principal"] == "1000.0000000" and point["return_bps"] == 1000
    assert len(p["positions"]) == 1
    pos = p["positions"][0]
    assert pos["agreement_id"] == str(ag.id) and pos["onchain_id"] == 7 and pos["customer_username"] == "musteri1"
    assert pos["base_asset_code"] == "XLM" and pos["pnl_bps"] == 1000
    assert {(b["code"], b["balance"]) for b in pos["balances"]} == {("XLM", "800.0000000"), ("USDC", "75.5000000")}
    assert len(p["recent_trades"]) == 1
    t = p["recent_trades"][0]
    assert t["symbol_label"] == "XLM/USDC · Alış" and t["token_in_code"] == "XLM" and t["tx_hash"] == "cd" * 32
    assert p["ratings"] == {"avg": "4.00", "count": 1, "distribution": {"4": 1}}
    assert p["recent_ratings"][0]["customer_username"] == "musteri1" and p["recent_ratings"][0]["score"] == 4
    assert p["active_listings"] == 0

    # 7d window uses hourly buckets -> two points; "all" includes the 40-day-old snapshot
    r = await client.get(f"/api/v1/traders/{trader.id}/profile", params={"range": "7d"})
    assert [pt["value"] for pt in r.json()["performance"]] == ["1050.0000000", "1100.0000000"]
    r = await client.get(f"/api/v1/traders/{trader.id}/profile", params={"range": "all"})
    assert len(r.json()["performance"]) == 2 and r.json()["performance"][0]["return_bps"] == -100

    r = await client.get(f"/api/v1/traders/{trader.id}/ratings")
    assert r.status_code == 200 and r.json()["total"] == 1 and r.json()["items"][0]["comment"] == "İyi"
    r = await client.get(f"/api/v1/traders/{customer.id}/profile")
    assert r.status_code == 404


async def test_list_traders_filters_and_sort(client, make_user, auth_headers):
    a, _ = await make_user("trader", username="a_tr", rating_avg=Decimal("4.5"), rating_count=3, markets=["crypto"])
    b, _ = await make_user("trader", username="b_tr", rating_avg=Decimal("3.0"), total_return_bps=2500, markets=["defi"])
    c, _ = await make_user("trader", username="c_tr", is_active=False)
    customer, ctoken = await make_user("customer")
    await client.post(f"/api/v1/traders/{b.id}/follow", headers=auth_headers(ctoken))

    r = await client.get("/api/v1/traders", headers=auth_headers(ctoken))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2 and [i["user"]["username"] for i in body["items"]] == ["a_tr", "b_tr"]
    assert body["items"][1]["follower_count"] == 1 and body["items"][1]["is_following"] is True
    assert body["items"][0]["is_following"] is False

    r = await client.get("/api/v1/traders", params={"sort": "return"})
    assert [i["user"]["username"] for i in r.json()["items"]] == ["b_tr", "a_tr"]
    assert r.json()["items"][0]["is_following"] is None
    r = await client.get("/api/v1/traders", params={"market": "defi"})
    assert [i["user"]["username"] for i in r.json()["items"]] == ["b_tr"]
    r = await client.get("/api/v1/traders", params={"q": "A_T"})
    assert [i["user"]["username"] for i in r.json()["items"]] == ["a_tr"]
    r = await client.get("/api/v1/traders", params={"sort": "followers", "limit": 1})
    assert r.json()["items"][0]["user"]["username"] == "b_tr" and r.json()["total"] == 2
