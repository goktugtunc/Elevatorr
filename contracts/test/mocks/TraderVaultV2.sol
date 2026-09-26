// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {TraderVault} from "../../src/TraderVault.sol";

/// @notice Upgrade testi için V2: yeni alan (`__gap`'ten sonra, append-only) + `version()` + `reinitializer(2)`.
contract TraderVaultV2 is TraderVault {
    uint256 public newField;

    function initializeV2(uint256 value) external reinitializer(2) {
        newField = value;
    }

    function version() external pure returns (uint256) {
        return 2;
    }
}
