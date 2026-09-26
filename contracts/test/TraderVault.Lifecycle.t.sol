// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {BaseTest} from "./Base.t.sol";
import {ITraderVault} from "../src/interfaces/ITraderVault.sol";

contract TraderVaultLifecycleTest is BaseTest {
    // Rust 8: lifecycle_customer_initiated (open -> accept -> trade -> settle)
    function test_lifecycle_customerInitiated() public {
        ITraderVault.Terms memory t = terms();

        // ---- open ----
        vm.expectEmit(address(vault));
        emit ITraderVault.Opened(1, trader, customer, PRINCIPAL, address(usdc));
        vm.prank(customer);
        uint256 id = vault.open(t);
        assertEq(id, 1);
        assertEq(usdc.balanceOf(customer), 0);
        assertEq(usdc.balanceOf(address(vault)), PRINCIPAL);
        ITraderVault.Agreement memory a = ag(id);
        assertEq(uint8(a.status), uint8(ITraderVault.AgreementStatus.Funded));
        assertEq(a.proposer, customer);
        assertEq(a.createdAt, START_TS);
        assertEq(a.startTime, 0);
        assertEq(a.endTime, 0);
        assertEq(a.settledAt, 0);
        assertEq(a.platformFeeBps, PLATFORM_FEE_BPS);
        assertEq(a.lastValue, PRINCIPAL);
        assertEq(a.terms.listingRef, t.listingRef);
        assertEq(a.tokens, addrs1(address(usdc)));
        assertBalances(id, addrs1(address(usdc)), one(PRINCIPAL));
        assertEq(vault.nextId(), 2);

        // ---- accept ----
        vm.warp(START_TS + 10);
        vm.expectEmit(address(vault));
        emit ITraderVault.Activated(id, START_TS + 10, START_TS + 10 + DURATION);
        vm.prank(trader);
        vault.accept(id);
        a = ag(id);
        assertEq(uint8(a.status), uint8(ITraderVault.AgreementStatus.Active));
        assertEq(a.startTime, START_TS + 10);
        assertEq(a.endTime, START_TS + 10 + DURATION);
        assertEq(vault.valueInBase(id), PRINCIPAL);

        // ---- trade 200 USDC -> 800 ALT ----
        uint256 amountIn = 200 * U6;
        uint256 minOut = 800 * U18;
        vm.expectEmit(address(vault));
        emit ITraderVault.Traded(id, trader, address(usdc), address(alt), amountIn, 800 * U18, PRINCIPAL);
        vm.prank(trader);
        uint256 out = vault.trade(id, address(usdc), address(alt), amountIn, minOut, dl());
        assertEq(out, 800 * U18);
        assertBalances(id, addrs2(address(usdc), address(alt)), two(800 * U6, 800 * U18));
        assertEq(usdc.balanceOf(address(vault)), 800 * U6);
        assertEq(alt.balanceOf(address(vault)), 800 * U18);
        assertEq(vault.valueInBase(id), PRINCIPAL);
        assertEq(ag(id).lastValue, PRINCIPAL);

        // ---- settle by customer (early), zero profit ----
        vm.expectEmit(address(vault));
        emit ITraderVault.Settled(id, PRINCIPAL, 0, 0, 0, PRINCIPAL, customer);
        vm.prank(customer);
        vault.settle(id, one(200 * U6));
        a = ag(id);
        assertEq(uint8(a.status), uint8(ITraderVault.AgreementStatus.Settled));
        assertEq(a.settledAt, now64());
        assertEq(a.finalValue, PRINCIPAL);
        assertEq(a.lastValue, PRINCIPAL);
        assertEq(a.customerPayout, PRINCIPAL);
        assertEq(a.traderFee, 0);
        assertEq(a.platformFee, 0);
        assertEq(usdc.balanceOf(customer), PRINCIPAL);
        assertEq(usdc.balanceOf(address(vault)), 0);
        assertEq(alt.balanceOf(address(vault)), 0);
        assertBalances(id, addrs1(address(usdc)), one(0));
        assertEq(vault.valueInBase(id), 0);
    }

    // Rust 9: lifecycle_trader_initiated_with_profit (propose -> fund -> trade -> settle)
    function test_lifecycle_traderInitiatedWithProfit() public {
        ITraderVault.Terms memory t = terms();

        // ---- propose ----
        vm.expectEmit(address(vault));
        emit ITraderVault.Proposed(1, trader, customer, PRINCIPAL, address(usdc));
        vm.prank(trader);
        uint256 id = vault.propose(t);
        ITraderVault.Agreement memory a = ag(id);
        assertEq(uint8(a.status), uint8(ITraderVault.AgreementStatus.Proposed));
        assertEq(a.proposer, trader);
        assertEq(usdc.balanceOf(address(vault)), 0);
        assertBalances(id, addrs1(address(usdc)), one(0));

        // ---- fund ----
        vm.expectEmit(address(vault));
        emit ITraderVault.Activated(id, START_TS, START_TS + DURATION);
        vm.prank(customer);
        vault.fund(id);
        a = ag(id);
        assertEq(uint8(a.status), uint8(ITraderVault.AgreementStatus.Active));
        assertEq(usdc.balanceOf(address(vault)), PRINCIPAL);
        assertEq(usdc.balanceOf(customer), 0);

        // ---- trade 200 USDC -> 800 ALT, sonra ALT 2x ----
        vm.prank(trader);
        vault.trade(id, address(usdc), address(alt), 200 * U6, 800 * U18, dl());
        setPrice(address(alt), address(usdc), 1, 2e12); // 800 ALT -> 400 USDC
        assertEq(vault.valueInBase(id), 1_200 * U6);

        // ---- settle by trader (early) ----
        uint256 finalValue = 1_200 * U6;
        uint256 traderFee = 40 * U6; // 20%
        uint256 platformFee = 2 * U6; // 1%
        uint256 customerPayout = finalValue - traderFee - platformFee;
        vm.expectEmit(address(vault));
        emit ITraderVault.Settled(id, finalValue, 200 * U6, traderFee, platformFee, customerPayout, trader);
        vm.prank(trader);
        vault.settle(id, one(400 * U6));
        assertEq(usdc.balanceOf(customer), customerPayout);
        assertEq(usdc.balanceOf(trader), traderFee);
        assertEq(usdc.balanceOf(feeRecipient), platformFee);
        assertEq(usdc.balanceOf(address(vault)), 0);
        a = ag(id);
        assertEq(uint8(a.status), uint8(ITraderVault.AgreementStatus.Settled));
        assertEq(a.finalValue, finalValue);
        assertEq(a.traderFee, traderFee);
        assertEq(a.platformFee, platformFee);
        assertEq(a.customerPayout, customerPayout);
    }

    // Rust 18: paused_blocks_new_activity_only
    function test_pause_blocksNewActivityOnly() public {
        vm.prank(trader);
        uint256 proposed = vault.propose(terms());
        vm.prank(customer);
        uint256 funded = vault.open(terms());
        usdc.mint(customer, 3 * PRINCIPAL);
        uint256 active = openActive();
        vm.prank(trader);
        vault.trade(active, address(usdc), address(alt), 100 * U6, 400 * U18, dl());
        vm.prank(customer);
        uint256 rid = vault.reserve(address(usdc), PRINCIPAL, listingRef());
        vm.prank(trader);
        uint256 proposed2 = vault.propose(terms());

        vm.prank(admin);
        vault.setPaused(true);

        vm.prank(trader);
        expectVaultError(ITraderVault.Paused.selector);
        vault.propose(terms());
        vm.prank(customer);
        expectVaultError(ITraderVault.Paused.selector);
        vault.open(terms());
        vm.prank(customer);
        expectVaultError(ITraderVault.Paused.selector);
        vault.openReserved(terms(), rid);
        vm.prank(customer);
        expectVaultError(ITraderVault.Paused.selector);
        vault.fund(proposed);
        vm.prank(customer);
        expectVaultError(ITraderVault.Paused.selector);
        vault.fundReserved(proposed2, rid);
        vm.prank(trader);
        expectVaultError(ITraderVault.Paused.selector);
        vault.accept(funded);
        vm.prank(trader);
        expectVaultError(ITraderVault.Paused.selector);
        vault.trade(active, address(usdc), address(alt), U6, 1, dl());
        vm.prank(customer);
        expectVaultError(ITraderVault.Paused.selector);
        vault.reserve(address(usdc), U6, listingRef());

        // settle, cancel, release, claim çalışır
        vm.prank(customer);
        vault.settle(active, one(100 * U6));
        assertStatus(active, ITraderVault.AgreementStatus.Settled);
        vm.prank(customer);
        vault.cancel(funded);
        assertStatus(funded, ITraderVault.AgreementStatus.Cancelled);
        vm.prank(trader);
        vault.cancel(proposed);
        assertStatus(proposed, ITraderVault.AgreementStatus.Cancelled);
        vm.prank(customer);
        assertEq(vault.release(rid, PRINCIPAL / 2), PRINCIPAL / 2);
        vm.prank(customer);
        assertEq(vault.releaseAll(rid), PRINCIPAL / 2);
        // claim pause'a takılmaz (bakiye olmadığı için InsufficientBalance)
        vm.prank(customer);
        expectVaultError(ITraderVault.InsufficientBalance.selector);
        vault.claim(active, address(usdc));

        // 1P (setup) + 3P (burada) - 2 open - 1 reserve + settle payout + refund + release
        assertEq(usdc.balanceOf(customer), 4 * PRINCIPAL);
        assertEq(usdc.balanceOf(address(vault)), 0);

        vm.prank(admin);
        vault.setPaused(false);
        vm.prank(trader);
        vault.propose(terms());
    }

    // Rust 19: invalid_terms_are_rejected
    function _check(ITraderVault.Terms memory t, bytes4 err) internal {
        vm.prank(trader);
        vm.expectRevert(err);
        vault.propose(t);
        vm.prank(customer);
        vm.expectRevert(err);
        vault.open(t);
    }

    function test_terms_invalidAreRejected() public {
        ITraderVault.Terms memory t;
        t = terms();
        t.principal = 0;
        _check(t, ITraderVault.InvalidTerms.selector);
        t = terms();
        t.durationSeconds = DAY - 1;
        _check(t, ITraderVault.InvalidTerms.selector);
        t = terms();
        t.durationSeconds = 3 * 365 * DAY + 1;
        _check(t, ITraderVault.InvalidTerms.selector);
        t = terms();
        t.commissionBps = 5_001;
        _check(t, ITraderVault.InvalidTerms.selector);
        t = terms();
        t.maxDrawdownBps = 99;
        _check(t, ITraderVault.InvalidTerms.selector);
        t = terms();
        t.maxDrawdownBps = 10_001;
        _check(t, ITraderVault.InvalidTerms.selector);
        // customer == trader
        t = terms();
        t.customer = trader;
        vm.prank(trader);
        expectVaultError(ITraderVault.InvalidTerms.selector);
        vault.propose(t);
        vm.prank(trader);
        expectVaultError(ITraderVault.InvalidTerms.selector);
        vault.open(t);
        // sıfır adres (yeni): propose'da customer=0, open'da trader=0
        t = terms();
        t.customer = address(0);
        vm.prank(trader);
        expectVaultError(ITraderVault.ZeroAddress.selector);
        vault.propose(t);
        t = terms();
        t.trader = address(0);
        vm.prank(customer);
        expectVaultError(ITraderVault.ZeroAddress.selector);
        vault.open(t);
        // base olmayan token
        t = terms();
        t.baseToken = address(eurc);
        _check(t, ITraderVault.TokenNotAllowed.selector);
        // bilinmeyen token
        t = terms();
        t.baseToken = makeAddr("unknown");
        _check(t, ITraderVault.TokenNotAllowed.selector);
        // sınır değerler kabul
        t = terms();
        t.durationSeconds = DAY;
        t.commissionBps = 5_000;
        t.maxDrawdownBps = 100;
        vm.prank(trader);
        vault.propose(t);
        t = terms();
        t.durationSeconds = 3 * 365 * DAY;
        t.commissionBps = 0;
        t.maxDrawdownBps = 10_000;
        vm.prank(trader);
        vault.propose(t);
        // msg.sender terms'teki tarafla eşleşmeli
        vm.prank(customer);
        expectVaultError(ITraderVault.Unauthorized.selector);
        vault.propose(terms());
        vm.prank(trader);
        expectVaultError(ITraderVault.Unauthorized.selector);
        vault.open(terms());
    }

    // Rust 27: lifecycle_functions_reject_wrong_signer
    function test_auth_wrongSenderReverts() public {
        ITraderVault.Terms memory t = terms();
        vm.prank(trader);
        uint256 proposed = vault.propose(t);
        vm.prank(customer);
        uint256 funded = vault.open(t);
        usdc.mint(customer, 2 * PRINCIPAL);
        uint256 active = openActive();
        vm.prank(customer);
        uint256 rid = vault.reserve(address(usdc), PRINCIPAL, listingRef());

        vm.prank(stranger);
        expectVaultError(ITraderVault.Unauthorized.selector);
        vault.propose(t);
        vm.prank(stranger);
        expectVaultError(ITraderVault.Unauthorized.selector);
        vault.open(t);
        vm.prank(trader);
        expectVaultError(ITraderVault.Unauthorized.selector);
        vault.openReserved(t, rid);
        vm.prank(trader);
        expectVaultError(ITraderVault.Unauthorized.selector);
        vault.fund(proposed);
        vm.prank(trader);
        expectVaultError(ITraderVault.Unauthorized.selector);
        vault.fundReserved(proposed, rid);
        vm.prank(customer);
        expectVaultError(ITraderVault.Unauthorized.selector);
        vault.accept(funded);
        vm.prank(stranger);
        expectVaultError(ITraderVault.NotParty.selector);
        vault.cancel(funded);
        vm.prank(customer);
        expectVaultError(ITraderVault.Unauthorized.selector);
        vault.trade(active, address(usdc), address(alt), U6, 1, dl());
        vm.prank(stranger);
        expectVaultError(ITraderVault.NotExpired.selector);
        vault.settle(active, none());
        vm.prank(stranger);
        expectVaultError(ITraderVault.Unauthorized.selector);
        vault.claim(active, address(usdc));
        vm.prank(stranger);
        expectVaultError(ITraderVault.Unauthorized.selector);
        vault.release(rid, 1);
        vm.prank(stranger);
        expectVaultError(ITraderVault.Unauthorized.selector);
        vault.releaseAll(rid);

        // state dokunulmadı
        assertStatus(proposed, ITraderVault.AgreementStatus.Proposed);
        assertStatus(funded, ITraderVault.AgreementStatus.Funded);
        assertStatus(active, ITraderVault.AgreementStatus.Active);
        assertEq(vaultBalance(active, address(usdc)), PRINCIPAL);
        assertEq(vault.getReservation(rid).amount, PRINCIPAL);
    }

    // Rust 28: cancel_proposed_only_by_proposer
    function test_cancel_proposedOnlyByProposer() public {
        vm.prank(trader);
        uint256 id = vault.propose(terms());
        vm.prank(customer);
        expectVaultError(ITraderVault.NotParty.selector);
        vault.cancel(id);
        vm.prank(stranger);
        expectVaultError(ITraderVault.NotParty.selector);
        vault.cancel(id);

        vm.expectEmit(address(vault));
        emit ITraderVault.Cancelled(id, 0);
        vm.prank(trader);
        vault.cancel(id);
        assertStatus(id, ITraderVault.AgreementStatus.Cancelled);

        vm.prank(customer);
        expectVaultError(ITraderVault.WrongStatus.selector);
        vault.fund(id);
        vm.prank(trader);
        expectVaultError(ITraderVault.WrongStatus.selector);
        vault.cancel(id);
    }

    // Rust 29: cancel_funded_refunds_customer
    function test_cancel_fundedRefundsCustomer() public {
        // customer
        vm.prank(customer);
        uint256 id = vault.open(terms());
        assertEq(usdc.balanceOf(customer), 0);
        vm.prank(stranger);
        expectVaultError(ITraderVault.NotParty.selector);
        vault.cancel(id);

        vm.expectEmit(address(vault));
        emit ITraderVault.Cancelled(id, PRINCIPAL);
        vm.prank(customer);
        vault.cancel(id);
        assertEq(usdc.balanceOf(customer), PRINCIPAL);
        assertEq(usdc.balanceOf(address(vault)), 0);
        assertStatus(id, ITraderVault.AgreementStatus.Cancelled);
        assertBalances(id, addrs1(address(usdc)), one(0));
        vm.prank(trader);
        expectVaultError(ITraderVault.WrongStatus.selector);
        vault.accept(id);

        // trader (teklifi reddeder)
        vm.prank(customer);
        uint256 id2 = vault.open(terms());
        vm.prank(trader);
        vault.cancel(id2);
        assertEq(usdc.balanceOf(customer), PRINCIPAL);
        assertStatus(id2, ITraderVault.AgreementStatus.Cancelled);

        // Active iptal edilemez (settle kullanılır)
        uint256 id3 = openActive();
        vm.prank(customer);
        expectVaultError(ITraderVault.WrongStatus.selector);
        vault.cancel(id3);
        // bilinmeyen id
        vm.prank(customer);
        expectVaultError(ITraderVault.NotFound.selector);
        vault.cancel(99);
    }

    function test_fund_recheckBaseTokenAndStatus() public {
        vm.prank(trader);
        uint256 id = vault.propose(terms());
        // Funded olanı fund edememek
        vm.prank(customer);
        uint256 funded = vault.open(terms());
        vm.prank(customer);
        expectVaultError(ITraderVault.WrongStatus.selector);
        vault.fund(funded);
        // accept Proposed üzerinde WrongStatus
        vm.prank(trader);
        expectVaultError(ITraderVault.WrongStatus.selector);
        vault.accept(id);
        // base de-list -> fund TokenNotAllowed
        vm.prank(admin);
        vault.setToken(address(usdc), false, false);
        vm.prank(customer);
        expectVaultError(ITraderVault.TokenNotAllowed.selector);
        vault.fund(id);
        vm.prank(admin);
        vault.setToken(address(usdc), true, true);
        usdc.mint(customer, PRINCIPAL);
        vm.prank(customer);
        vault.fund(id);
        assertStatus(id, ITraderVault.AgreementStatus.Active);
    }
}
