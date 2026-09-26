// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

/// @title ITraderVault
/// @notice TraderVault'un tipleri, custom error'ları, event'leri ve dış fonksiyon imzaları (ABI'nin kaynağı).
///         Bkz. docs/monad/01-kontrat-spec.md §2, §4, §5, §6.
interface ITraderVault {
    // ---------------------------------------------------------------------
    // Enum'lar (None = 0 nöbetçisi; Rust sırası +1 kayar)
    // ---------------------------------------------------------------------

    enum AgreementStatus {
        None,
        Proposed,
        Funded,
        Active,
        Settled,
        Cancelled
    }

    enum ReservationStatus {
        None,
        Open,
        Released,
        Consumed
    }

    // ---------------------------------------------------------------------
    // Struct'lar
    // ---------------------------------------------------------------------

    struct Terms {
        address customer;
        address trader;
        address baseToken;
        uint256 principal; // baseToken ham birimi, > 0
        uint64 durationSeconds; // MIN_DURATION..=MAX_DURATION
        uint16 commissionBps; // 0..=5000, trader'ın pozitif kâr payı
        uint16 maxDrawdownBps; // 100..=10000 (10000 = kontrol kapalı)
        bytes32 listingRef; // sha256(off-chain listing/offer id); yalnız indeksleme
    }

    struct Agreement {
        uint256 id;
        Terms terms;
        AgreementStatus status;
        address proposer; // open* -> customer, propose -> trader
        uint64 createdAt;
        uint64 startTime; // 0 until Active
        uint64 endTime; // 0 until Active
        uint64 settledAt;
        uint16 platformFeeBps; // SNAPSHOT: oluşturulma anındaki Config.platformFeeBps
        address[] tokens; // tokens[0] == terms.baseToken; length <= MAX_TOKENS
        uint256 finalValue;
        uint256 traderFee;
        uint256 platformFee;
        uint256 customerPayout;
        uint256 lastValue; // principal -> her trade'de valueAfter -> settle'da finalValue
    }

    struct Reservation {
        uint256 id;
        address customer;
        address token;
        uint256 amount; // hâlâ tutulan
        uint256 original; // ilk kilitlenen
        ReservationStatus status;
        uint64 createdAt;
        bytes32 listingRef;
    }

    struct Config {
        address router;
        address feeRecipient;
        uint16 platformFeeBps; // yeni anlaşmalar için
        uint16 settleSlippageBps; // admin/keeper settle toleransı
        bool paused;
        address pendingRouter; // 0 = bekleyen değişiklik yok
        uint64 routerActivationTime;
        uint64 routerDelay;
    }

    struct TokenInfo {
        bool allowed;
        bool isBase;
    }

    // ---------------------------------------------------------------------
    // Custom error'lar (§5; kodlar backend selector tablosunda, yeniden numaralanmaz)
    // ---------------------------------------------------------------------

    error NotInitialized(); // 1 (rezerve)
    error Unauthorized(); // 2
    error Paused(); // 3
    error InvalidTerms(); // 4
    error TokenNotAllowed(); // 5
    error NotFound(); // 6
    error WrongStatus(); // 7
    error Expired(); // 8
    error NotExpired(); // 9
    error InsufficientBalance(); // 10
    error TooManyTokens(); // 11
    error DrawdownBreached(); // 12
    error SlippageExceeded(); // 13
    error Overflow(); // 14 (rezerve; checked math Panic(0x11))
    error InvalidAmount(); // 15
    error RouterError(bytes reason); // 16
    error NotParty(); // 17
    error ReservationNotFound(); // 18
    error ReservationClosed(); // 19
    error ReservationInsufficient(); // 20
    error ReservationMismatch(); // 21
    error ZeroAmount(); // 22
    error ZeroAddress(); // 23
    error InvalidRouter(); // 24
    error RouterChangeNotReady(uint64 activationTime); // 25
    error NoPendingRouterChange(); // 26
    error InvalidToken(); // 27

    // ---------------------------------------------------------------------
    // Event'ler (§6)
    // ---------------------------------------------------------------------

