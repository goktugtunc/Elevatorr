# 02 — HTTP API sözleşmesi (Monad geçişi)

Backend (`backend/`, FastAPI) ile frontend'in (`app/`, Expo) **ortak uyacağı** sözleşme. Yalnızca Monad
geçişiyle **değişen, eklenen ve silinen** kısımlar yazılıdır; dokunulmayan uçlar §12'de liste olarak durur.

Kaynaklar: `backend/app/routers/*.py`, `backend/app/schemas/*.py` (v1-stellar tabanı), `app/src/lib/api/endpoints.ts`
ve `types.ts`, `docs/monad/00-inceleme.md` §D, `SPRINT-2-MONAD.md` §2 (K1–K13 bağlayıcı). Bu doküman mevcut alan adlarını
korur; yeniden adlandırma yalnızca Stellar'a özgü bir kavram taşıyan alanlarda yapılır (`stellar_address`, `contract_id`,
`ledger`, `public_key`, `unsigned_xdr`, `network_passphrase`, `issuer`, `canonical`).

Hedef ağ: **Monad Testnet**, `chain_id = 10143`, RPC `https://testnet-rpc.monad.xyz`, yerel token MON (18 ondalık), MON
yalnızca gas içindir (K6). Taban varlık tUSDC (6 ondalık).

Öncelik sırası: bu doküman > `00-inceleme.md` > eski `docs/api-entegrasyon.md` (Stellar; artık referans değil).

---

## 0. Genel kurallar (tüm uçlar)

| Konu | Kural |
|---|---|
| Önek | `/api/v1` (`settings.api_prefix`). `/health*` ve `/legal/*` öneksiz. |
| Kimlik | `Authorization: Bearer <jwt>`. Admin uçları ek olarak `X-Admin-Key`. |
| Adres (K11) | Girişte küçük harf **ve** EIP-55 karşık harf kabul edilir; karışık harf ise checksum doğrulanır (bozuksa 422 `invalid_address`). DB'de küçük harf. **Tüm çıktılarda EIP-55 checksum.** Karşılaştırmalar küçük harfle. Regex: `^0x[0-9a-fA-F]{40}$`. |
| Tutarlar (K12) | API'de **string**, insan okunur birim, üssüz ondalık (`"1000.50"`, `"0.000001"`). Ham (uint256) değer gerektiğinde alan adı `*_raw` ve string tam sayı (`"1000500000"`). Ondalık sayısı = ilgili `asset.decimals`. Girişte `asset.decimals`'tan fazla basamak → 422 `too_many_decimals`. `AmountIn`: `> 0`, en fazla 18 ondalık, 60 basamak. |
| Oranlar | bps, `int` (değişmez). |
| Tx hash | `0x` + 64 hex = **66 karakter**. Blok bilgisi `block_number: int`. Event sırası `log_index: int`. |
| Sayfalama | `limit` (1–100, varsayılan 20) / `offset` (≥0) sorgu; yanıt `Page[T] = {items, total, limit, offset}` (değişmez). İstisnalar korunur: `GET /discover` `cursor/next_cursor`, `GET /conversations/{id}/messages` `after/before/has_more`. |
| Hata gövdesi | `{code, message, details}` (değişmez). 422 doğrulama: `{code:"validation_error", message:"Invalid request", details:{errors:[...]}}`. |
| Zaman | ISO 8601 UTC (`2026-09-26T12:00:00Z`). |
| Explorer | İstemci URL'i `config.chain.explorer_url` üzerinden kurar: tx `"{explorer_url}/tx/{hash}"`, adres `"{explorer_url}/address/{addr}"`. Sunucu tarafında hazır `explorer_url` alanı yalnızca `TxStatusOut`, `TradeOut`, `WalletTransferOut`, `FaucetOut` içinde döner. |
| operationId | FastAPI varsayılanı: `{handler_adı}{path}` → `\W` → `_`, sonuna `_{method}`. Örn. `create_nonce_api_v1_auth_nonce_post`. Handler adları ve path şablonları **korunur** (§14); frontend tipleri `openapi-typescript` ile `/openapi.json`'dan üretilir. |

### 0.1 Hata kodları — yeni, yeniden adlandırılan, silinen

| Kod | HTTP | Ne zaman | Not |
|---|---|---|---|
| `chain_error` | 502 | RPC erişilemiyor / zaman aşımı | eski `stellar_error` yerine |
| `rate_limited` | 429 | `/auth/nonce` IP limiti, `/wallet/faucet` günlük limit | `details.retry_after_seconds`, faucet'te `details.next_allowed_at` |
| `invalid_address` | 422 | 0x adres değil ya da checksum bozuk | eski `invalid_public_key` yerine |
| `siwe_invalid` | 401 | mesaj EIP-4361 olarak parse edilemiyor | |
| `siwe_domain_mismatch` / `siwe_uri_mismatch` / `siwe_chain_mismatch` | 401 | mesajdaki domain/URI/chainId sunucununkiyle eşleşmiyor | |
| `siwe_expired` / `siwe_not_yet_valid` | 401 | mesajdaki `Expiration Time` geçmiş / `Not Before` gelmemiş | |
| `nonce_invalid` / `nonce_used` / `nonce_expired` | 401 | nonce DB'de yok / yakılmış / süresi geçmiş | korunur |
| `signature_invalid` | 401 | EIP-191 recover başarısız ya da adres mesajdakinden farklı | korunur |
| `session_expired` | 401 | `/auth/refresh`: ilk girişten 30 gün geçti | **istemci refresh'i tekrar denemez, girişe döner** |
| `token_expired` / `token_invalid` / `missing_token` / `not_registered` / `account_disabled` | 401/403 | değişmez | |
| `pending_tx_not_found` / `not_owner` / `pending_tx_expired` | 404/403/409 | değişmez | |
| `invalid_tx_hash` | 422 | 66 karakter `0x` hex değil | yeni |
| `tx_hash_conflict` | 409 | pending satırında başka bir hash var ya da hash başka bir pending satırına bağlı | yeni |
| `receipt_mismatch` | 409 | receipt'te `from`/`to`/`input` beklenenle eşleşmiyor | yeni; satır `failed` olur |
| `use_reserved_action` | 409 | `open`/`fund` istendi ama ilanın rezervasyonu anaparayı karşılıyor | `details.action` = `open_reserved`/`fund_reserved`, `details.reservation_id` |
| `reservation_insufficient` | 409 | `open_reserved`/`fund_reserved` istendi ama rezervasyon yok/yetersiz | `details.reservation_id`, `reserved_amount`, `principal` |
| `asset_required` | 422 | `claim` için `asset_id` yok | |
| `asset_not_mintable` / `faucet_disabled` | 422/503 | faucet | yeni |
| `invalid_contract_id`, `invalid_issuer`, `sep10_invalid`, `invalid_xdr`, `unsigned_xdr`, `xdr_mismatch`, `trustline_required`, `anchor_*` | — | **silindi** | |

---

## 1. Kimlik doğrulama — SIWE (EIP-4361), K2

Akış: `POST /auth/nonce {address}` → sunucu **mesaj metnini üretir** → cüzdan `personal_sign(message)` →
`POST /auth/verify {message, signature}` → JWT. İstemci mesajı **kendi kurmaz**; sunucunun verdiği `message` string'ini
byte'ı byte'ına imzalar ve aynen geri gönderir. EIP-1271 (akıllı hesap) bu sprintte **yok**; yalnızca EOA (EIP-191).

### 1.1 `POST /auth/nonce` — `create_nonce`

Auth: yok. Rate limit: IP başına 20/dk → 429 `rate_limited`.

İstek `NonceIn`:
```json
{ "address": "0x67ad0cae18528017b79e0af8c4d3dd5937caff19" }
```
`address`: 0x adres, küçük harf ya da checksum (eski alan `public_key` **silindi**).

