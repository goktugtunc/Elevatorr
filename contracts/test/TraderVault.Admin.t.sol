// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {BaseTest} from "./Base.t.sol";
import {ERC1967Proxy} from "@openzeppelin/contracts/proxy/ERC1967/ERC1967Proxy.sol";
import {OwnableUpgradeable} from "@openzeppelin/contracts-upgradeable/access/OwnableUpgradeable.sol";

import {TraderVault} from "../src/TraderVault.sol";
import {ITraderVault} from "../src/interfaces/ITraderVault.sol";
import {MockRouter} from "../src/mocks/MockRouter.sol";

contract TraderVaultAdminTest is BaseTest {
    // Rust 1: constructor_sets_config_and_ids
    function test_initialize_setsConfigAndIds() public view {
        ITraderVault.Config memory cfg = vault.getConfig();
        assertEq(cfg.router, address(router));
        assertEq(cfg.feeRecipient, feeRecipient);
        assertEq(cfg.platformFeeBps, PLATFORM_FEE_BPS);
        assertEq(cfg.settleSlippageBps, SETTLE_SLIPPAGE_BPS);
        assertFalse(cfg.paused);
        assertEq(cfg.pendingRouter, address(0));
        assertEq(cfg.routerActivationTime, 0);
        assertEq(cfg.routerDelay, ROUTER_DELAY);
        assertEq(vault.owner(), admin);
        assertEq(vault.pendingOwner(), address(0));
        assertEq(vault.nextId(), 1);
        assertEq(vault.nextReservationId(), 1);
        assertFalse(vault.paused());
        // sabitler
        assertEq(vault.MAX_TOKENS(), 6);
        assertEq(vault.MIN_DURATION(), 86_400);
        assertEq(vault.MAX_DURATION(), 94_608_000);
        assertEq(vault.KEEPER_GRACE(), KEEPER_GRACE);
        assertEq(vault.MAX_ROUTER_DELAY(), 2_592_000);
        assertEq(vault.DEFAULT_ROUTER_DELAY(), 86_400);
    }

    // Rust 2
    function test_initialize_revertsPlatformFeeAboveCap() public {
        expectVaultError(ITraderVault.InvalidTerms.selector);
        deployVaultWith(admin, address(router), feeRecipient, 1_001, 100, ROUTER_DELAY);
        // sınır kabul
        TraderVault v = deployVaultWith(admin, address(router), feeRecipient, 1_000, 100, ROUTER_DELAY);
        assertEq(v.getConfig().platformFeeBps, 1_000);
    }

    // Rust 3
    function test_initialize_revertsSlippageAboveCap() public {
        expectVaultError(ITraderVault.InvalidTerms.selector);
        deployVaultWith(admin, address(router), feeRecipient, 0, 5_001, ROUTER_DELAY);
        TraderVault v = deployVaultWith(admin, address(router), feeRecipient, 0, 5_000, ROUTER_DELAY);
        assertEq(v.getConfig().settleSlippageBps, 5_000);
    }

    function test_initialize_revertsZeroAddressesAndBadRouterAndDelay() public {
        expectVaultError(ITraderVault.ZeroAddress.selector);
        deployVaultWith(address(0), address(router), feeRecipient, 100, 100, ROUTER_DELAY);
        expectVaultError(ITraderVault.ZeroAddress.selector);
        deployVaultWith(admin, address(router), address(0), 100, 100, ROUTER_DELAY);
        expectVaultError(ITraderVault.InvalidRouter.selector);
        deployVaultWith(admin, stranger, feeRecipient, 100, 100, ROUTER_DELAY);
        expectVaultError(ITraderVault.InvalidTerms.selector);
        deployVaultWith(admin, address(router), feeRecipient, 100, 100, 2_592_001);
        TraderVault v = deployVaultWith(admin, address(router), feeRecipient, 100, 100, 2_592_000);
        assertEq(v.getConfig().routerDelay, 2_592_000);
        // routerDelay 0 da geçerli (testnet)
        v = deployVaultWith(admin, address(router), feeRecipient, 100, 100, 0);
        assertEq(v.getConfig().routerDelay, 0);
    }

    // Rust 4: admin_token_allow_list
    function test_setToken_allowListAndEvent() public {
        address t = address(newToken(6, false, false));
        // newToken zaten setToken(false,false) çağırdı; bilinmeyen adres gibi {false,false}
        ITraderVault.TokenInfo memory info = vault.isTokenAllowed(t);
        assertFalse(info.allowed);
        assertFalse(info.isBase);
        assertFalse(vault.isTokenAllowed(stranger).allowed);

        vm.expectEmit(address(vault));
        emit ITraderVault.TokenSet(t, true, true);
        vm.prank(admin);
        vault.setToken(t, true, true);
        info = vault.isTokenAllowed(t);
        assertTrue(info.allowed);
        assertTrue(info.isBase);

        // de-listing isBase'i de siler (bayrak ne olursa olsun)
        vm.expectEmit(address(vault));
        emit ITraderVault.TokenSet(t, false, false);
        vm.prank(admin);
        vault.setToken(t, false, true);
        info = vault.isTokenAllowed(t);
        assertFalse(info.allowed);
        assertFalse(info.isBase);

        // allowed && !isBase
        vm.prank(admin);
        vault.setToken(t, true, false);
        info = vault.isTokenAllowed(t);
        assertTrue(info.allowed);
        assertFalse(info.isBase);

        // hatalar
        vm.prank(admin);
        expectVaultError(ITraderVault.ZeroAddress.selector);
        vault.setToken(address(0), true, true);
        vm.prank(admin);
        expectVaultError(ITraderVault.InvalidToken.selector);
        vault.setToken(stranger, true, false);
        // kodsuz adres de-list edilebilir (allowed=false)
        vm.prank(admin);
        vault.setToken(stranger, false, false);
    }

    // Rust 5: admin_config_changes_emit_events_and_validate
    function test_adminConfig_changesEmitEventsAndValidate() public {
        MockRouter newRouter = new MockRouter(address(this));
        address newFee = makeAddr("newFee");

        // router: iki adımlı
        uint64 activation = now64() + ROUTER_DELAY;
        vm.expectEmit(address(vault));
        emit ITraderVault.RouterChangeProposed(address(newRouter), activation);
        vm.prank(admin);
        vault.proposeRouterChange(address(newRouter));
        ITraderVault.Config memory cfg = vault.getConfig();
        assertEq(cfg.pendingRouter, address(newRouter));
        assertEq(cfg.routerActivationTime, activation);
        assertEq(cfg.router, address(router), "router unchanged until apply");

        vm.prank(admin);
        vm.expectRevert(abi.encodeWithSelector(ITraderVault.RouterChangeNotReady.selector, activation));
        vault.applyRouterChange();

        vm.warp(activation);
        vm.expectEmit(address(vault));
        emit ITraderVault.ConfigChanged(
            bytes32("router"), address(newRouter), PLATFORM_FEE_BPS, feeRecipient, false, SETTLE_SLIPPAGE_BPS
        );
        vm.prank(admin);
        vault.applyRouterChange();
        cfg = vault.getConfig();
        assertEq(cfg.router, address(newRouter));
        assertEq(cfg.pendingRouter, address(0));
        assertEq(cfg.routerActivationTime, 0);

        // fees
        vm.expectEmit(address(vault));
        emit ITraderVault.ConfigChanged(bytes32("fees"), address(newRouter), 500, newFee, false, SETTLE_SLIPPAGE_BPS);
        vm.prank(admin);
        vault.setFees(500, newFee);
        cfg = vault.getConfig();
        assertEq(cfg.platformFeeBps, 500);
        assertEq(cfg.feeRecipient, newFee);
        vm.prank(admin);
        expectVaultError(ITraderVault.InvalidTerms.selector);
        vault.setFees(1_001, newFee);
        vm.prank(admin);
        expectVaultError(ITraderVault.ZeroAddress.selector);
        vault.setFees(100, address(0));

        // paused
        vm.expectEmit(address(vault));
        emit ITraderVault.ConfigChanged(bytes32("paused"), address(newRouter), 500, newFee, true, SETTLE_SLIPPAGE_BPS);
        vm.prank(admin);
        vault.setPaused(true);
        assertTrue(vault.getConfig().paused);
        assertTrue(vault.paused());
        vm.prank(admin);
        vault.setPaused(false);
        assertFalse(vault.paused());

        // slippage
        vm.expectEmit(address(vault));
        emit ITraderVault.ConfigChanged(bytes32("slippage"), address(newRouter), 500, newFee, false, 250);
        vm.prank(admin);
        vault.setSettleSlippage(250);
        assertEq(vault.getConfig().settleSlippageBps, 250);
        vm.prank(admin);
        expectVaultError(ITraderVault.InvalidTerms.selector);
        vault.setSettleSlippage(5_001);
    }

    // Rust 6: admin_functions_reject_non_admin
    function test_admin_revertsForNonOwner() public {
        bytes memory err = abi.encodeWithSelector(OwnableUpgradeable.OwnableUnauthorizedAccount.selector, stranger);
        address t = address(alt);

        vm.startPrank(stranger);
        vm.expectRevert(err);
        vault.setToken(t, true, true);
        vm.expectRevert(err);
        vault.proposeRouterChange(address(router));
        vm.expectRevert(err);
        vault.applyRouterChange();
        vm.expectRevert(err);
        vault.cancelRouterChange();
        vm.expectRevert(err);
        vault.setFees(0, t);
        vm.expectRevert(err);
        vault.setPaused(true);
        vm.expectRevert(err);
        vault.setSettleSlippage(10);
        vm.expectRevert(err);
        vault.upgradeToAndCall(address(impl), "");
        vm.stopPrank();

        // hiçbir şey değişmedi
        ITraderVault.Config memory cfg = vault.getConfig();
        assertFalse(cfg.paused);
        assertEq(cfg.router, address(router));
        assertEq(cfg.pendingRouter, address(0));
        assertEq(cfg.platformFeeBps, PLATFORM_FEE_BPS);
        assertEq(cfg.settleSlippageBps, SETTLE_SLIPPAGE_BPS);
        assertTrue(vault.isTokenAllowed(t).isBase);
    }
}
