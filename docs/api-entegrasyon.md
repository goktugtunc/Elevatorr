# API entegrasyonu — TraderKirala API (Monad)

**Sözleşmenin tek kaynağı:** [monad/02-api-sozlesme.md](monad/02-api-sozlesme.md)
(uçlar, şemalar, hata kodları, işlem modeli). Bu sayfa yalnız frontend tarafındaki
karşılıkları ve komutları özetler.

**Base URL:** `.env → EXPO_PUBLIC_API_BASE_URL` origin alır; kod `/api/v1` önekini ekler.
Yerel geliştirme `http://127.0.0.1:8013` (backend compose projesi), üretim
`https://monadback.yolalapp.com`. Zincir **Monad Testnet (10143)**; `GET /config`
RPC/explorer/vault adresini döner ve `.env` yedeklerinin önüne geçer.

## Frontend karşılıkları

| Ne | Nerede |
|---|---|
| Şema tipleri (üretilen) | `app/src/lib/api/schema.d.ts` — `npm run gen:api` (sunucu `/openapi.json`) ya da `npm run gen:api:file` (`app/openapi.json`) |
| Tip takma adları | `app/src/lib/api/types.ts` |
| Uçlar | `app/src/lib/api/endpoints.ts` (`authApi`, `walletApi`, `agreementsApi`, …) |
| HTTP istemcisi, 401 köprüsü, `ApiError` | `app/src/lib/api/client.ts` |
| Kullanıcı hata metinleri | `app/src/lib/errors.ts` (04 §8.3) |
| Zincir işlemi yürütücü | `app/src/lib/chain/tx.ts`, `useTxExecutor` (02 §2.7) |

```bash
cd app
npm run gen:api                                   # http://127.0.0.1:8013/openapi.json
OPENAPI_URL=https://monadback.yolalapp.com/openapi.json npm run gen:api
```

## Kurallar (02 §0 özeti)

- **Tutarlar string** (insan okunur, `"1000.50"`); `*_raw` string tam sayı (wei benzeri).
  `Number()` tutarlar için yasak — `@/lib/chain` `formatAmount` / `formatRaw` / `parseAmountInput`.
- **Oranlar bps** (2000 = %20); kullanıcı yüzde girer, `× 100` ile bps'e çevrilir.
- **Adresler** EIP-55 checksum; karşılaştırma küçük harfle (`sameAddress`).
- **Hata gövdesi** `{code, message, details}` → `ApiError.code` / `.details`; `retry_after_seconds`.

## Giriş — SIWE (EIP-4361)

```
POST /api/v1/auth/nonce   { address }              → { message, nonce, expires_at }
     cüzdan `personal_sign` ile mesajı imzalar (MetaMask / WalletConnect / uygulama içi)
POST /api/v1/auth/verify  { message, signature }   → { token, expires_at, address, registered, user }
POST /api/v1/auth/refresh                          → yeni JWT (cüzdan imzası gerekmez)
```

`registered: false` → `POST /users/register`. Ayrıntı: `app/src/lib/auth/siwe.ts`, 02 §1.

## Zincir üstü işlemler

Backend calldata üretir (`UnsignedTxOut`), **kullanıcı cüzdanda imzalar ve gönderir**,
tx hash sunucuya bildirilir:

```
POST /api/v1/agreements/{id}/tx/{action}   → UnsignedTxOut (+ approve ön adımları)
wallet.sendTransaction(...)                → tx_hash
POST /api/v1/tx/submit { pending_id, tx_hash }
GET  /api/v1/tx/{pending_id}               → durum (submitted → confirmed | failed)
```

Aynı model: `POST /listings/{id}/tx/reserve|release`, `POST /wallet/tx/transfer`,
`POST /agreements/{id}/tx/trade`. Gizli anahtar hiçbir adımda sunucuya gitmez.
