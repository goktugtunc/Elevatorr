// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Test, stdError} from "forge-std/Test.sol";
import {SettleMath} from "../src/libraries/SettleMath.sol";
import {ITraderVault} from "../src/interfaces/ITraderVault.sol";

/// @dev Kütüphane fonksiyonlarını dış çağrıya açar (revert yakalamak için).
contract SettleMathHarness {
    function bpsOf(uint256 amount, uint256 bps) external pure returns (uint256) {
        return SettleMath.bpsOf(amount, bps);
    }

    function drawdownFloor(uint256 principal, uint256 ddBps) external pure returns (uint256) {
        return SettleMath.drawdownFloor(principal, ddBps);
    }

    function minOutWithSlippage(uint256 quote, uint256 slipBps) external pure returns (uint256) {
        return SettleMath.minOutWithSlippage(quote, slipBps);
    }

    function settlement(uint256 finalValue, uint256 principal, uint256 commissionBps, uint256 platformFeeBps)
        external
        pure
        returns (uint256, uint256, uint256, uint256)
    {
        return SettleMath.settlement(finalValue, principal, commissionBps, platformFeeBps);
    }
}

contract SettleMathTest is Test {
    uint256 internal constant U6 = 1e6;
    uint256 internal constant PRINCIPAL = 1_000 * U6;
    uint256 internal constant HUGE = 1e40;

    SettleMathHarness internal h;

    function setUp() public {
        h = new SettleMathHarness();
    }

    // Rust 36: settlement_math_invariants (§7)
    function testFuzz_settlement_invariants(uint256 finalValue, uint256 principal, uint16 commission, uint16 platform)
        public
        view
    {
        finalValue = bound(finalValue, 0, HUGE);
        principal = bound(principal, 0, HUGE);
        commission = uint16(bound(commission, 0, 5_000));
        platform = uint16(bound(platform, 0, 1_000));

        (uint256 profit, uint256 traderFee, uint256 platformFee, uint256 customerPayout) =
            h.settlement(finalValue, principal, commission, platform);

        uint256 expectedProfit = finalValue > principal ? finalValue - principal : 0;
        assertEq(profit, expectedProfit, "profit");
        assertEq(customerPayout + traderFee + platformFee, finalValue, "payout + fees == finalValue");
        assertLe(traderFee + platformFee, profit, "fees <= profit");
        assertEq(traderFee, expectedProfit * commission / 10_000, "traderFee");
        assertEq(platformFee, expectedProfit * platform / 10_000, "platformFee");
        if (finalValue <= principal) {
            assertEq(customerPayout, finalValue, "loss: customer gets everything");
            assertEq(traderFee, 0);
            assertEq(platformFee, 0);
        } else {
            assertGe(customerPayout, principal, "profit case never dips below principal");
        }
    }

    function test_settlement_examples() public view {
        (uint256 p, uint256 tf, uint256 pf, uint256 cp) = h.settlement(1_200 * U6, PRINCIPAL, 2_000, 100);
        assertEq(p, 200 * U6);
        assertEq(tf, 40 * U6);
        assertEq(pf, 2 * U6);
        assertEq(cp, 1_158 * U6);
        (p, tf, pf, cp) = h.settlement(900 * U6, PRINCIPAL, 2_000, 100);
        assertEq(p, 0);
        assertEq(tf, 0);
        assertEq(pf, 0);
        assertEq(cp, 900 * U6);
        (p, tf, pf, cp) = h.settlement(PRINCIPAL, PRINCIPAL, 5_000, 1_000);
        assertEq(cp, PRINCIPAL);
        (p, tf, pf, cp) = h.settlement(0, PRINCIPAL, 5_000, 1_000);
        assertEq(cp, 0);
        // yuvarlama aşağı: profit 1, 20% -> 0
        (p, tf, pf, cp) = h.settlement(PRINCIPAL + 1, PRINCIPAL, 2_000, 100);
        assertEq(p, 1);
        assertEq(tf, 0);
        assertEq(pf, 0);
        assertEq(cp, PRINCIPAL + 1);
    }

    function test_settlement_overflowPanics() public {
        vm.expectRevert(stdError.arithmeticError);
        h.bpsOf(type(uint256).max, 2);
        vm.expectRevert(stdError.arithmeticError);
        h.settlement(type(uint256).max, 1, 5_000, 1_000);
        vm.expectRevert(stdError.arithmeticError);
        h.drawdownFloor(type(uint256).max, 100);
        vm.expectRevert(stdError.arithmeticError);
        h.minOutWithSlippage(type(uint256).max, 100);
        // sınırlar
        vm.expectRevert(ITraderVault.InvalidTerms.selector);
        h.drawdownFloor(PRINCIPAL, 10_001);
        vm.expectRevert(ITraderVault.InvalidTerms.selector);
        h.minOutWithSlippage(1_000, 10_001);
    }

    // Rust 37: drawdown_and_slippage_math
    function test_math_drawdownAndSlippage() public view {
        assertEq(h.drawdownFloor(PRINCIPAL, 2_000), 800 * U6);
        assertEq(h.drawdownFloor(PRINCIPAL, 10_000), 0);
        assertEq(h.drawdownFloor(PRINCIPAL, 100), 990 * U6);
        assertEq(h.drawdownFloor(PRINCIPAL, 0), PRINCIPAL);
        assertEq(h.minOutWithSlippage(1_000, 100), 990);
        assertEq(h.minOutWithSlippage(1_000, 0), 1_000);
        assertEq(h.minOutWithSlippage(0, 500), 0);
        assertEq(h.minOutWithSlippage(1_000, 10_000), 0);
        // floor yuvarlama
        assertEq(h.bpsOf(999, 2_500), 249);
        assertEq(h.bpsOf(0, 10_000), 0);
        assertEq(h.bpsOf(1, 9_999), 0);
        assertEq(h.bpsOf(10_000, 1), 1);
    }

    function testFuzz_drawdownFloor_monotonic(uint256 p, uint16 bps) public view {
        p = bound(p, 1, 1_000_000_000 * U6);
        bps = uint16(bound(bps, 100, 10_000));
        uint256 floor_ = h.drawdownFloor(p, bps);
        assertLe(floor_, p);
        if (bps < 10_000) {
            assertLe(h.drawdownFloor(p, bps + 1), floor_, "larger drawdown never raises the floor");
        } else {
            assertEq(floor_, 0);
        }
        // floor + bpsOf(p, bps) ~ p (yuvarlama en fazla 1)
        assertLe(p - floor_ - h.bpsOf(p, bps), 1);
    }

    function testFuzz_minOutWithSlippage_monotonic(uint256 q, uint16 slip) public view {
        q = bound(q, 0, HUGE);
        slip = uint16(bound(slip, 0, 10_000));
        uint256 m = h.minOutWithSlippage(q, slip);
        assertLe(m, q);
        if (slip < 10_000) assertLe(h.minOutWithSlippage(q, slip + 1), m);
        if (slip == 0) assertEq(m, q);
    }
}
