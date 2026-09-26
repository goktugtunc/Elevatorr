"""Offers slice: direction/kind rules, defaults from listing + profile, inbox/outbox, accept -> agreement
draft (terms copied, listing_ref, proposer_role) + conversation + notifications, reject/withdraw, expiry."""
from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models import (
    Agreement,
    AgreementStatus,
    Conversation,
    Listing,
    Message,
    Notification,
    Offer,
    OfferStatus,
)
from app.services import offers as offers_service
from tests.test_listings import API, create_capital, create_service, mount


@pytest.fixture(scope="module", autouse=True)
def _mounted() -> None:
    mount()


async def _notifications(db, user_id: uuid.UUID) -> list[Notification]:  # noqa: ANN001
    q = select(Notification).where(Notification.user_id == user_id).order_by(Notification.created_at)
    return list((await db.execute(q)).scalars().all())


async def test_trader_offers_on_capital_listing(client, db, make_user, auth_headers, seed_assets):
    customer, ct = await make_user("customer", risk_profile="balanced")
    trader, tt = await make_user("trader", commission_bps=2000)
    listing = await create_capital(client, ct, auth_headers, amount="75000", duration_days=180, max_loss_bps=2000)

    r = await client.post(
        f"{API}/offers",
        json={"listing_id": listing["id"], "commission_bps": 1800, "expected_return_min_bps": 1500,
              "expected_return_max_bps": 2000, "note": "Merhaba, ilanına teklif verdim."},
        headers=auth_headers(tt),
    )
    assert r.status_code == 201, r.text
    o = r.json()
    assert o["direction"] == "trader_to_customer" and o["status"] == "pending"
    assert o["from_user_id"] == str(trader.id) and o["to_user_id"] == str(customer.id)
    assert Decimal(o["amount"]) == Decimal("75000") and o["duration_days"] == 180 and o["max_drawdown_bps"] == 2000
    assert o["base_asset"]["id"] == listing["base_asset"]["id"] and o["commission_bps"] == 1800
    assert o["listing"]["title"] == listing["title"] and o["is_incoming"] is False
    assert o["conversation_id"] is not None
    expires = datetime.fromisoformat(o["expires_at"])
    assert timedelta(hours=71) < expires - datetime.now(UTC) < timedelta(hours=73)

    # side effects: counter, thread with the note as first message, notification, swipe card consumed
    row = await db.get(Listing, uuid.UUID(listing["id"]))
    assert row is not None and row.offer_count == 1
    conv = (await db.execute(select(Conversation).where(Conversation.offer_id == uuid.UUID(o["id"])))).scalar_one()
    assert {conv.participant_a, conv.participant_b} == {trader.id, customer.id} and conv.agreement_id is None
    msgs = (await db.execute(select(Message).where(Message.conversation_id == conv.id))).scalars().all()
    assert len(msgs) == 1 and msgs[0].body == "Merhaba, ilanına teklif verdim." and msgs[0].sender_id == trader.id
    notes = await _notifications(db, customer.id)
    assert [n.type for n in notes] == ["offer_received"] and notes[0].category.value == "offer"
    assert notes[0].data["offer_id"] == o["id"] and notes[0].data["conversation_id"] == str(conv.id)
    r = await client.get(f"{API}/discover", headers=auth_headers(tt))
    assert r.json()["items"] == []

    # defaults: commission from the trader profile; duplicate pending offer is rejected
    r = await client.post(f"{API}/offers", json={"listing_id": listing["id"]}, headers=auth_headers(tt))
    assert r.status_code == 409 and r.json()["code"] == "offer_already_pending"
    _, tt2 = await make_user("trader", commission_bps=2500)
    r = await client.post(f"{API}/offers", json={"listing_id": listing["id"]}, headers=auth_headers(tt2))
    assert r.status_code == 201 and r.json()["commission_bps"] == 2500
    # the base asset of a capital listing is fixed
    r = await client.post(
        f"{API}/offers", json={"listing_id": listing["id"], "base_asset_id": str(seed_assets["USDC"].id)},
        headers=auth_headers(await_token := tt2),
    )
    assert r.status_code in (409, 422) and await_token
    # owner's detail view lists the offers
    r = await client.get(f"{API}/listings/{listing['id']}", headers=auth_headers(ct))
    assert len(r.json()["offers"]) == 2 and r.json()["pending_offers"] == 2
    r = await client.get(f"{API}/listings/{listing['id']}", headers=auth_headers(tt))
    assert r.json()["offers"] is None and r.json()["my_offer_id"] == o["id"]


