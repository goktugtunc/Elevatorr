"""FakeChainGateway — the Solidity state machine (01-kontrat-spec §4) as tests see it (03 §8.3)."""
from __future__ import annotations

from dataclasses import replace

import pytest

from app.core.errors import ChainError
from app.services.chain import abi, get_chain
from app.services.chain.abi import KEEPER_GRACE, VaultError
from app.services.chain.amounts import drawdown_floor, min_out_for_slippage, settle_math
from app.services.chain.errors import ContractRevertError
from app.services.chain.fake import (
    FAKE_FEE_RECIPIENT,
    FAKE_OWNER,
    FAKE_ROUTER,
    FAKE_TUSDC,
    FAKE_TWBTC,
    FAKE_TWETH,
    FAKE_VAULT,
    GAS_TABLE,
    FakeChainGateway,
)
from app.services.chain.gateway import ChainGateway
from app.services.chain.types import (
    RES_CONSUMED,
    RES_OPEN,
    RES_RELEASED,
    STATUS_ACTIVE,
    STATUS_CANCELLED,
    STATUS_FUNDED,
    STATUS_PROPOSED,
    STATUS_SETTLED,
    Terms,
    UnsignedTx,
)

USDC = 10**6
PRINCIPAL = 1_000 * USDC


def raises_vault(err: VaultError):
    return pytest.raises(ContractRevertError, match=f"vault:{err.name}|{err.name}")


def assert_revert(exc_info, err: VaultError) -> None:
    e = exc_info.value
    assert e.error is err, (e.error, e.revert)
    assert e.error_code == f"vault:{err.name}"
    assert e.details["contract_error_code"] == int(err)


async def open_and_accept(chain: FakeChainGateway, terms: Terms, *, fund: bool = True) -> int:
    if fund:
        chain.fund_wallet(terms.customer, terms.base_token, terms.principal)
    tx = await chain.build_open(terms.customer, terms)
    rc = await chain.get_receipt(chain.wallet_send(tx))
    assert rc is not None and rc.ok, rc.revert
    ag_id = rc.events[0].args["id"]
    rc = chain.mine(await chain.build_accept(terms.trader, ag_id))
    assert rc.ok
    return ag_id


# --- basics -------------------------------------------------------------------------------------------------


async def test_fake_satisfies_protocol_and_defaults(chain: FakeChainGateway):
    assert isinstance(chain, ChainGateway)
    assert get_chain() is chain
    assert chain.kind == "fake" and await chain.chain_id() == 10143
    assert chain.vault_address == FAKE_VAULT and chain.router_address == FAKE_ROUTER
    cfg = await chain.read_config()
    assert cfg.owner == FAKE_OWNER and cfg.router == FAKE_ROUTER and cfg.fee_recipient == FAKE_FEE_RECIPIENT
    assert (cfg.platform_fee_bps, cfg.settle_slippage_bps, cfg.paused, cfg.pending_router) == (100, 100, False, None)
    assert (await chain.is_token_allowed(FAKE_TUSDC)).is_base is True
    assert (await chain.is_token_allowed(FAKE_TWETH)).allowed is True
    assert (await chain.is_token_allowed(FAKE_TWETH)).is_base is False
    assert await chain.token_metadata(FAKE_TWBTC) == ("tWBTC", 8)
    assert await chain.next_id() == 1 and await chain.next_reservation_id() == 1
    block = await chain.get_block("latest")
    assert block.number == await chain.latest_block() == 1000
    assert (await chain.get_block(1000)).hash == block.hash
    with pytest.raises(ChainError):
        await chain.get_block(5)


async def test_quotes_follow_spec_prices(chain: FakeChainGateway):
    q = await chain.quote([FAKE_TUSDC, FAKE_TWETH], 3_000 * USDC)
    assert q.amount_out == 10**18 and q.source == "fake" and q.amounts[0] == 3_000 * USDC
    assert (await chain.quote([FAKE_TWETH, FAKE_TUSDC], 10**18)).amount_out == 3_000 * USDC
    assert (await chain.quote([FAKE_TWBTC, FAKE_TUSDC], 10**8)).amount_out == 60_000 * USDC
    assert (await chain.quote([FAKE_TWBTC, FAKE_TWETH], 10**8)).amount_out == 20 * 10**18
    multi = await chain.quote([FAKE_TUSDC, FAKE_TWETH, FAKE_TWBTC], 60_000 * USDC)
    assert multi.amounts == [60_000 * USDC, 20 * 10**18, 10**8]
    chain.remove_route(FAKE_TWBTC, FAKE_TUSDC)
    with pytest.raises(ChainError) as ei:
        await chain.quote([FAKE_TWBTC, FAKE_TUSDC], 10**8)
    assert ei.value.code == "quote_failed" and ei.value.details["error_code"] == "router:NoPrice"
    with pytest.raises(ChainError):
        await chain.quote([FAKE_TUSDC, FAKE_TWETH], 0)


# --- open / approve requirement --------------------------------------------------------------------------------


async def test_build_open_adds_approve_pre_step_and_real_calldata(chain: FakeChainGateway, terms: Terms, accounts):
    chain.fund_wallet(accounts.customer, FAKE_TUSDC, PRINCIPAL)
    tx = await chain.build_open(accounts.customer, terms)
    assert isinstance(tx, UnsignedTx)
    assert tx.kind == "open" and tx.action == "open" and tx.to == FAKE_VAULT and tx.chain_id == 10143
    assert tx.from_address == accounts.customer and tx.value == 0 and tx.gas == GAS_TABLE["open"]
    assert abi.decode_call("TraderVault", tx.data)[0] == "open"
    assert tx.summary["gas_estimated"] is False and tx.summary["terms"]["principal"] == PRINCIPAL
    assert len(tx.pre_steps) == 1
    step = tx.pre_steps[0]
    assert step.kind == "approve" and step.to == FAKE_TUSDC and step.spender == FAKE_VAULT and step.amount_raw == PRINCIPAL
    assert abi.decode_call("TestToken", step.data) == ("approve", {"spender": FAKE_VAULT, "value": PRINCIPAL})
    # once approved, no pre-step and the dry run counts as an estimate
    chain.approve(accounts.customer, FAKE_TUSDC, FAKE_VAULT, PRINCIPAL)
    tx2 = await chain.build_open(accounts.customer, terms)
    assert tx2.pre_steps == [] and tx2.summary["gas_estimated"] is True


