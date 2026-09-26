"""Messages slice: threads opened by offers, list/unread, polling with `after`, send + notification,
mark read, participant-only access."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.models import Notification
from tests.test_listings import API, create_capital, mount


@pytest.fixture(scope="module", autouse=True)
def _mounted() -> None:
    mount()


async def test_thread_lifecycle(client, db, make_user, auth_headers, seed_assets):
    customer, ct = await make_user("customer", display_name="Ayşe")
    trader, tt = await make_user("trader", display_name="Kaan")
    _, st = await make_user("trader")
    cap = await create_capital(client, ct, auth_headers)
    offer = (
        await client.post(f"{API}/offers", json={"listing_id": cap["id"], "note": "Tekliflimi inceledin mi?"}, headers=auth_headers(tt))
    ).json()
    conv_id = offer["conversation_id"]

    # both parties see the thread; the recipient has one unread (the note)
    r = await client.get(f"{API}/conversations", headers=auth_headers(ct))
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1
    c = r.json()["items"][0]
    assert c["id"] == conv_id and c["offer_id"] == offer["id"] and c["agreement_id"] is None
    assert c["other_user"]["id"] == str(trader.id) and c["unread_count"] == 1
    assert c["last_message"]["body"] == "Tekliflimi inceledin mi?" and c["last_message"]["is_mine"] is False
    r = await client.get(f"{API}/conversations", headers=auth_headers(tt))
    assert r.json()["items"][0]["unread_count"] == 0 and r.json()["items"][0]["other_user"]["id"] == str(customer.id)
    r = await client.get(f"{API}/conversations/unread-count", headers=auth_headers(ct))
    assert r.json() == {"conversations": 1, "messages": 1}

    # strangers get 404 everywhere
    for method, url, kw in (
        ("get", f"{API}/conversations/{conv_id}", {}),
        ("get", f"{API}/conversations/{conv_id}/messages", {}),
        ("post", f"{API}/conversations/{conv_id}/messages", {"json": {"body": "hi"}}),
        ("post", f"{API}/conversations/{conv_id}/read", {}),
    ):
        r = await getattr(client, method)(url, headers=auth_headers(st), **kw)
        assert r.status_code == 404 and r.json()["code"] == "conversation_not_found", url
    assert (await client.get(f"{API}/conversations")).status_code == 401

    # reply + notification to the other side
    r = await client.post(f"{API}/conversations/{conv_id}/messages", json={"body": "  Selam, profilini inceledim.  "}, headers=auth_headers(ct))
    assert r.status_code == 201, r.text
    reply = r.json()
    assert reply["body"] == "Selam, profilini inceledim." and reply["is_mine"] is True and reply["read_at"] is None
    notes = (await db.execute(select(Notification).where(Notification.user_id == trader.id))).scalars().all()
    msg_notes = [n for n in notes if n.type == "message_received"]
    assert len(msg_notes) == 1 and msg_notes[0].title == "Ayşe" and msg_notes[0].data["conversation_id"] == conv_id
    r = await client.post(f"{API}/conversations/{conv_id}/messages", json={"body": "   "}, headers=auth_headers(ct))
    assert r.status_code == 422

    # thread is chronological; `after` polls only newer messages
    r = await client.get(f"{API}/conversations/{conv_id}/messages", headers=auth_headers(tt))
    items = r.json()["items"]
    assert [m["body"] for m in items] == ["Tekliflimi inceledin mi?", "Selam, profilini inceledim."]
    assert [m["is_mine"] for m in items] == [True, False] and r.json()["has_more"] is False
    r = await client.get(f"{API}/conversations/{conv_id}/messages", params={"after": items[0]["created_at"]}, headers=auth_headers(tt))
    assert [m["id"] for m in r.json()["items"]] == [reply["id"]]
    r = await client.get(f"{API}/conversations/{conv_id}/messages", params={"after": datetime.now(UTC).isoformat()}, headers=auth_headers(tt))
    assert r.json()["items"] == []
    r = await client.get(f"{API}/conversations/{conv_id}/messages", params={"limit": 1}, headers=auth_headers(tt))
    assert [m["id"] for m in r.json()["items"]] == [reply["id"]] and r.json()["has_more"] is True
    r = await client.get(f"{API}/conversations/{conv_id}/messages", params={"limit": 1, "before": reply["created_at"]}, headers=auth_headers(tt))
    assert [m["body"] for m in r.json()["items"]] == ["Tekliflimi inceledin mi?"] and r.json()["has_more"] is False

    # mark read: only the other side's messages, idempotent
    r = await client.post(f"{API}/conversations/{conv_id}/read", headers=auth_headers(tt))
    assert r.json() == {"conversation_id": conv_id, "updated": 1}
    r = await client.post(f"{API}/conversations/{conv_id}/read", headers=auth_headers(tt))
    assert r.json()["updated"] == 0
    r = await client.get(f"{API}/conversations/{conv_id}", headers=auth_headers(tt))
    assert r.json()["unread_count"] == 0 and r.json()["last_message"]["read_at"] is not None
    r = await client.get(f"{API}/conversations/unread-count", headers=auth_headers(ct))
    assert r.json() == {"conversations": 1, "messages": 1}  # the customer still has the note unread

    # accepting the offer links the agreement to the same thread
    r = await client.post(f"{API}/offers/{offer['id']}/accept", headers=auth_headers(ct))
    assert r.status_code == 200
    r = await client.get(f"{API}/conversations/{conv_id}", headers=auth_headers(ct))
    assert r.json()["agreement_id"] == (await client.get(f"{API}/offers/{offer['id']}", headers=auth_headers(ct))).json()["agreement_id"]


async def test_conversation_ordering_by_activity(client, make_user, auth_headers, seed_assets):
    customer, ct = await make_user("customer")
    _, t1 = await make_user("trader")
    _, t2 = await make_user("trader")
    cap = await create_capital(client, ct, auth_headers)
    first = (await client.post(f"{API}/offers", json={"listing_id": cap["id"], "note": "ilk"}, headers=auth_headers(t1))).json()
    second = (await client.post(f"{API}/offers", json={"listing_id": cap["id"], "note": "ikinci"}, headers=auth_headers(t2))).json()
    r = await client.get(f"{API}/conversations", headers=auth_headers(ct))
    assert [c["id"] for c in r.json()["items"]] == [second["conversation_id"], first["conversation_id"]]
    await client.post(f"{API}/conversations/{first['conversation_id']}/messages", json={"body": "hop"}, headers=auth_headers(ct))
    r = await client.get(f"{API}/conversations", headers=auth_headers(ct))
    assert [c["id"] for c in r.json()["items"]] == [first["conversation_id"], second["conversation_id"]]
    r = await client.get(f"{API}/conversations/{uuid.uuid4()}", headers=auth_headers(ct))
    assert r.status_code == 404