async def test_customer_requests_service(client, make_user, auth_headers, seed_assets):
    customer, ct = await make_user("customer")
    trader, tt = await make_user("trader", commission_bps=2000)
    listing = await create_service(client, tt, auth_headers, commission_bps=1800, min_capital="500")
    h = auth_headers(ct)

    r = await client.post(f"{API}/offers", json={"listing_id": listing["id"], "duration_days": 90}, headers=h)
    assert r.status_code == 422 and r.json()["code"] == "amount_required"
    r = await client.post(f"{API}/offers", json={"listing_id": listing["id"], "amount": "100", "duration_days": 90}, headers=h)
    assert r.status_code == 422 and r.json()["code"] == "below_min_capital"
    r = await client.post(f"{API}/offers", json={"listing_id": listing["id"], "amount": "1000"}, headers=h)
    assert r.status_code == 422 and r.json()["code"] == "duration_required"

    r = await client.post(f"{API}/offers", json={"listing_id": listing["id"], "amount": "1000", "duration_days": 90}, headers=h)
    assert r.status_code == 201, r.text
    o = r.json()
    assert o["direction"] == "customer_to_trader" and o["to_user_id"] == str(trader.id)
    assert o["commission_bps"] == 1800 and o["max_drawdown_bps"] == 10000
    assert o["base_asset"]["code"] == get_settings().default_base_asset_code
    assert o["expected_return_min_bps"] == 1500 and o["expected_return_max_bps"] == 2500  # from the listing
    assert o["conversation_id"] is not None

    # explicit base asset + own drawdown limit
    _, ct2 = await make_user("customer")
    usdc = seed_assets["USDC"]
    r = await client.post(
        f"{API}/offers",
        json={"listing_id": listing["id"], "amount": "2500", "duration_days": 30, "base_asset_id": str(usdc.id),
              "max_drawdown_bps": 1500, "commission_bps": 1500},
        headers=auth_headers(ct2),
    )
    assert r.status_code == 201, r.text
    assert r.json()["base_asset"]["id"] == str(usdc.id) and r.json()["max_drawdown_bps"] == 1500

    # kind / ownership rules
    cap = await create_capital(client, ct, auth_headers)
    r = await client.post(f"{API}/offers", json={"listing_id": cap["id"], "amount": "10", "duration_days": 30}, headers=h)
    assert r.status_code == 422 and r.json()["code"] == "own_listing"
    _, ct3 = await make_user("customer")
    r = await client.post(f"{API}/offers", json={"listing_id": cap["id"], "amount": "10", "duration_days": 30}, headers=auth_headers(ct3))
    assert r.status_code == 422 and r.json()["code"] == "listing_kind_mismatch"
    r = await client.post(f"{API}/offers", json={"listing_id": listing["id"], "amount": "1000", "duration_days": 30}, headers=auth_headers(tt))
    assert r.status_code == 422 and r.json()["code"] == "own_listing"
    await client.post(f"{API}/listings/{listing['id']}/pause", headers=auth_headers(tt))
    r = await client.post(f"{API}/offers", json={"listing_id": listing["id"], "amount": "1000", "duration_days": 30}, headers=auth_headers(ct3))
    assert r.status_code == 409 and r.json()["code"] == "listing_not_active"


async def test_inbox_outbox_and_visibility(client, make_user, auth_headers, seed_assets):
    customer, ct = await make_user("customer")
    trader, tt = await make_user("trader")
    stranger, st = await make_user("trader")
    cap = await create_capital(client, ct, auth_headers)
    svc = await create_service(client, tt, auth_headers)
    sent = (await client.post(f"{API}/offers", json={"listing_id": cap["id"]}, headers=auth_headers(tt))).json()
    received = (
        await client.post(f"{API}/offers", json={"listing_id": svc["id"], "amount": "1000", "duration_days": 30}, headers=auth_headers(ct))
    ).json()

    r = await client.get(f"{API}/offers", headers=auth_headers(tt))  # inbox default
    assert [x["id"] for x in r.json()["items"]] == [received["id"]] and r.json()["items"][0]["is_incoming"] is True
    r = await client.get(f"{API}/offers", params={"box": "outbox"}, headers=auth_headers(tt))
    assert [x["id"] for x in r.json()["items"]] == [sent["id"]]
    r = await client.get(f"{API}/offers", params={"box": "all"}, headers=auth_headers(tt))
    assert {x["id"] for x in r.json()["items"]} == {sent["id"], received["id"]}
    r = await client.get(f"{API}/offers", params={"box": "all", "status": "accepted"}, headers=auth_headers(tt))
    assert r.json()["total"] == 0
    r = await client.get(f"{API}/offers/stats", headers=auth_headers(tt))
    assert r.json() == {"pending_inbox": 1, "pending_outbox": 1}
    r = await client.get(f"{API}/offers/{sent['id']}", headers=auth_headers(ct))
    assert r.status_code == 200 and r.json()["is_incoming"] is True
    r = await client.get(f"{API}/offers/{sent['id']}", headers=auth_headers(st))
    assert r.status_code == 404
    r = await client.post(f"{API}/offers/{sent['id']}/accept", headers=auth_headers(st))
    assert r.status_code == 404