async def test_open_without_approve_reverts_with_transfer_failed(chain: FakeChainGateway, terms: Terms, accounts):
    chain.fund_wallet(accounts.customer, FAKE_TUSDC, PRINCIPAL)
    tx = await chain.build_open(accounts.customer, terms)
    tx_hash = chain.send(accounts.customer, tx.to, tx.data)  # main tx only, approve skipped
    rc = await chain.get_receipt(tx_hash)
    assert rc is not None and rc.status == 0 and rc.events == []
    assert rc.revert is not None and rc.revert.code == VaultError.TransferFailed and rc.revert.name == "TransferFailed"
    assert (await chain.explain_failure(rc)).code == 29
    assert await chain.next_id() == 1  # revert consumes no id
    # insufficient wallet balance surfaces as the ERC-20 error at build time
    chain.approve(accounts.customer, FAKE_TUSDC, FAKE_VAULT, 10 * PRINCIPAL)
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_open(accounts.customer, replace(terms, principal=5 * PRINCIPAL))
    assert ei.value.error_code == "erc20:ERC20InsufficientBalance"


async def test_open_mines_opened_event_and_escrows_funds(chain: FakeChainGateway, terms: Terms, accounts):
    chain.fund_wallet(accounts.customer, FAKE_TUSDC, PRINCIPAL)
    tx = await chain.build_open(accounts.customer, terms)
    tx_hash = chain.wallet_send(tx)
    rc = await chain.get_receipt(tx_hash)
    assert rc is not None and rc.ok and rc.tx_hash == tx_hash
    assert rc.from_address == accounts.customer and rc.to_address == FAKE_VAULT and rc.input == tx.data
    assert [e.name for e in rc.events] == ["Opened"] and rc.raw_log_count == 2  # + ERC-20 Transfer
    ev = rc.events[0]
    assert ev.args == {"id": 1, "trader": accounts.trader, "customer": accounts.customer, "principal": PRINCIPAL,
                       "baseToken": FAKE_TUSDC}
    assert ev.block_hash == rc.block_hash and ev.block_timestamp == rc.block_timestamp and ev.tx_hash == tx_hash
    ag = await chain.read_agreement(1)
    assert ag.status == STATUS_FUNDED and ag.proposer == accounts.customer and ag.tokens == [FAKE_TUSDC]
    assert ag.last_value == PRINCIPAL and ag.platform_fee_bps == 100 and ag.start_time == 0
    assert await chain.read_balances(1) == [(FAKE_TUSDC, PRINCIPAL)]
    assert await chain.token_balance(FAKE_TUSDC, accounts.customer) == 0
    assert await chain.token_balance(FAKE_TUSDC, FAKE_VAULT) == PRINCIPAL
    assert await chain.allowance(FAKE_TUSDC, accounts.customer, FAKE_VAULT) == 0
    with pytest.raises(ContractRevertError) as ei:
        await chain.read_agreement(2)
    assert_revert(ei, VaultError.NotFound)


async def test_open_rule_order_matches_solidity(chain: FakeChainGateway, terms: Terms, accounts):
    chain.fund_wallet(accounts.customer, FAKE_TUSDC, PRINCIPAL)
    chain.approve(accounts.customer, FAKE_TUSDC, FAKE_VAULT, PRINCIPAL)
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_open(accounts.other, terms)  # sender != terms.customer
    assert_revert(ei, VaultError.Unauthorized)
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_open(accounts.customer, replace(terms, base_token=FAKE_TWETH))
    assert_revert(ei, VaultError.TokenNotAllowed)
    chain.set_paused(True)
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_open(accounts.other, terms)  # Paused checked before Unauthorized
    assert_revert(ei, VaultError.Paused)
    chain.set_paused(False)
    # off-chain Terms.validate() rejects the obvious range errors before calldata is built (422)
    from app.core.errors import ValidationError

    with pytest.raises(ValidationError):
        await chain.build_open(accounts.customer, replace(terms, duration_seconds=10))


# --- propose / fund / accept / cancel -----------------------------------------------------------------------------


async def test_propose_fund_activates(chain: FakeChainGateway, terms: Terms, accounts):
    rc = chain.mine(await chain.build_propose(accounts.trader, terms))
    assert rc.ok and rc.events[0].name == "Proposed" and rc.events[0].args["trader"] == accounts.trader
    ag = await chain.read_agreement(1)
    assert ag.status == STATUS_PROPOSED and ag.proposer == accounts.trader
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_propose(accounts.customer, terms)
    assert_revert(ei, VaultError.Unauthorized)
    # fund needs approve (pre-step) and the customer
    chain.fund_wallet(accounts.customer, FAKE_TUSDC, PRINCIPAL)
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_fund(accounts.trader, 1)
    assert_revert(ei, VaultError.Unauthorized)
    tx = await chain.build_fund(accounts.customer, 1)
    assert tx.kind == "fund" and [p.kind for p in tx.pre_steps] == ["approve"]
    assert tx.summary["principal"] == PRINCIPAL and tx.summary["token"] == FAKE_TUSDC
    rc = await chain.get_receipt(chain.wallet_send(tx))
    assert rc.ok and [e.name for e in rc.events] == ["Activated"]
    ag = await chain.read_agreement(1)
    assert ag.status == STATUS_ACTIVE and ag.end_time == ag.start_time + terms.duration_seconds
    assert rc.events[0].args == {"id": 1, "startTime": ag.start_time, "endTime": ag.end_time}
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_fund(accounts.customer, 1)
    assert_revert(ei, VaultError.WrongStatus)
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_fund(accounts.customer, 99)
    assert_revert(ei, VaultError.NotFound)


async def test_accept_rules(chain: FakeChainGateway, terms: Terms, accounts):
    chain.fund_wallet(accounts.customer, FAKE_TUSDC, PRINCIPAL)
    chain.wallet_send(await chain.build_open(accounts.customer, terms))
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_accept(accounts.customer, 1)
    assert_revert(ei, VaultError.Unauthorized)
    chain.set_paused(True)
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_accept(accounts.trader, 1)
    assert_revert(ei, VaultError.Paused)
    chain.set_paused(False)
    rc = chain.mine(await chain.build_accept(accounts.trader, 1))
    assert rc.ok and (await chain.read_agreement(1)).status == STATUS_ACTIVE
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_accept(accounts.trader, 1)
    assert_revert(ei, VaultError.WrongStatus)


