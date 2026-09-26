// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {MockRouter} from "../src/mocks/MockRouter.sol";
import {TestToken} from "../src/mocks/TestToken.sol";

contract MockRouterTest is Test {
    uint256 internal constant U6 = 1e6;
    uint256 internal constant U18 = 1e18;

    address internal owner = makeAddr("owner");
    address internal user = makeAddr("user");
    address internal stranger = makeAddr("stranger");
    MockRouter internal router;
    TestToken internal usdc;
    TestToken internal alt;
    TestToken internal eurc;

    function setUp() public {
        vm.warp(1_700_000_000);
        router = new MockRouter(owner);
        usdc = new TestToken("USDC", "USDC", 6, address(this));
        alt = new TestToken("ALT", "ALT", 18, address(this));
        eurc = new TestToken("EURC", "EURC", 6, address(this));
        usdc.mint(address(router), 1e9 * U6);
        alt.mint(address(router), 1e9 * U18);
        eurc.mint(address(router), 1e9 * U6);
        vm.startPrank(owner);
        router.setPrice(address(usdc), address(alt), 4e12, 1);
        router.setPrice(address(alt), address(usdc), 1, 4e12);
        router.setPrice(address(usdc), address(eurc), 9, 10);
        vm.stopPrank();
        usdc.mint(user, 1_000 * U6);
        vm.prank(user);
        usdc.approve(address(router), type(uint256).max);
    }

    function _path(address a, address b) internal pure returns (address[] memory p) {
        p = new address[](2);
        p[0] = a;
        p[1] = b;
    }

    // M1: quote_and_swap — girdi msg.sender'dan çekilir
    function test_mockRouter_quoteAndSwap() public {
        uint256[] memory q = router.getAmountsOut(200 * U6, _path(address(usdc), address(alt)));
        assertEq(q.length, 2);
        assertEq(q[0], 200 * U6);
        assertEq(q[1], 800 * U18);
        // çok adımlı yol
        address[] memory p3 = new address[](3);
        p3[0] = address(alt);
        p3[1] = address(usdc);
        p3[2] = address(eurc);
        q = router.getAmountsOut(800 * U18, p3);
        assertEq(q[1], 200 * U6);
        assertEq(q[2], 180 * U6);

        vm.expectEmit(address(router));
        emit MockRouter.PriceSet(address(usdc), address(alt), 4e12, 1);
        vm.prank(owner);
        router.setPrice(address(usdc), address(alt), 4e12, 1);

        vm.prank(user);
        uint256[] memory amounts = router.swapExactTokensForTokens(
            200 * U6, 800 * U18, _path(address(usdc), address(alt)), user, block.timestamp
        );
        assertEq(amounts[1], 800 * U18);
        assertEq(usdc.balanceOf(user), 800 * U6);
        assertEq(alt.balanceOf(user), 800 * U18);
        assertEq(usdc.balanceOf(address(router)), 1e9 * U6 + 200 * U6);
        // `to` farklı olabilir
        vm.prank(user);
        router.swapExactTokensForTokens(100 * U6, 1, _path(address(usdc), address(alt)), stranger, block.timestamp + 10);
        assertEq(alt.balanceOf(stranger), 400 * U18);
        // approve yoksa transferFrom düşer
        vm.prank(stranger);
        vm.expectRevert();
        router.swapExactTokensForTokens(1, 1, _path(address(alt), address(usdc)), stranger, block.timestamp);
        // withdraw
        vm.prank(owner);
        router.withdraw(address(usdc), owner, 5 * U6);
        assertEq(usdc.balanceOf(owner), 5 * U6);
        vm.prank(owner);
        vm.expectRevert(MockRouter.InvalidAmount.selector);
        router.withdraw(address(usdc), owner, 0);
    }

    // M2: errors
    function test_mockRouter_errors() public {
        vm.expectRevert(abi.encodeWithSelector(MockRouter.NoPrice.selector, address(eurc), address(usdc)));
        router.getAmountsOut(U6, _path(address(eurc), address(usdc)));
        vm.expectRevert(MockRouter.InvalidAmount.selector);
        router.getAmountsOut(0, _path(address(usdc), address(alt)));
        address[] memory short_ = new address[](1);
        short_[0] = address(usdc);
        vm.expectRevert(MockRouter.InvalidPath.selector);
        router.getAmountsOut(U6, short_);
        vm.prank(owner);
        vm.expectRevert(MockRouter.InvalidAmount.selector);
        router.setPrice(address(usdc), address(alt), 0, 1);
        vm.prank(owner);
        vm.expectRevert(MockRouter.InvalidAmount.selector);
        router.setPrice(address(usdc), address(alt), 1, 0);

        vm.startPrank(user);
        vm.expectRevert(abi.encodeWithSelector(MockRouter.InsufficientOutput.selector, 800 * U18, 800 * U18 + 1));
        router.swapExactTokensForTokens(
            200 * U6, 800 * U18 + 1, _path(address(usdc), address(alt)), user, block.timestamp
        );
        vm.expectRevert(MockRouter.DeadlineExpired.selector);
        router.swapExactTokensForTokens(200 * U6, 1, _path(address(usdc), address(alt)), user, block.timestamp - 1);
        vm.expectRevert(abi.encodeWithSelector(MockRouter.NoPrice.selector, address(eurc), address(usdc)));
        router.swapExactTokensForTokens(U6, 1, _path(address(eurc), address(usdc)), user, block.timestamp);
        vm.stopPrank();

        // lenient mod: min yok sayılır
        vm.expectEmit(address(router));
        emit MockRouter.StrictSet(false);
        vm.prank(owner);
        router.setStrict(false);
        assertFalse(router.strict());
        vm.prank(user);
        uint256[] memory amounts = router.swapExactTokensForTokens(
            200 * U6, 800 * U18 + 1, _path(address(usdc), address(alt)), user, block.timestamp
        );
        assertEq(amounts[1], 800 * U18);
        // clearPrice -> NoPrice
        vm.expectEmit(address(router));
        emit MockRouter.PriceCleared(address(usdc), address(alt));
        vm.prank(owner);
        router.clearPrice(address(usdc), address(alt));
        vm.expectRevert(abi.encodeWithSelector(MockRouter.NoPrice.selector, address(usdc), address(alt)));
        router.getAmountsOut(U6, _path(address(usdc), address(alt)));
        (uint256 num, uint256 den) = router.prices(address(usdc), address(alt));
        assertEq(num, 0);
        assertEq(den, 0);
    }

    // M3: admin_only
    function test_mockRouter_onlyOwner() public {
        bytes memory err = abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, stranger);
        vm.startPrank(stranger);
        vm.expectRevert(err);
        router.setPrice(address(usdc), address(alt), 1, 1);
        vm.expectRevert(err);
        router.clearPrice(address(usdc), address(alt));
        vm.expectRevert(err);
        router.setStrict(false);
        vm.expectRevert(err);
        router.withdraw(address(usdc), stranger, 1);
        vm.stopPrank();
        assertTrue(router.strict());
        assertEq(router.owner(), owner);
    }
}
