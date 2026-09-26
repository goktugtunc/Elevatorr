// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {ERC1967Proxy} from "@openzeppelin/contracts/proxy/ERC1967/ERC1967Proxy.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

import {TraderVault} from "../src/TraderVault.sol";
import {ITraderVault} from "../src/interfaces/ITraderVault.sol";
import {MockRouter} from "../src/mocks/MockRouter.sol";
import {TestToken} from "../src/mocks/TestToken.sol";

/// @notice Ortak fixture (spec §10.1; Rust `setup()` karşılığı).
///         usdc (6 dp, base), alt (18 dp, base; Rust `xlm` rolü), eurc (6 dp, base değil).
///         Fiyatlar: 1 USDC = 4 ALT, 1 USDC = 0.9 EURC. Router ve tokenların sahibi test kontratıdır;
///         vault owner'ı `admin`dir.
abstract contract BaseTest is Test {
    uint256 internal constant U6 = 1e6;
    uint256 internal constant U18 = 1e18;
    uint256 internal constant PRINCIPAL = 1_000 * U6;
    uint64 internal constant DAY = 86_400;
    uint64 internal constant DURATION = 30 * DAY;
    uint64 internal constant START_TS = 1_700_000_000;
    uint256 internal constant LIQ = 1_000_000_000; // x token birimi
    uint16 internal constant PLATFORM_FEE_BPS = 100; // 1%
    uint16 internal constant SETTLE_SLIPPAGE_BPS = 100; // 1%
    uint64 internal constant ROUTER_DELAY = 86_400;
    uint64 internal constant KEEPER_GRACE = 604_800;

    address internal admin;
    address internal customer;
    address internal trader;
    address internal feeRecipient;
    address internal stranger;

    TestToken internal usdc;
    TestToken internal alt;
    TestToken internal eurc;
    MockRouter internal router;
    TraderVault internal impl;
    TraderVault internal vault;

    uint256 private _refNonce;

    struct Snapshot {
        bytes32 agreement;
        bytes32 balances;
        uint256 vaultUsdc;
        uint256 vaultAlt;
        uint256 routerUsdc;
        uint256 routerAlt;
    }

    function setUp() public virtual {
        vm.warp(START_TS);

        admin = makeAddr("admin");
        customer = makeAddr("customer");
        trader = makeAddr("trader");
        feeRecipient = makeAddr("feeRecipient");
        stranger = makeAddr("stranger");

        usdc = new TestToken("Test USDC", "tUSDC", 6, address(this));
        alt = new TestToken("Test ALT", "tALT", 18, address(this));
        eurc = new TestToken("Test EURC", "tEURC", 6, address(this));
        router = new MockRouter(address(this));

        impl = new TraderVault();
        vault = deployVault(address(router));

        vm.startPrank(admin);
        vault.setToken(address(usdc), true, true);
        vault.setToken(address(alt), true, true);
        vault.setToken(address(eurc), true, false);
        vm.stopPrank();

        usdc.mint(customer, PRINCIPAL);
        usdc.mint(address(router), LIQ * U6);
        alt.mint(address(router), LIQ * U18);
        eurc.mint(address(router), LIQ * U6);

        setPrice(address(usdc), address(alt), 4e12, 1);
        setPrice(address(alt), address(usdc), 1, 4e12);
        setPrice(address(usdc), address(eurc), 9, 10);
        setPrice(address(eurc), address(usdc), 10, 9);

        vm.prank(customer);
        usdc.approve(address(vault), type(uint256).max);
    }

    // ------------------------------------------------------------------
    // Deploy / config
    // ------------------------------------------------------------------

    function deployVault(address router_) internal returns (TraderVault) {
        return deployVaultWith(admin, router_, feeRecipient, PLATFORM_FEE_BPS, SETTLE_SLIPPAGE_BPS, ROUTER_DELAY);
    }

    function deployVaultWith(
        address owner_,
        address router_,
        address feeRecipient_,
        uint16 platformFeeBps_,
        uint16 settleSlippageBps_,
        uint64 routerDelay_
    ) internal returns (TraderVault) {
        ERC1967Proxy proxy = new ERC1967Proxy(
            address(impl),
            abi.encodeCall(
                TraderVault.initialize,
                (owner_, router_, feeRecipient_, platformFeeBps_, settleSlippageBps_, routerDelay_)
            )
        );
        return TraderVault(address(proxy));
    }

    /// @dev Yeni TestToken: router'a bol likidite basılır; allow-list durumu admin ile yazılır.
    function newToken(uint8 decimals, bool allowed, bool isBase) internal returns (TestToken t) {
        t = new TestToken("Extra", "X", decimals, address(this));
        t.mint(address(router), LIQ * (10 ** uint256(decimals)));
        vm.prank(admin);
        vault.setToken(address(t), allowed, isBase);
    }

    function setPrice(address a, address b, uint256 num, uint256 den) internal {
        router.setPrice(a, b, num, den);
    }

    function clearPrice(address a, address b) internal {
        router.clearPrice(a, b);
    }

    // ------------------------------------------------------------------
    // Terms / lifecycle
    // ------------------------------------------------------------------

    function listingRef() internal returns (bytes32) {
        return keccak256(abi.encode("listing", ++_refNonce));
    }

    function terms() internal returns (ITraderVault.Terms memory) {
        return ITraderVault.Terms({
            customer: customer,
            trader: trader,
            baseToken: address(usdc),
            principal: PRINCIPAL,
            durationSeconds: DURATION,
            commissionBps: 2_000,
            maxDrawdownBps: 2_000,
            listingRef: listingRef()
        });
    }

    function termsWithDrawdown(uint16 dd) internal returns (ITraderVault.Terms memory t) {
        t = terms();
        t.maxDrawdownBps = dd;
    }

    /// @dev open (customer) + accept (trader) -> Active
    function openActive() internal returns (uint256 id) {
        return openActiveWith(terms());
    }

    function openActiveWith(ITraderVault.Terms memory t) internal returns (uint256 id) {
        vm.prank(t.customer);
        id = vault.open(t);
        vm.prank(t.trader);
        vault.accept(id);
    }

    /// @dev propose (trader) + fund (customer) -> Active
    function proposeActive() internal returns (uint256 id) {
        vm.prank(trader);
        id = vault.propose(terms());
        vm.prank(customer);
        vault.fund(id);
    }

    function ag(uint256 id) internal view returns (ITraderVault.Agreement memory) {
        return vault.getAgreement(id);
    }

    function vaultBalance(uint256 id, address token) internal view returns (uint256) {
        (address[] memory tokens, uint256[] memory amounts) = vault.getBalances(id);
        for (uint256 i = 0; i < tokens.length; ++i) {
            if (tokens[i] == token) return amounts[i];
        }
        return 0;
    }

    function tokensOf(uint256 id) internal view returns (address[] memory) {
        return vault.getAgreement(id).tokens;
    }

    function now64() internal view returns (uint64) {
        return uint64(block.timestamp);
    }

    function dl() internal view returns (uint64) {
        return now64() + 60;
    }

    // ------------------------------------------------------------------
    // Snapshot / assertions
    // ------------------------------------------------------------------

    function snapshot(uint256 id) internal view returns (Snapshot memory s) {
        (address[] memory tokens, uint256[] memory amounts) = vault.getBalances(id);
        s.agreement = keccak256(abi.encode(vault.getAgreement(id)));
        s.balances = keccak256(abi.encode(tokens, amounts));
        s.vaultUsdc = usdc.balanceOf(address(vault));
        s.vaultAlt = alt.balanceOf(address(vault));
        s.routerUsdc = usdc.balanceOf(address(router));
        s.routerAlt = alt.balanceOf(address(router));
    }

    function assertUnchanged(uint256 id, Snapshot memory s) internal view {
        Snapshot memory n = snapshot(id);
        assertEq(n.agreement, s.agreement, "agreement changed");
        assertEq(n.balances, s.balances, "balances changed");
        assertEq(n.vaultUsdc, s.vaultUsdc, "vault usdc changed");
        assertEq(n.vaultAlt, s.vaultAlt, "vault alt changed");
        assertEq(n.routerUsdc, s.routerUsdc, "router usdc changed");
        assertEq(n.routerAlt, s.routerAlt, "router alt changed");
    }

    function assertBalances(uint256 id, address[] memory tokens, uint256[] memory amounts) internal view {
        (address[] memory t, uint256[] memory a) = vault.getBalances(id);
        assertEq(t, tokens, "token list");
        assertEq(a, amounts, "amount list");
    }

    function assertStatus(uint256 id, ITraderVault.AgreementStatus st) internal view {
        assertEq(uint8(vault.getAgreement(id).status), uint8(st), "status");
    }

    function expectVaultError(bytes4 selector) internal {
        vm.expectRevert(selector);
    }

    function expectRouterError() internal {
        vm.expectPartialRevert(ITraderVault.RouterError.selector);
    }

    // ------------------------------------------------------------------
    // Küçük dizi yardımcıları
    // ------------------------------------------------------------------

    function none() internal pure returns (uint256[] memory a) {
        a = new uint256[](0);
    }

    function one(uint256 x) internal pure returns (uint256[] memory a) {
        a = new uint256[](1);
        a[0] = x;
    }

    function two(uint256 x, uint256 y) internal pure returns (uint256[] memory a) {
        a = new uint256[](2);
        a[0] = x;
        a[1] = y;
    }

    function addrs1(address x) internal pure returns (address[] memory a) {
        a = new address[](1);
        a[0] = x;
    }

    function addrs2(address x, address y) internal pure returns (address[] memory a) {
        a = new address[](2);
        a[0] = x;
        a[1] = y;
    }
}
