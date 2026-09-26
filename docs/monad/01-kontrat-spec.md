# 01 — `TraderVault` Solidity kontrat spesifikasyonu (Monad Testnet)

**Durum:** Sprint 2 (SC-01…SC-12) için bağlayıcı uygulama spesifikasyonu.
**Kaynak:** `backend/contracts/vault/src/{lib,types,storage,events,errors,math,router,test}.rs` (v1-stellar),
`backend/contracts/mock_router/src/{lib,test}.rs`, `backend/docs/CONTRACT.md`, `docs/monad/00-inceleme.md` §A,
`SPRINT-2-MONAD.md` §2 (K1–K13). Rust davranışı satır satır okunarak aktarıldı; sapmalar Ek A'da listelenir.
**Hedef ağ:** Monad Testnet, chainId `10143`, RPC `https://testnet-rpc.monad.xyz`, MON 18 ondalık (yalnız gas; K6).
**Dil kuralı:** doküman Türkçe; kod tanımlayıcıları, JSON alanları ve event/error adları İngilizce.

Bu dokümanı uygulayan ajan başka kaynağa bakmadan kontratı, mock'ları, testleri ve deploy betiğini yazabilmelidir.
"ZORUNLU" işaretli her madde uygulanır; "opsiyonel" olanlar sprint sonuna bırakılabilir.

---

## 0. Kapsam, doğrulanmış ağ bilgisi ve karar notları

### 0.1 Doğrulanmış Monad bilgileri (26 Eyl 2026, docs.monad.xyz)
| Konu | Değer | Spec'e etkisi |
|---|---|---|
| Testnet chainId / RPC | `10143`, `https://testnet-rpc.monad.xyz` (50 rps; `eth_call`/`estimateGas` 25 rps) | `foundry.toml` rpc alias; backend indexer pencereleri (BE-09) |
| Explorer | MonadVision `https://testnet.monadvision.com`, Monadscan `https://testnet.monadscan.com` | `/config.explorer_url` MonadVision |
| Faucet | `https://faucet.monad.xyz` | Cüzdan ekranı MON linki (FE-43) |
| Verify | `forge verify-contract <addr> <Name> --chain 10143 --verifier sourcify --verifier-url https://sourcify-api-monad.blockvision.org/` | §9.3 |
| EVM | EIP-7702 destekleniyor (Prague seviyesi) → Cancun opcode'ları (PUSH0, MCOPY, TSTORE) güvenli | `evm_version = "cancun"` |
| Gas modeli | Gönderenden `gas_limit × gas_price` düşülür (kullanılan değil) | Backend `gas` alanı `estimateGas × 1.2`, şişirilmez (BE-03) |
| Kontrat boyutu | 128 KB limit | Yine de 24 KB altında kalınır (`forge build --sizes`) |
| Kanonik (testnet) | WMON `0xFb8bf4c1CC7a94c73D209a149eA2AbEa852BC541`, Multicall3 `0xcA11bde05977b3631167028862bE2a173976CA11`, Permit2 `0x000000000022d473030f116ddee9f6b43ac78ba3` | Vault'a WMON girmez (K6); bilgi amaçlı |

### 0.2 Bağlayıcı kararların bu spec'teki karşılığı
| Karar | Uygulama |
|---|---|
| K1 | Solidity `0.8.28`, Foundry, OZ v5: `UUPSUpgradeable`, `Ownable2StepUpgradeable`, `ReentrancyGuardUpgradeable`, `SafeERC20`. Pausable için bkz. 0.3/1 |
| K4 | Token çekişi `safeTransferFrom(msg.sender → vault)`; kullanıcı önce tam tutar `approve` eder. Permit yok |
| K5 | `MockRouter.sol` (UniswapV2 imzaları) + `TestToken.sol` (tUSDC 6, tWETH 18, tWBTC 8). Gerçek Uniswap v2 çıkarsa vault değişmeden çalışır (`IUniswapV2Router02Like`) |
| K6 | Taban varlık tUSDC; vault yalnız ERC-20 tutar, `payable` fonksiyon yok, `receive()` yok |
| K7 | Faucet: backend `MINTER_KEY` adresi `TestToken.MINTER_ROLE` sahibi (OPS-11). Kontratta herkese açık `mint` **yok** |
| SC-08 | `platformFeeBps` anlaşma açılışında `Agreement`'a sabitlenir; admin settle yoluna `lastValue` tabanı; router değişimi iki adımlı ve gecikmeli |

### 0.3 Karar değişikliği önerileri (spec mevcut karara göre yazıldı; ekip onaylarsa SPRINT-2'ye işlenir)
1. **K1 – OZ `Pausable` kullanılmıyor.** Gerekçe: (a) Rust'ta pause yalnızca `propose/open/openReserved/fund/fundReserved/accept/trade/reserve`'i durdurur; `cancel/settle/claim/release` çalışır. (b) Bu dokümanın zorunlu kıldığı hata adı `error Paused()` (kod 3), OZ `PausableUpgradeable`'ın `event Paused(address)` bildirimiyle **aynı sözleşmede derlenemez** (identifier çakışması). Bu yüzden `paused` bayrağı `Config` içinde tutulur, kendi `whenNotPaused` modifier'ımız `Paused()` ile revert eder ve Rust'taki gibi `ConfigChanged("paused")` yayınlanır. Ekip OZ Pausable'da ısrar ederse: hata adı `EnforcedPause()` olur ve backend selector tablosunda kod 3'e eşlenir; başka hiçbir şey değişmez.
2. **Dizin adı.** SPRINT-2 §3 tablosu `contracts-evm/` diyor; bu spec ve görev tanımı kök `contracts/` kullanır (mevcut `contracts/README.md` Stellar dönemi yer tutucusudur, silinir). SPRINT-2 tablosundaki `contracts-evm/` ifadesi `contracts/` olarak düzeltilmeli.

---

## 1. Dosya düzeni ve araç zinciri (SC-01, SC-12)

```
contracts/                              # kök Foundry projesi (contracts/README.md yer tutucusu silinir)
├── foundry.toml
├── remappings.txt
├── .gitignore                          # out/ cache/ broadcast/**/dry-run/ lib/
├── lib/                                # forge install (git submodule)
│   ├── forge-std
│   ├── openzeppelin-contracts            # v5.x (upgradeable ile AYNI tag)
│   └── openzeppelin-contracts-upgradeable
├── src/
│   ├── TraderVault.sol                 # UUPS implementasyonu
│   ├── interfaces/
│   │   ├── ITraderVault.sol            # enum/struct/error/event + dış fonksiyon imzaları (ABI'nin kaynağı)
│   │   └── IUniswapV2Router02Like.sol  # getAmountsOut + swapExactTokensForTokens
│   ├── libraries/
│   │   └── SettleMath.sol
│   └── mocks/
│       ├── MockRouter.sol              # testnet'e DEPLOY edilir (K5)
│       └── TestToken.sol               # testnet'e DEPLOY edilir (tUSDC/tWETH/tWBTC)
├── test/
│   ├── Base.t.sol                      # ortak fixture (Rust setup() karşılığı)
│   ├── TraderVault.Admin.t.sol
│   ├── TraderVault.Lifecycle.t.sol
│   ├── TraderVault.Trade.t.sol
│   ├── TraderVault.Settle.t.sol
│   ├── TraderVault.Reservation.t.sol
│   ├── TraderVault.Views.t.sol
│   ├── TraderVault.Security.t.sol      # reentrancy, decimals, fee snapshot, router delay
│   ├── TraderVault.Upgrade.t.sol
│   ├── TraderVault.Invariant.t.sol     # + handler
│   ├── SettleMath.t.sol                # fuzz
│   ├── MockRouter.t.sol
│   └── mocks/                          # yalnız test: BlocklistToken, ReentrantToken, MaliciousRouter, TraderVaultV2
├── script/
│   ├── Deploy.s.sol
│   └── Ops.s.sol                       # opsiyonel: setPrice / mint / setToken yardımcıları
├── scripts/
│   ├── export-abi.sh                   # out/ → backend + app
│   └── deployments-from-broadcast.sh   # broadcast/…/run-latest.json → deployments/*.json txHashes/blockNumber
└── deployments/
    ├── monad-testnet.json              # §9.4 şeması
    └── storage-layout.json             # forge inspect çıktısı (upgrade güvenliği, CI diff)
```

### 1.1 `foundry.toml` (ZORUNLU)
```toml
[profile.default]
src = "src"
out = "out"
libs = ["lib"]
test = "test"
script = "script"
solc_version = "0.8.28"
evm_version = "cancun"          # Monad EIP-7702 destekliyor (Prague seviyesi); invalid opcode görülürse "shanghai"
optimizer = true
optimizer_runs = 200
via_ir = false                  # "stack too deep" çıkarsa true yapılır; settle() yerel değişkenleri buna yakın
bytecode_hash = "none"          # deterministik bytecode, kaynak doğrulama kolaylığı
cbor_metadata = true
use_literal_content = true
fs_permissions = [
  { access = "read-write", path = "./deployments" },
  { access = "read", path = "./broadcast" }
]
ffi = false

[profile.default.fuzz]
runs = 2000
max_test_rejects = 100000

[profile.default.invariant]
runs = 256
depth = 50
fail_on_revert = false

[profile.ci]
fuzz = { runs = 10000 }
invariant = { runs = 512, depth = 100 }

[rpc_endpoints]
monad_testnet = "${MONAD_TESTNET_RPC_URL}"     # .env: https://testnet-rpc.monad.xyz
anvil = "http://127.0.0.1:8545"

[fmt]
line_length = 120
tab_width = 4
```
`.env.example`: `MONAD_TESTNET_RPC_URL`, `DEPLOYER_PRIVATE_KEY`, `FEE_RECIPIENT`, `MINTER_ADDRESS`, `PLATFORM_FEE_BPS=100`, `SETTLE_SLIPPAGE_BPS=100`, `ROUTER_DELAY=86400`.

### 1.2 `remappings.txt` ve bağımlılıklar
```
forge-std/=lib/forge-std/src/
@openzeppelin/contracts/=lib/openzeppelin-contracts/contracts/
@openzeppelin/contracts-upgradeable/=lib/openzeppelin-contracts-upgradeable/contracts/
```
```sh
forge init --no-git contracts && cd contracts
forge install foundry-rs/forge-std
forge install OpenZeppelin/openzeppelin-contracts@v5.4.0
forge install OpenZeppelin/openzeppelin-contracts-upgradeable@v5.4.0   # contracts ile AYNI tag zorunlu
```
Kullanılan OZ modülleri: `UUPSUpgradeable`, `Ownable2StepUpgradeable` (+`OwnableUpgradeable`), `ReentrancyGuardUpgradeable`
(**transient sürüm değil**), `Initializable`, `SafeERC20`, `IERC20`, `ERC1967Proxy`, `ERC20`, `AccessControl`, `Ownable`.

### 1.3 CI (`.github/workflows/contracts.yml`)
`forge fmt --check` → `forge build --sizes` → `forge test -vvv` → `FOUNDRY_PROFILE=ci forge test` (nightly) →
`forge coverage --report summary` (vault ≥ %90 satır, DoD-1) → `forge inspect TraderVault storage-layout --json | diff deployments/storage-layout.json -`
→ `slither . --filter-paths "lib|test|script"` (yüksek bulgu → kırmızı).

