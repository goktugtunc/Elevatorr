// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {BaseTest} from "./Base.t.sol";
import {IERC20Errors} from "@openzeppelin/contracts/interfaces/draft-IERC6093.sol";
import {ReentrancyGuardUpgradeable} from "@openzeppelin/contracts-upgradeable/utils/ReentrancyGuardUpgradeable.sol";
import {OwnableUpgradeable} from "@openzeppelin/contracts-upgradeable/access/OwnableUpgradeable.sol";

import {TraderVault} from "../src/TraderVault.sol";
import {ITraderVault} from "../src/interfaces/ITraderVault.sol";
import {TestToken} from "../src/mocks/TestToken.sol";
import {ReentrantToken} from "./mocks/ReentrantToken.sol";
import {MaliciousRouter} from "./mocks/MaliciousRouter.sol";

contract TraderVaultSecurityTest is BaseTest {
    bytes internal REENTRANT = abi.encodeWithSelector(ReentrancyGuardUpgradeable.ReentrancyGuardReentrantCall.selector);

    // ------------------------------------------------------------------
    // Yardımcılar
    // ------------------------------------------------------------------

    /// @dev Vault'u MaliciousRouter ile yeniden kurar (usdc base, alt allowed); müşteriye P basar.
    function _maliciousSetup() internal returns (TraderVault v, MaliciousRouter mr) {
        mr = new MaliciousRouter();
        v = deployVault(address(mr));
        vm.startPrank(admin);
        v.setToken(address(usdc), true, true);
        v.setToken(address(alt), true, false);
        vm.stopPrank();
        mr.setPrice(address(usdc), address(alt), 4e12, 1);
        mr.setPrice(address(alt), address(usdc), 1, 4e12);
        usdc.mint(address(mr), LIQ * U6);
        alt.mint(address(mr), LIQ * U18);
        vm.prank(customer);
        usdc.approve(address(v), type(uint256).max);
    }

    function _openActiveOn(TraderVault v) internal returns (uint256 id) {
        ITraderVault.Terms memory t = terms();
        vm.prank(customer);
        id = v.open(t);
        vm.prank(trader);
        v.accept(id);
    }

    /// @dev ReentrantToken'ı allow-list'e alır, router'a likidite basar ve 1:1 fiyatlar.
    function _reentrantToken() internal returns (ReentrantToken rt) {
        rt = new ReentrantToken("Reentrant", "RNT", 6, address(this));
        rt.mint(address(router), LIQ * U6);
        vm.prank(admin);
        vault.setToken(address(rt), true, false);
        setPrice(address(usdc), address(rt), 1, 1);
        setPrice(address(rt), address(usdc), 1, 1);
    }

    function _snapshotAll(uint256 id, address extra) internal view returns (bytes32) {
        (address[] memory t, uint256[] memory a) = vault.getBalances(id);
        return keccak256(
            abi.encode(
                vault.getAgreement(id),
                t,
                a,
                usdc.balanceOf(address(vault)),
                alt.balanceOf(address(vault)),
                TestToken(extra).balanceOf(address(vault)),
                usdc.balanceOf(customer),
                TestToken(extra).balanceOf(customer)
            )
        );
    }

    // ------------------------------------------------------------------
    // Reentrancy
    // ------------------------------------------------------------------

    function test_reentrancy_tokenReentersTradeDuringSwap() public {
        uint256 id = openActiveWith(termsWithDrawdown(10_000));
        ReentrantToken rt = _reentrantToken();
        bytes memory reenter = abi.encodeCall(vault.trade, (id, address(usdc), address(rt), 10 * U6, 1, dl()));
        bytes32 s = _snapshotAll(id, address(rt));

        // bubble: router'ın token transferi guard revert'ini taşır -> RouterError(guard)
        rt.arm(address(vault), reenter, true);
        vm.prank(trader);
        vm.expectRevert(abi.encodeWithSelector(ITraderVault.RouterError.selector, REENTRANT));
        vault.trade(id, address(usdc), address(rt), 100 * U6, 100 * U6, dl());
        assertEq(_snapshotAll(id, address(rt)), s, "state changed");

        // record: iç çağrı başarısız kaydedilir, dış trade normal biter (tek trade muhasebesi)
        rt.arm(address(vault), reenter, false);
        vm.prank(trader);
        vault.trade(id, address(usdc), address(rt), 100 * U6, 100 * U6, dl());
        assertTrue(rt.fired());
        assertFalse(rt.lastOk());
        assertEq(rt.lastReturn(), REENTRANT);
        assertEq(vaultBalance(id, address(rt)), 100 * U6);
        assertEq(vaultBalance(id, address(usdc)), 900 * U6);
    }

    function test_reentrancy_routerReentersTrade() public {
        (TraderVault v, MaliciousRouter mr) = _maliciousSetup();
        uint256 id = _openActiveOn(v);
        mr.setMode(MaliciousRouter.Mode.REENTER);
        // swap içinde vault.trade -> guard
        mr.setReenter(address(v), abi.encodeCall(v.trade, (id, address(usdc), address(alt), U6, 1, dl())), true);
        vm.prank(trader);
        vm.expectRevert(abi.encodeWithSelector(ITraderVault.RouterError.selector, REENTRANT));
        v.trade(id, address(usdc), address(alt), 200 * U6, 800 * U18, dl());
        assertEq(usdc.balanceOf(address(v)), PRINCIPAL);
        assertEq(alt.balanceOf(address(v)), 0);
        // swap içinde vault.settle (trader olarak değil; router taraf değil) -> yine guard, NotExpired'dan önce
        mr.setReenter(address(v), abi.encodeCall(v.settle, (id, none())), true);
        vm.prank(trader);
        vm.expectRevert(abi.encodeWithSelector(ITraderVault.RouterError.selector, REENTRANT));
        v.trade(id, address(usdc), address(alt), 200 * U6, 800 * U18, dl());
        // kayıt modu: dış trade tamamlanır, iç çağrı guard ile düşmüştür
        mr.setReenter(address(v), abi.encodeCall(v.trade, (id, address(usdc), address(alt), U6, 1, dl())), false);
        vm.prank(trader);
        v.trade(id, address(usdc), address(alt), 200 * U6, 800 * U18, dl());
        assertFalse(mr.reenterOk());
        assertEq(mr.reenterReturn(), REENTRANT);
        assertEq(alt.balanceOf(address(v)), 800 * U18);
    }

    function test_reentrancy_routerReentersSettle() public {
        (TraderVault v, MaliciousRouter mr) = _maliciousSetup();
        uint256 id = _openActiveOn(v);
        vm.prank(trader);
        v.trade(id, address(usdc), address(alt), 200 * U6, 800 * U18, dl());
        mr.setMode(MaliciousRouter.Mode.REENTER);
        // settle içindeki swap'ta claim / settle / cancel denemeleri
        bytes[3] memory attempts = [
            abi.encodeCall(v.settle, (id, one(0))),
            abi.encodeCall(v.claim, (id, address(alt))),
            abi.encodeCall(v.cancel, (id))
        ];
        for (uint256 i = 0; i < attempts.length; ++i) {
            mr.setReenter(address(v), attempts[i], true);
            vm.prank(customer);
            vm.expectRevert(abi.encodeWithSelector(ITraderVault.RouterError.selector, REENTRANT));
            v.settle(id, one(200 * U6));
            assertEq(uint8(v.getAgreement(id).status), uint8(ITraderVault.AgreementStatus.Active));
            assertEq(alt.balanceOf(address(v)), 800 * U18);
        }
        // kayıt modu: settle tamamlanır, iç claim guard'a takılmıştır
        mr.setReenter(address(v), abi.encodeCall(v.claim, (id, address(alt))), false);
        vm.prank(customer);
        v.settle(id, one(200 * U6));
        assertFalse(mr.reenterOk());
        assertEq(mr.reenterReturn(), REENTRANT);
        assertEq(usdc.balanceOf(customer), PRINCIPAL);
    }

    function test_reentrancy_tokenReentersSettleDuringInKind() public {
        uint256 id = openActiveWith(termsWithDrawdown(10_000));
        ReentrantToken rt = _reentrantToken();
        vm.prank(trader);
        vault.trade(id, address(usdc), address(rt), 300 * U6, 300 * U6, dl());
        clearPrice(address(rt), address(usdc)); // in-kind yol
        bytes[3] memory attempts = [
            abi.encodeCall(vault.settle, (id, one(0))),
            abi.encodeCall(vault.claim, (id, address(rt))),
            abi.encodeCall(vault.cancel, (id))
        ];
        for (uint256 i = 0; i < attempts.length; ++i) {
            // bubble: _tryTransfer revert'i yutar -> delivered=false, orphan; settle geri alınmaz
            // (token'ın kendi kayıtları da transferle birlikte geri alınır; yalnız gözlemlenebilir sonuç doğrulanır)
            rt.arm(address(vault), attempts[i], true);
            uint256 snap = vm.snapshotState();
            vm.expectEmit(address(vault));
            emit ITraderVault.Unliquidated(id, address(rt), 300 * U6, false);
            vm.prank(customer);
            vault.settle(id, one(0));
            assertTrue(rt.armed(), "bubble reverted the token's own state too");
            assertEq(vaultBalance(id, address(rt)), 300 * U6, "orphan kept");
            assertEq(rt.balanceOf(customer), 0);
            assertEq(usdc.balanceOf(customer), 700 * U6);
            vm.revertToState(snap);
        }
        // record: transfer başarılı, iç çağrı guard'a takılmış, teslim edilmiş
        rt.arm(address(vault), attempts[0], false);
        vm.expectEmit(address(vault));
        emit ITraderVault.Unliquidated(id, address(rt), 300 * U6, true);
        vm.prank(customer);
        vault.settle(id, one(0));
        assertEq(rt.lastReturn(), REENTRANT);
        assertEq(rt.balanceOf(customer), 300 * U6);
        assertEq(tokensOf(id), addrs1(address(usdc)));
    }

    function test_reentrancy_tokenReentersClaim() public {
        uint256 id = openActiveWith(termsWithDrawdown(10_000));
        ReentrantToken rt = _reentrantToken();
        vm.prank(trader);
        vault.trade(id, address(usdc), address(rt), 300 * U6, 300 * U6, dl());
        clearPrice(address(rt), address(usdc));
        // teslimatı düşürüp orphan yarat: iç claim guard'a takılır, bubble transferi düşürür -> delivered=false
        rt.arm(address(vault), abi.encodeCall(vault.claim, (id, address(rt))), true);
        vm.prank(customer);
        vault.settle(id, one(0));
        assertEq(vaultBalance(id, address(rt)), 300 * U6);
        // claim sırasında claim'e yeniden girme: safeTransfer bubble eder -> guard revert
        rt.arm(address(vault), abi.encodeCall(vault.claim, (id, address(rt))), true);
        vm.prank(customer);
        vm.expectRevert(REENTRANT);
        vault.claim(id, address(rt));
        assertEq(vaultBalance(id, address(rt)), 300 * U6);
        assertEq(tokensOf(id), addrs2(address(usdc), address(rt)));
        // kayıt modunda claim biter, iç claim guard'a takılmıştır (çift ödeme yok)
        rt.arm(address(vault), abi.encodeCall(vault.claim, (id, address(rt))), false);
        vm.prank(customer);
        assertEq(vault.claim(id, address(rt)), 300 * U6);
        assertEq(rt.lastReturn(), REENTRANT);
        assertEq(rt.balanceOf(customer), 300 * U6);
        assertEq(rt.balanceOf(address(vault)), 0);
    }

    function test_reentrancy_tokenReentersRelease() public {
        ReentrantToken rt = new ReentrantToken("Reentrant", "RNT", 6, address(this));
        vm.prank(admin);
        vault.setToken(address(rt), true, true);
        rt.mint(customer, PRINCIPAL);
        vm.prank(customer);
        rt.approve(address(vault), type(uint256).max);
        vm.prank(customer);
        uint256 rid = vault.reserve(address(rt), PRINCIPAL, listingRef());
        // release içinde releaseAll / release
        rt.arm(address(vault), abi.encodeCall(vault.releaseAll, (rid)), true);
        vm.prank(customer);
        vm.expectRevert(REENTRANT);
        vault.release(rid, PRINCIPAL / 2);
        assertEq(vault.getReservation(rid).amount, PRINCIPAL);
        assertEq(rt.balanceOf(customer), 0);
        rt.arm(address(vault), abi.encodeCall(vault.release, (rid, 1)), true);
        vm.prank(customer);
        vm.expectRevert(REENTRANT);
        vault.releaseAll(rid);
        // reserve içinde (transferFrom) release
        rt.mint(customer, PRINCIPAL);
        rt.arm(address(vault), abi.encodeCall(vault.release, (rid, 1)), true);
        vm.prank(customer);
        vm.expectRevert(REENTRANT);
        vault.reserve(address(rt), PRINCIPAL, listingRef());
        assertEq(vault.nextReservationId(), 2);
        // kayıt modu: tek ödeme
        rt.arm(address(vault), abi.encodeCall(vault.releaseAll, (rid)), false);
        vm.prank(customer);
        assertEq(vault.releaseAll(rid), PRINCIPAL);
        assertEq(rt.lastReturn(), REENTRANT);
        assertEq(rt.balanceOf(customer), 2 * PRINCIPAL);
        assertEq(rt.balanceOf(address(vault)), 0);
    }

    function test_reentrancy_tokenReentersCancelAndOpen() public {
        ReentrantToken rt = new ReentrantToken("Reentrant", "RNT", 6, address(this));
        vm.prank(admin);
        vault.setToken(address(rt), true, true);
        rt.mint(customer, 2 * PRINCIPAL);
        vm.prank(customer);
        rt.approve(address(vault), type(uint256).max);
        ITraderVault.Terms memory t = terms();
        t.baseToken = address(rt);
        // open içinde (transferFrom) cancel(1) -> guard
        rt.arm(address(vault), abi.encodeCall(vault.cancel, (1)), true);
        vm.prank(customer);
        vm.expectRevert(REENTRANT);
        vault.open(t);
        assertEq(vault.nextId(), 1);
        rt.disarm(); // bubble modunda token state'i de geri alındı, hâlâ armed
        vm.prank(customer);
        uint256 id = vault.open(t);
        // cancel içinde (transfer) cancel -> guard
        rt.arm(address(vault), abi.encodeCall(vault.cancel, (id)), true);
        vm.prank(customer);
        vm.expectRevert(REENTRANT);
        vault.cancel(id);
        assertStatus(id, ITraderVault.AgreementStatus.Funded);
        // kayıt: tek iade
        rt.arm(address(vault), abi.encodeCall(vault.cancel, (id)), false);
        vm.prank(customer);
        vault.cancel(id);
        assertEq(rt.lastReturn(), REENTRANT);
        assertEq(rt.balanceOf(customer), 2 * PRINCIPAL);
        // fund içinde (transferFrom) fund -> guard
        vm.prank(trader);
        uint256 p = vault.propose(t);
        rt.arm(address(vault), abi.encodeCall(vault.fund, (p)), true);
        vm.prank(customer);
        vm.expectRevert(REENTRANT);
        vault.fund(p);
        assertStatus(p, ITraderVault.AgreementStatus.Proposed);
    }

    // ------------------------------------------------------------------
    // Kötü niyetli router modları
    // ------------------------------------------------------------------

    function test_router_reportsMoreThanSent_creditsActualDelta() public {
        (TraderVault v, MaliciousRouter mr) = _maliciousSetup();
        uint256 id = _openActiveOn(v);
        mr.setMode(MaliciousRouter.Mode.REPORT_MORE); // 800 bildirir, 400 gönderir
        vm.prank(trader);
        expectVaultError(ITraderVault.SlippageExceeded.selector);
        v.trade(id, address(usdc), address(alt), 200 * U6, 800 * U18, dl());
        assertEq(alt.balanceOf(address(v)), 0);
        // minOut gerçek farkı kabul ediyorsa: credited = gerçek fark (400), bildirilen (800) değil
        vm.expectEmit(address(v));
        emit ITraderVault.Traded(id, trader, address(usdc), address(alt), 200 * U6, 400 * U18, 900 * U6);
        vm.prank(trader);
        uint256 out = v.trade(id, address(usdc), address(alt), 200 * U6, 400 * U18, dl());
        assertEq(out, 400 * U18);
        (address[] memory toks, uint256[] memory amts) = v.getBalances(id);
        assertEq(toks, addrs2(address(usdc), address(alt)));
        assertEq(amts, two(800 * U6, 400 * U18));
        assertEq(alt.balanceOf(address(v)), 400 * U18, "accounting == holdings");
        // settle'da da aynı: 400 ALT -> 100 bildirilir, 50 gelir; finalValue gerçek üzerinden
        vm.prank(customer);
        v.settle(id, one(50 * U6));
        assertEq(v.getAgreement(id).finalValue, 850 * U6);
        assertEq(usdc.balanceOf(customer), 850 * U6);
        assertEq(usdc.balanceOf(address(v)), 0);
    }

    function test_router_sendsMoreThanReported_creditsReported() public {
        (TraderVault v, MaliciousRouter mr) = _maliciousSetup();
        uint256 id = _openActiveOn(v);
        mr.setMode(MaliciousRouter.Mode.SEND_MORE); // 800 bildirir, 1600 gönderir
        vm.prank(trader);
        uint256 out = v.trade(id, address(usdc), address(alt), 200 * U6, 800 * U18, dl());
        assertEq(out, 800 * U18);
        assertEq(_bal(v, id, address(alt)), 800 * U18, "credited = reported");
        assertEq(alt.balanceOf(address(v)), 1_600 * U18, "vault holds >= accounting");
        // settle: 800 ALT -> 200 bildirilir, 400 gelir; credited = 200
        vm.prank(customer);
        v.settle(id, one(200 * U6));
        assertEq(v.getAgreement(id).finalValue, PRINCIPAL);
        assertEq(usdc.balanceOf(customer), PRINCIPAL);
        assertEq(usdc.balanceOf(address(v)), 200 * U6, "excess stays in vault, never credited");
    }

    function _bal(TraderVault v, uint256 id, address token) internal view returns (uint256) {
        (address[] memory t, uint256[] memory a) = v.getBalances(id);
        for (uint256 i = 0; i < t.length; ++i) {
            if (t[i] == token) return a[i];
        }
        return 0;
    }

    function test_router_ignoresMin_vaultRechecks() public {
        (TraderVault v, MaliciousRouter mr) = _maliciousSetup();
        uint256 id = _openActiveOn(v);
        mr.setMode(MaliciousRouter.Mode.IGNORE_MIN);
        vm.prank(trader);
        expectVaultError(ITraderVault.SlippageExceeded.selector);
        v.trade(id, address(usdc), address(alt), 200 * U6, 800 * U18 + 1, dl());
        vm.prank(trader);
        v.trade(id, address(usdc), address(alt), 200 * U6, 800 * U18, dl());
        // settle'da parti minOut'u da yeniden kontrol edilir
        vm.prank(customer);
        expectVaultError(ITraderVault.SlippageExceeded.selector);
        v.settle(id, one(200 * U6 + 1));
        assertEq(uint8(v.getAgreement(id).status), uint8(ITraderVault.AgreementStatus.Active));
    }

    function test_router_doesNotPullInput_reverts() public {
        (TraderVault v, MaliciousRouter mr) = _maliciousSetup();
        uint256 id = _openActiveOn(v);
        mr.setMode(MaliciousRouter.Mode.NO_PULL);
        vm.prank(trader);
        vm.expectRevert(abi.encodeWithSelector(ITraderVault.RouterError.selector, bytes("input-mismatch")));
        v.trade(id, address(usdc), address(alt), 200 * U6, 800 * U18, dl());
        assertEq(usdc.balanceOf(address(v)), PRINCIPAL);
        assertEq(alt.balanceOf(address(v)), 0);
        // settle yolunda da
        mr.setMode(MaliciousRouter.Mode.NORMAL);
        vm.prank(trader);
        v.trade(id, address(usdc), address(alt), 200 * U6, 800 * U18, dl());
        mr.setMode(MaliciousRouter.Mode.NO_PULL);
        vm.prank(customer);
        vm.expectRevert(abi.encodeWithSelector(ITraderVault.RouterError.selector, bytes("input-mismatch")));
        v.settle(id, one(200 * U6));
        assertEq(uint8(v.getAgreement(id).status), uint8(ITraderVault.AgreementStatus.Active));
    }

    function test_router_quoteReverts_countsAsZero() public {
        (TraderVault v, MaliciousRouter mr) = _maliciousSetup();
        uint256 id = _openActiveOn(v);
        vm.prank(trader);
        v.trade(id, address(usdc), address(alt), 200 * U6, 800 * U18, dl());
        assertEq(v.valueInBase(id), PRINCIPAL);
        mr.setMode(MaliciousRouter.Mode.GAS_BURN);
        assertEq(v.valueInBase(id), 800 * U6);
        (,, uint256[] memory q, uint256 est,,) = v.previewSettle(id);
        assertEq(q, one(0));
        assertEq(est, 800 * U6);
        // quote yok -> base'e dönüş kontrolü yeni trade'i engeller
        vm.prank(trader);
        expectVaultError(ITraderVault.TokenNotAllowed.selector);
        v.trade(id, address(usdc), address(alt), 100 * U6, 400 * U18, dl());
        // settle: in-kind teslim
        vm.expectEmit(address(v));
        emit ITraderVault.Unliquidated(id, address(alt), 800 * U18, true);
        vm.prank(customer);
        v.settle(id, one(0));
        assertEq(v.getAgreement(id).finalValue, 800 * U6);
        assertEq(alt.balanceOf(customer), 800 * U18);
        assertEq(usdc.balanceOf(customer), 800 * U6);
    }

    function test_approve_isResetAfterSwap() public {
        uint256 id = openActive();
        vm.prank(trader);
        vault.trade(id, address(usdc), address(alt), 200 * U6, 800 * U18, dl());
        assertEq(usdc.allowance(address(vault), address(router)), 0);
        assertEq(alt.allowance(address(vault), address(router)), 0);
        vm.prank(customer);
        vault.settle(id, one(200 * U6));
        assertEq(alt.allowance(address(vault), address(router)), 0);
    }

    function test_open_withoutApprove_revertsTransferFailed() public {
        address fresh = makeAddr("fresh");
        usdc.mint(fresh, PRINCIPAL);
        ITraderVault.Terms memory t = terms();
        t.customer = fresh;
        // OZ ERC20 allowance yokken revert eder; SafeERC20 nedeni ham taşır
        vm.prank(fresh);
        vm.expectRevert(
            abi.encodeWithSelector(IERC20Errors.ERC20InsufficientAllowance.selector, address(vault), 0, PRINCIPAL)
        );
        vault.open(t);
        vm.prank(fresh);
        vm.expectPartialRevert(IERC20Errors.ERC20InsufficientAllowance.selector);
        vault.reserve(address(usdc), PRINCIPAL, listingRef());
        assertEq(vault.nextId(), 1);
        assertEq(vault.nextReservationId(), 1);
        // approve sonrası geçer
        vm.prank(fresh);
        usdc.approve(address(vault), PRINCIPAL);
        vm.prank(fresh);
        vault.open(t);
        assertEq(usdc.balanceOf(fresh), 0);
    }

    // ------------------------------------------------------------------
    // Fee snapshot, router gecikmesi, Ownable2Step
    // ------------------------------------------------------------------

    function test_feeSnapshot_setFeesAfterOpenDoesNotAffectAgreement() public {
        uint256 id = openActive(); // fee 100
        assertEq(ag(id).platformFeeBps, 100);
        vm.prank(admin);
        vault.setFees(1_000, feeRecipient);
        assertEq(ag(id).platformFeeBps, 100, "snapshot");
        vm.prank(trader);
        vault.trade(id, address(usdc), address(alt), 200 * U6, 800 * U18, dl());
        setPrice(address(alt), address(usdc), 1, 2e12); // kâr 200
        vm.prank(customer);
        vault.settle(id, one(400 * U6));
        assertEq(ag(id).platformFee, 2 * U6, "1% of profit, not 10%");
        assertEq(ag(id).traderFee, 40 * U6);
        // yeni anlaşma 10% snapshot'lar
        usdc.mint(customer, PRINCIPAL);
        uint256 id2 = openActive();
        assertEq(ag(id2).platformFeeBps, 1_000);
        vm.prank(trader);
        vault.trade(id2, address(usdc), address(alt), 200 * U6, 800 * U18, dl());
        // feeRecipient settle anındaki config'ten okunur
        address newFee = makeAddr("newFee");
        vm.prank(admin);
        vault.setFees(0, newFee);
        vm.prank(customer);
        vault.settle(id2, one(400 * U6));
        assertEq(ag(id2).platformFee, 20 * U6, "10% of profit");
        assertEq(usdc.balanceOf(newFee), 20 * U6);
        assertEq(usdc.balanceOf(feeRecipient), 2 * U6);
    }

    function test_routerDelay_proposeApplyCancel() public {
        MaliciousRouter other = new MaliciousRouter();
        vm.startPrank(admin);
        expectVaultError(ITraderVault.NoPendingRouterChange.selector);
        vault.applyRouterChange();
        expectVaultError(ITraderVault.NoPendingRouterChange.selector);
        vault.cancelRouterChange();
        expectVaultError(ITraderVault.ZeroAddress.selector);
        vault.proposeRouterChange(address(0));
        expectVaultError(ITraderVault.InvalidRouter.selector);
        vault.proposeRouterChange(stranger);

        uint64 activation = now64() + ROUTER_DELAY;
        vault.proposeRouterChange(address(other));
        vm.expectRevert(abi.encodeWithSelector(ITraderVault.RouterChangeNotReady.selector, activation));
        vault.applyRouterChange();
        vm.warp(activation - 1);
        vm.expectRevert(abi.encodeWithSelector(ITraderVault.RouterChangeNotReady.selector, activation));
        vault.applyRouterChange();
        // yeni teklif eskisini ezer (süre yeniden başlar)
        MaliciousRouter third = new MaliciousRouter();
        uint64 activation2 = now64() + ROUTER_DELAY;
        vault.proposeRouterChange(address(third));
        assertEq(vault.getConfig().pendingRouter, address(third));
        assertEq(vault.getConfig().routerActivationTime, activation2);
        vm.warp(activation);
        vm.expectRevert(abi.encodeWithSelector(ITraderVault.RouterChangeNotReady.selector, activation2));
        vault.applyRouterChange();
        // iptal
        vm.expectEmit(address(vault));
        emit ITraderVault.RouterChangeCancelled(address(third));
        vault.cancelRouterChange();
        assertEq(vault.getConfig().pendingRouter, address(0));
        assertEq(vault.getConfig().routerActivationTime, 0);
        assertEq(vault.getConfig().router, address(router));
        // yeniden teklif + uygulama
        vault.proposeRouterChange(address(other));
        vm.warp(now64() + ROUTER_DELAY);
        vm.expectEmit(address(vault));
        emit ITraderVault.ConfigChanged(
            bytes32("router"), address(other), PLATFORM_FEE_BPS, feeRecipient, false, SETTLE_SLIPPAGE_BPS
        );
        vault.applyRouterChange();
        assertEq(vault.getConfig().router, address(other));
        vm.stopPrank();
        // routerDelay = 0 ile anında uygulanır
        TraderVault fast = deployVaultWith(admin, address(router), feeRecipient, 100, 100, 0);
        vm.startPrank(admin);
        fast.proposeRouterChange(address(other));
        fast.applyRouterChange();
        assertEq(fast.getConfig().router, address(other));
        vm.stopPrank();
    }

    function test_ownable2Step_transferAndAccept() public {
        address newOwner = makeAddr("newOwner");
        vm.prank(admin);
        vault.transferOwnership(newOwner);
        assertEq(vault.owner(), admin);
        assertEq(vault.pendingOwner(), newOwner);
        // bekleyen owner admin fn çağıramaz
        vm.prank(newOwner);
        vm.expectRevert(abi.encodeWithSelector(OwnableUpgradeable.OwnableUnauthorizedAccount.selector, newOwner));
        vault.setPaused(true);
        // yabancı kabul edemez
        vm.prank(stranger);
        vm.expectRevert(abi.encodeWithSelector(OwnableUpgradeable.OwnableUnauthorizedAccount.selector, stranger));
        vault.acceptOwnership();
        vm.prank(newOwner);
        vault.acceptOwnership();
        assertEq(vault.owner(), newOwner);
        assertEq(vault.pendingOwner(), address(0));
        vm.prank(admin);
        vm.expectRevert(abi.encodeWithSelector(OwnableUpgradeable.OwnableUnauthorizedAccount.selector, admin));
        vault.setPaused(true);
        vm.prank(newOwner);
        vault.setPaused(true);
        assertTrue(vault.paused());
        // admin settle yolu yeni owner'a geçer
        vm.prank(newOwner);
        vault.setPaused(false);
        uint256 id = openActive();
        vm.warp(ag(id).endTime);
        vm.prank(admin);
        expectVaultError(ITraderVault.NotExpired.selector);
        vault.settle(id, none());
        vm.prank(newOwner);
        vault.settle(id, none());
        assertStatus(id, ITraderVault.AgreementStatus.Settled);
    }

    // ------------------------------------------------------------------
    // Decimals: tUSDC(6) / tWBTC(8) / tWETH(18)
    // ------------------------------------------------------------------

    function test_decimals_usdc6_wbtc8_weth18_endToEnd() public {
        TestToken wbtc = newToken(8, true, false);
        TestToken weth = newToken(18, true, false);
        // 1 WETH = 3000 USDC, 1 WBTC = 60000 USDC, WETH:WBTC = 20:1 (deploy betiği oranları)
        setPrice(address(usdc), address(weth), 1e18, 3000e6);
        setPrice(address(weth), address(usdc), 3000e6, 1e18);
        setPrice(address(usdc), address(wbtc), 1e8, 60000e6);
        setPrice(address(wbtc), address(usdc), 60000e6, 1e8);
        setPrice(address(weth), address(wbtc), 1e8, 20e18);
        setPrice(address(wbtc), address(weth), 20e18, 1e8);

        uint256 id = openActive(); // 1000e6, taban 800e6
        uint64 d = dl();
        vm.startPrank(trader);
        // 600 USDC -> 0.01 BTC (1_000_000 sat)
        uint256 sats = vault.trade(id, address(usdc), address(wbtc), 600e6, 1_000_000, d);
        assertEq(sats, 1_000_000);
        assertEq(vault.valueInBase(id), 1000e6);
        // 0.01 BTC -> 0.2 WETH
        uint256 wei_ = vault.trade(id, address(wbtc), address(weth), 1_000_000, 2e17, d);
        assertEq(wei_, 2e17);
        assertEq(tokensOf(id), addrs2(address(usdc), address(weth)));
        assertEq(vault.valueInBase(id), 1000e6);
        // 0.2 WETH -> 600 USDC
        assertEq(vault.trade(id, address(weth), address(usdc), 2e17, 600e6, d), 600e6);
        assertEq(tokensOf(id), addrs1(address(usdc)));
        assertEq(vaultBalance(id, address(usdc)), 1000e6);

        vm.stopPrank();
        // Drawdown tabanı ham birimlerde: WETH 2000'e düşer -> 600 USDC -> 0.2 WETH = 400 => 800 == taban
        setPrice(address(weth), address(usdc), 2000e6, 1e18);
        vm.prank(trader);
        vault.trade(id, address(usdc), address(weth), 600e6, 2e17, d);
        assertEq(vault.valueInBase(id), 800e6);
        setPrice(address(weth), address(usdc), 1999e6, 1e18); // 799.8 < 800
        vm.prank(trader);
        expectVaultError(ITraderVault.DrawdownBreached.selector);
        vault.trade(id, address(usdc), address(weth), 1e6, 1, d);

        // Kâr: WETH 3500 -> 0.2 WETH = 700 => 1100; trader settle
        setPrice(address(weth), address(usdc), 3500e6, 1e18);
        (,, uint256[] memory q, uint256 est,,) = vault.previewSettle(id);
        assertEq(q, one(700e6));
        assertEq(est, 1100e6);
        vm.expectEmit(address(vault));
        emit ITraderVault.Settled(id, 1100e6, 100e6, 20e6, 1e6, 1079e6, trader);
        vm.prank(trader);
        vault.settle(id, q);
        assertEq(usdc.balanceOf(customer), 1079e6);
        assertEq(usdc.balanceOf(trader), 20e6);
        assertEq(usdc.balanceOf(feeRecipient), 1e6);

        // Dust: 1 satoshi = 600 mikro-USDC (quote > 0, likide edilir); 1 wei WETH quote 0 -> in-kind
        usdc.mint(customer, PRINCIPAL);
        uint256 id2 = openActive();
        vm.startPrank(trader);
        vault.trade(id2, address(usdc), address(wbtc), 600e6, 1_000_000, d);
        vault.trade(id2, address(wbtc), address(usdc), 999_999, 1, d); // 599_999_400
        assertEq(vaultBalance(id2, address(wbtc)), 1);
        vault.trade(id2, address(usdc), address(weth), 300e6, 1e17, d);
        vault.trade(id2, address(weth), address(usdc), 1e17 - 1, 1, d);
        assertEq(vaultBalance(id2, address(weth)), 1);
        vm.stopPrank();
        (,, q, est,,) = vault.previewSettle(id2);
        assertEq(q, two(600, 0));
        uint256 usdcBal = vaultBalance(id2, address(usdc));
        assertEq(est, usdcBal + 600);
        vm.expectEmit(address(vault));
        emit ITraderVault.Unliquidated(id2, address(weth), 1, true);
        vm.prank(customer);
        vault.settle(id2, two(600, 0));
        assertEq(ag(id2).finalValue, usdcBal + 600);
        assertEq(weth.balanceOf(customer), 1);
        assertEq(wbtc.balanceOf(address(vault)), 0);
        assertEq(weth.balanceOf(address(vault)), 0);
    }

    function test_decimals_altBase18() public {
        // base = alt (18 dp); principal 4000 ALT; usdc yan bacak
        alt.mint(customer, 4_000 * U18);
        vm.prank(customer);
        alt.approve(address(vault), type(uint256).max);
        ITraderVault.Terms memory t = terms();
        t.baseToken = address(alt);
        t.principal = 4_000 * U18;
        uint256 id = openActiveWith(t);
        vm.prank(trader);
        vault.trade(id, address(alt), address(usdc), 2_000 * U18, 500 * U6, dl());
        assertEq(vault.valueInBase(id), 4_000 * U18);
        setPrice(address(usdc), address(alt), 5e12, 1); // USDC güçlenir: 500 USDC -> 2500 ALT
        assertEq(vault.valueInBase(id), 4_500 * U18);
        vm.prank(trader);
        vault.settle(id, one(2_500 * U18));
        ITraderVault.Agreement memory a = ag(id);
        assertEq(a.finalValue, 4_500 * U18);
        assertEq(a.traderFee, 100 * U18);
        assertEq(a.platformFee, 5 * U18);
        assertEq(a.customerPayout, 4_395 * U18);
        assertEq(alt.balanceOf(customer), 4_395 * U18);
    }

    function test_zeroAmount_paths() public {
        uint256 id = openActive();
        usdc.mint(customer, 1);
        vm.prank(customer);
        uint256 rid = vault.reserve(address(usdc), 1, listingRef());
        vm.prank(customer);
        expectVaultError(ITraderVault.ZeroAmount.selector);
        vault.reserve(address(usdc), 0, listingRef());
        vm.prank(trader);
        expectVaultError(ITraderVault.ZeroAmount.selector);
        vault.trade(id, address(usdc), address(alt), 0, 1, dl());
        vm.prank(trader);
        expectVaultError(ITraderVault.ZeroAmount.selector);
        vault.trade(id, address(usdc), address(alt), U6, 0, dl());
        vm.prank(customer);
        expectVaultError(ITraderVault.ZeroAmount.selector);
        vault.release(rid, 0);
        // sıfır tutar kontrolü Expired'dan sonra, TokenNotAllowed'dan önce
        vm.prank(trader);
        expectVaultError(ITraderVault.ZeroAmount.selector);
        vault.trade(id, address(usdc), address(usdc), 0, 0, dl());
        vm.prank(trader);
        expectVaultError(ITraderVault.Expired.selector);
        vault.trade(id, address(usdc), address(alt), 0, 0, now64() - 1);
    }

    function test_settle_partyMinOutZeroAccepted_butZeroDeliveryRejected() public {
        uint256 id = openActiveWith(termsWithDrawdown(10_000));
        vm.prank(trader);
        vault.trade(id, address(usdc), address(alt), 200 * U6, 800 * U18, dl());
        // Parti minOut=0 verebilir; router pozitif çıktı verdiği sürece kabul edilir
        vm.prank(customer);
        vault.settle(id, one(0));
        assertEq(ag(id).finalValue, PRINCIPAL);

        // Router minOut'u "bildirir" ama daha azını teslim ederse: parti minOut'u gerçek teslimata uygulanır
        (TraderVault v, MaliciousRouter mr) = _maliciousSetup();
        uint256 id2 = _openActiveOn(v);
        vm.prank(trader);
        v.trade(id2, address(usdc), address(alt), 200 * U6, 800 * U18, dl());
        mr.setMode(MaliciousRouter.Mode.REPORT_MORE); // 200 bildirir, 100 gönderir
        vm.prank(customer);
        expectVaultError(ITraderVault.SlippageExceeded.selector);
        v.settle(id2, one(200 * U6));
        vm.prank(customer);
        expectVaultError(ITraderVault.SlippageExceeded.selector);
        v.settle(id2, one(100 * U6 + 1));
        assertEq(uint8(v.getAgreement(id2).status), uint8(ITraderVault.AgreementStatus.Active));
        assertEq(alt.balanceOf(address(v)), 800 * U18);
        // minOut=0 ile parti her şeyi kabul eder: reported 200 ama credited 100
        vm.prank(customer);
        v.settle(id2, one(0));
        assertEq(v.getAgreement(id2).finalValue, 900 * U6);
        assertEq(usdc.balanceOf(address(v)), 0);
    }
}