Yanıt `NonceOut`:
```json
{
  "nonce": "6f1c9e2ab84d4f7e9c0b3a5d2e8f1a47",
  "message": "monadback.yolalapp.com wants you to sign in with your Ethereum account:\n0x67aD0CaE18528017B79E0aF8C4D3dd5937caFF19\n\nTraderKirala'ya giriş yap. Bu imza işlem yapmaz, gas harcamaz.\n\nURI: https://monadback.yolalapp.com\nVersion: 1\nChain ID: 10143\nNonce: 6f1c9e2ab84d4f7e9c0b3a5d2e8f1a47\nIssued At: 2026-09-26T12:00:00.000Z\nExpiration Time: 2026-09-26T12:05:00.000Z",
  "expires_at": "2026-09-26T12:05:00Z",
  "chain_id": 10143,
  "domain": "monadback.yolalapp.com"
}
```
Sunucu kuralları: nonce 16 byte hex (32 alfasayısal karakter, EIP-4361 ≥ 8 şartını sağlar), TTL
`settings.auth_nonce_ttl_seconds` (300). Mesajdaki adres **checksum** biçiminde yazılır (EIP-4361 gereği). `domain`,
`uri`, `statement` `settings.siwe_domain / siwe_uri / siwe_statement`'tan gelir ve `/config.auth` ile aynıdır.
DB: `auth_nonces.address String(42)` küçük harf (eski `public_key String(56)`).

Hatalar: 422 `invalid_address`, 429 `rate_limited`.

### 1.2 `POST /auth/verify` — `verify_nonce`

Auth: yok.

İstek `VerifyIn`:
```json
{
  "message": "<NonceOut.message birebir>",
  "signature": "0x8d3f…c41b"
}
```
`signature`: 65 byte EIP-191 imzası, `0x` + 130 hex. (Eski `public_key`, `nonce` alanları **silindi**; ikisi de mesajın içinde.)

Sunucu doğrulama sırası (her adım ayrı kod döner):
1. Mesaj EIP-4361 olarak parse edilir → değilse `siwe_invalid`.
2. `domain == siwe_domain` → `siwe_domain_mismatch`; `uri == siwe_uri` → `siwe_uri_mismatch`; `chain_id == 10143` → `siwe_chain_mismatch`.
3. `Expiration Time` geçmemiş → `siwe_expired`; varsa `Not Before` gelmiş → `siwe_not_yet_valid`.
4. Nonce DB'de var, `used_at IS NULL`, `expires_at > now` (SELECT … FOR UPDATE) → `nonce_invalid` / `nonce_used` / `nonce_expired`. Nonce satırındaki adres mesajdaki adrese eşit (küçük harf) → değilse `nonce_invalid`.
5. `Account.recover_message(message, signature)` == mesajdaki adres (küçük harf karşılaştırma) → değilse `signature_invalid`.
6. Nonce yakılır (`used_at = now`), kullanıcı aranır, `last_login_at` güncellenir, JWT üretilir.

Yanıt `LoginOut` (`/auth/verify` ve `/auth/refresh` ortak):
```json
{
  "token": "eyJhbGciOiJIUzI1NiIs…",
  "expires_at": "2026-10-03T12:00:04Z",
  "address": "0x67aD0CaE18528017B79E0aF8C4D3dd5937caFF19",
  "registered": true,
  "user": { "…MeOut (§6)…" }
}
```
`registered=false` iken `user=null`; istemci `POST /users/register`'a gider. Alan `public_key` → **`address`** (checksum).

### 1.3 `POST /auth/refresh` — `refresh`

Auth: geçerli (süresi dolmamış) Bearer. Yanıt `LoginOut`. Değişen davranış:
- JWT'ye `auth_time` (ilk `/auth/verify` anı, epoch saniye) ve `jti` claim'leri eklenir; refresh **`auth_time`'ı taşır**, yenilemez.
- `now - auth_time > settings.refresh_max_age_seconds` (varsayılan 30 gün) → 401 `session_expired`. İstemci bu kodda oturumu kapatır, yeniden SIWE yapar.
- Süresi dolmuş token ile refresh → 401 `token_expired` (değişmez). Frontend: refresh isteği 401 köprüsünü **atlar** (§13).

### 1.4 `GET /auth/me` — `me`

Davranış değişmez. `AuthMeOut.public_key` → **`address`** (checksum):
```json
{ "address": "0x67aD0…FF19", "registered": true, "user": { "…MeOut…" }, "token_expires_at": "2026-10-03T12:00:04Z" }
```

### 1.5 JWT claim'leri (HS256, `core/security.py`)

| Claim | Değer |
|---|---|
| `sub` | cüzdan adresi **küçük harf** |
| `uid` | user id (kayıtlıysa) |
| `role` | `customer` / `trader` / null |
| `iat`, `exp` | `exp = iat + access_token_ttl_seconds` (7 gün) |
| `jti` | uuid hex (var; korunur) |
| `iss` | `siwe_domain` |
| `auth_time` | **yeni**; ilk SIWE doğrulama anı, refresh'te değişmez |

### 1.6 Silinen: `GET /auth/sep10` (`sep10_challenge`), `POST /auth/sep10` (`sep10_verify`) — bkz. §11.

---

## 2. İşlem modeli (K3, K4)

Backend **imzasız calldata** döner; cüzdan `eth_sendTransaction` ile yayınlar; istemci hash'i `POST /tx/submit` ile bildirir;
backend receipt'i alır, `from/to/input` doğrular, sonucu uygular. Relayer / gas sponsorluğu yok.

### 2.1 `UnsignedTxOut`

Tüm `…/tx/…` build uçlarının ortak yanıtı.

```json
{
  "pending_tx_id": "0c7d6e0e-4a1b-4a6e-9f39-2b1f8f6f1a10",
  "kind": "open",
  "action": "open",
  "agreement_id": "6b5a…", 
  "listing_id": null,
  "chain_id": 10143,
  "from_address": "0x67aD0CaE18528017B79E0aF8C4D3dd5937caFF19",
  "to": "0x1111111111111111111111111111111111111111",
  "data": "0x8f2c1a3b00000000000000000000000067ad0c…",
  "value": "0",
  "gas": "312000",
  "description": "500 tUSDC anaparayı kasaya kilitle (sözleşme #12)",
  "pre_steps": [
    {
      "kind": "approve",
      "to": "0x2222222222222222222222222222222222222222",
      "data": "0x095ea7b3000000000000000000000000111111…",
      "value": "0",
      "gas": "60000",
      "description": "Kasaya 500 tUSDC harcama izni ver",
      "spender": "0x1111111111111111111111111111111111111111",
      "asset_id": "9a1f…",
      "symbol": "tUSDC",
      "amount": "500",
      "amount_raw": "500000000"
    }
  ],
  "summary": { "reservation_id": null, "role": "customer" },
  "expires_at": "2026-09-26T12:15:00Z"
}
```

| Alan | Tip | Açıklama |
|---|---|---|
| `pending_tx_id` | uuid | `pending_transactions.id`; `/tx/submit` ve `GET /tx/{id}` anahtarı. Admin uçlarında `null`. |
| `kind` | `PendingTxKind` | Alan adı korunur. Yeni değer kümesi: `open, open_reserved, propose, fund, fund_reserved, accept, cancel, settle, claim, trade, reserve, release, transfer, admin`. Silinen: `payment`, `trustline`. |
| `action` | string | **ABI'deki fonksiyon adı** (camelCase): `open`, `openReserved`, `propose`, `fund`, `fundReserved`, `accept`, `cancel`, `settle`, `claim`, `trade`, `reserve`, `release`, `releaseAll`, ERC-20 `transfer`, native için `"transfer"`, admin için `setToken` vb. İstemci calldata'yı bununla decode/gösterir. |
| `agreement_id`, `listing_id` | uuid\|null | bağlam (ikisi de opsiyonel) |
| `chain_id` | int | 10143; cüzdan zinciri farklıysa istemci `wallet_switchEthereumChain` |
| `from_address` | string | göndermesi gereken hesap (çağıran; checksum). Eski `source`. |
| `to` | string | vault / token / alıcı adresi (checksum) |
| `data` | string | `0x` hex calldata; native transferde `"0x"` |
| `value` | string | wei, ondalık string; ERC-20 ve vault çağrılarında `"0"` |
| `gas` | string | `eth_estimateGas × 1.2`, ondalık string. İstemci `BigInt(gas)`. Cüzdan yine kendi tahminini kullanabilir. |
| `description` | string | Türkçe, tek cümle, "ne imzalıyorum?" |
| `pre_steps` | `PreStepOut[]` | **sırayla, her biri ayrı `eth_sendTransaction`**; ana işlem bunlar onaylanmadan gönderilmez. Allowance yeterliyse boş. Backend pre_step'leri takip etmez (hash bildirilmez). |
| `summary` | object | Korunur; yapılandırılmış bağlam (settle bacakları/slippage, trade notu, reservation_id). Boş `{}` olabilir. |
| `expires_at` | datetime | Pending satırının imza için son anı (15 dk). Sonrasında `/tx/submit` → 409 `pending_tx_expired`; aynı türde yeni build eskisini `expired` yapar. |