async def test_cancel_paths(chain: FakeChainGateway, terms: Terms, accounts):
    # Proposed: only the proposer
    chain.mine(await chain.build_propose(accounts.trader, terms))
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_cancel(accounts.customer, 1)
    assert_revert(ei, VaultError.NotParty)
    rc = chain.mine(await chain.build_cancel(accounts.trader, 1))
    assert rc.ok and rc.events[0].name == "Cancelled" and rc.events[0].args == {"id": 1, "refunded": 0}
    assert (await chain.read_agreement(1)).status == STATUS_CANCELLED
    # Funded: either party, refund goes to the customer's wallet
    chain.fund_wallet(accounts.customer, FAKE_TUSDC, PRINCIPAL)
    chain.wallet_send(await chain.build_open(accounts.customer, terms))
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_cancel(accounts.other, 2)
    assert_revert(ei, VaultError.NotParty)
    rc = chain.mine(await chain.build_cancel(accounts.trader, 2))
    assert rc.ok and rc.events[0].args == {"id": 2, "refunded": PRINCIPAL}
    assert await chain.token_balance(FAKE_TUSDC, accounts.customer) == PRINCIPAL
    assert await chain.read_balances(2) == [(FAKE_TUSDC, 0)]
    # Active / Cancelled: WrongStatus
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_cancel(accounts.trader, 2)
    assert_revert(ei, VaultError.WrongStatus)
    ag_id = await open_and_accept(chain, terms)
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_cancel(accounts.customer, ag_id)
    assert_revert(ei, VaultError.WrongStatus)


# --- trade -----------------------------------------------------------------------------------------------------


async def test_trade_swaps_and_emits_traded(chain: FakeChainGateway, terms: Terms, accounts):
    ag_id = await open_and_accept(chain, terms)
    q = await chain.quote([FAKE_TUSDC, FAKE_TWETH], 300 * USDC)
    deadline = chain.now + 600
    tx = await chain.build_trade(accounts.trader, ag_id, FAKE_TUSDC, FAKE_TWETH, 300 * USDC, q.amount_out, deadline)
    assert tx.kind == "trade" and tx.summary["min_out"] == q.amount_out
    rc = chain.mine(tx)
    assert rc.ok and [e.name for e in rc.events] == ["Traded"]
    assert rc.raw_log_count == 3  # two ERC-20 transfers + Traded
    args = rc.events[0].args
    assert args["amountIn"] == 300 * USDC and args["amountOut"] == 10**17 and args["tokenOut"] == FAKE_TWETH
    assert args["valueAfter"] == PRINCIPAL  # 700 USDC + 0.1 WETH × 3000
    ag = await chain.read_agreement(ag_id)
    assert ag.tokens == [FAKE_TUSDC, FAKE_TWETH] and ag.last_value == PRINCIPAL
    assert await chain.read_balances(ag_id) == [(FAKE_TUSDC, 700 * USDC), (FAKE_TWETH, 10**17)]
    assert await chain.read_value_in_base(ag_id) == PRINCIPAL
    # selling the whole non-base leg removes it from the token list (order preserving)
    q2 = await chain.quote([FAKE_TWETH, FAKE_TUSDC], 10**17)
    rc = chain.mine(await chain.build_trade(accounts.trader, ag_id, FAKE_TWETH, FAKE_TUSDC, 10**17, q2.amount_out, deadline))
    assert rc.ok and (await chain.read_agreement(ag_id)).tokens == [FAKE_TUSDC]
    assert await chain.read_balances(ag_id) == [(FAKE_TUSDC, PRINCIPAL)]


async def test_trade_rule_order(chain: FakeChainGateway, terms: Terms, accounts):
    ag_id = await open_and_accept(chain, terms)
    deadline = chain.now + 600
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_trade(accounts.trader, 99, FAKE_TUSDC, FAKE_TWETH, 1, 1, deadline)
    assert_revert(ei, VaultError.NotFound)
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_trade(accounts.customer, ag_id, FAKE_TUSDC, FAKE_TWETH, 1, 1, deadline)
    assert_revert(ei, VaultError.Unauthorized)
    chain.set_paused(True)
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_trade(accounts.trader, ag_id, FAKE_TUSDC, FAKE_TWETH, 1, 1, deadline)
    assert_revert(ei, VaultError.Paused)
    chain.set_paused(False)
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_trade(accounts.trader, ag_id, FAKE_TUSDC, FAKE_TWETH, 1, 1, chain.now - 1)
    assert_revert(ei, VaultError.Expired)
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_trade(accounts.trader, ag_id, FAKE_TUSDC, FAKE_TWETH, 0, 1, deadline)
    assert_revert(ei, VaultError.ZeroAmount)
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_trade(accounts.trader, ag_id, FAKE_TUSDC, FAKE_TWETH, 1, 0, deadline)
    assert_revert(ei, VaultError.ZeroAmount)
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_trade(accounts.trader, ag_id, FAKE_TUSDC, FAKE_TUSDC, 1, 1, deadline)
    assert_revert(ei, VaultError.TokenNotAllowed)
    unknown = "0x" + "dd" * 20
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_trade(accounts.trader, ag_id, FAKE_TUSDC, unknown, 1, 1, deadline)
    assert_revert(ei, VaultError.TokenNotAllowed)
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_trade(accounts.trader, ag_id, FAKE_TUSDC, FAKE_TWETH, PRINCIPAL + 1, 1, deadline)
    assert_revert(ei, VaultError.InsufficientBalance)
    # min_out above the router price: strict router -> RouterError(InsufficientOutput)
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_trade(accounts.trader, ag_id, FAKE_TUSDC, FAKE_TWETH, 300 * USDC, 10**18, deadline)
    assert_revert(ei, VaultError.RouterError)
    assert ei.value.revert.args["inner"] == "router:InsufficientOutput"
    # lenient router -> the vault's own re-check
    chain.set_strict_router(False)
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_trade(accounts.trader, ag_id, FAKE_TUSDC, FAKE_TWETH, 300 * USDC, 10**18, deadline)
    assert_revert(ei, VaultError.SlippageExceeded)
    chain.set_strict_router(True)
    # no route at all
    chain.remove_route(FAKE_TUSDC, FAKE_TWBTC)
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_trade(accounts.trader, ag_id, FAKE_TUSDC, FAKE_TWBTC, 300 * USDC, 1, deadline)
    assert ei.value.error is VaultError.RouterError and ei.value.revert.args["inner"] == "router:NoPrice"
    # after end_time: Expired
    chain.advance(terms.duration_seconds)
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_trade(accounts.trader, ag_id, FAKE_TUSDC, FAKE_TWETH, 1, 1, chain.now + 600)
    assert_revert(ei, VaultError.Expired)


