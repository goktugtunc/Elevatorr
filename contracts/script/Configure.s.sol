// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Script} from "forge-std/Script.sol";
import {console2} from "forge-std/console2.sol";

import {TraderVault} from "../src/TraderVault.sol";
import {MockRouter} from "../src/mocks/MockRouter.sol";
import {TestToken} from "../src/mocks/TestToken.sol";

/// @title Configure
/// @notice Deploy sonrası opsiyonel yönetim yardımcıları. Vault/router adresleri `deployments/<DEPLOYMENT_NAME>.json`'dan
///         okunur (varsayılan monad-testnet). Her fonksiyon `--sig` ile çağrılır; gönderen vault owner / router owner /
///         MINTER_ROLE sahibi olmalıdır.
///
/// Örnekler (contracts/ içinde, PATH'te forge):
///   forge script script/Configure.s.sol --sig "setToken(address,bool,bool)" 0xTOKEN true false \
///       --rpc-url monad_testnet --private-key $MONAD_DEPLOYER_PRIVATE_KEY --broadcast
///   forge script script/Configure.s.sol --sig "setPrice(address,address,uint256,uint256)" 0xUSDC 0xWETH 1000000000000000000 3200000000 \
///       --rpc-url monad_testnet --private-key $MONAD_DEPLOYER_PRIVATE_KEY --broadcast
///   forge script script/Configure.s.sol --sig "setPair(address,address,uint256,uint256)" 0xWETH 0xUSDC 3200000000 1000000000000000000 ...
///   forge script script/Configure.s.sol --sig "mint(address,address,uint256)" 0xUSDC 0xTO 1000000000 ...
///   forge script script/Configure.s.sol --sig "grantMinter(address)" 0xMINTER ...
contract Configure is Script {
    function _deployments() internal view returns (string memory json) {
        string memory name = vm.envOr("DEPLOYMENT_NAME", string("monad-testnet"));
        json = vm.readFile(string.concat("deployments/", name, ".json"));
    }

    function _vault() internal view returns (TraderVault) {
        return TraderVault(vm.parseJsonAddress(_deployments(), ".vault.proxy"));
    }

    function _router() internal view returns (MockRouter) {
        return MockRouter(vm.parseJsonAddress(_deployments(), ".router"));
    }

    function _tokens() internal view returns (address[] memory addrs) {
        string memory json = _deployments();
        addrs = new address[](3);
        addrs[0] = vm.parseJsonAddress(json, ".tokens[0].address");
        addrs[1] = vm.parseJsonAddress(json, ".tokens[1].address");
        addrs[2] = vm.parseJsonAddress(json, ".tokens[2].address");
    }

    /// @notice Vault allow-list güncelle (owner).
    function setToken(address token, bool allowed, bool isBase) external {
        TraderVault vault = _vault();
        vm.startBroadcast();
        vault.setToken(token, allowed, isBase);
        vm.stopBroadcast();
        console2.log("setToken", token, allowed, isBase);
    }

    /// @notice MockRouter tek yön fiyat: out = in * num / den (router owner).
    function setPrice(address tokenIn, address tokenOut, uint256 num, uint256 den) external {
        MockRouter router = _router();
        vm.startBroadcast();
        router.setPrice(tokenIn, tokenOut, num, den);
        vm.stopBroadcast();
        console2.log("setPrice", tokenIn, tokenOut);
    }

    /// @notice Çift yön fiyat: a->b = num/den, b->a = den/num (router owner).
    ///         Örn. 1 WETH = 3200 USDC için setPair(tWETH, tUSDC, 3200e6, 1e18).
    function setPair(address a, address b, uint256 num, uint256 den) external {
        MockRouter router = _router();
        vm.startBroadcast();
        router.setPrice(a, b, num, den);
        router.setPrice(b, a, den, num);
        vm.stopBroadcast();
        console2.log("setPair", a, b);
    }

    /// @notice TestToken mint (gönderen MINTER_ROLE sahibi olmalı). Miktar ham birim.
    function mint(address token, address to, uint256 amount) external {
        vm.startBroadcast();
        TestToken(token).mint(to, amount);
        vm.stopBroadcast();
        console2.log("mint", token, to, amount);
    }

    /// @notice Üç TestToken'da MINTER_ROLE ver (gönderen DEFAULT_ADMIN_ROLE = deployer).
    function grantMinter(address minter) external {
        address[] memory toks = _tokens();
        vm.startBroadcast();
        for (uint256 i = 0; i < toks.length; ++i) {
            TestToken t = TestToken(toks[i]);
            t.grantRole(t.MINTER_ROLE(), minter);
        }
        vm.stopBroadcast();
        console2.log("grantMinter", minter);
    }

    /// @notice Vault durdur / devam ettir (owner).
    function setPaused(bool paused) external {
        TraderVault vault = _vault();
        vm.startBroadcast();
        vault.setPaused(paused);
        vm.stopBroadcast();
        console2.log("setPaused", paused);
    }
}
