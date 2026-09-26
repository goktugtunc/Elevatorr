// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {BaseTest} from "./Base.t.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {ITraderVault} from "../src/interfaces/ITraderVault.sol";
import {TestToken} from "../src/mocks/TestToken.sol";
import {InvariantHandler} from "./utils/InvariantHandler.sol";
import {BlocklistToken} from "./mocks/BlocklistToken.sol";

/// @notice §10.3 Invariant.t.sol: MockRouter ile bakiye EŞİTLİĞİ iddia edilir (SEND_MORE yalnız Security'de).
contract TraderVaultInvariantTest is BaseTest {
    InvariantHandler internal handler;
    TestToken internal wbtc;
    BlocklistToken internal wrd;
    address[] internal allTokens;

    function setUp() public override {
        super.setUp();
        wbtc = newToken(8, true, false);
        // wbtc fiyatları (1 BTC = 60000 USDC); alt/eurc çaprazları handler setPrice ile gelir
        setPrice(address(usdc), address(wbtc), 1e8, 60000e6);
        setPrice(address(wbtc), address(usdc), 60000e6, 1e8);
        // alt <-> eurc / wbtc çaprazları, adil değerlerden
        setPrice(address(alt), address(eurc), 9, 40e12);
        setPrice(address(eurc), address(alt), 40e12, 9);
        setPrice(address(alt), address(wbtc), 1e8, 240000e18);
        setPrice(address(wbtc), address(alt), 240000e18, 1e8);
        setPrice(address(eurc), address(wbtc), 1e8 * 10, 60000e6 * 9);
        setPrice(address(wbtc), address(eurc), 60000e6 * 9, 1e8 * 10);

        // blocklist token (6 dp, allowed, base değil): 1 WRD = 2 USDC; orphan/claim yolu
        wrd = new BlocklistToken("Weird", "WRD", 6, address(this));
        vm.prank(admin);
        vault.setToken(address(wrd), true, false);
        setPrice(address(usdc), address(wrd), 1, 2);
        setPrice(address(wrd), address(usdc), 2, 1);
        setPrice(address(alt), address(wrd), 1, 8e12);
        setPrice(address(wrd), address(alt), 8e12, 1);

        allTokens = new address[](5);
        allTokens[0] = address(usdc);
        allTokens[1] = address(alt);
        allTokens[2] = address(eurc);
        allTokens[3] = address(wbtc);
        allTokens[4] = address(wrd);
        // mikro-USD cinsinden 1 birim (10**decimals) tokenın adil değeri
        uint256[] memory fair = new uint256[](5);
        fair[0] = 1e6; // 1 USDC
        fair[1] = 250_000; // 1 ALT = 0.25 USD
        fair[2] = 1_111_111; // 1 EURC = 1/0.9 USD
        fair[3] = 60_000e6; // 1 BTC
        fair[4] = 2e6; // 1 WRD = 2 USD
        address[] memory actors = new address[](3);
        actors[0] = customer;
        actors[1] = trader;
        actors[2] = stranger;

        handler = new InvariantHandler(vault, router, address(this), admin, actors, allTokens, fair, address(wrd));
        handler.setFairPrices(); // tüm çaprazlar adil fiyattan
        // handler mint edebilsin (faucet rolü)
        for (uint256 i = 0; i < allTokens.length; ++i) {
            TestToken(allTokens[i]).grantRole(TestToken(allTokens[i]).MINTER_ROLE(), address(handler));
            // router'a bol likidite
            TestToken(allTokens[i]).mint(address(router), 1e15 * (10 ** uint256(TestToken(allTokens[i]).decimals())));
        }
        // Başlangıç durumu: her çalıştırma anlamlı state ile başlasın (Active anlaşmalar, açık rezervasyon,
        // orphan'lı Settled anlaşma). Handler eylemleri üzerinden kurulur; ghost sayaçlar tutarlı kalır.
        handler.open(0, 1, 0, 5_000 * U6, 7); // customer -> trader, usdc
        handler.accept(0);
        handler.trade(0, 0, 1, 1_000 * U6, 95); // usdc -> alt
        handler.trade(0, 0, 3, 500 * U6, 95); // usdc -> wbtc
        handler.propose(1, 2, 1, 3_000 * U18, 11); // trader(customer) <- stranger(trader), alt base
        handler.fund(1);
        handler.reserve(0, 0, 20_000 * U6);
        handler.reserve(2, 1, 4_000 * U18);
        // orphan: wrd tut, rotayı sil, müşteriyi blokla, settle
        handler.open(2, 0, 0, 2_000 * U6, 13); // stranger -> customer(trader), usdc
        handler.accept(2);
        handler.trade(2, 0, 4, 400 * U6, 95); // usdc -> wrd
        handler.settle(2, 0, 3); // customer settle, minMode 3 -> _forceInKind

        targetContract(address(handler));
        // setFairPrices yalnız kurulum yardımcısı; fuzz eylemi değil
        bytes4[] memory excluded = new bytes4[](1);
        excluded[0] = InvariantHandler.setFairPrices.selector;
        excludeSelector(FuzzSelector({addr: address(handler), selectors: excluded}));
    }

    // ------------------------------------------------------------------
    // Invariant'lar
    // ------------------------------------------------------------------

    /// @dev Her token t için: balanceOf(vault) == Σ_id _balances[id][t] + Σ_res(open, token==t) amount
    function invariant_tokenBalancesCoverAccounting() public view {
        uint256 n = handler.agreementCount();
        uint256 m = handler.reservationCount();
        for (uint256 k = 0; k < allTokens.length; ++k) {
            address t = allTokens[k];
            uint256 sum = 0;
            for (uint256 i = 0; i < n; ++i) {
                (address[] memory toks, uint256[] memory amts) = vault.getBalances(handler.agreementIds(i));
                for (uint256 j = 0; j < toks.length; ++j) {
                    if (toks[j] == t) sum += amts[j];
                }
            }
            for (uint256 i = 0; i < m; ++i) {
                ITraderVault.Reservation memory r = vault.getReservation(handler.reservationIds(i));
                if (r.token == t && r.status == ITraderVault.ReservationStatus.Open) sum += r.amount;
            }
            assertEq(IERC20(t).balanceOf(address(vault)), sum, "vault holdings != accounting");
        }
    }

    /// @dev tokens.length <= 6, tokens[0] == base, tekrar yok; Active iken base dışı her tokenın bakiyesi > 0;
    ///      Proposed/Funded/Cancelled iken yalnız base; Settled iken base bakiyesi 0.
    function invariant_tokensListWellFormed() public view {
        uint256 n = handler.agreementCount();
        for (uint256 i = 0; i < n; ++i) {
            uint256 id = handler.agreementIds(i);
            ITraderVault.Agreement memory a = vault.getAgreement(id);
            (address[] memory toks, uint256[] memory amts) = vault.getBalances(id);
            assertLe(toks.length, vault.MAX_TOKENS(), "too many tokens");
            assertGe(toks.length, 1, "base missing");
            assertEq(toks[0], a.terms.baseToken, "tokens[0] != base");
            for (uint256 j = 0; j < toks.length; ++j) {
                for (uint256 l = j + 1; l < toks.length; ++l) {
                    assertTrue(toks[j] != toks[l], "duplicate token");
                }
            }
            if (a.status == ITraderVault.AgreementStatus.Active) {
                for (uint256 j = 1; j < toks.length; ++j) {
                    assertGt(amts[j], 0, "active non-base token with zero balance");
                }
                assertEq(a.tokens.length, toks.length);
            } else if (a.status == ITraderVault.AgreementStatus.Settled) {
                assertEq(amts[0], 0, "settled base balance must be 0");
                for (uint256 j = 1; j < toks.length; ++j) {
                    assertGt(amts[j], 0, "settled orphan with zero balance");
                }
            } else {
                assertEq(toks.length, 1, "non-active agreement holds non-base tokens");
                if (a.status == ITraderVault.AgreementStatus.Funded) {
                    assertEq(amts[0], a.terms.principal, "funded balance != principal");
                } else {
                    assertEq(amts[0], 0, "proposed/cancelled balance != 0");
                }
            }
        }
    }

    /// @dev Settled/Cancelled geri dönmez; Settled ise finalValue == traderFee + platformFee + customerPayout,
    ///      lastValue == finalValue, settledAt > 0. Released/Consumed rezervasyonlar geri dönmez.
    function invariant_statusMonotonic() public view {
        uint256 n = handler.agreementCount();
        for (uint256 i = 0; i < n; ++i) {
            uint256 id = handler.agreementIds(i);
            ITraderVault.Agreement memory a = vault.getAgreement(id);
            ITraderVault.AgreementStatus term = handler.terminalStatus(id);
            if (term != ITraderVault.AgreementStatus.None) {
                assertEq(uint8(a.status), uint8(term), "terminal status changed");
            }
            if (a.status == ITraderVault.AgreementStatus.Settled) {
                assertEq(a.finalValue, a.traderFee + a.platformFee + a.customerPayout, "settlement split");
                assertEq(a.lastValue, a.finalValue, "lastValue != finalValue");
                assertGt(a.settledAt, 0);
                assertLe(
                    a.traderFee + a.platformFee, a.finalValue > a.terms.principal ? a.finalValue - a.terms.principal : 0
                );
            } else {
                assertEq(a.settledAt, 0);
                assertEq(a.finalValue, 0);
            }
            if (a.status == ITraderVault.AgreementStatus.Active || a.status == ITraderVault.AgreementStatus.Settled) {
                assertGt(a.startTime, 0);
                assertEq(a.endTime, a.startTime + a.terms.durationSeconds);
            }
            assertLe(a.platformFeeBps, vault.MAX_PLATFORM_FEE_BPS());
        }
        uint256 m = handler.reservationCount();
        for (uint256 i = 0; i < m; ++i) {
            uint256 rid = handler.reservationIds(i);
            ITraderVault.Reservation memory r = vault.getReservation(rid);
            ITraderVault.ReservationStatus term = handler.terminalResStatus(rid);
            if (term != ITraderVault.ReservationStatus.None) {
                assertEq(uint8(r.status), uint8(term), "terminal reservation status changed");
            }
            assertLe(r.amount, r.original);
            if (r.status != ITraderVault.ReservationStatus.Open) {
                assertEq(r.amount, 0, "closed reservation with balance");
            } else {
                assertGt(r.amount, 0, "open reservation with zero balance");
            }
        }
    }

    /// @dev nextId / nextReservationId yalnız başarılı oluşturmada 1 artar; id'ler 1..n ardışık.
    function invariant_idsIncreaseByOne() public view {
        assertEq(vault.nextId(), 1 + handler.ghostAgreements(), "nextId");
        assertEq(vault.nextReservationId(), 1 + handler.ghostReservations(), "nextReservationId");
        uint256 n = handler.agreementCount();
        for (uint256 i = 0; i < n; ++i) {
            assertEq(handler.agreementIds(i), i + 1, "agreement ids not sequential");
            assertEq(vault.getAgreement(i + 1).id, i + 1);
        }
        uint256 m = handler.reservationCount();
        for (uint256 i = 0; i < m; ++i) {
            assertEq(handler.reservationIds(i), i + 1, "reservation ids not sequential");
        }
        // bir sonraki id henüz yok
        try vault.getAgreement(vault.nextId()) {
            revert("nextId already exists");
        } catch {}
    }
}
