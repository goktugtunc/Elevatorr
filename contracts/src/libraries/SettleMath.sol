// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {ITraderVault} from "../interfaces/ITraderVault.sol";

/// @title SettleMath
/// @notice Bps ve settle bölüşüm matematiği (§7). Checked math; taşma -> Panic(0x11).
///         Yuvarlama her adımda aşağı; fee'ler yalnız kâr üzerinden alınır.
library SettleMath {
    uint256 internal constant BPS_DENOM = 10_000;

    /// @dev amount * bps / 10_000 (floor)
    function bpsOf(uint256 amount, uint256 bps) internal pure returns (uint256) {
        return amount * bps / BPS_DENOM;
    }

    /// @dev principal * (10_000 - ddBps) / 10_000; ddBps > 10_000 -> InvalidTerms
    function drawdownFloor(uint256 principal, uint256 ddBps) internal pure returns (uint256) {
        if (ddBps > BPS_DENOM) revert ITraderVault.InvalidTerms();
        return principal * (BPS_DENOM - ddBps) / BPS_DENOM;
    }

    /// @dev quote * (10_000 - slipBps) / 10_000; slipBps > 10_000 -> InvalidTerms
    function minOutWithSlippage(uint256 quote, uint256 slipBps) internal pure returns (uint256) {
        if (slipBps > BPS_DENOM) revert ITraderVault.InvalidTerms();
        return quote * (BPS_DENOM - slipBps) / BPS_DENOM;
    }

    /// @dev profit = max(finalValue - principal, 0); fee'ler profit'ten; customerPayout = kalan.
    function settlement(uint256 finalValue, uint256 principal, uint256 commissionBps, uint256 platformFeeBps)
        internal
        pure
        returns (uint256 profit, uint256 traderFee, uint256 platformFee, uint256 customerPayout)
    {
        profit = finalValue > principal ? finalValue - principal : 0;
        traderFee = bpsOf(profit, commissionBps);
        platformFee = bpsOf(profit, platformFeeBps);
        customerPayout = finalValue - traderFee - platformFee;
    }
}
