// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Script} from "forge-std/Script.sol";
import {console2} from "forge-std/console2.sol";
import {ERC1967Proxy} from "@openzeppelin/contracts/proxy/ERC1967/ERC1967Proxy.sol";

import {TraderVault} from "../src/TraderVault.sol";
import {MockRouter} from "../src/mocks/MockRouter.sol";
import {TestToken} from "../src/mocks/TestToken.sol";

/// @title Deploy
/// @notice Spec §9.1 akışı: 3 TestToken + MockRouter (+ fiyat/likidite) + TraderVault impl + ERC1967Proxy + allow-list,
///         ardından `deployments/<DEPLOYMENT_NAME>.json` (§9.4 şeması; txHashes/blockNumber shell'de broadcast'ten doldurulur).
///
/// Env (hepsi opsiyonel):
///   DEPLOYMENT_NAME      monad-testnet | anvil-local          (varsayılan monad-testnet)
///   MINTER_ADDRESS       TestToken MINTER_ROLE alacak faucet adresi (backend MINTER_KEY); boşsa yalnız deployer
///   FEE_RECIPIENT        platform ücreti alıcısı               (varsayılan deployer)
///   PLATFORM_FEE_BPS     0..1000                               (varsayılan 0)
///   SETTLE_SLIPPAGE_BPS  0..5000                               (varsayılan 100)
///   ROUTER_DELAY         saniye, 0..2592000                    (varsayılan 0; testnet için 600, ana ağ 86400 önerilir)
///
/// Deployer, `forge script ... --private-key` (veya --sender/--ledger) ile verilen hesaptır; script anahtar okumaz.
contract Deploy is Script {
    // Fiyatlar (ham birim oranı, out = in * num / den): 1 WETH = 3000 USDC, 1 WBTC = 60000 USDC, 1 WBTC = 20 WETH
    uint256 internal constant WETH_USDC = 3000e6;
    uint256 internal constant WBTC_USDC = 60_000e6;
    uint256 internal constant WBTC_WETH = 20e18;

    // Router likiditesi ve deployer demo bakiyesi (§9.1 adım 5)
    uint256 internal constant ROUTER_USDC = 10_000_000e6;
    uint256 internal constant ROUTER_WETH = 5_000e18;
    uint256 internal constant ROUTER_WBTC = 250e8;
    uint256 internal constant DEMO_USDC = 100_000e6;

    struct Params {
        string name;
        address minter;
        address feeRecipient;
        uint16 platformFeeBps;
        uint16 settleSlippageBps;
        uint64 routerDelay;
    }

    struct Deployed {
        TestToken tUSDC;
        TestToken tWETH;
        TestToken tWBTC;
        MockRouter router;
        TraderVault impl;
        TraderVault vault;
    }

    function run() external {
        Params memory p = _params();
        uint256 startBlock = block.number;

        vm.startBroadcast();
        (, address deployer,) = vm.readCallers();
        if (p.feeRecipient == address(0)) p.feeRecipient = deployer;

        Deployed memory d;

        // 1) Test tokenlar (admin + MINTER_ROLE = deployer)
        d.tUSDC = new TestToken("Test USDC", "tUSDC", 6, deployer);
        d.tWETH = new TestToken("Test WETH", "tWETH", 18, deployer);
        d.tWBTC = new TestToken("Test WBTC", "tWBTC", 8, deployer);

        // 2) MockRouter + iki yönlü sabit fiyatlar
        d.router = new MockRouter(deployer);
        d.router.setPrice(address(d.tUSDC), address(d.tWETH), 1e18, WETH_USDC);
        d.router.setPrice(address(d.tWETH), address(d.tUSDC), WETH_USDC, 1e18);
        d.router.setPrice(address(d.tUSDC), address(d.tWBTC), 1e8, WBTC_USDC);
        d.router.setPrice(address(d.tWBTC), address(d.tUSDC), WBTC_USDC, 1e8);
        d.router.setPrice(address(d.tWETH), address(d.tWBTC), 1e8, WBTC_WETH);
        d.router.setPrice(address(d.tWBTC), address(d.tWETH), WBTC_WETH, 1e8);

        // 3) Router likiditesi + deployer demo bakiyesi
        d.tUSDC.mint(address(d.router), ROUTER_USDC);
        d.tWETH.mint(address(d.router), ROUTER_WETH);
        d.tWBTC.mint(address(d.router), ROUTER_WBTC);
        d.tUSDC.mint(deployer, DEMO_USDC);

        // 4) Faucet minter (K7)
        if (p.minter != address(0) && p.minter != deployer) {
            bytes32 role = d.tUSDC.MINTER_ROLE();
            d.tUSDC.grantRole(role, p.minter);
            d.tWETH.grantRole(role, p.minter);
            d.tWBTC.grantRole(role, p.minter);
        }

        // 5) Vault: implementasyon + UUPS proxy (initialize proxy constructor'ında)
        d.impl = new TraderVault();
        bytes memory initData = abi.encodeCall(
            TraderVault.initialize,
            (deployer, address(d.router), p.feeRecipient, p.platformFeeBps, p.settleSlippageBps, p.routerDelay)
        );
        ERC1967Proxy proxy = new ERC1967Proxy(address(d.impl), initData);
        d.vault = TraderVault(address(proxy));

        // 6) Allow-list: tUSDC taban, tWETH/tWBTC işlem varlığı
        d.vault.setToken(address(d.tUSDC), true, true);
        d.vault.setToken(address(d.tWETH), true, false);
        d.vault.setToken(address(d.tWBTC), true, false);

        vm.stopBroadcast();

        _writeDeployments(p, d, deployer, startBlock);
        _log(p, d, deployer);
    }

    // ------------------------------------------------------------------

    function _params() internal view returns (Params memory p) {
        p.name = vm.envOr("DEPLOYMENT_NAME", string("monad-testnet"));
        p.minter = vm.envOr("MINTER_ADDRESS", address(0));
        p.feeRecipient = vm.envOr("FEE_RECIPIENT", address(0));
        p.platformFeeBps = uint16(vm.envOr("PLATFORM_FEE_BPS", uint256(0)));
        p.settleSlippageBps = uint16(vm.envOr("SETTLE_SLIPPAGE_BPS", uint256(100)));
        p.routerDelay = uint64(vm.envOr("ROUTER_DELAY", uint256(0)));
        require(
            keccak256(bytes(p.name)) == keccak256("monad-testnet")
                || keccak256(bytes(p.name)) == keccak256("anvil-local"),
            "DEPLOYMENT_NAME must be monad-testnet or anvil-local"
        );
    }

    /// @dev §9.4 şeması. `txHashes` ve kesin `blockNumber` broadcast dosyasından shell'de yazılır
    ///      (scripts/deploy-testnet.sh, scripts/anvil-local.sh); burada blockNumber = deploy öncesi blok (indexer alt sınırı).
    function _writeDeployments(Params memory p, Deployed memory d, address deployer, uint256 startBlock) internal {
        string memory vaultObj = "vault";
        vm.serializeAddress(vaultObj, "proxy", address(d.vault));
        vaultObj = vm.serializeAddress(vaultObj, "implementation", address(d.impl));

        string[] memory tokens = new string[](3);
        tokens[0] = _tokenObj("tok0", d.tUSDC, true);
        tokens[1] = _tokenObj("tok1", d.tWETH, false);
        tokens[2] = _tokenObj("tok2", d.tWBTC, false);

        string memory root = "root";
        vm.serializeUint(root, "chainId", block.chainid);
        vm.serializeString(root, "vault", vaultObj);
        vm.serializeAddress(root, "router", address(d.router));
        vm.serializeString(root, "tokens", tokens);
        vm.serializeAddress(root, "deployer", deployer);
        vm.serializeUint(root, "blockNumber", startBlock);
        vm.serializeAddress(root, "feeRecipient", p.feeRecipient);
        vm.serializeUint(root, "platformFeeBps", p.platformFeeBps);
        vm.serializeUint(root, "settleSlippageBps", p.settleSlippageBps);
        vm.serializeUint(root, "routerDelay", p.routerDelay);
        vm.serializeAddress(root, "minter", p.minter == address(0) ? deployer : p.minter);
        string memory json = vm.serializeUint(root, "deployedAt", block.timestamp);

        string memory path = string.concat("deployments/", p.name, ".json");
        vm.writeJson(json, path);
        console2.log("deployments yazildi:", path);
    }

    function _tokenObj(string memory key, TestToken t, bool isBase) internal returns (string memory) {
        vm.serializeString(key, "symbol", t.symbol());
        vm.serializeAddress(key, "address", address(t));
        vm.serializeUint(key, "decimals", t.decimals());
        return vm.serializeBool(key, "isBase", isBase);
    }

    function _log(Params memory p, Deployed memory d, address deployer) internal pure {
        console2.log("--- TraderVault deploy (%s) ---", p.name);
        console2.log("deployer / owner      :", deployer);
        console2.log("vault proxy           :", address(d.vault));
        console2.log("vault implementation  :", address(d.impl));
        console2.log("router                :", address(d.router));
        console2.log("tUSDC (6, base)       :", address(d.tUSDC));
        console2.log("tWETH (18)            :", address(d.tWETH));
        console2.log("tWBTC (8)             :", address(d.tWBTC));
        console2.log("feeRecipient          :", p.feeRecipient);
        console2.log("platformFeeBps        :", p.platformFeeBps);
        console2.log("settleSlippageBps     :", p.settleSlippageBps);
        console2.log("routerDelay (s)       :", p.routerDelay);
        console2.log("minter                :", p.minter == address(0) ? deployer : p.minter);
    }
}
