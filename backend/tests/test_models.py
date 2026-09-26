"""Smoke tests for the data model: row creation across the whole schema, enum round trips,
FK cycle offers<->agreements, uniques, seed idempotency and SAC id correctness."""
from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from stellar_sdk import Asset as SdkAsset

from app.core.config import PUBLIC_PASSPHRASE, TESTNET_PASSPHRASE
from app.models import (
    Agreement,
    AgreementBalance,
    AgreementStatus,
    AgreementValueSnapshot,
    AnchorSession,
    AnchorTransaction,
    AnchorTxKind,
    AnchorTxStatus,
    Asset,
    AuthNonce,
    Conversation,
    Favorite,
    Follow,
    IndexerState,
    Interaction,
    InteractionAction,
    InteractionTargetType,
    Listing,
    ListingKind,
    ListingStatus,
    Message,
    Notification,
    NotificationCategory,
    Offer,
    OfferDirection,
    OfferStatus,
    PendingTransaction,
    PendingTxKind,
    PendingTxStatus,
    Rating,
    RiskLevel,
    RiskProfile,
    Trade,
    User,
    UserRole,
)

NOW = datetime.now(UTC)


async def test_user_roles_and_enum_roundtrip(db, make_user):
    customer, token = await make_user("customer", username="ayse")
    trader, _ = await make_user(UserRole.trader, username="ali", commission_bps=1500)
    assert token.count(".") == 2

    db.expunge_all()
    c = (await db.execute(select(User).where(User.username == "ayse"))).scalar_one()
    t = (await db.execute(select(User).where(User.username == "ali"))).scalar_one()
    assert c.role is UserRole.customer and c.is_customer and not c.is_trader
    assert c.risk_profile is RiskProfile.balanced
    assert c.markets == ["crypto", "stable_fx"]
    assert c.budget_amount == Decimal("1000.0000000")
    assert t.role is UserRole.trader and t.risk_level is RiskLevel.medium and t.commission_bps == 1500
    assert t.rating_avg == Decimal("0.00") and t.managed_capital == Decimal("0") and t.active_agreements == 0
    assert c.is_active and not c.is_admin and c.created_at is not None


async def test_stellar_address_and_username_unique(db, make_user):
    user, _ = await make_user("customer")
    db.add(User(stellar_address=user.stellar_address, role=UserRole.trader, username="other", display_name="x"))
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


async def test_seed_assets_idempotent_and_sac_ids_match(db, settings, seed_assets):
    from scripts.seed_assets import SEED, seed_network

    network = settings.stellar_network
    assert len(seed_assets) == len(SEED[network])
    created, updated = await seed_network(db, network)  # second run: nothing to do
    assert (created, updated) == (0, 0)
    xlm = seed_assets["XLM"]
    assert xlm.is_native and xlm.is_classic and xlm.canonical == "native" and xlm.is_base_allowed

    # classic assets: the SAC id must be the deterministic contract id for (network, code, issuer)
    passphrase = TESTNET_PASSPHRASE if network == "testnet" else PUBLIC_PASSPHRASE
    for asset in seed_assets.values():
        if asset.is_native:
            assert SdkAsset.native().contract_id(passphrase) == asset.contract_id
        elif asset.issuer is not None:
            assert SdkAsset(asset.code, asset.issuer).contract_id(passphrase) == asset.contract_id
        else:  # pure Soroban token (Soroswap test tokens)
            assert not asset.is_classic and asset.canonical == asset.contract_id
    # mainnet rows are checked too so a typo never ships
    for row in SEED["public"]:
        if row["issuer"]:
            assert SdkAsset(row["code"], row["issuer"]).contract_id(PUBLIC_PASSPHRASE) == row["contract_id"]