async def test_accept_creates_agreement_draft_and_conversation(client, db, make_user, auth_headers, seed_assets):
    customer, ct = await make_user("customer", risk_profile="conservative")
    trader, tt = await make_user("trader")
    listing = await create_capital(
        client, ct, auth_headers, amount="12345.5", duration_days=45, max_loss_bps=1200, risk_profile="balanced"
    )
    offer = (
        await client.post(f"{API}/offers", json={"listing_id": listing["id"], "commission_bps": 1750, "note": "Selam"}, headers=auth_headers(tt))
    ).json()

    r = await client.post(f"{API}/offers/{offer['id']}/accept", headers=auth_headers(tt))
    assert r.status_code == 403 and r.json()["code"] == "not_offer_recipient"

    r = await client.post(f"{API}/offers/{offer['id']}/accept", headers=auth_headers(ct))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["offer"]["status"] == "accepted" and body["offer"]["responded_at"] is not None
    ag = body["agreement"]
    assert body["offer"]["agreement_id"] == ag["id"] and body["conversation_id"] == offer["conversation_id"]
    assert body["next_action"] == "open"  # the customer accepted -> customer escrows via `open`
    assert ag["status"] == "draft" and ag["onchain_id"] is None and ag["proposer_role"] == "customer"
    assert ag["customer_id"] == str(customer.id) and ag["trader_id"] == str(trader.id)
    assert Decimal(ag["principal"]) == Decimal("12345.5") and ag["duration_secs"] == 45 * 86_400
    assert ag["commission_bps"] == 1750 and ag["max_drawdown_bps"] == 1200 and ag["risk_profile"] == "balanced"
    assert ag["base_asset_id"] == listing["base_asset"]["id"] and ag["base_asset"]["code"] == listing["base_asset"]["code"]
    assert ag["listing_ref"] == hashlib.sha256(offer["id"].encode()).hexdigest() == offers_service.listing_ref_for(uuid.UUID(offer["id"]))
    assert len(ag["listing_ref"]) == 64 and ag["offer_id"] == offer["id"] and ag["listing_id"] == listing["id"]

    row = await db.get(Agreement, uuid.UUID(ag["id"]))
    assert row is not None and row.status is AgreementStatus.draft and row.offer_id == uuid.UUID(offer["id"])
    conv = await db.get(Conversation, uuid.UUID(body["conversation_id"]))
    assert conv is not None and conv.agreement_id == row.id and conv.offer_id == row.offer_id
    trader_notes = [n.type for n in await _notifications(db, trader.id)]
    customer_notes = [n.type for n in await _notifications(db, customer.id)]
    assert trader_notes == ["offer_accepted", "agreement_created"]
    assert customer_notes == ["offer_received", "agreement_created"]
    accepted = next(n for n in await _notifications(db, trader.id) if n.type == "offer_accepted")
    assert accepted.data["agreement_id"] == ag["id"] and accepted.data["next_action"] == "open"

    # not pending any more
    r = await client.post(f"{API}/offers/{offer['id']}/accept", headers=auth_headers(ct))
    assert r.status_code == 409 and r.json()["code"] == "offer_not_pending"
    r = await client.post(f"{API}/offers/{offer['id']}/reject", headers=auth_headers(ct))
    assert r.status_code == 409
    r = await client.get(f"{API}/offers", params={"box": "inbox", "status": "accepted"}, headers=auth_headers(ct))
    assert [x["id"] for x in r.json()["items"]] == [offer["id"]]


async def test_trader_accepting_sets_propose_path(client, make_user, auth_headers, seed_assets):
    customer, ct = await make_user("customer", risk_profile="aggressive")
    trader, tt = await make_user("trader")
    svc = await create_service(client, tt, auth_headers)
    offer = (
        await client.post(f"{API}/offers", json={"listing_id": svc["id"], "amount": "800", "duration_days": 10}, headers=auth_headers(ct))
    ).json()
    r = await client.post(f"{API}/offers/{offer['id']}/accept", headers=auth_headers(tt))
    assert r.status_code == 200, r.text
    ag = r.json()["agreement"]
    assert r.json()["next_action"] == "propose" and ag["proposer_role"] == "trader"
    assert ag["customer_id"] == str(customer.id) and ag["trader_id"] == str(trader.id)
    assert ag["risk_profile"] == "aggressive"  # service listing has none -> the customer's profile
    assert ag["duration_secs"] == 10 * 86_400 and Decimal(ag["principal"]) == Decimal("800")


