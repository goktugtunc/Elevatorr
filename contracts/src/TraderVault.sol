// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Initializable} from "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";
import {UUPSUpgradeable} from "@openzeppelin/contracts-upgradeable/proxy/utils/UUPSUpgradeable.sol";
import {Ownable2StepUpgradeable} from "@openzeppelin/contracts-upgradeable/access/Ownable2StepUpgradeable.sol";
import {ReentrancyGuardUpgradeable} from "@openzeppelin/contracts-upgradeable/utils/ReentrancyGuardUpgradeable.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

import {ITraderVault} from "./interfaces/ITraderVault.sol";
import {IUniswapV2Router02Like} from "./interfaces/IUniswapV2Router02Like.sol";
import {SettleMath} from "./libraries/SettleMath.sol";

/// @title TraderVault
/// @notice Müşteri sermayesini emanette tutan, trader'ın yalnız allow-list'teki tokenlar arasında router
///         üzerinden işlem yapabildiği ve anlaşma sonunda base token'da bölüşüm yapan kasa (UUPS).
///         Davranış: docs/monad/01-kontrat-spec.md (bağlayıcı).
contract TraderVault is
    ITraderVault,
    Initializable,
    UUPSUpgradeable,
    Ownable2StepUpgradeable,
    ReentrancyGuardUpgradeable
{
    using SafeERC20 for IERC20;

    // ---------------------------------------------------------------------
    // Sabitler (§3.1)
    // ---------------------------------------------------------------------

    uint8 public constant MAX_TOKENS = 6;
    uint64 public constant MIN_DURATION = 86_400; // 1 gün
    uint64 public constant MAX_DURATION = 94_608_000; // 3 x 365 gün
    uint16 public constant MAX_COMMISSION_BPS = 5_000;
    uint16 public constant MAX_PLATFORM_FEE_BPS = 1_000;
    uint16 public constant MIN_DRAWDOWN_BPS = 100;
    uint16 public constant MAX_SETTLE_SLIPPAGE_BPS = 5_000;
    uint64 public constant KEEPER_GRACE = 604_800; // 7 gün
    uint16 public constant BPS_DENOM = 10_000;
    uint64 public constant MAX_ROUTER_DELAY = 2_592_000; // 30 gün
    uint64 public constant DEFAULT_ROUTER_DELAY = 86_400; // 24 saat

    // ---------------------------------------------------------------------
    // State (§3.2; ZORUNLU sıra, append-only, __gap)
    // ---------------------------------------------------------------------

    Config private _config;
    uint256 private _nextId;
    uint256 private _nextReservationId;
    mapping(uint256 => Agreement) private _agreements;
    mapping(uint256 => mapping(address => uint256)) private _balances;
    mapping(uint256 => Reservation) private _reservations;
    mapping(address => TokenInfo) private _tokens;
    uint256[50] private __gap;

    // ---------------------------------------------------------------------
    // Kurulum
    // ---------------------------------------------------------------------

    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }

    modifier whenNotPaused() {
        if (_config.paused) revert Paused();
        _;
    }

    /// @inheritdoc ITraderVault
    function initialize(
        address owner_,
        address router_,
        address feeRecipient_,
        uint16 platformFeeBps_,
        uint16 settleSlippageBps_,
        uint64 routerDelay_
    ) external initializer {
        if (owner_ == address(0) || feeRecipient_ == address(0)) revert ZeroAddress();
        if (router_.code.length == 0) revert InvalidRouter();
        if (platformFeeBps_ > MAX_PLATFORM_FEE_BPS || settleSlippageBps_ > MAX_SETTLE_SLIPPAGE_BPS) {
            revert InvalidTerms();
        }
        if (routerDelay_ > MAX_ROUTER_DELAY) revert InvalidTerms();

        __Ownable_init(owner_);
        __Ownable2Step_init();
        __ReentrancyGuard_init();
        __UUPSUpgradeable_init();

        _config = Config({
            router: router_,
            feeRecipient: feeRecipient_,
            platformFeeBps: platformFeeBps_,
            settleSlippageBps: settleSlippageBps_,
            paused: false,
            pendingRouter: address(0),
            routerActivationTime: 0,
            routerDelay: routerDelay_
        });
        _nextId = 1;
        _nextReservationId = 1;
    }

    function _authorizeUpgrade(address) internal override onlyOwner {}

    // ---------------------------------------------------------------------
    // Admin (§4.2)
    // ---------------------------------------------------------------------

    function setToken(address token, bool allowed, bool isBase) external onlyOwner {
        if (token == address(0)) revert ZeroAddress();
        if (allowed && token.code.length == 0) revert InvalidToken();
        bool base = allowed && isBase;
        _tokens[token] = TokenInfo({allowed: allowed, isBase: base});
        emit TokenSet(token, allowed, base);
    }

    function proposeRouterChange(address newRouter) external onlyOwner {
        if (newRouter == address(0)) revert ZeroAddress();
        if (newRouter.code.length == 0) revert InvalidRouter();
        uint64 activation = uint64(block.timestamp) + _config.routerDelay;
        _config.pendingRouter = newRouter;
        _config.routerActivationTime = activation;
        emit RouterChangeProposed(newRouter, activation);
    }

    function applyRouterChange() external onlyOwner {
        address pending = _config.pendingRouter;
        if (pending == address(0)) revert NoPendingRouterChange();
        uint64 activation = _config.routerActivationTime;
        if (uint64(block.timestamp) < activation) revert RouterChangeNotReady(activation);
        _config.router = pending;
        _config.pendingRouter = address(0);
        _config.routerActivationTime = 0;
        _emitConfigChanged("router");
    }

    function cancelRouterChange() external onlyOwner {
        address pending = _config.pendingRouter;
        if (pending == address(0)) revert NoPendingRouterChange();
        _config.pendingRouter = address(0);
        _config.routerActivationTime = 0;
        emit RouterChangeCancelled(pending);
    }

    function setFees(uint16 platformFeeBps, address feeRecipient) external onlyOwner {
        if (platformFeeBps > MAX_PLATFORM_FEE_BPS) revert InvalidTerms();
        if (feeRecipient == address(0)) revert ZeroAddress();
        _config.platformFeeBps = platformFeeBps;
        _config.feeRecipient = feeRecipient;
        _emitConfigChanged("fees");
    }

    function setPaused(bool paused_) external onlyOwner {
        _config.paused = paused_;
        _emitConfigChanged("paused");
    }

    function setSettleSlippage(uint16 bps) external onlyOwner {
        if (bps > MAX_SETTLE_SLIPPAGE_BPS) revert InvalidTerms();
        _config.settleSlippageBps = bps;
        _emitConfigChanged("slippage");
    }

    function _emitConfigChanged(bytes32 key) private {
        Config storage c = _config;
        emit ConfigChanged(key, c.router, c.platformFeeBps, c.feeRecipient, c.paused, c.settleSlippageBps);
    }

    // ---------------------------------------------------------------------
    // Rezervasyonlar (§4.3)
    // ---------------------------------------------------------------------

    function reserve(address token, uint256 amount, bytes32 listingRef)
        external
        nonReentrant
        whenNotPaused
        returns (uint256 id)
    {
        if (amount == 0) revert ZeroAmount();
        TokenInfo storage info = _tokens[token];
        if (!(info.allowed && info.isBase)) revert TokenNotAllowed();

        id = _nextReservationId++;
        _reservations[id] = Reservation({
            id: id,
            customer: msg.sender,
            token: token,
            amount: amount,
            original: amount,
            status: ReservationStatus.Open,
            createdAt: uint64(block.timestamp),
            listingRef: listingRef
        });
        emit Reserved(id, msg.sender, token, amount, listingRef);

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);
    }

    function release(uint256 id, uint256 amount) external nonReentrant returns (uint256) {
        Reservation storage res = _openReservationOf(id);
        return _release(res, amount);
    }

    function releaseAll(uint256 id) external nonReentrant returns (uint256) {
        Reservation storage res = _openReservationOf(id);
        return _release(res, res.amount);
    }

    /// @dev Adım 1–3: NotFound -> Unauthorized -> Closed
    function _openReservationOf(uint256 id) private view returns (Reservation storage res) {
        res = _reservations[id];
        if (res.status == ReservationStatus.None) revert ReservationNotFound();
        if (msg.sender != res.customer) revert Unauthorized();
        if (res.status != ReservationStatus.Open) revert ReservationClosed();
    }

    function _release(Reservation storage res, uint256 amount) private returns (uint256) {
        if (amount == 0) revert ZeroAmount();
        if (amount > res.amount) revert ReservationInsufficient();
        uint256 remaining = res.amount - amount;
        res.amount = remaining;
        if (remaining == 0) res.status = ReservationStatus.Released;
        emit Released(res.id, res.customer, res.token, amount, remaining);

        IERC20(res.token).safeTransfer(res.customer, amount);
        return amount;
    }

    // ---------------------------------------------------------------------
    // Yaşam döngüsü (§4.4)
    // ---------------------------------------------------------------------

    function propose(Terms calldata terms) external whenNotPaused returns (uint256 id) {
        if (msg.sender != terms.trader) revert Unauthorized();
        _validateTerms(terms);
        id = _nextId++;
        _newAgreement(id, terms, AgreementStatus.Proposed, msg.sender);
        emit Proposed(id, terms.trader, terms.customer, terms.principal, terms.baseToken);
    }

    function open(Terms calldata terms) external nonReentrant whenNotPaused returns (uint256 id) {
        if (msg.sender != terms.customer) revert Unauthorized();
        _validateTerms(terms);
        id = _nextId++;
        _newAgreement(id, terms, AgreementStatus.Funded, msg.sender);
        _balances[id][terms.baseToken] = terms.principal;
        emit Opened(id, terms.trader, terms.customer, terms.principal, terms.baseToken);

        IERC20(terms.baseToken).safeTransferFrom(msg.sender, address(this), terms.principal);
    }

    function openReserved(Terms calldata terms, uint256 reservationId) external whenNotPaused returns (uint256 id) {
        if (msg.sender != terms.customer) revert Unauthorized();
        _validateTerms(terms);
        id = _nextId++;
        Agreement storage ag = _newAgreement(id, terms, AgreementStatus.Funded, msg.sender);
        _drawReservation(reservationId, ag);
        emit Opened(id, terms.trader, terms.customer, terms.principal, terms.baseToken);
    }

    function fund(uint256 id) external nonReentrant {
        Agreement storage ag = _fundChecks(id);
        _balances[id][ag.terms.baseToken] = ag.terms.principal;
        _activate(ag);

        IERC20(ag.terms.baseToken).safeTransferFrom(msg.sender, address(this), ag.terms.principal);
    }

    function fundReserved(uint256 id, uint256 reservationId) external {
        Agreement storage ag = _fundChecks(id);
        _drawReservation(reservationId, ag);
        _activate(ag);
    }

    /// @dev fund/fundReserved ortak 5 kontrol: NotFound -> Unauthorized -> Paused -> WrongStatus -> TokenNotAllowed
    function _fundChecks(uint256 id) private view returns (Agreement storage ag) {
        ag = _agreements[id];
        if (ag.status == AgreementStatus.None) revert NotFound();
        if (msg.sender != ag.terms.customer) revert Unauthorized();
        if (_config.paused) revert Paused();
        if (ag.status != AgreementStatus.Proposed) revert WrongStatus();
        TokenInfo storage info = _tokens[ag.terms.baseToken];
        if (!(info.allowed && info.isBase)) revert TokenNotAllowed();
    }

    function accept(uint256 id) external {
        Agreement storage ag = _agreements[id];
        if (ag.status == AgreementStatus.None) revert NotFound();
        if (msg.sender != ag.terms.trader) revert Unauthorized();
        if (_config.paused) revert Paused();
        if (ag.status != AgreementStatus.Funded) revert WrongStatus();
        _activate(ag);
    }

    function cancel(uint256 id) external nonReentrant {
        Agreement storage ag = _agreements[id];
        AgreementStatus st = ag.status;
        if (st == AgreementStatus.None) revert NotFound();
        if (st == AgreementStatus.Proposed) {
            if (msg.sender != ag.proposer) revert NotParty();
        } else if (st == AgreementStatus.Funded) {
            if (msg.sender != ag.terms.customer && msg.sender != ag.terms.trader) revert NotParty();
        } else {
            revert WrongStatus();
        }

        uint256 refunded = 0;
        address base = ag.terms.baseToken;
        if (st == AgreementStatus.Funded) {
            refunded = _balances[id][base];
            delete _balances[id][base];
        }
        ag.status = AgreementStatus.Cancelled;
        emit Cancelled(id, refunded);

        if (refunded > 0) IERC20(base).safeTransfer(ag.terms.customer, refunded);
    }

    // ---------------------------------------------------------------------
    // trade (§4.5)
    // ---------------------------------------------------------------------

    function trade(uint256 id, address tokenIn, address tokenOut, uint256 amountIn, uint256 minOut, uint64 deadline)
        external
        nonReentrant
        returns (uint256 amountOut)
    {
        Agreement storage ag = _agreements[id];
        uint256 balIn = _tradeChecks(ag, tokenIn, tokenOut, amountIn, minOut, deadline);

        // Efektler (adım 9–10)
        _tradeEffects(ag, tokenIn, tokenOut, balIn, amountIn);

        // Etkileşim (adım 11)
        uint256 before = IERC20(tokenOut).balanceOf(address(this));
        uint256 reported = _swapViaRouter(tokenIn, tokenOut, amountIn, minOut, deadline);
        amountOut = _credited(reported, before, IERC20(tokenOut).balanceOf(address(this)));
        if (amountOut < minOut) revert SlippageExceeded();

        // Adım 12–15
        _balances[id][tokenOut] += amountOut;
        uint256 valueAfter = _tradePostChecks(ag, tokenOut);
        ag.lastValue = valueAfter;
        emit Traded(id, ag.terms.trader, tokenIn, tokenOut, amountIn, amountOut, valueAfter);
    }

    /// @dev Adım 1–8; `balIn` döner.
    function _tradeChecks(
        Agreement storage ag,
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 minOut,
        uint64 deadline
    ) private view returns (uint256 balIn) {
        if (ag.status == AgreementStatus.None) revert NotFound();
        if (msg.sender != ag.terms.trader) revert Unauthorized();
        if (_config.paused) revert Paused();
        if (ag.status != AgreementStatus.Active) revert WrongStatus();
        uint64 nowTs = uint64(block.timestamp);
        if (nowTs >= ag.endTime || deadline < nowTs) revert Expired();
        if (amountIn == 0 || minOut == 0) revert ZeroAmount();
        if (tokenIn == tokenOut || !_tokens[tokenIn].allowed || !_tokens[tokenOut].allowed) revert TokenNotAllowed();
        balIn = _balances[ag.id][tokenIn];
        if (balIn < amountIn) revert InsufficientBalance();
    }

    /// @dev Adım 9–10: tokenOut listeye eklenir (tokenIn çıkarılmadan ÖNCE sayılır), tokenIn bakiyesi düşülür.
    function _tradeEffects(Agreement storage ag, address tokenIn, address tokenOut, uint256 balIn, uint256 amountIn)
        private
    {
        uint256 id = ag.id;
        if (!_contains(ag.tokens, tokenOut)) {
            if (ag.tokens.length >= MAX_TOKENS) revert TooManyTokens();
            ag.tokens.push(tokenOut);
        }
        uint256 newIn = balIn - amountIn;
        if (newIn == 0 && tokenIn != ag.terms.baseToken) {
            delete _balances[id][tokenIn];
            _removeToken(ag.tokens, tokenIn);
        } else {
            _balances[id][tokenIn] = newIn;
        }
    }

    /// @dev Adım 13–14: route-back ve drawdown kontrolü; `valueAfter` döner.
    function _tradePostChecks(Agreement storage ag, address tokenOut) private view returns (uint256 valueAfter) {
        uint256 outQuote;
        (valueAfter, outQuote) = _valuation(ag, tokenOut);
        if (tokenOut != ag.terms.baseToken && outQuote == 0) revert TokenNotAllowed();
        if (valueAfter < SettleMath.drawdownFloor(ag.terms.principal, ag.terms.maxDrawdownBps)) {
            revert DrawdownBreached();
        }
    }

    // ---------------------------------------------------------------------
    // settle (§4.6)
    // ---------------------------------------------------------------------

    function settle(uint256 id, uint256[] calldata minOuts) external nonReentrant {
        Agreement storage ag = _agreements[id];
        if (ag.status == AgreementStatus.None) revert NotFound();
        if (ag.status != AgreementStatus.Active) revert WrongStatus();
        uint64 nowTs = uint64(block.timestamp);
        (bool isParty, bool isTrader) = _settleAccess(ag, minOuts.length, nowTs);

        // ---- efektler önce ----
        ag.status = AgreementStatus.Settled;
        ag.settledAt = nowTs;

        uint256 finalValue = _settleLiquidate(ag, minOuts, isParty);

        if (isTrader && finalValue < SettleMath.drawdownFloor(ag.terms.principal, ag.terms.maxDrawdownBps)) {
            revert DrawdownBreached();
        }
        if (!isParty && finalValue < SettleMath.minOutWithSlippage(ag.lastValue, _config.settleSlippageBps)) {
            revert SlippageExceeded();
        }

        _settlePayout(ag, finalValue);
    }

    /// @dev Kim çağırıyor, minOuts uzunluğu ve zaman kontrolleri.
    function _settleAccess(Agreement storage ag, uint256 minOutsLen, uint64 nowTs)
        private
        view
        returns (bool isParty, bool isTrader)
    {
        uint256 nonBase = ag.tokens.length - 1;
        bool isCustomer = msg.sender == ag.terms.customer;
        isTrader = msg.sender == ag.terms.trader;
        isParty = isCustomer || isTrader;
        if (isParty) {
            if (minOutsLen != nonBase) revert InvalidAmount();
        } else {
            if (nowTs < ag.endTime) revert NotExpired();
            bool isAdmin = msg.sender == owner();
            if (!isAdmin && nowTs < ag.endTime + KEEPER_GRACE) revert NotExpired();
            if (minOutsLen != 0 && minOutsLen != nonBase) revert InvalidAmount();
        }
    }

    /// @dev Base dışı her bacağı base'e çevirir (veya in-kind teslim eder); `ag.tokens`'ı
    ///      `[base] + orphan bacaklar` olarak yeniden kurar; `finalValue` döner (in-kind bacaklar dahil değil).
    function _settleLiquidate(Agreement storage ag, uint256[] calldata minOuts, bool isParty)
        private
        returns (uint256 finalValue)
    {
        address base = ag.terms.baseToken;
        address[] memory held = ag.tokens; // bellek kopyası
        bool useSupplied = minOuts.length != 0;

        // kept listesi: base + teslim edilemeyen in-kind bacaklar (loop içinde push edilir)
        delete ag.tokens;
        ag.tokens.push(base);

        uint256 baseBefore = IERC20(base).balanceOf(address(this));
        uint256 reportedTotal = 0;
        uint256 minTotal = 0; // swap edilen bacakların minOut toplamı
        for (uint256 i = 1; i < held.length; ++i) {
            (uint256 reported, uint256 minOut) = _settleLeg(ag, held[i], useSupplied ? minOuts[i - 1] : 0, isParty);
            reportedTotal += reported;
            minTotal += minOut;
        }
        uint256 liquidated = _credited(reportedTotal, baseBefore, IERC20(base).balanceOf(address(this)));
        // Gerçekten gelen base, istenen minimumların toplamının altındaysa (router bildirdiğinden az teslim
        // etti) settle geri alınır; `trade` adım 11 ile aynı savunma.
        if (liquidated < minTotal) revert SlippageExceeded();
        finalValue = _balances[ag.id][base] + liquidated;
    }

    /// @dev Tek bacak: bakiye swap'tan ÖNCE sıfırlanır. Quote 0 -> in-kind (başarısızsa orphan olarak kalır).
    ///      Swap edilen bacak için (reported, uygulanan minOut) döner; in-kind bacak için (0, 0).
    function _settleLeg(Agreement storage ag, address token, uint256 supplied, bool isParty)
        private
        returns (uint256 reported, uint256 minOut)
    {
        uint256 id = ag.id;
        uint256 bal = _balances[id][token];
        if (bal == 0) return (0, 0);
        delete _balances[id][token];

        address base = ag.terms.baseToken;
        uint256 q = _quote(token, base, bal);
        if (q == 0) {
            bool delivered = _tryTransfer(token, ag.terms.customer, bal);
            if (!delivered) {
                _balances[id][token] = bal;
                ag.tokens.push(token);
            }
            emit Unliquidated(id, token, bal, delivered);
            return (0, 0);
        }
        minOut = isParty ? supplied : _max(supplied, SettleMath.minOutWithSlippage(q, _config.settleSlippageBps));
        reported = _swapViaRouter(token, base, bal, minOut, uint64(block.timestamp));
    }

    /// @dev Bölüşüm ve ödemeler: fee bacakları try, müşteri düz transfer.
    function _settlePayout(Agreement storage ag, uint256 finalValue) private {
        (uint256 profit, uint256 traderFee, uint256 platformFee, uint256 customerPayout) = SettleMath.settlement(
            finalValue,
            ag.terms.principal,
            ag.terms.commissionBps,
            ag.platformFeeBps /* snapshot */
        );

        address base = ag.terms.baseToken;
        delete _balances[ag.id][base];
        ag.finalValue = finalValue;
        ag.lastValue = finalValue;

        if (!_tryTransfer(base, ag.terms.trader, traderFee)) {
            customerPayout += traderFee;
            traderFee = 0;
        }
        if (!_tryTransfer(base, _config.feeRecipient, platformFee)) {
            customerPayout += platformFee;
            platformFee = 0;
        }
        ag.traderFee = traderFee;
        ag.platformFee = platformFee;
        ag.customerPayout = customerPayout;

        if (customerPayout > 0) IERC20(base).safeTransfer(ag.terms.customer, customerPayout);
        emit Settled(ag.id, finalValue, profit, traderFee, platformFee, customerPayout, msg.sender);
    }

    // ---------------------------------------------------------------------
    // claim (§4.7)
    // ---------------------------------------------------------------------

    function claim(uint256 id, address token) external nonReentrant returns (uint256 amount) {
        Agreement storage ag = _agreements[id];
        if (ag.status == AgreementStatus.None) revert NotFound();
        if (msg.sender != ag.terms.customer) revert Unauthorized();
        if (ag.status != AgreementStatus.Settled) revert WrongStatus();
        amount = _balances[id][token];
        if (amount == 0) revert InsufficientBalance();
        delete _balances[id][token];
        _removeToken(ag.tokens, token);
        emit Claimed(id, token, amount);

        IERC20(token).safeTransfer(ag.terms.customer, amount);
    }

    // ---------------------------------------------------------------------
    // View'lar (§4.8)
    // ---------------------------------------------------------------------

    function getAgreement(uint256 id) external view returns (Agreement memory) {
        Agreement storage ag = _agreements[id];
        if (ag.status == AgreementStatus.None) revert NotFound();
        return ag;
    }

    function getBalances(uint256 id) external view returns (address[] memory tokens, uint256[] memory amounts) {
        Agreement storage ag = _agreements[id];
        if (ag.status == AgreementStatus.None) revert NotFound();
        tokens = ag.tokens;
        amounts = new uint256[](tokens.length);
        for (uint256 i = 0; i < tokens.length; ++i) {
            amounts[i] = _balances[id][tokens[i]];
        }
    }

    function valueInBase(uint256 id) external view returns (uint256) {
        Agreement storage ag = _agreements[id];
        if (ag.status == AgreementStatus.None) revert NotFound();
        (uint256 total,) = _valuation(ag, ag.terms.baseToken);
        return total;
    }

    function previewSettle(uint256 id)
        external
        view
        returns (
            address[] memory tokens,
            uint256[] memory balances,
            uint256[] memory quotes,
            uint256 estimatedFinalValue,
            uint256 drawdownFloor,
            uint256 keeperFloor
        )
    {
        Agreement storage ag = _agreements[id];
        if (ag.status == AgreementStatus.None) revert NotFound();
        address base = ag.terms.baseToken;
        uint256 n = ag.tokens.length - 1;
        tokens = new address[](n);
        balances = new uint256[](n);
        quotes = new uint256[](n);
        estimatedFinalValue = _balances[id][base];
        for (uint256 i = 0; i < n; ++i) {
            address token = ag.tokens[i + 1];
            uint256 bal = _balances[id][token];
            tokens[i] = token;
            balances[i] = bal;
            quotes[i] = _quote(token, base, bal);
            estimatedFinalValue += quotes[i];
        }
        drawdownFloor = SettleMath.drawdownFloor(ag.terms.principal, ag.terms.maxDrawdownBps);
        keeperFloor = SettleMath.minOutWithSlippage(ag.lastValue, _config.settleSlippageBps);
    }

    function getConfig() external view returns (Config memory) {
        return _config;
    }

    function isTokenAllowed(address token) external view returns (TokenInfo memory) {
        return _tokens[token];
    }

    function nextId() external view returns (uint256) {
        return _nextId;
    }

    function nextReservationId() external view returns (uint256) {
        return _nextReservationId;
    }

    function getReservation(uint256 id) external view returns (Reservation memory) {
        Reservation storage res = _reservations[id];
        if (res.status == ReservationStatus.None) revert ReservationNotFound();
        return res;
    }

    function paused() external view returns (bool) {
        return _config.paused;
    }

    // ---------------------------------------------------------------------
    // İç yardımcılar (§4.0)
    // ---------------------------------------------------------------------

    function _validateTerms(Terms calldata t) private view {
        if (t.customer == address(0) || t.trader == address(0)) revert ZeroAddress();
        if (t.principal == 0) revert InvalidTerms();
        if (t.durationSeconds < MIN_DURATION || t.durationSeconds > MAX_DURATION) revert InvalidTerms();
        if (t.commissionBps > MAX_COMMISSION_BPS) revert InvalidTerms();
        if (t.maxDrawdownBps < MIN_DRAWDOWN_BPS || t.maxDrawdownBps > BPS_DENOM) revert InvalidTerms();
        if (t.customer == t.trader) revert InvalidTerms();
        TokenInfo storage info = _tokens[t.baseToken];
        if (!(info.allowed && info.isBase)) revert TokenNotAllowed();
    }

    function _newAgreement(uint256 id, Terms calldata terms, AgreementStatus status, address proposer)
        private
        returns (Agreement storage ag)
    {
        ag = _agreements[id];
        ag.id = id;
        ag.terms = terms;
        ag.status = status;
        ag.proposer = proposer;
        ag.createdAt = uint64(block.timestamp);
        ag.platformFeeBps = _config.platformFeeBps; // snapshot
        ag.tokens.push(terms.baseToken);
        ag.lastValue = terms.principal;
        // startTime/endTime/settledAt/finalValue/traderFee/platformFee/customerPayout = 0 (varsayılan)
    }

    function _activate(Agreement storage ag) private {
        uint64 nowTs = uint64(block.timestamp);
        ag.startTime = nowTs;
        ag.endTime = nowTs + ag.terms.durationSeconds;
        ag.status = AgreementStatus.Active;
        emit Activated(ag.id, nowTs, ag.endTime);
    }

    function _drawReservation(uint256 resId, Agreement storage ag) private {
        Reservation storage res = _reservations[resId];
        if (res.status == ReservationStatus.None) revert ReservationNotFound();
        if (res.status != ReservationStatus.Open) revert ReservationClosed();
        if (res.customer != ag.terms.customer || res.token != ag.terms.baseToken) revert ReservationMismatch();
        uint256 principal = ag.terms.principal;
        if (res.amount < principal) revert ReservationInsufficient();
        uint256 remaining = res.amount - principal;
        res.amount = remaining;
        if (remaining == 0) res.status = ReservationStatus.Consumed;
        _balances[ag.id][ag.terms.baseToken] = principal;
        emit ReservationDrawn(resId, ag.id, principal, remaining);
    }

    /// @dev Router quote; hata / boş dönüş -> 0 (staticcall).
    function _quote(address token, address base, uint256 amount) private view returns (uint256) {
        if (amount == 0) return 0;
        address[] memory path = new address[](2);
        path[0] = token;
        path[1] = base;
        try IUniswapV2Router02Like(_config.router).getAmountsOut(amount, path) returns (uint256[] memory a) {
            return a.length == 0 ? 0 : a[a.length - 1];
        } catch {
            return 0;
        }
    }

    /// @dev Portföy değeri base cinsinden + `probe` token'ının quote'u (aynı geçişte).
    function _valuation(Agreement storage ag, address probe) private view returns (uint256 total, uint256 probeQuote) {
        uint256 id = ag.id;
        address base = ag.terms.baseToken;
        total = _balances[id][base];
        uint256 len = ag.tokens.length;
        for (uint256 i = 1; i < len; ++i) {
            address token = ag.tokens[i];
            uint256 bal = _balances[id][token];
            if (bal == 0) continue;
            uint256 q = _quote(token, base, bal);
            if (token == probe) probeQuote = q;
            total += q;
        }
    }

    /// @dev Router üzerinden swap; router'ın tam `amountIn` çektiği doğrulanır, izin sıfırlanır.
    function _swapViaRouter(address tokenIn, address tokenOut, uint256 amountIn, uint256 minOut, uint64 deadline)
        private
        returns (uint256 reported)
    {
        IUniswapV2Router02Like router = IUniswapV2Router02Like(_config.router);
        uint256 inBefore = IERC20(tokenIn).balanceOf(address(this));
        IERC20(tokenIn).forceApprove(address(router), amountIn);

        address[] memory path = new address[](2);
        path[0] = tokenIn;
        path[1] = tokenOut;
        try router.swapExactTokensForTokens(amountIn, minOut, path, address(this), deadline) returns (
            uint256[] memory amounts
        ) {
            if (amounts.length == 0) revert RouterError("");
            reported = amounts[amounts.length - 1];
        } catch (bytes memory reason) {
            revert RouterError(reason);
        }

        IERC20(tokenIn).forceApprove(address(router), 0);
        if (inBefore - IERC20(tokenIn).balanceOf(address(this)) != amountIn) {
            revert RouterError(bytes("input-mismatch"));
        }
        if (reported == 0 || reported < minOut) revert SlippageExceeded();
    }

    /// @dev min(reported, gerçekten gelen)
    function _credited(uint256 reported, uint256 before, uint256 after_) private pure returns (uint256) {
        uint256 received = after_ > before ? after_ - before : 0;
        return reported < received ? reported : received;
    }

    /// @dev Revert yutan transfer; yalnız fee bacakları ve in-kind teslimde kullanılır.
    function _tryTransfer(address token, address to, uint256 amount) private returns (bool) {
        if (amount == 0) return true;
        (bool ok, bytes memory data) = token.call(abi.encodeCall(IERC20.transfer, (to, amount)));
        if (!ok) return false;
        if (data.length == 0) return token.code.length > 0;
        return data.length >= 32 && abi.decode(data, (bool));
    }

    /// @dev Sıra koruyucu çıkarma (shift-left + pop); index 0 (base) asla çıkarılmaz. Yoksa no-op.
    function _removeToken(address[] storage arr, address token) private {
        uint256 len = arr.length;
        for (uint256 i = 1; i < len; ++i) {
            if (arr[i] == token) {
                for (uint256 j = i; j + 1 < len; ++j) {
                    arr[j] = arr[j + 1];
                }
                arr.pop();
                return;
            }
        }
    }

    function _contains(address[] storage arr, address token) private view returns (bool) {
        uint256 len = arr.length;
        for (uint256 i = 0; i < len; ++i) {
            if (arr[i] == token) return true;
        }
        return false;
    }

    function _max(uint256 a, uint256 b) private pure returns (uint256) {
        return a > b ? a : b;
    }
}
