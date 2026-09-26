"""Auth slice — Sign-In with Ethereum (02-api-sozlesme §1): nonce message format, verify (every error code in
order), single-use nonces, rate limit, JWT claims, /auth/me, /auth/refresh (auth_time carry-over, session ceiling),
and the removed SEP-10 endpoints."""
from __future__ import annotations

import re
import time
from datetime import UTC, datetime, timedelta

from eth_account import Account

from app.core.ratelimit import TokenBucket, set_nonce_limiter
from app.core.security import create_access_token, decode_access_token
from app.services.siwe import build_message, parse_message
from tests.conftest import API, sign_siwe, siwe_login, wallet_token

MESSAGE_RE = re.compile(
    r"^(?P<domain>\S+) wants you to sign in with your Ethereum account:\n"
    r"(?P<address>0x[0-9a-fA-F]{40})\n\n"
    r"(?P<statement>[^\n]+)\n\n"
    r"URI: (?P<uri>\S+)\nVersion: 1\nChain ID: (?P<chain>\d+)\nNonce: (?P<nonce>[A-Za-z0-9]{8,})\n"
    r"Issued At: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\n"
    r"Expiration Time: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"
)


def _message(settings, address: str, nonce: str, *, expires_in: int = 300, **overrides) -> str:  # noqa: ANN001
    now = datetime.now(UTC)
    kw = dict(
        domain=settings.siwe_domain,
        address=address,
        statement=settings.siwe_statement,
        uri=settings.siwe_uri,
        chain_id=settings.chain_id,
        nonce=nonce,
        issued_at=now,
        expiration_time=now + timedelta(seconds=expires_in),
    )
    kw.update(overrides)
    return build_message(**kw)


# --- nonce + verify -------------------------------------------------------------------------------------------


async def test_nonce_message_format_and_login_unregistered_wallet(client, settings):
    account = Account.create()
    r = await client.post(f"{API}/auth/nonce", json={"address": account.address.lower()})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["chain_id"] == settings.chain_id and body["domain"] == settings.siwe_domain
    assert len(body["nonce"]) == 32 and re.fullmatch(r"[0-9a-f]{32}", body["nonce"])
    m = MESSAGE_RE.match(body["message"])
    assert m, body["message"]
    assert m["domain"] == settings.siwe_domain and m["uri"] == settings.siwe_uri
    assert m["address"] == account.address  # checksum form in the message (EIP-4361)
    assert m["statement"] == settings.siwe_statement and int(m["chain"]) == settings.chain_id and m["nonce"] == body["nonce"]
    fields = parse_message(body["message"])
    assert fields.expiration_time is not None
    assert abs((fields.expiration_time - datetime.fromisoformat(body["expires_at"])).total_seconds()) < 1
    assert timedelta(seconds=290) < fields.expiration_time - fields.issued_at <= timedelta(seconds=300)

    login = await siwe_login(client, account)
    assert login["registered"] is False and login["user"] is None
    assert login["address"] == account.address  # checksum output
    claims = decode_access_token(settings, login["token"])
    assert claims.address == account.address.lower() and claims.user_id is None and claims.role is None
    assert abs(claims.auth_time - int(time.time())) < 5

    # nonce is single-use
    sig = sign_siwe(account, body["message"])
    r = await client.post(f"{API}/auth/verify", json={"message": body["message"], "signature": sig})
    assert r.status_code == 401 and r.json()["code"] == "nonce_used"


async def test_login_registered_wallet_and_wrong_signer(client, settings, make_user):
    user, _ = await make_user("trader", username="ali_trader")
    account = user._account
    r = await client.post(f"{API}/auth/nonce", json={"address": account.address})
    nonce = r.json()

    other = Account.create()
    r = await client.post(f"{API}/auth/verify", json={"message": nonce["message"], "signature": sign_siwe(other, nonce["message"])})
    assert r.status_code == 401 and r.json()["code"] == "signature_invalid"

    r = await client.post(f"{API}/auth/verify", json={"message": nonce["message"], "signature": sign_siwe(account, nonce["message"])})
    assert r.status_code == 200, r.text
    login = r.json()
    assert login["registered"] is True and login["user"]["username"] == "ali_trader" and login["user"]["role"] == "trader"
    assert login["user"]["wallet_address"] == account.address and "expo_push_token" in login["user"]  # MeOut
    claims = decode_access_token(settings, login["token"])
    assert claims.user_id == user.id and claims.role == "trader"


