# 03 — Backend'in Monad'a taşınma tasarımı ve uygulama planı

**Durum:** Sprint 2 BE-01…BE-27 için bağlayıcı uygulama tasarımı. Uygulayan ajanlar paralel çalışır; §11'deki
**dosya sahipliği** planı zorunludur.
**Kaynaklar (öncelik sırasıyla):** `SPRINT-2-MONAD.md` §2 (K1–K13), `docs/monad/02-api-sozlesme.md` (HTTP şekli),
`docs/monad/01-kontrat-spec.md` (ABI, hata/event tabloları), `docs/monad/06-monad-testnet.md` (doğrulanmış ağ
bilgisi), `docs/monad/00-inceleme.md` (v1-stellar bulguları). Kod referansları `backend/` (v1-stellar tabanı).
**Hedef ağ:** Monad Testnet, chainId `10143`, RPC `https://testnet-rpc.monad.xyz`, MON 18 ondalık (yalnız gas; K6).
**Dil kuralı:** doküman Türkçe; kod tanımlayıcıları, JSON alanları, hata kodları İngilizce.

Bu dokümanı uygulayan ajan, sahip olduğu dosyalar için başka kaynağa bakmadan kodu yazabilmelidir. "ZORUNLU"
işaretli maddeler uygulanır; "opsiyonel" olanlar sprint sonuna bırakılabilir. Testler kullanıcı talimatıyla
**en sona** bırakılır (§10 → Dalga 3); zincire dokunan her şey `FakeChainGateway` ile yazılır, gerçek ağ doğrulaması
cüzdanlara MON geldikten sonra yapılır.

---

## 0. Özet: ne değişiyor, ne kalıyor

| Katman | v1-stellar | Monad |
|---|---|---|
| Zincir erişimi | `app/services/stellar/*` (`SorobanGateway`, `HorizonGateway`, `contract_abi`, `sep10`, `fake`) — **silinir** | `app/services/chain/*` (`ChainGateway` Protocol, `MonadGateway` web3.py v7, `FakeChainGateway`, ABI JSON) |
| İmzalama modeli | backend imzasız XDR üretir → cüzdan imzalar → backend yayınlar/poll eder | backend `{to, data, value, gas, chain_id}` üretir → cüzdan `eth_sendTransaction` → istemci hash bildirir → backend receipt doğrular (K3) |
| Token onayı | Stellar auth ağacı | ERC-20 `approve` pre-step, tam tutar (K4) |
| Giriş | SEP-10 + ed25519 nonce mesajı | SIWE (EIP-4361) + EIP-191 `recover_message` (K2) |
| Adres | `G…`/`C…` String(56), büyük-küçük harf duyarlı | `0x…` String(42), DB küçük harf, çıktı EIP-55 (K11) |
| Tutar | 7 ondalık, `Numeric(30,7)`, i128 | token `decimals`, `Numeric(78,18)`, uint256 (K12) |
| Olay akışı | `getEvents` cursor | `eth_getLogs` blok penceresi, `CONFIRMATIONS`, `last_block_hash`, `failed_events` |
| Anchor / SEP-24 | router + worker + 2 tablo | **silinir**; `POST /wallet/faucet` (K7) |
| Market candles | Horizon | **silinir** |
| Yayın | compose `mobilapp`, 8012, `mobilback.yolalapp.com` | compose `traderkirala`, 8013, `monadback.yolalapp.com` (K13) |

Zincirden bağımsız iş katmanı (ilanlar, teklifler, keşfet, mesajlar, bildirimler, puanlar, panel — ~%70) **korunur**;
yalnızca alan adları (`wallet_address`, `address`, `symbol`, `block_number`, `log_index`) ve tutar/adres yardımcıları değişir.

### 0.1 Eski → yeni ad eşlemesi (tüm ajanlar için hızlı tablo)

| Eski | Yeni | Nerede |
|---|---|---|
| `get_soroban()/set_soroban()`, `get_horizon()/set_horizon()` | `get_chain()/set_chain()` | `app/services/chain/__init__.py` |
| `SorobanDep`, `HorizonDep` | `ChainDep` | `app/api_deps.py` |
| `StellarError` (`stellar_error`, 502) | `ChainError` (`chain_error`, 502) | `app/core/errors.py` |
| `VaultContractError`, `VaultError` (1–21) | `ContractRevertError`, `VaultError` (1–30) | `app/services/chain/errors.py`, `abi.py` |
| `to_stroops/from_stroops` | `to_raw/from_raw` | `app/services/chain/amounts.py` |
| `is_valid_public_key`, `is_valid_contract_id` | `is_evm_address` | `app/services/chain/addresses.py` |
| `User.stellar_address` | `User.wallet_address` (küçük harf) | `app/models/user.py` |
| `Asset.contract_id / code / issuer / network` | `Asset.address / symbol / — / chain_id` | `app/models/asset.py` |
| `Trade.ledger / onchain_seq` | `Trade.block_number / log_index` | `app/models/trade.py` |
| `Agreement.last_event_ledger` | `Agreement.last_event_block_number` | `app/models/agreement.py` |
| `PendingTransaction.unsigned_xdr` | `to_address, calldata, value, gas, action, from_address` | `app/models/pending_transaction.py` |
| `PendingTxStatus.built / success` | `pending / confirmed` | `app/models/enums.py` |
| `IndexerState.cursor / ledger` | `last_block_hash / block_number` | `app/models/indexer_state.py` |
| `TokenClaims.public_key` | `TokenClaims.address` | `app/core/security.py` |
| `UnsignedTx.xdr/hash/network_passphrase` | `UnsignedTx.to/data/value/gas/chain_id/pre_steps` | `app/services/chain/types.py` |
| `EventRecord.ledger/event_index/topics_xdr/value_xdr` | `EventRecord.block_number/log_index/name/args` | `app/services/chain/types.py` |
| `TxResult` | `TxReceiptResult` | `app/services/chain/types.py` |
| `settings.vault_contract_id / stellar_network / …` | `settings.vault_address / chain_id / …` | `app/core/config.py` (§2) |

---

## 1. `app/services/chain/` paketi (Dalga 0 + Dalga 1a)

Yalnızca bu paket `web3`, `eth_account`, `eth_utils`, `hexbytes` import eder. Servisler ve router'lar zinciri
`ChainGateway` Protocol'ü üzerinden görür; testler `set_chain(FakeChainGateway())` enjekte eder.

```
app/services/chain/
├── __init__.py      get_chain() / set_chain() tembel singleton           (Dalga 0)
├── types.py         veri sınıfları                                          (Dalga 0)
├── errors.py        ChainError hiyerarşisi                                  (Dalga 0)
├── addresses.py     is_evm_address / normalize / checksum                   (Dalga 0)
├── amounts.py       to_raw / from_raw / settle_math (uint256)               (Dalga 0)
├── gateway.py       typing.Protocol ChainGateway (tüm imzalar)              (Dalga 1a)
├── abi.py           ABI yükleme, selector→VaultError, event decode          (Dalga 1a)
├── monad.py         MonadGateway (web3.py v7 AsyncWeb3)                     (Dalga 1a)
├── fake.py          FakeChainGateway (durum makinesi + calldata + receipt)  (Dalga 1a)
└── abi/
    ├── TraderVault.json   SC-12 export; gelene kadar 01-kontrat-spec'ten elle yazılmış geçici ABI
    ├── MockRouter.json
    ├── TestToken.json
    └── IERC20.json        approve/transfer/transferFrom/allowance/balanceOf/decimals/symbol + Transfer/Approval
```

### 1.1 `__init__.py` — tembel singleton (eski `get_soroban` deseni)

```python
"""Chain layer: lazy singleton for the Monad gateway. Only app.services.chain.* imports web3."""
from __future__ import annotations
from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    from app.services.chain.gateway import ChainGateway

_chain: Any | None = None

def get_chain() -> ChainGateway:
    global _chain
    if _chain is None:
        from app.core.config import get_settings
        from app.services.chain.monad import MonadGateway
        _chain = MonadGateway(get_settings())
    return _chain

def set_chain(gw: Any | None) -> None:
    """Test hook: inject a FakeChainGateway (None resets to the lazy default)."""
    global _chain
    _chain = gw

__all__ = ["get_chain", "set_chain"]
```

`app/api_deps.py`: `ChainDep = Depends(get_chain)`; router imzaları `chain=ChainDep`. Worker: `WorkerDeps.chain`.

### 1.2 `types.py` — veri sınıfları (ZORUNLU alanlar)

Tüm adresler **küçük harf** `str`; tutarlar `int` ham birim; zaman damgaları `int` unix saniye (kontrat) ya da
aware `datetime` (blok zamanı). `frozen=True` dataclass.

```python
@dataclass(frozen=True)
class Terms:                       # ITraderVault.Terms (01-spec §2.2)
    customer: str; trader: str; base_token: str
    principal: int; duration_seconds: int; commission_bps: int; max_drawdown_bps: int
    listing_ref: bytes             # 32 bayt
    def validate(self) -> None     # 01-spec §4.0 _validateTerms sırası; ihlalde ValidationError(code="invalid_terms")
    def as_abi_tuple(self) -> tuple  # (checksum(customer), checksum(trader), checksum(base_token), principal, duration, commission, dd, listing_ref)
    def as_dict(self) -> dict[str, Any]   # JSON-safe (listing_ref hex)

@dataclass(frozen=True)
class AgreementView:               # getAgreement() çıktısı, struct alan sırası 01-spec §2.2
    id: int; terms: Terms; status: int          # 0 None,1 Proposed,2 Funded,3 Active,4 Settled,5 Cancelled
    proposer: str; created_at: int; start_time: int; end_time: int; settled_at: int
    platform_fee_bps: int; tokens: list[str]
    final_value: int; trader_fee: int; platform_fee: int; customer_payout: int; last_value: int

@dataclass(frozen=True)
class ReservationView:
    id: int; customer: str; token: str; amount: int; original: int
    status: int                    # 0 None,1 Open,2 Released,3 Consumed
    created_at: int; listing_ref: bytes

@dataclass(frozen=True)
class ConfigView:
    owner: str; router: str; fee_recipient: str; platform_fee_bps: int; settle_slippage_bps: int
    paused: bool; pending_router: str | None; router_activation_time: int; router_delay: int

@dataclass(frozen=True)
class TokenInfoView:  allowed: bool = False; is_base: bool = False

@dataclass(frozen=True)
class TokenBalance:   token: str; raw: int; symbol: str | None = None; decimals: int | None = None

@dataclass(frozen=True)
class RouterQuote:    path: list[str]; amount_in: int; amounts: list[int]; source: str   # "router" | "fake"
    @property amount_out -> amounts[-1]

@dataclass(frozen=True)
class BlockInfo:      number: int; hash: str; parent_hash: str; timestamp: datetime

@dataclass(frozen=True)
class PreStep:                     # 02-api §2.2
    kind: str                      # "approve"
    to: str; data: str; value: int; gas: int | None
    description: str; spender: str; token: str; amount_raw: int

@dataclass(frozen=True)
class UnsignedTx:                  # 02-api §2.1 UnsignedTxOut'un zincir yarısı
    kind: str                      # PendingTxKind değeri
    action: str                    # ABI fonksiyon adı (camelCase) / "transfer"
    from_address: str; to: str; data: str; value: int; gas: int | None; chain_id: int
    summary: dict[str, Any]        # JSON-safe, ham birimler
    expires_at: datetime           # now + settings.pending_tx_ttl_seconds
    pre_steps: list[PreStep] = field(default_factory=list)

@dataclass(frozen=True)
class EventRecord:                 # eth_getLogs ya da receipt.logs'tan decode edilmiş TEK vault olayı
    block_number: int; block_hash: str; log_index: int; tx_hash: str; tx_index: int
    address: str                   # yayan kontrat (vault), küçük harf
    name: str                      # Solidity event adı: "Opened", "Traded", …
    args: dict[str, Any]           # ABI adları (camelCase) → int / str(küçük harf adres) / bytes / bool
    topic0: str
    block_timestamp: datetime | None = None
    removed: bool = False

@dataclass(frozen=True)
class RevertInfo:
    selector: str | None; name: str | None; code: int | None   # VaultError kodu (1–30) ya da None
    args: dict[str, Any]; message: str                         # insan okunur; error_code üretimi §4.4

@dataclass(frozen=True)
class TxInfo:                      # eth_getTransactionByHash (receipt olmadan da var olabilir)
    tx_hash: str; from_address: str; to_address: str | None; input: str; value: int
    nonce: int; gas: int; block_number: int | None; block_hash: str | None

@dataclass(frozen=True)
class TxReceiptResult:
    tx_hash: str; status: int      # 1 başarılı, 0 revert
    block_number: int; block_hash: str; block_timestamp: datetime | None
    from_address: str; to_address: str | None; input: str; value: int
    gas_used: int; gas_limit: int; effective_gas_price: int
    events: list[EventRecord]      # yalnız vault adresinden decode edilebilen loglar
    raw_log_count: int
    confirmations: int             # latest_block - block_number + 1 (okuma anında)
    revert: RevertInfo | None = None
    @property ok -> status == 1

@dataclass(frozen=True)
class SettlePreview:               # previewSettle() (opsiyonel view; ABI'de yoksa None)
    tokens: list[str]; balances: list[int]; quotes: list[int]
    estimated_final_value: int; drawdown_floor: int; keeper_floor: int
```

### 1.3 `errors.py` — hata hiyerarşisi

`app/core/errors.py`'de (Dalga 0) `StellarError` **silinir**, yerine:

```python
class ChainError(AppError):
    """RPC erişilemiyor / zaman aşımı / beklenmeyen cevap."""
    status_code = 502; code = "chain_error"
```

`app/services/chain/errors.py`:

| Sınıf | Taban | HTTP / code | Ne zaman |
|---|---|---|---|
| `ChainUnavailableError` | `ChainError` | 502 `chain_error` | bağlantı/zaman aşımı, 3 deneme sonrası; `details={"rpc":…,"attempts":3}` |
| `RpcRateLimitedError` | `ChainError` | 502 `chain_error` | RPC 429 / `-32005`; backoff sonrası hâlâ |
| `ContractRevertError` | `AppError` | 400 `contract_error` | `estimateGas`/`eth_call` revert; `error: VaultError \| None`, `revert: RevertInfo`, `error_code` ("vault:NotFound", "erc20:…", "reverted"); `details={"error_code":…, "contract_error_code":…}` |
| `TxNotFoundError` | `ChainError` | 404 `tx_not_found` | hash ağda bilinmiyor (yalnız iç kullanım; API'de `not_included`) |
| `InvalidAddressError` | `ValidationError` | 422 `invalid_address` | 0x adres değil / checksum bozuk |
| `AmountError` | `ValidationError` | 422 `invalid_amount` / `too_many_decimals` | `to_raw` hatası |
| `ReceiptMismatchError` | `ConflictError` | 409 `receipt_mismatch` | tx_submit doğrulaması (§4.3) |
| `FaucetDisabledError` | `AppError` | 503 `faucet_disabled` | `minter_private_key` yok |

`VaultError` (IntEnum, `abi.py`) kodları 01-spec §5 tablosuyla **birebir** (1 NotInitialized … 21 ReservationMismatch,
22 ZeroAmount, 23 ZeroAddress, 24 InvalidRouter, 25 RouterChangeNotReady, 26 NoPendingRouterChange, 27 InvalidToken,
28 Reentrancy, 29 TransferFailed, 30 UpgradeError). `ERROR_MESSAGES: dict[VaultError, str]` Türkçe tek cümle (bildirim
metinleri). Servisler `ContractRevertError` yakalayıp 02-api §3.1'e göre `ChainError("…", details={"error_code": e.error_code})`
ya da doğrudan hatayı geçirir (gövde `{code:"contract_error", details.error_code:"vault:…"}`).

### 1.4 `addresses.py`

```python
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
ZERO_ADDRESS = "0x" + "00" * 20
def is_evm_address(value: object) -> bool          # regex + karışık harfse eth_utils.is_checksum_address
def normalize(value: str) -> str                   # strip; is_evm_address değilse InvalidAddressError; küçük harf döner
def checksum(value: str) -> str                    # eth_utils.to_checksum_address (API çıktısı, SIWE mesajı, ABI encode)
def same(a: str | None, b: str | None) -> bool     # normalize eşitliği (None güvenli)
def short(value: str) -> str                       # "0x67aD…FF19" (log/bildirim)
```
Kural (K11): DB'ye yazılan her adres `normalize()`; API'ye çıkan her adres `checksum()`; karşılaştırma `same()`.
Pydantic için `app/schemas/common.py` `Address = Annotated[str, AfterValidator(normalize)]` (girişte küçük harfe çevirir)
ve `AddressOut = Annotated[str, PlainSerializer(checksum)]` (çıkışta checksum) tanımlar (Dalga 0).

### 1.5 `amounts.py` — `app/services/amounts.py`'nin yerine

```python
UINT256_MAX = 2**256 - 1; BPS_DENOM = 10_000; ZERO = Decimal(0); MAX_DECIMALS = 18
def unit(decimals) -> Decimal                       # Decimal(1).scaleb(-decimals)
def quantize(amount, decimals) -> Decimal           # ROUND_DOWN
def to_raw(amount: Decimal|int|str, decimals: int) -> int
    # strict: sonlu, ondalık fazlası -> AmountError("too_many_decimals"), 0 <= raw <= UINT256_MAX değilse AmountError
def from_raw(raw: int, decimals: int) -> Decimal    # exactly `decimals` places
def parse_amount(value: str|Decimal, decimals: int) -> Decimal   # API girişi: > 0, en fazla `decimals` basamak
def format_amount(amount: Decimal, decimals: int | None = None) -> str   # üssüz, sondaki sıfırlar atılır ("1000", "0.000001")
def bps_floor(raw: int, bps: int) -> int
def min_out_for_slippage(quoted_out: int, slippage_bps: int) -> int
def drawdown_floor(principal: int, max_drawdown_bps: int) -> int
def settle_math(final_value, principal, commission_bps, platform_fee_bps) -> Settlement   # uint256 sınırı, SettleMath.sol ile aynı
def wei_to_mon(wei: int) -> Decimal; def mon_to_wei(amount) -> int   # from_raw/to_raw(…, 18) kısayolları
```
i128 sabitleri, `STROOP`, `DEFAULT_DECIMALS` **yoktur**; `decimals` her çağrıda açık verilir (varsayılan yok — 7 ondalık
varsayımı geri sızmasın). Geçiş: Dalga 0'da `app/services/amounts.py` şu tek satıra iner:
`from app.services.chain.amounts import *  # noqa: F401,F403 — geçici köprü; Dalga 3(i) siler`. Eski `to_stroops/from_stroops`
adları **yoktur**; Dalga 2 ajanları kendi dosyalarındaki çağrıları `to_raw/from_raw(…, asset.decimals)` yapar.

### 1.6 `gateway.py` — `ChainGateway` Protocol (TÜM imzalar, ZORUNLU)

```python
class ChainGateway(Protocol):
    kind: str                        # "monad" | "fake" (QuoteOut.source, loglar)
    chain_id_expected: int           # settings.chain_id
    vault_address: str; router_address: str        # küçük harf

    # --- ağ ---
    async def chain_id(self) -> int
    async def latest_block(self) -> int
    async def get_block(self, number: int | str) -> BlockInfo          # "latest" kabul eder
    async def close(self) -> None

    # --- vault okumaları (eth_call; NotFound -> ContractRevertError(VaultError.NotFound)) ---
    async def read_agreement(self, agreement_id: int) -> AgreementView
    async def read_balances(self, agreement_id: int) -> list[tuple[str, int]]      # getBalances → [(token, raw)] tokens sırası
    async def read_value_in_base(self, agreement_id: int) -> int
    async def read_config(self) -> ConfigView                                       # getConfig() + owner()
    async def read_reservation(self, reservation_id: int) -> ReservationView
    async def preview_settle(self, agreement_id: int) -> SettlePreview | None       # ABI'de yoksa None
    async def is_token_allowed(self, token: str) -> TokenInfoView
    async def next_id(self) -> int
    async def next_reservation_id(self) -> int

    # --- router / token okumaları ---
    async def quote(self, path: list[str], amount_raw: int) -> RouterQuote          # getAmountsOut; revert -> ChainError(code="quote_failed")
    async def token_balance(self, token: str, holder: str) -> int
    async def native_balance(self, address: str) -> int                            # wei
    async def allowance(self, token: str, owner: str, spender: str) -> int
    async def token_metadata(self, token: str) -> tuple[str, int]                   # (symbol, decimals)

    # --- işlem üreticileri (calldata + estimate_gas×1.2; revert -> ContractRevertError) ---
    async def build_approve(self, owner: str, token: str, spender: str, amount_raw: int) -> PreStep
    async def build_reserve(self, customer: str, token: str, amount_raw: int, listing_ref: bytes) -> UnsignedTx
    async def build_release(self, customer: str, reservation_id: int, amount_raw: int) -> UnsignedTx   # amount_raw > 0
    async def build_release_all(self, customer: str, reservation_id: int) -> UnsignedTx
    async def build_propose(self, trader: str, terms: Terms) -> UnsignedTx
    async def build_open(self, customer: str, terms: Terms) -> UnsignedTx
    async def build_open_reserved(self, customer: str, terms: Terms, reservation_id: int) -> UnsignedTx
    async def build_fund(self, customer: str, agreement_id: int) -> UnsignedTx
    async def build_fund_reserved(self, customer: str, agreement_id: int, reservation_id: int) -> UnsignedTx
    async def build_accept(self, trader: str, agreement_id: int) -> UnsignedTx
    async def build_cancel(self, who: str, agreement_id: int) -> UnsignedTx
    async def build_trade(self, trader: str, agreement_id: int, token_in: str, token_out: str,
                          amount_in: int, min_out: int, deadline: int) -> UnsignedTx
    async def build_settle(self, caller: str, agreement_id: int, min_outs: list[int]) -> UnsignedTx
    async def build_claim(self, customer: str, agreement_id: int, token: str) -> UnsignedTx
    async def build_transfer(self, sender: str, to: str, amount_wei: int) -> UnsignedTx                  # native MON, data "0x"
    async def build_token_transfer(self, sender: str, token: str, to: str, amount_raw: int) -> UnsignedTx
    async def build_admin_set_token(self, owner: str, token: str, allowed: bool, is_base: bool) -> UnsignedTx
    async def build_admin_set_paused(self, owner: str, paused: bool) -> UnsignedTx
    async def build_admin_set_fees(self, owner: str, platform_fee_bps: int, fee_recipient: str) -> UnsignedTx
    async def build_admin_propose_router(self, owner: str, router: str) -> UnsignedTx
    async def build_admin_apply_router(self, owner: str) -> UnsignedTx
    async def build_admin_cancel_router(self, owner: str) -> UnsignedTx
    async def build_admin_set_settle_slippage(self, owner: str, bps: int) -> UnsignedTx

    # --- işlem / receipt / log ---
    async def estimate_gas(self, sender: str, to: str, data: str, value: int = 0) -> int   # ham tahmin (×1.2 build_* içinde)
    async def get_transaction(self, tx_hash: str) -> TxInfo | None
    async def get_receipt(self, tx_hash: str) -> TxReceiptResult | None                  # None = henüz blokta değil / bilinmiyor
    async def explain_failure(self, receipt: TxReceiptResult) -> RevertInfo               # status 0: eth_call replay -> decode_revert
    async def get_logs(self, from_block: int, to_block: int, address: str | None = None,
                       topics: list[Any] | None = None) -> list[EventRecord]               # address None -> vault
    async def faucet_mint(self, minter_key: str, token: str, to: str, amount_raw: int) -> str   # TestToken.mint; tx_hash
```

Yardımcı (Protocol dışı, `types.py`): `UnsignedTx` nesnesinden `UnsignedTxOut` alanlarını üretme servis tarafındadır (`agreements.unsigned_out`).

### 1.7 `abi.py`

- `load_abi(name) -> list[dict]` (`abi/<name>.json`, `functools.lru_cache`).
- `vault_contract(w3, address)`, `erc20_contract(w3, address)`, `router_contract(w3, address)`, `test_token_contract(w3, address)`.
- **Encode:** `encode_call(abi_name: str, fn: str, args: list) -> str` — web3 v7 `Contract.encode_abi(fn, args=…)` üzerinden;
  adres argümanları `checksum()` ile geçirilir (web3 küçük harf adres kabul etmez), `bytes32` argümanları `bytes`.
  `TERMS_COMPONENTS` sırası `Terms.as_abi_tuple()` ile aynı.
- **Selector tablosu:** `ERROR_SELECTORS: dict[str, tuple[str, int | None, list[type]]]` = `keccak(signature)[:4]` →
  (ad, `VaultError` kodu, arg tipleri). Tablo 01-spec §5'ten üretilir: ABI'deki her `error` girdisi + OZ hataları
  (`OwnableUnauthorizedAccount(address)`→2, `OwnableInvalidOwner(address)`→23, `InvalidInitialization()`/`NotInitializing()`→1,
  `ReentrancyGuardReentrantCall()`→28, `SafeERC20FailedOperation(address)`→29, UUPS/ERC1967/`AddressEmptyCode`/`FailedCall`→30,
  `ERC20InsufficientAllowance(address,uint256,uint256)`, `ERC20InsufficientBalance(address,uint256,uint256)`,
  `ERC20InvalidReceiver(address)`, `ERC20InvalidSender(address)` → kod None, ad `erc20:<Ad>`), ayrıca `Error(string)` (0x08c379a0)
  ve `Panic(uint256)` (0x4e487b71; `0x11` → Overflow(14), diğerleri kod None). Router hataları (`NoPrice`, `InvalidPath`,
  `InsufficientOutput`, `DeadlineExpired`, `InvalidAmount` — MockRouter) `RouterError(bytes)` içinden ikinci kademe decode edilir.
- `decode_revert(data: bytes | str | None) -> RevertInfo`: boş → `{name:None, code:None, message:"işlem geri alındı"}`;
  `RouterError(bytes reason)` → iç `reason` tekrar decode edilir, `message="RouterError: <iç ad>"`.
- `error_code_of(info: RevertInfo) -> str`: `vault:<Ad>` (kodu olan), `erc20:<Ad>`, `panic:<hex>`, `reverted`.
- **Event tablosu:** `EVENT_TOPICS: dict[str, dict]` topic0 → ABI event girdisi (TraderVault + OZ `Upgraded`,
  `OwnershipTransferStarted`, `OwnershipTransferred`, `Initialized`). `decode_log(log: dict) -> EventRecord | None`
  (bilinmeyen topic0 → None); `args` içinde adresler küçük harf, `bytes32` → `bytes`, `uint` → `int`, `bool`.
  `decode_receipt_logs(receipt_logs, vault) -> list[EventRecord]` yalnız `address == vault` olanlar.
- **Sabitler:** `MAX_TOKENS=6, MIN_DURATION=86_400, MAX_DURATION=94_608_000, MAX_COMMISSION_BPS=5_000,
  MAX_PLATFORM_FEE_BPS=1_000, MIN_DRAWDOWN_BPS=100, MAX_SETTLE_SLIPPAGE_BPS=5_000, KEEPER_GRACE=604_800, BPS_DENOM=10_000`,
  `CONFIG_KEYS = {"router","fees","paused","slippage"}` (bytes32 sağa sıfır dolgulu ASCII decode için `config_key_of(b32) -> str`).
- **Geçici ABI (ZORUNLU, Dalga 1a):** `abi/TraderVault.json` 01-spec §2 (struct/enum), §4 (fonksiyon imzaları), §5 (error),
  §6 (event) tablolarından elle yazılır; `tests/chain/test_abi.py` selector'ları sabitler
  (`Unauthorized()`=`0x82b42900`, `Paused()`=`0x9e87fac8`, `NotFound()`=`0xc5723b51`, `ZeroAmount()`=`0x1f2a2005`,
  `Error(string)`=`0x08c379a0`, `Panic(uint256)`=`0x4e487b71`; kalanları test `keccak` ile üretir ve tabloyla karşılaştırır).
  SC-12 `export-abi.sh` gerçek ABI'yi aynı dosya adına yazar; test seti farkı yakalar.

### 1.8 `monad.py` — `MonadGateway` (web3.py v7)

```python
class MonadGateway:
    kind = "monad"
    def __init__(self, settings: Settings) -> None:
        self.rpc_url = settings.rpc_url
        self.chain_id_expected = settings.chain_id
        self.vault_address = normalize(settings.vault_address) if settings.vault_address else ""
        self.router_address = normalize(settings.router_address) if settings.router_address else ""
        self.confirmations = settings.confirmations
        self.gas_margin = Decimal("1.2")
        self._w3 = AsyncWeb3(AsyncHTTPProvider(self.rpc_url, request_kwargs={"timeout": settings.rpc_timeout_seconds}))
        self._limiter = TokenBucket(rate_per_second=settings.rpc_max_rps, burst=settings.rpc_max_rps)
        self._block_cache: LRU[int, BlockInfo] (256)
```

Kurallar (ZORUNLU):
1. **Her RPC çağrısı** `_call(coro_factory)` sarmalayıcısından geçer: token bucket bekletir (`asyncio.Lock` + `monotonic`),
   hata sınıflandırması: `aiohttp.ClientError`/`asyncio.TimeoutError`/HTTP 5xx/429/`-32005`/`-32603 "too many requests"` →
   backoff `0.5, 1, 2` sn, 3 deneme → `ChainUnavailableError`/`RpcRateLimitedError`. `ContractCustomError`/`ContractLogicError`
   → `decode_revert(e.data)` → `ContractRevertError` (tekrar denenmez). Varsayılan `rpc_max_rps=12` (süreç başına; API 2 uvicorn
   worker + 1 worker süreci = 36 rps < 50; `eth_call`/`estimateGas` 25 rps sınırı için ayrıca `rpc_call_max_rps=8`).
2. `estimate_gas` sonucu `int(est * 1.2)`; Monad `gas_limit` üzerinden ücretlendirir (06-doküman), daha fazla şişirilmez.
   `build_*` içinde `estimate=True` varsayılan; revert olursa `ContractRevertError` (servis 502 `chain_error` ya da 400 `contract_error`
   döndürür, §6.1). Build sırasında `estimateGas` için `from` = çağıran; approve gerektiren `open/fund/reserve`'de allowance yoksa
   `estimateGas` `SafeERC20FailedOperation` ile revert eder → build_* bu durumda **tahmin yerine sabit tavan** kullanır
   (`GAS_FALLBACK = {"open": 320_000, "fund": 260_000, "reserve": 200_000, "openReserved": 300_000, "fundReserved": 240_000}`) ve
   `summary["gas_estimated"]=False`; pre_step'ler onaylandıktan sonra cüzdan zaten kendi tahminini yapar.
3. Okumalar: `contract.functions.<fn>(...).call()`; tuple/struct → `types.py` sınıflarına konumsal eşleme (ABI output
   `components` sırası 01-spec §2.2). `read_config` = `getConfig()` + `owner()` (iki çağrı, 60 sn önbellek `routers/config.py`'de).
4. `quote(path, amount)` = `router.getAmountsOut(amount, [checksum(p)…])`; revert (`NoPrice` vb.) → `ChainError(code="quote_failed")`
   (mevcut çağıranlar bu kodu bekliyor: `trading.compute_quote`, `agreements.settle_min_outs`).
5. `get_receipt`: `eth_getTransactionReceipt` (None → None) + `eth_getTransactionByHash` (input/value/gas için) + blok zamanı
   (`_block_cache`) + `latest_block` (confirmations) → `TxReceiptResult`; loglar `abi.decode_receipt_logs(logs, vault)`.
   `status==0` ise `explain_failure` çağrılmaz (çağıran karar verir; §4.3 adım 7).
6. `explain_failure(receipt)`: `eth_call({from,to,data,value}, block_number)` → revert verisi → `decode_revert`;
   `gas_used == gas_limit` ve veri yoksa `RevertInfo(name="OutOfGas", message=…, code=None)` → `error_code="out_of_gas"`.
7. `get_logs(from, to, address, topics)`: `eth_getLogs`; `-32602/-32005 "range too large"/"limit exceeded"` hatasında pencere
   ikiye bölünür (özyinelemeli, alt sınır 50 blok), sonuç `decode_log` ile `EventRecord`; `block_timestamp` `_block_cache` üzerinden
   doldurulur (farklı blok başına 1 `eth_getBlockByNumber`).
8. `faucet_mint(minter_key, token, to, amount_raw)`: `LocalAccount = Account.from_key(minter_key)`; süreç içi `asyncio.Lock`;
   `nonce = get_transaction_count(minter, "pending")`; tip-2 alanlar (`maxFeePerGas = base_fee*2 + tip`, `maxPriorityFeePerGas =
   eth_maxPriorityFeePerGas`; başarısızsa legacy `gasPrice`); `gas = estimate×1.2`; `sign_transaction` + `send_raw_transaction`;
   "nonce too low" / "replacement transaction underpriced" → nonce yenile, **bir kez** tekrar; döner `tx_hash`.
9. `build_transfer` (native): `to=alıcı, data="0x", value=wei, gas=21_000` (estimate gerekmez); `build_token_transfer`:
   `to=token, data=transfer(to, amount)`.
10. Monad PoA değildir; `ExtraDataToPOAMiddleware` **eklenmez**. `extraData` hatası görülürse (`06`'da yok) o zaman eklenir — not olarak.
11. `close()` provider'ın aiohttp oturumunu kapatır (`await self._w3.provider.disconnect()`).

### 1.9 `fake.py` — `FakeChainGateway`

Eski `fake.py:344-585` durum makinesi **taşınır**, Solidity semantiğine (01-spec §4) çekilir ve eksikler eklenir:

- **State:** `tokens: dict[addr, _Token{allowed, is_base, decimals, symbol}]`, `agreements: dict[int, _Agreement]` (mutable
  kopyası `AgreementView`), `balances[(id, token)]`, `reservations: dict[int, _Reservation]`, `wallets[(addr, token)]`,
  `allowances[(owner, token, spender)]`, `native[addr]` (wei), `config`, `owner`, `next_id=1`, `next_reservation_id=1`,
  `now`, `block_number`, `blocks: list[BlockInfo]` (hash = keccak(number, parent)), `txs: dict[hash, TxInfo]`,
  `receipts: dict[hash, TxReceiptResult]`, `logs: list[EventRecord]`, `queue: list[_PendingTx]` (auto_mine=False iken),
  `calls: list[tuple[str, dict]]` (denetim izi), `prices[(in, out)] = Fraction`, `strict_router=True`.
- **Varsayılan kurulum** (`install_defaults`): `FAKE_VAULT="0x00…0v1"`, `FAKE_ROUTER`, `FAKE_OWNER`, `TUSDC (6, base)`,
  `TWETH (18)`, `TWBTC (8)`, fiyatlar 01-spec §9.1 (1 WETH = 3000 USDC, 1 WBTC = 60000 USDC, WETH/WBTC 20:1),
  `platform_fee_bps=100`, `settle_slippage_bps=100`, `router_delay=600`, `now=1_800_000_000`, `block_number=1000`.
  Sabitler `FAKE_TUSDC/FAKE_TWETH/FAKE_TWBTC` `scripts/seed_assets.py` test JSON'uyla aynıdır (`tests/conftest.py`).
- **Durum makinesi** (`_do_*`, hepsi `(state, sender, …)` alır, `Solidity sırasıyla` kontrol eder, `VaultError` ile
  `ContractRevertError` fırlatır, `list[EventRecord-benzeri (name, args)]` döner): `_do_reserve`, `_do_release`,
  `_do_release_all`, `_do_propose`, `_do_open`, `_do_open_reserved`, `_do_fund`, `_do_fund_reserved`, `_do_accept`,
  `_do_cancel`, `_do_trade`, `_do_settle` (üç kademe + `lastValue` tabanı + in-kind dust + fee try), `_do_claim`,
  `_do_set_token`, `_do_set_paused`, `_do_set_fees`, `_do_propose_router/_apply_router/_cancel_router`, `_do_set_settle_slippage`,
  ERC-20: `_do_approve`, `_do_transfer`, `_do_transfer_from` (allowance yoksa `SafeERC20FailedOperation`→29), `_do_mint`
  (minter allow-list), native transfer (`data=="0x"`). **Yeni**: `open/fund/reserve` cüzdandan `transferFrom` çeker → allowance
  ve bakiye şart (K4 testi). `release(0)` → `ZeroAmount`; `releaseAll` ayrı. Enum değerleri `None=0` kaydırmalı.
- **Build:** her `build_*` gerçek calldata üretir (`abi.encode_call`), `estimate=True` ise dry-run (deepcopy üzerinde `_do_*`) —
  başarısızsa `ContractRevertError` (gerçek `estimateGas` revert'i gibi), gas sabit `GAS_TABLE[action]`; `pre_steps` için
  `allowance` bakılır (`build_approve`). `UnsignedTx.expires_at = now_utc() + 900 s`.
- **"Gönder" simülasyonu (cüzdanın yerine, testler kullanır):**
  `send(sender: str, to: str, data: str, value: int = 0) -> str` → `tx_hash = keccak(sender, nonce, to, data, value)`;
  `_dispatch(to, data)`: selector → (`vault` ise `_do_*`, token ise ERC-20/mint, `data=="0x"` ise native). `auto_mine=True`
  (varsayılan) → hemen `mine()`; değilse kuyruğa girer (tx `get_transaction` ile görünür, `get_receipt` None → "submitted" testleri).
- `mine(pending_tx=None) -> TxReceiptResult`: blok numarasını 1 artırır, yeni `BlockInfo`, kuyruktaki (ya da verilen) işlemleri
  sırayla uygular; başarı → `status=1` + `EventRecord`'lar (`log_index` blok içinde 0'dan; `name/args` `abi.decode_log` ile aynı
  şekil, çünkü fake event'leri **ABI ile encode edip tekrar decode eder** — üretim decode yolu test edilir); revert →
  `status=0`, `revert=RevertInfo`. `get_receipt` sonucu `confirmations = block_number_latest - block + 1`.
- `advance(seconds)`, `set_time`, `set_token`, `set_paused`, `set_fees`, `set_settle_slippage`, `set_price(in, out, price,
  both_ways=True)`, `remove_route`, `set_strict_router(bool)`, `fund_wallet(addr, token, raw)`, `fund_native(addr, wei)`,
  `approve(owner, token, spender, raw)` (kısa yol), `reorg(depth: int)` (son `depth` bloğun receipt/loglarını siler, blok hash'lerini
  değiştirir; indexer reorg testi), `reset()`.
- `get_logs(from, to, address, topics)`: `logs` listesinden aralık filtresi; `latest_block`; `get_block`; `faucet_mint` → `_do_mint`
  (minter_key'den adres türetmek yerine `minter_address` parametresiyle kurulur; `settings.minter_private_key` fake'te yorumlanmaz).
- `read_*`/`quote`/`token_balance`/`native_balance`/`allowance`/`token_metadata`/`explain_failure`/`estimate_gas` Protocol'e uygun.

---

## 2. Settings (`app/core/config.py`, Dalga 1b)

### 2.1 Kaldırılan alanlar
`sep10_server_secret, sep10_challenge_timeout, home_domain, web_auth_domain, stellar_network, horizon_url, soroban_rpc_url,
friendbot_url, platform_secret, pool_key_encryption_key, base_fee_stroops, tx_timeout_seconds, default_slippage_pct,
max_slippage_pct, deposit_ttl_minutes, platform_fee_pct, max_profit_share_pct, vault_contract_id, soroswap_router_id,
soroswap_api_url, soroswap_api_key, anchor_enabled, anchor_home_domain, anchor_assets, anchor_lang, anchor_sync_seconds,
worker_deposit_poll_seconds, worker_nav_snapshot_seconds, worker_withdrawal_poll_seconds` ve türetilmiş
`network_passphrase, effective_horizon_url, effective_rpc_url, effective_friendbot_url, is_testnet,
effective_soroswap_router_id, anchor_asset_list`; modül sabitleri `TESTNET_PASSPHRASE, PUBLIC_PASSPHRASE, SOROSWAP_ROUTER_*`.

### 2.2 Yeni / değişen alanlar (env adı = alan adının büyük harfi)

| Alan | Tip / varsayılan | Not |
|---|---|---|
| `app_name` | `"TraderKirala API"` | |
| `database_url` | `postgresql+asyncpg://trader:trader@db:5432/traderkirala` | |
| `chain_id` | `int = 10143` | `MONAD_TESTNET_CHAIN_ID = 10143`, `MONAD_MAINNET_CHAIN_ID = 143` sabitleri |
| `chain_name` | `"Monad Testnet"` | `/config.chain.name` |
| `rpc_url` | `"https://testnet-rpc.monad.xyz"` | |
| `ws_url` | `str \| None = None` | `/config.chain.ws_url` |
| `explorer_url` | `"https://testnet.monadvision.com"` | 06-doküman (02-api'deki `monadexplorer` varsayımı kapanır) |
| `faucet_url` | `"https://faucet.monad.xyz"` | MON faucet linki |
| `native_symbol` / `native_decimals` | `"MON"` / `18` | |
| `vault_address` | `str \| None = None` | proxy adresi; `None` → indexer/reconciler atlar, build 503 `contract_not_configured` |
| `router_address` | `str \| None = None` | MockRouter |
| `multicall3_address` | `"0xcA11bde05977b3631167028862bE2a173976CA11"` | opsiyonel bakiye toplu okuması |
| `confirmations` | `int = 2` | indexer güvenli baş derinliği |
| `indexer_block_window` | `int = 2000` | `eth_getLogs` penceresi |
| `indexer_start_block` | `int \| None = None` | None → `deployments/monad-testnet.json.blockNumber` → o da yoksa `latest - 5000` |
| `indexer_max_windows_per_run` | `int = 10` | tick başına en fazla 20k blok |
| `indexer_poll_seconds` | `int = 5` | korunur |
| `pending_tracker_seconds` | `int = 5` | yeni worker job'u |
| `submitted_tx_timeout_minutes` | `int = 30` | `submitted` → `failed(not_included)` |
| `reconcile_seconds` | `int = 60` | korunur |
| `platform_address` | `str \| None = None` | fee recipient varsayılanı (`set_fees`); `PLATFORM_SECRET` yerine |
| `minter_private_key` | `str \| None = None` | faucet; yoksa `/wallet/faucet` 503 `faucet_disabled`; loglanmaz |
| `faucet_amounts` | `dict[str, str] = {"tUSDC": "1000", "tWETH": "0.5", "tWBTC": "0.02"}` | env'de JSON; symbol → insan okunur tutar |
| `faucet_daily_limit` | `int = 1` | kullanıcı+varlık başına gün |
| `siwe_domain` | `"monadback.yolalapp.com"` | JWT `iss` de bu |
| `siwe_uri` | `"https://monadback.yolalapp.com"` | |
| `siwe_statement` | `"TraderKirala'ya giriş yap. Bu imza işlem yapmaz, gas harcamaz."` | |
| `auth_nonce_ttl_seconds` | `int = 300` | korunur |
| `auth_nonce_rate_limit_per_minute` | `int = 20` | IP başına |
| `access_token_ttl_seconds` | `int = 604_800` | korunur |
| `jwt_absolute_ttl_days` | `int = 30` | `refresh_max_age_seconds` property = `days*86400` |
| `pending_tx_ttl_seconds` | `int = 900` | `UnsignedTx.expires_at` |
| `tx_submit_timeout_seconds` | `int = 20` | receipt bekleme (eski 60) |
| `settle_slippage_bps` / `default_trade_slippage_bps` | `100` / `100` | korunur |
| `default_base_asset_code` | `"tUSDC"` | symbol |
| `rpc_timeout_seconds` / `rpc_max_rps` / `rpc_call_max_rps` / `rpc_retries` | `10` / `12` / `8` / `3` | §1.8 |
| `deployments_file` | `"deployments/monad-testnet.json"` | backend köküne göre; `seed_assets`, `indexer_start_block` |
| `assets_json` | `str \| None = None` | deploy JSON yoksa `[{symbol,address,decimals,isBase}]` |
| `usd_prices_json` | `str \| None = None` | fx indikatif fiyat ezme; varsayılan `{"tUSDC":"1","tWETH":"3000","tWBTC":"60000"}` |
| `mon_usd_price` | `Decimal \| None = None` | BE-14: sabit ya da kapalı |
| `cors_origins` | `["*"]` (dev) | prod `.env`'de daraltılır (BE-25) |

Doğrulayıcılar: `vault_address/router_address/platform_address/multicall3_address` `normalize()` ile küçük harfe çevrilir
(`field_validator(mode="before")`); `minter_private_key` `0x`+64 hex değilse `ValueError`; `faucet_amounts` string→dict JSON.
`Settings.__repr__`/log satırlarında `minter_private_key`, `jwt_secret`, `admin_key` asla yazılmaz.

### 2.3 `.env.example` (tam metin)

```
# ---- TraderKirala backend (Monad Testnet) — copy to .env and fill; scripts/gen_env.py generates secrets ----
APP_ENV=prod                       # dev | test | prod
LOG_LEVEL=INFO
DOCS_ENABLED=true                  # prod: false
CORS_ORIGINS=*                     # prod: https://app.traderkirala.com,https://traderkirala.vercel.app
LEGAL_CONTACT_EMAIL=

POSTGRES_PASSWORD=change-me
# DATABASE_URL is set by docker-compose; for local runs:
# DATABASE_URL=postgresql+asyncpg://trader:trader@127.0.0.1:5434/traderkirala

JWT_SECRET=change-me-at-least-32-chars
ADMIN_KEY=change-me-admin-key

# ---- chain ----
CHAIN_ID=10143
CHAIN_NAME=Monad Testnet
RPC_URL=https://testnet-rpc.monad.xyz
WS_URL=
EXPLORER_URL=https://testnet.monadvision.com
FAUCET_URL=https://faucet.monad.xyz
RPC_MAX_RPS=12
RPC_CALL_MAX_RPS=8

# ---- contracts (deployments/monad-testnet.json is the source of truth; these must match) ----
VAULT_ADDRESS=0x0000000000000000000000000000000000000000
ROUTER_ADDRESS=0x0000000000000000000000000000000000000000
PLATFORM_ADDRESS=0x67aD0CaE18528017B79E0aF8C4D3dd5937caFF19
DEFAULT_BASE_ASSET_CODE=tUSDC
CONFIRMATIONS=2
INDEXER_BLOCK_WINDOW=2000
INDEXER_START_BLOCK=                # empty -> deployments JSON blockNumber
DEPLOYMENTS_FILE=deployments/monad-testnet.json
# ASSETS_JSON=[{"symbol":"tUSDC","address":"0x..","decimals":6,"isBase":true}]   # fallback when the JSON file is absent

# ---- faucet (K7 / OPS-11): a low-privilege key that only holds TestToken MINTER_ROLE ----
MINTER_PRIVATE_KEY=                 # empty -> /wallet/faucet returns 503 faucet_disabled
FAUCET_AMOUNTS={"tUSDC":"1000","tWETH":"0.5","tWBTC":"0.02"}
FAUCET_DAILY_LIMIT=1

# ---- auth (SIWE) ----
SIWE_DOMAIN=monadback.yolalapp.com
SIWE_URI=https://monadback.yolalapp.com
SIWE_STATEMENT=TraderKirala'ya giriş yap. Bu imza işlem yapmaz, gas harcamaz.
AUTH_NONCE_TTL_SECONDS=300
AUTH_NONCE_RATE_LIMIT_PER_MINUTE=20
ACCESS_TOKEN_TTL_SECONDS=604800
JWT_ABSOLUTE_TTL_DAYS=30

# ---- transactions ----
PENDING_TX_TTL_SECONDS=900
TX_SUBMIT_TIMEOUT_SECONDS=20
SUBMITTED_TX_TIMEOUT_MINUTES=30
SETTLE_SLIPPAGE_BPS=100
DEFAULT_TRADE_SLIPPAGE_BPS=100

# ---- worker intervals (seconds) ----
INDEXER_POLL_SECONDS=5
PENDING_TRACKER_SECONDS=5
RECONCILE_SECONDS=60
WORKER_OFFER_EXPIRY_SECONDS=60
FX_CACHE_SECONDS=600
# USD_PRICES_JSON={"tUSDC":"1","tWETH":"3000","tWBTC":"60000"}
# MON_USD_PRICE=

# ---- Expo push (optional) ----
EXPO_PUSH_ENABLED=false
EXPO_ACCESS_TOKEN=
```

`scripts/gen_env.py` (Dalga 1b): `stellar_sdk` importu kalkar; `--domain monadback.yolalapp.com`, `--platform-address`,
`--vault`, `--router` argümanları; `MINTER_PRIVATE_KEY` için `eth_account.Account.create()` ile **yeni** anahtar üretir ve
adresini yorum satırında yazar (bu adrese `grantRole(MINTER_ROLE)` verilmesi gerekir); secrets `token_urlsafe`.

---

## 3. Auth — SIWE (Dalga 1c)

### 3.1 Kütüphane kararı
**`siwe` PyPI paketi kullanılmaz; mesaj sunucuda kurulur, `eth_account` ile elle doğrulanır.** Gerekçe: (1) mesajı biz
ürettiğimiz için grameri bilinen bir alt kümedir, tam bir ABNF parser'a gerek yoktur; (2) `siwe` paketi `abnf`/`pydantic`/`web3`
sürüm pinleri taşır, mevcut `pydantic 2.13` / `web3 7.x` ile çakışma riski vardır; (3) `Account.recover_message(encode_defunct(text=…))`
EIP-191 doğrulaması için yeterlidir, EIP-1271 bu sprintte yoktur (02-api §1); (4) nonce satırında **mesajın tamamı** saklanır ve
byte'ı byte'ına eşitlik istenir — parse yalnız hata kodlarını ayrıştırmak için yapılır.

### 3.2 `app/services/siwe.py` (yeni)

```python
MESSAGE_TEMPLATE = (
    "{domain} wants you to sign in with your Ethereum account:\n{address}\n\n{statement}\n\n"
    "URI: {uri}\nVersion: 1\nChain ID: {chain_id}\nNonce: {nonce}\nIssued At: {issued_at}\nExpiration Time: {expiration_time}"
)
@dataclass(frozen=True) class SiweFields: domain, address, statement, uri, version, chain_id, nonce, issued_at, expiration_time, not_before
def build_message(*, domain, address, statement, uri, chain_id, nonce, issued_at: datetime, expiration_time: datetime) -> str
    # address checksum; zaman "%Y-%m-%dT%H:%M:%S.%fZ" kırpılmış ms (2026-09-26T12:00:00.000Z)
def parse_message(text: str) -> SiweFields           # satır tabanlı; başlık regex'i, "Key: value" alanları; eksik/bozuk -> SiweParseError
def recover_address(message: str, signature: str) -> str   # 0x+130 hex; Account.recover_message(encode_defunct(text=message), signature=…) -> küçük harf; hata -> SignatureInvalid
```

### 3.3 Akış (`services/auth.py`, 02-api §1 sırası)

`create_nonce(db, settings, address, client_ip)`:
1. `rate_limiter.hit(f"nonce:{ip}")` → aşımda `RateLimitedError` (429 `rate_limited`, `details.retry_after_seconds`).
2. `addr = normalize(address)` (422 `invalid_address`).
3. `purge_expired_nonces` (1 saatten eski); `nonce = secrets.token_hex(16)`; `issued_at = now`, `expires_at = now + ttl`.
4. `message = build_message(...)`; `AuthNonce(address=addr, nonce, message, expires_at, issued_at)` flush.
5. `NonceOut(nonce, message, expires_at, chain_id, domain)`.

`verify_siwe_login(db, settings, message, signature)`:
1. `parse_message` → `siwe_invalid`.
2. `domain == siwe_domain` → `siwe_domain_mismatch`; `uri == siwe_uri` → `siwe_uri_mismatch`; `chain_id == settings.chain_id` →
   `siwe_chain_mismatch`.
3. `expiration_time > now` → `siwe_expired`; `not_before` varsa `<= now` → `siwe_not_yet_valid`.
4. `SELECT … FROM auth_nonces WHERE nonce=:n FOR UPDATE` → yok `nonce_invalid`; `used_at` dolu `nonce_used`; `expires_at <= now`
   `nonce_expired`; `row.address != normalize(fields.address)` → `nonce_invalid`; **`row.message != message` → `siwe_invalid`**
   (statement/issued_at değiştirilmiş mesaj reddedilir).
5. `recover_address(message, signature) == row.address` → değilse `signature_invalid`.
6. `row.used_at = now`; `build_login_response(db, settings, row.address, auth_time=int(now.timestamp()))`.

`build_login_response(db, settings, address, *, auth_time)`: kullanıcı `select(User).where(User.wallet_address == address)`
(**users.py import etmez**; Dalga 1'de users.py hâlâ eski), `account_disabled`, `last_login_at`, `issue_token(...)`,
`LoginOut(token, expires_at, address=checksum(address), registered, user)`.

`refresh_token(db, settings, claims)`: `now - claims.auth_time > settings.refresh_max_age_seconds` → 401 `session_expired`;
aksi hâlde `build_login_response(..., auth_time=claims.auth_time)` (auth_time **taşınır**). `auth_me` → `AuthMeOut(address=checksum…)`.

### 3.4 `models/auth_nonce.py`
```python
address: Mapped[str] = mapped_column(String(42), nullable=False, index=True)     # küçük harf
nonce:   Mapped[str] = mapped_column(String(64), unique=True, nullable=False)     # 32 hex
message: Mapped[str] = mapped_column(Text, nullable=False)                        # imzalanan metin, birebir
issued_at, expires_at: DateTime(tz)  not null;  used_at nullable;  created_at server_default now()
```

### 3.5 JWT (`core/security.py`)
- `TokenClaims(address: str, user_id, role, expires_at, jti, auth_time: int)`.
- `create_access_token(settings, *, address, user_id, role, auth_time: int | None = None)`: `sub=address.lower()`, `uid`, `role`,
  `iat`, `exp=iat+access_token_ttl_seconds`, `jti=uuid4().hex`, `iss=settings.siwe_domain`, `auth_time = auth_time or iat`.
- `decode_access_token`: `issuer=settings.siwe_domain`, `options.require=["sub","exp","iat","jti","auth_time"]`; `sub` normalize edilir.
- AES-GCM / `pool_key_encryption_key` fonksiyonları **silinir** (`encrypt_secret`, `decrypt_secret`, `generate_encryption_key`).

### 3.6 Rate limit — `app/core/ratelimit.py` (yeni)
```python
class TokenBucket: def __init__(rate_per_minute: int, burst: int | None = None); def hit(key: str, now=None) -> float | None  # None=izin, float=retry_after
nonce_limiter = TokenBucket(rate_per_minute=settings.auth_nonce_rate_limit_per_minute)   # modül düzeyi, lazy
def client_ip(request: Request) -> str    # request.client.host (uvicorn --proxy-headers X-Forwarded-For'u çözer; nginx X-Real-IP set eder)
```
Not: 2 uvicorn worker → süreç başına ayrı kova, etkin limit ~2×. Gerçek koruma nginx `limit_req zone=traderkirala_auth`
(§9.3); uygulama içi kova ikinci savunmadır. Anahtar bellek içi `dict`, 10 dk dokunulmayan girdiler periyodik temizlenir (`hit` içinde).

### 3.7 `api_deps.py`
`_load_user(db, address)` → `User.wallet_address == normalize(address)`; `get_current_user(claims)` → `_load_user(db, claims.address)`;
`SorobanDep/HorizonDep` → `ChainDep = Depends(get_chain)`; `require_admin_key` korunur; `__all__` güncellenir.

### 3.8 Şemalar (`schemas/auth.py`)
`NonceIn{address: Address}`, `NonceOut{nonce, message, expires_at, chain_id, domain}`, `VerifyIn{message: str(min 1, max 2000),
signature: str(regex ^0x[0-9a-fA-F]{130}$)}`, `LoginOut{token, expires_at, address: AddressOut, registered, user: MeOut|None}`,
`AuthMeOut{address: AddressOut, registered, user, token_expires_at}`. `Sep10*` silinir. Router: `sep10_challenge/sep10_verify`
silinir; `create_nonce(body, request: Request, db, settings)` IP'yi geçirir; handler adları korunur (02-api §14).

---

## 4. İşlem modeli (Dalga 1b model, Dalga 2e servis)

### 4.1 `PendingTransaction` (yeni kolonlar — 02-api §2)
| Kolon | Tip | Not |
|---|---|---|
| `kind` | Enum(PendingTxKind) | `open, open_reserved, propose, fund, fund_reserved, accept, cancel, settle, claim, trade, reserve, release, transfer, admin` |
| `action` | String(32) NOT NULL | ABI fn adı: `open`, `openReserved`, `releaseAll`, `transfer`, `setToken`… |
| `from_address` | String(42) NOT NULL | göndermesi gereken cüzdan (küçük harf) — receipt doğrulaması bunu kullanır |
| `to_address` | String(42) NOT NULL | vault / token / alıcı |
| `calldata` | Text NOT NULL | `0x…` küçük harf; native transferde `"0x"` |
| `value` | Numeric(78,0) NOT NULL default 0 | wei |
| `gas` | BigInteger nullable | önerilen `gas` |
| `chain_id` | Integer NOT NULL | |
| `agreement_id` | UUID FK nullable (var) | |
| `listing_id` | UUID FK `listings` SET NULL nullable, index | reserve/release; eski `payload.context.listing_id` yerine |
| `tx_hash` | String(66) nullable | **kısmi unique index** `uq_pending_transactions_tx_hash WHERE tx_hash IS NOT NULL` |
| `status` | Enum | `pending, submitted, confirmed, failed, expired`; server_default `pending` |
| `block_number`, `block_hash` | BigInteger / String(66) nullable | receipt |
| `error_code`, `error_message`, `contract_error_code` | String(64) / Text / Integer nullable | 02-api §2.6 |
| `payload` | JSONB | builder bağlamı (`context`), gateway `summary`; korunur |
| `result` | JSONB nullable | `{events:[…], gas_used, effective_gas_price, applied}` |
| `created_at, submitted_at, confirmed_at, expires_at` | DateTime(tz) | `confirmed_at` yeni (failed'da da dolar) |
| ~~`unsigned_xdr`~~ | — | **düşer** |

### 4.2 Build tarafı (`agreements.record_pending`, tüm build uçları)
`record_pending(db, user, kind, agreement, unsigned: UnsignedTx, payload=None, *, listing=None) -> PendingTransaction`:
aynı (user, kind, agreement|listing) için `pending` durumundaki eski satırlar `expired(superseded)`; yeni satır `to_address,
calldata, value, gas, action, from_address=user.wallet_address, chain_id, expires_at=unsigned.expires_at`,
`payload={"action":…, **summary, "context": payload}`. `unsigned_out(pending, unsigned) -> UnsignedTxOut` 02-api §2.1
alanlarını (checksum adresler, `value/gas` string, `pre_steps` `PreStepOut` listesi, `description` §6.1 tablosundan) doldurur.

### 4.3 `POST /tx/submit` — `services/tx_submit.py` yeni akışı (ZORUNLU sıra)

```
submit_hash(db, settings, chain, user, pending_tx_id, tx_hash) -> TxStatusOut
 1. tx_hash biçimi (^0x[0-9a-f]{64}$, küçük harfe çevir)            -> 422 invalid_tx_hash
 2. pending = db.get(PendingTransaction, id, with_for_update=True)   -> 404 pending_tx_not_found; sahibi/admin değil -> 403 not_owner
 3. status ∈ {confirmed, failed}  -> idempotent: tx_status_out(pending) döner (hash farklıysa 409 tx_hash_conflict)
    status == expired ya da (pending ve expires_at <= now) -> expired yaz, 409 pending_tx_expired
    status == submitted ve pending.tx_hash != tx_hash -> 409 tx_hash_conflict
    başka satırda aynı tx_hash (select id where tx_hash=… and id != pending.id) -> 409 tx_hash_conflict
 4. pending.status = submitted; pending.tx_hash = tx_hash; pending.submitted_at = now;  await db.commit()   # KİLİT BURADA BİTER
 5. receipt = await _wait_receipt(chain, tx_hash, timeout=settings.tx_submit_timeout_seconds, poll=1.0)
      döngü: get_receipt -> None ise 1 sn uyku; zaman aşımında None
 6. receipt None -> tx_status_out(pending) (status "submitted"); worker pending_tracker devralır
 7. finalize_receipt(ctx, pending_id, receipt, actor_user_id=user.id) -> yeniden FOR UPDATE alır (kısa):
      a. satır artık submitted değilse (tracker önce bitirdi) -> mevcut durumu döndür (idempotent)
      b. doğrulama: same(receipt.from_address, pending.from_address) and same(receipt.to_address, pending.to_address)
         and receipt.input.lower() == pending.calldata.lower() and receipt.value == int(pending.value)
         değilse: status=failed, error_code="receipt_mismatch", error_message, confirmed_at=now; await db.commit(); raise ReceiptMismatchError (409)
      c. receipt.status == 1: applied = indexer.apply_tx_result(ctx, pending, receipt, actor_user_id)  # §4.5
         status=confirmed, block_number/block_hash, confirmed_at, result={events, gas_used, effective_gas_price, applied}
      d. receipt.status == 0: info = await chain.explain_failure(receipt); status=failed,
         error_code=abi.error_code_of(info) ("vault:<Ad>" | "erc20:<Ad>" | "out_of_gas" | "reverted"), contract_error_code=info.code,
         error_message=ERROR_MESSAGES/info.message; notify(user, "tx_failed", …)
      e. flush (request session commit eder)
 8. tx_status_out(db, settings, chain, pending) -> TxStatusOut  (02-api §2.3; confirmations = latest_block - block_number + 1)
```
İstisna kuralı: bu servis **`db.commit()` çağıran tek servistir** (adım 4 ve 7b); gerekçe 00-inceleme §B hata 2 (60 sn kilit).
`get_db` bağımlılığı hata durumunda rollback yaptığından, hata fırlatmadan önce yazılan `failed` durumu 7b'de açıkça commit edilir.

`get_pending(db, settings, chain, user, pending_id)` (`GET /tx/{pending_id}`): sahiplik; `pending` + süresi geçmiş → `expired`;
`submitted` → `get_receipt` (tek çağrı, bekleme yok) → varsa `finalize_receipt`; `TxStatusOut` döner. `PendingTxOut` silinir.

### 4.4 Hata kodu üretimi (`error_code`)
`vault:<Ad>` (kodlu custom error), `erc20:<Ad>`, `panic:<hex>`, `out_of_gas`, `reverted` (`explain_failure`); `receipt_mismatch`
(7b); `not_included` (tracker: 30 dk receipt yok ve `get_transaction` None); `reorged` (tracker: `confirmed` satırın `block_hash`'i
zincirde artık yok ve receipt yok, §5.4); `expired` (imzalanmadı). `contract_error_code` yalnız `vault:` için (1–30).

### 4.5 `apply_tx_result` — receipt loglarından event işleme (indexer ile aynı yol)
`indexer.apply_tx_result(ctx, pending, receipt: TxReceiptResult, *, actor_user_id) -> dict{applied:int, events:list}`:
`receipt.events` (yalnız vault adresi) sırayla `apply_event(ctx, record, actor_user_id=…)` → her biri idempotent (§5.3);
`events` listesi API için `event_out(record) = {"name", "args"}` (`args` içindeki tüm `int` değerler **string**, adresler checksum,
bytes hex). `pending.kind`'e göre iyimser ilerletme (`_optimistic_by_kind`) **kalkar**: gerçek receipt'te loglar her zaman vardır;
event yoksa (ör. yalnız `approve` receipt'i yanlış satıra bildirildi) satır `failed(receipt_mismatch)` olur — zaten 7b yakalar.
Bu fonksiyon indexer'ın da kullandığı `apply_event` üzerinden çalıştığı için aynı olay daha sonra `eth_getLogs` ile ikinci kez
gelince değişiklik yaratmaz (`created_tx == tx_hash`, `(tx_hash, log_index)` benzersizliği, statü kontrolleri).

### 4.6 Worker `pending_tracker` job'u (yeni; `services/indexer.py` içinde `track_submitted`, `expire_pending`)
Her `pending_tracker_seconds` (5 sn):
1. `expire_pending(ctx)`: `pending` + `expires_at <= now` → `expired` (eski `finalize_submitted`'dan ayrıldı).
2. `track_submitted(ctx, limit=25)`: `submitted` ve `submitted_at <= now - 10 s` satırları (`FOR UPDATE SKIP LOCKED`):
   `receipt = get_receipt(tx_hash)`; varsa `finalize_receipt` (§4.3/7); yoksa `get_transaction(tx_hash)`: None **ve**
   `submitted_at + submitted_tx_timeout_minutes <= now` → `failed(not_included)`; bilinen ama bekliyor → dokunma.
3. `recheck_recent_confirmed(ctx)`: son `confirmations*10` blok içinde `confirmed` olmuş satırlar için `get_block(block_number).hash ==
   block_hash` kontrolü; farklıysa `get_receipt` → yeni bloktaysa güncelle, yoksa `failed(reorged)` + reconciler'a bırak (§5.4).
   (Monad MonadBFT ~0.8 sn finality; bu adım savunma amaçlı, ucuz.)

### 4.7 Reorg / CONFIRMATIONS
- `/tx/submit` ve `GET /tx/{id}` receipt gelir gelmez (0 onay) uygular — UX için. `TxStatusOut.confirmations` bilgilendiricidir.
- Indexer yalnız `latest - confirmations` derinliğine kadar okur; aynı olayları ikinci kez görür (idempotent).
- Reorg'da düşen tx: §4.6/3 ve §5.4.

---

## 5. Indexer (Dalga 2f) — `services/indexer.py`, `services/trader_stats.py`, `worker/main.py`

### 5.1 Cursor ve pencereler (`run_indexer_once(db, chain, settings) -> IndexerRunResult`)
```
state = indexer_state["vault_events"]   {block_number, last_block_hash}
latest = chain.latest_block(); safe_head = latest - settings.confirmations
if state.block_number is None:
    start = settings.indexer_start_block or deployments.blockNumber or max(0, latest - 5000)
else:
    reorg kontrolü (§5.4); start = state.block_number + 1
windows = 0
while start <= safe_head and windows < indexer_max_windows_per_run:
    end = min(start + indexer_block_window - 1, safe_head)
    logs = chain.get_logs(start, end)              # vault adresi; gateway pencereyi gerekirse böler
    for rec in logs (block_number, log_index sırası): _apply_guarded(ctx, rec)   # §5.3
    state.block_number = end; state.last_block_hash = chain.get_block(end).hash; flush
    start = end + 1; windows += 1
retry_failed_events(ctx)                            # §5.3
```
`IndexerRunResult{skipped, reason, from_block, to_block, latest_block, events_seen, events_applied, events_failed, reorg_depth, errors}`.
`IndexerStatusOut` (02-api §10): `lag_blocks = latest - state.block_number`, `confirmations`, `vault_address`.

### 5.2 Event → handler eşlemesi (Rust adı → Solidity adı → handler)
| Rust | Solidity event | Handler | Etki |
|---|---|---|---|
| `proposed` | `Proposed(id, trader, customer, principal, baseToken)` | `_on_created(opened=False)` | draft → `proposed`, `onchain_id`, `created_tx`, `platform_fee_bps`/`vault_address` (`read_agreement` best-effort), bildirim müşteriye |
| `opened` | `Opened(id, trader, customer, principal, baseToken)` | `_on_created(opened=True)` | draft → `funded`, bakiye `[base=principal]`, bildirim trader'a |
| `activated` | `Activated(id, startTime, endTime)` | `_on_activated` | → `active`, `start/end_time`, `activate_tx`, ilk snapshot, `refresh_trader_stats` |
| `cancelled` | `Cancelled(id, refunded)` | `_on_cancelled` | → `cancelled`, bakiyeler silinir, iade bildirimi |
| `traded` | `Traded(id, trader, tokenIn, tokenOut, amountIn, amountOut, valueAfter)` | `_on_traded` | `Trade(tx_hash, log_index, block_number)`, bakiyeler `read_balances` (RPC yoksa aritmetik), snapshot, bildirimler |
| `settled` | `Settled(id, finalValue, profit, traderFee, platformFee, customerPayout, by)` | `_on_settled` | → `settled`, tutarlar, `settled_by`, **bakiyeler silinmez**: `read_balances` ile orphan bakiyeler kalır (claim için) |
| — | `Unliquidated(id, token, amount, delivered)` | `_on_unliquidated` | `delivered=false` → o token için `AgreementBalance` korunur/oluşturulur, müşteriye "claim bekliyor" bildirimi; `true` → bildirim "token olarak teslim edildi" |
| — | `Claimed(id, token, amount)` | `_on_claimed` | bakiye satırı silinir, bildirim |
| `reserved` | `Reserved(id, customer, token, amount, listingRef)` | `_on_reserved` | `listing_ref` eşleşen draft ilan → `reservation_id`, `reserved_amount`, `reserve_tx`, `draft→active` |
| `released` | `Released(id, customer, token, amount, remaining)` | `_on_released` | `reserved_amount=remaining`, `release_tx` |
| `reservation_drawn` | `ReservationDrawn(id, agreementId, amount, remaining)` | `_on_reservation_drawn` | `reserved_amount=remaining` |
| `token_set` | `TokenSet(token, allowed, isBase)` | `_on_token_set` | `assets.onchain_allowed` |
| `config_changed` | `ConfigChanged(key, router, platformFeeBps, feeRecipient, paused, settleSlippageBps)` | `_on_config_changed` | `routers/config.reset_contract_cache()` eşdeğeri: `indexer_state["vault_config"]` JSON; log |
| `upgraded` | OZ `Upgraded(implementation)` | `_on_upgraded` | log ERROR seviyesinde (beklenmedik upgrade alarmı) + admin bildirimi (is_admin kullanıcılar) |
| — | `RouterChangeProposed/Cancelled`, `OwnershipTransfer*`, `Initialized` | `_on_admin_event` | yalnız log |

Kimlik çözümü (`_resolve_agreement`): `onchain_id` → pending satırı (`tx_hash`, kind ∈ open/open_reserved/propose) → `read_agreement`
ile terms'e göre draft eşleştirme (`listing_ref` hex, sonra taraflar+principal+base) → `_create_from_chain`. Adresler `same()` ile.
Statü eşlemesi: `AgreementStatus.from_onchain(1..5)`; `0` → `None` (kayıt yok). Tutarlar `from_raw(raw, asset.decimals)`.

### 5.3 Hata veren olayı yutmama — `failed_events`
`_apply_guarded(ctx, rec)`: `async with db.begin_nested(): apply_event(...)`. İstisnada olay **kaybolmaz**:
`failed_events` tablosuna upsert (`(tx_hash, log_index)` unique; `attempts += 1`, `error`, `next_retry_at = now + min(2^attempts, 3600) s`,
`args` JSON, `block_number`, `event_name`) ve **cursor ilerler** (bir zehirli olay zincirin kalanını durdurmaz). Her tick sonunda
`retry_failed_events(ctx, limit=20)`: `resolved_at IS NULL AND next_retry_at <= now` satırları `EventRecord`'a geri çevirir (`args`
JSON'dan; adres/bytes/int tipleri `abi.event_arg_types` ile), tekrar `apply_event`; başarıda `resolved_at = now`. `attempts >= 10`
olanlar `ERROR` loglanır (`alert` etiketiyle), denenmeye devam eder (saatte bir). `IndexerRunResult.events_failed` sayılır.
Karar değişikliği önerisi §12/1: `IndexerStatusOut.failed_events` alanı.

### 5.4 Reorg tespiti
Tick başında `state.last_block_hash` varsa `chain.get_block(state.block_number).hash` ile karşılaştırılır. Farklıysa:
`ancestor = state.block_number - 1` iken geriye yürü (en fazla `confirmations * 8` blok; her adımda `get_block(n).hash` ile o blokta
yazılmış `Trade.block_number == n` satırlarının/`pending.block_hash`'lerin hash'i karşılaştırılır — eşleşen ilk blok ortak ata);
`Trade` satırları `block_number > ancestor` **silinir** (indexer yeniden yazar), `Agreement.*_tx` alanları için `get_receipt(tx)`
None ise ilgili tx alanı `None` yapılır ve `reconciler.sync_status_from_chain` statüyü düzeltir; `state.block_number = ancestor`,
`IndexerRunResult.reorg_depth`; ERROR log. Derinlik `confirmations*8`'i aşarsa indexer bu tick'i atlar ve `alert` loglar (elle müdahale).

### 5.5 Trader istatistikleri → `services/trader_stats.py`
`refresh_trader_stats(db, trader_id)` (indexer.py:1094-1139) aynen taşınır; `money.quantize(x, 18)`; `indexer.py`, `users.py`,
`dashboard.py` bu modülden import eder. `max_drawdown_bps` tanımı değişmez (bilinen eksik, Sprint 3).

### 5.6 Reconciler (`run_reconcile_once`) yeni okuma çağrıları
`refresh_agreement_from_chain(ctx, ag, *, snapshot, alerts, now)`: `view = chain.read_agreement(id)` (NotFound → atla);
`sync_status_from_chain(ctx, ag, view)` (`view.status` 1–5; settled'da `final_value/trader_fee/platform_fee/customer_payout/settled_at`
struct'tan, `profit = max(0, final - principal)`, bakiyeler `read_balances` ile senkron — orphan kalır); aktifse `balances =
read_balances`, `value = read_value_in_base` → `_upsert_balances`, `_set_value`, snapshot, `check_alerts` (değişmez). `GET /agreements/{id}`
canlı yenileme 10 sn süreç içi önbellek arkasında (`_refresh_cache: dict[uuid, float]`, BE-24).

### 5.7 `worker/main.py`
`JobContext.chain`, `WorkerDeps(settings, chain)`. `JOBS`: `indexer (indexer_poll_seconds)`, `pending_tracker (pending_tracker_seconds)`,
`reconciler (reconcile_seconds)`, `expiry (worker_offer_expiry_seconds: offers.expire_stale + nonce purge)`, `push`, `fx`.
`anchor_sync` ve `app/worker/anchor_sync.py` silinir; `_close` yalnız `chain.close()`. Başlangıç logu: `chain_id, rpc_url, vault`.
`run_once(job, *, db, settings, chain)`.

---

## 6. Servis değişiklikleri — dosya dosya

### 6.1 `services/agreements.py` (2e)
- `build_terms(ag) -> Terms`: adresler `ag.customer.wallet_address`, `ag.trader.wallet_address`, `ag.base_asset.address`;
  `principal = to_raw(ag.principal, decimals)`; `listing_ref = bytes.fromhex(ag.listing_ref)` (onarım korunur).
- `ACTIONS = ("propose","open","open_reserved","fund","fund_reserved","accept","cancel","settle","claim")`;
  `TxAction` Literal aynı. Eşleme (02-api §3.1):

| action | durum / rol | çağrı | pre_steps | description (TR) |
|---|---|---|---|---|
| `propose` | draft / trader | `build_propose(addr, terms)` | — | "Sözleşme #{n} teklifini zincire yaz" |
| `open` | draft / customer; rezerv anaparayı karşılıyorsa **409 `use_reserved_action`** | `build_open` | `approve(vault, principal)` | "{amount} {sym} anaparayı kasaya kilitle (sözleşme #{n})" |
| `open_reserved` | draft / customer; rezerv yok/yetersiz → **409 `reservation_insufficient`** | `build_open_reserved(addr, terms, res_id)` | — | "Rezervasyon #{r}'den {amount} {sym} ile sözleşmeyi aç" |
| `fund` | proposed / customer (aynı 409 kuralı) | `build_fund` | `approve` | "Sözleşme #{n} için {amount} {sym} yatır" |
| `fund_reserved` | proposed / customer | `build_fund_reserved` | — | |
| `accept` | funded / trader | `build_accept` | — | "Sözleşme #{n}'yi kabul et ve başlat" |
| `cancel` | proposed (proposer) / funded (iki taraf) | `build_cancel` | — | "Sözleşme #{n}'yi iptal et" |
| `settle` | active; taraf her an; admin (`read_config().owner`) `end_time` sonrası; herkes `end_time+7g` | `build_settle(addr, id, min_outs)` | — | "Sözleşme #{n}'yi kapat ve dağıt" |
| `claim` | settled / customer; `asset_id` zorunlu (422 `asset_required`), bakiye > 0 (409 `nothing_to_claim`) | `build_claim(addr, id, token)` | — | "{sym} bakiyesini talep et" |

- `approval_pre_steps(chain, owner, asset, spender, amount_raw) -> list[PreStep]`: `allowance(asset.address, owner, spender) <
  amount_raw` ise `[build_approve(...)]`; `PreStepOut{…, asset_id, symbol, amount, amount_raw}`. USDT-tipi sıfırlama yok.
- `_listing_reservation(db, ag) -> tuple[int|None, Decimal|None]` (id, kalan) — `available_actions` `open_reserved` / `fund_reserved`
  döndürür (rezerv ≥ principal ise), aksi hâlde `open`/`fund`; `claim` yalnız settled + müşteri + `balances` boş değil;
  `AgreementOut.claimable_assets = [asset_id…]`.
- `settle_min_outs(chain, ag, slippage_bps)`: `view = read_agreement`, `balances = dict(read_balances)`, `tokens[1:]` sırasıyla
  `quote([token, base], bal)` (hata → 0), `min_out_for_slippage`; keeper/admin yolunda `min_outs=[]`. `preview_settle` varsa tercih.
- Hata eşlemesi: `ContractRevertError` → aynen (400 `contract_error`, `details.error_code`); `ChainError` → 502 `chain_error`.
- `record_pending` §4.2; `unsigned_out(pending, unsigned) -> UnsignedTxOut` (`from_address/to` checksum, `value/gas` str).
- TL/fiyat: `xlm_usd_price` **silinir**; `TlConverter.usd_price(symbol)` `fx.usd_price(symbol)` (indikatif tablo); MON `None`.
- `party_out` → `wallet_address=checksum(user.wallet_address)`; `asset_brief` → `AssetBriefOut{id, symbol, code=symbol, address=checksum,
  decimals, name, icon_url, category}`; `agreement_out` → `last_event_block_number`, `vault_address=checksum(ag.vault_address or
  settings.vault_address)`, `settled_by=checksum|None`, `*_tx` 66 kr.
- Kararlı dışa aktarımlar (2f/2g bunlara güvenir): `OPEN_STATUSES, CLOSED_STATUSES, now_utc, ts_to_dt, json_safe, compute_listing_ref,
  listing_ref_bytes, principal_raw, build_terms, record_pending, unsigned_out, party_out, asset_brief, is_expired, party_role, available_actions`.

### 6.2 `services/trading.py` (2e)
`resolve_asset(db, settings, ref)`: uuid → `Asset.id`; `is_evm_address(ref)` → `Asset.address == normalize(ref)` & `chain_id`;
aksi hâlde `Asset.symbol == ref` (case-insensitive, aktif). `symbol_label` `address` karşılaştırması, `code`→`symbol`.
`compute_quote`: `read_agreement/read_balances/read_value_in_base`, `quote([in,out], amount)`; `_in_base` `quote([token, base])`;
`source = chain.kind == "monad" and "router" or "fake"`; `api_amount_out=None`, `price_impact_pct=None`; `MAX_TOKENS` `abi`'den.
`build_trade_tx`: `build_trade(user.wallet_address, onchain_id, in.address, out.address, amount_in_raw, min_out_raw, deadline)`;
`summary` 02-api §3.2; `pre_steps` yok. `trade_out`: `block_number`, `log_index`, `explorer_url=f"{explorer}/tx/{hash}"`.
`parse_amount(value, decimals)` → `chain.amounts.parse_amount` (varsayılan yok).

### 6.3 `services/listings.py` (2e)
`build_reserve_tx(db, settings, chain, owner, listing_id) -> (listing, UnsignedTx, pending)`: sahip/customer/capital/status kontrolü;
in-flight kontrolü `PendingTransaction.listing_id == listing.id AND kind == reserve AND status IN (pending, submitted)`; `amount_raw =
to_raw(listing.amount, asset.decimals)`; `unsigned = build_reserve(addr, asset.address, amount_raw, listing_ref_bytes(listing))`;
`pre_steps = approval_pre_steps(...)`; `summary = {listing_id, amount, amount_raw, asset_id, listing_ref}`; `record_pending(..., listing=listing)`.
`build_release_tx(db, settings, chain, owner, listing_id, amount: Decimal | None)`: `amount is None` → `build_release_all` (`action=
"releaseAll"`); `amount > 0` → `build_release(…, to_raw(amount))`; `amount == 0` 422; `amount > reserved_amount` 409 `reservation_locked`
(`details={reserved_amount, requested}`); `reservation_id is None` 409 `no_reservation`. `update_listing`: rezerve edilmiş capital ilanında
`amount` değişikliği 409 `amount_locked` (BE-23). `set_status` korunur. `ListingOut.reservation_id/reserved_amount/is_funded` korunur.

### 6.4 `services/wallet.py` (2g) — tamamen yeniden yazılır
- `get_wallet(db, settings, chain, user) -> WalletOut`: `native_balance(addr)` (wei → `balance/balance_raw`); aktif asset'lerin tümü için
  `token_balance` (sıfır dahil; RPC hatası → o satır `balance="0"` + log; tüm RPC çöktü → 502 `chain_error`); opsiyonel Multicall3 tek
  çağrıda (`aggregate3`); `explorer_url = f"{explorer}/address/{checksum}"`. Anchor/Horizon/TL alanları yok.
- `deposit_info(settings, user, faucet_state)`: `pay_uri = f"ethereum:{checksum}@{chain_id}"` (EIP-681), `faucet_url`, `token_faucet =
  {enabled: minter var, assets: [{asset_id, symbol, amount, daily_limit, next_allowed_at}]}` (`FaucetClaim` son talep + 24 sa), `instructions`.
- `build_transfer(db, settings, chain, user, body: WalletTransferIn) -> UnsignedTxOut`: `to = normalize(body.to)` (kendine gönderme 422
  `invalid_destination`); `asset_id None` → `build_transfer(addr, to, mon_to_wei(amount))` (`native_balance < wei + 21000*gas_price` →
  400 `insufficient_funds` `details{needed, available}`); ERC-20 → `build_token_transfer(addr, asset.address, to, to_raw(amount, dec))`
  (`token_balance < raw` → 400). `record_pending(kind=transfer, action="transfer")`.
- `faucet(db, settings, chain, user, asset_id) -> FaucetOut`: `minter_private_key` yok → 503 `faucet_disabled`; asset `symbol ∉
  faucet_amounts` → 422 `asset_not_mintable`; `FaucetClaim` son 24 saatte `>= faucet_daily_limit` → 429 `rate_limited`
  (`details.next_allowed_at`); `tx_hash = chain.faucet_mint(key, asset.address, user.wallet_address, to_raw(amount))`; `FaucetClaim`
  satırı **hemen** yazılır (çift talep yarışına karşı önce `INSERT`, sonra mint; mint hatasında satır silinir); receipt için 15 sn
  beklenir (`status: confirmed | submitted`); `FaucetOut{tx_hash, status, asset_id, symbol, amount, amount_raw, next_allowed_at, explorer_url}`.
- `list_transactions` (opsiyonel, 02-api §7.4): `get_logs(from, to, address=token, topics=[Transfer, [from=user] | [None, to=user]])`
  son `indexer_block_window*5` blok; uygulanmazsa uç **eklenmez**.
- Router `routers/wallet.py`: `get_wallet`, `deposit_info`, `transfer` (`POST /wallet/tx/transfer`), `faucet` (`POST /wallet/faucet`),
  opsiyonel `list_transactions`. `payment`, `trustline`, `AnchorDep` silinir.

### 6.5 `services/offers.py` (2e)
`listing_ref_for(offer_id) = sha256(str(offer_id)).hexdigest()` **değişmez** (bytes32 = 32 bayt hex). `next_onchain_action(acceptor_role,
listing)`: customer → `open_reserved` (ilan `is_funded` ve `reserved_amount >= amount`) ya da `open`; trader → `propose`.
`OfferAcceptOut.next_action` Literal genişler (02-api §8.4). Kabulde ilan `matched`? — ListingStatus'ta yok; BE-23'ün bu kısmı (ilan
`matched`, diğer teklifler `rejected`) **Sprint 3'e ertelenir** (§12/5), bu sprintte yalnız `max_loss_bps` gevşetme reddi ve
`amount_locked` uygulanır. `format_amount(agreement.principal, base.decimals)`.

### 6.6 `services/dashboard.py` + `routers/dashboard.py` (2g)
`chain.token_balance(base.address, user.wallet_address)` → `from_raw(raw, base.decimals)`; `wallet_error` korunur; `base_asset_code =
symbol`. `preferred_base_asset` `Asset.chain_id == settings.chain_id`. `trader_stats` importu yeni modülden.

### 6.7 `services/admin.py` + `services/admin_tx.py` (2e)
- `app/services/stellar/admin_tx.py` → `app/services/admin_tx.py`: `ADMIN_FUNCTIONS = {"set_token": build_admin_set_token, "set_paused":…,
  "set_fees":…, "set_router": build_admin_propose_router, "apply_router": build_admin_apply_router, "set_settle_slippage":…}`;
  `resolve_token(db, settings, token)` uuid | 0x; `build_admin_tx(settings, chain, function, args) -> AdminTxOut`: `owner = (await
  chain.read_config()).owner`, `fee_recipient None → settings.platform_address` (o da yoksa 422), `UnsignedTxOut` alanları +
  `{function, args, vault_address, note}`, `pending_tx_id=None`, `kind="admin"`.
- `submit_admin_tx(settings, chain, tx_hash) -> AdminTxSubmitOut`: `_wait_receipt` (aynı zaman aşımı); `to == vault`, `from == owner`
  doğrulanır (uyuşmazlık → 409 `receipt_mismatch`); `status ∈ submitted|confirmed|failed`, `error_code`, `block_number`, `explorer_url`.
  Admin receipt'inin event'leri indexer'a bırakılır (TokenSet/ConfigChanged idempotent).
- `stats`: `vault_address`, `chain_id`, `anchor_transactions_by_status` silinir. `sync_assets_onchain`: `is_token_allowed(asset.address)`,
  `AssetSyncOut{vault_address, rows[{asset_id, symbol, address, …}]}`. `indexer_status/indexer_reset` §5.1 alanları (`block_number`,
  `last_block_hash`; reset `block_number` null → satır silinir). `create_asset`: `decimals` zincirden `token_metadata` ile doğrulanır
  (`decimals_mismatch`), `address` normalize, dup `(chain_id, address)`; `PATCH /admin/users` `role` → 422 `role_change_forbidden`.
- `contract_config(chain)` → `ContractConfigOut{owner, router, platform_fee_bps, fee_recipient, paused, settle_slippage_bps}` (checksum).

### 6.8 `routers/config.py` (1d) — `ConfigOut` 02-api §4
`chain{chain_id, name, rpc_url, ws_url, explorer_url, native_symbol, native_decimals, faucet_url}`, `contracts{vault, router, multicall3}`
(hepsi settings'ten, checksum), `assets` (`Asset.chain_id == settings.chain_id AND is_active`, sıra `is_base_allowed desc, symbol asc`),
`default_base_asset_code/id`, `platform_fee_bps` (contract'tan), `settle_slippage_bps`, `default_trade_slippage_bps`,
`tx_submit_timeout_seconds`, `pending_tx_ttl_seconds`, `limits`, `contract` (`read_config` 60 sn önbellek; hata → `None` + `contract_error`),
`auth{siwe_domain, siwe_uri, siwe_statement, nonce_ttl_seconds, access_token_ttl_seconds, refresh_max_age_seconds}`, `fx_cache_seconds`,
`usd_prices` (symbol anahtarlı). `API_VERSION = "2.0.0"`. `/fx`, `/fx/convert` şekli aynı, `amount_usd` `decimal_places=18`.

### 6.9 `routers/meta.py` (1d)
`/health` değişmez. `/health/chain`: `t0; cid = await chain.chain_id(); bn = await chain.latest_block()` → 200 `{ok:true, chain_id, block_number,
latency_ms, rpc_url}`; `cid != settings.chain_id` → 503 `{ok:false, chain_id, error:"chain_id_mismatch", rpc_url}`; istisna → 503
`{ok:false, error: e.__class__.__name__}`. `/health/stellar` silinir.

### 6.10 Silinen dosyalar (1d)
`app/services/stellar/` (tümü), `app/services/anchor.py`, `app/routers/anchor.py`, `app/schemas/anchor.py`, `app/worker/anchor_sync.py`,
`app/models/anchor.py`, `app/services/market.py`, `app/routers/market.py`, `app/schemas/market.py`, `tests/test_anchor.py`,
`tests/test_soroban_live.py`, `tests/test_contract_abi.py`, `tests/test_fake_gateways.py`, `scripts/deploy_contract.sh`,
`scripts/e2e_testnet.py` (OPS-12 yeniden yazar), `deploy/contract.testnet.json`, `deploy/deploy.testnet.log`,
`deploy/nginx/mobilback.yolalapp.com` (yerine `monadback.yolalapp.com`, 1b), `docs/refs/*` (Stellar/SEP/Soroswap), `docs/CONTRACT.md`
(yerine `docs/monad/01-kontrat-spec.md`'ye bağlantı), `contracts/` (Rust; Foundry projesi kök `contracts/`'a taşındı — 01-spec §0.3/2).
`ROUTER_MODULES` (`app/main.py`, 1d): `market`, `anchor` çıkar; sıra: `config, meta, legal, auth, users, notifications, assets, admin,
listings, discover, offers, conversations, dashboard, ratings, agreements, tx, trades, activity, wallet`. `lifespan` logu `chain_id/rpc_url`.

### 6.11 `services/fx.py` (2g)
`INDICATIVE_USD_PRICES` `settings.usd_prices_json` ile ezilebilir; varsayılan `{"tUSDC": 1, "tWETH": 3000, "tWBTC": 60000}` (MockRouter
deploy fiyatları); `usd_price(symbol)`; `MON` → `settings.mon_usd_price` (None). `RATE_UNIT` 7 dp kalabilir (kur). `INDICATIVE_NOTE` metni Monad.

### 6.12 `services/users.py` (2g)
`get_by_wallet_address(db, address)` (`normalize`), `register(db, settings, address, data)` adres JWT `sub`'dan (gövdede adres yoksayılır),
`create_access_token(address=…, auth_time=claims.auth_time)`; `UserOut/MeOut.wallet_address: AddressOut`; `GET /users/{id}` `budget_amount=None`
(BE-22); `trader_profile`: `live_positions` müşteri adı/anapara anonim (`customer_username="—"`, `principal` yuvarlanmış aralık) ve
`recent_trades` `notify_investors=True` filtresi (BE-22); `PositionBalanceOut{asset_id, symbol, code, address, balance}`; `TradeBriefOut.block_number`.
Admin kullanıcı araması `User.wallet_address.ilike(pattern.lower())`.

### 6.13 `routers/legal.py` (1d)
Stellar/SEP/Freighter/XLM geçen cümleler Monad/MetaMask/MON/tUSDC ile değiştirilir; "Elevator" → "TraderKirala"; şekil (HTML) aynı.

---

## 7. Modeller ve Alembic (Dalga 1b)

### 7.1 Kolon kolon değişiklik tablosu (00-inceleme §B DB şeması temel)

| Tablo | Eski | Yeni |
|---|---|---|
| `users` | `stellar_address String(56) unique idx` | `wallet_address String(42) unique, ix_users_wallet_address` (küçük harf) |
| `users` | `budget_amount, min_capital, managed_capital Numeric(30,7)` | `Numeric(78,18)` |
| `auth_nonces` | `public_key String(56) idx` | `address String(42) idx`; **+** `message Text NOT NULL`, `issued_at DateTime(tz) NOT NULL` |
| `assets` | `network String(16)` | `chain_id Integer NOT NULL` |
| `assets` | `contract_id String(56)` | `address String(42) NOT NULL` (küçük harf) |
| `assets` | `code String(12)` | `symbol String(12) NOT NULL` |
| `assets` | `issuer String(56)` | **düşer** |
| `assets` | `decimals Integer default 7` | `decimals Integer NOT NULL` (varsayılan yok) |
| `assets` | `uq_assets_network_contract_id`, `ix_assets_network_code` | `uq_assets_chain_id_address`, `ix_assets_chain_id_symbol` |
| `agreements` | 8× `Numeric(30,7)` (principal, current_value, high_water_value, final_value, profit, trader_fee, platform_fee, customer_payout) | `Numeric(78,18)` |
| `agreements` | `created_tx/activate_tx/cancel_tx/settle_tx String(64)` | `String(66)` |
| `agreements` | `settled_by String(56)` | `String(42)` |
| `agreements` | `last_event_ledger BigInteger` | `last_event_block_number BigInteger` |
| `agreements` | — | **+** `platform_fee_bps Integer NULL`, `vault_address String(42) NULL` |
| `agreement_balances` | `balance Numeric(30,7)` | `Numeric(78,18)` |
| `agreement_value_snapshots` | `value Numeric(30,7)` | `Numeric(78,18)` |
| `listings` | `amount, reserved_amount, min_capital Numeric(30,7)` | `Numeric(78,18)` |
| `listings` | `reserve_tx, release_tx String(64)` | `String(66)` |
| `offers` | `amount Numeric(30,7)` | `Numeric(78,18)` |
| `trades` | `onchain_seq BigInteger` | `log_index Integer` |
| `trades` | `tx_hash String(64)` | `String(66)` |
| `trades` | `ledger BigInteger` | `block_number BigInteger` |
| `trades` | `amount_in, amount_out, value_after Numeric(30,7)` | `Numeric(78,18)` |
| `trades` | `uq_trades_tx_hash_onchain_seq` | `uq_trades_tx_hash_log_index` |
| `pending_transactions` | `unsigned_xdr Text` | **düşer** |
| `pending_transactions` | `tx_hash String(64) idx` | `String(66)` + kısmi unique `uq_pending_transactions_tx_hash WHERE tx_hash IS NOT NULL` |
| `pending_transactions` | — | **+** `action String(32) NOT NULL`, `from_address String(42) NOT NULL`, `to_address String(42) NOT NULL`, `calldata Text NOT NULL`, `value Numeric(78,0) NOT NULL server_default '0'`, `gas BigInteger`, `chain_id Integer NOT NULL`, `listing_id UUID FK listings ON DELETE SET NULL (ix)`, `block_number BigInteger`, `block_hash String(66)`, `error_code String(64)`, `error_message Text`, `contract_error_code Integer`, `confirmed_at DateTime(tz)` |
| `pending_transactions` | `status server_default 'built'` | `'pending'` (değer kümesi §4.1) |
| `indexer_state` | `cursor String(128)` | **düşer** |
| `indexer_state` | `ledger BigInteger` | `block_number BigInteger` |
| `indexer_state` | — | **+** `last_block_hash String(66)` |
| `anchor_sessions`, `anchor_transactions` | | **DROP TABLE** |
| `failed_events` (yeni) | | `id UUID pk, tx_hash String(66), log_index Integer, block_number BigInteger, event_name String(64), args JSONB, error Text, attempts Integer NOT NULL default 1, next_retry_at DateTime(tz), resolved_at DateTime(tz), created_at, updated_at`; `uq_failed_events_tx_hash_log_index`, `ix_failed_events_next_retry_at` |
| `faucet_claims` (yeni) | | `id UUID pk, user_id FK users CASCADE, asset_id FK assets RESTRICT, tx_hash String(66), amount Numeric(78,18), created_at`; `ix_faucet_claims_user_asset_created (user_id, asset_id, created_at)` |

Model dosyaları: `models/failed_event.py`, `models/faucet_claim.py` (yeni); `models/enums.py`: `AgreementStatus.from_onchain` →
`{1: proposed, 2: funded, 3: active, 4: settled, 5: cancelled}` ve `0`/bilinmeyen → `None` döner (`from_onchain(status) -> AgreementStatus | None`);
`ReservationStatus(IntEnum) None=0, Open=1, Released=2, Consumed=3` eklenir; `PendingTxKind/PendingTxStatus` §4.1; `AnchorTxKind/AnchorTxStatus` silinir.
`models/__init__.py` anchor importları çıkar, yeni modeller girer. `Asset.is_native` property → sabit `False`; `canonical/is_classic` silinir;
`__repr__` `symbol/address`.

### 7.2 Migration — `alembic/versions/20260927_1200_d3e4f5a6b7c8_monad_cutover.py`
`revision = "d3e4f5a6b7c8"`, `down_revision = "c2d3e4f5a6b7"`. **Irreversible:** `downgrade()` → `raise RuntimeError("monad_cutover is
irreversible; restore from the pg_dump taken before the upgrade")`. Elle yazılır (autogenerate değil); adımlar:
0. Docstring: "Öncesinde `scripts/backup.sh` (pg_dump) alınır" (K10).
1. `TRUNCATE TABLE users, auth_nonces, assets, indexer_state, anchor_sessions, anchor_transactions, follows, interactions, favorites,
   listings, notifications, offers, agreements, agreement_balances, agreement_value_snapshots, conversations, messages, pending_transactions,
   ratings, trades RESTART IDENTITY CASCADE` (cüzdanlar değişiyor; K10).
2. `DROP TABLE anchor_transactions, anchor_sessions`.
3. `users`: `alter_column stellar_address → wallet_address type String(42)`; indeks/unique yeniden adlandırma (`ix_users_stellar_address` →
   `ix_users_wallet_address`, `uq_users_stellar_address` → `uq_users_wallet_address`); 3 Numeric tipi.
4. `auth_nonces`: rename + type; `add_column message Text NOT NULL`, `issued_at` (tablo boş → server_default gerekmez).
5. `assets`: drop constraint/index; `network → chain_id Integer` (`USING 10143` gereksiz, boş); `contract_id → address String(42)`;
   `code → symbol`; `drop issuer`; `decimals` server_default kaldır; yeni uq/ix.
6. `agreements`, `agreement_balances`, `agreement_value_snapshots`, `listings`, `offers`, `trades`: tip/isim değişiklikleri; `trades`
   unique constraint drop/create; `agreements` yeni iki kolon.
7. `pending_transactions`: drop `unsigned_xdr`; add kolonlar; `tx_hash` tip + `drop_index ix_pending_transactions_tx_hash` +
   `create_index(..., unique=True, postgresql_where=text("tx_hash IS NOT NULL"))`; `status` server_default.
8. `indexer_state`: drop `cursor`; rename `ledger → block_number`; add `last_block_hash`.
9. `create_table failed_events`, `create_table faucet_claims`.
Test: `tests/test_alembic.py` (Dalga 3h) boş DB'de `alembic upgrade head` çalıştırır ve `Base.metadata` ile `compare_metadata` farkı sıfır ister.

### 7.3 `scripts/seed_assets.py` yeni yapısı
```
python -m scripts.seed_assets [--file deployments/monad-testnet.json]
```
1. `load_deployment(settings) -> Deployment{chain_id, vault, router, tokens[{symbol,address,decimals,is_base}], block_number}`:
   `settings.deployments_file` (backend köküne göre `Path(__file__).parents[1] / file`) varsa okur (01-spec §9.4 şeması); yoksa
   `settings.vault_address/router_address` + `settings.assets_json`; ikisi de yoksa `SystemExit("no deployment info")`.
2. `deployment.chain_id != settings.chain_id` → hata; `vault != settings.vault_address` → **WARNING** (yanlış .env yakalanır).
3. `seed_assets(db, deployment) -> (created, updated)`: satır `(chain_id, normalize(address))` ile bulunur; `symbol, name, decimals,
   category, is_base_allowed` yenilenir; `is_active`, `onchain_allowed` mevcut satırda korunur. Ad/kategori haritası:
   `tUSDC → ("Test USD Coin", stable_fx)`, `tWETH → ("Test Wrapped Ether", crypto)`, `tWBTC → ("Test Wrapped Bitcoin", crypto)`,
   bilinmeyen symbol → (`symbol`, crypto).
4. `indexer_state["vault_events"]` yoksa ve `deployment.block_number > 0` → `block_number = deployment.block_number - 1` yazılır (indexer
   deploy bloğundan başlar). Var olan satıra dokunulmaz.
`entrypoint.sh` seed adımı korunur. Test fixture `seed_assets` (Dalga 3h) aynı fonksiyonu `FakeChainGateway` sabitleriyle kurulan
bir `Deployment` ile çağırır.

---

## 8. Testler (Dalga 3h) — tasarım

### 8.1 `tests/conftest.py` yeni fixture'ları
- `engine`, `_clean_tables`, `db`, `client`: değişmez (create_all/drop_all; `DATABASE_URL_TEST`).
- `chain` → `FakeChainGateway(now=int(time.time()))`, `set_chain(gw)`; teardown `set_chain(None)`. (Eski `soroban`/`horizon` kalkar;
  `tests/helpers_agreements.soroban_gateway` → `chain_gateway`.)
- `make_user(role, *, account: LocalAccount | None = None, **kw) -> (User, token)`: `account or Account.create()`;
  `wallet_address = account.address.lower()`; token `create_access_token(settings, address=…, user_id, role, auth_time=now)`;
  fixture `User` nesnesine `user._account = account` iliştirir (test yardımcıları imza/gönderim için).
- `seed_assets` → `scripts.seed_assets.seed_assets(db, Deployment(chain_id=10143, vault=FAKE_VAULT, router=FAKE_ROUTER, tokens=[tUSDC(6,base),
  tWETH(18), tWBTC(8)], block_number=0))`; döner `{"tUSDC": Asset, "tWETH": Asset, "tWBTC": Asset}`.
- `siwe_login(client, account) -> token`: `POST /auth/nonce {address}` → `account.sign_message(encode_defunct(text=message))` →
  `POST /auth/verify`.
- `wallet_send(chain, built: dict, account) -> tx_hash`: pre_steps sırayla `chain.send(from, to, data, value)`, sonra ana işlem;
  `api_submit(client, headers, built, account)`: `wallet_send` + `POST /tx/submit {pending_tx_id, tx_hash}`; `api_action(...)`.
- `auth_headers`, `admin_headers` değişmez. `tests/helpers_agreements.py`: `make_agreement` (aynı), `_fx_stub` (aynı),
  `chain_open_and_accept(chain, ag, customer, trader) -> onchain_id` (fake `send` ile).

### 8.2 Silinecek / yeniden yazılacak test dosyaları
| Dosya | Karar |
|---|---|
| `test_anchor.py`, `test_soroban_live.py`, `test_contract_abi.py`, `test_fake_gateways.py` | **silinir** (1d) |
| `conftest.py`, `helpers_agreements.py` | yeniden yazılır (§8.1) |
| `test_auth.py` | yeniden: SIWE (§8.3) |
| `test_agreements_flow.py` | yeniden: build → pre_steps → `wallet_send` → `/tx/submit {tx_hash}`; hata yolları `contract_error`, `receipt_mismatch`, `use_reserved_action` |
| `test_indexer.py` | yeniden: `get_logs` pencereleri, cursor `block_number/last_block_hash`, `failed_events`, Unliquidated/Claimed |
| `test_trading.py` | uyarlanır: `address/symbol` çözümleme, `source="fake"`, `api_amount_out None` |
| `test_wallet.py` | yeniden: `GET /wallet` (native+tokens), `deposit-info` EIP-681, `transfer` (native/ERC-20), `faucet` (limit, disabled) |
| `test_notifications_admin.py` | uyarlanır: `ConfigOut` yeni şekil, admin tx (`from_address = owner`, `apply_router`), `indexer_reset`, `sync-onchain` |
| `test_models.py` | uyarlanır: `wallet_address` unique, `seed_assets` idempotent, anchor testleri çıkar, `Numeric(78,18)` 18 ondalık roundtrip |
| `test_worker.py` | uyarlanır: registry `indexer, pending_tracker, reconciler, expiry, push, fx`; `anchor_sync` yok |
| `test_users.py` | uyarlanır: `wallet_address`, register adres JWT'den, `budget_amount` gizli, profil anonimleştirme |
| `test_amounts.py` | → `tests/chain/test_amounts.py`: `to_raw/from_raw` (6/8/18 dp), `UINT256_MAX`, `too_many_decimals`, `settle_math` invariant |
| `test_dashboard.py` | uyarlanır: `chain.token_balance`; RPC yokken `wallet_error` |
| `test_listings.py` | uyarlanır + kırık `draft` testleri düzeltilir; reserve/release build (§8.3) |
| `test_offers.py` | uyarlanır: `next_action ∈ {open, open_reserved, propose}` |
| `test_discover.py`, `test_messages.py` | değişmez |

### 8.3 Yeni testler
- `tests/chain/test_abi.py`: selector tablosu (spec §5) vs ABI JSON; `decode_revert` (`Error(string)`, `Panic(0x11)`→14, `RouterError(bytes)`
  iç decode, boş); `decode_log` (Opened/Traded/Reserved/ConfigChanged key decode); `encode_call("open", [terms])` selector doğru.
- `tests/chain/test_addresses.py`: küçük/büyük/karışık harf, bozuk checksum 422, `checksum()`.
- `tests/chain/test_fake_gateway.py`: eski `test_fake_gateways.py`'nin Solidity karşılığı — lifecycle, loss/fee, propose/fund/cancel,
  auth (`Unauthorized/NotParty`), trade kuralları, settle üç kademe + `lastValue` tabanı + `ZeroAmount`, **rezervasyon akışı**
  (`reserve → openReserved → settle`, `release(0)` → `ZeroAmount`, `releaseAll`, `ReservationMismatch/Insufficient/Closed`),
  **approve şartı** (allowance yok → `TransferFailed` 29), `auto_mine=False` + `mine()`, `reorg(1)` sonrası `get_receipt None`.
- `tests/test_siwe.py`: nonce mesaj biçimi birebir (02-api §1.1 örneği ile regex), verify başarı; `siwe_domain_mismatch`,
  `siwe_chain_mismatch`, `siwe_expired`, `nonce_used`, `signature_invalid` (başka hesap), mesaj değiştirilmiş (`siwe_invalid`),
  nonce rate limit 429, `refresh` `auth_time` taşıma ve `session_expired` (monkeypatch `jwt_absolute_ttl_days=0`), JWT `sub` küçük harf,
  `/auth/me` checksum adres, `sep10` uçları 404.
- `tests/test_tx_submit.py`: receipt doğrulama (from/to/input/value uyuşmazlığı → 409 ve satır `failed`), idempotent tekrar,
  `tx_hash_conflict`, `invalid_tx_hash`, `auto_mine=False` → `submitted` → `pending_tracker` tamamlar, `not_included` zaman aşımı,
  revert (`status 0`) → `error_code="vault:…"` + `contract_error_code`, kilit süresi (submit sırasında ikinci oturum satırı okuyabilir).
- `tests/test_indexer_reorg.py`: iki blokta olay → `reorg(2)` → tick: `reorg_depth`, cursor geri, `Trade` satırları silinir/yeniden yazılır.
- `tests/test_reservation_flow.py`: ilan → `tx/reserve` (pre_steps approve + reserve) → indexer `Reserved` → ilan `active`; teklif kabul
  `next_action=open_reserved`; `open` → 409 `use_reserved_action`; `open_reserved` → funded; `release` → 409 `reservation_locked`;
  settle sonrası `releaseAll`.
- `tests/test_faucet.py`: mint, günlük limit 429 `next_allowed_at`, `faucet_disabled`, `asset_not_mintable`.
- `tests/test_alembic.py`: `alembic upgrade head` + `compare_metadata` boş (test DB'sinde ayrı şema/DB; `DATABASE_URL_TEST`).
- `tests/chain/test_anvil.py` (`@pytest.mark.chain`, opsiyonel): `ANVIL_RPC_URL` env yoksa skip; `contracts/` deploy betiği ile kurulmuş
  anvil (`anvil --chain-id 10143`) üzerinde `MonadGateway`: `chain_id`, `read_config`, `build_open` (estimate), `send` (anvil hesabıyla
  `eth_sendTransaction`), `get_receipt`, `get_logs` decode, `explain_failure` revert decode. `pyproject.toml` markers:
  `["chain: needs a running anvil with the contracts deployed (ANVIL_RPC_URL)"]`; `live` marker silinir.

### 8.4 Yerel çalıştırma
- `backend/.venv` (Python 3.12.14) — paket listesi boş/eksik olabilir: `.venv/bin/pip install -r requirements-dev.txt`.
- Postgres `127.0.0.1:5434`, kullanıcı `trader` / parola `trader`, DB `traderkirala`, test DB `traderkirala_test`
  (`docker/initdb/01-test-db.sh` `CREATE DATABASE traderkirala_test`).
- Test `.env` örneği (repo dışında, `backend/.env`):
  ```
  APP_ENV=test
  DATABASE_URL=postgresql+asyncpg://trader:trader@127.0.0.1:5434/traderkirala
  DATABASE_URL_TEST=postgresql+asyncpg://trader:trader@127.0.0.1:5434/traderkirala_test
  JWT_SECRET=test-secret-test-secret-test-secret-123456
  ADMIN_KEY=test-admin-key-1234567890
  CHAIN_ID=10143
  RPC_URL=http://127.0.0.1:8545
  VAULT_ADDRESS=0x00000000000000000000000000000000000000f1
  ROUTER_ADDRESS=0x00000000000000000000000000000000000000f2
  PLATFORM_ADDRESS=0x67aD0CaE18528017B79E0aF8C4D3dd5937caFF19
  SIWE_DOMAIN=localhost
  SIWE_URI=http://localhost
  DOCS_ENABLED=true
  ```
- Komutlar: `cd backend && .venv/bin/python -m pytest -q` (fake ile); `.venv/bin/ruff check app tests scripts`;
  `ANVIL_RPC_URL=http://127.0.0.1:8545 .venv/bin/python -m pytest -q -m chain`.

---

## 9. Docker / compose / nginx (Dalga 1b)

- **`docker-compose.yml`:** `name: traderkirala`; `db`: `POSTGRES_DB=traderkirala`, `POSTGRES_USER=trader`, volume `traderkirala_pgdata`,
  network `traderkirala_net`, healthcheck `pg_isready -U trader -d traderkirala`; `api`: image `traderkirala-api:latest`, `ports:
  "127.0.0.1:8013:8000"`, `DATABASE_URL=postgresql+asyncpg://trader:${POSTGRES_PASSWORD}@db:5432/traderkirala`, `UVICORN_WORKERS=2`;
  `worker`: aynı image, `command: ["worker"]`, tek kopya. Stellar compose'u (`mobilapp`, 8012) **dokunulmaz** (K13).
- **`Dockerfile`:** `COPY tests ./tests` satırı **kalkar**; `COPY deployments ./deployments` eklenir (JSON yoksa build kırılmasın diye
  `deployments/.gitkeep`); `postgresql-client` kalır (backup), `curl` kalır (healthcheck).
- **`.dockerignore`:** mevcut + `tests/`, `*.bak`, `src.bak.*`, `_parked/`, `backups/`, `.venv/`, `contracts/`, `docs/`.
- **`docker/entrypoint.sh`:** değişmez (migrate + seed + uvicorn; `--proxy-headers --forwarded-allow-ips='*'` rate limit IP'si için şart).
- **`docker/initdb/01-test-db.sh`:** `CREATE DATABASE traderkirala_test`.
- **`scripts/dev.sh`:** `NET=traderkirala_traderkirala_net`, image `traderkirala-api:latest`, `LIVE_STELLAR` → `ANVIL_RPC_URL`.
- **`deploy/nginx/monadback.yolalapp.com`:** `mobilback` dosyasının kopyası; `server_name monadback.yolalapp.com`; `proxy_pass
  http://127.0.0.1:8013`; zone adları `traderkirala_auth (10r/s, burst 20)` ve `traderkirala_api (60r/s, burst 120)`; `/health` ve
  `/health/chain` rate limitsiz; `proxy_read_timeout 60s` (uzun poll yok; `tx_submit_timeout_seconds=20`); certbot blokları OPS-10'da
  eklenir (`certbot --nginx -d monadback.yolalapp.com`); Cloudflare realip include korunur.
- `scripts/backup.sh` DB adı `traderkirala`, container `traderkirala-db`.

---

## 10. Dalgalar ve "bitti" ölçütleri

| Dalga | Ajanlar | Bitti ölçütü |
|---|---|---|
| 0 (tek ajan, sıralı) | ortak temel | `python -c "import app.services.chain.types, app.services.chain.amounts, app.services.chain.addresses, app.services.chain.errors, app.core.errors, app.schemas.common"`; `ruff check` bu dosyalarda temiz; `pip install -r requirements-dev.txt` başarılı |
| 1 (paralel) | (a) (b) (c) (d) | (a): `import app.services.chain.monad, app.services.chain.fake`; `tests/chain/test_abi.py`, `test_addresses.py`, `test_amounts.py`, `test_fake_gateway.py` yeşil (bu 4 test dosyasını **(a) yazar**, DB gerektirmez). (b): `import app.models, app.core.config`; `alembic upgrade head` boş DB'de geçer; `python -m scripts.seed_assets --file tests/fixtures/deployment.fake.json` çalışır; `docker compose config` geçerli. (c): `import app.services.auth, app.services.siwe, app.core.security, app.core.ratelimit, app.api_deps, app.routers.auth`. (d): `grep -ri "stellar\|soroban\|horizon\|xdr\|sep10\|stroop\|anchor\|friendbot" app/` yalnız Dalga 2 dosyalarında sonuç verir (liste ile raporlar); `import app.routers.config, app.routers.meta, app.schemas.assets`. **Not:** Dalga 1 sonunda `import app.main` **başarısız olabilir** (Dalga 2 dosyaları hâlâ eski); bu beklenir. |
| 2 (paralel) | (e) (f) (g) | `import app.main` başarılı; `uvicorn app.main:app` ayağa kalkar, `GET /health`, `GET /api/v1/config` (fake/boş vault ile) 200; `ruff check app` temiz; her ajan sahip olduğu servislerin bir "smoke" senaryosunu `FakeChainGateway` ile REPL'de çalıştırır (test yazmaz). |
| 3 (sıralı: h → i) | (h) (i) | (h): `pytest -q` yeşil (anvil hariç); `tests/test_alembic.py` yeşil. (i): `ruff check app tests scripts` temiz; DoD-3 grep (`grep -ri "stellar\|soroban\|horizon\|xdr\|sep10\|stroop" app/`) **boş**; `app/services/amounts.py` köprüsü silinmiş; `pip check` temiz. |

Kurallar: bir ajan **yalnız** kendi listesindeki dosyalara yazar; başka bir dosyada değişiklik gerekiyorsa sahibine not bırakır
(`docs/monad/notes/<ajan>.md`, kısa). Yeni dosya ihtiyacı listedeki dizinlerle sınırlıdır. Ortak dosyaların tek sahibi vardır (§11.4).

---

## 11. UYGULAMA PLANI — PARALEL AJANLAR İÇİN DOSYA SAHİPLİĞİ

### 11.1 Dalga 0 — ortak temel (tek ajan, önce biter)
| Dosya | İş |
|---|---|
| `app/services/chain/__init__.py` | §1.1 |
| `app/services/chain/types.py` | §1.2 |
| `app/services/chain/errors.py` | §1.3 |
| `app/services/chain/addresses.py` | §1.4 |
| `app/services/chain/amounts.py` | §1.5 |
| `app/services/amounts.py` | tek satır köprü (§1.5) |
| `app/core/errors.py` | `StellarError` → `ChainError` (`chain_error`); `RateLimitedError(429, rate_limited)` eklenir |
| `app/schemas/common.py` | `Amount` serializer normalize (`format(v.normalize(),"f")`, `0E+…` → `"0"`); `AmountIn = Field(gt=0, decimal_places=18, max_digits=60)`; `Address`, `AddressOut`, `TxHash = Annotated[str, Field(pattern=r"^0x[0-9a-fA-F]{64}$")]`; `STROOP/quantize_amount` silinir |
| `requirements.txt` | `stellar-sdk` çıkar; `web3>=7.6,<8`, `eth-account>=0.13`, `eth-utils>=5`, `hexbytes>=1`; `aiohttp` kalır (web3 async provider); `cryptography` kalır (PyJWT/eth-account) |
| `requirements-dev.txt` | değişmez (`respx` kalır: fx testleri) |
| `pyproject.toml` | marker `chain` (§8.3), `live` silinir |

### 11.2 Dalga 1 (paralel)
**(a) Zincir paketi + ABI + fake + birim testleri**
`app/services/chain/gateway.py`, `abi.py`, `monad.py`, `fake.py`, `app/services/chain/abi/*.json`,
`tests/chain/__init__.py`, `tests/chain/test_abi.py`, `tests/chain/test_addresses.py`, `tests/chain/test_amounts.py`,
`tests/chain/test_fake_gateway.py`, `tests/fixtures/deployment.fake.json` (seed testi için sahte deploy JSON'u).

**(b) Settings + modeller + Alembic + seed + env + Docker/compose**
`app/core/config.py`, `app/models/__init__.py`, `app/models/enums.py`, `app/models/user.py`, `app/models/asset.py`,
`app/models/agreement.py`, `app/models/listing.py`, `app/models/offer.py`, `app/models/trade.py`, `app/models/pending_transaction.py`,
`app/models/indexer_state.py`, `app/models/failed_event.py` (yeni), `app/models/faucet_claim.py` (yeni),
`alembic/versions/20260927_1200_d3e4f5a6b7c8_monad_cutover.py`, `scripts/seed_assets.py`, `scripts/gen_env.py`, `scripts/dev.sh`,
`scripts/backup.sh`, `.env.example`, `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `docker/initdb/01-test-db.sh`,
`docker/entrypoint.sh`, `deploy/nginx/monadback.yolalapp.com` (yeni), `deployments/.gitkeep`.
(`app/models/auth_nonce.py` **(c)'nin**; `app/models/anchor.py` **(d) siler**; migration bu iki tabloyu bu dokümandan yazar.)

**(c) SIWE auth**
`app/services/auth.py`, `app/services/siwe.py` (yeni), `app/core/security.py`, `app/core/ratelimit.py` (yeni), `app/api_deps.py`,
`app/routers/auth.py`, `app/schemas/auth.py`, `app/models/auth_nonce.py`.
Kısıt: `app.services.users` import etmez (§3.3); `User.wallet_address` (b) ile aynı anda hazır olur.

**(d) Silme + config/meta router + legal + README**
Silinenler §6.10 listesi (dosya bazında `git rm`), `app/main.py` (`ROUTER_MODULES`, lifespan logu, `version="2.0.0"`),
`app/routers/config.py`, `app/routers/meta.py`, `app/schemas/assets.py` (`AssetOut/AssetBriefOut`… 02-api §5; `ConfigOut` §6.8),
`app/routers/assets.py` (`chain_id`, `symbol` sıralaması), `app/routers/legal.py`, `README.md`, `docs/ARCHITECTURE.md` (Monad'a göre
kısa yeniden yazım), `docs/DESIGN.md` (üstüne "v1-stellar arşiv" notu), `.gitignore` (`deployments/*.json` **commit edilir**; `_parked`, `.venv` kalır).

### 11.3 Dalga 2 (paralel; Dalga 1 tamamlanınca)
**(e) İşlem servisleri** — `app/services/tx_submit.py`, `app/services/agreements.py`, `app/services/trading.py`,
`app/services/listings.py`, `app/services/offers.py`, `app/services/admin.py`, `app/services/admin_tx.py` (yeni),
`app/routers/tx.py`, `app/routers/agreements.py`, `app/routers/listings.py`, `app/routers/admin.py`, `app/routers/offers.py`,
`app/routers/trades.py`, `app/routers/activity.py`, `app/schemas/tx.py`, `app/schemas/agreements.py`, `app/schemas/trades.py`,
`app/schemas/listings.py`, `app/schemas/offers.py`, `app/schemas/admin.py`, `app/services/activity.py`.
Kısıt: `app.services.indexer` içinden yalnız §11.5'teki kararlı adları import eder.

**(f) Indexer + trader_stats + worker + reconciler** — `app/services/indexer.py`, `app/services/trader_stats.py` (yeni),
`app/worker/main.py`, `app/worker/__init__.py`.
Kısıt: `app.services.agreements` içinden yalnız §6.1 "kararlı dışa aktarımlar"ı import eder; `notifications.notify/notify_many` aynı.

**(g) Cüzdan + panel + kullanıcılar + fx + şemalar** — `app/services/wallet.py`, `app/routers/wallet.py`, `app/schemas/wallet.py`,
`app/services/dashboard.py`, `app/routers/dashboard.py`, `app/schemas/dashboard.py`, `app/services/users.py`, `app/routers/users.py`,
`app/schemas/users.py`, `app/services/fx.py`, `app/services/discover.py` (yalnız `wallet_address/symbol` dokunuşu), `app/schemas/discover.py`,
`app/services/notifications.py`, `app/services/messages.py`, `app/services/ratings.py` (yalnız alan adı dokunuşları varsa).

### 11.4 Ortak dosyaların tek sahibi
| Dosya | Sahip | Not |
|---|---|---|
| `app/models/__init__.py` | (b) | anchor çıkar, `FailedEvent`, `FaucetClaim`, `ReservationStatus` girer |
| `app/main.py` | (d) | `ROUTER_MODULES`, sürüm, lifespan |
| `app/schemas/common.py` | Dalga 0 | sonra dokunulmaz |
| `app/core/errors.py` | Dalga 0 | sonra dokunulmaz |
| `app/api_deps.py` | (c) | `ChainDep`; (e)/(g) yalnız import eder |
| `app/services/amounts.py` | Dalga 0 yazar, (i) siler | Dalga 2 kimse import etmez |
| `requirements*.txt`, `pyproject.toml` | Dalga 0 | (i) yalnız `ruff` düzeltmesi |
| `tests/conftest.py`, `tests/helpers_agreements.py` | (h) | Dalga 1'de (a) yalnız `tests/chain/*` yazar (DB'siz) |

### 11.5 Dalga 2 içi kararlı arayüzler (iki taraf da buna göre kodlar)
- `app.services.indexer` → (e) kullanır: `IndexContext(db, settings, chain)`, `apply_event(ctx, record, *, actor_user_id=None) -> bool`,
  `apply_tx_result(ctx, pending, receipt, *, actor_user_id=None) -> dict`, `finalize_receipt(ctx, pending_id, receipt, *, actor_user_id) ->
  PendingTransaction`, `expire_pending(ctx) -> int`, `track_submitted(ctx, *, limit=25) -> int`, `refresh_agreement_from_chain(ctx, ag, *,
  snapshot=True, alerts=True, now=None) -> dict`, `event_out(record) -> dict`, `run_indexer_once`, `run_reconcile_once`.
- `app.services.agreements` → (f)/(g) kullanır: §6.1 listesi; `record_pending(db, user, kind, agreement, unsigned, payload=None, *, listing=None)`.
- `app.services.trader_stats.refresh_trader_stats(db, trader_id)` → (e)/(g) kullanır.
- `app.services.trading.symbol_label(token_in, token_out, base)`, `trade_out(trade, agreement=None)` → (f)/(g) kullanır.
- `app.services.users.get_by_wallet_address(db, address)` → (e) kullanır.

### 11.6 Dalga 3 (sıralı)
**(h) Testler** — `tests/**` (Dalga 1a'nın `tests/chain/*` dosyaları dahil, gerekirse günceller); test yazarken bulduğu hatayı ilgili
`app/` dosyasında düzeltebilir (tek ajan çalıştığı için çakışma yok); `tests/test_alembic.py`.
**(i) Temizlik** — `ruff check --fix` tüm proje; DoD grep; `app/services/amounts.py` silinir; ölü importlar; `docs/monad/notes/*` kapanır.

---

## 12. Karar değişikliği önerileri (spec mevcut karara göre yazıldı)
1. **02-api §10 `IndexerStatusOut`'a `failed_events: int` alanı** (opsiyonel, geriye uyumlu): `failed_events` tablosundaki çözülmemiş satır
   sayısı operatöre görünsün. Ayrıca opsiyonel `POST /admin/indexer/retry-failed`. Kabul edilirse 02-api'ye işlenir; kabul edilmezse yalnız log.
2. **"Servisler flush-only, oturumu request commit eder" kuralına istisna:** `tx_submit.submit_hash` ve `finalize_receipt` (§4.3) açık
   `commit()` çağırır; aksi hâlde satır kilidi receipt beklemesi boyunca tutulur (00-inceleme §B hata 2). Alternatif (ayrı oturum açmak)
   `get_db` bağımlılığıyla çakışır.
3. **Explorer varsayılanı** 02-api'nin `testnet.monadexplorer.com` varsayımı yerine 06-doküman ile doğrulanmış `https://testnet.monadvision.com`.
4. **`pending_tracker` ayrı worker job'u** (indexer tick'inin içinde değil): indexer RPC hatasıyla dursa da receipt takibi sürer.
5. **BE-23'ün "teklif kabul edilince ilan `matched`, diğer teklifler `rejected`" kısmı Sprint 3'e** — `ListingStatus`'a yeni değer ve keşfet
   sorgularının değişmesi gerekir; bu sprintte `amount_locked` ve `max_loss_bps` gevşetme reddi yapılır.
6. **`Agreement.platform_fee_bps` ve `Agreement.vault_address` kolonları** (02-api'de `vault_address` çıktı alanı var; kolon değil): ileride
   vault değişiminde eski sözleşmelerin doğru kontrata bağlı kalması için satırda tutulur.

## 13. Test notu
Kullanıcı talimatı: testler **en sona**. Dalga 1a'nın `tests/chain/*` birim testleri DB ve ağ gerektirmez ve fake/ABI'nin doğruluğunu
ölçtüğü için istisnadır (kod yazımını beklemez). Zincire dokunan tüm entegrasyon testleri (`tests/chain/test_anvil.py`) ve gerçek Monad
Testnet denemeleri cüzdanlara MON geldikten sonra (OPS-03 faucet adımı) yapılır; o güne kadar `FakeChainGateway` tek referanstır.
