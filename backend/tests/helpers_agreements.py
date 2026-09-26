"""Shared helpers for the agreements / trading / indexer / reservation tests (not collected by pytest).

* row factories (`make_agreement`);
* the wallet stand-in: `wallet_send(chain, built)` sends the `pre_steps` then the main transaction of an
  `UnsignedTxOut` body on the fake chain and returns the tx hash; `api_submit` reports it to `POST /tx/submit`;
  `api_action` builds + sends + submits one lifecycle action;
* `chain_open_and_accept(chain, ag, customer, trader)`: customer `open` + trader `accept` straight on the fake
  vault (no API), returns the on-chain id; `fund(chain, user, asset, amount)` credits a wallet.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Agreement, AgreementStatus, Asset, User, UserRole
from app.services.agreements import build_terms, compute_listing_ref
from app.services.chain.amounts import to_raw
from app.services.chain.fake import FakeChainGateway
from app.services.chain.types import TxReceiptResult, UnsignedTx

API = "/api/v1"


# --- rows -------------------------------------------------------------------------------------------


async def make_agreement(
    db: AsyncSession,
    customer: User,
    trader: User,
    base_asset: Asset,
    *,
    principal: Decimal | str = Decimal("1000"),
    duration_days: int = 7,
    commission_bps: int = 2000,
    max_drawdown_bps: int = 1000,
    proposer_role: UserRole = UserRole.customer,
    status: AgreementStatus = AgreementStatus.draft,
    **kw: Any,
) -> Agreement:
    """A draft (or given-status) agreement row like the offers slice creates on accept. Committed."""
    ag_id = uuid.uuid4()
    ag = Agreement(
        id=ag_id,
        customer_id=customer.id,
        trader_id=trader.id,
        base_asset_id=base_asset.id,
        principal=Decimal(str(principal)),
        duration_secs=duration_days * 86_400,
        commission_bps=commission_bps,
        max_drawdown_bps=max_drawdown_bps,
        listing_ref=compute_listing_ref(ag_id),
        status=status,
        proposer_role=proposer_role,
        **kw,
    )
    db.add(ag)
    await db.commit()
    await db.refresh(ag)
    return ag


# --- wallets ------------------------------------------------------------------------------------------


def fund(chain: FakeChainGateway, user: User | str, asset: Asset | str, amount: Decimal | str | int, decimals: int | None = None) -> int:
    """Credit `amount` (human units) of `asset` to the user's wallet on the fake chain; returns the raw amount."""
    address = user if isinstance(user, str) else user.wallet_address
    token = asset if isinstance(asset, str) else asset.address
    dec = decimals if decimals is not None else (asset.decimals if isinstance(asset, Asset) else 18)
    raw = to_raw(Decimal(str(amount)), dec)
    chain.fund_wallet(address, token, raw)
    return raw


# --- sending / submitting -----------------------------------------------------------------------------


def wallet_send(chain: FakeChainGateway, built: dict[str, Any], sender: str | None = None) -> str:
    """The wallet: send every `pre_steps` entry (approve) then the main transaction of an `UnsignedTxOut` body."""
    frm = sender or built["from_address"]
    for step in built.get("pre_steps") or []:
        chain.send(frm, step["to"], step["data"], int(step.get("value") or 0))
    return chain.send(frm, built["to"], built["data"], int(built.get("value") or 0))


async def api_submit(client: AsyncClient, headers: dict[str, str], chain: FakeChainGateway, built: dict[str, Any]) -> dict[str, Any]:
    """Send what `POST .../tx/{action}` returned on the fake chain and `POST /tx/submit` the hash (asserts HTTP 200)."""
    tx_hash = wallet_send(chain, built)
    r = await client.post(f"{API}/tx/submit", json={"pending_tx_id": built["pending_tx_id"], "tx_hash": tx_hash}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def api_action(
    client: AsyncClient,
    headers: dict[str, str],
    chain: FakeChainGateway,
    agreement_id: uuid.UUID | str,
    action: str,
    body: dict | None = None,
) -> dict[str, Any]:
    """Build + send + submit one lifecycle action through the API; returns the `TxStatusOut` body."""
    r = await client.post(f"{API}/agreements/{agreement_id}/tx/{action}", json=body, headers=headers)
    assert r.status_code == 200, r.text
    return await api_submit(client, headers, chain, r.json())


def chain_submit(chain: FakeChainGateway, unsigned: UnsignedTx) -> TxReceiptResult:
    """Send an `UnsignedTx` (pre-steps included) directly on the fake and return its receipt (asserts success)."""
    tx_hash = chain.wallet_send(unsigned)
    receipt = chain.receipts[tx_hash]
    assert receipt.ok, receipt.revert
    return receipt


async def chain_open_and_accept(chain: FakeChainGateway, ag: Agreement, customer: User, trader: User) -> int:
    """Customer `open` (wallet funded + approve pre-step) then trader `accept` on the fake vault; on-chain id."""
    fund(chain, customer, ag.base_asset, ag.principal)
    receipt = chain_submit(chain, await chain.build_open(customer.wallet_address, build_terms(ag)))
    onchain_id = int(receipt.events[0].args["id"])
    chain_submit(chain, await chain.build_accept(trader.wallet_address, onchain_id))
    return onchain_id


def mine_confirmations(chain: FakeChainGateway, confirmations: int) -> None:
    """Mine enough empty blocks for the last transaction to be `confirmations` deep (indexer safe head)."""
    for _ in range(max(0, int(confirmations))):
        chain.mine()