async def test_reject_withdraw_and_listing_closed(client, db, make_user, auth_headers, seed_assets):
    customer, ct = await make_user("customer")
    trader, tt = await make_user("trader")
    cap = await create_capital(client, ct, auth_headers)
    o1 = (await client.post(f"{API}/offers", json={"listing_id": cap["id"]}, headers=auth_headers(tt))).json()
    r = await client.post(f"{API}/offers/{o1['id']}/reject", json={"reason": "Komisyon yüksek"}, headers=auth_headers(tt))
    assert r.status_code == 403 and r.json()["code"] == "not_offer_recipient"
    r = await client.post(f"{API}/offers/{o1['id']}/reject", json={"reason": "Komisyon yüksek"}, headers=auth_headers(ct))
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    n = [x for x in await _notifications(db, trader.id) if x.type == "offer_rejected"]
    assert len(n) == 1 and "Komisyon yüksek" in n[0].body

    o2 = (await client.post(f"{API}/offers", json={"listing_id": cap["id"]}, headers=auth_headers(tt))).json()
    r = await client.post(f"{API}/offers/{o2['id']}/withdraw", headers=auth_headers(ct))
    assert r.status_code == 403 and r.json()["code"] == "not_offer_sender"
    r = await client.post(f"{API}/offers/{o2['id']}/withdraw", headers=auth_headers(tt))
    assert r.status_code == 200 and r.json()["status"] == "withdrawn"
    assert [x.type for x in await _notifications(db, customer.id)][-1] == "offer_withdrawn"

    o3 = (await client.post(f"{API}/offers", json={"listing_id": cap["id"]}, headers=auth_headers(tt))).json()
    await client.post(f"{API}/listings/{cap['id']}/close", headers=auth_headers(ct))
    r = await client.post(f"{API}/offers/{o3['id']}/accept", headers=auth_headers(ct))
    assert r.status_code == 409 and r.json()["code"] == "listing_not_active"
    assert (await db.execute(select(Agreement))).scalars().all() == []
    row = await db.get(Listing, uuid.UUID(cap["id"]))
    assert row is not None and row.offer_count == 3


async def test_expire_stale_and_expired_offer_cannot_be_accepted(client, db, make_user, auth_headers, seed_assets):
    customer, ct = await make_user("customer")
    trader, tt = await make_user("trader")
    cap = await create_capital(client, ct, auth_headers)
    stale = (await client.post(f"{API}/offers", json={"listing_id": cap["id"], "expires_in_hours": 1}, headers=auth_headers(tt))).json()
    _, tt2 = await make_user("trader")
    fresh = (await client.post(f"{API}/offers", json={"listing_id": cap["id"]}, headers=auth_headers(tt2))).json()

    row = await db.get(Offer, uuid.UUID(stale["id"]))
    assert row is not None
    row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await db.commit()

    assert await offers_service.expire_stale(db) == 1
    await db.commit()
    assert await offers_service.expire_stale(db) == 0
    await db.refresh(row)
    assert row.status is OfferStatus.expired and row.responded_at is not None
    assert [x.type for x in await _notifications(db, trader.id)] == ["offer_expired"]
    assert (await db.get(Offer, uuid.UUID(fresh["id"]))).status is OfferStatus.pending

    r = await client.post(f"{API}/offers/{stale['id']}/accept", headers=auth_headers(ct))
    assert r.status_code == 409 and r.json()["code"] == "offer_not_pending"

    # an offer past its expiry that the worker has not swept yet is expired on the fly
    fresh_row = await db.get(Offer, uuid.UUID(fresh["id"]))
    fresh_row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db.commit()
    r = await client.post(f"{API}/offers/{fresh['id']}/accept", headers=auth_headers(ct))
    assert r.status_code == 409 and r.json()["code"] == "offer_expired"
    assert r.json()["details"] == {"status": "expired"}
    r = await client.post(f"{API}/offers/{fresh['id']}/withdraw", headers=auth_headers(tt2))
    assert r.status_code == 409 and r.json()["code"] == "offer_expired"
    assert await offers_service.expire_stale(db) == 1  # the worker sweeps it
