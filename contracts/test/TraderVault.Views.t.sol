// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {BaseTest} from "./Base.t.sol";
import {ITraderVault} from "../src/interfaces/ITraderVault.sol";
import {TestToken} from "../src/mocks/TestToken.sol";

contract TraderVaultViewsTest is BaseTest {
    // Rust 31: views_on_missing_agreement
    function test_views_missingAgreementReverts() public {
        expectVaultError(ITraderVault.NotFound.selector);
        vault.getAgreement(7);
        expectVaultError(ITraderVault.NotFound.selector);
        vault.getBalances(7);
        expectVaultError(ITraderVault.NotFound.selector);
        vault.valueInBase(7);
        expectVaultError(ITraderVault.NotFound.selector);
        vault.previewSettle(7);
        expectVaultError(ITraderVault.ReservationNotFound.selector);
        vault.getReservation(7);
        vm.prank(trader);
        expectVaultError(ITraderVault.NotFound.selector);
        vault.accept(7);
        vm.prank(customer);
        expectVaultError(ITraderVault.NotFound.selector);
        vault.fund(7);
        vm.prank(customer);
        expectVaultError(ITraderVault.NotFound.selector);
        vault.fundReserved(7, 1);
        vm.prank(customer);
        expectVaultError(ITraderVault.NotFound.selector);
        vault.cancel(7);
        // id 0 da yok
        expectVaultError(ITraderVault.NotFound.selector);
        vault.getAgreement(0);
    }

    // Rust 32: value_in_base_counts_unquotable_token_as_zero
    function test_valueInBase_unquotableCountsAsZero() public {
        uint256 id = openActiveWith(termsWithDrawdown(10_000));
        address weird = address(newToken(6, true, false));
        setPrice(address(usdc), weird, 1, 1);
        setPrice(weird, address(usdc), 1, 1);
        vm.prank(trader);
        vault.trade(id, address(usdc), weird, 300 * U6, 300 * U6, dl());
        assertEq(vaultBalance(id, weird), 300 * U6);
        assertEq(vault.valueInBase(id), PRINCIPAL);
        // Havuz kaybolur: tutulan drawdown kontrolü için 0 değerinde ...
        clearPrice(weird, address(usdc));
        assertEq(vault.valueInBase(id), 700 * U6);
        (address[] memory pt, uint256[] memory pb, uint256[] memory pq, uint256 est, uint256 ddf, uint256 kf) =
            vault.previewSettle(id);
        assertEq(pt, addrs1(weird));
        assertEq(pb, one(300 * U6));
        assertEq(pq, one(0));
        assertEq(est, 700 * U6);
        assertEq(ddf, 0);
        assertEq(kf, 990 * U6);
        // ... ve settle'da satılamaz, in-kind teslim edilir (herhangi bir minOut, yüksek bile)
        vm.expectEmit(address(vault));
        emit ITraderVault.Unliquidated(id, weird, 300 * U6, true);
        vm.expectEmit(address(vault));
        emit ITraderVault.Settled(id, 700 * U6, 0, 0, 0, 700 * U6, customer);
        vm.prank(customer);
        vault.settle(id, one(300 * U6));
        ITraderVault.Agreement memory a = ag(id);
        assertEq(uint8(a.status), uint8(ITraderVault.AgreementStatus.Settled));
        assertEq(a.finalValue, 700 * U6);
        assertEq(a.tokens, addrs1(address(usdc)));
        assertEq(usdc.balanceOf(customer), 700 * U6);
        assertEq(TestToken(weird).balanceOf(customer), 300 * U6);
        assertEq(TestToken(weird).balanceOf(address(vault)), 0);
        assertEq(vault.valueInBase(id), 0);
    }

    // Rust 35: ids_are_sequential_across_propose_and_open
    function test_ids_sequentialAcrossProposeAndOpen() public {
        usdc.mint(customer, PRINCIPAL);
        assertEq(vault.nextId(), 1);
        vm.prank(trader);
        assertEq(vault.propose(terms()), 1);
        vm.prank(customer);
        assertEq(vault.open(terms()), 2);
        vm.prank(trader);
        assertEq(vault.propose(terms()), 3);
        assertEq(vault.nextId(), 4);
        // başarısız oluşturma id tüketmez
        ITraderVault.Terms memory bad = terms();
        bad.principal = 0;
        vm.prank(trader);
        expectVaultError(ITraderVault.InvalidTerms.selector);
        vault.propose(bad);
        assertEq(vault.nextId(), 4);
        // open transfer başarısız olsa da id tüketilmez
        uint256 rest = usdc.balanceOf(customer);
        vm.prank(customer);
        usdc.transfer(stranger, rest); // müşteri bakiyesi 0
        vm.prank(customer);
        vm.expectRevert();
        vault.open(terms());
        assertEq(vault.nextId(), 4);
    }

    function test_previewSettle_reflectsQuotesAndFloors() public {
        uint256 id = openActive();
        (address[] memory pt, uint256[] memory pb, uint256[] memory pq, uint256 est, uint256 ddf, uint256 kf) =
            vault.previewSettle(id);
        assertEq(pt.length, 0);
        assertEq(pb.length, 0);
        assertEq(pq.length, 0);
        assertEq(est, PRINCIPAL);
        assertEq(ddf, 800 * U6);
        assertEq(kf, 990 * U6);

        vm.startPrank(trader);
        vault.trade(id, address(usdc), address(alt), 200 * U6, 800 * U18, dl());
        vault.trade(id, address(usdc), address(eurc), 100 * U6, 90 * U6, dl());
        vm.stopPrank();
        setPrice(address(alt), address(usdc), 1, 2e12); // 800 ALT -> 400
        (pt, pb, pq, est, ddf, kf) = vault.previewSettle(id);
        assertEq(pt, addrs2(address(alt), address(eurc)));
        assertEq(pb, two(800 * U18, 90 * U6));
        assertEq(pq, two(400 * U6, 100 * U6));
        assertEq(est, 1_200 * U6);
        assertEq(ddf, 800 * U6);
        assertEq(kf, 990 * U6, "keeperFloor comes from lastValue (1000), not from the quote");
        assertEq(vault.valueInBase(id), 1_200 * U6);
        // parti minOuts'unu preview'dan üretir
        vm.prank(customer);
        vault.settle(id, pq);
        assertEq(ag(id).finalValue, 1_200 * U6);
        assertEq(ag(id).lastValue, 1_200 * U6);
    }

    function test_getConfig_isTokenAllowed_paused() public {
        ITraderVault.Config memory cfg = vault.getConfig();
        assertEq(cfg.router, address(router));
        assertTrue(vault.isTokenAllowed(address(usdc)).isBase);
        assertTrue(vault.isTokenAllowed(address(eurc)).allowed);
        assertFalse(vault.isTokenAllowed(address(eurc)).isBase);
        assertFalse(vault.isTokenAllowed(stranger).allowed);
        assertFalse(vault.paused());
        vm.prank(admin);
        vault.setPaused(true);
        assertTrue(vault.paused());
        assertTrue(vault.getConfig().paused);
    }

    function test_getAgreement_fullStruct() public {
        ITraderVault.Terms memory t = terms();
        vm.prank(customer);
        uint256 id = vault.open(t);
        ITraderVault.Agreement memory a = ag(id);
        assertEq(a.id, id);
        assertEq(a.terms.customer, t.customer);
        assertEq(a.terms.trader, t.trader);
        assertEq(a.terms.baseToken, t.baseToken);
        assertEq(a.terms.principal, t.principal);
        assertEq(a.terms.durationSeconds, t.durationSeconds);
        assertEq(a.terms.commissionBps, t.commissionBps);
        assertEq(a.terms.maxDrawdownBps, t.maxDrawdownBps);
        assertEq(a.terms.listingRef, t.listingRef);
        assertEq(a.finalValue, 0);
        assertEq(a.traderFee, 0);
        assertEq(a.platformFee, 0);
        assertEq(a.customerPayout, 0);
    }
}