### 1.4 ABI dışa aktarma (`scripts/export-abi.sh`, SC-12)
```sh
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
forge build
BE=../backend/app/services/chain/abi; FE=../app/src/lib/chain/abi
mkdir -p "$BE" "$FE"
for C in TraderVault MockRouter TestToken; do
  jq '.abi' "out/$C.sol/$C.json" > "$BE/$C.json"
done
{
  echo "// AUTO-GENERATED by contracts/scripts/export-abi.sh — do not edit"
  echo "export const traderVaultAbi = $(jq -c '.abi' out/TraderVault.sol/TraderVault.json) as const;"
} > "$FE/traderVault.ts"
{
  echo "// AUTO-GENERATED by contracts/scripts/export-abi.sh — do not edit"
  echo "export const mockRouterAbi = $(jq -c '.abi' out/MockRouter.sol/MockRouter.json) as const;"
} > "$FE/mockRouter.ts"
mkdir -p ../backend/deployments && cp deployments/monad-testnet.json ../backend/deployments/   # seed_assets.py okur (BE-21)
```
Frontend ERC-20 için viem'in `erc20Abi`'si kullanılır; ayrı dosya üretilmez. Frontend adresleri `/config`'ten alır (BE-13); deploy JSON'u frontend'e kopyalanmaz.

---

## 2. Tipler (`ITraderVault.sol`, SC-02)

Tüm tutarlar `uint256` (ham token birimi, token'ın kendi `decimals`'ı). Rust'taki `i128 <= 0` kontrolleri `== 0` kontrolüne düşer; negatif dal yoktur.
Kimlikler `uint256`, zaman damgaları `uint64` (`block.timestamp` cast), bps alanları `uint16`.

### 2.1 Enum'lar ve backend eşlemesi (ZORUNLU – BE-04/enums.py etkisi)
```solidity
enum AgreementStatus  { None, Proposed, Funded, Active, Settled, Cancelled }   // 0,1,2,3,4,5
enum ReservationStatus { None, Open, Released, Consumed }                      // 0,1,2,3
```
Rust sırası korunur (`Proposed < Funded < Active < Settled < Cancelled`, `Open < Released < Consumed`), ancak `None = 0`
"kayıt yok" nöbetçisi eklendiği için **sayısal kodlar +1 kayar**. `status == None` → agreement yok (`NotFound`).

| Rust `Status` | değer | Solidity | değer | backend `AgreementStatus` |
|---|---|---|---|---|
| — | — | `None` | 0 | (kayıt yok → 404) |
| `Proposed` | 0 | `Proposed` | 1 | `proposed` |
| `Funded` | 1 | `Funded` | 2 | `funded` |
| `Active` | 2 | `Active` | 3 | `active` |
| `Settled` | 3 | `Settled` | 4 | `settled` |
| `Cancelled` | 4 | `Cancelled` | 5 | `cancelled` |

`backend/app/models/enums.py:96-102` `_ONCHAIN_STATUS` → `{1: proposed, 2: funded, 3: active, 4: settled, 5: cancelled}` olur; `0` için `KeyError` yerine "not found" muamelesi.
Aynı şekilde `ReservationStatus`: `Open=1, Released=2, Consumed=3`.

### 2.2 Struct'lar
```solidity
struct Terms {
    address customer;
    address trader;
    address baseToken;
    uint256 principal;          // baseToken ham birimi, > 0
    uint64  durationSeconds;    // MIN_DURATION..=MAX_DURATION
    uint16  commissionBps;      // 0..=5000, trader'ın pozitif kâr payı
    uint16  maxDrawdownBps;     // 100..=10000 (10000 = kontrol kapalı)
    bytes32 listingRef;         // sha256(off-chain listing/offer id); yalnız indeksleme
}

struct Agreement {
    uint256 id;
    Terms   terms;
    AgreementStatus status;
    address proposer;           // open* → customer, propose → trader
    uint64  createdAt;
    uint64  startTime;          // 0 until Active
    uint64  endTime;            // 0 until Active
    uint64  settledAt;
    uint16  platformFeeBps;     // SNAPSHOT: oluşturulma anındaki Config.platformFeeBps (SC-08 / risk 3)
    address[] tokens;           // tutulan tokenlar; tokens[0] == terms.baseToken her zaman; length <= MAX_TOKENS
                                // settle sonrası: [base] + teslim edilemeyen in-kind bacaklar
    uint256 finalValue;
    uint256 traderFee;
    uint256 platformFee;
    uint256 customerPayout;
    uint256 lastValue;          // principal (oluşturma) → her trade'de valueAfter → settle'da finalValue
}

struct Reservation {
    uint256 id;
    address customer;
    address token;              // reserve anında allowed && isBase olmalı
    uint256 amount;             // hâlâ tutulan (anlaşmalar çektikçe azalır)
    uint256 original;           // ilk kilitlenen
    ReservationStatus status;
    uint64  createdAt;
    bytes32 listingRef;
}

struct Config {
    address router;             // aktif router (quote + swap)
    address feeRecipient;
    uint16  platformFeeBps;     // yeni anlaşmalar için; mevcutlar kendi snapshot'ını kullanır
    uint16  settleSlippageBps;  // admin/keeper settle toleransı
    bool    paused;
    address pendingRouter;      // 0 = bekleyen değişiklik yok
    uint64  routerActivationTime;
    uint64  routerDelay;        // initialize'da verilir; ana ağ 86400, testnet daha kısa olabilir
}

struct TokenInfo { bool allowed; bool isBase; }
```
Rust `Config.admin` alanı kaldırıldı: admin = `owner()` (Ownable2Step). Backend admin adresini `owner()` ile okur.

---

## 3. Storage ve sabitler (SC-02)

### 3.1 Sabitler (`TraderVault.sol`, `public constant`)
| İsim | Değer | Rust karşılığı |
|---|---|---|
| `MAX_TOKENS` | `6` (`uint8`) | `MAX_TOKENS` |
| `MIN_DURATION` | `86_400` s (1 gün) | `MIN_DURATION` |
| `MAX_DURATION` | `94_608_000` s (3 × 365 gün) | `MAX_DURATION` |
| `MAX_COMMISSION_BPS` | `5_000` | `MAX_COMMISSION_BPS` |
| `MAX_PLATFORM_FEE_BPS` | `1_000` | `MAX_PLATFORM_FEE_BPS` |
| `MIN_DRAWDOWN_BPS` | `100` | `MIN_DRAWDOWN_BPS` |
| `MAX_SETTLE_SLIPPAGE_BPS` | `5_000` | `MAX_SETTLE_SLIPPAGE_BPS` |
| `KEEPER_GRACE` | `604_800` s (7 gün) | `SETTLE_GRACE_SECS` |
| `BPS_DENOM` | `10_000` | `BPS_DENOM` |
| `MAX_ROUTER_DELAY` | `2_592_000` s (30 gün) | yeni; `initialize(routerDelay_)` üst sınırı |
| `DEFAULT_ROUTER_DELAY` | `86_400` s (24 saat) | yeni; deploy betiği varsayılanı |

TTL sabitleri (`DAY_IN_LEDGERS`, `TTL_*`) ve `extend_instance/bump_persistent` çağrıları Solidity'de **yoktur**.

### 3.2 State (ZORUNLU sıra; append-only, `__gap`)
```solidity
Config  private _config;
uint256 private _nextId;                                         // 1'den başlar
uint256 private _nextReservationId;                              // 1'den başlar, ayrı dizi
mapping(uint256 => Agreement)                    private _agreements;
mapping(uint256 => mapping(address => uint256))  private _balances;     // Balance(id, token); yoksa 0
mapping(uint256 => Reservation)                  private _reservations;
mapping(address => TokenInfo)                    private _tokens;       // AllowedToken(token); yoksa {false,false}
uint256[50] private __gap;
```
Kurallar: Rust'taki `remove_balance` = `delete _balances[id][token]`. Bakiye girdisi 0 yazılmaz, silinir. `tokens` dizisinden çıkarma
**sıra koruyucu** (`shift-left + pop`), `tokens[0]` asla çıkarılmaz. Upgrade'de yeni alanlar yalnız `__gap`'ten alınır; `forge inspect` çıktısı commit'lenir.

### 3.3 Miras ve modifier'lar
```solidity
contract TraderVault is ITraderVault, Initializable, UUPSUpgradeable, Ownable2StepUpgradeable, ReentrancyGuardUpgradeable {
    using SafeERC20 for IERC20;
    constructor() { _disableInitializers(); }
    modifier whenNotPaused() { if (_config.paused) revert Paused(); _; }
    function _authorizeUpgrade(address) internal override onlyOwner {}
}
```

---

## 4. Fonksiyonlar (SC-03…SC-08)

Genel kurallar:
- Rust `x.require_auth()` → `msg.sender == x` kontrolü; tutmazsa `Unauthorized()`. Rust'taki serbest `caller/customer/trader` parametreleri **kaldırılır**.
- Sıralı ön koşullar tablodaki sırayla kontrol edilir (testler hata önceliğine bağlıdır).
- Dış çağrı yapan her mutasyon `nonReentrant`. Sıra **CEI**: kontroller → state yazımı ve event → token transferi/swap. Swap sonucuna bağlı alanlar (credited, finalValue) zorunlu olarak dış çağrıdan sonra yazılır; `nonReentrant` bunu korur.
- Zaman: `uint64 nowTs = uint64(block.timestamp)`.

### 4.0 İç yardımcılar (ZORUNLU davranış)

**`_validateTerms(Terms calldata t)`** (Rust `validate_terms` :60) — sırayla:
1. `t.customer == address(0) || t.trader == address(0)` → `ZeroAddress()` (yeni; Stellar adresi boş olamazdı)
2. `t.principal == 0` → `InvalidTerms()`
3. `t.durationSeconds < MIN_DURATION || > MAX_DURATION` → `InvalidTerms()`
4. `t.commissionBps > MAX_COMMISSION_BPS` → `InvalidTerms()`
5. `t.maxDrawdownBps < MIN_DRAWDOWN_BPS || > BPS_DENOM` → `InvalidTerms()`
6. `t.customer == t.trader` → `InvalidTerms()`
7. `!( _tokens[t.baseToken].allowed && isBase )` → `TokenNotAllowed()`

**`_newAgreement(id, terms, status, proposer)`** (Rust :83): `createdAt = nowTs`, `start/end/settledAt = 0`, `tokens = [baseToken]`,
`finalValue/traderFee/platformFee/customerPayout = 0`, `lastValue = principal`, **`platformFeeBps = _config.platformFeeBps`** (snapshot).

**`_activate(ag)`** (Rust :153): `startTime = nowTs; endTime = nowTs + durationSeconds; status = Active; emit Activated(id, startTime, endTime)`.