async def test_full_agreement_lifecycle_rows(db, make_user, seed_assets):
    customer, _ = await make_user("customer")
    trader, _ = await make_user("trader")
    xlm = seed_assets["XLM"]

    listing = Listing(
        owner_id=customer.id,
        kind=ListingKind.capital,
        title="1000 XLM, 30 gün",
        risk_profile=RiskProfile.conservative,
        markets=["crypto"],
        amount=Decimal("1000"),
        base_asset_id=xlm.id,
        duration_days=30,
        max_loss_bps=2000,
    )
    db.add(listing)
    await db.flush()
    assert listing.status is ListingStatus.active and listing.is_capital

    offer = Offer(
        listing_id=listing.id,
        from_user_id=trader.id,
        to_user_id=customer.id,
        direction=OfferDirection.trader_to_customer,
        amount=Decimal("1000"),
        base_asset_id=xlm.id,
        duration_days=30,
        commission_bps=2000,
        max_drawdown_bps=2000,
        expected_return_min_bps=500,
        expected_return_max_bps=1500,
        note="Teklif",
        expires_at=NOW + timedelta(days=3),
    )
    db.add(offer)
    await db.flush()
    assert offer.status is OfferStatus.pending and offer.duration_secs == 30 * 86_400

    listing_ref = hashlib.sha256(offer.id.bytes).hexdigest()
    agreement = Agreement(
        offer_id=offer.id,
        listing_id=listing.id,
        customer_id=customer.id,
        trader_id=trader.id,
        base_asset_id=xlm.id,
        principal=offer.amount,
        duration_secs=offer.duration_secs,
        commission_bps=offer.commission_bps,
        max_drawdown_bps=offer.max_drawdown_bps,
        risk_profile=listing.risk_profile,
        listing_ref=listing_ref,
        proposer_role=UserRole.trader,
    )
    db.add(agreement)
    await db.flush()
    # FK cycle offers.agreement_id <-> agreements.offer_id
    offer.agreement_id = agreement.id
    offer.status = OfferStatus.accepted
    offer.responded_at = NOW
    conv = Conversation(offer_id=offer.id, agreement_id=agreement.id, participant_a=customer.id, participant_b=trader.id)
    db.add(conv)
    await db.flush()
    db.add(Message(conversation_id=conv.id, sender_id=trader.id, body="Merhaba"))

    # indexer: Proposed -> Activated -> Traded -> Settled
    agreement.onchain_id = 1
    agreement.status = AgreementStatus.from_onchain(0)
    assert agreement.status is AgreementStatus.proposed
    agreement.created_tx = "a" * 64
    agreement.status = AgreementStatus.active
    agreement.start_time = NOW
    agreement.end_time = NOW + timedelta(seconds=agreement.duration_secs)
    db.add(AgreementBalance(agreement_id=agreement.id, asset_id=xlm.id, balance=Decimal("1000")))
    db.add(AgreementValueSnapshot(agreement_id=agreement.id, value=Decimal("1000")))
    usdc = seed_assets.get("USDC_SOROSWAP") or seed_assets["USDC"]
    db.add(
        Trade(
            agreement_id=agreement.id,
            onchain_seq=0,
            tx_hash="b" * 64,
            ledger=123_456,
            trader_id=trader.id,
            token_in_id=xlm.id,
            token_out_id=usdc.id,
            amount_in=Decimal("100"),
            amount_out=Decimal("11.5"),
            value_after=Decimal("1001.25"),
            symbol_label="XLM/USDC · Alış",
            note="Momentum girişi",
        )
    )
    db.add(AgreementBalance(agreement_id=agreement.id, asset_id=usdc.id, balance=Decimal("11.5")))
    db.add(
        PendingTransaction(
            user_id=trader.id,
            kind=PendingTxKind.trade,
            agreement_id=agreement.id,
            unsigned_xdr="AAAA",
            tx_hash="b" * 64,
            status=PendingTxStatus.success,
            result={"status": "SUCCESS", "ledger": 123_456},
            payload={"note": "Momentum girişi", "notify_investors": True},
            expires_at=NOW + timedelta(minutes=5),
        )
    )
    db.add(
        Notification(
            user_id=customer.id,
            category=NotificationCategory.agreement,
            type="trade_executed",
            title="Yeni işlem",
            body="XLM/USDC",
            data={"agreement_id": str(agreement.id)},
        )
    )
    db.add(IndexerState(key="vault_events", cursor="0001-0000000001", ledger=123_456))
    await db.flush()

    agreement.status = AgreementStatus.settled
    agreement.settle_tx = "c" * 64
    agreement.final_value = Decimal("1100")
    agreement.profit = Decimal("100")
    agreement.trader_fee = Decimal("20")
    agreement.platform_fee = Decimal("0")
    agreement.customer_payout = Decimal("1080")
    agreement.settled_at = NOW
    agreement.settled_by = customer.stellar_address
    agreement.last_event_ledger = 123_999
    db.add(Rating(agreement_id=agreement.id, customer_id=customer.id, trader_id=trader.id, score=5, comment="Süper"))
    await db.commit()

    db.expunge_all()
    a = (await db.execute(select(Agreement).where(Agreement.onchain_id == 1))).scalar_one()
    assert a.status is AgreementStatus.settled and a.proposer_role is UserRole.trader
    assert a.customer.username == customer.username and a.trader.id == trader.id  # joined loads
    assert a.base_asset.code == "XLM"
    assert {b.asset.code: b.balance for b in a.balances} == {"XLM": Decimal("1000"), "USDC": Decimal("11.5")}
    assert a.customer_payout + a.trader_fee + a.platform_fee == a.final_value
    assert a.party_role(customer.id) is UserRole.customer and a.party_role(uuid.uuid4()) is None
    assert not a.is_open

    o = await db.get(Offer, offer.id)
    assert o.agreement_id == a.id and o.status is OfferStatus.accepted and o.listing.title.startswith("1000")
    t = (await db.execute(select(Trade).where(Trade.agreement_id == a.id))).scalar_one()
    assert t.token_in.code == "XLM" and t.token_out.code == "USDC" and t.notify_investors is True
    p = (await db.execute(select(PendingTransaction).where(PendingTransaction.tx_hash == "b" * 64))).scalar_one()
    assert p.kind is PendingTxKind.trade and p.status is PendingTxStatus.success and p.payload["notify_investors"]
    n = (await db.execute(select(Notification))).scalar_one()
    assert n.category is NotificationCategory.agreement and n.read_at is None
    c = (await db.execute(select(Conversation))).scalar_one()
    assert c.has_participant(trader.id) and c.other_participant(trader.id) == customer.id
    r = (await db.execute(select(Rating))).scalar_one()
    assert r.score == 5
    state = await db.get(IndexerState, "vault_events")
    assert state.ledger == 123_456