async def test_trade_drawdown_and_route_back_and_too_many_tokens(chain: FakeChainGateway, terms: Terms, accounts):
    ag_id = await open_and_accept(chain, terms)
    deadline = chain.now + 600
    # buy 0.3 WETH (900 USDC), then WETH crashes 30%: value 100 + 0.3×2100 = 730 < 800 floor
    q = await chain.quote([FAKE_TUSDC, FAKE_TWETH], 900 * USDC)
    chain.mine(await chain.build_trade(accounts.trader, ag_id, FAKE_TUSDC, FAKE_TWETH, 900 * USDC, q.amount_out, deadline))
    chain.set_price(FAKE_TWETH, FAKE_TUSDC, 2_100)
    assert await chain.read_value_in_base(ag_id) == 730 * USDC
    q = await chain.quote([FAKE_TUSDC, FAKE_TWETH], 50 * USDC)
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_trade(accounts.trader, ag_id, FAKE_TUSDC, FAKE_TWETH, 50 * USDC, q.amount_out, deadline)
    assert_revert(ei, VaultError.DrawdownBreached)
    # a token with no route back to base cannot be parked (TokenNotAllowed after the swap)
    chain.set_price(FAKE_TWETH, FAKE_TUSDC, 3_000)
    chain.register_token("0x" + "ee" * 20, "tX", 18, allowed=True)
    chain.set_raw_price(FAKE_TUSDC, "0x" + "ee" * 20, 10**12, 1)  # forward only
    chain.fund_wallet(FAKE_ROUTER, "0x" + "ee" * 20, 10**30)
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_trade(accounts.trader, ag_id, FAKE_TUSDC, "0x" + "ee" * 20, 10 * USDC, 1, deadline)
    assert_revert(ei, VaultError.TokenNotAllowed)
    # MAX_TOKENS: base + 5 legs is the cap; the sixth non-base token is refused (counted before tokenIn removal)
    extra = []
    for i in range(5):
        t = "0x" + f"{0xa0 + i:02x}" * 20
        extra.append(t)
        chain.register_token(t, f"T{i}", 18, allowed=True)
        chain.set_price(t, FAKE_TUSDC, 1)
        chain.fund_wallet(FAKE_ROUTER, t, 10**30)
    for t in extra[:4]:
        rc = chain.mine(await chain.build_trade(accounts.trader, ag_id, FAKE_TUSDC, t, 5 * USDC, 1, deadline))
        assert rc.ok, rc.revert
    assert len((await chain.read_agreement(ag_id)).tokens) == 6
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_trade(accounts.trader, ag_id, FAKE_TUSDC, extra[4], 5 * USDC, 1, deadline)
    assert_revert(ei, VaultError.TooManyTokens)


# --- settle --------------------------------------------------------------------------------------------------------


async def test_settle_profit_splits_fees_with_snapshot_bps(chain: FakeChainGateway, terms: Terms, accounts):
    ag_id = await open_and_accept(chain, terms)
    q = await chain.quote([FAKE_TUSDC, FAKE_TWETH], 600 * USDC)
    chain.mine(await chain.build_trade(accounts.trader, ag_id, FAKE_TUSDC, FAKE_TWETH, 600 * USDC, q.amount_out, chain.now + 60))
    chain.set_price(FAKE_TWETH, FAKE_TUSDC, 4_500)  # 0.2 WETH -> 900 USDC; final 1300
    chain.set_fees(500)  # config change after creation must not affect this agreement (snapshot 100)
    preview = await chain.preview_settle(ag_id)
    assert preview.tokens == [FAKE_TWETH] and preview.quotes == [900 * USDC] and preview.estimated_final_value == 1_300 * USDC
    assert preview.drawdown_floor == 800 * USDC and preview.keeper_floor == min_out_for_slippage(PRINCIPAL, 100)
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_settle(accounts.customer, ag_id, [])  # party must pass exactly len(tokens)-1 entries
    assert_revert(ei, VaultError.InvalidAmount)
    tx = await chain.build_settle(accounts.customer, ag_id, [preview.quotes[0]])
    rc = chain.mine(tx)
    assert rc.ok and [e.name for e in rc.events] == ["Settled"]
    expected = settle_math(1_300 * USDC, PRINCIPAL, 2_000, 100)
    assert rc.events[0].args == {
        "id": ag_id, "finalValue": 1_300 * USDC, "profit": 300 * USDC, "traderFee": expected.trader_fee,
        "platformFee": expected.platform_fee, "customerPayout": expected.customer_payout, "by": accounts.customer,
    }
    ag = await chain.read_agreement(ag_id)
    assert ag.status == STATUS_SETTLED and ag.tokens == [FAKE_TUSDC] and ag.final_value == ag.last_value == 1_300 * USDC
    assert (ag.trader_fee, ag.platform_fee, ag.customer_payout) == (60 * USDC, 3 * USDC, 1_237 * USDC)
    assert await chain.token_balance(FAKE_TUSDC, accounts.customer) == 1_237 * USDC
    assert await chain.token_balance(FAKE_TUSDC, accounts.trader) == 60 * USDC
    assert await chain.token_balance(FAKE_TUSDC, FAKE_FEE_RECIPIENT) == 3 * USDC
    assert await chain.token_balance(FAKE_TUSDC, FAKE_VAULT) == 0
    assert await chain.read_balances(ag_id) == [(FAKE_TUSDC, 0)]
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_settle(accounts.customer, ag_id, [])
    assert_revert(ei, VaultError.WrongStatus)


