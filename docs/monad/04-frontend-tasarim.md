# 04 — Frontend (`app/`) Monad tasarımı

**Durum:** Sprint 2 FE-30…FE-44 için bağlayıcı uygulama spesifikasyonu (26 Eyl 2026).
**Kaynaklar (öncelik sırasıyla):** `SPRINT-2-MONAD.md` §2 K1–K13 → `docs/monad/02-api-sozlesme.md` (HTTP sözleşmesi, §13 frontend
düzeltme listesi) → `docs/monad/01-kontrat-spec.md` (ABI, hata/event adları) → `docs/monad/06-monad-testnet.md` (ağ bilgisi) →
`docs/monad/00-inceleme.md` §D (mevcut hatalar). Kaynak kod: `app/app/**`, `app/src/**`, `app/package.json`, `app/app.json`,
`app/index.js`, `app/.env.example`. Eski `docs/api-entegrasyon.md`, `docs/gelistirme-notlari.md`, `docs/mobil-test.md`, `SPRINT-1.md`
Stellar dönemine aittir; **referans değildir**, Dalga 3'te güncellenir.
**Hedef ağ:** Monad Testnet, chainId `10143` (`0x279f`), RPC `https://testnet-rpc.monad.xyz`, MON 18 ondalık (yalnız gas, K6).
Taban varlık tUSDC (6 ondalık). Yeni tasarımda Stellar'a ait hiçbir şey kalmaz (DoD-4: `grep -ri stellar src app` boş).
**Dil kuralı:** doküman Türkçe; kod tanımlayıcıları, dosya adları, JSON alanları ve **arayüz metinleri İngilizce** (mevcut arayüz dili).

Bu dokümanı uygulayan ajan, başka kaynağa bakmadan ilgili dosyaları yazabilmelidir. "ZORUNLU" işaretli maddeler uygulanır; "opsiyonel"
olanlar sprint sonuna bırakılabilir. Sürüm numaraları 26 Eyl 2026'da `npm view <paket> version` ile doğrulandı (§1.1).

---

## 0. Özet: ne değişiyor, ne kalıyor

| Katman | Bugün (v1-stellar) | Monad |
|---|---|---|
| Zincir kütüphanesi | `@stellar/stellar-sdk` 17, `lib/stellar/*` (kullanılmıyor) | **viem 2.x**, `lib/chain/*` (config, client, abi, format, errors, tx) |
| Web cüzdanı | Stellar Wallets Kit (Freighter, xBull…) | **wagmi 2.x + viem**: `injected` (MetaMask, Rabby, Phantom EVM, EIP-6963) + `walletConnect` bağlayıcısı (K8) |
| Mobil cüzdan | WalletConnect `stellar:testnet` / `stellar_signXDR`; **sessizce oluşan** yerel Keypair; SEP-7 (ölü) | WalletConnect `eip155:10143` (`personal_sign`, `eth_sendTransaction`, `wallet_switchEthereumChain`…); uygulama içi cüzdan **yalnız açık seçimle**, viem `privateKeyToAccount` + `expo-secure-store`, **web'de yok** (K9). SEP-7 silinir |
| Giriş | SEP-10 challenge XDR | **SIWE (EIP-4361)**: `POST /auth/nonce` → sunucunun verdiği mesaj `personal_sign` → `POST /auth/verify` (K2) |
| İşlem | backend imzasız XDR → cüzdan imzalar → backend yayınlar | backend `{to, data, value, gas, chain_id, pre_steps}` → cüzdan `eth_sendTransaction` → `POST /tx/submit {pending_tx_id, tx_hash}` → `GET /tx/{id}` poll (K3, K4) |
| Tutarlar | `Number()` ile biçim | string + `decimals`; `formatUnits`/`parseUnits`, `Number()` yasak (K12) |
| API tipleri | elle yazılmış, şemadan sapmış | `openapi-typescript` ile üretilen `schema.d.ts`; `endpoints.ts` 02 §13'e göre düzeltilir |
| Ekranlar | 6 gerçek, 15 Placeholder | + FE-41 ilan oluştur/İlanlarım, FE-42 sözleşme, FE-43 cüzdan, FE-44 paneller; kalan 7 Placeholder Sprint 3 |

Expo Go uyumu **korunur**: eklenen her paket saf JS'tir (viem, wagmi, WalletConnect universal provider); native modül gerektiren
`@reown/appkit-react-native`, `react-native-quick-crypto`, `react-native-get-random-values` **eklenmez** (K9).

### 0.1 Karar değişikliği önerileri (spec mevcut karara göre yazıldı)

1. **K8 — wagmi sürümü.** npm'de bugün `wagmi@latest = 3.7.7`; K8 "wagmi v2" der. Bu spec **wagmi `^2.19.5`** (2.x'in son
   sürümü) kullanır. wagmi 3 de viem 2.x ve `@tanstack/react-query ≥5` ile çalışır (peer: `typescript >=5.9.3`, bizde 6.0.3), bu
   sprintte kullandığımız yüzey (`createConfig`, `injected`, `walletConnect`, `wagmi/actions`) her iki sürümde aynıdır. Ekip
   onaylarsa `^3.7.7`'ye geçiş yalnız `package.json` değişikliğidir; kararı Gün 0'da (OPS-04) verin. Spec 2.x ile uygulanır.
2. **FE-33 — deeplink listesi.** SPRINT-2 tablosu "MetaMask, Rainbow ve Trust" der; görev tanımı Phantom'u da ister. Phantom EVM
   WalletConnect şeması kayıt defterinden **doğrulanmadı** → `verified: false` ile en sonda denenir (§3.7). Karar değişmez.
3. **Explorer.** viem'in `monadTestnet` tanımı `testnet.monadexplorer.com`, 06 dokümanı `testnet.monadvision.com` (birincil) der.
   Frontend her zaman `/config.chain.explorer_url` kullanır; env yedeği monadvision'dır. Backend hangi değeri verirse o geçerlidir.

---

## 1. Bağımlılıklar, Expo Go uyumu, polyfill sırası (FE-30)

### 1.1 `package.json` değişiklikleri (ZORUNLU)

Doğrulanan sürümler (26 Eyl 2026, `npm view`): `viem 2.56.9`, `wagmi 2.19.5` (`@2` etiketi; `latest` 3.7.7), `@wagmi/core 2.22.1`,
`@wagmi/connectors 5.11.2`, `@tanstack/react-query 5.104.0` (kurulu 5.103.1 yeterli), `@walletconnect/universal-provider 2.25.0`
(kurulu = son), `openapi-typescript 7.13.0`, `expo-secure-store 57.0.4` (kurulu).

| İşlem | Paket | Sürüm | Not |
|---|---|---|---|
| **ekle** | `viem` | `^2.56.9` | Bugün `node_modules/viem@2.56.8` zaten var ama **geçişli** (`@creit.tech/stellar-wallets-kit` → `@reown/appkit`). Doğrudan bağımlılık olur |
| **ekle** | `wagmi` | `^2.19.5` | Yalnız `wallet.web.ts` içinden import edilir (web bundle). Peer: `viem 2.x`, `react >=18`, `@tanstack/react-query >=5`, `typescript >=5.0.4` — hepsi sağlanıyor. `wagmi/connectors` ve `wagmi/actions` alt yolları kullanılır; **hook ve `WagmiProvider` kullanılmaz** (adaptör imperatif) |
| **kalır** | `@tanstack/react-query` | `^5.103.1` | ekranlar zaten kullanıyor |
| **kalır** | `@walletconnect/universal-provider` | **`2.25.0` (sabit)** | mobil WalletConnect yolu (K9). wagmi'nin `walletConnect` bağlayıcısı `@walletconnect/ethereum-provider` → `@walletconnect/sign-client 2.x` getirir; `npm ls @walletconnect/sign-client` **tek sürüm** göstermeli (gerekirse `overrides`) |
| **kalır** | `buffer`, `fast-text-encoding`, `expo-crypto`, `expo-secure-store`, `expo-clipboard`, `expo-linking`, `react-native-qrcode-svg`, `react-native-svg` | mevcut | polyfill, güvenli depo, QR |
| **ekle (dev)** | `openapi-typescript` | `^7.13.0` | `npm run gen:api` (§5.1) |
| **kaldır** | `@stellar/stellar-sdk` | — | `lib/stellar/*`, `auth/sep10.ts`, `wallet/local.ts` ile birlikte |
| **kaldır** | `@creit.tech/stellar-wallets-kit` | — | web adaptörü wagmi olur. Bununla `@reown/appkit*`, `@coinbase/wallet-sdk`, `@base-org/account` gibi geçişli paketler de düşer (bundle küçülür) |
| **kaldır** | `@stellar/freighter-api` | — | |
| **eklenmez** | `@reown/appkit`, `@reown/appkit-react-native`, `@reown/appkit-wagmi-react-native` | — | K9: native modül (netinfo, modal, svg) → development build gerekir; Expo Go kırılır |
| **eklenmez** | `react-native-get-random-values`, `react-native-quick-crypto`, `@ethersproject/*`, `ethers` | — | Expo Go dışı ya da gereksiz |

`scripts` eklemeleri: `"gen:api": "openapi-typescript ${OPENAPI_URL:-http://127.0.0.1:8013/openapi.json} -o src/lib/api/schema.d.ts"`,
`"gen:api:file": "openapi-typescript ./openapi.json -o src/lib/api/schema.d.ts"` (§5.1). `engines.node >=22` kalır.

Kurulum (Dalga 1 (a)): `npm uninstall @stellar/stellar-sdk @creit.tech/stellar-wallets-kit @stellar/freighter-api &&
npm install viem@^2.56.9 wagmi@^2.19.5 && npm install -D openapi-typescript@^7.13.0`. Sonra `npm ls viem @walletconnect/sign-client
@noble/hashes` ile tekilleştirme kontrolü (`@noble/hashes` viem için ≥1.8.0'a yükselir; stellar-sdk gidince 1.7.1 kopyası düşer).

### 1.2 Expo Go uyumu gerekçeleri

- **viem**: saf JS; ihtiyaçları `TextEncoder/TextDecoder`, `crypto.getRandomValues`, `BigInt`, `fetch`. Hermes (RN 0.86) `BigInt`,
  `fetch`, `URL` sağlar; ilk ikisi `src/polyfills.ts` ile geliyor. `crypto.subtle` yalnız `ox`'un `WebCryptoP256/AesGcm/webauthn`
  modüllerinde (bizim yollarımızda yok). WebSocket transport'u `isows`'u tembel import eder ve `isows` `react-native` export koşuluyla
  `native.js`'e çözülür (`ws` Node paketi bundle'a girmez); zaten yalnız `http()` transport kullanılır.
- **wagmi**: yalnız web dosyasında import edilir; Metro native bundle'ında yer almaz. Web'de `wallet_addEthereumChain` yedeği
  `switchChain(config, { chainId, addEthereumChainParameter })` ile hazır gelir.