async def test_rating_score_check_and_unique_agreement(db, make_user, seed_assets):
    customer, _ = await make_user("customer")
    trader, _ = await make_user("trader")
    agreement = Agreement(
        customer_id=customer.id,
        trader_id=trader.id,
        base_asset_id=seed_assets["XLM"].id,
        principal=Decimal("10"),
        duration_secs=86_400,
        commission_bps=1000,
        max_drawdown_bps=10_000,
        listing_ref="0" * 64,
        proposer_role=UserRole.customer,
    )
    db.add(agreement)
    await db.flush()
    db.add(Rating(agreement_id=agreement.id, customer_id=customer.id, trader_id=trader.id, score=6))
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


async def test_interactions_follows_favorites(db, make_user, seed_assets):
    customer, _ = await make_user("customer")
    trader, _ = await make_user("trader")
    listing = Listing(owner_id=trader.id, kind=ListingKind.service, title="Servis", commission_bps=1000)
    db.add(listing)
    await db.flush()
    db.add(Interaction(user_id=customer.id, target_type=InteractionTargetType.listing, target_id=listing.id, action=InteractionAction.like))
    db.add(Interaction(user_id=customer.id, target_type=InteractionTargetType.user, target_id=trader.id, action=InteractionAction.pass_))
    db.add(Follow(follower_id=customer.id, trader_id=trader.id))
    db.add(Favorite(user_id=customer.id, listing_id=listing.id))
    await db.commit()

    db.expunge_all()
    likes = (await db.execute(select(Interaction).where(Interaction.action == InteractionAction.like))).scalars().all()
    assert len(likes) == 1 and likes[0].action is InteractionAction.like
    passed = (await db.execute(select(Interaction).where(Interaction.action == "pass"))).scalar_one()
    assert passed.action is InteractionAction.pass_ and passed.action.value == "pass"
    f = (await db.execute(select(Follow))).scalar_one()
    assert f.trader.role is UserRole.trader
    fav = (await db.execute(select(Favorite))).scalar_one()
    assert fav.listing.is_service

    # unique(user_id, target_type, target_id, action)
    db.add(Interaction(user_id=customer.id, target_type=InteractionTargetType.listing, target_id=listing.id, action=InteractionAction.like))
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


