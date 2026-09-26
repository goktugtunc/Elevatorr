// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {BaseTest} from "./Base.t.sol";
import {ITraderVault} from "../src/interfaces/ITraderVault.sol";
import {MockRouter} from "../src/mocks/MockRouter.sol";
import {TestToken} from "../src/mocks/TestToken.sol";

contract TraderVaultTradeTest is BaseTest {
    // Rust 20: token_allow_list_is_enforced
    function test_allowList_isEnforced() public {
        uint256 id = openActive();
        address rogue = address(newToken(6, false, false));
        setPrice(address(usdc), rogue, 1, 1);
        setPrice(rogue, address(usdc), 1, 1);
        uint64 d = dl();

        vm.startPrank(trader);
        expectVaultError(ITraderVault.TokenNotAllowed.selector);
        vault.trade(id, address(usdc), rogue, U6, 1, d);
        expectVaultError(ITraderVault.TokenNotAllowed.selector);
        vault.trade(id, rogue, address(usdc), U6, 1, d);
        expectVaultError(ITraderVault.TokenNotAllowed.selector);
        vault.trade(id, address(usdc), address(usdc), U6, 1, d);
        vm.stopPrank();

        // uçuş sırasında de-list: o tokena yeni trade engellenir
        vm.prank(admin);
        vault.setToken(address(eurc), false, false);
        vm.prank(trader);
        expectVaultError(ITraderVault.TokenNotAllowed.selector);
        vault.trade(id, address(usdc), address(eurc), U6, 1, d);

        // base'in de-list'i yeni anlaşma ve fund'ı engeller; mevcut aktif anlaşma settle olabilir
        vm.prank(trader);
        uint256 proposed = vault.propose(terms());
        vm.prank(admin);
        vault.setToken(address(usdc), false, false);
        vm.prank(customer);
        expectVaultError(ITraderVault.TokenNotAllowed.selector);
        vault.open(terms());
        vm.prank(customer);
        expectVaultError(ITraderVault.TokenNotAllowed.selector);
        vault.fund(proposed);
        vm.prank(customer);
        vault.settle(id, none());
        assertStatus(id, ITraderVault.AgreementStatus.Settled);
    }

    // Rust 21: max_tokens_bound_and_slot_reuse
    function test_trade_maxTokensBoundAndSlotReuse() public {
        uint256 id = openActiveWith(termsWithDrawdown(10_000));
        uint8 maxTokens = vault.MAX_TOKENS();
        address[] memory extra = new address[](maxTokens);
        for (uint256 i = 0; i < maxTokens; ++i) {
            extra[i] = address(newToken(6, true, false));
            setPrice(address(usdc), extra[i], 1, 1);
            setPrice(extra[i], address(usdc), 1, 1);
        }
        uint64 d = dl();
        vm.startPrank(trader);
        // base + 5 = MAX_TOKENS
        for (uint256 i = 0; i + 1 < maxTokens; ++i) {
            vault.trade(id, address(usdc), extra[i], 10 * U6, 10 * U6, d);
        }
        assertEq(tokensOf(id).length, maxTokens);
        address sixth = extra[maxTokens - 1];
        expectVaultError(ITraderVault.TooManyTokens.selector);
        vault.trade(id, address(usdc), sixth, 10 * U6, 10 * U6, d);
        // zaten tutulan tokena trade serbest
        vault.trade(id, address(usdc), extra[0], 10 * U6, 10 * U6, d);
        assertEq(vaultBalance(id, extra[0]), 20 * U6);
        // tamamen çıkış slotu serbest bırakır, sıra korunur
        vault.trade(id, extra[1], address(usdc), 10 * U6, 10 * U6, d);
        address[] memory toks = tokensOf(id);
        assertEq(toks.length, maxTokens - 1);
        assertEq(toks[0], address(usdc));
        assertEq(toks[1], extra[0]);
        assertEq(toks[2], extra[2]);
        assertEq(toks[3], extra[3]);
        assertEq(toks[4], extra[4]);
        assertEq(vaultBalance(id, extra[1]), 0);
        vault.trade(id, address(usdc), sixth, 10 * U6, 10 * U6, d);
        toks = tokensOf(id);
        assertEq(toks.length, maxTokens);
        assertEq(toks[5], sixth);
        assertEq(vault.valueInBase(id), PRINCIPAL);
        vm.stopPrank();

        // settle hepsini sırayla likide eder
        uint256[] memory minOuts = new uint256[](5);
        minOuts[0] = 20 * U6;
        for (uint256 i = 1; i < 5; ++i) {
            minOuts[i] = 10 * U6;
        }
        vm.prank(customer);
        vault.settle(id, minOuts);
        assertEq(usdc.balanceOf(customer), PRINCIPAL);
        for (uint256 i = 0; i < maxTokens; ++i) {
            assertEq(TestToken(extra[i]).balanceOf(address(vault)), 0);
        }
        assertEq(tokensOf(id), addrs1(address(usdc)));
    }

    // Rust 22: trade_router_slippage_failure_rolls_back
    function test_trade_routerSlippageFailureRollsBack() public {
        uint256 id = openActive();
        Snapshot memory s = snapshot(id);
        // 200 USDC 800 ALT quote eder; 800+1 istemek strict router'ı düşürür
        vm.prank(trader);
        vm.expectRevert(
            abi.encodeWithSelector(
                ITraderVault.RouterError.selector,
                abi.encodeWithSelector(MockRouter.InsufficientOutput.selector, 800 * U18, 800 * U18 + 1)
            )
        );
        vault.trade(id, address(usdc), address(alt), 200 * U6, 800 * U18 + 1, dl());
        assertUnchanged(id, s);
        // router revert nedeni ham taşınır: fiyat yok
        clearPrice(address(usdc), address(alt));
        vm.prank(trader);
        vm.expectRevert(
            abi.encodeWithSelector(
                ITraderVault.RouterError.selector,
                abi.encodeWithSelector(MockRouter.NoPrice.selector, address(usdc), address(alt))
            )
        );
        vault.trade(id, address(usdc), address(alt), 200 * U6, 1, dl());
        assertUnchanged(id, s);
    }

    // Rust 23: trade_recheck_catches_router_that_ignores_min_out
    function test_trade_recheckCatchesLenientRouter() public {
        uint256 id = openActive();
        router.setStrict(false);
        Snapshot memory s = snapshot(id);
        vm.prank(trader);
        expectVaultError(ITraderVault.SlippageExceeded.selector);
        vault.trade(id, address(usdc), address(alt), 200 * U6, 800 * U18 + 1, dl());
        assertUnchanged(id, s);
        // tam minOut vault'un kendi kontrolünden geçer
        vm.prank(trader);
        vault.trade(id, address(usdc), address(alt), 200 * U6, 800 * U18, dl());
        assertEq(vaultBalance(id, address(alt)), 800 * U18);
    }

    // Rust 24: trade_drawdown_breach_rolls_back
    function test_trade_drawdownBreachRollsBack() public {
        uint256 id = openActive(); // dd 20% -> taban 800 USDC
        // Router ALT'ı 4/USDC satar, 8/USDC geri alır (50% kesinti)
        setPrice(address(alt), address(usdc), 1, 8e12);
        uint64 d = dl();
        Snapshot memory s = snapshot(id);
        vm.startPrank(trader);
        // 500 USDC -> 2000 ALT (250 değerinde) => 750 < 800
        expectVaultError(ITraderVault.DrawdownBreached.selector);
        vault.trade(id, address(usdc), address(alt), 500 * U6, 2_000 * U18, d);
        vm.stopPrank();
        assertUnchanged(id, s);
        vm.startPrank(trader);
        // 400 USDC -> 1600 ALT (200 değerinde) => 800 == taban: kabul
        vault.trade(id, address(usdc), address(alt), 400 * U6, 1_600 * U18, d);
        assertEq(vault.valueInBase(id), 800 * U6);
        // zarar getiren her trade tabanı aşar
        expectVaultError(ITraderVault.DrawdownBreached.selector);
        vault.trade(id, address(usdc), address(alt), U6, 4 * U18, d);
        // değeri koruyan trade (ALT -> USDC 1/8) serbest
        vault.trade(id, address(alt), address(usdc), 800 * U18, 100 * U6, d);
        assertEq(vault.valueInBase(id), 800 * U6);
        vm.stopPrank();
    }

    // Rust 25: trade_rejects_token_without_route_back_to_base
    function test_trade_rejectsTokenWithoutRouteToBase() public {
        uint256 id = openActiveWith(termsWithDrawdown(10_000));
        uint64 d = dl();
        // doğrudan: [token, base] yolu yok
        address oneway = address(newToken(6, true, false));
        setPrice(address(usdc), oneway, 1, 1);
        Snapshot memory s = snapshot(id);
        vm.prank(trader);
        expectVaultError(ITraderVault.TokenNotAllowed.selector);
        vault.trade(id, address(usdc), oneway, 300 * U6, 300 * U6, d);
        assertUnchanged(id, s);
        // iki adımlı park: USDC -> ALT serbest, ALT -> iso değil (iso'nun USDC yolu yok)
        address iso = address(newToken(18, true, false));
        setPrice(address(alt), iso, 1, 1);
        setPrice(iso, address(alt), 1, 1);
        vm.prank(trader);
        vault.trade(id, address(usdc), address(alt), 200 * U6, 800 * U18, d);
        s = snapshot(id);
        vm.prank(trader);
        expectVaultError(ITraderVault.TokenNotAllowed.selector);
        vault.trade(id, address(alt), iso, 800 * U18, 800 * U18, d);
        assertUnchanged(id, s);
        // yol açılınca aynı trade geçer ...
        setPrice(iso, address(usdc), 1, 4e12);
        vm.prank(trader);
        vault.trade(id, address(alt), iso, 800 * U18, 800 * U18, d);
        assertEq(vaultBalance(id, iso), 800 * U18);
        assertEq(vaultBalance(id, address(alt)), 0);
        assertEq(vault.valueInBase(id), PRINCIPAL);
        assertEq(tokensOf(id), addrs2(address(usdc), iso));
        // ... base'e satış quote gerektirmez
        vm.prank(trader);
        vault.trade(id, iso, address(usdc), 800 * U18, 200 * U6, d);
        assertEq(vault.valueInBase(id), PRINCIPAL);
        assertEq(tokensOf(id), addrs1(address(usdc)));
    }

    // Rust 26: trade_input_validation
    function test_trade_inputValidation() public {
        vm.prank(customer);
        uint256 funded = vault.open(terms());
        usdc.mint(customer, PRINCIPAL);
        uint256 id = openActive();
        uint64 d = dl();
        vm.startPrank(trader);
        expectVaultError(ITraderVault.WrongStatus.selector);
        vault.trade(funded, address(usdc), address(alt), U6, 1, d);
        expectVaultError(ITraderVault.NotFound.selector);
        vault.trade(99, address(usdc), address(alt), U6, 1, d);
        expectVaultError(ITraderVault.ZeroAmount.selector);
        vault.trade(id, address(usdc), address(alt), 0, 1, d);
        expectVaultError(ITraderVault.ZeroAmount.selector);
        vault.trade(id, address(usdc), address(alt), U6, 0, d);
        expectVaultError(ITraderVault.InsufficientBalance.selector);
        vault.trade(id, address(usdc), address(alt), PRINCIPAL + 1, 1, d);
        expectVaultError(ITraderVault.InsufficientBalance.selector);
        vault.trade(id, address(alt), address(usdc), U6, 1, d);
        // bayat deadline
        expectVaultError(ITraderVault.Expired.selector);
        vault.trade(id, address(usdc), address(alt), U6, 1, now64() - 1);
        // deadline == now geçerli
        vault.trade(id, address(usdc), address(alt), U6, 1, now64());
        vm.stopPrank();
        // endTime'da/sonrasında
        vm.warp(ag(id).endTime);
        vm.prank(trader);
        expectVaultError(ITraderVault.Expired.selector);
        vault.trade(id, address(usdc), address(alt), U6, 1, dl());
    }

    function test_trade_partialExitKeepsTokenListed() public {
        uint256 id = openActive();
        vm.startPrank(trader);
        vault.trade(id, address(usdc), address(alt), 200 * U6, 800 * U18, dl());
        vault.trade(id, address(alt), address(usdc), 400 * U18, 100 * U6, dl());
        vm.stopPrank();
        assertEq(tokensOf(id), addrs2(address(usdc), address(alt)));
        assertBalances(id, addrs2(address(usdc), address(alt)), two(900 * U6, 400 * U18));
        // base'in tamamını satmak base'i listeden çıkarmaz
        vm.prank(trader);
        vault.trade(id, address(usdc), address(alt), 900 * U6, 3_600 * U18, dl());
        assertEq(tokensOf(id)[0], address(usdc));
        assertBalances(id, addrs2(address(usdc), address(alt)), two(0, 4_000 * U18));
        assertEq(vault.valueInBase(id), PRINCIPAL);
    }
}
