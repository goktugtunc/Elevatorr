// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

import {TraderVault} from "../../src/TraderVault.sol";
import {ITraderVault} from "../../src/interfaces/ITraderVault.sol";
import {MockRouter} from "../../src/mocks/MockRouter.sol";
import {TestToken} from "../../src/mocks/TestToken.sol";
import {BlocklistToken} from "../mocks/BlocklistToken.sol";

/// @notice Invariant handler (§10.3): aktörler customer/trader/stranger (+ admin yalnız settle/pause),
///         eylemler reserve/release/releaseAll/propose/open/openReserved/fund/fundReserved/accept/cancel/
///         trade/settle/claim/setPrice/warp/setPaused. `fail_on_revert = false`: geçersiz kombinasyonlar
///         serbestçe revert eder; ghost sayaçlar yalnız başarılı çağrıda güncellenir.
///         Handler token'ları vault'a DOĞRUDAN asla göndermez (bakiye eşitliği invariant'ı için).
contract InvariantHandler is Test {
    TraderVault public vault;
    MockRouter public router;
    address public routerOwner;
    address public admin;

    address[] public actors; // customer, trader, stranger
    address[] public tokens; // usdc(6, base), alt(18, base), eurc(6), wbtc(8)
    mapping(address => uint256) public unit; // 10**decimals
    mapping(address => uint256) public fairValue; // 1 birim token'ın mikro-USD değeri (fiyat tabanı)

    address public blocklist; // orphan/claim yolu için (tokens içinde de yer alır); 0 = yok

    uint256[] public agreementIds;
    uint256[] public reservationIds;
    uint256 public ghostAgreements;
    uint256 public ghostReservations;

    // Monotonluk: ilk kez terminal duruma girildiğinde kaydedilir, asla ezilmez
    mapping(uint256 => ITraderVault.AgreementStatus) public terminalStatus;
    mapping(uint256 => ITraderVault.ReservationStatus) public terminalResStatus;

    // Sayaçlar (görünürlük / kalibrasyon)
    uint256 public nTrades;
    uint256 public nSettles;
    uint256 public nClaims;
    uint256 public nInKind;

    constructor(
        TraderVault vault_,
        MockRouter router_,
        address routerOwner_,
        address admin_,
        address[] memory actors_,
        address[] memory tokens_,
        uint256[] memory fairValues_,
        address blocklist_
    ) {
        blocklist = blocklist_;
        vault = vault_;
        router = router_;
        routerOwner = routerOwner_;
        admin = admin_;
        actors = actors_;
        tokens = tokens_;
        for (uint256 i = 0; i < tokens_.length; ++i) {
            unit[tokens_[i]] = 10 ** uint256(TestToken(tokens_[i]).decimals());
            fairValue[tokens_[i]] = fairValues_[i];
        }
    }

    /// @dev Tüm (a,b) çiftlerini adil fiyata kurar (setUp'tan ve in-kind sonrası çağrılır).
    function setFairPrices() public {
        for (uint256 i = 0; i < tokens.length; ++i) {
            for (uint256 j = 0; j < tokens.length; ++j) {
                if (i != j) _setFair(tokens[i], tokens[j], 100);
            }
        }
    }

    function _setFair(address a, address b, uint256 factorPct) internal {
        // out = in * (valA * unitB) / (unitA * valB) * factor/100
        uint256 num = fairValue[a] * unit[b] * factorPct;
        uint256 den = unit[a] * fairValue[b] * 100;
        vm.prank(routerOwner);
        router.setPrice(a, b, num, den);
    }

    // ------------------------------------------------------------------
    // Görünümler
    // ------------------------------------------------------------------

    function agreementCount() external view returns (uint256) {
        return agreementIds.length;
    }

    function reservationCount() external view returns (uint256) {
        return reservationIds.length;
    }

    function tokenCount() external view returns (uint256) {
        return tokens.length;
    }

    // ------------------------------------------------------------------
    // Seçiciler
    // ------------------------------------------------------------------

    function _actor(uint256 seed) internal view returns (address) {
        return actors[seed % actors.length];
    }

    function _token(uint256 seed) internal view returns (address) {
        return tokens[seed % tokens.length];
    }

    function _baseToken(uint256 seed) internal view returns (address) {
        // usdc veya alt
        return tokens[seed % 2];
    }

    function _agreement(uint256 seed) internal view returns (uint256) {
        if (agreementIds.length == 0) return 0;
        return agreementIds[seed % agreementIds.length];
    }

    /// @dev Verilen durumdaki bir anlaşmayı arar (seed'den başlayıp dairesel); yoksa rastgele biri (revert yolu).
    function _agreementWith(uint256 seed, ITraderVault.AgreementStatus want) internal view returns (uint256) {
        uint256 n = agreementIds.length;
        if (n == 0) return 0;
        for (uint256 k = 0; k < n; ++k) {
            uint256 id = agreementIds[(seed + k) % n];
            if (vault.getAgreement(id).status == want) return id;
        }
        return agreementIds[seed % n];
    }

    /// @dev Süresi dolmamış Active anlaşma arar; yoksa herhangi bir Active (Expired yolu), yoksa rastgele.
    function _liveAgreement(uint256 seed) internal view returns (uint256) {
        uint256 n = agreementIds.length;
        if (n == 0) return 0;
        for (uint256 k = 0; k < n; ++k) {
            uint256 id = agreementIds[(seed + k) % n];
            ITraderVault.Agreement memory a = vault.getAgreement(id);
            if (a.status == ITraderVault.AgreementStatus.Active && a.endTime > block.timestamp) return id;
        }
        return _agreementWith(seed, ITraderVault.AgreementStatus.Active);
    }

    function _reservation(uint256 seed) internal view returns (uint256) {
        if (reservationIds.length == 0) return 0;
        return reservationIds[seed % reservationIds.length];
    }

    /// @dev Açık bir rezervasyon arar; yoksa rastgele biri.
    function _openReservation(uint256 seed) internal view returns (uint256) {
        uint256 n = reservationIds.length;
        if (n == 0) return 0;
        for (uint256 k = 0; k < n; ++k) {
            uint256 rid = reservationIds[(seed + k) % n];
            if (vault.getReservation(rid).status == ITraderVault.ReservationStatus.Open) return rid;
        }
        return reservationIds[seed % n];
    }

    function _terms(address customer, address trader, address base, uint256 principal, uint256 seed)
        internal
        pure
        returns (ITraderVault.Terms memory t)
    {
        t.customer = customer;
        t.trader = trader;
        t.baseToken = base;
        t.principal = principal;
        t.durationSeconds = uint64(bound(seed, 86_400, 30 * 86_400));
        t.commissionBps = uint16(bound(seed >> 8, 0, 5_000));
        t.maxDrawdownBps = uint16(bound(seed >> 24, 100, 10_000));
        t.listingRef = keccak256(abi.encode(seed));
    }

    function _mintAndApprove(address who, address token, uint256 amount) internal {
        TestToken(token).mint(who, amount);
        vm.prank(who);
        IERC20(token).approve(address(vault), type(uint256).max);
    }

    function _recordAgreement(uint256 id) internal {
        agreementIds.push(id);
        ghostAgreements++;
    }

    function _syncStatuses() internal {
        for (uint256 i = 0; i < agreementIds.length; ++i) {
            uint256 id = agreementIds[i];
            ITraderVault.AgreementStatus st = vault.getAgreement(id).status;
            if (
                terminalStatus[id] == ITraderVault.AgreementStatus.None
                    && (st == ITraderVault.AgreementStatus.Settled || st == ITraderVault.AgreementStatus.Cancelled)
            ) {
                terminalStatus[id] = st;
            }
        }
        for (uint256 i = 0; i < reservationIds.length; ++i) {
            uint256 rid = reservationIds[i];
            ITraderVault.ReservationStatus st = vault.getReservation(rid).status;
            if (
                terminalResStatus[rid] == ITraderVault.ReservationStatus.None
                    && (st == ITraderVault.ReservationStatus.Released || st == ITraderVault.ReservationStatus.Consumed)
            ) {
                terminalResStatus[rid] = st;
            }
        }
    }

    // ------------------------------------------------------------------
    // Eylemler: rezervasyon
    // ------------------------------------------------------------------

    function reserve(uint256 actorSeed, uint256 tokenSeed, uint256 amount) external {
        address who = _actor(actorSeed);
        address token = _baseToken(tokenSeed);
        amount = bound(amount, 1, 100_000 * unit[token]);
        _mintAndApprove(who, token, amount);
        vm.prank(who);
        uint256 rid = vault.reserve(token, amount, keccak256(abi.encode(amount, who)));
        reservationIds.push(rid);
        ghostReservations++;
        _syncStatuses();
    }

    function release(uint256 resSeed, uint256 amount) external {
        uint256 rid = _openReservation(resSeed);
        ITraderVault.Reservation memory r = vault.getReservation(rid);
        amount = amount % 11 == 0 ? bound(amount, 0, r.amount + 1) : bound(amount, 1, r.amount == 0 ? 1 : r.amount);
        vm.prank(r.customer);
        vault.release(rid, amount);
        _syncStatuses();
    }

    function releaseAll(uint256 resSeed) external {
        uint256 rid = _openReservation(resSeed);
        ITraderVault.Reservation memory r = vault.getReservation(rid);
        vm.prank(r.customer);
        vault.releaseAll(rid);
        _syncStatuses();
    }

    // ------------------------------------------------------------------
    // Eylemler: yaşam döngüsü
    // ------------------------------------------------------------------

    function propose(uint256 custSeed, uint256 traderSeed, uint256 baseSeed, uint256 principal, uint256 seed) external {
        address customer = _actor(custSeed);
        address trader = _actor(traderSeed);
        if (trader == customer && seed % 5 != 0) trader = actors[(traderSeed + 1) % actors.length];
        address base = _baseToken(baseSeed);
        principal = bound(principal, 1, 100_000 * unit[base]);
        vm.prank(trader);
        uint256 id = vault.propose(_terms(customer, trader, base, principal, seed));
        _recordAgreement(id);
        _syncStatuses();
    }

    function open(uint256 custSeed, uint256 traderSeed, uint256 baseSeed, uint256 principal, uint256 seed) external {
        address customer = _actor(custSeed);
        address trader = _actor(traderSeed);
        if (trader == customer && seed % 5 != 0) trader = actors[(traderSeed + 1) % actors.length];
        address base = _baseToken(baseSeed);
        principal = bound(principal, 1, 100_000 * unit[base]);
        _mintAndApprove(customer, base, principal);
        vm.prank(customer);
        uint256 id = vault.open(_terms(customer, trader, base, principal, seed));
        _recordAgreement(id);
        _syncStatuses();
    }

    function openReserved(uint256 resSeed, uint256 traderSeed, uint256 principalPct, uint256 seed) external {
        uint256 rid = _openReservation(resSeed);
        ITraderVault.Reservation memory r = vault.getReservation(rid);
        address trader = _actor(traderSeed);
        if (trader == r.customer) trader = actors[(traderSeed + 1) % actors.length];
        uint256 principal = bound(principalPct, 1, r.amount == 0 ? 1 : r.amount);
        vm.prank(r.customer);
        uint256 id = vault.openReserved(_terms(r.customer, trader, r.token, principal, seed), rid);
        _recordAgreement(id);
        _syncStatuses();
    }

    function fund(uint256 idSeed) external {
        uint256 id = _agreementWith(idSeed, ITraderVault.AgreementStatus.Proposed);
        ITraderVault.Agreement memory a = vault.getAgreement(id);
        _mintAndApprove(a.terms.customer, a.terms.baseToken, a.terms.principal);
        vm.prank(a.terms.customer);
        vault.fund(id);
        _syncStatuses();
    }

    function fundReserved(uint256 idSeed, uint256 resSeed) external {
        uint256 rid = _openReservation(resSeed);
        ITraderVault.Reservation memory r = vault.getReservation(rid);
        uint256 id = _agreementWith(idSeed, ITraderVault.AgreementStatus.Proposed);
        // uyan (müşteri + token + tutar) Proposed anlaşma ara; yoksa rastgele (Mismatch/Insufficient yolu)
        uint256 n = agreementIds.length;
        for (uint256 k = 0; k < n; ++k) {
            uint256 cand = agreementIds[(idSeed + k) % n];
            ITraderVault.Agreement memory c = vault.getAgreement(cand);
            if (
                c.status == ITraderVault.AgreementStatus.Proposed && c.terms.customer == r.customer
                    && c.terms.baseToken == r.token && c.terms.principal <= r.amount
            ) {
                id = cand;
                break;
            }
        }
        ITraderVault.Agreement memory a = vault.getAgreement(id);
        vm.prank(a.terms.customer);
        vault.fundReserved(id, rid);
        _syncStatuses();
    }

    function accept(uint256 idSeed) external {
        uint256 id = _agreementWith(idSeed, ITraderVault.AgreementStatus.Funded);
        ITraderVault.Agreement memory a = vault.getAgreement(id);
        vm.prank(a.terms.trader);
        vault.accept(id);
        _syncStatuses();
    }

    function cancel(uint256 idSeed, uint256 actorSeed) external {
        uint256 id = _agreementWith(
            idSeed, actorSeed % 3 == 0 ? ITraderVault.AgreementStatus.Proposed : ITraderVault.AgreementStatus.Funded
        );
        ITraderVault.Agreement memory a = vault.getAgreement(id);
        address who = actorSeed % 2 == 0 ? a.terms.customer : a.terms.trader;
        if (actorSeed % 7 == 0) who = _actor(actorSeed); // bazen yabancı (NotParty)
        vm.prank(who);
        vault.cancel(id);
        _syncStatuses();
    }

    // ------------------------------------------------------------------
    // Eylemler: trade / settle / claim
    // ------------------------------------------------------------------

    function trade(uint256 idSeed, uint256 inSeed, uint256 outSeed, uint256 pct, uint256 minPct) external {
        uint256 id = _liveAgreement(idSeed);
        ITraderVault.Agreement memory a = vault.getAgreement(id);
        (address[] memory held, uint256[] memory amts) = vault.getBalances(id);
        address tokenIn = held[inSeed % held.length];
        uint256 balIn = amts[inSeed % held.length];
        address tokenOut = _token(outSeed);
        if (tokenOut == tokenIn && outSeed % 9 != 0) tokenOut = tokens[(outSeed + 1) % tokens.length];
        uint256 amountIn = balIn == 0 ? 1 : bound(pct, 1, balIn);
        if (balIn > 0 && pct % 4 != 0) amountIn = bound(pct, 1, balIn / 4 + 1); // çoğunlukla küçük dilim
        uint256 minOut = 1;
        address[] memory path = new address[](2);
        path[0] = tokenIn;
        path[1] = tokenOut;
        try router.getAmountsOut(amountIn, path) returns (uint256[] memory q) {
            uint256 quote = q[1];
            // çoğunlukla quote'un kendisi, bazen biraz altı, nadiren üstü (RouterError yolu)
            uint256 f = bound(minPct, 90, 100);
            if (minPct % 23 == 0) f = 101; // nadiren RouterError yolu
            minOut = quote * f / 100;
            if (minOut == 0) minOut = 1;
        } catch {}
        vm.prank(a.terms.trader);
        vault.trade(id, tokenIn, tokenOut, amountIn, minOut, uint64(block.timestamp) + 60);
        nTrades++;
        _syncStatuses();
    }

    function settle(uint256 idSeed, uint256 actorSeed, uint256 minMode) external {
        uint256 id = _agreementWith(idSeed, ITraderVault.AgreementStatus.Active);
        ITraderVault.Agreement memory a = vault.getAgreement(id);
        uint256 m = actorSeed % 4;
        address who = m == 0 ? a.terms.customer : m == 1 ? a.terms.trader : m == 2 ? admin : _actor(actorSeed);
        bool isParty = who == a.terms.customer || who == a.terms.trader;
        uint256[] memory minOuts;
        if (isParty || minMode % 3 == 0) {
            (address[] memory held, uint256[] memory amts) = vault.getBalances(id);
            minOuts = new uint256[](held.length - 1);
            for (uint256 i = 1; i < held.length; ++i) {
                if (minMode % 2 == 0) {
                    minOuts[i - 1] = 0;
                } else {
                    address[] memory path = new address[](2);
                    path[0] = held[i];
                    path[1] = a.terms.baseToken;
                    try router.getAmountsOut(amts[i] == 0 ? 1 : amts[i], path) returns (uint256[] memory q) {
                        minOuts[i - 1] = q[1] * 99 / 100;
                    } catch {
                        minOuts[i - 1] = 0;
                    }
                }
            }
        } else {
            minOuts = new uint256[](0);
        }
        bool forced = minMode % 3 == 0;
        if (forced) _forceInKind(id, a);
        vm.prank(who);
        vault.settle(id, minOuts);
        nSettles++;
        if (forced) setFairPrices(); // silinen rotaları geri kur
        if (_orphanCount(id) > 0) nInKind++; // orphan kaldı -> claim yolu açık
        _syncStatuses();
    }

    /// @dev Tutulan base dışı bacakların base fiyatını siler (in-kind yolu); blocklist bacağı varsa
    ///      müşteriyi bloklar (orphan yolu).
    function _forceInKind(uint256 id, ITraderVault.Agreement memory a) internal {
        (address[] memory held,) = vault.getBalances(id);
        for (uint256 i = 1; i < held.length; ++i) {
            vm.prank(routerOwner);
            router.clearPrice(held[i], a.terms.baseToken);
            if (held[i] == blocklist) BlocklistToken(blocklist).setBlocked(a.terms.customer, true);
        }
    }

    function _orphanCount(uint256 id) internal view returns (uint256 n) {
        (, uint256[] memory amts) = vault.getBalances(id);
        for (uint256 i = 1; i < amts.length; ++i) {
            if (amts[i] > 0) n++;
        }
    }

    function claim(uint256 idSeed, uint256 tokenSeed) external {
        uint256 id = _agreementWith(idSeed, ITraderVault.AgreementStatus.Settled);
        ITraderVault.Agreement memory a = vault.getAgreement(id);
        // orphan varsa onu hedefle
        address token = a.tokens[a.tokens.length > 1 ? 1 + (tokenSeed % (a.tokens.length - 1)) : 0];
        if (blocklist != address(0) && tokenSeed % 2 == 0) {
            BlocklistToken(blocklist).setBlocked(a.terms.customer, false);
        }
        vm.prank(a.terms.customer);
        vault.claim(id, token);
        nClaims++;
        _syncStatuses();
    }

    // ------------------------------------------------------------------
    // Eylemler: ortam
    // ------------------------------------------------------------------

    /// @dev Fiyatı adil değerin %90..%110'una çeker; yön bazlı. Bazen fiyatı siler (in-kind yolu).
    function setPrice(uint256 aSeed, uint256 bSeed, uint256 factorPct, uint256 clearSeed) external {
        address a = _token(aSeed);
        address b = _token(bSeed);
        if (a == b) return;
        if (clearSeed % 29 == 0) {
            vm.prank(routerOwner);
            router.clearPrice(a, b);
            return;
        }
        _setFair(a, b, bound(factorPct, 90, 110));
    }

    function warp(uint256 secs) external {
        secs = bound(secs, 1, 3 * 86_400);
        vm.warp(block.timestamp + secs);
    }

    /// @dev Blocklist token'ında bir aktörü bloklar/açar: in-kind teslim başarısız -> orphan -> claim.
    function toggleBlock(uint256 actorSeed, uint256 seed) external {
        if (blocklist == address(0)) return;
        BlocklistToken(blocklist).setBlocked(_actor(actorSeed), seed % 2 == 0);
    }

    function setPaused(uint256 seed) external {
        vm.prank(admin);
        vault.setPaused(seed % 10 == 0); // çoğunlukla açık
    }
}