async def test_settle_loss_pays_no_fees_and_trader_floor(chain: FakeChainGateway, terms: Terms, accounts):
    ag_id = await open_and_accept(chain, terms)
    q = await chain.quote([FAKE_TUSDC, FAKE_TWETH], 600 * USDC)
    chain.mine(await chain.build_trade(accounts.trader, ag_id, FAKE_TUSDC, FAKE_TWETH, 600 * USDC, q.amount_out, chain.now + 60))
    chain.set_price(FAKE_TWETH, FAKE_TUSDC, 1_500)  # 0.2 WETH -> 300; final 700 < floor 800
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_settle(accounts.trader, ag_id, [1])
    assert_revert(ei, VaultError.DrawdownBreached)
    rc = chain.mine(await chain.build_settle(accounts.customer, ag_id, [1]))  # customer may settle at any value
    assert rc.ok
    ag = await chain.read_agreement(ag_id)
    assert (ag.final_value, ag.trader_fee, ag.platform_fee, ag.customer_payout) == (700 * USDC, 0, 0, 700 * USDC)
    assert await chain.token_balance(FAKE_TUSDC, accounts.customer) == 700 * USDC
    assert await chain.token_balance(FAKE_TUSDC, accounts.trader) == 0


async def test_settle_admin_and_keeper_tiers(chain: FakeChainGateway, terms: Terms, accounts):
    ag_id = await open_and_accept(chain, terms)
    q = await chain.quote([FAKE_TUSDC, FAKE_TWETH], 500 * USDC)
    chain.mine(await chain.build_trade(accounts.trader, ag_id, FAKE_TUSDC, FAKE_TWETH, 500 * USDC, q.amount_out, chain.now + 60))
    # before end_time: neither admin nor keeper
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_settle(FAKE_OWNER, ag_id, [])
    assert_revert(ei, VaultError.NotExpired)
    chain.advance(terms.duration_seconds)
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_settle(accounts.other, ag_id, [])  # keeper needs the grace period too
    assert_revert(ei, VaultError.NotExpired)
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_settle(FAKE_OWNER, ag_id, [1, 2])  # wrong length (non-empty)
    assert_revert(ei, VaultError.InvalidAmount)
    # admin: lastValue × (1 - slippage) floor
    chain.set_price(FAKE_TWETH, FAKE_TUSDC, 2_800)  # value 500 + 466.67 = 966.67 < 990
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_settle(FAKE_OWNER, ag_id, [])
    assert_revert(ei, VaultError.SlippageExceeded)
    chain.set_price(FAKE_TWETH, FAKE_TUSDC, 3_000)
    # keeper after grace with empty minOuts (floor rounding on the WETH leg costs 1 raw unit)
    chain.advance(KEEPER_GRACE)
    rc = chain.mine(await chain.build_settle(accounts.other, ag_id, []))
    assert rc.ok and rc.events[-1].args["by"] == accounts.other
    ag = await chain.read_agreement(ag_id)
    assert ag.final_value == PRINCIPAL - 1 == ag.last_value and ag.trader_fee == 0


async def test_settle_in_kind_delivery_and_orphan_claim(chain: FakeChainGateway, terms: Terms, accounts):
    ag_id = await open_and_accept(chain, terms)
    deadline = chain.now + 60
    q1 = await chain.quote([FAKE_TUSDC, FAKE_TWETH], 300 * USDC)
    chain.mine(await chain.build_trade(accounts.trader, ag_id, FAKE_TUSDC, FAKE_TWETH, 300 * USDC, q1.amount_out, deadline))
    q2 = await chain.quote([FAKE_TUSDC, FAKE_TWBTC], 600 * USDC)
    chain.mine(await chain.build_trade(accounts.trader, ag_id, FAKE_TUSDC, FAKE_TWBTC, 600 * USDC, q2.amount_out, deadline))
    assert (await chain.read_agreement(ag_id)).tokens == [FAKE_TUSDC, FAKE_TWETH, FAKE_TWBTC]
    # WETH loses its route (delivered in kind); WBTC route lost and customer blocked (orphan stays claimable)
    chain.remove_route(FAKE_TWETH, FAKE_TUSDC)
    chain.remove_route(FAKE_TWBTC, FAKE_TUSDC)
    chain.set_blocked(FAKE_TWBTC, accounts.customer)
    rc = chain.mine(await chain.build_settle(accounts.customer, ag_id, [0, 0]))
    assert rc.ok
    names = [(e.name, e.args.get("token"), e.args.get("delivered")) for e in rc.events]
    assert names == [("Unliquidated", FAKE_TWETH, True), ("Unliquidated", FAKE_TWBTC, False), ("Settled", None, None)]
    assert rc.events[-1].args["finalValue"] == 100 * USDC  # in-kind legs are not part of finalValue
    assert await chain.token_balance(FAKE_TWETH, accounts.customer) == q1.amount_out
    ag = await chain.read_agreement(ag_id)
    assert ag.tokens == [FAKE_TUSDC, FAKE_TWBTC] and ag.customer_payout == 100 * USDC
    assert await chain.read_balances(ag_id) == [(FAKE_TUSDC, 0), (FAKE_TWBTC, q2.amount_out)]
    # claim: only the customer, only Settled, only non-zero
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_claim(accounts.trader, ag_id, FAKE_TWBTC)
    assert_revert(ei, VaultError.Unauthorized)
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_claim(accounts.customer, ag_id, FAKE_TWETH)
    assert_revert(ei, VaultError.InsufficientBalance)
    chain.set_blocked(FAKE_TWBTC, accounts.customer, False)
    rc = chain.mine(await chain.build_claim(accounts.customer, ag_id, FAKE_TWBTC))
    assert rc.ok and rc.events[0].name == "Claimed" and rc.events[0].args["amount"] == q2.amount_out
    assert (await chain.read_agreement(ag_id)).tokens == [FAKE_TUSDC]
    assert await chain.token_balance(FAKE_TWBTC, accounts.customer) == q2.amount_out


