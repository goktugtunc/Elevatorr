"""Dashboard slice (+ ratings): customer portfolio with live wallet balance via the Soroban gateway,
followed traders, listing interactions; trader managed capital / investors / pending offers / commission;
rating a settled agreement updates the trader's stats."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.core.errors import StellarError
from app.models import Agreement, AgreementStatus, Rating, User, UserRole
from app.services.amounts import to_stroops
from tests.test_listings import API, create_capital, create_service, mount


@pytest.fixture(scope="module", autouse=True)
def _mounted() -> None:
    mount()


def _agreement(customer: User, trader: User, asset, **kw) -> Agreement:  # noqa: ANN001
    data = dict(
        customer_id=customer.id,
        trader_id=trader.id,
        base_asset_id=asset.id,
        principal=Decimal("1000"),
        duration_secs=30 * 86_400,
        commission_bps=2000,
        max_drawdown_bps=2000,
        listing_ref="ab" * 32,
        status=AgreementStatus.active,
        proposer_role=UserRole.customer,
    )
    data.update(kw)
    return Agreement(**data)


async def test_customer_dashboard(client, db, make_user, auth_headers, seed_assets, soroban):
    customer, ct = await make_user("customer")
    t1, t1t = await make_user("trader", total_return_bps=1240, monthly_return_bps=310)
    t2, _ = await make_user("trader")
    t3, _ = await make_user("trader")
    xlm = seed_assets["XLM"]
    now = datetime.now(UTC)
    db.add_all(
        [
            _agreement(customer, t1, xlm, principal=Decimal("1000"), current_value=Decimal("1120"), onchain_id=1,
                       start_time=now - timedelta(days=3), end_time=now + timedelta(days=27)),
            _agreement(customer, t2, xlm, principal=Decimal("500"), status=AgreementStatus.funded, onchain_id=2),
            _agreement(customer, t1, xlm, principal=Decimal("2000"), status=AgreementStatus.settled, onchain_id=3,
                       final_value=Decimal("2300"), profit=Decimal("300"), trader_fee=Decimal("60"),
                       platform_fee=Decimal("0"), customer_payout=Decimal("2240"), settled_at=now - timedelta(days=5)),
            _agreement(customer, t3, xlm, principal=Decimal("100"), status=AgreementStatus.settled, onchain_id=4,
                       final_value=Decimal("90"), customer_payout=Decimal("90"), settled_at=now - timedelta(days=60)),
            _agreement(customer, t3, xlm, principal=Decimal("999"), status=AgreementStatus.draft, onchain_id=None),
        ]
    )
    await db.commit()
    for tid in (t1.id, t3.id):
        r = await client.post(f"{API}/discover/user/{tid}/action", json={"action": "follow"}, headers=auth_headers(ct))
        assert r.status_code == 200
    listing = await create_capital(client, ct, auth_headers)
    r = await client.post(f"{API}/discover/listing/{listing['id']}/action", json={"action": "like"}, headers=auth_headers(t1t))
    assert r.status_code == 200
    r = await client.post(f"{API}/offers", json={"listing_id": listing["id"]}, headers=auth_headers(t1t))
    assert r.status_code == 201
    soroban.fund_wallet(customer.stellar_address, xlm.contract_id, to_stroops(Decimal("250.5")))

    r = await client.get(f"{API}/dashboard", headers=auth_headers(ct))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["role"] == "customer" and d["base_asset_code"] == "XLM"
    assert Decimal(d["wallet_balance"]) == Decimal("250.5") and d["wallet_error"] is None
    assert Decimal(d["invested_principal"]) == Decimal("1500")  # active + funded, never draft/settled
    assert Decimal(d["positions_value"]) == Decimal("1620")  # 1120 valued + 500 principal (unvalued)
    assert Decimal(d["portfolio_value"]) == Decimal("1870.5")
    assert Decimal(d["open_pnl"]) == Decimal("120") and d["open_pnl_bps"] == 800
    assert Decimal(d["month_pnl"]) == Decimal("360")  # 240 realised this month + 120 open; the 60-day loss excluded
    assert d["month_change_bps"] == int((Decimal("360") * 10_000 / Decimal("3500")).to_integral_value())
    assert {p["counterparty_id"] for p in d["positions"]} == {str(t1.id), str(t2.id)}
    pos = next(p for p in d["positions"] if p["counterparty_id"] == str(t1.id))
    assert pos["onchain_id"] == 1 and pos["pnl_bps"] == 1200 and pos["status"] == "active" and pos["duration_days"] == 30
    assert d["followed_count"] == 2 and d["invested_count"] == 2
    followed = {f["trader_id"]: f for f in d["followed"]}
    assert followed[str(t1.id)]["invested"] is True and Decimal(followed[str(t1.id)]["invested_principal"]) == Decimal("1000")
    assert followed[str(t1.id)]["open_pnl_bps"] == 1200 and followed[str(t1.id)]["total_return_bps"] == 1240
    assert followed[str(t3.id)]["invested"] is False and followed[str(t3.id)]["open_pnl_bps"] is None
    assert d["listing_interactions"] == {"listings": 1, "views": 0, "likes": 1, "offers": 1, "pending_offers": 1}


async def test_customer_dashboard_degrades_without_rpc(client, make_user, auth_headers, seed_assets, soroban):
    customer, ct = await make_user("customer")

    async def boom(token: str, holder: str) -> int:
        raise StellarError("rpc unreachable", code="rpc_unavailable")

    soroban.token_balance = boom  # type: ignore[method-assign]
    r = await client.get(f"{API}/dashboard", headers=auth_headers(ct))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["wallet_balance"] is None and d["wallet_error"] == "rpc_unavailable"
    assert Decimal(d["portfolio_value"]) == 0 and d["positions"] == [] and d["followed"] == []
    assert d["listing_interactions"]["listings"] == 0


async def test_trader_dashboard_and_checklist(client, db, make_user, auth_headers, seed_assets, soroban):
    trader, tt = await make_user("trader", avatar_url=None)
    c1, c1t = await make_user("customer")
    c2, c2t = await make_user("customer")
    xlm = seed_assets["XLM"]
    now = datetime.now(UTC)
    db.add_all(
        [
            _agreement(c1, trader, xlm, principal=Decimal("3000"), current_value=Decimal("3150"), onchain_id=10),
            _agreement(c1, trader, xlm, principal=Decimal("1000"), status=AgreementStatus.funded, onchain_id=11),
            _agreement(c2, trader, xlm, principal=Decimal("500"), current_value=Decimal("480"), onchain_id=12),
            _agreement(c2, trader, xlm, principal=Decimal("2000"), status=AgreementStatus.settled, onchain_id=13,
                       final_value=Decimal("2500"), trader_fee=Decimal("100"), settled_at=now - timedelta(days=2)),
            _agreement(c1, trader, xlm, principal=Decimal("2000"), status=AgreementStatus.settled, onchain_id=14,
                       final_value=Decimal("2200"), trader_fee=Decimal("40"), settled_at=now - timedelta(days=45)),
            _agreement(c1, trader, xlm, principal=Decimal("50"), status=AgreementStatus.cancelled, onchain_id=15),
        ]
    )
    await db.commit()
    cap = await create_capital(client, c1t, auth_headers, amount="75000", risk_profile="balanced")
    cap2 = await create_capital(client, c2t, auth_headers, amount="30000")
    o1 = (await client.post(f"{API}/offers", json={"listing_id": cap["id"]}, headers=auth_headers(tt))).json()
    assert o1["status"] == "pending"

    r = await client.get(f"{API}/dashboard", headers=auth_headers(tt))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["role"] == "trader"
    assert Decimal(d["managed_capital"]) == Decimal("4630") and Decimal(d["invested_principal"]) == Decimal("4500")
    assert Decimal(d["open_pnl"]) == Decimal("130") and d["active_investors"] == 2
    assert Decimal(d["month_commission"]) == Decimal("100") and Decimal(d["total_commission"]) == Decimal("140")
    assert d["settled_agreements"] == 2 and len(d["positions"]) == 3
    assert d["pending_offers_count"] == 0 and d["pending_offers"] == []  # the trader's own outbox is not "pending for me"
    chk = d["profile_checklist"]
    assert chk["has_avatar"] is False and chk["has_strategy"] is True and chk["has_service_listing"] is False
    assert chk["has_trade"] is False and chk["completion_pct"] == 40

    # a customer requesting the trader's service shows up as a pending offer; a service listing completes the checklist
    svc = await create_service(client, tt, auth_headers)
    r = await client.post(f"{API}/offers", json={"listing_id": svc["id"], "amount": "1500", "duration_days": 60}, headers=auth_headers(c2t))
    assert r.status_code == 201, r.text
    r = await client.get(f"{API}/dashboard", headers=auth_headers(tt))
    d = r.json()
    assert d["pending_offers_count"] == 1
    po = d["pending_offers"][0]
    assert po["from_user_id"] == str(c2.id) and Decimal(po["amount"]) == Decimal("1500") and po["duration_days"] == 60
    assert po["risk_profile"] == "balanced" and po["base_asset_code"] == "XLM"
    assert d["profile_checklist"]["has_service_listing"] is True and d["profile_checklist"]["completion_pct"] == 60
    assert d["listing_interactions"] == {"listings": 1, "views": 0, "likes": 0, "offers": 1, "pending_offers": 1}
    assert cap2["id"]


# --- ratings ------------------------------------------------------------------------------------------------


async def test_customer_rates_settled_agreement_once(client, db, make_user, auth_headers, seed_assets):
    customer, ct = await make_user("customer", display_name="M. Coşkun")
    trader, tt = await make_user("trader")
    _, other_ct = await make_user("customer")
    xlm = seed_assets["XLM"]
    active = _agreement(customer, trader, xlm, onchain_id=20)
    settled = _agreement(customer, trader, xlm, onchain_id=21, status=AgreementStatus.settled, settled_at=datetime.now(UTC))
    settled2 = _agreement(customer, trader, xlm, onchain_id=22, status=AgreementStatus.settled, settled_at=datetime.now(UTC))
    db.add_all([active, settled, settled2])
    await db.commit()
    h = auth_headers(ct)

    r = await client.post(f"{API}/agreements/{active.id}/rating", json={"score": 5}, headers=h)
    assert r.status_code == 409 and r.json()["code"] == "agreement_not_settled"
    r = await client.post(f"{API}/agreements/{settled.id}/rating", json={"score": 5}, headers=auth_headers(tt))
    assert r.status_code == 403 and r.json()["code"] == "not_agreement_customer"
    r = await client.post(f"{API}/agreements/{settled.id}/rating", json={"score": 5}, headers=auth_headers(other_ct))
    assert r.status_code == 404
    r = await client.post(f"{API}/agreements/{settled.id}/rating", json={"score": 6}, headers=h)
    assert r.status_code == 422
    r = await client.get(f"{API}/agreements/{settled.id}/rating", headers=h)
    assert r.status_code == 404 and r.json()["code"] == "rating_not_found"

    r = await client.post(f"{API}/agreements/{settled.id}/rating", json={"score": 5, "comment": "6 aydır çalışıyoruz, raporlama çok şeffaf."}, headers=h)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["trader_id"] == str(trader.id) and Decimal(body["rating_avg"]) == Decimal("5") and body["rating_count"] == 1
    assert body["rating"]["score"] == 5 and body["rating"]["customer_display_name"] == "M. Coşkun"
    r = await client.post(f"{API}/agreements/{settled.id}/rating", json={"score": 1}, headers=h)
    assert r.status_code == 409 and r.json()["code"] == "already_rated"
    r = await client.post(f"{API}/agreements/{settled2.id}/rating", json={"score": 4}, headers=h)
    assert r.status_code == 201 and Decimal(r.json()["rating_avg"]) == Decimal("4.5") and r.json()["rating_count"] == 2

    await db.refresh(trader)
    assert trader.rating_avg == Decimal("4.50") and trader.rating_count == 2
    assert len((await db.execute(__import__("sqlalchemy").select(Rating))).scalars().all()) == 2
    r = await client.get(f"{API}/agreements/{settled.id}/rating", headers=auth_headers(tt))  # the trader can read it
    assert r.status_code == 200 and r.json()["comment"].startswith("6 aydır")
    r = await client.get(f"{API}/traders/{trader.id}/ratings")
    assert r.json()["total"] == 2
    r = await client.get(f"{API}/users/{trader.id}")
    assert Decimal(r.json()["stats"]["rating_avg"]) == Decimal("4.5")
    assert uuid.UUID(body["rating"]["id"])