- **WalletConnect universal provider**: bugünkü paket, aynı sürüm; yalnız namespace/method değişir. Oturum deposu AsyncStorage
  (`@react-native-async-storage/async-storage`, Expo Go'da var).
- **expo-secure-store**: Expo Go'da var; 0x + 64 hex özel anahtar (66 karakter) Android 2048 byte sınırının çok altında.
- **Deep link dönüşü**: Expo Go'da `traderkirala://` kayıtlı değil → `Linking.createURL('/')` (`exp://…/--/`) korunur.

### 1.3 Polyfill sırası (`index.js` → `src/polyfills.ts` → `expo-router/entry`) — DEĞİŞMEZ

`@noble/hashes` (viem'in ve WalletConnect'in bağımlılığı) `globalThis.crypto`'yu **import anında** yakalar; bu yüzden `index.js`'teki
iki satırın sırası korunur. `src/polyfills.ts` içeriği aynı kalır (`fast-text-encoding`, `Buffer`, `crypto.getRandomValues`,
`crypto.randomUUID` — expo-crypto ile); yalnız yorumlar güncellenir ("stellar-sdk" → "viem/ox"). **viem için ek polyfill gerekmez**:
BigInt Hermes'te yerlidir, `Promise.withResolvers` viem kendi yardımcısıyla sağlar (`utils/promise/withResolvers`), `Buffer` viem
tarafından kullanılmaz (WalletConnect ve `auth/jwt.ts` için kalır). `structuredClone` gerekmez.

Doğrulama (Dalga 1 (a) sonunda): `npx expo export --platform ios --dev` bundle'ı hatasız derlenir; Expo Go'da açılışta
"crypto.getRandomValues must be defined" ya da "TextEncoder is not defined" görülürse sıralama bozulmuştur.

---

## 2. `src/lib/chain/` — zincir katmanı (FE-30, FE-37, FE-39)

```
src/lib/chain/
├── index.ts          # dışa açılan yüzey
├── config.ts         # zincir tanımı, /config ile birleştirme, explorer URL'leri, addEthereumChain parametreleri
├── client.ts         # viem publicClient (http), waitForReceipt
├── abi/
│   ├── traderVault.ts  # AUTO-GENERATED (contracts/scripts/export-abi.sh, 01 §1.4) — elle düzenlenmez
│   ├── mockRouter.ts   # AUTO-GENERATED (opsiyonel; frontend kullanmıyor)
│   └── index.ts        # traderVaultAbi, erc20Abi (viem'den), decodeVaultError
├── format.ts         # formatUnits/parseUnits sarmalayıcıları, shortAddress (0x), checksum
├── errors.ts         # ChainError, EIP-1193/viem hata eşlemesi, vault hata sözlüğü
├── tx.ts             # executeUnsignedTx, durum makinesi, outbox
└── useTxExecutor.ts  # React hook (react-query invalidation dahil)
```

`lib/stellar/*` silinir; `shortAddress` ve explorer yardımcıları buraya taşınır (import yolları `@/lib/chain`).

### 2.1 `config.ts` (ZORUNLU)

```ts
import { defineChain, type Chain } from 'viem';
import { monadTestnet as viemMonadTestnet } from 'viem/chains';
import { env } from '@/lib/env';
import type { ConfigOut } from '@/lib/api/types';

export const CHAIN_ID = 10143 as const;                 // env.chainId bununla eşleşmiyorsa uygulama açılışta hata verir
export const CHAIN_ID_HEX = '0x279f' as const;
export const NATIVE = { name: 'MON', symbol: 'MON', decimals: 18 } as const;
export const DEFAULT_FAUCET_URL = 'https://faucet.monad.xyz';

interface RuntimeChainConfig {
  rpcUrl: string;             // env → /config.chain.rpc_url
  explorerUrl: string;        // env → /config.chain.explorer_url
  faucetUrl: string;          // DEFAULT_FAUCET_URL → /config.chain.faucet_url
  vault: Address | null;      // /config.contracts.vault
  router: Address | null;     // /config.contracts.router
  multicall3: Address | null;
  assets: AssetOut[];         // /config.assets (allow-list; MON burada değildir, K6)
  defaultBaseAssetId: string | null;
  siweDomain: string | null;  // /config.auth.siwe_domain — siwe.ts sağlama yapar
  settleSlippageBps: number;  // /config.settle_slippage_bps (varsayılan 100)
  defaultTradeSlippageBps: number;
  pendingTxTtlSeconds: number;
  source: 'env' | 'server';
}
```

Kurallar:
- Başlangıç değeri `env`'den (`env.rpcUrl`, `env.explorerUrl`). `metaApi.config()` başarılı olunca **`applyServerConfig(cfg)`**
  çağrılır; `cfg.chain.chain_id !== CHAIN_ID` ise `ChainError('SERVER_CHAIN_MISMATCH')` fırlatılır ve giriş ekranı "Server is on
  chain {id}, this app is built for Monad Testnet (10143)." gösterir; uygulama devam etmez (SIWE zaten `siwe_chain_mismatch` verirdi).
- `getChain(): Chain` → `defineChain({ ...viemMonadTestnet, rpcUrls: { default: { http: [rpcUrl] } }, blockExplorers: { default:
  { name: 'Monad explorer', url: explorerUrl } } })`. `viemMonadTestnet` id/nativeCurrency/multicall3/blockTime (400 ms) doğrudur.
- `explorerTxUrl(hash)` = `${explorerUrl}/tx/${hash}`, `explorerAddressUrl(addr)` = `${explorerUrl}/address/${addr}` (02 §0). Sunucu
  hazır `explorer_url` verdiyse (`TxStatusOut`, `TradeOut`, `WalletTransferOut`, `FaucetOut`) **o kullanılır**.
- `addEthereumChainParams()` → `{ chainId: CHAIN_ID_HEX, chainName: 'Monad Testnet', nativeCurrency: NATIVE, rpcUrls: [rpcUrl],
  blockExplorerUrls: [explorerUrl] }` (06 §"Cüzdan ekleme"). WalletConnect ve wagmi yedeği bunu kullanır.
- Değişiklikleri dinlemek için basit `subscribe(listener)`; `client.ts` rpcUrl değişince istemciyi yeniden kurar. UI için
  `useChainConfig()` (zustand `store/chain.ts`, opsiyonel — ekranlar çoğunlukla `metaApi.config` react-query cache'ini okur).
- `assetBySymbol(symbol)`, `assetById(id)`, `assetByAddress(addr)` yardımcıları (küçük harf karşılaştırma, K11).

### 2.2 `client.ts` (ZORUNLU)

```ts
export function getPublicClient(): PublicClient   // tembel singleton; createPublicClient({ chain: getChain(), transport: http(rpcUrl, { batch: true, retryCount: 3, timeout: 15_000 }) })
export async function waitForReceipt(hash: Hex, opts?: { timeoutMs?: number }): Promise<TransactionReceipt>
  // publicClient.waitForTransactionReceipt({ hash, confirmations: 1, pollingInterval: 1_000, timeout: opts.timeoutMs ?? 60_000 })
  // receipt.status === 'reverted' → ChainError('TX_REVERTED', { hash })
export async function getNativeBalance(address: Address): Promise<bigint>   // yalnız yerel cüzdan gas ön kontrolü için
```
RPC limiti (`eth_call`/`estimateGas` 25 rps, 06): frontend zincirden **okuma yapmaz** (bakiyeler, allowance, anlaşma durumu backend'den
gelir, K3). `publicClient` yalnız receipt beklemek ve yerel cüzdanın MON bakiyesini kontrol etmek için kullanılır. `eth_call`
gerekirse `multicall` (viem `multicall`, `multicall3` adresi config'ten) ile tek istekte yapılır.

### 2.3 `abi/` (SC-12'ye bağlı)

- `abi/traderVault.ts`: `export const traderVaultAbi = [...] as const;` — `contracts/scripts/export-abi.sh` üretir (01 §1.4). SC-11 deploy
  öncesi Dalga 1'de dosya **geçici** olarak 01 §4–§6'daki imzalardan elle yazılır (fonksiyonlar: `open, openReserved, propose, fund,
  fundReserved, accept, cancel, trade, settle, claim, reserve, release, releaseAll, getAgreement, getBalances, previewSettle,
  getConfig, isTokenAllowed, nextId`; 01 §5 custom error'lar; 01 §6 event'ler) ve `// TEMPORARY — replace with export-abi.sh output`
  başlığı taşır. Dalga 3'te üretilen dosya ile değiştirilir.
- `abi/index.ts`: `export { traderVaultAbi }`, `export { erc20Abi } from 'viem'` (ayrı ERC-20 ABI dosyası **üretilmez**, 01 §1.4),
  `decodeVaultError(data: Hex): { name: string; args: unknown[] } | null` (`decodeErrorResult({ abi: [...traderVaultAbi, ...erc20Abi], data })`).
- Frontend calldata **üretmez** (backend üretir, K3); ABI yalnız (a) revert verisini ada çevirmek, (b) `TxProgressSheet`'te
  `decodeFunctionData` ile "ne imzalıyorum" satırını doğrulamak (`action` ile fonksiyon adı eşleşmeli; eşleşmezse uyarı) için kullanılır.
- Kontrat adresleri **`/config.contracts`**'tan gelir; deploy JSON'u frontend'e kopyalanmaz (01 §1.4).

### 2.4 `format.ts` (FE-39, K12) — ZORUNLU

```ts
export function shortAddress(address: string, head = 6, tail = 4): string   // '0x67aD0CaE…FF19' → head 0x+4 karakter (02 §13 #30)
export function checksum(address: string): Address                           // viem getAddress; geçersizse ChainError('INVALID_ADDRESS')
export function isEvmAddress(value: string): value is Address                 // viem isAddress (checksum karışıksa doğrular)
export function sameAddress(a?: string | null, b?: string | null): boolean    // küçük harf karşılaştırma (K11)
export function formatRaw(raw: string | bigint, decimals: number, symbol?: string, opts?: FormatOpts): string  // formatUnits + gruplama
export function formatAmount(human: string | null | undefined, symbol?: string, opts?: FormatOpts): string      // API'den gelen insan okunur string
export function parseAmountInput(input: string, decimals: number): { human: string; raw: bigint } | null       // parseUnits; fazla ondalık → null
export function formatMon(wei: string | bigint, opts?: FormatOpts): string    // formatRaw(wei, 18, 'MON', { maxFraction: 4 })
export function formatTxHash(hash: string): string                            // '0x5e1c…9a7b'
interface FormatOpts { maxFraction?: number /* varsayılan min(decimals, 6), sondaki sıfırlar atılır */; minFraction?: number; grouping?: boolean /* true */ }
```
- **`Number()` tutarlar için yasaktır.** `formatAmount` string'i `.` üzerinden ikiye böler, tam kısmı regex ile gruplar (BigInt güvenli),
  kesri `maxFraction`'a kırpar. `Intl.NumberFormat` yalnız tam kısım gruplama ve bps/yüzde için kullanılır.
- `lib/format.ts`'teki `formatAmount(value, code)` **silinir**, çağıranlar (`ListingCard`, `OfferSheet`) `@/lib/chain`'den import eder;
  `formatTRY` (ölü kod) silinir; `parseNumberInput` yalnız **bps/yüzde/gün** girişleri için kalır, tutar girişleri `parseAmountInput`
  kullanır (hata metni: "Use at most {decimals} decimals for {symbol}." → sunucudaki `too_many_decimals` ile aynı kural).
- API'ye gönderilen tutar her zaman `human` string'idir (`"500"`, `"0.25"`); `raw` yalnız cüzdana gösterim/karşılaştırma için.

### 2.5 `errors.ts` (ZORUNLU)

```ts
export type ChainErrorCode =
  | 'USER_REJECTED'        // EIP-1193 4001, viem UserRejectedRequestError, WC "User rejected"
  | 'WRONG_NETWORK'        // 4901 chainDisconnected, viem ChainMismatchError, oturum zinciri ≠ 10143 ve switch başarısız
  | 'CHAIN_NOT_ADDED'      // 4902 (MetaMask), viem SwitchChainError, wagmi ChainNotConfiguredError → addEthereumChain de reddedildi
  | 'WRONG_ACCOUNT'        // cüzdan adresi ≠ UnsignedTxOut.from_address / oturum adresi
  | 'UNAUTHORIZED'         // 4100
  | 'UNSUPPORTED_METHOD'   // 4200 (ör. cüzdan wallet_switchEthereumChain bilmiyor)
  | 'DISCONNECTED'         // 4900, WC session_delete
  | 'REQUEST_PENDING'      // -32002 (MetaMask: bir istek zaten açık)
  | 'INSUFFICIENT_FUNDS'   // -32000 + /insufficient funds/i, viem InsufficientFundsError → MON yok (faucet linki)
  | 'CONTRACT_REVERT'      // ContractFunctionRevertedError / -32603 data → decodeVaultError → details.errorName (ör. 'Paused')
  | 'TX_REVERTED'          // receipt.status === 'reverted' (pre_step approve için)
  | 'TX_TIMEOUT'           // WaitForTransactionReceiptTimeoutError / poll süresi doldu (işlem hâlâ bekliyor olabilir)
  | 'RPC_ERROR'            // HttpRequestError, TimeoutError, LimitExceededRpcError (429)
  | 'SERVER_CHAIN_MISMATCH'// /config.chain.chain_id ≠ 10143
  | 'INVALID_ADDRESS' | 'NOT_CONNECTED' | 'NOT_AVAILABLE' | 'MISSING_CONFIG' | 'EXPIRED' | 'UNKNOWN';

export class ChainError extends Error {
  constructor(message: string, public readonly code: ChainErrorCode, public readonly details?: { errorName?: string; hash?: Hex; cause?: unknown; chainId?: number }) {…}
}
export function toChainError(err: unknown): ChainError     // idempotent; viem sınıfları (instanceof + err.walk?.()), EIP-1193 {code} nesneleri, WC hata metinleri, ApiError DEĞİL
export function vaultErrorCopy(name: string): string       // 'vault:Paused' | 'Paused' → İngilizce metin (tablo aşağıda)
```
`WalletError` (bugünkü `lib/wallet/types.ts`) **kaldırılır**; tek sınıf `ChainError`'dur. Geçiş kolaylığı için
`lib/wallet/types.ts` `export { ChainError as WalletError }` yapar (Dalga 3'te import'lar `ChainError`'a çevrilir, alias silinir).

Vault/ERC-20 hata sözlüğü (`error_code` `"vault:<Ad>"` ya da revert'ten decode edilen ad; 01 §5, 02 §2.6):

| Ad | Arayüz metni (İngilizce) |
|---|---|
| `Unauthorized` | Only the party named in the agreement can do this. Check the connected account. |
| `Paused` | The vault is paused for maintenance. Cancel, settle and claim still work; try again later. |
| `InvalidTerms` | The agreement terms are outside the allowed limits. |
| `TokenNotAllowed` | This token is not on the vault's allow-list. |
| `NotFound` / `ReservationNotFound` | This agreement (or reservation) does not exist on-chain yet. Refresh and try again. |
| `WrongStatus` | The agreement moved to another state. Refresh to see the current actions. |
| `Expired` | The agreement period is over; trades are closed. Settle it instead. |
| `NotExpired` | The agreement has not ended yet; only the parties can settle now. |
| `InsufficientBalance` | The agreement does not hold enough of this token. |
| `TooManyTokens` | An agreement can hold at most 6 tokens. Close a position first. |
| `DrawdownBreached` | This would push the value below the max drawdown floor. Reduce the size. |
| `SlippageExceeded` | Price moved too much. Increase slippage tolerance or try again. |
| `RouterError` | The swap router rejected the trade. Try a smaller amount or a different pair. |
| `NotParty` | Only the agreement parties can cancel it. |
| `ReservationClosed` / `ReservationInsufficient` / `ReservationMismatch` | The listing's locked capital cannot cover this. Lock more capital or open it from your wallet. |
| `ZeroAmount` | Enter an amount greater than zero. |
| `ZeroAddress` / `InvalidRouter` / `InvalidToken` | Invalid address in the request. |
| `TransferFailed` (`SafeERC20FailedOperation`), `erc20:ERC20InsufficientAllowance` | The token approval did not go through. Approve again. |
| `erc20:ERC20InsufficientBalance` | Your wallet does not hold enough {symbol}. Get test tokens from the Wallet screen. |
| `Reentrancy`, `UpgradeError`, `Overflow`, `NotInitialized`, `reverted`, diğer | The transaction was rejected by the contract ({name}). |
| `receipt_mismatch` | The transaction on-chain does not match what was prepared. Nothing was applied; build it again. |
| `not_included` / `reorged` | The transaction was not included in the chain. Build it again. |
| `out_of_gas` | The transaction ran out of gas. Try again; the wallet will re-estimate. |

`lib/errors.ts` (`userMessage`) bu dosyayı kullanır (§8.3).

### 2.6 `tx.ts` — imzasız işlem yürütücüsü (FE-37, 02 §2.7) — ZORUNLU

```ts
export type TxPhase =
  | 'idle' | 'building' | 'checking_wallet' | 'switching_chain'
  | 'approving'            // pre_steps[i] cüzdanda / receipt bekleniyor
  | 'awaiting_signature'   // ana işlem cüzdanda
  | 'submitting'           // POST /tx/submit
  | 'submitted'            // hash bildirildi, receipt yok (TxStatusOut.status = submitted)
  | 'confirmed' | 'failed' | 'rejected' | 'expired' | 'timeout';

export interface TxProgress {
  phase: TxPhase;
  step: { index: number; total: number };   // total = pre_steps.length + 1
  unsigned: UnsignedTxOut | null;
  txHash: Hex | null;                        // ana işlem
  preStepHashes: Hex[];
  status: TxStatusOut | null;                // son /tx yanıtı
  explorerUrl: string | null;                // status.explorer_url ?? explorerTxUrl(txHash)
  error: ChainError | ApiError | null;
}

export interface ExecuteOptions {
  adapter: WalletAdapter;
  onProgress?: (p: TxProgress) => void;
  signal?: AbortSignal;                      // yalnız polling'i keser; zincire gitmiş işlem geri alınamaz
  pollIntervalMs?: number;                   // 2_000
  pollTimeoutMs?: number;                    // 180_000 → 'timeout' (işlem hâlâ bekliyor olabilir; UI "Check again" verir)
  receiptTimeoutMs?: number;                 // 60_000 (pre_steps için)
}

export async function executeUnsignedTx(unsigned: UnsignedTxOut, opts: ExecuteOptions): Promise<TxStatusOut>
export async function flushTxOutbox(): Promise<void>      // hydrate'te ve her yeni submit'ten önce
```

Adımlar (sırası ZORUNLU):
1. `Date.parse(unsigned.expires_at) <= now` → `ChainError('EXPIRED')` (çağıran build'i yeniler).
2. `checking_wallet`: `address = await adapter.getAddress()`; yoksa `NOT_CONNECTED`. `!sameAddress(address, unsigned.from_address)` →
   `WRONG_ACCOUNT` ("Switch your wallet to {shortAddress(from_address)}").
3. `switching_chain`: `chainId = await adapter.getChainId()`; `chainId !== unsigned.chain_id` → `await adapter.switchChain(unsigned.chain_id)`
   (adaptör 4902'de `wallet_addEthereumChain` dener; başarısız → `CHAIN_NOT_ADDED`).
4. Her `pre_steps[i]` için (`approving`, step `i+1/total`): `hash = adapter.sendTransaction({ to, data, value: BigInt(value), gas: BigInt(gas) })`
   → `waitForReceipt(hash)`; `reverted` → `TX_REVERTED`. Backend pre_step hash'lerini **takip etmez** (02 §2.1); yalnız `TxProgress.preStepHashes`'e yazılır.
5. `awaiting_signature`: `txHash = adapter.sendTransaction({ to, data, value, gas, from: from_address })`.
6. Hash alınır alınmaz `plainStorage` `tk.txOutbox` listesine `{ pending_tx_id, tx_hash, at }` yazılır (submit kaybolmasın).
7. `submitting`: `st = txApi.submit(pending_tx_id, txHash)`. `ApiError.status === 0 || >= 500` → 1 s, 3 s, 9 s ile 3 deneme; yine
   başarısız → outbox'ta kalır, `flushTxOutbox()` sonraki açılışta tamamlar; kullanıcıya "Sent — the server will pick it up" + explorer
   linki gösterilir (phase `submitted`). `409 tx_hash_conflict` → outbox'tan silinir, hata gösterilir. Başarılı → outbox'tan silinir.
8. `st.status ∈ {pending, submitted}` iken `pollIntervalMs` aralıkla `txApi.status(pending_tx_id)`; `pollTimeoutMs` dolarsa phase
   `timeout` (hata değil; `TxStatusOut` son hâliyle döner, UI "Still pending — check again" düğmesi `useTxExecutor.recheck()` çağırır).
9. `confirmed` → phase `confirmed`; `failed` → `failed` (`error_code`/`error_message`/`contract_error_code`, `vaultErrorCopy`);
   `expired` → `expired` (imzadan önce süre dolmuş).

Hata yolları: 4001 herhangi bir adımda → phase `rejected`, pending satırına dokunulmaz (süresi dolar, 02 §2.7). `WRONG_NETWORK`/
`CHAIN_NOT_ADDED` → phase `failed`, UI "Switch network" düğmesi. Aynı pending için yürütücü **ikinci kez çağrılmaz**; UI düğmeyi
`phase ∈ {idle, failed, rejected, expired, timeout}` dışında kilitler. `use_reserved_action` (409, build aşamasında) yürütücünün
değil çağıranın işidir (§6.3).

### 2.7 `useTxExecutor.ts` (ZORUNLU)

```ts
export function useTxExecutor(opts?: { invalidate?: QueryKey[]; onConfirmed?: (st: TxStatusOut) => void }) {
  return {
    progress: TxProgress,                                    // başlangıç phase 'idle'
    run: (build: () => Promise<UnsignedTxOut>) => Promise<TxStatusOut | null>,  // 'building' → executeUnsignedTx; hata → progress.error, null döner
    recheck: () => Promise<void>,                            // GET /tx/{id} tek sefer (timeout sonrası)
    reset: () => void,
    isBusy: boolean,                                         // phase ∉ {idle, confirmed, failed, rejected, expired, timeout}
  };
}
```
- `adapter` `@/lib/wallet` `wallet` singleton'ıdır; `run` içinde `useSession.getState().address` ile `from_address` karşılaştırılır.
- `confirmed`'da `queryClient.invalidateQueries` şu anahtarlarla: `['agreement', agreement_id]`, `['agreements']`, `['listing', listing_id]`,
  `['listings']`, `['wallet']`, `['dashboard']`, `['tx', pending_tx_id]` + `opts.invalidate`.
- `build` `ApiError 409 use_reserved_action` fırlatırsa `run` **bir kez** `details.action` ile yeniden build eder (çağıran build fonksiyonu
  `(action) => …` imzasını da kabul edebilir; basitlik için `run(build, { onUseReserved?: (action) => Promise<UnsignedTxOut> })`).
- UI bileşeni `components/tx/TxProgressSheet.tsx` (§6.6) `progress`'i çizer.

---

## 3. `src/lib/wallet/` — cüzdan adaptörü (FE-31…FE-34)

```
src/lib/wallet/
├── index.ts            # wallet, restoreWalletMode, walletConnectAvailable, WC_WALLETS, openPairing, tipler — localWallet DIŞA AÇILMAZ
├── types.ts            # WalletAdapter, ConnectOptions, TxRequest, WalletInfo, WalletKind
├── wallet.ts           # native dağıtıcı: walletconnect | local (mod açıkça seçilir)
├── wallet.web.ts       # wagmi createConfig + injected + walletConnect; Metro web'de bunu çözer
├── walletconnect.ts    # UniversalProvider, eip155:10143
├── local.ts            # native uygulama içi cüzdan (viem account + expo-secure-store)
├── local.web.ts        # web stub: available=false, tüm çağrılar ChainError('NOT_AVAILABLE')
└── deeplinks.ts        # MetaMask, Rainbow, Trust, Phantom şemaları + openPairing
```
Silinenler: `sep7.ts`, `lib/stellar/*`, `auth/sep10.ts`, `signAuthEntry`, `isFreighterAvailable`, `FREIGHTER_WALLET_ID`,
`buildSep7TxUri/buildSignInUri/openInWallet/SEP7_SCHEME`, `wallet.ts:10`'daki `lib/auth/sep7.ts` atfı.

### 3.1 `types.ts` (ZORUNLU)

```ts
import type { Address, Hex } from 'viem';

export type WalletKind = 'injected' | 'walletconnect' | 'local';
export interface WalletInfo { id: string; kind: WalletKind; name: string; icon?: string }   // name: 'MetaMask' | 'Rabby' | 'WalletConnect' | 'In-app wallet' …

export interface ConnectOptions {
  /** Native: hangi yol. Verilmez ve kayıtlı mod da yoksa ChainError('MISSING_CONFIG') — sessizce yerel cüzdan AÇILMAZ (inceleme §D hata 5). */
  mode?: 'walletconnect' | 'local';
  /** Web: wagmi bağlayıcı kimliği; verilmezse 'injected' denenir, yoksa 'walletConnect'. */
  connectorId?: 'injected' | 'walletConnect';
  /** WalletConnect eşleşme URI'si (QR / deep link). */
  onUri?: (uri: string) => void;
  /** Bağlandıktan sonra bu zincire geçilsin (varsayılan CHAIN_ID). Başarısızlık connect'i düşürmez; chainId döner, UI banner gösterir. */
  targetChainId?: number;
}

export interface TxRequest { to: Address; data: Hex; value: bigint; gas?: bigint; from?: Address }

export type WalletEvent =
  | { type: 'accountsChanged'; accounts: Address[] }
  | { type: 'chainChanged'; chainId: number }
  | { type: 'disconnect' };

export interface WalletAdapter {
  readonly available: boolean;                 // platformda en az bir yol var mı
  readonly kind: WalletKind | null;            // aktif yol (bağlı değilse null)
  connect(options?: ConnectOptions): Promise<{ address: Address; chainId: number; wallet: WalletInfo }>;
  disconnect(): Promise<void>;
  getAddress(): Promise<Address | null>;       // checksum (viem getAddress)
  getChainId(): Promise<number | null>;
  switchChain(chainId: number): Promise<void>; // wallet_switchEthereumChain → 4902/unsupported → wallet_addEthereumChain(addEthereumChainParams()) → yine hata → ChainError('CHAIN_NOT_ADDED')
  signMessage(message: string): Promise<Hex>;  // EIP-191 personal_sign; UTF-8 metin girer, 0x+130 hex çıkar
  sendTransaction(tx: TxRequest): Promise<Hex>;// eth_sendTransaction; tx hash döner (receipt beklemez)
  on(listener: (e: WalletEvent) => void): () => void;   // abonelikten çıkma fonksiyonu döner
  abortPairing?(): Promise<void>;              // yalnız WalletConnect
}
```
XDR, `signTransaction`, `signAuthEntry`, `networkPassphrase` **yoktur**.

### 3.2 `wallet.web.ts` — wagmi (FE-32) — ZORUNLU

```ts
import { createConfig, createStorage, http, reconnect, connect, disconnect, getAccount, getChainId, switchChain, signMessage, sendTransaction, watchAccount, watchChainId } from 'wagmi/actions' /* ve 'wagmi' (createConfig, createStorage, http) */;
import { injected, walletConnect } from 'wagmi/connectors';

const chain = getChain();
const connectors = [
  injected({ shimDisconnect: true }),                                   // MetaMask, Rabby, Phantom EVM… EIP-6963 keşfi (multiInjectedProviderDiscovery: true)
  ...(env.walletConnectProjectId ? [walletConnect({ projectId: env.walletConnectProjectId, showQrModal: true, metadata: APP_METADATA })] : []),
];
export const wagmiConfig = createConfig({
  chains: [chain],
  connectors,
  transports: { [CHAIN_ID]: http(rpcUrl) },
  storage: createStorage({ storage: typeof window !== 'undefined' ? window.localStorage : undefined }),   // oturum kalıcılığı; reconnect() hydrate'te
  ssr: false,
  multiInjectedProviderDiscovery: true,
});
```
- `connect({ connectorId })`: `connect(wagmiConfig, { connector, chainId: CHAIN_ID })` → wagmi bağlanınca `switchChain` dener; cüzdanda
  zincir yoksa `switchChain(wagmiConfig, { chainId: CHAIN_ID, addEthereumChainParameter: addEthereumChainParams() })` → wagmi 4902'yi
  `wallet_addEthereumChain` ile karşılar. Kullanıcı eklemeyi reddederse connect **başarılıdır**, `chainId` farklı döner; UI `ChainBanner`
  gösterir (imza için zincir şart değil ama işlemler için şart).
- `injected` bulunamazsa (`getAccount` connector yok / `window.ethereum` yok) → `ChainError('NOT_AVAILABLE', 'No browser wallet found.
  Install MetaMask or Rabby, or use WalletConnect.')`; UI "Install MetaMask" linki (`https://metamask.io/download/`).
- `signMessage(message)` → `signMessage(wagmiConfig, { message })`. `sendTransaction(tx)` → `sendTransaction(wagmiConfig, { to, data,
  value, gas, chainId: CHAIN_ID })` (gas backend'den; wagmi/viem yeniden tahmin etmez).
- `on`: `watchAccount` (address değişimi → `accountsChanged`, `status==='disconnected'` → `disconnect`), `watchChainId` → `chainChanged`.
- `restoreWalletMode()` web'de `null`; `walletConnectAvailable()` `Boolean(projectId)`; `localWalletAvailable = false`.
- `getAddress()` → `getAccount(wagmiConfig).address` (checksum); ilk çağrıda `reconnect(wagmiConfig)` beklenir.

### 3.3 `wallet.ts` — native dağıtıcı (K9) — ZORUNLU

- `type NativeWalletMode = 'walletconnect' | 'local'`; kayıtlı mod `plainStorage` `tk.walletMode` (değer kümesi yalnız bu ikisi; eski
  `'sep7'` değeri okunursa silinir).
- `restoreWalletMode(): Promise<NativeWalletMode | null>` — **varsayılan yok**. `connect(options)`: `mode = options.mode ?? stored`;
  ikisi de yoksa `ChainError('MISSING_CONFIG', 'Choose how to connect: WalletConnect or in-app wallet.')`. `mode === 'walletconnect'`
  ama `projectId` yok → `MISSING_CONFIG`. Yerel cüzdan yalnız `mode: 'local'` ile açıkça istenince oluşturulur/yüklenir.
- `available`: `walletConnectAvailable() || localWalletAvailable` (native'de `local` her zaman var → true).
- Diğer metotlar aktif moda yönlendirir; `kind` `'walletconnect' | 'local' | null`. `disconnect()`: WC ise oturumu kapatır; yerel
  cüzdanı **silmez** (ayrı `forgetLocalWallet()`, §3.5).
- `on`: WC olaylarını iletir; yerel cüzdanda olay yoktur (chainId sabit 10143).

### 3.4 `walletconnect.ts` — `eip155:10143` (FE-33) — ZORUNLU değişiklikler

| Bugün | Olacak |
|---|---|
| `CHAIN = 'stellar:testnet'` | `const CAIP_CHAIN = \`eip155:${CHAIN_ID}\`` |
| `METHODS = ['stellar_signXDR', …]` | `METHODS = ['personal_sign', 'eth_sendTransaction', 'eth_signTypedData_v4', 'wallet_switchEthereumChain', 'wallet_addEthereumChain', 'eth_chainId', 'eth_accounts']` (typed data ileride permit için, K4 sonraki sprint) |
| `events: []` | `EVENTS = ['accountsChanged', 'chainChanged']` |
| `provider.connect({ namespaces: { stellar: … } })` | `provider.connect({ optionalNamespaces: { eip155: { chains: [CAIP_CHAIN], methods: METHODS, events: EVENTS, rpcMap: { [CHAIN_ID]: rpcUrl } } } })` — **optional**: Monad'ı tanımayan cüzdan da eşleşir, sonra `switchChain` (inceleme §D güvenlik notu) |
| `session.namespaces.stellar.accounts[0]` → `G…` | `accounts = session.namespaces.eip155?.accounts ?? []` → `'eip155:10143:0x…'`; **önce** chain `10143` olan hesap, yoksa ilk `eip155` hesabı + `chainId` o hesabınki (UI switch ister). Adres `getAddress()` ile checksum |
| — | Bağlantı sonrası `provider.setDefaultChain(CAIP_CHAIN, rpcUrl)` |
| `stellar_signXDR` | `signMessage(m)`: `provider.request<Hex>({ method: 'personal_sign', params: [toHex(m), address] }, CAIP_CHAIN)` (mesaj **hex-kodlu UTF-8**, sıra `[data, address]`) |
| — | `sendTransaction(tx)`: `provider.request<Hex>({ method: 'eth_sendTransaction', params: [{ from: address, to, data, value: numberToHex(value), gas: tx.gas ? numberToHex(tx.gas) : undefined }] }, CAIP_CHAIN)` — hex quantity, öndeki sıfır yok (`0x0`) |
| — | `getChainId()`: oturumdaki hesabın zinciri; `switchChain(id)`: `wallet_switchEthereumChain [{ chainId: CHAIN_ID_HEX }]` → hata 4902 / `UNSUPPORTED_METHOD` / mesajda "Unrecognized chain" → `wallet_addEthereumChain [addEthereumChainParams()]` → hâlâ hata → `CHAIN_NOT_ADDED` (UI: "Add Monad Testnet to your wallet manually: RPC …, chain 10143" + "or use the in-app wallet") |
| — | `on`: `provider.on('session_event', ({ params }) → params.event.name 'accountsChanged' | 'chainChanged')`, `provider.on('session_delete' | 'session_expire', → disconnect)`, `provider.on('chainChanged')` |
| `APP_METADATA.description` "…on Stellar." | "Rent a trader; your capital stays locked in a vault on Monad." `url`/`icons` aynı; `redirect.native = Linking.createURL('/')` korunur |
| `mapError` metin eşlemesi | `toChainError` (§2.5); `/reject|denied|cancel/i` → `USER_REJECTED`, `/chain|namespace|unsupported/i` → `WRONG_NETWORK`, `/expire|timeout/i` → `RPC_ERROR` |

Korunur: `getProvider()` tembel singleton, `abortPairingAttempt()` temizliği, `display_uri` → `onUri`, `bringWalletToFront()` (peer
`redirect.native` → `WC_WALLETS` şeması), `withTimeout(120_000)`, `cleanupPendingPairings()`. `WC_WALLETS` eşlemesi `peer.name`
ile küçük harf karşılaştırılır ("MetaMask Wallet" → `includes('metamask')`).

### 3.5 `local.ts` / `local.web.ts` — uygulama içi cüzdan (FE-34, K9) — ZORUNLU

```ts
import { generatePrivateKey, privateKeyToAccount } from 'viem/accounts';
import { createWalletClient, http } from 'viem';
import * as SecureStore from 'expo-secure-store';

const KEY = STORAGE_KEYS.walletSecret;   // 'tk.walletSecret' → değer 0x + 64 hex
export const localWalletAvailable = true; // local.web.ts: false

export const localWallet = {
  exists(): Promise<boolean>,
  address(): Promise<Address | null>,
  create(): Promise<Address>,                 // mevcut varsa üstüne yazmaz; generatePrivateKey() → SecureStore.setItemAsync(KEY, pk, { keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY })
  importPrivateKey(hex: string): Promise<Address>,   // /^0x[0-9a-fA-F]{64}$/ değilse ChainError('INVALID_ADDRESS' → mesaj "That is not a valid private key (0x + 64 hex).")
  exportPrivateKey(): Promise<string | null>, // yedekleme ekranı; loglanmaz, ağa gitmez
  forget(): Promise<void>,                    // SecureStore.deleteItemAsync + cache temizliği
  signMessage(message: string): Promise<Hex>, // account.signMessage({ message })
  sendTransaction(tx: TxRequest): Promise<Hex>,
};
```
- `sendTransaction`: `createWalletClient({ account, chain: getChain(), transport: http(rpcUrl) }).sendTransaction({ to, data, value, gas,
  type: 'eip1559' })` — viem nonce ve `maxFeePerGas/maxPriorityFeePerGas`'ı RPC'den alır; `gas` backend'den gelir (yeniden tahmin yok,
  Monad `gas_limit` üzerinden ücretlendirir, 06). Gönderimden önce `getNativeBalance(address) < gas × maxFeePerGas` ise
  `ChainError('INSUFFICIENT_FUNDS', 'You need MON for gas. Get some from the Monad faucet.')` (faucet linki UI'da).
- Web'de `local.web.ts` stub'ı dışında **hiçbir yerel cüzdan kodu bundle'a girmez**; `lib/wallet/index.ts` `localWallet`'ı **dışa
  açmaz** (bugünkü `wallet/index.ts:9` güvenlik bulgusu). Ekranlar yerel cüzdana `wallet.kind === 'local'` ve
  `getLocalWallet()` (native `local.ts`'ten, web'de `null`) üzerinden ulaşır.
- Kullanıcı deneyimi: seçim ekranında "In-app wallet (testnet only)"; ilk oluşturmada tek seferlik uyarı: "This wallet lives on this
  device only. Back up the private key from the Wallet screen. Testnet only — do not send real funds." Yedekleme/unutma §6.5.

### 3.6 `deeplinks.ts` (FE-33) — ZORUNLU

```ts
export const WC_WALLETS: WalletLinkTarget[] = [
  { id: 'metamask', label: 'MetaMask', native: 'metamask://', universal: 'https://metamask.app.link', verified: true },
  { id: 'rainbow',  label: 'Rainbow',  native: 'rainbow://',  universal: 'https://rnbwapp.com',        verified: true },
  { id: 'trust',    label: 'Trust Wallet', native: 'trust://', universal: 'https://link.trustwallet.com', verified: true },
  { id: 'phantom',  label: 'Phantom',  native: 'phantom://',  universal: 'https://phantom.app/ul',     verified: false },  // Gün 0'da WC explorer API ile doğrulanır (§0.1-2)
];
```
`pairingLinks()` biçimleri aynı kalır (`<universal>/wc?uri=…`, `<native>wc?uri=…`, ham `wc:`). Freighter/LOBSTR/xBull satırları silinir.
Doğrulama komutu (Gün 0): `curl "https://explorer-api.walletconnect.com/v3/wallets?projectId=$WC_PROJECT_ID&search=phantom"` →
`mobile.native/universal`.

### 3.7 `index.ts` dışa açılan yüzey

`wallet`, `restoreWalletMode`, `walletConnectAvailable`, `localWalletAvailable`, `getLocalWallet` (native: `localWallet`, web: `null`),
`WC_WALLETS`, `pairingLinks`, `openPairing`, tipler (`WalletAdapter`, `ConnectOptions`, `TxRequest`, `WalletEvent`, `WalletKind`,
`WalletInfo`, `NativeWalletMode`), `ChainError` (re-export). **`localWallet` doğrudan export edilmez.**

---

## 4. Kimlik: SIWE ve oturum (FE-35, FE-36)

### 4.1 `src/lib/auth/siwe.ts` (ZORUNLU) — `sep10.ts` silinir

```ts
import { parseSiweMessage } from 'viem/siwe';

export type SiweErrorCode = 'BACKEND_MISSING' | 'INVALID_MESSAGE' | 'ADDRESS_MISMATCH' | 'WRONG_NETWORK' | 'DOMAIN_MISMATCH' | 'EXPIRED' | 'NO_TOKEN';
export class SiweError extends Error { constructor(message: string, public readonly code: SiweErrorCode) {…} }
export interface SiweSession { token: string; expiresAt: number | null; address: Address; registered: boolean; user: MeOut | null }

export async function loginWithSiwe(adapter: WalletAdapter, address: Address): Promise<SiweSession>
```
Akış (02 §1):
1. `nonce = await authApi.nonce(address)` → `NonceOut { nonce, message, expires_at, chain_id, domain }`. 404 → `BACKEND_MISSING`
   ("Sign-in endpoint not found (/auth/nonce). Is the server on the Monad build?").
2. **Körlemesine imza yok** — `parsed = parseSiweMessage(nonce.message)`; `parsed.address` yok ya da `!sameAddress(parsed.address, address)`
   → `ADDRESS_MISMATCH`; `parsed.chainId !== CHAIN_ID` → `WRONG_NETWORK`; `parsed.domain !== nonce.domain` ya da (config yüklüyse)
   `!== config.auth.siwe_domain` → `DOMAIN_MISMATCH`; `parsed.nonce !== nonce.nonce` → `INVALID_MESSAGE`;
   `parsed.expirationTime && parsed.expirationTime <= now` → `EXPIRED`. İstemci mesajı **kurmaz ve değiştirmez**.
3. `signature = await adapter.signMessage(nonce.message)` (byte'ı byte'ına aynı string).
4. `login = await authApi.verify({ message: nonce.message, signature })` → `LoginOut { token, expires_at, address, registered, user }`.
   Sunucu hataları (`siwe_*`, `nonce_*`, `signature_invalid`, `invalid_address`, `rate_limited`) `ApiError.code` ile `userMessage`'a düşer.
5. `token` yoksa `NO_TOKEN`. Dönüş: `{ token, expiresAt: Date.parse(expires_at) || null, address: login.address, registered, user }`.

Zincir notu: `personal_sign` zincirden bağımsızdır; ama WalletConnect isteği `eip155:10143` namespace'inde gider, bu yüzden mobilde
bağlanırken zincir eklenmiş olmalı (§3.4). Web'de imza için zincir şart değil; `connect` zaten switch'i denedi, başarısızsa banner.

### 4.2 `src/store/session.ts` değişiklikleri (FE-36) — inceleme §D hatalar 1–5 çözümleri ZORUNLU

```ts
interface SessionState {
  status: 'booting' | 'signed_out' | 'wallet_connected' | 'signed_in';
  address: Address | null;
  chainId: number | null;            // yeni — cüzdanın zinciri; ChainBanner CHAIN_ID ile karşılaştırır
  walletKind: WalletKind | null;     // yeni ('injected' | 'walletconnect' | 'local')
  walletName: string | null;         // yeni ('MetaMask', 'In-app wallet' …)
  walletConnected: boolean;          // yeni — JWT var ama cüzdan bağlantısı düşmüş olabilir
  role, profile, registered, expiresAt, pairingUri, walletMode, onboardingSeen, error;
  hydrate(); markOnboardingSeen();
  connectWallet(options: { mode?: NativeWalletMode; connectorId?: 'injected' | 'walletConnect' }): Promise<Address>;  // mod/bağlayıcı AÇIK; sessiz yerel cüzdan yok
  cancelPairing(); signIn(); register(payload); refreshProfile(); switchToAppChain(); signOut(); forgetLocalWallet(); clearError();
}
interface PersistedSession { address: string; role: UserRole | null; registered: boolean; expiresAt: number | null; walletKind: WalletKind | null }
```

| # | İnceleme §D hatası | Çözüm |
|---|---|---|
| 1 | **Kayıt döngüsü**: `register()` `MeOut` sanıyor, token saklanmıyor | `const out = await usersApi.register(payload)` → `RegisterOut {user, token, expires_at}`; `secureStorage.set(jwt, out.token)`; `persist({ address, role: out.user.role, registered: true, expiresAt: Date.parse(out.expires_at), walletKind })`; `set({ profile: out.user, role: out.user.role, registered: true, expiresAt, status: 'signed_in' })`. `index.tsx` artık rolü bulur |
| 2 | **Refresh kilitlenmesi**: `authApi.refresh` 401'de köprü kendini bekliyor | `authApi.refresh = () => http.post<LoginOut>('/auth/refresh', undefined, { skipAuthBridge: true })` (client.ts `RequestOptions.skipAuthBridge`, §5.3). Köprü: `ApiError 401` ve `code ∈ {session_expired, token_expired, token_invalid, missing_token}` → `null` döner → `onSessionExpired()`; başka hata (ağ) → `null` ama oturum **kapatılmaz** (istek hatası ekrana düşer). `refreshInFlight` `finally`'de sıfırlanır (mevcut) |
| 3 | `JSON.parse` try/catch dışında (`session.ts:84`) | `safeParsePersisted(raw): PersistedSession | null` — parse hatası ya da şema dışı değer → `clearStoredSession()` ve `signed_out` |
| 4 | `hydrate` `registered=false` bırakıyor | `PersistedSession.registered` alanı; eski kayıtta yoksa `role !== null` türetilir. `hydrate` sonrası `refreshProfile()` yine tazeler |
| 5 | Mobilde gizli yerel cüzdan (`details.tsx` → `signIn()` → `connectWallet()` → `local`) | `signIn()` **bağlamaz**: `address` yoksa `ChainError('NOT_CONNECTED')`. Bağlama yalnız `connectWallet({ mode | connectorId })` ile ve yalnız `ConnectWalletPanel` (§6.1) üzerinden; kayıt ekranı da aynı paneli kullanır |

Ek kurallar:
- `hydrate`: `flushTxOutbox()` (§2.6) çağrılır; web'de `wallet.getAddress()` (`reconnect`) ile `walletConnected` belirlenir; native'de
  `restoreWalletMode()` `null` olabilir.
- `connectWallet` sonrası `wallet.on(...)` aboneliği: `accountsChanged` → yeni adres ≠ `address` ise `signOut()` + `error: 'Wallet account
  changed. Sign in again with the new account.'`; `chainChanged` → `set({ chainId })`; `disconnect` → `set({ walletConnected: false })`
  (JWT korunur; işlem denemesinde "Reconnect wallet" istenir).
- `switchToAppChain()` → `wallet.switchChain(CHAIN_ID)`; hata `userMessage` ile `error`'a.
- `signIn()`: `loginWithSiwe(wallet, address)` → jwt + persist + `set({ status:'signed_in', role, profile, registered, expiresAt })`.
- `signOut()`: `clearStoredSession()`, `wallet.disconnect()` (WC oturumu kapanır, wagmi `disconnect`), yerel cüzdan **kalır**; state sıfırlanır.
- `forgetLocalWallet()`: yalnız native + `walletKind === 'local'`; önce `signOut()`, sonra `getLocalWallet().forget()`, `tk.walletMode`
  silinir. UI iki adımlı onay ister (§6.5).
- `PersistedSession.address` her zaman checksum; karşılaştırmalar `sameAddress`.

### 4.3 `client.ts` (§5.3) ve `lib/auth/index.ts`

`lib/auth/index.ts`: `export { loginWithSiwe, SiweError } from './siwe'; export type { SiweErrorCode, SiweSession }; export { decodeJwt,
jwtExpiresAt, isExpired }`. `jwt.ts` kalır; `JwtPayload.sub` yorumu "cüzdan adresi (küçük harf)" olur; yeni claim'ler `auth_time`, `jti`,
`uid`, `role` tipe eklenir (okuma amaçlı).

---

## 5. API katmanı (FE-38)

### 5.1 Tip üretimi — `openapi-typescript` (ZORUNLU)

- Çıktı: `src/lib/api/schema.d.ts` (**commit edilir**; typecheck backend'siz çalışır). ESLint/Prettier ignore listesine eklenir
  (`eslint.config.js` `ignores: [..., 'src/lib/api/schema.d.ts', 'src/lib/chain/abi/*.ts']`, `.prettierignore` aynı).
- `npm run gen:api` — canlı ya da yerel Monad backend'inden: `OPENAPI_URL=https://monadback.yolalapp.com/openapi.json npm run gen:api`
  (varsayılan `http://127.0.0.1:8013/openapi.json`, K13 port). `docs_enabled=false` prod'da kapalıysa yerel/staging kullanılır.
- Çevrimdışı yol: backend `.venv` ile `openapi.json` üretilir, sonra `npm run gen:api:file`:
  ```sh
  cd backend && .venv/bin/python -c "import json; from app.main import create_app; print(json.dumps(create_app().openapi()))" > ../app/openapi.json
  ```
  `Settings` zorunlu alanlar ister (bugün `pool_key_encryption_key` vb.; BE-01 sonrası `DATABASE_URL`, `JWT_SECRET`… ) → komut
  `backend/.env.example`'dan sahte değerlerle çalıştırılır (`env $(cat .env.example | xargs) …`). Backend `scripts/export_openapi.py`
  sağlarsa o kullanılır. `app/openapi.json` `.gitignore`'a eklenir.
- `types.ts` **yalnız takma ad** taşır: `import type { components, operations, paths } from './schema'; export type Schemas =
  components['schemas']; export type MeOut = Schemas['MeOut']; …` Enum birlikleri şemadan türer (`Schemas['ListingStatus']`). Elle tip
  yazılmaz; `Page<T>` generic'i `{ items: T[]; total: number; limit: number; offset: number }` olarak elle tanımlanır (OpenAPI'de
  `Page_ListingOut_` gibi somut adlar üretilir; `Page<T>` bunların ortak şeklidir).
- Kontrol: Dalga 3'te `npm run gen:api && git diff --exit-code src/lib/api/schema.d.ts` (sözleşme sürüklenmesi yakalanır).

### 5.2 `endpoints.ts` hedef yüzeyi (02 §13 #1–35 uygulanır)

Aşağıdaki imzalar bağlayıcıdır; dönüş tipleri `schema.d.ts`'ten. `P = { limit?: number; offset?: number }`.

| Modül | Fonksiyon → tip | Not (02 §13) |
|---|---|---|
| `metaApi` | `config(): ConfigOut` (auth=false) · `health()` · `healthChain(): HealthChainOut` (`GET /health/chain`) | #29, #32; `assets()` → `assetsApi` |
| `authApi` | `nonce(address): NonceOut` `{address}` · `verify({message, signature}): LoginOut` · `me(): AuthMeOut` · `refresh(): LoginOut` (**skipAuthBridge**) | #2–#6; `sep10*`, `verifyNonce` silinir |
| `usersApi` | `register(RegisterIn): RegisterOut` · `me(): MeOut` · `updateMe(UserUpdateIn): MeOut` · `byUsername(u): UserOut` · `byId(id): UserOut` | #1, #33, #34 |
| `tradersApi` | `list(P & {q?}): Page<TraderCardOut>` · `profile(id): TraderProfileOut` · `follow/unfollow(id): FollowOut` · `ratings(id, P): Page<RatingOut>` | #22, #23 |
| `discoverApi` | değişmez (`cursor`) | |
| `listingsApi` | `list({kind?, sort?, …} & P): Page<ListingOut>` · `mine({status?} & P): Page<ListingOut>` · `mineCounts(): ListingCountsOut` · `saved(P): Page<ListingOut>` · `byId(id): ListingDetailOut` · `create(ListingCreateIn): ListingOut` · `update(id, ListingUpdateIn): ListingOut` · `pause/resume/close(id): ListingOut` · **`reserveTx(id): UnsignedTxOut`** (`POST /listings/{id}/tx/reserve`) · **`releaseTx(id, {amount?}): UnsignedTxOut`** | #16–#18, #34 |
| `offersApi` | `create(OfferCreateIn): OfferOut` · `list({box: 'inbox'\|'outbox'\|'all', status?, listing_id?} & P): Page<OfferOut>` · `byId` · `accept(id): OfferAcceptOut` · `reject(id, {reason?})` · `withdraw(id)` · `stats(): OfferStatsOut` | #19–#21 |
| `agreementsApi` | `list({role?, status?} & P): Page<AgreementOut>` · `byId(id, {refresh?}): AgreementOut` · `quote(id, {token_in, token_out, amount_in, slippage_bps?, deadline_seconds?}): QuoteOut` · `trades(id, P): Page<TradeOut>` · `valueHistory(id, {range}): ValueHistoryOut` · **`buildTx(id, action: TxAction, body?: TxActionIn): UnsignedTxOut`** · **`buildTradeTx(id, TradeTxIn): UnsignedTxOut`** · `rate(id, RatingIn): RatingOut` · `rating(id): RatingOut` | #9, #24–#26, #34 |
| `tradesApi` | `update(id, {note?, notify_investors?}): TradeOut` | #34 |
| `txApi` | **`submit(pendingTxId, txHash): TxStatusOut`** gövde `{pending_tx_id, tx_hash}` · **`status(pendingId): TxStatusOut`** | #7, #8 |
| `walletApi` | `get(): WalletOut` · `depositInfo(): DepositInfoOut` · **`transfer({asset_id, to, amount}): UnsignedTxOut`** · **`faucet({asset_id}): FaucetOut`** · **`transactions(P): Page<WalletTransferOut>`** (404 tolere edilir) | #10, #11; `buildPaymentTx/buildTrustlineTx` silinir |
| `anchorApi` | **silinir** | K7 |
| `dashboardApi` | `get(): DashboardOut` (`CustomerDashboardOut \| TraderDashboardOut`, `role` ayrıştırıcı) | |
| `activityApi` | `feed(P): Page<ActivityItemOut>` | #16 |
| `conversationsApi` | `list(P)` · `start({user_id}): ConversationOut` · `unreadCount(): ConversationsUnreadOut {conversations, messages}` · `byId` · `messages(id, {after?, before?, limit?}): MessagesPageOut` · `send(id, {body})` · `markRead(id)` | #13, #27, #34 |
| `notificationsApi` | `list({category?, unread_only?} & P): Page<NotificationOut>` · `unreadCount(): UnreadCountOut {unread, by_category}` · `markAllRead(category?): MarkReadOut` gövde `{all: true, category}` · `markRead(id): MarkReadOut` · `setPushToken(token): MeOut` · `byId(id)` | #12, #14, #15 |
| `assetsApi` | `list({base_only?, onchain_only?}): AssetOut[]` · `byId(id): AssetOut` | #31, #34 |
| `fxApi` | `rates(): FxOut` · `convert({amount_usd}): FxConvertOut` | #28 |

`TxAction = 'propose' | 'open' | 'open_reserved' | 'fund' | 'fund_reserved' | 'accept' | 'cancel' | 'settle' | 'claim'` (şemadan).
Sayfalama yardımcısı `src/lib/api/paging.ts`: `nextOffset(page: Page<unknown>): number | undefined` (`offset + limit < total ?
offset + limit : undefined`) — `useInfiniteQuery({ initialPageParam: 0, getNextPageParam: nextOffset })`.

### 5.3 `client.ts` değişiklikleri

- `RequestOptions { body?, auth?: boolean, query?, retried?, skipAuthBridge?: boolean }`; `http.post(path, body?, opts?: boolean |
  { auth?: boolean; skipAuthBridge?: boolean })` (boolean geriye uyum). 401 dalı: `if (res.status === 401 && auth && !retried &&
  !skipAuthBridge && authBridge)`.
- `ApiError.details` alanı eklenir (`envelope.details`), `use_reserved_action`/`rate_limited`/`insufficient_funds` bunları kullanır.
- 429: `Retry-After` başlığı ya da `details.retry_after_seconds` `ApiError.retryAfterSeconds`'a yazılır.
- Yorumlardaki "SEP-10" temizlenir.

### 5.4 Okunmamış sayaçlar ve fx

- `src/lib/api/hooks.ts` (opsiyonel, Dalga 2 (e)): `useUnreadCounts()` → `useQueries` (`notificationsApi.unreadCount`,
  `conversationsApi.unreadCount`), `refetchInterval: 30_000`, `enabled: status === 'signed_in'`; `TabBar` rozetleri Sprint 3.
- Fiyat/TL karşılığı: `config.usd_prices` (**symbol anahtarlı**, `MON` `null` olabilir) ve `fxApi.rates()`; `usdValue(human, symbol)`
  yardımcısı `formatAmount` ile aynı BigInt-güvenli yolu izler (ondalık çarpım için `parseUnits(price, 18)`). TL gösterimi sprintte
  yalnız cüzdan ekranında toplam için, `fx.stale` ise gizlenir.

---

## 6. Ekranlar

Genel kurallar (Sprint 1 DoD devam eder): veri yalnız `src/lib/api`'den; sahte veri yok; her ekranda boş / yükleniyor / hata durumu;
`userMessage(err)`; Risk Strip. Tutarlar `formatAmount(value, asset.symbol)`; adresler `shortAddress`; hash'ler `formatTxHash` + explorer linki
(`Linking.openURL`). Ağ etiketi her yerde **"Monad Testnet"**.

### 6.1 Değişen mevcut ekranlar

**`(auth)/login.tsx`**
- Silinir: `stellarConfig`, `isFreighterAvailable`, `FREIGHTER_WALLET_ID`, Freighter/xBull/Albedo metinleri, `network_passphrase` karşılaştırması.
- Sunucu kontrolü: `metaApi.config` → `applyServerConfig`; `backend.data.chain.chain_id !== CHAIN_ID` → "Server is on chain {id}; this app
  is built for Monad Testnet (10143)." (giriş düğmeleri kapalı). Başarılı: "Server connected · {auth.siwe_domain}".
- Yeni bileşen **`components/wallet/ConnectWalletPanel.tsx`** (login ve register/details ortak):
  - Web: `Connect wallet` (birincil; `connectWallet({ connectorId: 'injected' })`; `NOT_AVAILABLE` → "No browser wallet found" +
    `Install MetaMask` linki), `WalletConnect` (ikincil; projectId varsa; wagmi QR modalı açılır).
  - Native: `Connect wallet` (birincil; `connectWallet({ mode: 'walletconnect' })` → `WalletConnectSheet` QR + "Open in MetaMask/Rainbow/
    Trust/Phantom"; projectId yoksa düğme gizlenir ve uyarı), `Use in-app wallet` (ikincil; `connectWallet({ mode: 'local' })`; yerel
    cüzdan zaten varsa etiket `Continue with in-app wallet · 0x67aD…FF19`).
  - Bağlanınca `onConnected(address)` → login `signIn()` → `router.replace('/')`. Bağlı ama zincir farklıysa panelde `ChainBanner`.
- Pill: `Network: Monad Testnet`. Kopya §8.1.
- Kapsam dışı: "Sign up" akışı aynı (`register/role`).

**`(auth)/register/details.tsx`**
- `shortAddress` `@/lib/chain`'den. Cüzdan kartı: adres yoksa **`ConnectWalletPanel`** (compact) gösterilir — `signIn()` doğrudan çağrılmaz.
  `onSubmit`: `status !== 'signed_in'` ise `signIn()` (adres var, imza istenir), sonra `register(payload)` → token saklanır (§4.2 #1) →
  `router.replace('/')`. Kart, `walletName` pill'i gösterir ("MetaMask" / "In-app wallet").
- Tutar alanları (`budget_amount`, `min_capital`): `parseAmountInput(input, baseAsset.decimals)` (config'ten `default_base_asset_id`);
  hata metni "Enter a valid amount (up to {decimals} decimals)."; `commission` bps mantığı aynı.
- Yorumlar: "SEP-10" → "SIWE".

**`(customer)/discover.tsx`, `(trader)/discover.tsx`**
- Mantık aynı; yalnız import değişimleri. Trader discover **hata 6** (inceleme §D): sağa kaydırma OfferSheet'i açıyor ve kart deck'ten
  düşüyor; sheet iptal edilirse kart kaybolur. Çözüm: `SwipeDeck`'e `restore()` (son kaydırılan kartı geri getirir) eklenir;
  `OfferSheet.onClose` teklif gönderilmediyse `deckRef.current?.restore()` çağırır. Opsiyonel ama küçük; (d) ya da (e) değil, Dalga 3.
- `ListingCard.tsx`: `shortAddress(owner?.wallet_address ?? '', 6, 4)` (bugün `listing.owner_id` UUID veriliyordu — hata);
  `listing.base_asset?.symbol`; `formatAmount(listing.amount, symbol)`; `stats.managed_capital` aynı yol; `Number(stats.rating_avg)` puan için kalır.
- `OfferSheet.tsx`: `base_asset.symbol`, tutar `parseAmountInput(amount, listing.base_asset?.decimals ?? 6)`.

**`app/_layout.tsx`**: değişmez (Stack dosyaları otomatik). `hydrate` içinde `flushTxOutbox` (store'da). Opsiyonel: `contract/[id]` ve
`wallet` için `Stack.Screen` girdileri (başlık gizli zaten).

**`app/index.tsx`**: mantık aynı; `registered` artık doğru geliyor. `status === 'signed_in' && !registered` → `register/role`.

**`components/wallet/WalletConnectSheet.tsx`**: subtitle "Approve the connection request in your wallet"; düğmeler `WC_WALLETS`
(MetaMask birincil); alt metin "…scan the code with a wallet on another device. Connecting is free — you only approve a session, no
funds move." aynı. `verified:false` cüzdanlar `variant="ghost"`.

**Yeni `components/wallet/ChainBanner.tsx`**: `useSession` `walletConnected && chainId !== CHAIN_ID` iken amber şerit: "Your wallet is on
another network." + `Switch to Monad Testnet` (`switchToAppChain`). `Screen` içine değil, giriş/cüzdan/sözleşme/ilan ekranlarına eklenir.

### 6.2 FE-41 — İlan oluştur (`listing/create.tsx`) ve İlanlarım (`(customer)/listings.tsx`, `(trader)/listings.tsx`, `listing/[id].tsx`)

**`listing/create.tsx`** (modal; rol `useSession.role`):
- Adım 1 Basics: `title` (3–120), `description` (≤4000), `markets` (1–3 chip), `risk_profile`.
- Adım 2 Terms — customer (capital): `amount` (`parseAmountInput(_, baseAsset.decimals)`), `base_asset_id` (config `assets` `is_base_allowed`
  olanlar; tek varlık varsa gizli), `duration_days` (1–1095), `max_loss_bps` (yüzde girişi ×100; boş = limit yok); trader (service):
  `commission_bps` (profilden ön dolu), `min_capital`, `expected_return_min/max_bps`.
- Adım 3 Review → `listingsApi.create(ListingCreateIn)` → `ListingOut`. Service ilan `active` doğar → `router.replace('/listing/[id]')`.
  Capital ilan **`draft`** doğar → Adım 4.
- Adım 4 Lock capital (yalnız capital): açıklama "Your {amount} {symbol} moves into the TraderKirala vault. Trades can only start from this
  locked amount." `SlideToConfirm` "Slide to lock {amount} {symbol}" → `useTxExecutor().run(() => listingsApi.reserveTx(listing.id))`
  (pre_steps: approve → reserve). `TxProgressSheet` açılır; `confirmed` → "Listing is live" → `/listing/[id]`. `Later` düğmesi ilanı
  draft bırakır (İlanlarım'da "Deposit pending · Lock capital"). Uçlar: `POST /listings`, `POST /listings/{id}/tx/reserve`, `/tx/submit`, `GET /tx/{id}`.
- Durumlar: form doğrulama hataları alan altında; `create` hatası (`validation_error` details.errors alan eşlemesi); tx hataları
  `TxProgressSheet` içinde; `INSUFFICIENT_FUNDS` → "You need MON for gas" + `Open faucet`; `erc20:ERC20InsufficientBalance` → "Get test
  USDC from the Wallet screen" + `Go to wallet`.

**İlanlarım (`(customer)/listings.tsx`, `(trader)/listings.tsx`)** — ortak `components/listings/MyListingsScreen.tsx`:
- `listingsApi.mineCounts()` → Segmented `Draft (n)` (yalnız customer) · `Active` · `Paused` · `Closed`; `listingsApi.mine({status, limit:20,
  offset})` `useInfiniteQuery`. Kart: title, `formatAmount(amount|min_capital, symbol)`, `reserved_amount`/`is_funded` pill ("Locked
  {reserved_amount} {symbol}" / "Deposit pending"), `offer_count`, `view_count`. Tıklama → `/listing/[id]`.
- Satır aksiyonları: `pause`/`resume` (`listingsApi.pause/resume`), `close` (`409 reservation_locked` → "Release the locked capital
  first"), capital draft → `Lock capital` (reserveTx), capital active/paused ve `reserved_amount > 0` → `Release capital` (`releaseTx(id)`
  → `releaseAll`; kısmi tutar sheet'i opsiyonel `releaseTx(id, {amount})`). Hepsi `useTxExecutor` + `TxProgressSheet`.
- Boş durum: "You have no {tab} listings." + `Create listing` (FAB → `/listing/create`). Hata: kart + retry.

**`listing/[id].tsx`** (detay; teklif kabulü demo akışının parçasıdır):
- `listingsApi.byId(id)` → `ListingDetailOut`. Başlık, stats, durum pill, `reservation_id`/`reserved_amount`.
- Sahip: `offers` listesi (`OfferOut`: `from_user.display_name`, `formatAmount(amount, base_asset.symbol)`, `duration_days`,
  `commission_bps`, `max_drawdown_bps`, `expires_at`) → `Accept` → `offersApi.accept(id)` → `OfferAcceptOut` → toast "Offer accepted"
  → `router.push('/contract/[agreement.id]?next={next_action}')` (`next_action`: `open | open_reserved | propose`). `Reject` →
  `offersApi.reject`. Ziyaretçi: `my_offer_id` varsa "Your offer is pending" + `Withdraw`; yoksa `Make offer` (`OfferSheet`, trader) ya da
  `Request offer` (`discoverApi.action('listing', id, 'offer_request')`, customer).
- Draft/closed ilan sahibi dışına 404 gelir (BE-24) → "This listing is not public."

### 6.3 FE-42 — Sözleşme (`contract/[id].tsx`)

Veri: `agreementsApi.byId(id)` (`refetchInterval`: `status ∈ {draft, proposed, funded, active}` ya da `pending_tx` varsa 10 s; aksi
`false`), `agreementsApi.trades(id, {limit: 20})`, `agreementsApi.valueHistory(id, {range})` (Segmented `24h/7d/30d/all`, `Sparkline`).
`?next=` parametresi geldiyse ilgili aksiyon vurgulanır.

Bölümler:
1. Başlık: `StatusChip(status)`, `#onchain_id`, taraflar (`customer/trader.display_name` + `shortAddress(wallet_address)` + `my_role`).
2. Terms: `formatAmount(principal, base_asset.symbol)`, `duration_days`, `formatBps(commission_bps)`, `formatBps(max_drawdown_bps)`
   (10000 → "no limit"), `drawdown_floor`, `risk_profile`, `vault_address` (explorer linki).
3. Value: `current_value`, `pnl` + `pnl_bps` (`pnlColor`), `drawdown_bps`, `high_water_value`, `seconds_remaining` sayaç; settled ise
   `final_value`, `profit`, `trader_fee`, `platform_fee`, `customer_payout`, `settled_by`, `settle_tx` linki.
4. Balances: `balances[]` (`asset.symbol`, `balance`); settled + `claimable_assets` → her satırda `Claim` (`buildTx(id, 'claim', {asset_id})`).
5. Trades: `TradeOut` satırları (`token_in.symbol → token_out.symbol`, `amount_in/out`, `value_after`, `note`, `explorer_url`).
6. Pending banner: `pending_tx` `status ∈ {pending, submitted}` → "Transaction pending · {action}" + explorer + `Check status`
   (`txApi.status`).
7. Aksiyon alanı (`available_actions` **tek kaynak**; rol/durum hesaplanmaz):

| action | Kim | UI | build |
|---|---|---|---|
| `open` / `open_reserved` | customer, draft | `SlideToConfirm` "Slide to lock {principal} {symbol}" (open: approve + open; reserved: "from your listing deposit") | `buildTx(id, action)` |
| `propose` | trader, draft | `SlideToConfirm` "Slide to propose" | `buildTx(id, 'propose')` |
| `fund` / `fund_reserved` | customer, proposed | `SlideToConfirm` "Slide to fund" | `buildTx(id, action)` |
| `accept` | trader, funded | `SlideToConfirm` "Slide to accept and start" | `buildTx(id, 'accept')` |
| `cancel` | proposer / iki taraf | `Button variant="danger"` + onay sheet ("Funded: principal returns to the customer's wallet") | `buildTx(id, 'cancel')` |
| `settle` | taraflar; admin/keeper süre sonrası | `Button` → `SettleSheet`: `slippage_bps` (varsayılan `config.settle_slippage_bps`, 0–5000), açıklama "Positions are swapped back to {symbol}; fees apply only on profit." | `buildTx(id, 'settle', {slippage_bps})` |
| `claim` | customer, settled | satır düğmesi | `buildTx(id, 'claim', {asset_id})` |
| `trade` | trader, active | `Button` → `TradeSheet` (aşağıda) | `buildTradeTx(id, TradeTxIn)` |

`use_reserved_action` (409) → `run` `details.action` ile yeniler (§2.7); `reservation_insufficient` → "Your listing deposit doesn't
cover this principal. Release it and open from your wallet." `invalid_state` → refetch.

**`TradeSheet`** (`components/contract/TradeSheet.tsx`): `token_in` (mevcut `balances`'tan), `token_out` (config `assets`, `onchain_allowed`,
≠ token_in), `amount_in` (`parseAmountInput(_, token_in.decimals)`, `Max`), `slippage_bps` (varsayılan `config.default_trade_slippage_bps`),
`note` (≤2000), `notify_investors` Switch, `deadline_seconds` 300. Quote: 500 ms debounce → `agreementsApi.quote(id, {...})` →
`QuoteOut.amount_out`, `min_out`; `source` "router"/"fake" etiketi. Gönder → `buildTradeTx` → `TxProgressSheet`. `DrawdownBreached`
ön uyarısı backend `chain_error details.error_code` ile gelir (gas tahmini revert) → sözlük metni.

Durumlar: yükleniyor iskeleti; 404 `agreement_not_found`; 403 taraf değil → "You are not a party to this agreement."; ağ hatası retry.

### 6.4 FE-43 — Cüzdan (`wallet.tsx`)

Uçlar: `walletApi.get()` (`refetchInterval` 15 s), `walletApi.depositInfo()`, `walletApi.faucet({asset_id})`, `walletApi.transactions({limit:
20})` (404 → bölüm gizli), `metaApi.config` (faucet_url, default_base_asset_id), opsiyonel `walletApi.transfer`.

Bölümler:
1. Adres kartı: `checksum(address)` (tam), `Copy` (expo-clipboard), `Show QR` (`react-native-qrcode-svg` ile `deposit_info.pay_uri`
   `ethereum:0x…@10143`), explorer linki (`explorer_url`), bağlantı pill'i "MetaMask · Monad Testnet" / "In-app wallet" / "WalletConnect";
   `ChainBanner`.
2. MON: `formatMon(native.balance_raw)` + `Get MON from faucet` (`Linking.openURL(config.chain.faucet_url)`); 0 ise amber ipucu "You need
   MON to pay gas."
3. Tokens: `tokens[]` → `formatAmount(balance, symbol)`, `is_base_allowed` pill; `Get test USDC` (`faucet({asset_id: default_base_asset_id})`,
  `token_faucet.enabled`, `next_allowed_at` varsa geri sayım ve disabled; 429 → `details.next_allowed_at`; `FaucetOut` → toast "1000 tUSDC
  minted" + explorer). USD/TL toplamı `usd_prices` ile (fiyatı olmayan token atlanır).
4. Send (opsiyonel, sprint sonu): sheet → `asset_id | null`, `to` (`isEvmAddress`), `amount` → `walletApi.transfer` → `useTxExecutor`.
5. Activity: `WalletTransferOut` satırları (`direction`, `symbol`, `amount`, `counterparty_label`, `explorer_url`).
6. Security (yalnız native + `walletKind === 'local'`): `Back up private key` (onay → 30 s görünür, `Copy`, uyarı metni), `Import a key`
   (yalnız cüzdan boşken/oturum kapalıyken; sprint sonu), `Forget this wallet` (§6.5).
7. `Sign out` (`signOut()`; her platform).

Durumlar: yükleniyor; `chain_error` (502) → "Balances are temporarily unavailable (RPC)." + retry; boş token listesi olmaz (allow-list
sıfır bakiyeyle gelir).

### 6.5 Sign-out ve "cüzdanı unut"

- **Sign out** (Wallet ekranı ve Profile Placeholder üstündeki gerçek `ListRow`): `useSession.signOut()`; WC oturumu ve wagmi bağlantısı
  kapanır, JWT silinir, `/(auth)/login`. Yerel cüzdan anahtarı **korunur** (aynı adresle geri dönülebilir).
- **Forget in-app wallet** (native, `local`): iki adımlı onay — (1) "Your MON and test tokens stay on this address. Without the private key you
  cannot access them again. Back up first?" `[Back up]` `[Continue]`; (2) `Field` içine `FORGET` yazılır → `forgetLocalWallet()` → login.
- Profile ekranları Placeholder kalır; yalnız üstüne `Wallet` (`/wallet`) ve `Sign out` satırları eklenir (e).

### 6.6 FE-44 — Paneller (`(customer)/dashboard.tsx`, `(trader)/dashboard.tsx`)

`dashboardApi.get()` → `role` ile ayrıştırılır (`CustomerDashboardOut | TraderDashboardOut`); `refetchInterval` 30 s; `base_asset_code`
gösterim sembolüdür.

Customer: `KpiBox` `Portfolio value` (`portfolio_value`; `wallet_error` varsa "wallet balance unavailable" alt notu), `Invested`
(`invested_principal`), `Open P&L` (`open_pnl`, `signed: open_pnl_bps`), `This month` (`month_pnl`, `month_change_bps`); `positions[]`
(`PositionBriefOut` → `ListRow` counterparty + `StatusChip` + `pnl_bps` → `/contract/[agreement_id]`); `followed[]` (`FollowedTraderOut`
→ `/trader/[id]` Placeholder); `listing_interactions` (views/likes/offers/pending_offers). Boş: "No agreements yet — Discover traders" →
`(customer)/discover`.

Trader: `Managed capital`, `Open P&L`, `Active investors`, `Commission this month` (`month_commission`), `Total commission`; `pending_offers[]`
(`PendingOfferBriefOut` → `Accept`/`Reject` → `offersApi.accept` → `next_action` → `/contract/[agreement_id]`), `positions[]`,
`profile_checklist` (`Progress` `completion_pct` + eksik maddeler linkleri: strateji → profil, servis ilanı → `/listing/create`),
`listing_interactions`. Boş: "No investors yet — publish a service listing."

Ortak: yükleniyor iskeleti, hata kartı + retry, `generated_at` "Updated 2m ago".

### 6.7 Paylaşılan yeni bileşenler

| Bileşen | Dosya | Props / davranış |
|---|---|---|
| `TxProgressSheet` | `components/tx/TxProgressSheet.tsx` | `{ progress: TxProgress; onClose(); onRetry?(); onRecheck?() }`. `BottomSheet`; adım listesi (approve ×n, main); faz metinleri §8.2; `unsigned.description` başlık altı; `decodeFunctionData` ile `action` doğrulaması (eşleşmezse amber "Calldata does not match the requested action" ve devam yok); explorer linki (`status.explorer_url ?? explorerTxUrl(txHash)`); `failed` → `vaultErrorCopy`/`userMessage`; `INSUFFICIENT_FUNDS` → `Open faucet`; `CHAIN_NOT_ADDED` → `Switch network` + manuel parametreler; `isBusy` iken kapatma kilitli (geri tuşu dahil) |
| `SlideToConfirm` | `components/ui/SlideToConfirm.tsx` | `{ label: string; onConfirm(): void; disabled?; loading? }`. `react-native-gesture-handler` Pan + reanimated (SwipeDeck ile aynı); eşik %85; erişilebilirlik için `onLongPress` (600 ms) de onaylar; web'de fare ile çalışır |
| `ConnectWalletPanel` | `components/wallet/ConnectWalletPanel.tsx` | `{ onConnected(address: Address): void; compact?: boolean }` (§6.1) |
| `ChainBanner` | `components/wallet/ChainBanner.tsx` | props yok; `useSession` okur (§6.1) |
| `AmountField` | `components/ui/AmountField.tsx` | `Field` + `decimals`/`symbol`; `parseAmountInput` doğrulaması, `Max` düğmesi (opsiyonel) |
| `ExplorerLink` | `components/ui/ExplorerLink.tsx` | `{ hash?: string; address?: string; url?: string; label? }` → `Linking.openURL` |

### 6.8 Placeholder kalanlar (Sprint 3)

`(customer)/activity.tsx` (FE-12), `(trader)/trades.tsx` (FE-14), `messages/index.tsx`, `messages/[id].tsx` (FE-08), `notifications.tsx`
(FE-19), `trader/[id].tsx` (FE-17), `(customer)/profile.tsx`, `(trader)/profile.tsx` (yalnız Wallet/Sign out satırları eklenir).
`Placeholder` notlarındaki "Horizon", "TRY anchor", "SEP-24", "anchorApi", "on-chain listing record", "bindings" ifadeleri Dalga 3'te
Monad karşılıklarıyla değiştirilir (`wallet.tsx` artık gerçek ekran; notu silinir).

---

## 7. Ortam değişkenleri (`src/lib/env.ts`, `.env.example`)

`env.ts`:
```ts
export const env = {
  apiBaseUrl: optional(process.env.EXPO_PUBLIC_API_BASE_URL, 'http://localhost:8013'),
  chainId: Number(optional(process.env.EXPO_PUBLIC_CHAIN_ID, '10143')),          // 10143 dışı değer → açılışta hata (config.ts)
  rpcUrl: optional(process.env.EXPO_PUBLIC_RPC_URL, 'https://testnet-rpc.monad.xyz'),
  explorerUrl: optional(process.env.EXPO_PUBLIC_EXPLORER_URL, 'https://testnet.monadvision.com'),
  walletConnectProjectId: optional(process.env.EXPO_PUBLIC_WALLETCONNECT_PROJECT_ID),
} as const;
```
Silinir: `stellarNetwork`, `horizonUrl`, `contracts.listing/escrow`, `anchorHomeDomain`, `relayerAddress`, `requireEnv` (ölü kod).
`process.env` erişimleri statik kalır (Expo satır içi yazar). Kontrat adresleri env'de **yoktur** (`/config.contracts`).

`.env.example` tam metni (ZORUNLU):
```dotenv
# TraderKirala frontend ortam değişkenleri
# Kopyala: cp .env.example .env
# Expo yalnızca EXPO_PUBLIC_ ile başlayan değişkenleri istemciye açar. Değerler derleme zamanında
# satır içine yazılır; değiştirdikten sonra `npx expo start --clear`.

# --- Backend API (Monad sürümü; K13: ayrı alt alan adı) ---
# Origin verilir; uç noktalar /api/v1 önekini kendisi ekler (GET /api/v1/config).
# Yerel geliştirme: http://localhost:8013   (traderkirala-monad compose projesi)
EXPO_PUBLIC_API_BASE_URL=https://monadback.yolalapp.com

# --- Monad Testnet ---
# Zincir kimliği sabittir; farklı bir değer uygulamayı açılışta durdurur.
EXPO_PUBLIC_CHAIN_ID=10143
# RPC ve explorer yalnızca /config alınamadığında kullanılan yedeklerdir; sunucu değeri her zaman öncelikli.
# Alternatif RPC'ler: https://rpc.ankr.com/monad_testnet · https://rpc-testnet.monadinfra.com
EXPO_PUBLIC_RPC_URL=https://testnet-rpc.monad.xyz
EXPO_PUBLIC_EXPLORER_URL=https://testnet.monadvision.com

# --- WalletConnect v2 (Reown) ---
# https://dashboard.reown.com → Create project → Project ID (32 karakter hex). Gizli değildir ama .env git'e girmez.
# Web'de WalletConnect bağlayıcısı, mobilde harici cüzdan (MetaMask, Rainbow, Trust) yolu için gerekir.
# Boş bırakılırsa web'de yalnızca tarayıcı cüzdanı (MetaMask/Rabby), mobilde yalnızca uygulama içi cüzdan çalışır.
# Expo Go ile test ederken panodaki Allowlist BOŞ olmalı (Expo Go kendi bundle id'siyle çalışır).
EXPO_PUBLIC_WALLETCONNECT_PROJECT_ID=
```
Silinen değişkenler: `EXPO_PUBLIC_STELLAR_NETWORK`, `EXPO_PUBLIC_STELLAR_RPC_URL`, `EXPO_PUBLIC_STELLAR_HORIZON_URL`,
`EXPO_PUBLIC_CONTRACT_LISTING_ID`, `EXPO_PUBLIC_CONTRACT_ESCROW_ID`, `EXPO_PUBLIC_ANCHOR_HOME_DOMAIN`, `EXPO_PUBLIC_RELAYER_ADDRESS`.
`.gitignore`'a `openapi.json` eklenir. `app.json` değişmez (`scheme: traderkirala`, plugin listesi aynı; `expo-secure-store` zaten var).

---

## 8. Metin ve UX (FE-40) — arayüz dili İngilizce

### 8.1 Giriş ekranı kopyası
- Başlık `Welcome`; alt metin `Connect an EVM wallet on Monad Testnet to continue`.
- Pill `Network: Monad Testnet`; sunucu satırı `Checking the server…` / `Server unreachable — signing in may not work right now.` /
  `Server is on chain {id}; this app is built for Monad Testnet (10143).` / `Server connected · {siwe_domain}`.
- Web düğmeleri: `Connect wallet` (yardım: `MetaMask, Rabby or any browser wallet`), `WalletConnect`, `Install MetaMask` (cüzdan yoksa).
  Mobil: `Connect wallet` (yardım: `Opens MetaMask, Rainbow or Trust over WalletConnect`), `Use in-app wallet` / `Continue with in-app
  wallet · 0x67aD…FF19` (yardım: `Testnet only — key stays on this device`).
- Alt not: `Signing in asks your wallet to sign a message (Sign-In with Ethereum). It is free and moves no funds.`
- Kaldırılan ifadeler: Freighter, xBull, Albedo, Lobstr, "Stellar wallet", "SEP-10 challenge", "Testnet (Public)".

### 8.2 İşlem fazı metinleri (`TxProgressSheet`)
`building` "Preparing transaction…" · `checking_wallet` "Checking wallet…" · `switching_chain` "Switch to Monad Testnet in your wallet" ·
`approving` "Approve {symbol} spending in your wallet ({i}/{n})" / "Approval sent — waiting for confirmation" · `awaiting_signature`
"Confirm the transaction in your wallet" · `submitting` "Sent — notifying the server" · `submitted` "Sent — waiting for confirmation" ·
`confirmed` "Confirmed" · `failed` "Failed: {message}" · `rejected` "You rejected the request in your wallet." · `expired` "This request
expired. Build it again." · `timeout` "Still pending. Check the explorer or try again in a moment."

### 8.3 `lib/errors.ts` — `userMessage` kaynağı (ZORUNLU)

| Kaynak | Kod | Metin |
|---|---|---|
| `ChainError` | `USER_REJECTED` | The request was rejected in your wallet. |
| | `WRONG_NETWORK` | Your wallet is on another network. Switch it to Monad Testnet (chain 10143). |
| | `CHAIN_NOT_ADDED` | Monad Testnet is not in your wallet yet. Approve adding it, or add it manually: RPC {rpcUrl}, chain ID 10143, symbol MON. |
| | `WRONG_ACCOUNT` | Your wallet is on a different account. Switch to {shortAddress}. |
| | `INSUFFICIENT_FUNDS` | Not enough MON to pay for gas. Get test MON from the Monad faucet. |
| | `CONTRACT_REVERT` / `TX_REVERTED` | `vaultErrorCopy(details.errorName)` ya da "The transaction was rejected by the contract." |
| | `TX_TIMEOUT` | The transaction is taking longer than usual. Check the explorer; it may still confirm. |
| | `RPC_ERROR` | The Monad RPC did not respond. Try again in a moment. |
| | `REQUEST_PENDING` | Your wallet already has a request open. Finish it there first. |
| | `NOT_CONNECTED` | No wallet connected. Connect a wallet first. |
| | `NOT_AVAILABLE` / `MISSING_CONFIG` / `EXPIRED` | `err.message` |
| | `SERVER_CHAIN_MISMATCH` | The server is on chain {id}; this app is built for Monad Testnet (10143). |
| `SiweError` | `WRONG_NETWORK` | The sign-in message is for another chain. This app runs on Monad Testnet. |
| | `DOMAIN_MISMATCH` | The sign-in message was issued by another domain. Check EXPO_PUBLIC_API_BASE_URL. |
| | `ADDRESS_MISMATCH` | The sign-in message is for a different wallet address. Check the connected account. |
| | `EXPIRED` | The sign-in request expired. Please try again. |
| | `BACKEND_MISSING` / `INVALID_MESSAGE` / `NO_TOKEN` | `err.message` |
| `ApiError.code` | `session_expired` | Your session has ended. Sign in again with your wallet. (**refresh tekrar denenmez**) |
| | `token_expired` / `token_invalid` / `missing_token` / 401 | Your session could not be verified. Sign in again with your wallet. |
| | `siwe_invalid` / `siwe_domain_mismatch` / `siwe_uri_mismatch` / `siwe_chain_mismatch` / `siwe_expired` / `siwe_not_yet_valid` | Sign-in failed: the message did not pass the server's checks ({code}). Try again. |
| | `nonce_invalid` / `nonce_used` / `nonce_expired` | The sign-in request is no longer valid. Start again. |
| | `signature_invalid` | The signature does not match the wallet address. |
| | `invalid_address` | That is not a valid address (0x + 40 hex). |
| | `not_registered` | Finish sign-up first. |
| | `rate_limited` | Too many requests. Try again in {retry_after_seconds}s. / Faucet: "Next test tokens available at {next_allowed_at}." |
| | `chain_error` (502) | The Monad RPC is unavailable right now. Try again shortly. (`details.error_code` varsa vault sözlüğü) |
| | `use_reserved_action` | (kullanıcıya gösterilmez; yürütücü yeniler) |
| | `reservation_insufficient` | Your listing deposit does not cover this principal. Release it and open from your wallet. |
| | `receipt_mismatch` / `tx_hash_conflict` / `invalid_tx_hash` | Vault sözlüğü (§2.5) |
| | `pending_tx_expired` | This transaction request expired before it was sent. Build it again. |
| | `pending_tx_not_found` / `not_owner` | This transaction belongs to another session. |
| | `too_many_decimals` | Use at most {decimals} decimals for this asset. |
| | `amount_locked` | The amount cannot change while capital is locked. Release it first. |
| | `reservation_locked` | Release the locked capital before closing the listing. |
| | `insufficient_funds` (400) | Not enough balance: need {details.needed}, have {details.available}. |
| | `asset_not_mintable` / `faucet_disabled` | Test tokens are not available for this asset right now. |
| | `invalid_state` / `wrong_party` / `not_expired` / `not_onchain` | This action is not available in the agreement's current state. Refresh. |
| | `validation_error` (422) | `err.message`; alan hataları `details.errors` ile form altına |
| | status 0 | Could not reach the server. Is the backend running? (EXPO_PUBLIC_API_BASE_URL) |
| | 403 / 404 / 409 / 429 / 5xx | mevcut genel metinler |

`networkLabel()` → sabit `'Monad Testnet'`. `Sep10Error`, `stellarConfig` import'ları silinir.

### 8.4 Diğer metinler
- `Placeholder` notları ve yorumlardaki Stellar ifadeleri (Dalga 3): `login.tsx`, `details.tsx`, `polyfills.ts`, `log.ts` ("gizli anahtar
  (S…)" → "(0x…)", "imzalı XDR" → "imza/calldata"), `storage.ts` (`walletMode` yorumu `local | walletconnect`), `client.ts`, `session.ts`,
  `format.ts` başlığı ("TRY anchor" → kaldır), `WalletConnectSheet`, `wallet.tsx`.
- Onboarding: "your capital stays in your own wallet" → "your capital sits in an on-chain vault, never in the trader's wallet" (doğruluk).
- `RiskStrip` metni değişmez. `STATUS_LABEL` değişmez.

---

## 9. Uygulama planı — paralel ajanlar için dosya sahipliği

Kural: bir dosyayı **tek** ajan yazar. Sınırlardaki arayüzler bu dokümanda sabittir (§2.6–2.7 `TxProgress`/`useTxExecutor`, §3.1
`WalletAdapter`, §4.2 `SessionState`, §5.2 imzalar, §6.7 bileşen props'ları); ajanlar birbirinin dosyasını beklemeden bu imzalara yazar.
Dalga içi ajanlar tamamlanmadan sonraki dalga başlamaz. Her ajan bitiminde kendi dosyalarında `npx tsc --noEmit -p . 2>&1 | grep
'<kendi dosyaları>'` boş olmalı; proje geneli typecheck Dalga 3'te.

### Dalga 1 (paralel)

**(a) Zincir + cüzdan + bağımlılıklar + env**
- `app/package.json`, `app/package-lock.json` (npm uninstall/install, §1.1), `app/.env.example`, `app/.gitignore` (`openapi.json`),
  `app/eslint.config.js` (ignores), `app/.prettierignore` (yeni), `app/index.js` (yorum), `app/src/polyfills.ts` (yorum).
- `app/src/lib/env.ts`, `app/src/lib/chain/**` (config, client, abi/traderVault.ts **geçici**, abi/index.ts, format, errors, tx, useTxExecutor, index).
- `app/src/lib/wallet/**` (types, wallet.ts, wallet.web.ts, walletconnect.ts, local.ts, local.web.ts, deeplinks.ts, index.ts); **siler**:
  `wallet/sep7.ts`, `lib/stellar/*`.
- `app/src/lib/format.ts` (`formatAmount`/`formatTRY` kaldırma; bps/gün yardımcıları kalır), `app/src/lib/storage.ts` (yorum + `tk.txOutbox` anahtarı).
- Doğrulama: `npx expo export --platform web` ve `--platform ios --dev` derlenir (ekranlar henüz kırık olabilir; yalnız kütüphane çözümlemesi).

**(b) API tipleri + endpoints + session**
- `app/src/lib/api/schema.d.ts` (üretim; backend hazır değilse 02'ye göre **elle iskelet** aynı ad/şekilde, `// TEMPORARY until gen:api`),
  `app/src/lib/api/types.ts`, `app/src/lib/api/endpoints.ts`, `app/src/lib/api/client.ts` (`skipAuthBridge`, `details`, `retryAfterSeconds`),
  `app/src/lib/api/paging.ts`, `app/src/lib/api/index.ts`.
- `app/src/store/session.ts` (§4.2 — `loginWithSiwe`'yi `@/lib/auth` üzerinden import eder; `wallet`'ı `@/lib/wallet`'tan **yalnız
  §3.1 arayüzüyle** kullanır), `app/src/lib/auth/siwe.ts`, `app/src/lib/auth/index.ts`, `app/src/lib/auth/jwt.ts`; **siler** `auth/sep10.ts`.
- `app/src/lib/errors.ts` (§8.3; `ChainError`/`vaultErrorCopy` `@/lib/chain/errors`'tan — (a) yazıyor, imza sabit).
- (b), (a)'dan bağımsızdır: `@/lib/chain` ve `@/lib/wallet` yalnız tip/imza düzeyinde referans alınır.

### Dalga 2 (paralel)

**(c) Kimlik ekranları**
- `app/app/(auth)/login.tsx`, `app/app/(auth)/register/details.tsx`, `app/app/(auth)/onboarding.tsx` (metin), `app/app/index.tsx` (gerekirse),
  `app/src/components/wallet/ConnectWalletPanel.tsx` (yeni), `app/src/components/wallet/ChainBanner.tsx` (yeni),
  `app/src/components/wallet/WalletConnectSheet.tsx`, `app/src/components/wallet/index.ts`.
- Kabul: web'de MetaMask ile SIWE girişi, mobilde (Expo Go) uygulama içi cüzdanla SIWE girişi ve kayıt; kayıt döngüsü yok.

**(d) İşlem UI + sözleşme + ilan oluştur/detay**
- `app/src/components/tx/TxProgressSheet.tsx`, `app/src/components/tx/index.ts`, `app/src/components/ui/SlideToConfirm.tsx`,
  `app/src/components/ui/AmountField.tsx`, `app/src/components/ui/ExplorerLink.tsx`, `app/src/components/ui/index.ts` (export ekleme),
  `app/src/components/contract/TradeSheet.tsx`, `app/src/components/contract/SettleSheet.tsx`, `app/src/components/contract/index.ts`,
  `app/app/contract/[id].tsx`, `app/app/listing/create.tsx`, `app/app/listing/[id].tsx`.
- Kabul (FakeMonadGateway ya da anvil ile): teklif kabul → `open` (approve + open) → `accept` → `trade` → `settle` → `claim` akışı
  `TxProgressSheet` üzerinden; 4001 reddi ve yanlış zincir yolları.

**(e) Cüzdan + paneller + İlanlarım**
- `app/app/wallet.tsx`, `app/app/(customer)/dashboard.tsx`, `app/app/(trader)/dashboard.tsx`, `app/app/(customer)/listings.tsx`,
  `app/app/(trader)/listings.tsx`, `app/src/components/listings/MyListingsScreen.tsx` (yeni), `app/src/components/listings/index.ts`,
  `app/src/components/dashboard/*` (yeni: `PositionRow.tsx`, `PendingOfferRow.tsx`), `app/app/(customer)/profile.tsx`, `app/app/(trader)/profile.tsx`
  (Wallet/Sign out satırları), `app/src/lib/api/hooks.ts` (opsiyonel `useUnreadCounts`).
- (e) `useTxExecutor` ve `TxProgressSheet`'i §2.7/§6.7 imzalarıyla kullanır ((d) yazıyor).

### Dalga 3 (tek ajan ya da sıralı)

- `npm run gen:api` (gerçek şema) → `schema.d.ts` güncellenir; `contracts/scripts/export-abi.sh` çıktısıyla `abi/traderVault.ts` değişir.
- `npm run check` (typecheck + lint) ve `npm run export:web` temiz; `npx expo export --platform ios --dev` derlenir.
- Metin temizliği (§8.4), `Placeholder` notları, `src/components/discover/{ListingCard,OfferSheet,SwipeDeck}.tsx` (symbol/decimals,
  `restore()` — hata 6), `src/types/domain.ts` yorumları.
- Grep kapıları: `grep -rniE "\b(stellar|soroban|xdr|sep-?10|sep-?7|freighter|xbull|albedo|lobstr|friendbot|passphrase|stroop)\b" app src`
  boş; `grep -rn "Number(" src app` yalnız bps/puan/gün dönüşümlerinde.
- Docs: `docs/api-entegrasyon.md` → `docs/monad/02-api-sozlesme.md`'ye işaret eden kısa Monad sürümü; `docs/mobil-test.md` Monad
  (Expo Go + uygulama içi cüzdan + MetaMask mobil WalletConnect adımları, faucet); `docs/design-system.md` "Stellar'a uyarlanan" bölümü →
  Monad; `docs/gelistirme-notlari.md` ve `SPRINT-1.md` başına arşiv notu (OPS-15); `app/README` varsa kurulum adımları.
- Demo provası (DoD-6, **testnet MON geldikten sonra**): web MetaMask + mobil Expo Go uygulama içi cüzdan; ekran kaydı.

### Test notu

Zincire dokunan uçtan uca doğrulama (SIWE gerçek cüzdanla, approve+open, settle) **sprintin sonuna** bırakılır; testnet cüzdanlarında MON
yok (06). Dalga 1–2 boyunca frontend backend'in `FakeMonadGateway` yoluna ya da yerel anvil'e (`anvil --chain-id 10143`, `EXPO_PUBLIC_RPC_URL=
http://<mac-ip>:8545`) bağlanır; cüzdan olarak uygulama içi cüzdan yeterlidir. Bu doküman testten bağımsız olarak frontend uygulamasının
tek referansıdır.