async def test_settle_fee_leg_falls_back_to_customer_when_blocked(chain: FakeChainGateway, terms: Terms, accounts):
    ag_id = await open_and_accept(chain, terms)
    q = await chain.quote([FAKE_TUSDC, FAKE_TWETH], 500 * USDC)
    chain.mine(await chain.build_trade(accounts.trader, ag_id, FAKE_TUSDC, FAKE_TWETH, 500 * USDC, q.amount_out, chain.now + 60))
    chain.set_price(FAKE_TWETH, FAKE_TUSDC, 3_600)  # ~+100 profit
    chain.set_blocked(FAKE_TUSDC, accounts.trader)
    preview = await chain.preview_settle(ag_id)
    rc = chain.mine(await chain.build_settle(accounts.customer, ag_id, preview.quotes))
    assert rc.ok
    expected = settle_math(preview.estimated_final_value, PRINCIPAL, 2_000, 100)
    args = rc.events[-1].args
    assert expected.trader_fee > 0 and args["traderFee"] == 0  # blocked leg folded into the customer payout
    assert args["platformFee"] == expected.platform_fee
    assert args["customerPayout"] == expected.customer_payout + expected.trader_fee
    assert await chain.token_balance(FAKE_TUSDC, accounts.trader) == 0
    assert await chain.token_balance(FAKE_TUSDC, accounts.customer) == args["customerPayout"]


# --- reservations -------------------------------------------------------------------------------------------------


async def test_reservation_flow(chain: FakeChainGateway, terms: Terms, accounts):
    chain.fund_wallet(accounts.customer, FAKE_TUSDC, 2 * PRINCIPAL)
    ref = b"\x02" * 32
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_reserve(accounts.customer, FAKE_TUSDC, 0, ref)
    assert_revert(ei, VaultError.ZeroAmount)
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_reserve(accounts.customer, FAKE_TWETH, 1, ref)
    assert_revert(ei, VaultError.TokenNotAllowed)
    tx = await chain.build_reserve(accounts.customer, FAKE_TUSDC, 2 * PRINCIPAL, ref)
    assert tx.kind == "reserve" and [p.kind for p in tx.pre_steps] == ["approve"] and tx.pre_steps[0].amount_raw == 2 * PRINCIPAL
    rc = await chain.get_receipt(chain.wallet_send(tx))
    assert rc.ok and rc.events[0].name == "Reserved"
    assert rc.events[0].args == {"id": 1, "customer": accounts.customer, "token": FAKE_TUSDC, "amount": 2 * PRINCIPAL,
                                 "listingRef": ref}
    res = await chain.read_reservation(1)
    assert res.status == RES_OPEN and res.amount == res.original == 2 * PRINCIPAL and res.listing_ref == ref
    assert await chain.token_balance(FAKE_TUSDC, FAKE_VAULT) == 2 * PRINCIPAL
    # release rules
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_release(accounts.customer, 1, 0)
    assert_revert(ei, VaultError.ZeroAmount)
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_release(accounts.other, 1, 1)
    assert_revert(ei, VaultError.Unauthorized)
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_release(accounts.customer, 1, 3 * PRINCIPAL)
    assert_revert(ei, VaultError.ReservationInsufficient)
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_release(accounts.customer, 9, 1)
    assert_revert(ei, VaultError.ReservationNotFound)
    # openReserved draws principal without a transfer
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_open_reserved(accounts.customer, replace(terms, principal=3 * PRINCIPAL), 1)
    assert_revert(ei, VaultError.ReservationInsufficient)
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_open_reserved(accounts.customer, terms, 7)
    assert_revert(ei, VaultError.ReservationNotFound)
    tx = await chain.build_open_reserved(accounts.customer, terms, 1)
    assert tx.kind == "open_reserved" and tx.pre_steps == [] and tx.summary["gas_estimated"] is True
    rc = chain.mine(tx)
    assert rc.ok and [e.name for e in rc.events] == ["ReservationDrawn", "Opened"] and rc.raw_log_count == 2
    assert rc.events[0].args == {"id": 1, "agreementId": 1, "amount": PRINCIPAL, "remaining": PRINCIPAL}
    assert (await chain.read_reservation(1)).amount == PRINCIPAL
    assert await chain.read_balances(1) == [(FAKE_TUSDC, PRINCIPAL)]
    # mismatch: another customer's agreement cannot draw this reservation (Unauthorized first, then mismatch)
    other_terms = replace(terms, customer=accounts.other)
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_open_reserved(accounts.other, other_terms, 1)
    assert_revert(ei, VaultError.ReservationMismatch)
    # release the rest -> Released status; then it is closed for everything
    rc = chain.mine(await chain.build_release_all(accounts.customer, 1))
    assert rc.ok and rc.events[0].name == "Released"
    assert rc.events[0].args == {"id": 1, "customer": accounts.customer, "token": FAKE_TUSDC, "amount": PRINCIPAL, "remaining": 0}
    assert (await chain.read_reservation(1)).status == RES_RELEASED
    assert await chain.token_balance(FAKE_TUSDC, accounts.customer) == PRINCIPAL
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_release_all(accounts.customer, 1)
    assert_revert(ei, VaultError.ReservationClosed)
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_open_reserved(accounts.customer, terms, 1)
    assert_revert(ei, VaultError.ReservationClosed)


async def test_fund_reserved_consumes_reservation(chain: FakeChainGateway, terms: Terms, accounts):
    chain.mine(await chain.build_propose(accounts.trader, terms))
    chain.fund_wallet(accounts.customer, FAKE_TUSDC, PRINCIPAL)
    chain.wallet_send(await chain.build_reserve(accounts.customer, FAKE_TUSDC, PRINCIPAL, b"\x03" * 32))
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_fund_reserved(accounts.trader, 1, 1)
    assert_revert(ei, VaultError.Unauthorized)
    rc = chain.mine(await chain.build_fund_reserved(accounts.customer, 1, 1))
    assert rc.ok and [e.name for e in rc.events] == ["ReservationDrawn", "Activated"]
    assert (await chain.read_reservation(1)).status == RES_CONSUMED
    assert (await chain.read_agreement(1)).status == STATUS_ACTIVE
    assert await chain.token_balance(FAKE_TUSDC, FAKE_VAULT) == PRINCIPAL
    # release(0)/releaseAll on a consumed reservation -> ReservationClosed
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_release_all(accounts.customer, 1)
    assert_revert(ei, VaultError.ReservationClosed)
    # a fresh reservation in a different token would mismatch
    chain.set_token(FAKE_TWETH, True, True)
    chain.mine(await chain.build_propose(accounts.trader, terms))
    chain.fund_wallet(accounts.customer, FAKE_TWETH, 10**18)
    chain.wallet_send(await chain.build_reserve(accounts.customer, FAKE_TWETH, 10**18, b"\x04" * 32))
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_fund_reserved(accounts.customer, 2, 2)
    assert_revert(ei, VaultError.ReservationMismatch)