async def test_anchor_rows_and_auth_nonce(db, make_user):
    user, _ = await make_user("customer")
    db.add(
        AnchorSession(
            user_id=user.id,
            anchor_domain="testanchor.stellar.org",
            account=user.stellar_address,
            jwt=b"\x00" * 12 + b"ciphertext",
            expires_at=NOW + timedelta(hours=1),
        )
    )
    tx = AnchorTransaction(
        user_id=user.id,
        anchor_domain="testanchor.stellar.org",
        anchor_tx_id="abc-123",
        kind=AnchorTxKind.withdraw,
        asset_code="USDC",
        asset_issuer="GBBD47IF6LWK7P7MDEVSCWR7DPUWV3NY3DTQEVFL4NAT4AQH3ZLLFLA5",
        amount_in=Decimal("100"),
        status=AnchorTxStatus.pending_user_transfer_start.value,
        withdraw_anchor_account="GCQHL377VFSG4MLLBEWX33CC3S7T5QAGWOD64A5BH4OOWWD3XWYDKHZH",
        withdraw_memo="12345",
        withdraw_memo_type="id",
        raw={"id": "abc-123", "status": "pending_user_transfer_start"},
    )
    db.add(tx)
    db.add(AuthNonce(public_key=user.stellar_address, nonce=uuid.uuid4().hex, expires_at=NOW + timedelta(minutes=5)))
    await db.commit()

    db.expunge_all()
    s = (await db.execute(select(AnchorSession))).scalar_one()
    assert s.jwt.startswith(b"\x00" * 12) and not s.is_expired(NOW)
    t = (await db.execute(select(AnchorTransaction))).scalar_one()
    assert t.kind is AnchorTxKind.withdraw and not t.is_terminal
    t.status = "completed"
    assert t.is_terminal
    t.status = "some_future_status"
    assert not t.is_terminal
    n = (await db.execute(select(AuthNonce))).scalar_one()
    assert n.used_at is None and n.created_at is not None

    # unique(anchor_domain, anchor_tx_id)
    db.add(AnchorTransaction(user_id=user.id, anchor_domain="testanchor.stellar.org", anchor_tx_id="abc-123", kind=AnchorTxKind.deposit, asset_code="native"))
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


async def test_asset_unique_per_network_contract(db):
    db.add(Asset(network="testnet", contract_id="C" + "A" * 55, code="FOO", name="Foo"))
    db.add(Asset(network="public", contract_id="C" + "A" * 55, code="FOO", name="Foo"))  # other network ok
    await db.flush()
    db.add(Asset(network="testnet", contract_id="C" + "A" * 55, code="BAR", name="Bar"))
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


async def test_client_fixture_routes_requests_to_test_db(client, db, make_user):
    """`get_db` inside the ASGI app must use the test engine installed by conftest."""
    user, _ = await make_user("trader", username="visible")
    r = await client.get("/health")
    assert r.status_code == 200 and r.json()["db"] == "ok"
    r = await client.get("/api/v1/config")
    assert r.status_code == 200 and r.json()["network"] in ("testnet", "public")
    # a session created through the app's factory sees the committed row
    import app.db.session as db_session

    async with db_session.get_session_factory()() as s:
        assert (await s.execute(select(User).where(User.username == "visible"))).scalar_one().id == user.id
