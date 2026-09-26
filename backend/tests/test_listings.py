"""Listings slice: create (role -> kind, defaults), validation, public list/search, mine + counts,
status transitions, detail view counting. Helpers here are reused by the other slice tests."""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models import Interaction, InteractionAction, Listing
from tests.test_auth import mount_routers

SLICE_ROUTERS = ("users", "listings", "discover", "offers", "conversations", "dashboard", "ratings")
API = "/api/v1"


def mount() -> None:
    mount_routers(SLICE_ROUTERS)


@pytest.fixture(scope="module", autouse=True)
def _mounted() -> None:
    mount()


async def create_capital(client, token, auth_headers, **overrides) -> dict:  # noqa: ANN001
    body = {
        "title": "250k sermaye, dengeli portföy",
        "description": "Emeklilik birikimimin bir kısmı",
        "amount": "250000",
        "duration_days": 180,
        "max_loss_bps": 2000,
        "markets": ["crypto"],
    }
    body.update(overrides)
    r = await client.post(f"{API}/listings", json=body, headers=auth_headers(token))
    assert r.status_code == 201, r.text
    return r.json()


async def create_service(client, token, auth_headers, **overrides) -> dict:  # noqa: ANN001
    body = {
        "title": "XLM/USDC momentum",
        "description": "Soroswap üzerinde likit çiftlerde swing",
        "commission_bps": 1800,
        "min_capital": "500",
        "expected_return_min_bps": 1500,
        "expected_return_max_bps": 2500,
        "markets": ["crypto", "stable_fx"],
    }
    body.update(overrides)
    r = await client.post(f"{API}/listings", json=body, headers=auth_headers(token))
    assert r.status_code == 201, r.text
    return r.json()


# --- create -------------------------------------------------------------------------------------------------