# --- mining model: queue, receipts, logs, reorg ----------------------------------------------------------------------


async def test_auto_mine_off_then_mine(chain: FakeChainGateway, terms: Terms, accounts):
    chain.auto_mine = False
    chain.fund_wallet(accounts.customer, FAKE_TUSDC, PRINCIPAL)
    tx = await chain.build_open(accounts.customer, terms)
    tx_hash = chain.wallet_send(tx)
    assert await chain.get_receipt(tx_hash) is None
    info = await chain.get_transaction(tx_hash)
    assert info is not None and info.block_number is None and info.input == tx.data and info.from_address == accounts.customer
    assert await chain.next_id() == 1 and len(chain.queue) == 2
    before = await chain.latest_block()
    rc = chain.mine()
    assert rc is not None and rc.ok and rc.tx_hash == tx_hash and rc.block_number == before + 1
    assert (await chain.get_receipt(tx_hash)).confirmations == 1
    chain.mine()
    chain.mine()
    assert (await chain.get_receipt(tx_hash)).confirmations == 3
    assert (await chain.get_transaction(tx_hash)).block_hash == rc.block_hash
    assert await chain.get_receipt("0x" + "00" * 32) is None and await chain.get_transaction("0x" + "00" * 32) is None
    assert chain.mine() is None  # empty block


async def test_get_logs_filters_by_range_address_and_topic(chain: FakeChainGateway, terms: Terms, accounts):
    start = await chain.latest_block()
    ag_id = await open_and_accept(chain, terms)
    q = await chain.quote([FAKE_TUSDC, FAKE_TWETH], 100 * USDC)
    chain.mine(await chain.build_trade(accounts.trader, ag_id, FAKE_TUSDC, FAKE_TWETH, 100 * USDC, q.amount_out, chain.now + 60))
    end = await chain.latest_block()
    logs = await chain.get_logs(start, end)
    assert [e.name for e in logs] == ["Opened", "Activated", "Traded"]
    assert all(e.address == FAKE_VAULT for e in logs)
    assert [e.block_number for e in logs] == sorted(e.block_number for e in logs)
    assert logs[0].block_timestamp is not None and logs[0].block_hash == (await chain.get_block(logs[0].block_number)).hash
    assert [e.name for e in await chain.get_logs(start, end, topics=[abi.event_topic("Traded")])] == ["Traded"]
    assert [e.name for e in await chain.get_logs(start, end, topics=[[abi.event_topic("Opened"), abi.event_topic("Activated")]])] == ["Opened", "Activated"]
    assert await chain.get_logs(start, start) == []
    assert [e.name for e in await chain.get_logs(logs[-1].block_number, end)] == ["Traded"]
    # ERC-20 transfers are addressable by token
    token_logs = await chain.get_logs(start, end, address=FAKE_TUSDC)
    assert [e.name for e in token_logs][:2] == ["Approval", "Transfer"]  # approve pre-step, then the escrow pull
    assert token_logs[0].args == {"owner": accounts.customer, "spender": FAKE_VAULT, "value": PRINCIPAL}
    assert token_logs[1].args == {"from": accounts.customer, "to": FAKE_VAULT, "value": PRINCIPAL}
    assert await chain.get_logs(start, end, address=accounts.other) == []


async def test_reorg_drops_receipts_and_restores_state(chain: FakeChainGateway, terms: Terms, accounts):
    chain.fund_wallet(accounts.customer, FAKE_TUSDC, PRINCIPAL)
    open_hash = chain.wallet_send(await chain.build_open(accounts.customer, terms))
    accept_rc = chain.mine(await chain.build_accept(accounts.trader, 1))
    latest = await chain.latest_block()
    old_hash = (await chain.get_block(latest)).hash
    assert (await chain.read_agreement(1)).status == STATUS_ACTIVE
    chain.reorg(1)
    assert await chain.latest_block() == latest
    assert (await chain.get_block(latest)).hash != old_hash
    assert await chain.get_receipt(accept_rc.tx_hash) is None
    assert await chain.get_transaction(accept_rc.tx_hash) is None
    assert (await chain.get_receipt(open_hash)) is not None  # older block untouched
    assert (await chain.read_agreement(1)).status == STATUS_FUNDED  # state rolled back with the block
    assert [e.name for e in await chain.get_logs(0, latest)] == ["Opened"]
    # the same action can be mined again on the new fork
    rc = chain.mine(await chain.build_accept(accounts.trader, 1))
    assert rc.ok and rc.block_number == latest + 1 and rc.tx_hash != accept_rc.tx_hash
    with pytest.raises(ValueError):
        chain.reorg(0)


# --- wallet: native / token transfer, faucet -------------------------------------------------------------------------


async def test_native_and_token_transfers(chain: FakeChainGateway, accounts):
    chain.fund_native(accounts.customer, 10**18)
    tx = await chain.build_transfer(accounts.customer, accounts.other, 4 * 10**17)
    assert tx.kind == "transfer" and tx.data == "0x" and tx.value == 4 * 10**17 and tx.gas == 21_000 and tx.pre_steps == []
    rc = await chain.get_receipt(chain.wallet_send(tx))
    assert rc.ok and rc.events == [] and rc.raw_log_count == 0 and rc.value == 4 * 10**17
    assert await chain.native_balance(accounts.other) == 4 * 10**17
    assert await chain.native_balance(accounts.customer) == 6 * 10**17
    # overspend: not a contract revert, the tx simply fails
    rc = await chain.get_receipt(chain.send(accounts.customer, accounts.other, "0x", 10**18))
    assert rc.status == 0 and rc.revert is not None and rc.revert.code is None
    # ERC-20
    chain.fund_wallet(accounts.customer, FAKE_TWETH, 2 * 10**18)
    tx = await chain.build_token_transfer(accounts.customer, FAKE_TWETH, accounts.other, 10**18)
    assert tx.to == FAKE_TWETH and abi.decode_call("TestToken", tx.data) == ("transfer", {"to": accounts.other, "value": 10**18})
    rc = await chain.get_receipt(chain.wallet_send(tx))
    assert rc.ok and rc.raw_log_count == 1 and rc.events == []  # token log, not a vault event
    assert await chain.token_balance(FAKE_TWETH, accounts.other) == 10**18
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_token_transfer(accounts.customer, FAKE_TWETH, accounts.other, 5 * 10**18)
    assert ei.value.error_code == "erc20:ERC20InsufficientBalance"