    event Proposed(
        uint256 indexed id, address indexed trader, address indexed customer, uint256 principal, address baseToken
    );
    event Opened(
        uint256 indexed id, address indexed trader, address indexed customer, uint256 principal, address baseToken
    );
    event Activated(uint256 indexed id, uint64 startTime, uint64 endTime);
    event Cancelled(uint256 indexed id, uint256 refunded);
    event Reserved(uint256 indexed id, address indexed customer, address token, uint256 amount, bytes32 listingRef);
    event Released(uint256 indexed id, address indexed customer, address token, uint256 amount, uint256 remaining);
    event ReservationDrawn(uint256 indexed id, uint256 indexed agreementId, uint256 amount, uint256 remaining);
    event Traded(
        uint256 indexed id,
        address indexed trader,
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 amountOut,
        uint256 valueAfter
    );
    event Settled(
        uint256 indexed id,
        uint256 finalValue,
        uint256 profit,
        uint256 traderFee,
        uint256 platformFee,
        uint256 customerPayout,
        address by
    );
    event Unliquidated(uint256 indexed id, address token, uint256 amount, bool delivered);
    event Claimed(uint256 indexed id, address token, uint256 amount);
    event ConfigChanged(
        bytes32 indexed key,
        address router,
        uint16 platformFeeBps,
        address feeRecipient,
        bool paused,
        uint16 settleSlippageBps
    );
    event TokenSet(address indexed token, bool allowed, bool isBase);
    event RouterChangeProposed(address indexed newRouter, uint64 activationTime);
    event RouterChangeCancelled(address indexed cancelledRouter);

    // ---------------------------------------------------------------------
    // Initialize (§4.1)
    // ---------------------------------------------------------------------

    function initialize(
        address owner_,
        address router_,
        address feeRecipient_,
        uint16 platformFeeBps_,
        uint16 settleSlippageBps_,
        uint64 routerDelay_
    ) external;

    // ---------------------------------------------------------------------
    // Admin (§4.2, onlyOwner)
    // ---------------------------------------------------------------------

    function setToken(address token, bool allowed, bool isBase) external;
    function proposeRouterChange(address newRouter) external;
    function applyRouterChange() external;
    function cancelRouterChange() external;
    function setFees(uint16 platformFeeBps, address feeRecipient) external;
    function setPaused(bool paused_) external;
    function setSettleSlippage(uint16 bps) external;

    // ---------------------------------------------------------------------
    // Rezervasyonlar (§4.3)
    // ---------------------------------------------------------------------

    function reserve(address token, uint256 amount, bytes32 listingRef) external returns (uint256 id);
    function release(uint256 id, uint256 amount) external returns (uint256);
    function releaseAll(uint256 id) external returns (uint256);

    // ---------------------------------------------------------------------
    // Yaşam döngüsü (§4.4)
    // ---------------------------------------------------------------------

    function propose(Terms calldata terms) external returns (uint256 id);
    function open(Terms calldata terms) external returns (uint256 id);
    function openReserved(Terms calldata terms, uint256 reservationId) external returns (uint256 id);
    function fund(uint256 id) external;
    function fundReserved(uint256 id, uint256 reservationId) external;
    function accept(uint256 id) external;
    function cancel(uint256 id) external;

    // ---------------------------------------------------------------------
    // Trade / settle / claim (§4.5–§4.7)
    // ---------------------------------------------------------------------

    function trade(uint256 id, address tokenIn, address tokenOut, uint256 amountIn, uint256 minOut, uint64 deadline)
        external
        returns (uint256 amountOut);
    function settle(uint256 id, uint256[] calldata minOuts) external;
    function claim(uint256 id, address token) external returns (uint256 amount);

    // ---------------------------------------------------------------------
    // View'lar (§4.8)
    // ---------------------------------------------------------------------

    function getAgreement(uint256 id) external view returns (Agreement memory);
    function getBalances(uint256 id) external view returns (address[] memory tokens, uint256[] memory amounts);
    function valueInBase(uint256 id) external view returns (uint256);
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
        );
    function getConfig() external view returns (Config memory);
    function isTokenAllowed(address token) external view returns (TokenInfo memory);
    function nextId() external view returns (uint256);
    function nextReservationId() external view returns (uint256);
    function getReservation(uint256 id) external view returns (Reservation memory);
    function paused() external view returns (bool);
}