async def test_customer_creates_capital_listing_with_defaults(client, make_user, auth_headers, seed_assets):
    customer, token = await make_user("customer", risk_profile="aggressive", markets=["defi"])
    r = await client.post(
        f"{API}/listings",
        json={"title": "Sermaye arıyorum", "amount": "1000.5", "duration_days": 30},
        headers=auth_headers(token),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "capital" and body["status"] == "active"
    assert Decimal(body["amount"]) == Decimal("1000.5")
    assert body["base_asset"]["code"] == get_settings().default_base_asset_code
    assert body["base_asset"]["is_base_allowed"] is True
    assert body["risk_profile"] == "aggressive"  # defaulted from the profile
    assert body["markets"] == ["defi"]
    assert body["max_loss_bps"] is None and body["commission_bps"] is None
    assert body["owner"]["id"] == str(customer.id) and body["is_owner"] is True
    assert body["view_count"] == 0 and body["like_count"] == 0 and body["offer_count"] == 0


async def test_trader_creates_service_listing_with_profile_defaults(client, make_user, auth_headers):
    trader, token = await make_user("trader", commission_bps=1500, min_capital=Decimal("250"))
    r = await client.post(f"{API}/listings", json={"title": "Momentum stratejisi"}, headers=auth_headers(token))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "service"
    assert body["commission_bps"] == 1500 and Decimal(body["min_capital"]) == Decimal("250")
    assert body["markets"] == ["crypto", "stable_fx"]
    assert body["amount"] is None and body["base_asset"] is None
    assert body["owner"]["stats"]["rating_count"] == 0


async def test_create_validation(client, make_user, auth_headers, seed_assets):
    _, ctoken = await make_user("customer")
    _, ttoken = await make_user("trader")
    h = auth_headers(ctoken)
    r = await client.post(f"{API}/listings", json={"title": "x" * 5, "kind": "service"}, headers=h)
    assert r.status_code == 422 and r.json()["code"] == "listing_kind_for_role"
    r = await client.post(f"{API}/listings", json={"title": "Sermaye", "amount": "10", "duration_days": 5, "commission_bps": 100}, headers=h)
    assert r.status_code == 422 and r.json()["code"] == "field_not_allowed_for_kind"
    r = await client.post(f"{API}/listings", json={"title": "Sermaye", "duration_days": 5}, headers=h)
    assert r.status_code == 422 and r.json()["code"] == "amount_required"
    r = await client.post(f"{API}/listings", json={"title": "Sermaye", "amount": "10"}, headers=h)
    assert r.status_code == 422 and r.json()["code"] == "duration_required"
    # contract limits: duration 1..1095 days, drawdown 100..10000, commission <= 5000
    r = await client.post(f"{API}/listings", json={"title": "Sermaye", "amount": "10", "duration_days": 2000}, headers=h)
    assert r.status_code == 422 and r.json()["code"] == "validation_error"
    r = await client.post(f"{API}/listings", json={"title": "Sermaye", "amount": "10", "duration_days": 10, "max_loss_bps": 50}, headers=h)
    assert r.status_code == 422
    r = await client.post(f"{API}/listings", json={"title": "Hizmet", "commission_bps": 6000}, headers=auth_headers(ttoken))
    assert r.status_code == 422
    r = await client.post(
        f"{API}/listings", json={"title": "Hizmet", "expected_return_min_bps": 3000, "expected_return_max_bps": 1000},
        headers=auth_headers(ttoken),
    )
    assert r.status_code == 422
    # base asset must be allow-listed as base
    eurc = seed_assets["EURC_SOROSWAP"]
    r = await client.post(
        f"{API}/listings", json={"title": "Sermaye", "amount": "10", "duration_days": 10, "base_asset_id": str(eurc.id)}, headers=h
    )
    assert r.status_code == 422 and r.json()["code"] == "asset_not_base"
    r = await client.post(f"{API}/listings", json={"title": "Sermaye", "amount": "10", "duration_days": 10}, headers={})
    assert r.status_code == 401


# --- list / search --------------------------------------------------------------------------------------------


async def test_public_list_filters_and_search(client, make_user, auth_headers, seed_assets):
    _, ctoken = await make_user("customer", markets=["crypto"])
    _, ttoken = await make_user("trader", display_name="Kaan Demir")
    cap = await create_capital(client, ctoken, auth_headers, markets=["crypto"])
    svc = await create_service(client, ttoken, auth_headers, markets=["stable_fx"], title="EUR/USD taşıma")
    paused = await create_service(client, ttoken, auth_headers, title="Duraklatılan")
    r = await client.post(f"{API}/listings/{paused['id']}/pause", headers=auth_headers(ttoken))
    assert r.status_code == 200 and r.json()["status"] == "paused"

    r = await client.get(f"{API}/listings")  # anonymous
    assert r.status_code == 200
    ids = {x["id"] for x in r.json()["items"]}
    assert ids == {cap["id"], svc["id"]} and r.json()["total"] == 2
    assert all(x["is_owner"] is None for x in r.json()["items"])

    r = await client.get(f"{API}/listings", params={"kind": "capital"})
    assert [x["id"] for x in r.json()["items"]] == [cap["id"]]
    r = await client.get(f"{API}/listings", params={"market": "stable_fx"})
    assert [x["id"] for x in r.json()["items"]] == [svc["id"]]
    r = await client.get(f"{API}/listings", params={"q": "kaan"})
    assert [x["id"] for x in r.json()["items"]] == [svc["id"]]
    r = await client.get(f"{API}/listings", params={"q": "taşıma"})
    assert [x["id"] for x in r.json()["items"]] == [svc["id"]]
    r = await client.get(f"{API}/listings", params={"sort": "amount", "limit": 1})
    assert r.json()["items"][0]["id"] == cap["id"] and r.json()["total"] == 2

    r = await client.get(f"{API}/listings", headers=auth_headers(ttoken))
    mine = {x["id"]: x for x in r.json()["items"]}
    assert mine[svc["id"]]["is_owner"] is True and mine[cap["id"]]["is_owner"] is False


async def test_mine_counts_and_status_transitions(client, db, make_user, auth_headers, seed_assets):
    owner, token = await make_user("customer")
    _, other_token = await make_user("customer")
    h = auth_headers(token)
    a = await create_capital(client, token, auth_headers, title="Birinci ilan")
    b = await create_capital(client, token, auth_headers, title="İkinci ilan")

    assert (await client.post(f"{API}/listings/{a['id']}/pause", headers=h)).json()["status"] == "paused"
    r = await client.post(f"{API}/listings/{a['id']}/pause", headers=h)
    assert r.status_code == 409 and r.json()["code"] == "listing_invalid_transition"
    assert (await client.post(f"{API}/listings/{a['id']}/resume", headers=h)).json()["status"] == "active"
    closed = (await client.post(f"{API}/listings/{b['id']}/close", headers=h)).json()
    assert closed["status"] == "closed" and closed["closed_at"] is not None
    r = await client.post(f"{API}/listings/{b['id']}/resume", headers=h)
    assert r.status_code == 409
    r = await client.patch(f"{API}/listings/{b['id']}", json={"title": "Yeni başlık"}, headers=h)
    assert r.status_code == 409 and r.json()["code"] == "listing_closed"

    r = await client.get(f"{API}/listings/mine", headers=h)
    assert r.json()["total"] == 2 and {x["status"] for x in r.json()["items"]} == {"active", "closed"}
    r = await client.get(f"{API}/listings/mine", params={"status": "closed"}, headers=h)
    assert [x["id"] for x in r.json()["items"]] == [b["id"]]
    r = await client.get(f"{API}/listings/mine/counts", headers=h)
    assert r.json() == {"active": 1, "paused": 0, "closed": 1}

    # only the owner edits
    r = await client.patch(f"{API}/listings/{a['id']}", json={"title": "Hack"}, headers=auth_headers(other_token))
    assert r.status_code == 403 and r.json()["code"] == "not_listing_owner"
    r = await client.post(f"{API}/listings/{a['id']}/close", headers=auth_headers(other_token))
    assert r.status_code == 403
    r = await client.patch(
        f"{API}/listings/{a['id']}",
        json={"title": "Güncel başlık", "amount": "300000", "max_loss_bps": 1500, "markets": ["defi", "defi"]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "Güncel başlık" and Decimal(body["amount"]) == Decimal("300000")
    assert body["max_loss_bps"] == 1500 and body["markets"] == ["defi"]
    r = await client.patch(f"{API}/listings/{a['id']}", json={"commission_bps": 100}, headers=h)
    assert r.status_code == 422 and r.json()["code"] == "field_not_allowed_for_kind"
    r = await client.patch(f"{API}/listings/{a['id']}", json={"amount": None}, headers=h)
    assert r.status_code == 422 and r.json()["code"] == "field_required"
    listing = await db.get(Listing, __import__("uuid").UUID(a["id"]))
    assert listing is not None and listing.title == "Güncel başlık"


async def test_detail_counts_one_view_per_viewer(client, db, make_user, auth_headers, seed_assets):
    owner, otoken = await make_user("customer")
    viewer, vtoken = await make_user("trader")
    listing = await create_capital(client, otoken, auth_headers)
    url = f"{API}/listings/{listing['id']}"

    r = await client.get(url)  # anonymous: no view recorded
    assert r.status_code == 200 and r.json()["view_count"] == 0 and r.json()["offers"] is None
    r = await client.get(url, headers=auth_headers(otoken))  # owner never counts, sees the offers list
    assert r.json()["view_count"] == 0 and r.json()["offers"] == [] and r.json()["is_owner"] is True
    r = await client.get(url, headers=auth_headers(vtoken))
    assert r.json()["view_count"] == 1 and r.json()["offers"] is None and r.json()["my_offer_id"] is None
    r = await client.get(url, headers=auth_headers(vtoken))
    assert r.json()["view_count"] == 1  # idempotent per viewer
    rows = (await db.execute(select(Interaction).where(Interaction.action == InteractionAction.view))).scalars().all()
    assert len(rows) == 1 and rows[0].user_id == viewer.id

    r = await client.get(f"{API}/listings/{__import__('uuid').uuid4()}")
    assert r.status_code == 404 and r.json()["code"] == "listing_not_found"