Silinen alanlar: `unsigned_xdr`, `network_passphrase`, `tx_hash` (yapım anında bilinmez), `source`.

### 2.2 `PreStepOut`

`{kind:"approve", to (token adresi), data (approve(spender, amount_raw)), value:"0", gas, description, spender (vault),
asset_id, symbol, amount, amount_raw}`. Tutar **tam principal** kadardır, sonsuz değil (K4). Mevcut allowance ≥ gereken ise
adım hiç gelmez; USDT-tipi "önce 0'a çek" tokenları için iki `approve` adımı gelebilir (test tokenlarımızda yok).

### 2.3 `POST /tx/submit` — `submit`

Auth: Bearer (pending satırının sahibi ya da admin).

İstek `TxSubmitIn`:
```json
{ "pending_tx_id": "0c7d6e0e-…", "tx_hash": "0x5e1c…9a7b" }
```
(eski `signed_xdr` **silindi**.)

Sunucu:
1. Satır sahibi mi (`not_owner`), süresi geçmiş mi (`pending_tx_expired`), hash biçimi (`invalid_tx_hash`).
2. Satır `confirmed`/`failed` ise **idempotent**: mevcut durum döner. Satırda farklı bir hash varsa ya da hash başka satıra bağlıysa 409 `tx_hash_conflict`.
3. `status=submitted`, `tx_hash`, `submitted_at` yazılır ve **commit edilir** (kilit poll boyunca tutulmaz — inceleme §B hata 2).
4. `eth_getTransactionByHash` + receipt için en fazla `settings.tx_submit_timeout_seconds` (Monad için 20 sn önerilir) beklenir. Receipt yoksa yanıt `status="submitted"`; worker `pending_tracker` takip eder, istemci `GET /tx/{id}` ile sorar.
5. Receipt gelince: `tx.from == user.wallet_address`, `tx.to == pending.to_address`, `tx.input == pending.calldata`, `tx.value == pending.value` (hepsi küçük harf/bytes eşitliği). Uyuşmazlık → satır `failed`, `error_code="receipt_mismatch"`, HTTP 409 `receipt_mismatch`.
6. `receipt.status == 1` → event'ler decode edilir, `apply_tx_result` (mirror + bildirim), satır `confirmed`. `status == 0` → `eth_call` ile revert nedeni alınır; custom error selector → `VaultError` adı (§2.6); satır `failed`.

Yanıt `TxStatusOut` (HTTP 200; başarısız işlem de 200 + `status:"failed"` döner, satır durumu korunsun diye):
```json
{
  "pending_tx_id": "0c7d6e0e-…",
  "kind": "open",
  "action": "open",
  "status": "confirmed",
  "tx_hash": "0x5e1c…9a7b",
  "block_number": 18342211,
  "confirmations": 3,
  "error_code": null,
  "error_message": null,
  "contract_error_code": null,
  "explorer_url": "https://testnet.monadexplorer.com/tx/0x5e1c…9a7b",
  "agreement_id": "6b5a…",
  "agreement_status": "funded",
  "onchain_id": 12,
  "trade_id": null,
  "events": [ { "name": "Opened", "args": { "id": 12, "customer": "0x67aD…", "principal_raw": "500000000" } } ],
  "submitted_at": "2026-09-26T12:01:10Z",
  "updated_at": "2026-09-26T12:01:12Z"
}
```

### 2.4 `GET /tx/{pending_id}` — `get_pending`

Aynı `TxStatusOut`. Path parametresi adı `pending_id` **korunur** (operationId `get_pending_api_v1_tx__pending_id__get`).
`submitted` satırlarda her çağrıda receipt sorulur (ucuz). Eski `PendingTxOut` (`unsigned_xdr`, `payload`, `result`, `is_expired`) **silindi**;
imzasız veriye tekrar ihtiyaç varsa build ucu yeniden çağrılır.

### 2.5 Durum makinesi

| `status` | Anlamı | DB (`pending_transactions.status`) |
|---|---|---|
| `pending` | build edildi, cüzdan henüz göndermedi | `pending` (eski `built`) |
| `submitted` | hash bildirildi, receipt yok | `submitted` |
| `confirmed` | receipt `status=1`, mirror güncellendi | `confirmed` (eski `success`) |
| `failed` | revert, receipt uyuşmazlığı, düşme (reorg), zaman aşımı | `failed` |
| `expired` | `expires_at` geçti, hiç gönderilmedi | `expired` |

Geçişler: `pending → submitted → confirmed|failed`, `pending → expired`. `submitted` satırı 30 dk içinde receipt almazsa
worker `failed` + `error_code="not_included"` yapar. Zincirde gerçekleşen bir olay pending satırından bağımsız olarak
indexer tarafından da işlenir (idempotent); yani hash bildirimi kaçsa bile sözleşme durumu düzelir.

### 2.6 `error_code` sözlüğü

`error_code` string, `error_message` insan okunur. Kontrat hataları `"vault:<Ad>"`, sayısal kodu ayrıca `contract_error_code`
(1–21, mobil eşleme için korunur). Solidity'de custom error adları Rust enum'ıyla **aynı**:

| Kod | Ad | Kod | Ad | Kod | Ad |
|---|---|---|---|---|---|
| 1 | NotInitialized | 8 | Expired | 15 | InvalidAmount |
| 2 | Unauthorized | 9 | NotExpired | 16 | RouterError |
| 3 | Paused | 10 | InsufficientBalance | 17 | NotParty |
| 4 | InvalidTerms | 11 | TooManyTokens | 18 | ReservationNotFound |
| 5 | TokenNotAllowed | 12 | DrawdownBreached | 19 | ReservationClosed |
| 6 | NotFound | 13 | SlippageExceeded | 20 | ReservationInsufficient |
| 7 | WrongStatus | 14 | Overflow | 21 | ReservationMismatch |

Kontrat dışı: `erc20:<reason>` (ör. `erc20:ERC20InsufficientAllowance`), `reverted` (neden çözülemedi), `receipt_mismatch`,
`not_included`, `reorged`, `expired`, `out_of_gas`.

### 2.7 İstemci akışı (referans, `lib/chain/tx.ts`)

```
res = POST build ucu → UnsignedTxOut
if wallet.chainId != res.chain_id → switchChain(res.chain_id)
for step in res.pre_steps: hash = sendTransaction(step); await waitForReceipt(hash)   // approve
hash = sendTransaction({to, data, value, gas, from: from_address})
st = POST /tx/submit {pending_tx_id, tx_hash: hash}
while st.status in (pending, submitted): sleep(2s); st = GET /tx/{pending_tx_id}
UI: pending="imza bekleniyor", submitted="gönderildi", confirmed="onaylandı", failed=error_message; her durumda explorer_url
```
Cüzdan 4001 → istemci `USER_REJECTED`, pending satırı dokunulmaz (süresi dolar).

---

## 3. `UnsignedTxOut` döndüren uçlar

### 3.1 `POST /agreements/{agreement_id}/tx/{action}` — `build_action`

Auth: Bearer. `action` path enum `TxAction`:
`propose | open | open_reserved | fund | fund_reserved | accept | cancel | settle | claim` (yeni: `open_reserved`, `fund_reserved`, `claim`; `trade` ayrı uçta).

İstek gövdesi `TxActionIn` (opsiyonel, JSON):
```json
{ "slippage_bps": 100, "asset_id": null }
```
- `slippage_bps` (0–5000): yalnız `settle`; her `min_out = quote × (1 − bps)`; varsayılan `config.settle_slippage_bps`.
- `asset_id` (uuid): yalnız `claim`; alınacak token. Yoksa 422 `asset_required`.

Rol/durum kuralları ve zincir çağrısı:

| action | Durum | Kim | Kontrat | pre_steps |
|---|---|---|---|---|
| `propose` | draft | trader | `propose(terms)` | — |
| `open` | draft | customer | `open(terms)` | `approve(vault, principal)` |
| `open_reserved` | draft | customer | `openReserved(terms, reservationId)` | — |
| `fund` | proposed | customer | `fund(id)` | `approve(vault, principal)` |
| `fund_reserved` | proposed | customer | `fundReserved(id, reservationId)` | — |
| `accept` | funded | trader | `accept(id)` | — |
| `cancel` | proposed (proposer) / funded (iki taraf) | | `cancel(id)` | — |
| `settle` | active | taraflar her an; admin `end_time` sonrası; herkes `end_time + 7 gün` sonrası | `settle(id, minOuts[])` | — |
| `claim` | settled | customer | `claim(id, token)` | — |

Rezervasyon seçimi **açıktır**, sessiz düşme yok (inceleme §C hata 3): `AgreementOut.available_actions` `open` **ya da**
`open_reserved` (`fund` ya da `fund_reserved`) içerir; istemci onu gönderir. `open` istenip rezervasyon anaparayı
karşılıyorsa 409 `use_reserved_action`; `open_reserved` istenip rezervasyon yok/yetersizse 409 `reservation_insufficient`.

Hatalar: 404 `agreement_not_found`, 409 `invalid_state` (`details.status/action`), 403 `wrong_party`, 403 `not_expired`
(taraf olmayan settle), 409 `not_onchain`, 422 `invalid_action`, 502 `chain_error` (gas tahmini/quote başarısız — revert
nedeni `details.error_code` ile).

### 3.2 `POST /agreements/{agreement_id}/tx/trade` — `build_trade`

Auth: Bearer (trader). İstek `TradeTxIn` (alanlar korunur; referans anlamı değişir):
```json
{
  "token_in": "tUSDC",
  "token_out": "0x3333333333333333333333333333333333333333",
  "amount_in": "100",
  "slippage_bps": 100,
  "note": "ETH long",
  "notify_investors": true,
  "deadline_seconds": 300
}
```
`token_in/token_out`: asset uuid **ya da** 0x adres **ya da** symbol (eski "C… contract id or code"). `amount_in` en fazla
`token_in.decimals` ondalık. Yanıt `UnsignedTxOut{kind:"trade", action:"trade"}`; `summary` = `{token_in, token_out,
amount_in, min_out, min_out_raw, slippage_bps, deadline, note, notify_investors}`. pre_steps yok (vault swap'ı kendi içinde
`forceApprove` eder).

`GET /agreements/{id}/quote` (`quote`) sorgu parametreleri değişmez; `token_in/token_out` aynı referans kuralı; `amount_in`
açıklaması "en fazla asset.decimals ondalık". `QuoteOut.source`: `"router"` (eth_call `getAmountsOut`) | `"fake"`; `api_amount_out`
her zaman `null` (Soroswap API yok; alan geriye uyum için kalır).

### 3.3 `POST /listings/{listing_id}/tx/reserve` — `reserve_listing_capital`

Auth: Bearer (ilan sahibi, customer, `kind=capital`, `status=draft|active|paused`). Gövde yok.
Zincir: `reserve(token, amountRaw, listingRef)`; `pre_steps: [approve(vault, amount)]`. `summary = {listing_id, amount,
amount_raw, asset_id, listing_ref}`. İlan indexer `Reserved` olayını işleyince `draft → active` olur (değişmez).

### 3.4 `POST /listings/{listing_id}/tx/release` — `release_listing_capital`

Auth: Bearer (ilan sahibi). **Yeni opsiyonel gövde** `ReleaseIn`:
```json
{ "amount": "250" }
```
- gövde yok / `amount: null` → `releaseAll(reservationId)` (`action:"releaseAll"`).
- `amount > 0` → `release(reservationId, amountRaw)` (`action:"release"`). `amount == 0` → 422 (SC-04: kontrat da reddeder).
Hatalar: 409 `no_reservation`, 409 `reservation_locked` (açık sözleşme rezervden çekiyor ve kalan yetmiyor).

### 3.5 `POST /wallet/tx/transfer` — `transfer` (yeni; `payment` yerine)

Auth: Bearer. İstek `WalletTransferIn`:
```json
{ "asset_id": "9a1f…", "to": "0xAbC…", "amount": "25.5" }
```
- `asset_id: null` → **native MON**: `to=alıcı`, `data="0x"`, `value=wei`, `action:"transfer"`, `kind:"transfer"`.
- `asset_id` verildi → ERC-20: `to=token adresi`, `data=transfer(alıcı, amountRaw)`, `value="0"`.
Silinen alanlar: `asset_code`, `asset_issuer`, `memo`, `memo_type`. Hatalar: 422 `invalid_address`, 400 `insufficient_funds`
(bakiye ya da gas için MON yok; `details.needed`, `details.available`).

### 3.6 Admin: `POST /admin/contract/tx/{fn}` — `tx_set_token`, `tx_set_paused`, `tx_set_fees`, `tx_set_router`, `tx_set_settle_slippage`, **`tx_apply_router` (yeni)**

Auth: `X-Admin-Key`. Kontrat sahibi (`owner`) operatör cüzdanıdır; sunucuda anahtar yok (OPS-11). Yanıt `AdminTxOut` =
`UnsignedTxOut` alanları + `{function, args}`; `pending_tx_id: null`, `kind:"admin"`, `from_address = contract.owner`.

| Uç | İstek gövdesi | `action` |
|---|---|---|
| `tx/set_token` | `SetTokenIn {token: "<asset uuid \| 0x adres>", allowed: true, is_base: false}` | `setToken` |
| `tx/set_paused` | `SetPausedIn {paused: bool}` | `setPaused` |
| `tx/set_fees` | `SetFeesIn {platform_fee_bps: 0–1000, fee_recipient: "0x…" \| null}` (null → `PLATFORM_ADDRESS`) | `setFees` |
| `tx/set_router` | `SetRouterIn {router: "0x…"}` (42 kr; eski 56) | `proposeRouter` (SC-08: iki adım, 24 sa) |
| `tx/apply_router` | gövde yok | `applyRouter` |
| `tx/set_settle_slippage` | `SetSettleSlippageIn {bps: 0–5000}` | `setSettleSlippage` |

Örnek `AdminTxOut`:
```json
{
  "function": "set_token", "args": {"token": "0x2222…", "allowed": true, "is_base": true},
  "pending_tx_id": null, "kind": "admin", "action": "setToken",
  "chain_id": 10143, "from_address": "0x67aD0…FF19", "to": "0x1111…1111",
  "data": "0x…", "value": "0", "gas": "72000",
  "description": "tUSDC tokenını allow-list'e ekle (taban)", "pre_steps": [], "summary": {}, "expires_at": null,
  "note": "Sign with the owner wallet and POST /admin/contract/tx/submit {tx_hash}"
}
```
Silinen: `contract_id` (→ `vault_address` alanı eklenir), `source`, `unsigned_xdr`, `network_passphrase`, `tx_hash`.

`POST /admin/contract/tx/submit` — `tx_submit`: istek `AdminTxSubmitIn {tx_hash}` (eski `signed_xdr` silindi). Receipt
beklenir (aynı zaman aşımı); `to == vault`, `from == owner` doğrulanır. Yanıt `AdminTxSubmitOut`:
```json
{ "tx_hash": "0x…", "status": "confirmed", "block_number": 18342300, "error_code": null, "error_message": null, "explorer_url": "…" }
```
`status`: `submitted | confirmed | failed` (eski `SUCCESS|FAILED|NOT_FOUND`, `ledger`, `contract_error_code` silindi; kontrat hatası `error_code="vault:…"`).
`/admin/contract/config` (`contract_config`) → `ContractConfigOut` (§4, `admin` → `owner`).

---

## 4. `GET /config` — `public_config`

Auth: yok. Uygulama açılışında tek çağrı. Yanıt `ConfigOut`:

```json
{
  "version": "2.0.0",
  "api_prefix": "/api/v1",
  "chain": {
    "chain_id": 10143,
    "name": "Monad Testnet",
    "rpc_url": "https://testnet-rpc.monad.xyz",
    "ws_url": null,
    "explorer_url": "https://testnet.monadexplorer.com",
    "native_symbol": "MON",
    "native_decimals": 18,
    "faucet_url": "https://faucet.monad.xyz"
  },
  "contracts": {
    "vault": "0x1111111111111111111111111111111111111111",
    "router": "0x4444444444444444444444444444444444444444",
    "multicall3": "0xcA11bde05977b3631167028862bE2a173976CA11"
  },
  "assets": [ { "…AssetOut (§5)…" } ],
  "default_base_asset_code": "tUSDC",
  "default_base_asset_id": "9a1f…",
  "platform_fee_bps": 100,
  "settle_slippage_bps": 100,
  "default_trade_slippage_bps": 100,
  "tx_submit_timeout_seconds": 20,
  "pending_tx_ttl_seconds": 900,
  "limits": {
    "max_tokens": 6, "min_duration_days": 1, "max_duration_days": 1095,
    "max_commission_bps": 5000, "max_platform_fee_bps": 1000,
    "min_drawdown_bps": 100, "max_drawdown_bps": 10000, "max_settle_slippage_bps": 5000
  },
  "contract": {
    "owner": "0x67aD0CaE18528017B79E0aF8C4D3dd5937caFF19",
    "router": "0x4444444444444444444444444444444444444444",
    "platform_fee_bps": 100,
    "fee_recipient": "0x67aD0CaE18528017B79E0aF8C4D3dd5937caFF19",
    "paused": false,
    "settle_slippage_bps": 100
  },
  "contract_error": null,
  "auth": {
    "siwe_domain": "monadback.yolalapp.com",
    "siwe_uri": "https://monadback.yolalapp.com",
    "siwe_statement": "TraderKirala'ya giriş yap. Bu imza işlem yapmaz, gas harcamaz.",
    "nonce_ttl_seconds": 300,
    "access_token_ttl_seconds": 604800,
    "refresh_max_age_seconds": 2592000
  },
  "fx_cache_seconds": 600,
  "usd_prices": { "tUSDC": "1", "tWETH": "3200.00", "tWBTC": "65000.00", "MON": null }
}
```

Notlar:
- `contract` canlı `getConfig()`'tir (60 sn süreç içi önbellek, değişmez); RPC yoksa `null` + `contract_error`. `platform_fee_bps` üst düzeyde `contract`'tan kopyadır (RPC yoksa `null`).
- `contracts.vault` **her zaman** `settings.vault_address`'ten gelir (RPC'ye bağlı değil).
- `limits` `ContractLimits` (değişmez). `auth.siwe_*` ile `/auth/nonce` mesajı birebir aynı kaynaktan üretilir.
- `usd_prices` anahtarı **symbol**. `ws_url`, `multicall3` opsiyonel (`null` olabilir).
- **Silinen alanlar:** `network`, `network_passphrase`, `horizon_url`, `soroban_rpc_url`, `friendbot_url` (→ `chain.faucet_url`), `home_domain`, `web_auth_domain`, `web_auth_endpoint`, `signing_key`, `platform_account`, `vault_contract_id` (→ `contracts.vault`), `soroswap_router_id` (→ `contracts.router`), `soroswap_api_url`, `anchor`, `auth.login_message_prefix`, `auth.auth_nonce_ttl_seconds` (→ `auth.nonce_ttl_seconds`), `auth.sep10_challenge_timeout`, `contract.admin` (→ `contract.owner`).

`GET /fx` (`fx`), `GET /fx/convert` (`fx_convert`): şekil değişmez; `usd_prices` anahtarı symbol olur; XLM fiyat kaynağı
kalkar, MON fiyatı sabit/`null` (BE-14).

---

## 5. Varlıklar

### 5.1 `AssetOut` (`GET /assets`, `GET /assets/{asset_id}`, `/config.assets`, `ListingOut.base_asset`, `OfferOut.base_asset`, admin)

```json
{
  "id": "9a1f4c2e-…",
  "chain_id": 10143,
  "address": "0x2222222222222222222222222222222222222222",
  "symbol": "tUSDC",
  "code": "tUSDC",
  "name": "Test USD Coin",
  "decimals": 6,
  "category": "stable_fx",
  "is_base_allowed": true,
  "is_active": true,
  "onchain_allowed": true,
  "icon_url": null,
  "is_native": false,
  "created_at": "2026-09-27T09:00:00Z"
}
```
- `symbol` yeni kanonik ad; **`code` aynı değeri taşır** (geriye uyum, `deprecated` işaretli). DB kolonu `assets.symbol`; `code` computed.
- `address` = ERC-20 adresi (eski `contract_id`). `chain_id` yeni (eski `network`).
- `is_native` her zaman `false`: MON asset tablosunda **yer almaz**, vault'a girmez (K6).
- **Silinen:** `network`, `contract_id`, `issuer`, `is_classic`, `canonical`.
- Sıralama: `is_base_allowed desc, symbol asc`. Sorgular `base_only`, `onchain_only` değişmez.

### 5.2 `AssetBriefOut` (`AgreementOut.base_asset`, `BalanceOut.asset`, `TradeOut.token_in/out`, `QuoteOut`)

`{id, symbol, code, address, decimals, name, icon_url, category}` — `contract_id` → `address`, `issuer` silindi, `decimals` varsayılanı yok (zorunlu).

### 5.3 Admin varlık uçları

- `POST /admin/assets` (`create_asset`) istek `AdminAssetCreateIn`: `{address (0x), symbol (^[A-Za-z0-9]{1,12}$), name, icon_url?, category, decimals (0–18, zorunlu), is_active, is_base_allowed}`. Silinen: `contract_id`, `code`, `issuer`, `network`. `decimals` zincirden `decimals()` ile doğrulanır (uyuşmazsa 422 `decimals_mismatch`). Tekrar → 409 `asset_exists`.
- `PATCH /admin/assets/{asset_id}` değişmez.
- `POST /admin/assets/sync-onchain` → `AssetSyncOut {vault_address, checked, changed, rows:[{asset_id, symbol, address, onchain_allowed, onchain_is_base, changed, error}]}` (`vault_contract_id` → `vault_address`, `code` → `symbol`, `contract_id` → `address`).

---

## 6. Kullanıcı şemaları

- `UserOut` / `MeOut` / `AdminUserOut`: `stellar_address` → **`wallet_address`** (checksum). Diğer alanlar değişmez. `min_capital`, `budget_amount` `AmountIn` kuralına (18 ondalık) geçer.
- `PartyOut` (`AgreementOut.customer/trader`, `ActivityItemOut`): `stellar_address` → `wallet_address`.
- `PositionBalanceOut`: `{asset_id, symbol, code, address, balance}` (`contract_id` → `address`, `code` korunur + `symbol`).
- `PositionOut.base_asset_code`, `PositionBriefOut.base_asset_code`, `PendingOfferBriefOut.base_asset_code`, `*DashboardOut.base_asset_code`, `ActivityItemOut.base_asset_code`: **korunur** (değer = symbol).
- `TradeBriefOut`: `ledger` → `block_number`; `token_in_code/token_out_code` korunur.
- `RegisterIn` değişmez; cüzdan adresi **JWT `sub`**'dan alınır (gövdede adres yok, varsa yoksayılır). `RegisterOut {user, token, expires_at}` **korunur** — frontend buna göre düzeltilir (§13).
- `GET /users/{user_id}`: `budget_amount` artık dönmez (`null`) — BE-22; şema alanı kalır.
- Admin `GET /admin/users?q=` adres araması küçük harfle yapılır.

---

## 7. Cüzdan

### 7.1 `GET /wallet` — `get_wallet`

Auth: Bearer. Sorgu `movements` **silindi** (→ `/wallet/transactions`). Yanıt `WalletOut`:
```json
{
  "address": "0x67aD0CaE18528017B79E0aF8C4D3dd5937caFF19",
  "chain_id": 10143,
  "native": { "symbol": "MON", "decimals": 18, "balance": "1.25", "balance_raw": "1250000000000000000" },
  "tokens": [
    { "asset_id": "9a1f…", "address": "0x2222…", "symbol": "tUSDC", "decimals": 6, "balance": "1500", "balance_raw": "1500000000", "is_base_allowed": true },
    { "asset_id": "b7e3…", "address": "0x3333…", "symbol": "tWETH", "decimals": 18, "balance": "0", "balance_raw": "0", "is_base_allowed": false }
  ],
  "explorer_url": "https://testnet.monadexplorer.com/address/0x67aD0…FF19",
  "updated_at": "2026-09-26T12:00:00Z"
}
```
`tokens` = aktif allow-list varlıklarının tümü (sıfır bakiye dahil; `eth_call balanceOf`, varsa Multicall3). **Silinen:**
`network`, `funded`, `balances[]` (→ `tokens`), `total_try`, `fx`, `missing_trustlines`, `movements`, `anchor_enabled`,
`anchor_home_domain`, `friendbot_url`. TL karşılığı isteyen ekran `/fx` ile hesaplar.