**`_drawReservation(resId, ag)`** (Rust :121) — sırayla: `res.status == None` → `ReservationNotFound()`; `!= Open` → `ReservationClosed()`;
`res.customer != ag.terms.customer || res.token != ag.terms.baseToken` → `ReservationMismatch()`; `res.amount < principal` → `ReservationInsufficient()`;
`remaining = res.amount - principal; res.amount = remaining; if remaining == 0 → status = Consumed`; `_balances[ag.id][base] = principal`;
`emit ReservationDrawn(resId, ag.id, principal, remaining)`. Token transferi yok.

**`_quote(token, base, amount) → uint256`** (Rust :173): `amount == 0 → 0`;
`try router.getAmountsOut(amount, [token, base]) returns (uint256[] memory a) { return a.length == 0 ? 0 : a[a.length-1]; } catch { return 0; }`.
Not: `try/catch` dönüş verisi decode hatasını yakalamaz; router admin allow-list'indedir, kabul edilir.

**`_valuation(ag, probe) → (total, probeQuote)`** (Rust :196): `total = _balances[id][base]`; `tokens[1..]` için `bal == 0` atla; `q = _quote(token, base, bal)`; `token == probe → probeQuote = q`; `total += q`.

**`_swapViaRouter(tokenIn, tokenOut, amountIn, minOut, deadline) → reported`** (Rust :228 + K5):
```
inBefore = IERC20(tokenIn).balanceOf(this)
IERC20(tokenIn).forceApprove(router, amountIn)
try router.swapExactTokensForTokens(amountIn, minOut, [tokenIn, tokenOut], address(this), deadline)
    returns (uint256[] memory amounts) { if (amounts.length == 0) revert RouterError(""); reported = amounts[amounts.length-1]; }
    catch (bytes memory reason) { revert RouterError(reason); }
IERC20(tokenIn).forceApprove(router, 0)                                   // artık izin bırakılmaz
if (inBefore - IERC20(tokenIn).balanceOf(this) != amountIn) revert RouterError("input-mismatch")   // yeni: router tam amountIn çekmeli
if (reported == 0 || reported < minOut) revert SlippageExceeded()
```
Uniswap V2 Router02 `path[0]`'ı `msg.sender`'dan `transferFrom` ile çeker; bu yüzden `pairFor` ve ön-yetki (Rust `authorize_as_current_contract`) gerekmez.

**`_credited(reported, before, after) → uint256`** (Rust :272): `received = after > before ? after - before : 0; return min(reported, received)`.

**`_tryTransfer(token, to, amount) → bool`** (Rust `pay` :281): `amount == 0 → true`; düşük seviyeli
`token.call(abi.encodeCall(IERC20.transfer, (to, amount)))`; başarı = `ok && (data.length == 0 ? token.code.length > 0 : (data.length >= 32 && abi.decode(data,(bool))))`.
Revert yutulur, `false` döner. Yalnız fee bacakları ve in-kind teslimde kullanılır; `customerPayout`, `claim`, `release`, `cancel` iadesi **`safeTransfer`** (revert eder).

**`_removeToken(address[] storage arr, address token)`**: `i` bulunur (`i > 0` şart), `arr[i..] ` sola kaydırılır, `pop()`. Sıra korunur.

### 4.1 `initialize` ve UUPS (SC-03)
```solidity
function initialize(address owner_, address router_, address feeRecipient_, uint16 platformFeeBps_, uint16 settleSlippageBps_, uint64 routerDelay_) external initializer
```
| Adım | Kural / hata |
|---|---|
| 1 | `owner_ == 0 || feeRecipient_ == 0` → `ZeroAddress()` |
| 2 | `router_.code.length == 0` → `InvalidRouter()` |
| 3 | `platformFeeBps_ > 1000 || settleSlippageBps_ > 5000` → `InvalidTerms()` (Rust `__constructor` :306) |
| 4 | `routerDelay_ > MAX_ROUTER_DELAY` → `InvalidTerms()` |
| 5 | `__Ownable_init(owner_); __Ownable2Step_init(); __ReentrancyGuard_init(); __UUPSUpgradeable_init();` |
| 6 | `_config = Config(router_, feeRecipient_, platformFeeBps_, settleSlippageBps_, false, address(0), 0, routerDelay_)`; `_nextId = 1; _nextReservationId = 1` |
| event | OZ `Initialized(uint64)`, `OwnershipTransferred(0 → owner_)`. Ek event yok (Rust constructor da yayınlamaz) |

Deploy: `new ERC1967Proxy(address(impl), abi.encodeCall(TraderVault.initialize, (...)))` — tek tx'te initialize (front-run yok).
Upgrade: OZ `upgradeToAndCall(newImpl, data)`; `_authorizeUpgrade` `onlyOwner`; `Upgraded(address indexed implementation)` OZ tarafından yayınlanır. Yeni sürümler `reinitializer(2)` kullanır.
Sahiplik devri: `transferOwnership(new)` → `acceptOwnership()` (Ownable2Step). Ana ağ öncesi owner = Safe/timelock (§11).

