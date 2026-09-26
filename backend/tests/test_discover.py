"""Discover slice: role-aware feed, exclusions, cursor pagination, remaining count and swipe actions."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Favorite, Follow, Interaction, InteractionAction, InteractionTargetType, Listing
from tests.test_listings import API, create_capital, create_service, mount


@pytest.fixture(scope="module", autouse=True)
def _mounted() -> None:
    mount()


async def test_feed_is_role_aware_and_excludes_own(client, make_user, auth_headers, seed_assets):
    c1, c1t = await make_user("customer")
    c2, c2t = await make_user("customer")
    t1, t1t = await make_user("trader")
    t2, t2t = await make_user("trader")
    cap1 = await create_capital(client, c1t, auth_headers)
    cap2 = await create_capital(client, c2t, auth_headers)
    svc1 = await create_service(client, t1t, auth_headers)
    svc2 = await create_service(client, t2t, auth_headers)
    paused = await create_service(client, t2t, auth_headers, title="Duraklatıldı")
    await client.post(f"{API}/listings/{paused['id']}/pause", headers=auth_headers(t2t))

    r = await client.get(f"{API}/discover", headers=auth_headers(c1t))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "service" and body["remaining"] == 2 and body["next_cursor"] is None
    assert {x["target_id"] for x in body["items"]} == {svc1["id"], svc2["id"]}
    card = body["items"][0]
    assert card["target_type"] == "listing" and card["kind"] == "service"
    assert card["owner_target_id"] == card["listing"]["owner_id"]
    assert card["listing"]["owner"]["role"] == "trader" and card["listing"]["owner"]["stats"] is not None
    assert card["is_following"] is False and "new" in card["tags"]

    r = await client.get(f"{API}/discover", headers=auth_headers(t1t))
    body = r.json()
    assert body["kind"] == "capital" and {x["target_id"] for x in body["items"]} == {cap1["id"], cap2["id"]}
    assert "no_offers_yet" in body["items"][0]["tags"] and "long_term" in body["items"][0]["tags"]

    r = await client.get(f"{API}/discover/remaining", headers=auth_headers(c2t))
    assert r.json() == {"remaining": 2, "kind": "service"}
    r = await client.get(f"{API}/discover", params={"market": "defi"}, headers=auth_headers(c2t))
    assert r.json()["remaining"] == 0 and r.json()["items"] == []
    assert (await client.get(f"{API}/discover")).status_code == 401


async def test_cursor_pagination(client, make_user, auth_headers, seed_assets):
    _, ct = await make_user("customer")
    _, tt = await make_user("trader")
    ids = [(await create_service(client, tt, auth_headers, title=f"Strateji {i}"))["id"] for i in range(5)]

    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        params = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        r = await client.get(f"{API}/discover", params=params, headers=auth_headers(ct))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["remaining"] == 5
        seen += [x["target_id"] for x in body["items"]]
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert pages == 3 and seen == list(reversed(ids))  # newest first, no duplicates, no gaps

    r = await client.get(f"{API}/discover", params={"cursor": "garbage"}, headers=auth_headers(ct))
    assert r.status_code == 422 and r.json()["code"] == "invalid_cursor"


async def test_actions_hide_cards_and_have_side_effects(client, db, make_user, auth_headers, seed_assets):
    customer, ct = await make_user("customer")
    trader, tt = await make_user("trader")
    trader2, tt2 = await make_user("trader")
    svc_a = await create_service(client, tt, auth_headers, title="Strateji A")
    svc_b = await create_service(client, tt, auth_headers, title="Strateji B")
    svc_c = await create_service(client, tt2, auth_headers, title="Strateji C")
    h = auth_headers(ct)

    def url(target_type: str, target_id: str) -> str:
        return f"{API}/discover/{target_type}/{target_id}/action"

    # pass -> hidden
    r = await client.post(url("listing", svc_a["id"]), json={"action": "pass"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["created"] is True and r.json()["remaining"] == 2
    r = await client.post(url("listing", svc_a["id"]), json={"action": "pass"}, headers=h)
    assert r.json()["created"] is False and r.json()["remaining"] == 2  # idempotent

    # like -> counter + hidden
    r = await client.post(url("listing", svc_b["id"]), json={"action": "like"}, headers=h)
    assert r.json()["created"] is True and r.json()["like_count"] == 1 and r.json()["remaining"] == 1
    r = await client.post(url("listing", svc_b["id"]), json={"action": "like"}, headers=h)
    assert r.json()["created"] is False and r.json()["like_count"] == 1
    listing_b = await db.get(Listing, __import__("uuid").UUID(svc_b["id"]))
    assert listing_b is not None and listing_b.like_count == 1

    # follow on a service card follows the trader, does not hide the card
    r = await client.post(url("listing", svc_c["id"]), json={"action": "follow"}, headers=h)
    assert r.json()["following"] is True and r.json()["target_type"] == "user" and r.json()["remaining"] == 1
    assert await db.get(Follow, (customer.id, trader2.id)) is not None
    r = await client.get(f"{API}/discover", headers=h)
    assert [x["target_id"] for x in r.json()["items"]] == [svc_c["id"]]
    assert r.json()["items"][0]["is_following"] is True

    # save -> favourite + hidden; unsave keeps it hidden (the save interaction stays) but removes the favourite
    r = await client.post(url("listing", svc_c["id"]), json={"action": "save"}, headers=h)
    assert r.json()["saved"] is True and r.json()["remaining"] == 0
    assert await db.get(Favorite, (customer.id, trader2.id)) is None
    r = await client.get(f"{API}/listings/saved", headers=h)
    assert [x["id"] for x in r.json()["items"]] == [svc_c["id"]] and r.json()["items"][0]["is_saved"] is True
    r = await client.delete(f"{API}/discover/listing/{svc_c['id']}/save", headers=h)
    assert r.json()["saved"] is False and r.json()["created"] is True
    assert (await client.get(f"{API}/listings/saved", headers=h)).json()["total"] == 0

    actions = (await db.execute(select(Interaction.action).where(Interaction.user_id == customer.id))).scalars().all()
    assert sorted(a.value for a in actions) == ["follow", "like", "pass", "save"]


async def test_user_target_actions_and_validation(client, db, make_user, auth_headers, seed_assets):
    customer, ct = await make_user("customer")
    customer2, ct2 = await make_user("customer")
    trader, tt = await make_user("trader")
    svc1 = await create_service(client, tt, auth_headers, title="Strateji S1")
    svc2 = await create_service(client, tt, auth_headers, title="Strateji S2")
    own = await create_capital(client, ct, auth_headers)
    h = auth_headers(ct)

    # passing the trader as a user hides all of their cards
    r = await client.post(f"{API}/discover/user/{trader.id}/action", json={"action": "pass"}, headers=h)
    assert r.status_code == 200 and r.json()["remaining"] == 0
    r = await client.get(f"{API}/discover", headers=h)
    assert r.json()["items"] == []
    row = (await db.execute(select(Interaction).where(Interaction.target_type == InteractionTargetType.user))).scalar_one()
    assert row.target_id == trader.id and row.action is InteractionAction.pass_

    # a trader's feed: like a capital listing via user-follow is invalid (customers cannot be followed)
    r = await client.post(f"{API}/discover/user/{customer.id}/action", json={"action": "follow"}, headers=auth_headers(tt))
    assert r.status_code == 422 and r.json()["code"] == "not_a_trader"
    r = await client.post(f"{API}/discover/user/{customer.id}/action", json={"action": "save"}, headers=auth_headers(tt))
    assert r.status_code == 422 and r.json()["code"] == "invalid_action"
    r = await client.post(f"{API}/discover/listing/{own['id']}/action", json={"action": "like"}, headers=h)
    assert r.status_code == 422 and r.json()["code"] == "own_listing"
    r = await client.post(f"{API}/discover/user/{customer.id}/action", json={"action": "like"}, headers=h)
    assert r.status_code == 422 and r.json()["code"] == "own_profile"
    r = await client.post(f"{API}/discover/listing/{svc1['id']}/action", json={"action": "teleport"}, headers=h)
    assert r.status_code == 422
    r = await client.post(f"{API}/discover/user/{__import__('uuid').uuid4()}/action", json={"action": "pass"}, headers=h)
    assert r.status_code == 404
    # the trader follows via the user target (customer2 -> trader) and the card shows is_following
    r = await client.post(f"{API}/discover/user/{trader.id}/action", json={"action": "follow"}, headers=auth_headers(ct2))
    assert r.json()["following"] is True and r.json()["created"] is True
    r = await client.post(f"{API}/discover/user/{trader.id}/action", json={"action": "follow"}, headers=auth_headers(ct2))
    assert r.json()["created"] is False
    r = await client.get(f"{API}/discover", headers=auth_headers(ct2))
    assert {x["target_id"] for x in r.json()["items"]} == {svc1["id"], svc2["id"]}
    assert all(x["is_following"] for x in r.json()["items"])