### 7.2 `GET /wallet/deposit-info` — `deposit_info`

Yanıt `DepositInfoOut` (sade):
```json
{
  "address": "0x67aD0…FF19",
  "chain_id": 10143,
  "pay_uri": "ethereum:0x67aD0CaE18528017B79E0aF8C4D3dd5937caFF19@10143",
  "faucet_url": "https://faucet.monad.xyz",
  "token_faucet": {
    "enabled": true,
    "assets": [ { "asset_id": "9a1f…", "symbol": "tUSDC", "amount": "1000", "daily_limit": 1, "next_allowed_at": null } ]
  },
  "instructions": [ "MON gas için gerekir; Monad faucet'inden alın.", "Test tokenları için 'Test USDC al' düğmesini kullanın." ]
}
```
`pay_uri` EIP-681 (QR). **Silinen:** `network`, `network_passphrase`, `funded`, `friendbot_url`, `anchor_*`.

### 7.3 `POST /wallet/faucet` — `faucet` (yeni, K7)

Auth: Bearer. İstek `FaucetIn {asset_id}`. Sunucu `MINTER_KEY` ile `TestToken.mint(user, amount)` gönderir (OPS-11);
tutar varlık başına sabit (`settings.faucet_amounts`), limit kullanıcı+varlık başına günde 1. Receipt için 15 sn beklenir.
```json
{ "tx_hash": "0x…", "status": "confirmed", "asset_id": "9a1f…", "symbol": "tUSDC", "amount": "1000", "amount_raw": "1000000000", "next_allowed_at": "2026-09-27T12:00:00Z", "explorer_url": "…" }
```
Hatalar: 429 `rate_limited` (`details.next_allowed_at`), 422 `asset_not_mintable`, 503 `faucet_disabled`, 502 `chain_error`.

### 7.4 `GET /wallet/transactions?limit&offset` — `list_transactions` (opsiyonel, sprint sonu)

`Page[WalletTransferOut]`: MON + allow-list tokenlarının `Transfer` logları (kullanıcı `from` ya da `to`), yeniden eskiye.
```json
{ "tx_hash": "0x…", "block_number": 18342211, "log_index": 3, "asset_id": "9a1f…", "symbol": "tUSDC", "direction": "in",
  "counterparty": "0x1111…1111", "counterparty_label": "vault", "amount": "500", "amount_raw": "500000000", "at": "2026-09-26T12:01:12Z", "explorer_url": "…" }
```
`asset_id: null` → MON. Uygulanmazsa uç hiç yayınlanmaz (frontend yokluğunu tolere eder).

### 7.5 Silinen: `POST /wallet/tx/payment` (`payment`), `POST /wallet/tx/trustline` (`trustline`).

---

## 8. Agreement / Trade / Listing çıktı değişiklikleri

### 8.1 `AgreementOut` (`GET /agreements`, `GET /agreements/{id}`)

| Alan | Değişiklik |
|---|---|
| `created_tx`, `activate_tx`, `cancel_tx`, `settle_tx` | `0x` + 64 hex (66 kr) |
| `settled_by` | 0x adres (checksum) \| null |
| `last_event_ledger` | → **`last_event_block_number`** |
| `vault_address` | **yeni**; sözleşmenin yaşadığı vault (proxy) adresi |
| `customer.wallet_address`, `trader.wallet_address` | `PartyOut` (§6) |
| `base_asset`, `balances[].asset` | `AssetBriefOut` (§5.2) |
| `available_actions` | değer kümesi: `open, open_reserved, propose, fund, fund_reserved, accept, cancel, trade, settle, claim`. `claim` yalnız `settled` + müşteri + bakiyesi > 0 token varken; `claimable_assets: [asset_id]` **yeni** alanla birlikte |
| `pending_tx` (`PendingTxBriefOut`) | `{id, kind, action, status, tx_hash, created_at, expires_at}`; `status` §2.5 değerleri; `action` yeni |
| `balances[]` | settle sonrası "orphan" (in-kind teslim edilemeyen) bakiyeler burada kalır; `claim` bunları alır |

`GET /agreements/{id}?refresh=true` canlı okuma 10 sn önbellek arkasında (BE-24); şekil değişmez. `principal` vb. tutarlar
`base_asset.decimals` ondalıklı string.

### 8.2 `TradeOut` (`/agreements/{id}/trades`, `PATCH /trades/{id}`, `ActivityItemOut.trade`)

`ledger` → `block_number`; `onchain_seq` → `log_index`; `tx_hash` 66 kr; **`explorer_url` eklenir**; `token_in/token_out`
`AssetBriefOut`. Diğer alanlar değişmez.

### 8.3 `ListingOut` / `ListingDetailOut`

Şekil değişmez (`reservation_id`, `reserved_amount`, `is_funded` korunur). `base_asset` yeni `AssetOut`. `amount`, `min_capital`
`base_asset.decimals` kuralında. `PATCH /listings/{id}` rezerve edilmiş capital ilanında `amount` değiştirmeye 409
`amount_locked` verir (BE-23).

### 8.4 `OfferAcceptOut.next_action`

Literal genişler: `"open" | "open_reserved" | "propose"` (ilan rezervasyonu anaparayı karşılıyorsa `open_reserved`).

### 8.5 `AdminAgreementOut`

`last_event_ledger` → `last_event_block_number`; `*_tx` 66 kr; `vault_address` eklenir.

---

## 9. Sağlık

- `GET /health` (`health`) değişmez: `{status, db, version}`.
- `GET /health/chain` (`health_chain`, **yeni**; `/health/stellar` silindi). Auth yok.
  - 200: `{ "ok": true, "chain_id": 10143, "block_number": 18342400, "latency_ms": 84, "rpc_url": "https://testnet-rpc.monad.xyz" }`
  - 503: `{ "ok": false, "chain_id": 10143, "error": "ConnectError", "rpc_url": "…" }`
  - `chain_id` RPC'nin `eth_chainId` cevabıdır; ayarlardakiyle farklıysa `ok:false`, `error:"chain_id_mismatch"`.

---

## 10. Admin şema değişiklikleri (X-Admin-Key)

| Şema | Değişiklik |
|---|---|
| `AdminStatsOut` | `vault_contract_id` → `vault_address`; `network` → `chain_id: int`; `anchor_transactions_by_status` **silindi**; `pending_transactions_by_status` anahtarları §2.5 |
| `IndexerStateOut` | `{key, block_number, last_block_hash, updated_at}` (`cursor`, `ledger` silindi) |
| `IndexerStatusOut` | `{states, latest_block, lag_blocks, confirmations, vault_address, rpc_error}` (`latest_ledger` → `latest_block`, `lag_ledgers` → `lag_blocks`) |
| `IndexerResetIn` | `{key:"vault_events", block_number: int \| null}` (`ledger`, `cursor` silindi; null → satırı sil) |
| `ContractConfigOut` | `admin` → `owner`; adresler checksum |
| `AdminTxOut`, `AdminTxSubmitIn/Out` | §3.6 |
| `AdminAssetCreateIn`, `AssetSyncOut` | §5.3 |
| `PATCH /admin/users/{id}` | `role` alanı gönderilirse 422 `role_change_forbidden` (BE-25); şemada kalır |

---

## 11. Silinen uçlar

