// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {BaseTest} from "./Base.t.sol";
import {ITraderVault} from "../src/interfaces/ITraderVault.sol";
import {BlocklistToken} from "./mocks/BlocklistToken.sol";

contract TraderVaultSettleTest is BaseTest {
    // Rust 10: settle_with_loss_pays_no_fees
    function test_settle_lossPaysNoFees() public {
        uint256 id = openActive();
        vm.prank(trader);
        vault.trade(id, address(usdc), address(alt), 200 * U6, 800 * U18, dl());
        setPrice(address(alt), address(usdc), 1, 8e12); // ALT yarıya iner: 800 ALT -> 100 USDC
        assertEq(vault.valueInBase(id), 900 * U6);

        vm.prank(customer);
        vault.settle(id, one(100 * U6));
        ITraderVault.Agreement memory a = ag(id);
        assertEq(a.finalValue, 900 * U6);
        assertEq(a.customerPayout, 900 * U6);
        assertEq(a.traderFee, 0);
        assertEq(a.platformFee, 0);
        assertEq(usdc.balanceOf(customer), 900 * U6);
        assertEq(usdc.balanceOf(trader), 0);
        assertEq(usdc.balanceOf(feeRecipient), 0);
    }

    // Rust 11: settle_without_trades_returns_principal
    function test_settle_withoutTradesReturnsPrincipal() public {
        uint256 id = proposeActive();
        vm.prank(customer);
        vault.settle(id, none());
        assertEq(usdc.balanceOf(customer), PRINCIPAL);
        assertStatus(id, ITraderVault.AgreementStatus.Settled);
    }

    // Rust 12: settle_keeper_path_opens_after_grace_with_reference_floor
    function test_settle_keeperPathAfterGraceWithReferenceFloor() public {
        uint256 id = openActive();
        vm.startPrank(trader);
        vault.trade(id, address(usdc), address(alt), 200 * U6, 800 * U18, dl());
        vault.trade(id, address(usdc), address(eurc), 100 * U6, 90 * U6, dl());
        vm.stopPrank();
        assertEq(ag(id).lastValue, PRINCIPAL);
        uint64 end = ag(id).endTime;

        // Süre dolmadan yabancı settle edemez ...
        vm.prank(stranger);
        expectVaultError(ITraderVault.NotExpired.selector);
        vault.settle(id, none());
        // ... taraflar ve admin için ayrılan grace penceresinde de edemez.
        vm.warp(end);
        vm.prank(trader);
        expectVaultError(ITraderVault.Expired.selector);
        vault.trade(id, address(usdc), address(alt), U6, 1, end + 60);
        vm.prank(stranger);
        expectVaultError(ITraderVault.NotExpired.selector);
        vault.settle(id, none());
        vm.warp(end + KEEPER_GRACE - 1);
        vm.prank(stranger);
        expectVaultError(ITraderVault.NotExpired.selector);
        vault.settle(id, none());

        // Keeper penceresi açık. Havuz 100x aleyhte: aynı-tx quote tabanı her zaman tutar,
        // taban lastValue'dan gelmeli.
        vm.warp(end + KEEPER_GRACE);
        setPrice(address(alt), address(usdc), 1, 4e14);
        Snapshot memory s = snapshot(id);
        vm.prank(stranger);
        expectVaultError(ITraderVault.SlippageExceeded.selector);
        vault.settle(id, none());
        assertUnchanged(id, s);
        // Verilen minOuts dikkate alınır (kontrat tabanı ile max) ...
        vm.prank(stranger);
        expectRouterError();
        vault.settle(id, two(201 * U6, 1));
        // ... ve boş ya da tam uzunlukta olmalı.
        vm.prank(stranger);
        expectVaultError(ITraderVault.InvalidAmount.selector);
        vault.settle(id, one(1));
        assertUnchanged(id, s);

        // Tolerans içinde hareket settle olur: 800 ALT @ 19/80 = 190 => 700 + 190 + 100 = 990 = lastValue x 99%
        setPrice(address(alt), address(usdc), 19, 80e12);
        vm.expectEmit(address(vault));
        emit ITraderVault.Settled(id, 990 * U6, 0, 0, 0, 990 * U6, stranger);
        vm.prank(stranger);
        vault.settle(id, none());
        ITraderVault.Agreement memory a = ag(id);
        assertEq(uint8(a.status), uint8(ITraderVault.AgreementStatus.Settled));
        assertEq(a.finalValue, 990 * U6);
        assertEq(a.customerPayout, 990 * U6);
        assertEq(usdc.balanceOf(customer), 990 * U6);
        assertEq(alt.balanceOf(address(vault)), 0);
        assertEq(eurc.balanceOf(address(vault)), 0);
    }

    // Rust 13: settle_by_admin_after_expiry_requires_auth_and_honours_min_outs
    function test_settle_adminAfterExpiryHonoursMinOuts() public {
        uint256 id = openActive();
        vm.prank(trader);
        vault.trade(id, address(usdc), address(alt), 200 * U6, 800 * U18, dl());
        uint64 end = ag(id).endTime;
        // admin taraf değil: endTime öncesi sıradan bir yabancı
        vm.prank(admin);
        expectVaultError(ITraderVault.NotExpired.selector);
        vault.settle(id, none());
        vm.warp(end);
        // endTime'dan itibaren admin settle edebilir (grace yok); minOuts'u zorlanır, atılmaz
        vm.prank(admin);
        expectRouterError();
        vault.settle(id, one(201 * U6));
        vm.prank(admin);
        expectVaultError(ITraderVault.InvalidAmount.selector);
        vault.settle(id, two(1, 1));
        assertStatus(id, ITraderVault.AgreementStatus.Active);
        vm.expectEmit(address(vault));
        emit ITraderVault.Settled(id, PRINCIPAL, 0, 0, 0, PRINCIPAL, admin);
        vm.prank(admin);
        vault.settle(id, one(200 * U6));
        assertStatus(id, ITraderVault.AgreementStatus.Settled);
        assertEq(usdc.balanceOf(customer), PRINCIPAL);
    }

    // Yeni (risk 2): admin da lastValue tabanına takılır
    function test_settle_adminBoundByLastValueFloor() public {
        uint256 id = openActive();
        vm.prank(trader);
        vault.trade(id, address(usdc), address(alt), 200 * U6, 800 * U18, dl());
        vm.warp(ag(id).endTime);
        setPrice(address(alt), address(usdc), 1, 4e14); // 100x ters
        Snapshot memory s = snapshot(id);
        vm.prank(admin);
        expectVaultError(ITraderVault.SlippageExceeded.selector);
        vault.settle(id, none());
        assertUnchanged(id, s);
        // admin'in kendi minOuts'u da tabanı aşamaz (max ile birleşir, ama finalValue kontrolü ayrı)
        vm.prank(admin);
        expectVaultError(ITraderVault.SlippageExceeded.selector);
        vault.settle(id, one(0));
        assertUnchanged(id, s);
        // tolerans içi: 800 ALT -> 190 USDC => 990 >= 990
        setPrice(address(alt), address(usdc), 19, 80e12);
        vm.prank(admin);
        vault.settle(id, none());
        assertEq(ag(id).finalValue, 990 * U6);
        // 989 olsaydı geçmezdi: ayrı anlaşma ile doğrula (slip 1% -> 990 tabanı)
        usdc.mint(customer, PRINCIPAL);
        setPrice(address(alt), address(usdc), 1, 4e12); // trade anında lastValue = 1000
        uint256 id2 = openActive();
        vm.prank(trader);
        vault.trade(id2, address(usdc), address(alt), 200 * U6, 800 * U18, dl());
        assertEq(ag(id2).lastValue, PRINCIPAL);
        vm.warp(ag(id2).endTime);
        setPrice(address(alt), address(usdc), 189, 800e12); // 800 ALT -> 189 => 989
        vm.prank(admin);
        expectVaultError(ITraderVault.SlippageExceeded.selector);
        vault.settle(id2, none());
    }

    // Rust 14: settle_by_trader_cannot_go_below_drawdown_floor
    function test_settle_traderCannotGoBelowDrawdownFloor() public {
        uint256 id = openActive(); // 20% -> taban 800 USDC
        vm.prank(trader);
        vault.trade(id, address(usdc), address(alt), 500 * U6, 2_000 * U18, dl());
        // Havuz 100x aleyhte: trader imzalı settle minOuts=[0] ile tabanın altına inemez
        setPrice(address(alt), address(usdc), 1, 4e14); // 2000 ALT -> 5 USDC => 505
        Snapshot memory s = snapshot(id);
        vm.prank(trader);
        expectVaultError(ITraderVault.DrawdownBreached.selector);
        vault.settle(id, one(0));
        assertUnchanged(id, s);
        // Süre dolduktan sonra da aynı
        vm.warp(ag(id).endTime + 1);
        vm.prank(trader);
        expectVaultError(ITraderVault.DrawdownBreached.selector);
        vault.settle(id, one(0));
        assertUnchanged(id, s);
        // Tam taban kabul: 2000 ALT @ 3/20 = 300 => 800
        setPrice(address(alt), address(usdc), 3, 20e12);
        vm.prank(trader);
        vault.settle(id, one(0));
        assertEq(ag(id).finalValue, 800 * U6);
        assertEq(usdc.balanceOf(customer), 800 * U6);

        // Müşteri zararı taşır ve her fiyatta çıkabilir
        usdc.mint(customer, PRINCIPAL - 800 * U6);
        uint256 id2 = openActive();
        vm.prank(trader);
        vault.trade(id2, address(usdc), address(alt), 500 * U6, 2_000 * U18, dl());
        setPrice(address(alt), address(usdc), 1, 4e14);
        vm.prank(customer);
        vault.settle(id2, one(0));
        assertEq(ag(id2).finalValue, 505 * U6);
        assertEq(usdc.balanceOf(customer), 505 * U6);
    }

    // Rust 15: settle_post_expiry_by_party_still_uses_their_min_outs
    function test_settle_postExpiryPartyUsesOwnMinOuts() public {
        uint256 id = openActive();
        vm.prank(trader);
        vault.trade(id, address(usdc), address(alt), 200 * U6, 800 * U18, dl());
        vm.warp(ag(id).endTime + 1);
        vm.prank(trader);
        expectRouterError();
        vault.settle(id, one(201 * U6));
        vm.expectEmit(address(vault));
        emit ITraderVault.Settled(id, PRINCIPAL, 0, 0, 0, PRINCIPAL, trader);
        vm.prank(trader);
        vault.settle(id, one(200 * U6));
        assertStatus(id, ITraderVault.AgreementStatus.Settled);
    }

    // Rust 16: settle_validation
    function test_settle_validation() public {
        vm.prank(customer);
        uint256 id = vault.open(terms());
        vm.prank(customer);
        expectVaultError(ITraderVault.WrongStatus.selector);
        vault.settle(id, none());
        vm.prank(trader);
        vault.accept(id);
        vm.prank(trader);
        vault.trade(id, address(usdc), address(alt), 200 * U6, 800 * U18, dl());
        // base dışı her token için tam bir giriş
        vm.prank(customer);
        expectVaultError(ITraderVault.InvalidAmount.selector);
        vault.settle(id, none());
        vm.prank(customer);
        expectVaultError(ITraderVault.InvalidAmount.selector);
        vault.settle(id, two(1, 1));
        // çok yüksek minOut -> router reddeder -> RouterError, state sağlam
        vm.prank(customer);
        expectRouterError();
        vault.settle(id, one(201 * U6));
        assertStatus(id, ITraderVault.AgreementStatus.Active);
        assertEq(vaultBalance(id, address(alt)), 800 * U18);
        // bilinmeyen id
        vm.prank(customer);
        expectVaultError(ITraderVault.NotFound.selector);
        vault.settle(99, none());
        // başarı, ikinci settle WrongStatus
        vm.prank(customer);
        vault.settle(id, one(200 * U6));
        vm.prank(customer);
        expectVaultError(ITraderVault.WrongStatus.selector);
        vault.settle(id, none());
        // Cancelled -> WrongStatus
        usdc.mint(customer, PRINCIPAL);
        vm.prank(customer);
        uint256 c = vault.open(terms());
        vm.prank(customer);
        vault.cancel(c);
        vm.prank(customer);
        expectVaultError(ITraderVault.WrongStatus.selector);
        vault.settle(c, none());
    }

    // Rust 17: fee_leg_that_cannot_be_received_is_folded_into_customer_payout
    function test_settle_unreceivableFeeLegFoldedIntoCustomer() public {
        // base = BlocklistToken; feeRecipient bloklu
        BlocklistToken base = new BlocklistToken("Blk USDC", "bUSDC", 6, address(this));
        base.mint(address(router), LIQ * U6);
        base.mint(customer, 2 * PRINCIPAL);
        vm.prank(admin);
        vault.setToken(address(base), true, true);
        setPrice(address(base), address(alt), 4e12, 1);
        setPrice(address(alt), address(base), 1, 4e12);
        vm.prank(customer);
        base.approve(address(vault), type(uint256).max);
        base.setBlocked(feeRecipient, true);

        ITraderVault.Terms memory t = terms();
        t.baseToken = address(base);
        uint256 id = openActiveWith(t);
        vm.prank(trader);
        vault.trade(id, address(base), address(alt), 200 * U6, 800 * U18, dl());
        setPrice(address(alt), address(base), 1, 2e12); // kâr 200
        vm.expectEmit(address(vault));
        emit ITraderVault.Settled(id, 1_200 * U6, 200 * U6, 40 * U6, 0, 1_160 * U6, customer);
        vm.prank(customer);
        vault.settle(id, one(400 * U6));
        ITraderVault.Agreement memory a = ag(id);
        assertEq(a.finalValue, 1_200 * U6);
        assertEq(a.traderFee, 40 * U6);
        assertEq(a.platformFee, 0, "unreceivable platform fee is not paid");
        assertEq(a.customerPayout, 1_160 * U6);
        assertEq(base.balanceOf(customer), PRINCIPAL + 1_160 * U6);
        assertEq(base.balanceOf(trader), 40 * U6);
        assertEq(base.balanceOf(feeRecipient), 0);
        assertEq(base.balanceOf(address(vault)), 0);

        // trader da alamıyorsa her iki fee müşteriye katlanır
        base.setBlocked(trader, true);
        t = terms();
        t.baseToken = address(base);
        uint256 id2 = openActiveWith(t);
        vm.prank(trader);
        vault.trade(id2, address(base), address(alt), 200 * U6, 800 * U18, dl());
        vm.expectEmit(address(vault));
        emit ITraderVault.Settled(id2, 1_200 * U6, 200 * U6, 0, 0, 1_200 * U6, customer);
        vm.prank(customer);
        vault.settle(id2, one(400 * U6));
        assertEq(ag(id2).traderFee, 0);
        assertEq(base.balanceOf(trader), 40 * U6);
        assertEq(base.balanceOf(address(vault)), 0);
    }

    // Rust 33: settle_delivers_unquotable_dust_in_kind_on_every_path
    function test_settle_deliversDustInKindOnEveryPath() public {
        usdc.mint(customer, 2 * PRINCIPAL);
        uint64 d = dl();
        uint256[3] memory ids;
        for (uint256 i = 0; i < 3; ++i) {
            uint256 id = openActive();
            vm.startPrank(trader);
            vault.trade(id, address(usdc), address(alt), 200 * U6, 800 * U18, d);
            vault.trade(id, address(alt), address(usdc), 800 * U18 - 1, 1, d);
            vm.stopPrank();
            assertEq(vaultBalance(id, address(alt)), 1);
            assertEq(tokensOf(id).length, 2);
            assertEq(vault.valueInBase(id), PRINCIPAL - 1);
            ids[i] = id;
        }
        // customer, her an
        _expectDust(ids[0], customer);
        vm.prank(customer);
        vault.settle(ids[0], one(0));
        _assertDustSettled(ids[0]);
        // trader, her an (PRINCIPAL - 1 taban üstü)
        _expectDust(ids[1], trader);
        vm.prank(trader);
        vault.settle(ids[1], one(1));
        _assertDustSettled(ids[1]);
        // keeper, grace sonrası (PRINCIPAL - 1 >= lastValue x 99%)
        vm.warp(ag(ids[2]).endTime + KEEPER_GRACE);
        _expectDust(ids[2], stranger);
        vm.prank(stranger);
        vault.settle(ids[2], none());
        _assertDustSettled(ids[2]);

        assertEq(alt.balanceOf(customer), 3);
        assertEq(usdc.balanceOf(customer), 3 * (PRINCIPAL - 1));
        assertEq(alt.balanceOf(address(vault)), 0);
        assertEq(usdc.balanceOf(address(vault)), 0);
    }

    function _expectDust(uint256 id, address by) internal {
        vm.expectEmit(address(vault));
        emit ITraderVault.Unliquidated(id, address(alt), 1, true);
        vm.expectEmit(address(vault));
        emit ITraderVault.Settled(id, PRINCIPAL - 1, 0, 0, 0, PRINCIPAL - 1, by);
    }

    function _assertDustSettled(uint256 id) internal view {
        assertStatus(id, ITraderVault.AgreementStatus.Settled);
        assertEq(tokensOf(id), addrs1(address(usdc)));
        assertBalances(id, addrs1(address(usdc)), one(0));
    }

    // Rust 34: settle_orphans_unreceivable_in_kind_leg_until_claimed
    function test_settle_orphansUnreceivableLegUntilClaimed() public {
        uint256 id = openActiveWith(termsWithDrawdown(10_000));
        BlocklistToken weird = new BlocklistToken("Weird", "WRD", 6, address(this));
        weird.mint(address(router), LIQ * U6);
        vm.prank(admin);
        vault.setToken(address(weird), true, false);
        setPrice(address(usdc), address(weird), 1, 1);
        setPrice(address(weird), address(usdc), 1, 1);
        vm.prank(trader);
        vault.trade(id, address(usdc), address(weird), 300 * U6, 300 * U6, dl());
        clearPrice(address(weird), address(usdc));
        // Müşteri `weird` alamıyor: bacak kaybolmaz, kalan settle devam eder
        weird.setBlocked(customer, true);
        vm.expectEmit(address(vault));
        emit ITraderVault.Unliquidated(id, address(weird), 300 * U6, false);
        vm.expectEmit(address(vault));
        emit ITraderVault.Settled(id, 700 * U6, 0, 0, 0, 700 * U6, customer);
        vm.prank(customer);
        vault.settle(id, one(0));
        ITraderVault.Agreement memory a = ag(id);
        assertEq(uint8(a.status), uint8(ITraderVault.AgreementStatus.Settled));
        assertEq(a.tokens, addrs2(address(usdc), address(weird)));
        assertBalances(id, addrs2(address(usdc), address(weird)), two(0, 300 * U6));
        assertEq(weird.balanceOf(address(vault)), 300 * U6);
        assertEq(usdc.balanceOf(customer), 700 * U6);
        // previewSettle settle sonrası orphan'ı gösterir
        (address[] memory pt, uint256[] memory pb, uint256[] memory pq, uint256 est,,) = vault.previewSettle(id);
        assertEq(pt, addrs1(address(weird)));
        assertEq(pb, one(300 * U6));
        assertEq(pq, one(0));
        assertEq(est, 0);

        // Hâlâ alınamıyor: claim revert, hiçbir şey hareket etmez
        vm.prank(customer);
        vm.expectRevert(abi.encodeWithSelector(BlocklistToken.Blocked.selector, customer));
        vault.claim(id, address(weird));
        assertEq(vaultBalance(id, address(weird)), 300 * U6);
        // Diğer tokenlar için claim edilecek bir şey yok
        vm.prank(customer);
        expectVaultError(ITraderVault.InsufficientBalance.selector);
        vault.claim(id, address(usdc));
        // Yalnız müşteri claim edebilir
        vm.prank(stranger);
        expectVaultError(ITraderVault.Unauthorized.selector);
        vault.claim(id, address(weird));
        vm.prank(customer);
        expectVaultError(ITraderVault.NotFound.selector);
        vault.claim(99, address(weird));

        // Alınabilir olunca müşteri toplar
        weird.setBlocked(customer, false);
        vm.expectEmit(address(vault));
        emit ITraderVault.Claimed(id, address(weird), 300 * U6);
        vm.prank(customer);
        assertEq(vault.claim(id, address(weird)), 300 * U6);
        assertEq(weird.balanceOf(customer), 300 * U6);
        assertEq(weird.balanceOf(address(vault)), 0);
        assertEq(tokensOf(id), addrs1(address(usdc)));
        vm.prank(customer);
        expectVaultError(ITraderVault.InsufficientBalance.selector);
        vault.claim(id, address(weird));
        // claim yalnız Settled için
        usdc.mint(customer, PRINCIPAL);
        uint256 active = openActive();
        vm.prank(customer);
        expectVaultError(ITraderVault.WrongStatus.selector);
        vault.claim(active, address(usdc));
    }

    function test_settle_multipleOrphansKeepOrder() public {
        uint256 id = openActiveWith(termsWithDrawdown(10_000));
        BlocklistToken w1 = new BlocklistToken("W1", "W1", 6, address(this));
        BlocklistToken w2 = new BlocklistToken("W2", "W2", 6, address(this));
        w1.mint(address(router), LIQ * U6);
        w2.mint(address(router), LIQ * U6);
        vm.startPrank(admin);
        vault.setToken(address(w1), true, false);
        vault.setToken(address(w2), true, false);
        vm.stopPrank();
        setPrice(address(usdc), address(w1), 1, 1);
        setPrice(address(w1), address(usdc), 1, 1);
        setPrice(address(usdc), address(w2), 1, 1);
        setPrice(address(w2), address(usdc), 1, 1);
        vm.startPrank(trader);
        vault.trade(id, address(usdc), address(alt), 100 * U6, 400 * U18, dl());
        vault.trade(id, address(usdc), address(w1), 100 * U6, 100 * U6, dl());
        vault.trade(id, address(usdc), address(w2), 100 * U6, 100 * U6, dl());
        vm.stopPrank();
        clearPrice(address(w1), address(usdc));
        clearPrice(address(w2), address(usdc));
        w1.setBlocked(customer, true);
        w2.setBlocked(customer, true);
        uint256[] memory minOuts = new uint256[](3);
        minOuts[0] = 100 * U6;
        vm.prank(customer);
        vault.settle(id, minOuts);
        address[] memory toks = tokensOf(id);
        assertEq(toks.length, 3);
        assertEq(toks[0], address(usdc));
        assertEq(toks[1], address(w1));
        assertEq(toks[2], address(w2));
        assertEq(ag(id).finalValue, 800 * U6);
        // ortadaki orphan claim edilince sıra korunur
        w1.setBlocked(customer, false);
        w2.setBlocked(customer, false);
        vm.prank(customer);
        vault.claim(id, address(w1));
        assertEq(tokensOf(id), addrs2(address(usdc), address(w2)));
        vm.prank(customer);
        vault.claim(id, address(w2));
        assertEq(tokensOf(id), addrs1(address(usdc)));
    }
}
