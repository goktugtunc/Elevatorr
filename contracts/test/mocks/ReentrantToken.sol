// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {TestToken} from "../../src/mocks/TestToken.sol";

/// @notice `transfer`/`transferFrom` sırasında yapılandırılan `target.call(data)`'yı çalıştıran token.
///         `bubble = true`  -> iç çağrı başarısızsa aynı revert verisiyle revert eder (transfer başarısız olur).
///         `bubble = false` -> sonucu kaydeder (`lastOk`, `lastReturn`) ve transfer normal tamamlanır.
///         Tek atımlık: `armed` bir kez tetiklenir (sonsuz döngü yok).
contract ReentrantToken is TestToken {
    address public target;
    bytes public data;
    bool public bubble;
    bool public armed;

    bool public fired;
    bool public lastOk;
    bytes public lastReturn;

    constructor(string memory name_, string memory symbol_, uint8 decimals_, address admin)
        TestToken(name_, symbol_, decimals_, admin)
    {}

    function arm(address target_, bytes calldata data_, bool bubble_) external {
        target = target_;
        data = data_;
        bubble = bubble_;
        armed = true;
        fired = false;
    }

    function disarm() external {
        armed = false;
    }

    function transfer(address to, uint256 value) public override returns (bool) {
        _reenter();
        return super.transfer(to, value);
    }

    function transferFrom(address from, address to, uint256 value) public override returns (bool) {
        _reenter();
        return super.transferFrom(from, to, value);
    }

    function _reenter() private {
        if (!armed) return;
        armed = false;
        fired = true;
        (bool ok, bytes memory ret) = target.call(data);
        lastOk = ok;
        lastReturn = ret;
        if (!ok && bubble) {
            assembly ("memory-safe") {
                revert(add(ret, 0x20), mload(ret))
            }
        }
    }
}