async def test_faucet_mint(chain: FakeChainGateway, accounts):
    tx_hash = await chain.faucet_mint("0x" + "ab" * 32, FAKE_TUSDC, accounts.customer, 1_000 * USDC)
    rc = await chain.get_receipt(tx_hash)
    assert rc is not None and rc.ok and rc.from_address == chain.minter_address and rc.to_address == FAKE_TUSDC
    assert await chain.token_balance(FAKE_TUSDC, accounts.customer) == 1_000 * USDC
    assert chain.calls[-1][0] == "faucet_mint"
    # a non-minter sending mint is refused by the token's AccessControl
    data = abi.encode_call("TestToken", "mint", [accounts.customer, 1])
    rc = await chain.get_receipt(chain.send(accounts.other, FAKE_TUSDC, data))
    assert rc.status == 0 and rc.revert.name == "AccessControlUnauthorizedAccount"
    assert abi.error_code_of(rc.revert) == "reverted"


# --- admin -----------------------------------------------------------------------------------------------------------


async def test_admin_functions_and_router_change(chain: FakeChainGateway, accounts):
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_admin_set_paused(accounts.other, True)
    assert_revert(ei, VaultError.Unauthorized)  # OwnableUnauthorizedAccount -> 2
    rc = chain.mine(await chain.build_admin_set_paused(FAKE_OWNER, True))
    assert rc.ok and rc.events[0].name == "ConfigChanged"
    assert abi.config_key_of(rc.events[0].args["key"]) == "paused" and rc.events[0].args["paused"] is True
    assert (await chain.read_config()).paused is True
    chain.mine(await chain.build_admin_set_paused(FAKE_OWNER, False))
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_admin_set_fees(FAKE_OWNER, 1_001, FAKE_FEE_RECIPIENT)
    assert_revert(ei, VaultError.InvalidTerms)
    rc = chain.mine(await chain.build_admin_set_fees(FAKE_OWNER, 250, accounts.other))
    assert abi.config_key_of(rc.events[0].args["key"]) == "fees" and rc.events[0].args["feeRecipient"] == accounts.other
    cfg = await chain.read_config()
    assert cfg.platform_fee_bps == 250 and cfg.fee_recipient == accounts.other
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_admin_set_settle_slippage(FAKE_OWNER, 5_001)
    assert_revert(ei, VaultError.InvalidTerms)
    chain.mine(await chain.build_admin_set_settle_slippage(FAKE_OWNER, 300))
    assert (await chain.read_config()).settle_slippage_bps == 300
    # tokens: unknown address has no code -> InvalidToken; de-listing clears isBase
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_admin_set_token(FAKE_OWNER, "0x" + "99" * 20, True, True)
    assert_revert(ei, VaultError.InvalidToken)
    rc = chain.mine(await chain.build_admin_set_token(FAKE_OWNER, FAKE_TWBTC, True, True))
    assert rc.events[0].name == "TokenSet" and rc.events[0].args == {"token": FAKE_TWBTC, "allowed": True, "isBase": True}
    rc = chain.mine(await chain.build_admin_set_token(FAKE_OWNER, FAKE_TWBTC, False, True))
    assert rc.events[0].args == {"token": FAKE_TWBTC, "allowed": False, "isBase": False}
    assert (await chain.is_token_allowed(FAKE_TWBTC)).allowed is False
    # router change: propose -> not ready -> advance -> apply; cancel path
    new_router = "0x" + "77" * 20
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_admin_apply_router(FAKE_OWNER)
    assert_revert(ei, VaultError.NoPendingRouterChange)
    rc = chain.mine(await chain.build_admin_propose_router(FAKE_OWNER, new_router))
    assert rc.events[0].name == "RouterChangeProposed" and rc.events[0].args["newRouter"] == new_router
    cfg = await chain.read_config()
    assert cfg.pending_router == new_router and cfg.router_activation_time == rc.events[0].args["activationTime"]
    with pytest.raises(ContractRevertError) as ei:
        await chain.build_admin_apply_router(FAKE_OWNER)
    assert_revert(ei, VaultError.RouterChangeNotReady)
    assert ei.value.revert.args == {"activationTime": cfg.router_activation_time}
    chain.advance(cfg.router_delay)
    rc = chain.mine(await chain.build_admin_apply_router(FAKE_OWNER))
    assert abi.config_key_of(rc.events[0].args["key"]) == "router" and rc.events[0].args["router"] == new_router
    cfg = await chain.read_config()
    assert cfg.router == new_router and cfg.pending_router is None
    chain.mine(await chain.build_admin_propose_router(FAKE_OWNER, FAKE_ROUTER))
    rc = chain.mine(await chain.build_admin_cancel_router(FAKE_OWNER))
    assert rc.events[0].name == "RouterChangeCancelled" and rc.events[0].args == {"cancelledRouter": FAKE_ROUTER}
    assert (await chain.read_config()).pending_router is None


async def test_estimate_gas_and_reset(chain: FakeChainGateway, terms: Terms, accounts):
    chain.fund_wallet(accounts.customer, FAKE_TUSDC, PRINCIPAL)
    chain.approve(accounts.customer, FAKE_TUSDC, FAKE_VAULT, PRINCIPAL)
    data = abi.encode_call("TraderVault", "open", [terms])
    assert await chain.estimate_gas(accounts.customer, FAKE_VAULT, data) == GAS_TABLE["open"]
    assert await chain.estimate_gas(accounts.customer, accounts.other, "0x", 0) == 21_000
    with pytest.raises(ContractRevertError):
        await chain.estimate_gas(accounts.other, FAKE_VAULT, data)
    assert drawdown_floor(terms.principal, terms.max_drawdown_bps) == 800 * USDC
    chain.reset()
    assert await chain.token_balance(FAKE_TUSDC, accounts.customer) == 0
    assert await chain.latest_block() == 1000 and chain.logs == [] and chain.receipts == {}
    assert (await chain.is_token_allowed(FAKE_TUSDC)).is_base is True  # defaults re-installed
