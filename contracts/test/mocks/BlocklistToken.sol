// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {TestToken} from "../../src/mocks/TestToken.sol";

/// @notice TestToken + blocklist: `from`/`to` bloklu ise her transfer revert eder.
///         EVM'de trustline yok; "alınamayan bacak" (Rust 17, 34) bu tokenla simüle edilir.
contract BlocklistToken is TestToken {
    mapping(address => bool) public blocked;

    error Blocked(address account);

    constructor(string memory name_, string memory symbol_, uint8 decimals_, address admin)
        TestToken(name_, symbol_, decimals_, admin)
    {}

    function setBlocked(address account, bool isBlocked) external {
        blocked[account] = isBlocked;
    }

    function _update(address from, address to, uint256 value) internal override {
        if (blocked[from]) revert Blocked(from);
        if (blocked[to]) revert Blocked(to);
        super._update(from, to, value);
    }
}