async def test_verify_error_codes_in_contract_order(client, settings):
    account = Account.create()
    nonce = (await client.post(f"{API}/auth/nonce", json={"address": account.address})).json()["nonce"]

    async def verify(message: str, signer=account):  # noqa: ANN001
        return await client.post(f"{API}/auth/verify", json={"message": message, "signature": sign_siwe(signer, message)})

    r = await verify("not a siwe message\nat all")
    assert r.status_code == 401 and r.json()["code"] == "siwe_invalid"
    r = await verify(_message(settings, account.address, nonce, domain="evil.example"))
    assert r.json()["code"] == "siwe_domain_mismatch"
    r = await verify(_message(settings, account.address, nonce, uri="https://evil.example"))
    assert r.json()["code"] == "siwe_uri_mismatch"
    r = await verify(_message(settings, account.address, nonce, chain_id=1))
    assert r.json()["code"] == "siwe_chain_mismatch"
    r = await verify(_message(settings, account.address, nonce, expires_in=-5))
    assert r.json()["code"] == "siwe_expired"
    future = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    r = await verify(_message(settings, account.address, nonce) + f"\nNot Before: {future}")
    assert r.json()["code"] == "siwe_not_yet_valid"
    r = await verify(_message(settings, account.address, "deadbeefdeadbeef"))
    assert r.json()["code"] == "nonce_invalid"
    other = Account.create()
    r = await verify(_message(settings, other.address, nonce), signer=other)  # nonce issued for another address
    assert r.json()["code"] == "nonce_invalid"
    r = await verify(_message(settings, account.address, nonce, statement="tampered statement"))
    assert r.json()["code"] == "siwe_invalid"  # differs from the issued text
    r = await client.post(f"{API}/auth/verify", json={"message": "x", "signature": "0x1234"})
    assert r.status_code == 422  # signature pattern
    # the original nonce survived every failed attempt and still logs in
    r = await verify(_message(settings, account.address, nonce))
    assert r.status_code == 401 and r.json()["code"] == "siwe_invalid"  # rebuilt text != issued text (timestamps)
    issued = (await client.post(f"{API}/auth/nonce", json={"address": account.address})).json()
    r = await verify(issued["message"])
    assert r.status_code == 200, r.text


async def test_nonce_rejects_invalid_address_and_broken_checksum(client):
    r = await client.post(f"{API}/auth/nonce", json={"address": "GNOTANADDRESS"})
    assert r.status_code == 422
    good = Account.create().address
    broken = good[:-1] + ("a" if good[-1] != "a" else "b")  # mixed case with a wrong checksum
    if broken != broken.lower() and broken != broken.upper():
        r = await client.post(f"{API}/auth/nonce", json={"address": broken})
        assert r.status_code == 422 and r.json()["code"] in ("invalid_address", "validation_error")


async def test_nonce_rate_limit(client):
    set_nonce_limiter(TokenBucket(rate_per_minute=1, burst=2))
    address = Account.create().address
    assert (await client.post(f"{API}/auth/nonce", json={"address": address})).status_code == 200
    assert (await client.post(f"{API}/auth/nonce", json={"address": address})).status_code == 200
    r = await client.post(f"{API}/auth/nonce", json={"address": address})
    assert r.status_code == 429 and r.json()["code"] == "rate_limited"
    assert r.json()["details"]["retry_after_seconds"] >= 1


async def test_disabled_account_cannot_login(client, make_user):
    user, _ = await make_user("customer", is_active=False)
    r = await client.post(f"{API}/auth/nonce", json={"address": user._account.address})
    nonce = r.json()
    r = await client.post(f"{API}/auth/verify", json={"message": nonce["message"], "signature": sign_siwe(user._account, nonce["message"])})
    assert r.status_code == 403 and r.json()["code"] == "account_disabled"


# --- me / refresh -----------------------------------------------------------------------------------------------


async def test_me_and_refresh_carry_auth_time(client, settings, make_user, auth_headers):
    user, token = await make_user("trader", username="mehmet")
    r = await client.get(f"{API}/auth/me", headers=auth_headers(token))
    assert r.status_code == 200, r.text
    assert r.json()["registered"] is True and r.json()["user"]["id"] == str(user.id)
    assert r.json()["address"] == user._account.address and r.json()["token_expires_at"]

    old = decode_access_token(settings, token)
    r = await client.post(f"{API}/auth/refresh", headers=auth_headers(token))
    assert r.status_code == 200, r.text
    new = decode_access_token(settings, r.json()["token"])
    assert new.user_id == user.id and new.auth_time == old.auth_time and new.jti != old.jti
    assert r.json()["user"]["username"] == "mehmet"

    # a wallet token without a profile
    account, bare = wallet_token(settings)
    r = await client.get(f"{API}/auth/me", headers=auth_headers(bare))
    assert r.status_code == 200 and r.json()["registered"] is False and r.json()["user"] is None
    assert r.json()["address"] == account.address

    r = await client.get(f"{API}/auth/me")
    assert r.status_code == 401 and r.json()["code"] == "missing_token"
    r = await client.get(f"{API}/auth/me", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401 and r.json()["code"] == "token_invalid"


async def test_refresh_session_expired_and_expired_token(client, settings, make_user, auth_headers, monkeypatch):
    user, _ = await make_user("customer")
    old_token = create_access_token(
        settings, address=user.wallet_address, user_id=user.id, role="customer", auth_time=int(time.time()) - 40 * 86_400
    )
    r = await client.post(f"{API}/auth/refresh", headers=auth_headers(old_token))
    assert r.status_code == 401 and r.json()["code"] == "session_expired"

    monkeypatch.setattr(settings, "jwt_absolute_ttl_days", 0)
    recent = create_access_token(settings, address=user.wallet_address, user_id=user.id, role="customer", auth_time=int(time.time()) - 5)
    r = await client.post(f"{API}/auth/refresh", headers=auth_headers(recent))
    assert r.status_code == 401 and r.json()["code"] == "session_expired"

    monkeypatch.setattr(settings, "access_token_ttl_seconds", -10)
    expired = create_access_token(settings, address=user.wallet_address, user_id=user.id, role="customer")
    r = await client.post(f"{API}/auth/refresh", headers=auth_headers(expired))
    assert r.status_code == 401 and r.json()["code"] == "token_expired"


async def test_removed_sep10_endpoints_are_gone(client):
    assert (await client.get(f"{API}/auth/sep10", params={"account": "x"})).status_code == 404
    assert (await client.post(f"{API}/auth/sep10", json={"transaction": "x"})).status_code == 404