| Method | Path | operationId | Yerine |
|---|---|---|---|
| GET | `/auth/sep10` | `sep10_challenge_api_v1_auth_sep10_get` | `POST /auth/nonce` |
| POST | `/auth/sep10` | `sep10_verify_api_v1_auth_sep10_post` | `POST /auth/verify` |
| POST | `/wallet/tx/payment` | `payment_api_v1_wallet_tx_payment_post` | `POST /wallet/tx/transfer` |
| POST | `/wallet/tx/trustline` | `trustline_api_v1_wallet_tx_trustline_post` | — (EVM'de trustline yok) |
| GET | `/market/candles` | `candles_api_v1_market_candles_get` | — (frontend kullanmıyor) |
| GET | `/health/stellar` | `health_stellar_health_stellar_get` | `GET /health/chain` |
| GET | `/anchor/info` | `info_api_v1_anchor_info_get` | — (K7) |
| GET | `/anchor/auth/session` | `session_api_v1_anchor_auth_session_get` | — |
| POST | `/anchor/auth/challenge` | `challenge_api_v1_anchor_auth_challenge_post` | — |
| POST | `/anchor/auth/token` | `token_api_v1_anchor_auth_token_post` | — |
| POST | `/anchor/deposit` | `deposit_api_v1_anchor_deposit_post` | `POST /wallet/faucet` (testnet) |
| POST | `/anchor/withdraw` | `withdraw_api_v1_anchor_withdraw_post` | — |
| GET | `/anchor/transactions` | `list_transactions_api_v1_anchor_transactions_get` | `GET /wallet/transactions` |
| GET | `/anchor/transactions/{tx_ref}` | `get_transaction_api_v1_anchor_transactions__tx_ref__get` | — |
| POST | `/anchor/transactions/{tx_ref}/tx/payment` | `withdraw_payment_api_v1_anchor_transactions__tx_ref__tx_payment_post` | — |
| POST | `/anchor/kyc` | `kyc_api_v1_anchor_kyc_post` | — |

Silinen şemalar: `Sep10ChallengeOut`, `Sep10VerifyIn`, `PendingTxOut`, `TxSubmitOut` (→ `TxStatusOut`), `WalletBalanceOut`,
`MissingTrustlineOut`, `MovementOut`, `WalletFxOut`, `DepositInfoAssetOut`, `WalletPaymentIn`, `TrustlineIn`, `AnchorConfigOut`,
`CandleOut`, `CandlesOut`, tüm `app/schemas/anchor.py`. Enum: `PendingTxKind.payment/trustline`, `AnchorTxKind`, `AnchorTxStatus`.

---

## 12. Değişmeyen uçlar

Path, sorgu, istek ve yanıt şekli aynı. Parantez içi: yalnızca **iç içe** şemalarda §5–§8'deki alan adı değişiklikleri yansır
(`wallet_address`, `AssetOut`, `block_number`), uç kendisi değişmez.

- **users:** `POST /users/register` (RegisterOut korunur), `GET /users/me`, `PATCH /users/me`, `GET /users/by-username/{username}`, `GET /users/{user_id}`, `GET /traders`, `GET /traders/{trader_id}/profile` (PositionOut/TradeBriefOut), `POST|DELETE /traders/{trader_id}/follow`, `GET /traders/{trader_id}/ratings`.
- **listings:** `POST /listings`, `GET /listings`, `GET /listings/mine`, `GET /listings/mine/counts`, `GET /listings/saved`, `GET /listings/{id}`, `PATCH /listings/{id}`, `POST /listings/{id}/pause|resume|close`.
- **discover:** `GET /discover` (cursor), `GET /discover/remaining`, `POST /discover/{target_type}/{target_id}/action`, `DELETE /discover/listing/{listing_id}/save`.
- **offers:** `POST /offers`, `GET /offers?box=inbox|outbox|all&status&listing_id`, `GET /offers/stats`, `GET /offers/{id}`, `POST /offers/{id}/accept` (`next_action` genişledi, §8.4), `POST /offers/{id}/reject`, `POST /offers/{id}/withdraw`.
- **agreements:** `GET /agreements?role&status&limit&offset`, `GET /agreements/{id}` (alanlar §8.1), `GET /agreements/{id}/quote`, `GET /agreements/{id}/trades`, `GET /agreements/{id}/value-history`.
- **ratings:** `POST /agreements/{id}/rating`, `GET /agreements/{id}/rating`.
- **trades:** `PATCH /trades/{trade_id}`.
- **activity:** `GET /activity?limit&offset`.
- **conversations:** `GET /conversations`, `POST /conversations`, `GET /conversations/unread-count` (`{conversations, messages}`), `GET /conversations/{id}`, `GET /conversations/{id}/messages?after&before&limit`, `POST /conversations/{id}/messages`, `POST /conversations/{id}/read`.
- **notifications:** `GET /notifications?category&unread_only&limit&offset`, `GET /notifications/unread-count` (`{unread, by_category}`), `POST /notifications/read` (`MarkReadIn {ids | all, category?}` zorunlu gövde), `POST /notifications/{id}/read`, `PUT /notifications/push-token`, `GET /notifications/{id}`.
- **dashboard:** `GET /dashboard` (`wallet_balance` artık RPC `balanceOf`; şekil aynı).
- **config/fx:** `GET /fx`, `GET /fx/convert?amount_usd`.
- **assets:** `GET /assets?base_only&onchain_only`, `GET /assets/{id}` (yalnız `AssetOut` şekli değişti, §5.1).
- **admin:** `GET /admin/stats` (alanlar §10), `GET /admin/users`, `GET|PATCH /admin/users/{id}`, `GET /admin/assets`, `PATCH /admin/assets/{id}`, `GET /admin/agreements`, `GET /admin/indexer`, `POST /admin/indexer/reset` (gövde §10), `GET /admin/contract/config`.
- **meta/legal:** `GET /health`, `GET /legal`, `/legal/privacy`, `/legal/terms`, `/legal/data-safety`, `/legal/financial-disclosure`, `/legal/delete-account` (metin BE-26 ile güncellenir; şekil HTML).
- **auth:** `GET /auth/me` (yalnız `public_key` → `address`).

---

## 13. Frontend `endpoints.ts` / `types.ts` düzeltme listesi (FE-36, FE-38)

Tipler `openapi-typescript` ile üretilir (`npx openapi-typescript $API/openapi.json -o src/lib/api/schema.d.ts`); elle yazılan
`types.ts` yalnız yardımcı takma adlar taşır. Aşağıdakiler bugünkü kodla OpenAPI arasındaki uyuşmazlıklardır (00-inceleme §D) ve
Monad ile gelen değişikliklerdir:

| # | Bugün (`endpoints.ts`) | Olacak |
|---|---|---|
| 1 | `usersApi.register` → `MeOut` | → `RegisterOut {user, token, expires_at}`; **token saklanır**, `user.role` buradan okunur (kayıt döngüsü) |
| 2 | `authApi.refresh` `auth=true` → 401'de köprü kendini bekliyor | refresh isteği köprüyü **atlar** (`retried:true` ya da ayrı bayrak); 401 `session_expired`/`token_expired` → oturum kapat |
| 3 | `authApi.sep10Challenge/sep10Verify`, `Sep10ChallengeOut` | **silinir** |
| 4 | `authApi.nonce(publicKey)` gövde `{public_key}` | `nonce(address)` gövde `{address}`; yanıt `NonceOut {nonce, message, expires_at, chain_id, domain}` |
| 5 | `authApi.verifyNonce({public_key, nonce, signature})` | `verify({message, signature})` → `LoginOut` |
| 6 | `LoginOut.public_key`, `AuthMeOut.public_key` | → `address` |
| 7 | `txApi.submit(signedXdr, pendingId)` gövde `{xdr, pending_id}` | `submit(pendingTxId, txHash)` gövde `{pending_tx_id, tx_hash}` → `TxStatusOut` |
| 8 | `txApi.status` → `{status, hash?}` | → `TxStatusOut` (`status`, `tx_hash`, `block_number`, `error_code`, `explorer_url`, …) |
| 9 | `agreementsApi.buildTx/buildTradeTx` → `{xdr, pending_id?}` | → `UnsignedTxOut` (`pending_tx_id, to, data, value, gas, chain_id, pre_steps, …`) |
| 10 | `walletApi.buildPaymentTx/buildTrustlineTx`, `anchorApi.*` | **silinir**; eklenir: `walletApi.transfer({asset_id, to, amount})`, `walletApi.faucet({asset_id})`, `walletApi.transactions({limit, offset})` |
| 11 | `walletApi.get` → `Record<string, unknown>` | → `WalletOut {address, native, tokens, explorer_url}` |
| 12 | `notificationsApi.unreadCount` → `{count}` | → `UnreadCountOut {unread, by_category}` |
| 13 | `conversationsApi.unreadCount` → `{count}` | → `ConversationsUnreadOut {conversations, messages}` |
| 14 | `notificationsApi.markAllRead()` gövdesiz | gövde `{all: true}` (`MarkReadIn`) → `MarkReadOut {updated}`; `markRead(id)` → `MarkReadOut` |
| 15 | `notificationsApi.setPushToken` → `{ok}` | → `MeOut` |
| 16 | `listingsApi.list/mine/saved`, `tradersApi.list`, `activityApi.feed`, `notificationsApi.list`, `agreementsApi.list/trades` `cursor` / `{items, next_cursor}` | `limit/offset` → `Page<T> {items, total, limit, offset}` |
| 17 | `listingsApi.mineCounts` → `Record<string, number>` | → `ListingCountsOut {draft, active, paused, closed}` |
| 18 | `listingsApi.byId` → `ListingOut` | → `ListingDetailOut` (`offers?`, `pending_offers`, `my_offer_id`) |
| 19 | `offersApi.list({status, role})` | `{box: 'inbox'\|'outbox'\|'all', status?, listing_id?, limit, offset}` |
| 20 | `offersApi.accept` → `OfferOut` | → `OfferAcceptOut {offer, agreement, conversation_id, next_action}` |
| 21 | `offersApi.stats` → `Record<string, number>` | → `OfferStatsOut {pending_inbox, pending_outbox}` |
| 22 | `tradersApi.list` → `{items: MeOut[]}` | → `Page<TraderCardOut>`; `tradersApi.profile` → `TraderProfileOut`; `tradersApi.ratings` → `Page<RatingOut>` |
| 23 | `FollowOut {trader_id, following}` | + `follower_count` |
| 24 | `agreementsApi.quote(id)` parametresiz | `quote(id, {token_in, token_out, amount_in, slippage_bps?, deadline_seconds?})` → `QuoteOut` |
| 25 | `agreementsApi.valueHistory` → `{items}` | → `ValueHistoryOut {agreement_id, range, principal, current_value, high_water_value, points}`; parametre `range` |
| 26 | `agreementsApi.list({status})` | `{role?, status?, limit, offset}` → `Page<AgreementOut>` |
| 27 | `conversationsApi.messages(id, {cursor})` | `{after?, before?, limit?}` → `MessagesPageOut {conversation_id, items, has_more}` |
| 28 | `fxApi.convert({amount, from, to})` | `convert({amount_usd})` → `FxConvertOut` |
| 29 | `metaApi.config` → elle `ConfigOut` | üretilen `ConfigOut` (§4); `chain`, `contracts`, `auth.siwe_*` okunur; `network_passphrase`, `horizon_url`, `vault_contract_id` kalkar |
| 30 | `UserOut.stellar_address` | → `wallet_address` (`shortAddress` 0x için: `0x67aD…FF19`) |
| 31 | `AssetOut {network, contract_id, code, issuer, canonical}` | `{chain_id, address, symbol, code, decimals, …, is_native:false}`; biçimlendirme `formatUnits(balance_raw, decimals)` ya da doğrudan `balance` string |
| 32 | `metaApi.health` | + `metaApi.healthChain()` → `GET /health/chain` |
| 33 | `RegisterIn` | değişmez; adres gövdeye **konmaz** |
| 34 | Frontend'de olmayan ama demo akışının gerektirdiği uçlar | eklenir: `listingsApi.reserveTx(id)`, `listingsApi.releaseTx(id, {amount?})`, `listingsApi.update(id, body)`, `agreementsApi.rating(id)`, `usersApi.byId(id)`, `conversationsApi.start({user_id})`, `tradesApi.update(id, {note, notify_investors})`, `assetsApi.byId(id)` |
| 35 | Hata eşlemesi (`errors.ts`) | `stellar_error` → `chain_error`; yeni: `session_expired`, `use_reserved_action`, `reservation_insufficient`, `receipt_mismatch`, `rate_limited`, `vault:*` kontrat hataları (§2.6 tablo ile Türkçe metin) |

---

## 14. OpenAPI operationId'leri (korunacak handler adları)

Varsayılan üretim: `{handler}{path}` → `\W`→`_` → `_{method}`. Handler adlarını ve path şablonlarını değiştirmeyin.

| Router | Handler (operationId'nin ön eki) |
|---|---|
| auth | `create_nonce`, `verify_nonce`, `me`, `refresh` (silinen: `sep10_challenge`, `sep10_verify`) |
| tx | `submit`, `get_pending` (`/tx/{pending_id}`) |
| agreements | `list_agreements`, `get_agreement`, `build_trade`, `build_action`, `quote`, `list_trades`, `value_history` |
| listings | `create_listing`, `list_listings`, `my_listings`, `my_listing_counts`, `saved_listings`, `get_listing`, `update_listing`, `reserve_listing_capital`, `release_listing_capital`, `pause_listing`, `resume_listing`, `close_listing` |
| wallet | `get_wallet`, `deposit_info`; **yeni** `transfer` (`/wallet/tx/transfer`), `faucet` (`/wallet/faucet`), `list_transactions` (`/wallet/transactions`); silinen `payment`, `trustline` |
| config | `public_config`, `fx`, `fx_convert` |
| meta | `health`; **yeni** `health_chain`; silinen `health_stellar` |
| users | `register`, `get_me`, `update_me`, `get_by_username`, `get_user`, `list_traders`, `trader_profile`, `follow`, `unfollow`, `trader_ratings` |
| assets | `list_assets`, `get_asset` |
| admin | `stats`, `list_users`, `get_user`, `update_user`, `list_assets`, `create_asset`, `update_asset`, `sync_assets_onchain`, `list_agreements`, `indexer_status`, `indexer_reset`, `contract_config`, `tx_set_token`, `tx_set_paused`, `tx_set_fees`, `tx_set_router`, `tx_set_settle_slippage`, `tx_submit`; **yeni** `tx_apply_router` |
| offers, discover, conversations, notifications, ratings, trades, activity, dashboard, legal | değişmez (§12) |
| market, anchor | router **silindi** |

Örnek tam operationId'ler: `verify_nonce_api_v1_auth_verify_post`, `submit_api_v1_tx_submit_post`,
`build_action_api_v1_agreements__agreement_id__tx__action__post`, `transfer_api_v1_wallet_tx_transfer_post`,
`health_chain_health_chain_get`. (Admin ve users router'larında aynı adlı `get_user`/`list_assets`/`list_agreements`
handler'ları path farkıyla ayrışır; bugün de öyle.)

---

## 15. Karar değişikliği önerisi

Yok. K1–K13 olduğu gibi uygulanır. Kararlarla çelişmeyen, bu dokümanın eklediği tasarım noktaları (bilgi için):
- `open/fund` ile `open_reserved/fund_reserved` ayrımı istemciye açık verilir; backend sessiz düşüş yapmaz (inceleme §C hata 3'ün kapanışı).
- `release` gövdesi eklendi; `releaseAll` ayrı ABI çağrısı (SC-04 ile uyumlu).
- `GET /tx/{pending_id}` path parametresinin adı operationId korunsun diye değişmedi.
- `AssetOut.code` geriye uyum için `symbol`'ün kopyası olarak kalır; Sprint 3'te kaldırılabilir.
- `/admin/contract/tx/apply_router` SC-08 (iki adımlı router değişimi) için zorunlu eklemedir.

Açık sorular (Gün 0 / OPS-03'te kapanır): explorer taban URL'i (`testnet.monadexplorer.com` varsayıldı), MON faucet URL'i,
`ws_url` verilecek mi, `tx_submit_timeout_seconds` için 20 sn yeterli mi (Monad blok süresi ~0,4 sn; receipt genelde < 2 sn).

## 16. Test notu

Sözleşme testleri (backend `pytest` SIWE/tx-submit testleri, frontend `openapi-typescript` tip senkronu ve `e2e_testnet.py`)
sprintin **sonuna** bırakılır; testnet cüzdanlarında henüz MON bulunmadığı için zincire dokunan testler `FakeMonadGateway` ile
yazılır, gerçek ağ doğrulaması OPS-03 faucet adımından sonra yapılır. Bu doküman testten bağımsız olarak backend ve frontend
uygulamasının tek referansıdır.