### 4.2 Admin (tümü `onlyOwner`)
| Fonksiyon | Ön koşullar (sıralı) | State | Event |
|---|---|---|---|
| `setToken(address token, bool allowed, bool isBase)` | `token == 0` → `ZeroAddress()`; `allowed && token.code.length == 0` → `InvalidToken()` | `_tokens[token] = {allowed, isBase: allowed && isBase}` (Rust :334: de-list `isBase`'i de siler) | `TokenSet(token, allowed, allowed && isBase)` |
| `proposeRouterChange(address newRouter)` | `newRouter == 0` → `ZeroAddress()`; `code.length == 0` → `InvalidRouter()` | `pendingRouter = newRouter; routerActivationTime = nowTs + routerDelay` (varsa eskisini ezer) | `RouterChangeProposed(newRouter, activationTime)` |
| `applyRouterChange()` | `pendingRouter == 0` → `NoPendingRouterChange()`; `nowTs < routerActivationTime` → `RouterChangeNotReady(activationTime)` | `router = pendingRouter; pendingRouter = 0; routerActivationTime = 0` | `ConfigChanged("router", …)` |
| `cancelRouterChange()` | `pendingRouter == 0` → `NoPendingRouterChange()` | pending alanları sıfırlanır | `RouterChangeCancelled(cancelledRouter)` |
| `setFees(uint16 platformFeeBps, address feeRecipient)` | `bps > 1000` → `InvalidTerms()`; `feeRecipient == 0` → `ZeroAddress()` | `_config.platformFeeBps/feeRecipient` (bps yalnız **yeni** anlaşmaları etkiler; recipient settle anında okunur) | `ConfigChanged("fees", …)` |
| `setPaused(bool paused)` | — | `_config.paused` | `ConfigChanged("paused", …)` |
| `setSettleSlippage(uint16 bps)` | `bps > 5000` → `InvalidTerms()` | `_config.settleSlippageBps` | `ConfigChanged("slippage", …)` |

Rust `set_router` (anında) kaldırıldı; `initialize` router'ı gecikmesiz kurar, sonrası iki adımlıdır. `routerDelay` initialize sonrası değişmez (değiştirmek upgrade gerektirir).
`ConfigChanged.key` değerleri `bytes32("router")`, `bytes32("fees")`, `bytes32("paused")`, `bytes32("slippage")` (sağa sıfır dolgulu ASCII).

### 4.3 Rezervasyonlar (SC-04)

**`reserve(address token, uint256 amount, bytes32 listingRef) external nonReentrant returns (uint256 id)`** (Rust :408)
| # | Kural |
|---|---|
| 1 | `paused` → `Paused()` |
| 2 | `amount == 0` → `ZeroAmount()` (Rust: `InvalidAmount`; Ek A-3) |
| 3 | `!(allowed && isBase)` → `TokenNotAllowed()` |
| 4 | `id = _nextReservationId++`; `_reservations[id] = {id, msg.sender, token, amount, amount, Open, nowTs, listingRef}` |
| 5 | `emit Reserved(id, msg.sender, token, amount, listingRef)` |
| 6 | `IERC20(token).safeTransferFrom(msg.sender, address(this), amount)` (K4: kullanıcı önce `approve`) |

**`release(uint256 id, uint256 amount) external nonReentrant returns (uint256)`** (Rust :455, **API değişti**)
| # | Kural |
|---|---|
| 1 | `res.status == None` → `ReservationNotFound()` |
| 2 | `msg.sender != res.customer` → `Unauthorized()` |
| 3 | `res.status != Open` → `ReservationClosed()` |
| 4 | `amount == 0` → **`ZeroAmount()`** (Rust'ta 0 = "tamamını iade"; kaldırıldı, risk 5) |
| 5 | `amount > res.amount` → `ReservationInsufficient()` |
| 6 | `remaining = res.amount - amount; res.amount = remaining; if remaining == 0 → status = Released` |
| 7 | `emit Released(id, res.customer, res.token, amount, remaining)` |
| 8 | `IERC20(res.token).safeTransfer(res.customer, amount)`; return `amount` |
Pause'da çalışır.

**`releaseAll(uint256 id) external nonReentrant returns (uint256)`**: adım 1–3 aynı; `amount = res.amount` (Open iken > 0'dır; yine de 0 ise `ZeroAmount()`); devamı `release` ile aynı (ortak `_release(id, amount)`).

### 4.4 Yaşam döngüsü (SC-05)

| Fonksiyon | Sıralı ön koşullar | State ve dış çağrı | Event |
|---|---|---|---|
| `propose(Terms calldata terms) returns (uint256 id)` | `paused` → `Paused()`; `msg.sender != terms.trader` → `Unauthorized()`; `_validateTerms` | `id = _nextId++`; `_newAgreement(id, terms, Proposed, msg.sender)`. Transfer yok. `nonReentrant` gerekmez | `Proposed(id, trader, customer, principal, baseToken)` |
| `open(Terms calldata terms) nonReentrant returns (uint256 id)` | `paused`; `msg.sender != terms.customer` → `Unauthorized()`; `_validateTerms` | `id = _nextId++`; `_newAgreement(…, Funded, msg.sender)`; `_balances[id][base] = principal`; event; **sonra** `safeTransferFrom(msg.sender, this, principal)` | `Opened(id, trader, customer, principal, baseToken)` |
| `openReserved(Terms calldata terms, uint256 reservationId) returns (uint256 id)` | `paused`; `msg.sender != terms.customer`; `_validateTerms` | `id = _nextId++`; `_newAgreement(…, Funded, msg.sender)`; `_drawReservation(reservationId, ag)`. Transfer yok | `ReservationDrawn(…)` sonra `Opened(…)` |
| `fund(uint256 id) nonReentrant` | `status == None` → `NotFound()`; `msg.sender != terms.customer` → `Unauthorized()`; `paused` → `Paused()`; `status != Proposed` → `WrongStatus()`; base `!(allowed && isBase)` → `TokenNotAllowed()` (fund anında yeniden kontrol, Rust :599) | `_balances[id][base] = principal`; `_activate(ag)`; **sonra** `safeTransferFrom(customer, this, principal)` | `Activated(id, startTime, endTime)` |
| `fundReserved(uint256 id, uint256 reservationId)` | `fund` ile aynı 5 kontrol | `_drawReservation`; `_activate` | `ReservationDrawn`, `Activated` |
| `accept(uint256 id)` | `NotFound`; `msg.sender != terms.trader` → `Unauthorized()`; `paused`; `status != Funded` → `WrongStatus()` | `_activate(ag)` | `Activated` |
| `cancel(uint256 id) nonReentrant` | `NotFound`; `Proposed` ise `msg.sender != proposer` → `NotParty()`; `Funded` ise `msg.sender ∉ {customer, trader}` → `NotParty()`; başka status → `WrongStatus()` | `refunded = 0`; `Funded` ise `refunded = _balances[id][base]; delete _balances[id][base]`; `status = Cancelled`; event; `refunded > 0` ise `safeTransfer(base, customer, refunded)` (**cüzdana**, rezervasyona değil) | `Cancelled(id, refunded)` |

`cancel` pause'da çalışır. Başarısız oluşturma id tüketmez (revert tüm state'i geri alır).

### 4.5 `trade` (SC-06)
```solidity
function trade(uint256 id, address tokenIn, address tokenOut, uint256 amountIn, uint256 minOut, uint64 deadline)
    external nonReentrant returns (uint256 amountOut)
```
| # | Kural (Rust :680 sırası) |
|---|---|
| 1 | `status == None` → `NotFound()` |
| 2 | `msg.sender != terms.trader` → `Unauthorized()` |
| 3 | `paused` → `Paused()` |
| 4 | `status != Active` → `WrongStatus()` |
| 5 | `nowTs >= endTime || deadline < nowTs` → `Expired()` |
| 6 | `amountIn == 0 || minOut == 0` → `ZeroAmount()` (Rust: `InvalidAmount`) |
| 7 | `tokenIn == tokenOut || !_tokens[tokenIn].allowed || !_tokens[tokenOut].allowed` → `TokenNotAllowed()` (`isBase` aranmaz) |
| 8 | `balIn = _balances[id][tokenIn]; balIn < amountIn` → `InsufficientBalance()` |
| 9 | `tokenOut` listede yoksa: `tokens.length >= MAX_TOKENS` → `TooManyTokens()` (Rust gibi **tokenIn çıkarılmadan önce** sayılır); değilse `tokens.push(tokenOut)` |
| 10 | Efekt: `newIn = balIn - amountIn`; `newIn == 0 && tokenIn != base` ise `delete _balances[id][tokenIn]; _removeToken(tokens, tokenIn)`; değilse `_balances[id][tokenIn] = newIn` |
| 11 | Etkileşim: `before = balanceOf(tokenOut)`; `reported = _swapViaRouter(tokenIn, tokenOut, amountIn, minOut, deadline)`; `amountOut = _credited(reported, before, balanceOf(tokenOut))`; `amountOut < minOut` → `SlippageExceeded()` |
| 12 | `_balances[id][tokenOut] += amountOut` |
| 13 | `(valueAfter, outQuote) = _valuation(ag, tokenOut)`; `tokenOut != base && outQuote == 0` → `TokenNotAllowed()` (base'e dönüş yolu olmayan token'a park edilmez) |
| 14 | `valueAfter < SettleMath.drawdownFloor(principal, maxDrawdownBps)` → `DrawdownBreached()` |
| 15 | `ag.lastValue = valueAfter`; `emit Traded(id, trader, tokenIn, tokenOut, amountIn, amountOut, valueAfter)`; return `amountOut` |

Hata yolları: strict router `minOut`'u reddederse revert `RouterError` (adım 11 içi); lenient router (min'i yok sayan) `SlippageExceeded`.
Adım 13–14 router'a `staticcall` yapar; state değişikliği yaratamaz. Tüm revert'ler tam geri alma sağlar.

### 4.6 `settle` (SC-07, SC-08)
```solidity
function settle(uint256 id, uint256[] calldata minOuts) external nonReentrant
```
Üç kademe (`msg.sender`'a göre):

| Kim | Ne zaman | `minOuts` | Ek taban |
|---|---|---|---|
| `customer` | her an | tam `tokens.length - 1` giriş (base dışı tokenlar `tokens` sırasıyla; sıfır bakiyeli girişler yok sayılır), **olduğu gibi** kullanılır | yok |
| `trader` | her an | aynı | `finalValue >= drawdownFloor` else `DrawdownBreached()` |
| `owner()` (taraf değilse) | `nowTs >= endTime` else `NotExpired()` | boş **veya** tam uzunluk; token tabanı `max(minOuts[i], quote × (1−slip))` | **`finalValue >= lastValue × (1−slip)` else `SlippageExceeded()`** (yeni, risk 2) |
| herkes | `nowTs >= endTime + KEEPER_GRACE` else `NotExpired()`; imza yok | admin gibi | `finalValue >= lastValue × (1−slip)` else `SlippageExceeded()` |

Yanlış `minOuts` uzunluğu → `InvalidAmount()` (negatif giriş Solidity'de yok). Pause'da çalışır.

Referans sözde kod (ZORUNLU sıra; "bakiye sıfırlama swap'tan ÖNCE"):
```solidity
Agreement storage ag = _agreements[id];
if (ag.status == AgreementStatus.None) revert NotFound();
if (ag.status != AgreementStatus.Active) revert WrongStatus();
uint64 nowTs = uint64(block.timestamp);
uint256 nonBase = ag.tokens.length - 1;
bool isCustomer = msg.sender == ag.terms.customer;
bool isTrader   = msg.sender == ag.terms.trader;
bool isParty    = isCustomer || isTrader;
bool isAdmin    = !isParty && msg.sender == owner();
if (isParty) { if (minOuts.length != nonBase) revert InvalidAmount(); }
else {
    if (nowTs < ag.endTime) revert NotExpired();
    if (!isAdmin && nowTs < ag.endTime + KEEPER_GRACE) revert NotExpired();
    if (minOuts.length != 0 && minOuts.length != nonBase) revert InvalidAmount();
}
bool useSupplied = minOuts.length != 0;

// ---- efektler önce ----
ag.status = AgreementStatus.Settled;      // nonReentrant'a ek savunma; revert olursa geri alınır
ag.settledAt = nowTs;

address base = ag.terms.baseToken;
address[] memory held = ag.tokens;        // bellek kopyası
address[] memory kept = new address[](held.length); kept[0] = base; uint256 keptLen = 1;
uint256 baseBefore = IERC20(base).balanceOf(address(this));
uint256 reportedTotal;
uint16 slip = _config.settleSlippageBps;

for (uint256 i = 1; i < held.length; ++i) {
    address token = held[i];
    uint256 supplied = useSupplied ? minOuts[i - 1] : 0;
    uint256 bal = _balances[id][token];
    if (bal == 0) continue;
    delete _balances[id][token];                                  // <-- swap'tan ÖNCE sıfırla
    uint256 q = _quote(token, base, bal);
    if (q == 0) {                                                 // route yok / dust → in-kind
        bool delivered = _tryTransfer(token, ag.terms.customer, bal);
        if (!delivered) { _balances[id][token] = bal; kept[keptLen++] = token; }   // orphan, claim ile alınır
        emit Unliquidated(id, token, bal, delivered);
        continue;
    }
    uint256 minOut = isParty ? supplied : _max(supplied, SettleMath.minOutWithSlippage(q, slip));
    reportedTotal += _swapViaRouter(token, base, bal, minOut, nowTs);   // deadline = now
}
uint256 liquidated = _credited(reportedTotal, baseBefore, IERC20(base).balanceOf(address(this)));
uint256 finalValue = _balances[id][base] + liquidated;              // in-kind bacaklar dahil DEĞİL

if (isTrader && finalValue < SettleMath.drawdownFloor(ag.terms.principal, ag.terms.maxDrawdownBps)) revert DrawdownBreached();
if (!isParty && finalValue < SettleMath.minOutWithSlippage(ag.lastValue, slip)) revert SlippageExceeded();

(uint256 profit, uint256 traderFee, uint256 platformFee, uint256 customerPayout) =
    SettleMath.settlement(finalValue, ag.terms.principal, ag.terms.commissionBps, ag.platformFeeBps /* snapshot */);

delete _balances[id][base];
ag.finalValue = finalValue; ag.lastValue = finalValue;
delete ag.tokens; for (uint256 k = 0; k < keptLen; ++k) ag.tokens.push(kept[k]);

// ---- ödemeler: fee bacakları try, müşteri düz transfer ----
if (!_tryTransfer(base, ag.terms.trader, traderFee))          { customerPayout += traderFee;   traderFee = 0; }
if (!_tryTransfer(base, _config.feeRecipient, platformFee))   { customerPayout += platformFee; platformFee = 0; }
if (customerPayout > 0) IERC20(base).safeTransfer(ag.terms.customer, customerPayout);
ag.traderFee = traderFee; ag.platformFee = platformFee; ag.customerPayout = customerPayout;
emit Settled(id, finalValue, profit, traderFee, platformFee, customerPayout, msg.sender);
```
Notlar: zararda `profit = 0` → fee yok, müşteri her şeyi alır. `Unliquidated` olayları `Settled`'dan önce, bacak başına bir kez.
`feeRecipient` settle anındaki config'ten, `platformFeeBps` anlaşma snapshot'ından okunur.
Router hatası (`RouterError`) tüm settle'ı geri alır — parti kendi `minOuts`'unu düşürerek, admin `end_time` sonrası yeniden dener.

### 4.7 `claim` (Rust :967)
```solidity
function claim(uint256 id, address token) external nonReentrant returns (uint256 amount)
```
`NotFound`; `msg.sender != terms.customer` → `Unauthorized()`; `status != Settled` → `WrongStatus()`; `amount = _balances[id][token]; amount == 0` → `InsufficientBalance()`;
`delete _balances[id][token]`; token listede ve indeks `> 0` ise `_removeToken`; `emit Claimed(id, token, amount)`; `IERC20(token).safeTransfer(customer, amount)` (revert ederse tamamı geri alınır — Rust düz `transfer`). Pause'da çalışır.

### 4.8 View'lar (auth yok)
| İmza | Davranış |
|---|---|
| `getAgreement(uint256 id) → Agreement memory` | `status == None` → `NotFound()` |
| `getBalances(uint256 id) → (address[] tokens, uint256[] amounts)` | `NotFound`; `tokens = ag.tokens` sırası, `amounts[i] = _balances[id][tokens[i]]` (settle sonrası `[base→0, orphan→bal]`) |
| `valueInBase(uint256 id) → uint256` | `NotFound`; `_valuation(ag, base).total`; quote edilemeyen token **0** sayılır. Router'a `staticcall` yapar |
| `previewSettle(uint256 id) → (address[] tokens, uint256[] balances, uint256[] quotes, uint256 estimatedFinalValue, uint256 drawdownFloor, uint256 keeperFloor)` | **yeni yardımcı** (opsiyonel ama önerilen): `tokens = ag.tokens[1..]`, `quotes[i] = _quote(...)`, `estimated = base bakiyesi + Σ quotes`, `drawdownFloor = principal×(1−dd)`, `keeperFloor = lastValue×(1−slip)`. Backend/FE parti `minOuts`'unu `quote × (1 − kullanıcı toleransı)` olarak üretir |
| `getConfig() → Config memory` | ham struct; admin için ayrıca `owner()` |
| `isTokenAllowed(address token) → TokenInfo memory` | bilinmeyen → `{false,false}` |
| `nextId() → uint256`, `nextReservationId() → uint256` | sıradaki id'ler |
| `getReservation(uint256 id) → Reservation memory` | `status == None` → `ReservationNotFound()` |
| OZ: `owner()`, `pendingOwner()`, `paused` (→ `getConfig().paused`), `proxiableUUID()`, `UPGRADE_INTERFACE_VERSION()` | — |

Read-only reentrancy: view'lar zincir üstünde başka kontrat tarafından tüketilmez (backend tx sonrası okur); ek koruma gerekmez.

---

## 5. Custom error'lar ve backend selector eşlemesi (BE-04)

Selector = `keccak256(bytes(signature))[:4]`. Backend `abi.py` bu tabloyu üretir; kod alanı `VaultError.code` olur. **Kodlar asla yeniden numaralanmaz.**

| Kod | Solidity imzası | Rust adı | Nerede |
|---|---|---|---|
| 1 | `NotInitialized()` | `NotInitialized` | Rezerve (initialize sonrası imkânsız). OZ `InvalidInitialization()`, `NotInitializing()` → 1 |
| 2 | `Unauthorized()` | `Unauthorized` | `msg.sender` gereken taraf değil: `propose/open/openReserved` (terms), `fund/fundReserved` (customer), `accept/trade` (trader), `claim` (customer), `release/releaseAll` (res.customer). OZ `OwnableUnauthorizedAccount(address)` → 2 |
| 3 | `Paused()` | `Paused` | `propose/open/openReserved/fund/fundReserved/accept/trade/reserve` |
| 4 | `InvalidTerms()` | `InvalidTerms` | terms aralıkları; `initialize/setFees/setSettleSlippage` bps üst sınırı; `routerDelay_` üst sınırı |
| 5 | `TokenNotAllowed()` | `TokenNotAllowed` | allow-list dışı / base değil / `tokenIn == tokenOut` / `tokenOut` base'e quote edilemiyor |
| 6 | `NotFound()` | `NotFound` | bilinmeyen agreement id (view'lar dahil) |
| 7 | `WrongStatus()` | `WrongStatus` | status uyuşmazlığı (`claim` Settled değilken dahil) |
| 8 | `Expired()` | `Expired` | `trade`: `now >= endTime` veya `deadline < now` |
| 9 | `NotExpired()` | `NotExpired` | admin `settle` `endTime` öncesi; keeper `endTime + 7g` öncesi |
| 10 | `InsufficientBalance()` | `InsufficientBalance` | `trade` bakiye; `claim` bakiye 0 |
| 11 | `TooManyTokens()` | `TooManyTokens` | 6. base dışı token |
| 12 | `DrawdownBreached()` | `DrawdownBreached` | `trade` sonrası değer; trader `settle` |
| 13 | `SlippageExceeded()` | `SlippageExceeded` | credited `< minOut` / `== 0`; admin+keeper `lastValue` tabanı |
| 14 | `Overflow()` | `Overflow` | Rezerve; Solidity checked math `Panic(0x11)` → 14 |
| 15 | `InvalidAmount()` | `InvalidAmount` | yalnız `minOuts` uzunluk hatası (sıfır tutarlar → 22) |
| 16 | `RouterError(bytes reason)` | `RouterError` | router `swapExactTokensForTokens` revert'i (ham neden taşınır), boş dönüş, `input-mismatch` |
| 17 | `NotParty()` | `NotParty` | `cancel` uygun olmayan adres |
| 18 | `ReservationNotFound()` | `ReservationNotFound` | bilinmeyen reservation id |
| 19 | `ReservationClosed()` | `ReservationClosed` | Released/Consumed rezervasyon |
| 20 | `ReservationInsufficient()` | `ReservationInsufficient` | `release` fazla tutar; `_drawReservation` yetersiz |
| 21 | `ReservationMismatch()` | `ReservationMismatch` | başka müşteri / başka token |
| 22 | `ZeroAmount()` | — (yeni) | `release(amount==0)`, `releaseAll` boş, `reserve(0)`, `trade(amountIn==0 \|\| minOut==0)` |
| 23 | `ZeroAddress()` | — (yeni) | `initialize`, `setToken`, `setFees`, `proposeRouterChange`, terms customer/trader = 0. OZ `OwnableInvalidOwner(address)` → 23 |
| 24 | `InvalidRouter()` | — (yeni) | router adresinde kod yok |
| 25 | `RouterChangeNotReady(uint64 activationTime)` | — (yeni) | `applyRouterChange` erken |
| 26 | `NoPendingRouterChange()` | — (yeni) | `applyRouterChange/cancelRouterChange` bekleyen yok |
| 27 | `InvalidToken()` | — (yeni) | `setToken(allowed=true)` kodu olmayan adres |
| 28 | OZ `ReentrancyGuardReentrantCall()` | — | backend adı `Reentrancy` |
| 29 | OZ `SafeERC20FailedOperation(address token)` | — | backend adı `TransferFailed` (approve/transferFrom başarısız, ör. allowance yok) |
| 30 | OZ `UUPSUnauthorizedCallContext()`, `UUPSUnsupportedProxiableUUID(bytes32)`, `ERC1967InvalidImplementation(address)`, `ERC1967NonPayable()`, `AddressEmptyCode(address)`, `FailedCall()` | — | backend adı `UpgradeError` |
| 0 | `Error(string)`, `Panic(uint256)` (0x11 hariç), boş revert | — | "işlem başarısız" genel mesajı |

`MockRouter` hataları ayrı sözlük (§8.1); vault bunları `RouterError(reason)` içinde ham olarak taşır, backend isterse iç selector'ı da çözer.

---

## 6. Event'ler (indexer decode eder, BE-04/BE-09)

`events.rs` 14 event tanımlar (00-inceleme "12" der; `Reserved/Released/ReservationDrawn` de dahildir). `Upgraded` OZ'un `Upgraded(address indexed implementation)` olayıyla değişir. En fazla 3 `indexed`.

| Solidity event | Rust | Ne zaman |
|---|---|---|
| `Proposed(uint256 indexed id, address indexed trader, address indexed customer, uint256 principal, address baseToken)` | `Proposed` | `propose` |
| `Opened(uint256 indexed id, address indexed trader, address indexed customer, uint256 principal, address baseToken)` | `Opened` | `open`, `openReserved` |
| `Activated(uint256 indexed id, uint64 startTime, uint64 endTime)` | `Activated` | `fund`, `fundReserved`, `accept` |
| `Cancelled(uint256 indexed id, uint256 refunded)` | `Cancelled` | `cancel` (Proposed → `refunded = 0`) |
| `Reserved(uint256 indexed id, address indexed customer, address token, uint256 amount, bytes32 listingRef)` | `Reserved` | `reserve` |
| `Released(uint256 indexed id, address indexed customer, address token, uint256 amount, uint256 remaining)` | `Released` | `release`, `releaseAll` |
| `ReservationDrawn(uint256 indexed id, uint256 indexed agreementId, uint256 amount, uint256 remaining)` | `ReservationDrawn` | `openReserved`, `fundReserved` (`Opened/Activated`'dan önce) |
| `Traded(uint256 indexed id, address indexed trader, address tokenIn, address tokenOut, uint256 amountIn, uint256 amountOut, uint256 valueAfter)` | `Traded` | `trade` (`amountOut` = credited) |
| `Settled(uint256 indexed id, uint256 finalValue, uint256 profit, uint256 traderFee, uint256 platformFee, uint256 customerPayout, address by)` | `Settled` | `settle`; `by = msg.sender` |
| `Unliquidated(uint256 indexed id, address token, uint256 amount, bool delivered)` | `Unliquidated` | `settle`, in-kind bacak başına, `Settled`'dan önce. `delivered=false` → indexer "claim bekliyor" |
| `Claimed(uint256 indexed id, address token, uint256 amount)` | `Claimed` | `claim` |
| `ConfigChanged(bytes32 indexed key, address router, uint16 platformFeeBps, address feeRecipient, bool paused, uint16 settleSlippageBps)` | `ConfigChanged` | `applyRouterChange("router")`, `setFees("fees")`, `setPaused("paused")`, `setSettleSlippage("slippage")`; tam config taşır |
| `TokenSet(address indexed token, bool allowed, bool isBase)` | `TokenSet` | `setToken` |
| OZ `Upgraded(address indexed implementation)` | `Upgraded` | `upgradeToAndCall` |
| `RouterChangeProposed(address indexed newRouter, uint64 activationTime)` | — (yeni) | `proposeRouterChange` |
| `RouterChangeCancelled(address indexed cancelledRouter)` | — (yeni) | `cancelRouterChange` |
| OZ `OwnershipTransferStarted(address indexed previousOwner, address indexed newOwner)`, `OwnershipTransferred(...)`, `Initialized(uint64 version)` | — | Ownable2Step / Initializable |

ERC-20 `Transfer` olayları (escrow, swap, ödeme) mutabakat için ayrıca okunabilir. Indexer benzersizliği `(tx_hash, log_index)`.

---

## 7. `SettleMath` kütüphanesi (`src/libraries/SettleMath.sol`)

`library SettleMath` — `pure`, `uint256`, checked math (taşma → `Panic(0x11)`). Hatalar `ITraderVault.InvalidTerms()` olarak import edilir.

```
bpsOf(amount, bps)                 = amount * bps / 10_000                          // floor
drawdownFloor(principal, ddBps)    = require(ddBps <= 10_000) else InvalidTerms;  principal * (10_000 - ddBps) / 10_000
minOutWithSlippage(quote, slipBps) = require(slipBps <= 10_000) else InvalidTerms; quote * (10_000 - slipBps) / 10_000
settlement(finalValue, principal, commissionBps, platformFeeBps)
    → (profit, traderFee, platformFee, customerPayout)
    profit         = finalValue > principal ? finalValue - principal : 0
    traderFee      = bpsOf(profit, commissionBps)
    platformFee    = bpsOf(profit, platformFeeBps)
    customerPayout = finalValue - traderFee - platformFee     // commission+platform <= 6000 bps olduğu için negatif olamaz
```
Yuvarlama: her adımda aşağı (tam sayı bölmesi); fee'ler kâr üzerinden, anapara veya zarardan asla alınmaz.

Fuzz invariant'ları (`SettleMath.t.sol`, Rust `settlement_math_invariants` + `drawdown_and_slippage_math`):
- `customerPayout + traderFee + platformFee == finalValue`
- `traderFee + platformFee <= profit`
- `finalValue <= principal ⇒ customerPayout == finalValue && fees == 0`
- `finalValue > principal ⇒ customerPayout >= principal`
- `traderFee == profit * commissionBps / 1e4`, `platformFee == profit * platformFeeBps / 1e4`
- `drawdownFloor(p, bps) <= p`, `bps` arttıkça monoton azalır; `drawdownFloor(p, 10_000) == 0`; `bpsOf(999, 2500) == 249`
- Sınırlar: `finalValue, principal ∈ [0, 1e40]`, `commissionBps ∈ [0, 5000]`, `platformFeeBps ∈ [0, 1000]`
- `drawdownFloor(_, 10_001)` → `InvalidTerms`; `bpsOf(type(uint256).max, 2)` → `Panic(0x11)` (`vm.expectRevert(stdError.arithmeticError)`)

---

## 8. Mock'lar (SC-09)

### 8.1 `MockRouter.sol` (testnet'e deploy edilir)
```solidity
contract MockRouter is IUniswapV2Router02Like, Ownable {
    using SafeERC20 for IERC20;
    struct Price { uint256 num; uint256 den; }          // out = in * num / den
    mapping(address => mapping(address => Price)) public prices;   // yönlü: prices[in][out]
    bool public strict = true;
    error NoPrice(address tokenIn, address tokenOut);
    error InvalidPath();
    error InsufficientOutput(uint256 amountOut, uint256 amountOutMin);
    error DeadlineExpired();
    error InvalidAmount();
    event PriceSet(address indexed tokenIn, address indexed tokenOut, uint256 num, uint256 den);
    event PriceCleared(address indexed tokenIn, address indexed tokenOut);
    event StrictSet(bool strict);
    constructor(address owner_) Ownable(owner_) {}
}
```
| Fonksiyon | Kural (Rust `mock_router/src/lib.rs`) |
|---|---|
| `setPrice(address tokenIn, address tokenOut, uint256 num, uint256 den) onlyOwner` | `num == 0 || den == 0` → `InvalidAmount()`; yaz; `PriceSet` |
| `clearPrice(address tokenIn, address tokenOut) onlyOwner` | `delete prices[in][out]`; sonraki quote/swap `NoPrice` (likidite kaybı simülasyonu) |
| `setStrict(bool) onlyOwner` | `false` → router `amountOutMin`'i yok sayar (vault'un kendi re-check'ini test etmek için) |
| `withdraw(address token, address to, uint256 amount) onlyOwner` | `amount == 0` → `InvalidAmount()`; `safeTransfer` |
| `getAmountsOut(uint256 amountIn, address[] calldata path) view → uint256[]` | `amountIn == 0` → `InvalidAmount()`; `path.length < 2` → `InvalidPath()`; her adım `prices[path[i]][path[i+1]].den == 0` → `NoPrice`; `current = current * num / den`; `[amountIn, …, out]` |
| `swapExactTokensForTokens(uint256 amountIn, uint256 amountOutMin, address[] calldata path, address to, uint256 deadline) → uint256[]` | `block.timestamp > deadline` → `DeadlineExpired()`; `amounts = getAmountsOut(...)`; `strict && out < amountOutMin` → `InsufficientOutput(out, min)`; `IERC20(path[0]).safeTransferFrom(msg.sender, this, amountIn)`; `IERC20(path[last]).safeTransfer(to, out)`; return `amounts` |

Rust'tan fark: girdi `to`'dan değil **`msg.sender`'dan** çekilir (Uniswap semantiği); `router_pair_for` yok (mock kendi likiditesini tutar). Likidite: deploy betiği mock'a `TestToken.mint` eder.

### 8.2 `TestToken.sol` (testnet'e deploy edilir)
```solidity
contract TestToken is ERC20, ERC20Burnable, AccessControl {
    bytes32 public constant MINTER_ROLE = keccak256("MINTER_ROLE");
    uint8 private immutable _decimals;
    constructor(string memory name_, string memory symbol_, uint8 decimals_, address admin) ERC20(name_, symbol_) {
        _decimals = decimals_;
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(MINTER_ROLE, admin);
    }
    function decimals() public view override returns (uint8) { return _decimals; }
    function mint(address to, uint256 amount) external onlyRole(MINTER_ROLE) { _mint(to, amount); }
}
```
Deploy sonrası `grantRole(MINTER_ROLE, MINTER_ADDRESS)` (backend faucet anahtarı, OPS-11; günlük limit backend'de, BE-11). Herkese açık mint yok. Fee-on-transfer/rebasing davranışı yok.

### 8.3 Yalnız test mock'ları (`test/mocks/`)
- `BlocklistToken`: `TestToken` + `setBlocked(address, bool)`; `_update` içinde `from`/`to` bloklu ise revert. Kullanım: alınamayan fee bacağı (17), orphan in-kind (34) — EVM'de trustline yok, blocklist aynı etkiyi verir.
- `ReentrantToken`: `transfer/transferFrom` sırasında yapılandırılan `target.call(data)`'yı çalıştırır (vault'a `trade/settle/claim/cancel/release` reentrancy denemeleri).
- `MaliciousRouter`: modlar `REPORT_MORE` (bildirdiğinden az gönderir), `SEND_MORE` (bildirdiğinden çok gönderir), `IGNORE_MIN` (lenient), `NO_PULL` (girdiyi çekmez), `REENTER` (swap içinde `vault.trade/settle` çağırır), `GAS_BURN` (quote'ta revert).
- `TraderVaultV2`: `TraderVault` + `uint256 public newField` (`__gap`'ten) + `function version() pure returns (uint256) {return 2;}` + `reinitializer(2)`.

---

## 9. Deploy (SC-11) ve `deployments/monad-testnet.json`

### 9.1 `script/Deploy.s.sol` akışı (ZORUNLU)
Env: `DEPLOYER_PRIVATE_KEY`, `FEE_RECIPIENT`, `MINTER_ADDRESS`, `PLATFORM_FEE_BPS` (100), `SETTLE_SLIPPAGE_BPS` (100), `ROUTER_DELAY` (testnet 600, ana ağ 86400).
1. `vm.startBroadcast(deployerKey)`
2. `tUSDC = new TestToken("Test USDC","tUSDC",6,deployer)`, `tWETH = new TestToken("Test WETH","tWETH",18,deployer)`, `tWBTC = new TestToken("Test WBTC","tWBTC",8,deployer)`
3. `router = new MockRouter(deployer)`
4. Fiyatlar (ham birim oranı, `out = in × num / den`), her iki yön:
   - 1 WETH = 3000 USDC: `setPrice(tUSDC, tWETH, 1e18, 3000e6)`, `setPrice(tWETH, tUSDC, 3000e6, 1e18)`
   - 1 WBTC = 60000 USDC: `setPrice(tUSDC, tWBTC, 1e8, 60000e6)`, `setPrice(tWBTC, tUSDC, 60000e6, 1e8)`
   - WETH↔WBTC (20:1): `setPrice(tWETH, tWBTC, 1e8, 20e18)`, `setPrice(tWBTC, tWETH, 20e18, 1e8)`
5. Likidite: `mint(router, 10_000_000e6 tUSDC)`, `mint(router, 5_000e18 tWETH)`, `mint(router, 250e8 tWBTC)`; demo: `mint(deployer, 100_000e6 tUSDC)`
6. `tUSDC/tWETH/tWBTC.grantRole(MINTER_ROLE, MINTER_ADDRESS)`
7. `impl = new TraderVault()`; `proxy = new ERC1967Proxy(address(impl), abi.encodeCall(TraderVault.initialize, (deployer, address(router), FEE_RECIPIENT, PLATFORM_FEE_BPS, SETTLE_SLIPPAGE_BPS, ROUTER_DELAY)))`
8. `vault = TraderVault(address(proxy))`; `setToken(tUSDC, true, true)`; `setToken(tWETH, true, false)`; `setToken(tWBTC, true, false)`
9. `vm.stopBroadcast()`; `vm.serialize*` + `vm.writeJson(json, "deployments/monad-testnet.json")` (txHashes/blockNumber hariç)
10. Sonra `scripts/deployments-from-broadcast.sh`: `broadcast/Deploy.s.sol/10143/run-latest.json` → `transactions[].{contractName, contractAddress, hash}` ve `receipts[].blockNumber` ile `txHashes` ve `blockNumber` doldurulur.

### 9.2 Komutlar
```sh
forge script script/Deploy.s.sol:Deploy --rpc-url monad_testnet --broadcast --slow -vvvv
scripts/deployments-from-broadcast.sh
scripts/export-abi.sh
```
Yerel: `anvil --chain-id 10143` + aynı betik (`--rpc-url anvil`) → backend `pytest -m chain` (BE-27).

### 9.3 Doğrulama (DoD-2)
```sh
V="--chain 10143 --verifier sourcify --verifier-url https://sourcify-api-monad.blockvision.org/"
forge verify-contract $IMPL   src/TraderVault.sol:TraderVault $V
forge verify-contract $PROXY  lib/openzeppelin-contracts/contracts/proxy/ERC1967/ERC1967Proxy.sol:ERC1967Proxy $V \
     --constructor-args $(cast abi-encode "constructor(address,bytes)" $IMPL $INIT_CALLDATA)
forge verify-contract $ROUTER src/mocks/MockRouter.sol:MockRouter $V --constructor-args $(cast abi-encode "constructor(address)" $DEPLOYER)
forge verify-contract $TUSDC  src/mocks/TestToken.sol:TestToken  $V --constructor-args $(cast abi-encode "constructor(string,string,uint8,address)" "Test USDC" tUSDC 6 $DEPLOYER)
```
Monadscan alternatifi: `--verifier etherscan --etherscan-api-key $MONADSCAN_API_KEY --watch`.

### 9.4 `deployments/monad-testnet.json` şeması (ZORUNLU alanlar)
```json
{
  "chainId": 10143,
  "vault": { "proxy": "0x…", "implementation": "0x…" },
  "router": "0x…",
  "tokens": [
    { "symbol": "tUSDC", "address": "0x…", "decimals": 6,  "isBase": true  },
    { "symbol": "tWETH", "address": "0x…", "decimals": 18, "isBase": false },
    { "symbol": "tWBTC", "address": "0x…", "decimals": 8,  "isBase": false }
  ],
  "deployer": "0x67aD0CaE18528017B79E0aF8C4D3dd5937caFF19",
  "blockNumber": 0,
  "txHashes": {
    "tUSDC": "0x…", "tWETH": "0x…", "tWBTC": "0x…", "router": "0x…",
    "vaultImplementation": "0x…", "vaultProxy": "0x…"
  }
}
```
Opsiyonel ek alanlar: `"routerDelay"`, `"platformFeeBps"`, `"settleSlippageBps"`, `"feeRecipient"`, `"gitCommit"`, `"deployedAt"`. Adresler EIP-55 checksum'lu yazılır; backend küçük harfe normalize eder (K11). `blockNumber` indexer başlangıç bloğudur (BE-09).

---

## 10. Test planı (SC-10)

### 10.1 Ortak fixture (`test/Base.t.sol`, Rust `setup()` :130)
- Aktörler: `admin` (owner), `customer`, `trader`, `feeRecipient`, `stranger` (`makeAddr`).
- Tokenlar: `usdc` (6 dp, `allowed+base`), `alt` (18 dp, `allowed+base`; Rust `xlm` rolü), `eurc` (6 dp, `allowed`, base değil). Ek tokenlar `newToken(decimals, allowed, isBase)` ile.
- Sabitler: `U6 = 1e6`, `U18 = 1e18`, `PRINCIPAL = 1000 * U6`, `DAY = 86_400`, `DURATION = 30 * DAY`, `START_TS = 1_700_000_000`, `LIQ` (router'a bol likidite), `PLATFORM_FEE_BPS = 100`, `SETTLE_SLIPPAGE_BPS = 100`, `ROUTER_DELAY = 86_400`.
- Fiyatlar (Rust: 1 USDC = 4 XLM, 1 USDC = 0.9 EURC): `setPrice(usdc, alt, 4e12, 1)`, `setPrice(alt, usdc, 1, 4e12)`, `setPrice(usdc, eurc, 9, 10)`, `setPrice(eurc, usdc, 10, 9)`.
- Deploy: impl + `ERC1967Proxy`; `vm.warp(START_TS)`; `usdc.mint(customer, PRINCIPAL)`; müşteri `approve(vault, type(uint256).max)` **fixture'da** (testler `approve` yolunu ayrıca test eder).
- Yardımcılar: `terms()` (commission 2000, drawdown 2000, rastgele `listingRef`), `openActive()` (open + accept), `proposeActive()` (propose + fund), `vaultBalance(id, token)`, `snapshot(id)/assertUnchanged` (agreement, balances, vault ve router token bakiyeleri), `expectVaultError(bytes4)`.
- Rust'taki `env.auths()` ağaç doğrulamaları → `vm.prank`; yanlış imzacı → `Unauthorized()/NotParty()`; auth yok senaryosu EVM'de anlamsız (msg.sender her zaman var).
- Dust: Rust 1 stroop XLM → 1 wei `alt` (quote `1 / 4e12 = 0`).

### 10.2 47 Rust testinin Foundry karşılığı
| # | Rust testi | Foundry (`test_…`) | Dosya | Not |
|---|---|---|---|---|
| 1 | constructor_sets_config_and_ids | `test_initialize_setsConfigAndIds` | Admin | + `owner()`, `nextReservationId()==1`, `routerDelay`; TTL iddiası silinir |
| 2 | constructor_rejects_platform_fee_above_cap | `test_initialize_revertsPlatformFeeAboveCap` | Admin | proxy deploy'u `InvalidTerms` ile revert |
| 3 | constructor_rejects_slippage_above_cap | `test_initialize_revertsSlippageAboveCap` | Admin | |
| 4 | admin_token_allow_list | `test_setToken_allowListAndEvent` | Admin | `vm.expectEmit`; de-list `isBase`'i siler; `InvalidToken` (kodsuz adres) eklenir |
| 5 | admin_config_changes_emit_events_and_validate | `test_adminConfig_changesEmitEventsAndValidate` | Admin | `setRouter` → `proposeRouterChange` + `warp` + `applyRouterChange`; `setFees(1001)`, `setSettleSlippage(5001)` → `InvalidTerms` |
| 6 | admin_functions_reject_non_admin | `test_admin_revertsForNonOwner` | Admin | tüm admin fn + `upgradeToAndCall` → `OwnableUnauthorizedAccount` |
| 7 | upgrade_admin_path_reaches_deployer | `test_upgrade_ownerCanUpgradeToV2` | Upgrade | gerçek UUPS (bkz. 10.3) |
| 8 | lifecycle_customer_initiated | `test_lifecycle_customerInitiated` | Lifecycle | open→accept→trade(200 USDC→800 alt)→customer settle `[200e6]`; tüm event/bakiye iddiaları |
| 9 | lifecycle_trader_initiated_with_profit | `test_lifecycle_traderInitiatedWithProfit` | Lifecycle | fiyat 2×; `traderFee=40e6`, `platformFee=2e6` |
| 10 | settle_with_loss_pays_no_fees | `test_settle_lossPaysNoFees` | Settle | |
| 11 | settle_without_trades_returns_principal | `test_settle_withoutTradesReturnsPrincipal` | Settle | `minOuts = []` |
| 12 | settle_keeper_path_opens_after_grace_with_reference_floor | `test_settle_keeperPathAfterGraceWithReferenceFloor` | Settle | `NotExpired` ×3, 100× ters fiyat → `SlippageExceeded`, `[201e6,1]` → `RouterError`, uzunluk → `InvalidAmount`, 19/80 → 990e6 geçer, `by = stranger` |
| 13 | settle_by_admin_after_expiry_requires_auth_and_honours_min_outs | `test_settle_adminAfterExpiryHonoursMinOuts` | Settle | + yeni iddia: admin da `lastValue` tabanına takılır (`test_settle_adminBoundByLastValueFloor`) |
| 14 | settle_by_trader_cannot_go_below_drawdown_floor | `test_settle_traderCannotGoBelowDrawdownFloor` | Settle | tam taban (800e6) kabul; müşteri 505e6 ile çıkabilir |
| 15 | settle_post_expiry_by_party_still_uses_their_min_outs | `test_settle_postExpiryPartyUsesOwnMinOuts` | Settle | |
| 16 | settle_validation | `test_settle_validation` | Settle | negatif giriş dalı silinir; `[]`, `[1,1]`, `[201e6]`→`RouterError`, id 99→`NotFound`, ikinci settle→`WrongStatus` |
| 17 | fee_leg_that_cannot_be_received_is_folded_into_customer_payout | `test_settle_unreceivableFeeLegFoldedIntoCustomer` | Settle | base = `BlocklistToken`, `feeRecipient` bloklu; `platformFee=0`, `customerPayout=1160e6` |
| 18 | paused_blocks_new_activity_only | `test_pause_blocksNewActivityOnly` | Lifecycle | + `reserve` bloklu, `release/releaseAll/claim` çalışır |
| 19 | invalid_terms_are_rejected | `test_terms_invalidAreRejected` | Lifecycle | `principal=-1` dalı silinir; `customer/trader = 0` → `ZeroAddress` eklenir; sınır değerler kabul |
| 20 | token_allow_list_is_enforced | `test_allowList_isEnforced` | Trade | |
| 21 | max_tokens_bound_and_slot_reuse | `test_trade_maxTokensBoundAndSlotReuse` | Trade | 5 base dışı + 6. → `TooManyTokens`; slot serbest kalır; 5 swap'lı settle; kaynak iddiaları → `vm.snapshotGas` (opsiyonel) |
| 22 | trade_router_slippage_failure_rolls_back | `test_trade_routerSlippageFailureRollsBack` | Trade | `RouterError` + `assertUnchanged` |
| 23 | trade_recheck_catches_router_that_ignores_min_out | `test_trade_recheckCatchesLenientRouter` | Trade | `setStrict(false)` → `SlippageExceeded` |
| 24 | trade_drawdown_breach_rolls_back | `test_trade_drawdownBreachRollsBack` | Trade | tam taban kabul |
| 25 | trade_rejects_token_without_route_back_to_base | `test_trade_rejectsTokenWithoutRouteToBase` | Trade | doğrudan ve iki adımlı |
| 26 | trade_input_validation | `test_trade_inputValidation` | Trade | 0 → `ZeroAmount`; negatif dalı silinir; `deadline < now`, `now == endTime` → `Expired` |
| 27 | lifecycle_functions_reject_wrong_signer | `test_auth_wrongSenderReverts` | Lifecycle | her mutasyon için `vm.prank(stranger)` → `Unauthorized()` (cancel → `NotParty()`); state değişmez |
| 28 | cancel_proposed_only_by_proposer | `test_cancel_proposedOnlyByProposer` | Lifecycle | |
| 29 | cancel_funded_refunds_customer | `test_cancel_fundedRefundsCustomer` | Lifecycle | customer ve trader; Active → `WrongStatus` |
| 30 | ttl_is_extended_on_writes | **silinir** | — | EVM'de TTL yok |
| 31 | views_on_missing_agreement | `test_views_missingAgreementReverts` | Views | + `previewSettle`, `getReservation` → `ReservationNotFound` |
| 32 | value_in_base_counts_unquotable_token_as_zero | `test_valueInBase_unquotableCountsAsZero` | Views | `clearPrice` sonrası in-kind teslim `delivered=true` |
| 33 | settle_delivers_unquotable_dust_in_kind_on_every_path | `test_settle_deliversDustInKindOnEveryPath` | Settle | 3 anlaşma, 1 wei `alt`; customer/trader/keeper |
| 34 | settle_orphans_unreceivable_in_kind_leg_until_claimed | `test_settle_orphansUnreceivableLegUntilClaimed` | Settle | `BlocklistToken` müşteriyi bloklar → `delivered=false`, `tokens=[usdc, weird]`; `claim` revert; unblock → `claim` 300e6, `Claimed`; `claim(active)` → `WrongStatus` |
| 35 | ids_are_sequential_across_propose_and_open | `test_ids_sequentialAcrossProposeAndOpen` | Views | başarısız oluşturma id tüketmez |
| 36 | settlement_math_invariants | `testFuzz_settlement_invariants`, `test_settlement_overflowPanics` | SettleMath | §7 |
| 37 | drawdown_and_slippage_math | `test_math_drawdownAndSlippage`, `testFuzz_drawdownFloor_monotonic` | SettleMath | |
| 38 | reserve_locks_capital_and_release_gives_it_back | `test_reserve_locksCapitalAndReleaseGivesItBack` | Reservation | `release(rid, 0)` → **`ZeroAmount`**; `releaseAll` kalanı verir; sonra `ReservationClosed` |
| 39 | reserve_validation | `test_reserve_validation` | Reservation | `eurc` → `TokenNotAllowed`; 0 → `ZeroAmount`; pause → `Paused` |
| 40 | release_works_while_paused | `test_release_worksWhilePaused` | Reservation | `releaseAll` |
| 41 | open_reserved_draws_the_principal_without_touching_the_wallet | `test_openReserved_drawsPrincipalWithoutWallet` | Reservation | |
| 42 | fund_reserved_activates_a_proposed_agreement | `test_fundReserved_activatesProposed` | Reservation | ikinci kullanım → `ReservationClosed` |
| 43 | reserved_funding_rejects_a_reservation_that_does_not_fit | `test_reserved_rejectsReservationThatDoesNotFit` | Reservation | Mismatch ×2, Insufficient, NotFound |
| 44 | a_reservation_bigger_than_the_principal_keeps_the_rest_locked | `test_reserve_biggerThanPrincipalKeepsRest` | Reservation | |
| 45 | reserved_agreement_settles_and_pays_the_customer | `test_reserved_settlesAndPaysCustomer` | Reservation | |
| 46 | cancelling_a_reserved_agreement_refunds_the_wallet | `test_cancel_reservedRefundsWallet` | Reservation | rezervasyon `Consumed` kalır |
| 47 | reservation_ids_are_their_own_sequence | `test_reservationIds_ownSequence` | Reservation | |
| M1 | mock quote_and_swap | `test_mockRouter_quoteAndSwap` | MockRouter | girdi `msg.sender`'dan çekilir (approve gerekir) |
| M2 | mock errors | `test_mockRouter_errors` | MockRouter | `NoPrice`, `InvalidAmount`, `InvalidPath`, `InsufficientOutput`, `DeadlineExpired`, lenient mod |
| M3 | mock admin_only | `test_mockRouter_onlyOwner` | MockRouter | `OwnableUnauthorizedAccount` |

### 10.3 Yeni testler (ZORUNLU)
**Security.t.sol**
- `test_reentrancy_tokenReentersTradeDuringSwap`: `ReentrantToken` allow-list'te, `MaliciousRouter(REENTER)` → `ReentrancyGuardReentrantCall`; state değişmez.
- `test_reentrancy_tokenReentersSettleDuringInKind`: in-kind teslim sırasında `settle/claim/cancel` → revert.
- `test_reentrancy_tokenReentersClaim`, `test_reentrancy_tokenReentersRelease`, `test_reentrancy_routerReentersSettle`.
- `test_router_reportsMoreThanSent_creditsActualDelta` (`REPORT_MORE`): credited = gerçek fark; fark `< minOut` → `SlippageExceeded`.
- `test_router_sendsMoreThanReported_creditsReported` (`SEND_MORE`): credited = reported; vault bakiyesi ≥ muhasebe.
- `test_router_doesNotPullInput_reverts` (`NO_PULL`) → `RouterError("input-mismatch")`.
- `test_router_quoteReverts_countsAsZero` (`GAS_BURN` quote revert) → `valueInBase` 0 sayar; in-kind yol.
- `test_approve_isResetAfterSwap`: swap sonrası `allowance(vault, router) == 0`.
- `test_open_withoutApprove_revertsTransferFailed` (K4): `SafeERC20FailedOperation`.
- `test_feeSnapshot_setFeesAfterOpenDoesNotAffectAgreement`: open (fee 100) → `setFees(1000)` → settle kârda 1% keser; yeni anlaşma 10%.
- `test_routerDelay_proposeApplyCancel`: erken `apply` → `RouterChangeNotReady`; `warp(delay)` → uygulanır + `ConfigChanged("router")`; `cancel`; pending yokken → `NoPendingRouterChange`; sıfır adres → `ZeroAddress`; kodsuz → `InvalidRouter`.
- `test_adminSettle_boundByLastValueFloor` (risk 2): süresi dolmuş anlaşma, 100× ters fiyat, admin `settle([])` → `SlippageExceeded`; tolerans içinde geçer.
- `test_ownable2Step_transferAndAccept`: pending owner admin fn çağıramaz; `acceptOwnership` sonrası eski owner reddedilir.
- `test_decimals_usdc6_wbtc8_weth18_endToEnd`: base tUSDC(6), 1 WBTC=60000 USDC, 1 WETH=3000 USDC gerçekçi oranlar; trade USDC→WBTC→WETH→USDC; drawdown tabanı ve settle bölüşümü ham birimlerde doğrulanır; dust 1 satoshi in-kind.
- `test_zeroAmount_paths`: `reserve(0)`, `trade(…,0,…)`, `trade(…,…,0)`, `release(id,0)` → `ZeroAmount`.

**Upgrade.t.sol**
- `test_upgrade_ownerCanUpgradeToV2`: aktif anlaşma + rezervasyon varken `upgradeToAndCall(v2, reinit)` → `Upgraded` event; `getAgreement/getBalances/getConfig/nextId` aynı; `version()==2`; settle çalışır.
- `test_upgrade_revertsForNonOwner`, `test_upgrade_implementationCannotBeInitialized` (`InvalidInitialization`), `test_initialize_cannotRunTwice`, `test_upgrade_storageLayoutUnchanged` (`forge inspect` diff CI'da).

**Invariant.t.sol** (handler: aktörler customer/trader/stranger/admin; eylemler `reserve/release/releaseAll/propose/open/openReserved/fund/fundReserved/accept/cancel/trade/settle/claim/setPrice/warp`; `fail_on_revert=false`)
- `invariant_tokenBalancesCoverAccounting`: her token `t` için `IERC20(t).balanceOf(vault) >= Σ_id _balances[id][t] + Σ_res(open, token==t) amount`; `MockRouter` ile **eşitlik** iddia edilir (`SEND_MORE` yalnız Security'de).
- `invariant_tokensListWellFormed`: `tokens.length <= 6`, `tokens[0] == baseToken`, tekrar yok, Active iken base dışı her token'ın bakiyesi > 0.
- `invariant_statusMonotonic`: `Settled/Cancelled` geri dönmez; `Settled` ise `finalValue == traderFee + platformFee + customerPayout`.
- `invariant_idsIncreaseByOne`: `nextId`, `nextReservationId` yalnız başarılı oluşturmada 1 artar.

### 10.4 Kapsam ve çalıştırma
`forge test` (varsayılan profil) her PR'da; `FOUNDRY_PROFILE=ci forge test` gecelik; `forge coverage` vault ≥ %90 satır (DoD-1). Test sırası kullanıcı talimatına göre **en sona** bırakılabilir; kod yazımı beklemez.

---

## 11. Güvenlik notları ve kabul edilen riskler

| # | Konu | Durum / önlem |
|---|---|---|
| 1 | **Spot quote ile drawdown atlatma** (00-inceleme risk 1; CONTRACT.md §8). Trader aynı tx içinde havuzu eğip `trade` çağırabilir; Monad'da atomik bundle kolaydır | Testnet'te **kabul**, belgelenir. Kontratın garantileri: dürüst trader tabanı aşamaz; trader settle'ı tabanın altına inemez; keeper yolu değer çıkaramaz; müşteri her an kendi `minOuts` ile çıkabilir. Sprint 3: oracle/TWAP ile `min(quote, reference)` |
| 2 | **Admin + kötü router** | Router değişimi 24 saat gecikmeli ve olaylı (`RouterChangeProposed`); taraflar bu sürede settle edebilir. Admin settle yolu artık `lastValue × (1−slip)` tabanına bağlı; admin `settleSlippageBps`'i en fazla 5000'e çekebilir → zarar `lastValue`'nun %50'siyle sınırlı ve yalnız kötü router ile mümkün |
| 3 | **Platform fee sonradan değişimi** | `Agreement.platformFeeBps` snapshot'ı; `setFees` yalnız yeni anlaşmaları etkiler |
| 4 | **Keeper canlılığı**: in-kind'a düşen token (quote 0) sorun değil; ancak `lastValue` tabanı piyasa `slip`'ten fazla düştüyse keeper **kalıcı olarak** settle edemez | Belgelenir. Çözüm yolu: taraf kendi `minOuts` ile; admin `endTime` sonrası (aynı taban); son çare `setSettleSlippage` artışı |
| 5 | `release(0)` tuzağı | `ZeroAmount`; `releaseAll` ayrı |
| 6 | **Reentrancy** | `nonReentrant` (storage tabanlı guard), CEI, settle'da bakiye silme swap'tan önce, status `Settled` dış çağrılardan önce; `ReentrantToken/MaliciousRouter` testleri |
| 7 | **Fee-on-transfer / rebasing / ERC-777 tokenlar** | Allow-list dışı tutulur (admin sorumluluğu). `open/fund/reserve` bakiye farkı ölçmez; `_swapViaRouter` girdi farkını ölçer |
| 8 | **`minOuts` ↔ `tokens` sırası uyumsuzluğu** | Parti `minOuts`'unu `previewSettle` ile üretir; arada bir `trade` listeyi değiştirirse uzunluk kontrolü (`InvalidAmount`) çoğu durumu yakalar, ancak aynı uzunlukta yer değişimi yakalanmaz (trader ön-koşu ile müşterinin düşük `minOut`'unu değerli token'a uygulatabilir). Rust'ta da vardı. Sprint 3 adayı: `settle(id, address[] tokens, uint256[] minOuts)` aşırı yüklemesi (liste eşitliği kontrolü) |
| 9 | **Read-only reentrancy** | View'lar zincir üstünde tüketilmez; kabul |
| 10 | **Admin anahtarı / upgrade** | Testnet: deployer EOA (sunucuda **tutulmaz**, OPS-11). Ana ağ öncesi owner → Safe + timelock; `Ownable2Step` yanlış adrese devri engeller |
| 11 | **Gas griefing** (kötü token/router tüm gas'ı yakar) | Yalnız allow-list'teki adresler çağrılır; kabul |
| 12 | **Monad gas modeli** | Kullanıcı `gas_limit` kadar ödediği için backend `estimateGas × 1.2` önerir; settle en pahalı yol (≤5 quote + 5 swap + 3 transfer) |
| 13 | **Reorg / onay derinliği** | Kontrat dışı; indexer `CONFIRMATIONS` ve `last_block_hash` (BE-09) |
| 14 | **Slither** | `arbitrary-send-erc20` (transferFrom `msg.sender`'dan — kasıtlı), `reentrancy-*` (guard var), `calls-loop` (MAX_TOKENS sınırlı) bulguları gerekçeli kabul; yüksek seviye bulgu sıfır olmalı |

---

## Ek A — Rust → Solidity davranış farkları (özet)
1. `caller/customer/trader` serbest parametreleri kalktı; `msg.sender` kullanılır. `cancel(id, caller)` → `cancel(id)`; `settle(id, caller, minOuts)` → `settle(id, minOuts)`; `reserve(customer, …)` → `reserve(…)`; `propose(trader, terms)` → `propose(terms)`; `open(customer, terms)` → `open(terms)`.
2. Admin = `owner()` (Ownable2Step); `Config.admin` yok. `set_router` → `proposeRouterChange/applyRouterChange/cancelRouterChange` (gecikmeli). `upgrade(wasm_hash)` → OZ `upgradeToAndCall`.
3. `InvalidAmount(15)` artık yalnız `minOuts` uzunluğu için; sıfır tutarlar `ZeroAmount(22)`. `release(id, 0)` tamamını iade **etmez**, revert eder; `releaseAll` eklendi.
4. `platformFeeBps` anlaşmaya sabitlenir (Rust settle anında config okurdu). Admin settle yolu da `lastValue` tabanına tabi (Rust yalnız keeper).
5. `RouterError` ham revert nedenini taşır (`bytes`). `_swapViaRouter` router'ın tam `amountIn` çektiğini doğrular (yeni).
6. Enum'lara `None=0` nöbetçisi eklendi → sayısal kodlar +1 (§2.1). `Terms.customer/trader == 0` → `ZeroAddress`.
7. Token transferi kullanıcıdan `transferFrom` ile (approve gerekir, K4); Stellar auth ağacı yok. Trustline kavramı yok; `Unliquidated{delivered:false}` yalnız transferi reddeden tokenlarda (blocklist/paused token) oluşur.
8. TTL/instance/persistent ayrımı yok; `remove_balance` = `delete`. Settle'da bakiye silme swap'tan **önce**; `status = Settled` dış çağrılardan önce.
9. Event'ler ABI event'i; `ConfigChanged.key` `Symbol` → `bytes32`. `Upgraded` OZ'dan.
10. Mock router girdiyi `to` yerine `msg.sender`'dan çeker; `router_pair_for` yok.

## Ek B — Uygulama sırası (SPRINT-2 SC-xx ↔ bölüm)
SC-01 → §1 · SC-02 → §2, §3, §5, §6 · SC-03 → §4.1, §4.2 · SC-04 → §4.3 · SC-05 → §4.4 · SC-06 → §4.5 · SC-07 → §4.6, §4.7 · SC-08 → §4.2 (router delay), §4.0 `_newAgreement` (snapshot), §4.6 (admin tabanı) · SC-09 → §8 · SC-10 → §10 · SC-11 → §9 · SC-12 → §1.4.
