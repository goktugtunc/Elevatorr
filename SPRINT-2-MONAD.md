# Sprint 2: Stellar'dan Monad'a geçiş

**Süre:** 2 hafta (10 iş günü), başında yarım günlük hazırlık (Gün 0) var.
**Kapsam:** kontrat, backend (`isinigetir.com:/home/mkati/mobilapp`) ve frontend (`app/`).
**Hedef ağ:** Monad Testnet, chainId `10143`, RPC `https://testnet-rpc.monad.xyz`, yerel token MON (18 ondalık).

**Sprint hedefi:** Uçtan uca akışın Monad Testnet üzerinde çalışması. Akış şu: SIWE ile giriş, rol seçimi, ilan, teklif, sözleşme (vault'a USDC kilitleme), trade, settle ve ödeme. Bu akış web'de herkese açık bir URL'den, mobilde Expo Go'dan açılmalı. Sprint sonunda kodda Stellar bağımlılığı kalmamalı.

---

## 1. İnceleme özeti (başlangıç durumu)

### Kontrat: `contracts/vault` (Rust/Soroban)
- Vault kontratı yaklaşık 1.650 satır, testler 2.039 satır (47 test). Ayrıca Soroswap uyumlu bir `mock_router` var.
- İçerdiği işler: rezervasyon (reserve/release), sözleşme (propose/open/fund/accept/cancel), trade (router üzerinden swap, drawdown tabanı), üç kademeli settle (taraflar / süre dolunca admin / 7 gün sonra keeper), in-kind dust teslimi ve claim, pause, upgrade.
- Testnet'te deploy edilmiş durumda: `CCGAGVFF…NAG2`.
- **Monad'a geçerken açılan riskler:**
  - Soroban reentrancy'yi kendiliğinden engelliyor, EVM engellemiyor.
  - Değerleme spot fiyata (quote) dayandığı için drawdown tek bir işlemle atlatılabiliyor. Bu bilinen bir açık.
  - Admin router'ı değiştirip süresi dolmuş sözleşmeleri boşaltabilir.
  - Platform fee, açık sözleşmelerde sonradan değiştirilebiliyor.
  - `release(0)` rezervasyonun tamamını iade ediyor.

### Backend: FastAPI, Postgres 16, worker (docker compose, `127.0.0.1:8012`, nginx → `mobilback.yolalapp.com`)
- 106 uç var. İş katmanının yaklaşık %70'i zincirden bağımsız: ilanlar, teklifler, keşfet, mesajlar, bildirimler, puanlar, panel.
- Zincir katmanı `app/services/stellar/*` altında tek bir soyutlamaya toplanmış:
  - `SorobanGateway` (885 satır), `contract_abi.py` (996), `horizon.py` (455), `sep10.py`, `fake.py` (1.207)
  - Bunları kullananlar: `indexer.py` (1.159), `tx_submit.py`, `anchor.py` (1.809, SEP-24/6/12).
- Stellar'a bağlı başka yerler:
  - Tutarlar 7 ondalığa sabit: 35 adet `Numeric(30,7)` kolonu ve `AmountIn`.
  - Tx hash kolonu `String(64)`. EVM hash'i 66 karakter olduğu için bu kolona sığmıyor.
  - Adres alanı `stellar_address String(56)`, blok bilgisi için `ledger` kullanılıyor.
  - Varlık modeli `CODE:ISSUER` biçiminde. XLM/USDC sabitleri ve yasal metinler de Stellar'a göre yazılmış.
- **Kritik tasarım farkı:** Bugünkü akışta backend imzasız XDR üretiyor, cüzdan imzalıyor, backend yayınlıyor. EVM cüzdanlarında `eth_signTransaction` yaygın olarak desteklenmediği için bu akış korunamaz. Yeni model: backend calldata döner, cüzdan `eth_sendTransaction` ile kendisi yayınlar, backend receipt'i doğrular.
- **Kalite:**
  - Sunucudaki repoda **hiç commit yok**.
  - 9 adet `.bak` dosyası ve 2 adet `src.bak.*` dizini var. Bunlar ve testler prod imajına giriyor.
  - `docs/ARCHITECTURE.md` artık geçerli olmayan havuz (pool) tasarımını anlatıyor.
  - Rezervasyon akışının fake'i yok, yani testlerle kapsanmıyor.
  - Bazı listing testleri büyük olasılıkla kırık (capital ilanının `draft` doğması değişikliğinden sonra).

### Frontend: `app/` (Expo 57)
- 25 route var. **Yalnızca 6'sı gerçek API'ye bağlı** (giriş, kayıt, iki keşfet ekranı, onboarding, rol). 15'i hâlâ Placeholder.
- Stellar'a bağlı kısımlar:
  - `lib/wallet/*` (Wallets Kit, Freighter, yerel Keypair, SEP-7, WC `stellar:` namespace)
  - `lib/auth/sep10.ts`
  - `lib/stellar/*` (ikisi de kullanılmıyor)
  - `env.ts`, giriş ekranındaki metinler
- Mevcut hatalar:
  - **Kayıt döngüsü:** `RegisterOut` yanıtı `MeOut` gibi okunuyor, rol `undefined` geliyor, kullanıcı rol ekranında takılı kalıyor.
  - **Refresh kilitlenmesi:** `/auth/refresh` 401 dönerse `refreshInFlight` kendini bekliyor ve uygulama asılı kalıyor.
  - **Fark edilmeden yerel cüzdan oluşuyor:** mobilde `details.tsx` → `connectWallet()` varsayılan olarak `local` açıyor.
  - Tx submit gövdesi şemayla uyuşmuyor (`xdr` alanı gönderiliyor, şema `signed_xdr` bekliyor).
  - Sayfalama uyuşmuyor (cursor ile offset). Okunmamış sayaçlarının şekli farklı.

---

## 2. Mimari kararlar (sprint başında sabitlenir)

| # | Konu | Karar | Gerekçe |
|---|---|---|---|
| K1 | Kontrat dili ve araç | Solidity 0.8.x, Foundry, OpenZeppelin (UUPS, ReentrancyGuard, SafeERC20, Pausable) | Monad EVM bytecode uyumlu. Foundry'nin fuzz/invariant testleri Rust testlerinin karşılığı |
| K2 | Giriş | **SIWE (EIP-4361)**. Mevcut `/auth/nonce` + `/auth/verify` uçları EIP-191 `recover_message` ile doğrulayacak şekilde yeniden yazılır. `/auth/sep10*` silinir | Uçlar ve JWT akışı zaten var; domain ve chainId bağlaması phishing'i de kapatır |
| K3 | İşlem modeli | `POST …/tx/{action}` → `{to, data, value, chain_id, gas}`. Cüzdan `eth_sendTransaction` ile gönderir. `POST /tx/submit {pending_tx_id, tx_hash}`. Backend receipt'te `from`, `to` ve `input` eşleşmesini kontrol eder | Cüzdanlar imzalayıp geri vermiyor. Relayer ya da ücret sponsorluğu bu sprintte yok |
| K4 | Token onayı | `open`, `fund` ve `reserve` öncesinde ERC-20 `approve` adımı eklenir. Tutar sonsuz değil, tam principal kadar. Permit (EIP-2612) sonraki sprinte bırakılır | Test USDC'nin permit desteği garanti değil |
| K5 | DEX / router | Kendi `MockRouter.sol` (UniswapV2 `getAmountsOut` / `swapExactTokensForTokens` imzası) ve kendi test tokenlarımız (tUSDC 6 ondalık, tWETH 18, tWBTC 8), mint yetkili. Monad testnet'teki Uniswap v2/v3 **Gün 0'da incelenir**; yeterli likidite ve adres varsa K5b olarak eklenir | Demo belirleyici olmalı. Mevcut kontrat tasarımı zaten mock router'a göre test ediliyor |
| K6 | Taban varlık | tUSDC (6 ondalık). MON yalnızca gas için kullanılır; vault'a WMON girmez | Native token ile ERC-20 ayrımı sadeleşir |
| K7 | Anchor (SEP-24, TRY) | **Kaldırılır.** Router, worker job, 2 tablo ve `test_anchor.py` silinir. Cüzdan ekranında "Test USDC al" butonu olur (backend faucet mint ya da kontratta `mint` herkese açık). Fiat on-ramp sonraki sprintin konusu | Monad'da SEP anchor yok |
| K8 | Cüzdan, web | wagmi v2 + viem, injected (MetaMask, Rabby, Phantom EVM) ve WalletConnect bağlayıcısı. `wallet.web.ts` içinde | Stellar Wallets Kit'in yerine geçer |
| K9 | Cüzdan, mobil | Mevcut `@walletconnect/universal-provider` kalır, namespace `eip155:10143` olur (Expo Go uyumu korunur). Uygulama içi cüzdan viem `privateKeyToAccount` ve `expo-secure-store` ile yapılır; **web'de kapalıdır**. SEP-7 silinir | Reown AppKit RN development build gerektirir. Expo Go yolunu kırmamak için ertelenir |
| K10 | Veri geçişi | Stellar ayna verisi taşınmaz. Tek bir **irreversible** Alembic migration yazılır, testnet DB sıfırlanır (öncesinde yedek alınır). Kullanıcılar yeniden kayıt olur | Cüzdan adresleri değişiyor; hackathon verisi |
| K11 | Adres normalizasyonu | DB'de küçük harf, API çıktısında EIP-55 checksum. Karşılaştırmalar her zaman küçük harf | Aynı cüzdanın iki ayrı kullanıcı olarak görünmesini önler |
| K12 | Tutar | Kolonlar `Numeric(78,18)`, API'de string. Ham değerle insan okunur değer arasındaki dönüşüm token başına `decimals` ile yapılır (`to_raw`/`from_raw`). Frontend `Number()` yerine `formatUnits` kullanır | 18 ondalıklı tokenlar bugünkü şemada kesiliyor |
| K13 | Yayına alma | Stellar sürümü `v1-stellar` etiketiyle dondurulur. Monad backend'i ayrı bir compose projesinde (`traderkirala-monad`, port 8013) ve yeni alt alan adında (`monadback.yolalapp.com`) ayağa kalkar. Geçiş sonunda DNS/nginx değiştirilir | Canlı Stellar demosu sprint boyunca bozulmaz |

---

## 3. Görevler

Tahminler: S ≤ ½ gün, M ≈ 1 gün, L ≈ 2 gün, XL ≈ 3+ gün.
İzler: **SC** kontrat, **BE** backend, **FE** frontend, **OPS** altyapı.

### Gün 0: Hazırlık (bloklayıcı)

| ID | Görev | Tahmin | Bağımlılık |
|---|---|---|---|
| OPS-01 | Sunucudaki backend reposunda `git init` ve ilk commit (mevcut hâl), `v1-stellar` etiketi, private remote'a push. `.bak` dosyaları ve `src.bak.*` dizinleri ayrı bir commit'te silinir | S | — |
| OPS-02 | `.dockerignore`'a `tests/`, `*.bak`, `_parked/`, `backups/` eklenir. Sunucudaki `.env` dosyasından kullanılmayan `PLATFORM_SECRET`, `POOL_FUNDING_XLM` ve `FIGMA_TOKEN` çıkarılır | S | OPS-01 |
| OPS-03 | Monad testnet ön keşfi: faucet'ten deployer ve demo cüzdanları için MON alınır. Uniswap v2/v3 adresleri, test USDC ve explorer (monadvision / monadscan testnet) doğrulanır. K5b'ye burada karar verilir | S | — |
| OPS-04 | Kararlar tablosunun (K1–K13) ekiple onaylanması | S | — |

### Kontrat: `contracts-evm/` (yeni, Foundry)

| ID | Görev | Tahmin | Bağımlılık |
|---|---|---|---|
| SC-01 | Foundry iskeleti, OZ bağımlılıkları, `foundry.toml`'da `monad_testnet` profili (chainId 10143), CI'da `forge build` ve `forge test` | S | OPS-03 |
| SC-02 | `TraderVault.sol` veri modeli: `Terms`, `Agreement`, `Reservation`, `Config`, `TokenInfo`, `balances[id][token]`, `tokens[id]` (en fazla 6). 21 hata kodu custom error olarak, 12 olay Solidity event'i olarak (en fazla 3 indexed alan) | M | SC-01 |
| SC-03 | Admin ve UUPS: `initialize`, `setToken`, `setRouter`, `setFees`, `setPaused`, `setSettleSlippage`, `_authorizeUpgrade`. Admin rolü Ownable2Step'e | M | SC-02 |
| SC-04 | Rezervasyon: `reserve`, `release(id, amount)` ve ayrı `releaseAll(id)` (`amount==0` artık hata verir) | M | SC-02 |
| SC-05 | Sözleşme yaşam döngüsü: `propose`, `open`, `openReserved`, `fund`, `fundReserved`, `accept`, `cancel`. `caller` parametresi kalkar, yerine `msg.sender` | L | SC-04 |
| SC-06 | `trade`: SafeERC20 `forceApprove(router, amt)`; credited değeri `min(bildirilen, bakiye farkı)`; drawdown kontrolü; `nonReentrant`; CEI sırası | L | SC-05 |
| SC-07 | `settle` (üç kademe), `claim`, in-kind dust ve orphan pozisyonlar. Fee transferi `try/catch` ile yapılır, başarısız olursa müşteriye eklenir. **Settle'da bakiyeler swap'tan önce sıfırlanır** (bugün sıra ters) | L | SC-06 |
| SC-08 | Güvenlik düzeltmeleri: `platformFeeBps` sözleşme açılışında Agreement içine sabitlenir. Admin settle yoluna `lastValue` tabanı eklenir. `setRouter` iki adımlı ve 24 saat gecikmeli olur. Upgrade için Safe veya timelock notu | M | SC-07 |
| SC-09 | `MockRouter.sol` ve `TestToken.sol` (mint'li ERC-20, ondalık parametreli) | S | SC-01 |
| SC-10 | Foundry testleri: Rust'taki 47 testin karşılıkları, ek olarak reentrancy (kötü niyetli token ve router), farklı ondalıklı tokenlar, UUPS upgrade, settle matematiği için fuzz, "toplam bakiye = kontrat bakiyesi" invariant'ı | XL | SC-02…SC-09 |
| SC-11 | `script/Deploy.s.sol`: tokenlar, router ve fiyatlar, vault proxy, allow-list. Monad testnet'e deploy ve explorer'da verify. Adresler `deployments/monad-testnet.json` dosyasına yazılır | M | SC-10 |
| SC-12 | ABI dışa aktarma: `out/*.json` dosyaları backend'e (`app/services/chain/abi/`) ve frontend'e (`src/lib/chain/abi.ts`, `as const`) kopyalanır | S | SC-11 |

### Backend: zincir katmanı

| ID | Görev | Tahmin | Bağımlılık |
|---|---|---|---|
| BE-01 | `requirements.txt`: `stellar-sdk` çıkar; `web3>=7`, `eth-account`, `siwe` eklenir. `core/config.py`'a `CHAIN_ID`, `RPC_URL`, `VAULT_ADDRESS`, `ROUTER_ADDRESS`, `CONFIRMATIONS`, `EXPLORER_URL`, `PLATFORM_ADDRESS` eklenir; passphrase, Horizon, friendbot, soroswap ve SEP-10 ayarları silinir | S | OPS-01 |
| BE-02 | `services/chain/` paketi: `types.py` (`UnsignedTx{to,data,value,chain_id,gas}`, `EventRecord{block,log_index,tx_hash}`, `TxResult`), `addresses.py` (`is_evm_address`, normalize), `amounts.py` (`to_raw`/`from_raw`, uint256) | M | BE-01 |
| BE-03 | `MonadGateway` (`AsyncWeb3`): `read_*` (eth_call), `build_*` (ABI encode ve `estimate_gas`), `get_receipt`, `get_logs(from,to)`, `token_balance`, `native_balance`, `quote` (router `getAmountsOut`) | L | BE-02, SC-12 |
| BE-04 | `abi.py`: custom error seçicilerinden `VaultError` koduna eşleme, event decode (`process_receipt` / `process_log`). `contract_abi.py` silinir | M | SC-12 |
| BE-05 | `FakeMonadGateway`: `fake.py`'deki durum makinesi calldata, receipt ve log üretecek şekilde taşınır. **Eksik olan rezervasyon build'leri de eklenir** | L | BE-03 |
| BE-06 | Auth, SIWE: `/auth/nonce` EIP-4361 mesaj parçalarını döner (domain, uri, chainId, nonce, issuedAt, expiration); `/auth/verify` `recover_message` ile doğrular, domain ve chainId kontrol edilir. `/auth/sep10*` ve `sep10.py` silinir. `/auth/nonce` için IP başına rate limit. `/auth/refresh`'e mutlak üst süre (ör. ilk girişten 30 gün) ve `jti` eklenir | M | BE-02 |
| BE-07 | İşlem modeli (K3): `PendingTransaction`'a `to_address`, `calldata` ve `value` eklenir, `tx_hash` nullable olur. `/tx/submit {pending_tx_id, tx_hash}` receipt'i alıp `from`, `to` ve `input` eşleşmesini doğrular ve `apply_tx_result` çalıştırır. **Kilit poll süresince tutulmaz**: durum `submitted` yapılır, commit edilir, sonra receipt beklenir | L | BE-03, BE-04 |
| BE-08 | Onay adımı (K4): `open`, `fund` ve `reserve` build yanıtına `pre_steps: [{kind:"approve", to:token, data}]` eklenir; mevcut allowance yeterliyse bu adım boş gelir | M | BE-07 |
| BE-09 | Indexer: `eth_getLogs` pencereleri (ör. 2.000 blok), `CONFIRMATIONS` derinliği, `last_block_hash` ile reorg tespiti. **Hata veren olay artık yutulmaz**: cursor ilerlemez, olay `failed_events` tablosuna yazılır ve yeniden denenir. Trader istatistik hesabı `services/trader_stats.py`'ye taşınır | L | BE-04 |
| BE-10 | `agreements.py`, `trading.py` ve `listings.py` build çağrılarının yeni gateway'e bağlanması; `C…` ve `G…` kontrolleri yerine `is_evm_address` | M | BE-03 |
| BE-11 | `wallet.py`: bakiyeler RPC'den (MON ve allow-list'teki ERC-20'ler). Trustline ve SEP-7 silinir; transfer için `ethereum:` EIP-681 URI. `/wallet/faucet` test tokenı mint eder (günlük limitli) | M | BE-03 |
| BE-12 | Anchor'ın kaldırılması (K7): `routers/anchor.py`, `services/anchor.py`, `worker/anchor_sync.py`, anchor modelleri ve şemaları, `test_anchor.py` | S | — |
| BE-13 | `/config`: `chain_id`, `rpc_url`, `explorer_url`, `vault_address`, `router_address`, `assets[{address,symbol,decimals}]`, `auth.siwe_domain`. `/health/stellar` yerine `/health/chain` (`eth_blockNumber`). `/market/candles`: Horizon yerine mock router fiyat geçmişi ya da kaldırılır | S | BE-01 |
| BE-14 | Worker: `reconciler` yeni gateway'e bağlanır, `anchor_sync` silinir, fx job'unda XLM fiyatı yerine MON fiyatı (sabit ya da kapalı) | S | BE-09 |

### Backend: şema, iş katmanı, hatalar

| ID | Görev | Tahmin | Bağımlılık |
|---|---|---|---|
| BE-20 | Alembic `monad_cutover` (irreversible, K10). Öncesinde `pg_dump` alınır. İçeriği: ayna tablolarının truncate edilmesi; `stellar_address` → `wallet_address String(42)`; tüm hash kolonlarının `String(66)` olması; `ledger` → `block_number BigInteger`; `onchain_seq` → `log_index`; `Numeric(30,7)` → `Numeric(78,18)`; `assets` tablosunda `issuer`, `canonical` ve `is_native` alanlarının düşmesi, `chain_id` eklenmesi; anchor tablolarının drop edilmesi | L | BE-02 |
| BE-21 | Şema ve servislerde isim temizliği: `UserOut.stellar_address` → `wallet_address`, `AmountIn` 18 ondalığa, `default_base_asset_code` tUSDC olur. `seed_assets.py` deploy JSON'unu okur | M | BE-20, SC-11 |
| BE-22 | Gizlilik: `GET /traders/{id}/profile` içindeki `live_positions` müşteri adı ve tutarlarını anonimleştirir; `notify_investors=false` olan trade'ler gizlenir; `GET /users/{id}` `budget_amount` alanını göstermez | S | — |
| BE-23 | İlan bütünlüğü: rezerve edilmiş capital ilanında `amount` PATCH ile değiştirilemez. Teklif kabul edilince ilan `matched` olur ve diğer bekleyen teklifler `rejected` olur. Teklifte `max_loss_bps` ilandakinden gevşek olamaz | M | — |
| BE-24 | `GET /listings/{id}`: draft ve closed ilanlar yalnızca sahibine gösterilir; görüntülenme sayacı GET isteğinde artmaz (ayrı bir `POST /view` ucu). `GET /agreements/{id}` zincirden yenilemeyi 10 saniyelik bir cache arkasına alır | S | — |
| BE-25 | Admin: `is_admin` bayrağı taraf yetkisi yerine geçmez (mesajlar, ilan düzenleme). `PATCH /admin/users` rol değişikliğini reddeder. Prod'da `cors_origins` ve `docs_enabled` daraltılır | S | — |
| BE-26 | Yasal metinler, README ve `docs/ARCHITECTURE.md`: Stellar, SEP, Freighter ve havuz anlatımları Monad'a göre güncellenir. "Elevator" isim kalıntıları temizlenir | M | — |
| BE-27 | Test paketi: `conftest` `Keypair.random()` yerine `eth_account.Account.create()`; Stellar'a özgü testler silinir ya da yeniden yazılır; rezervasyon akışı ve SIWE testleri eklenir; kırık listing testleri düzeltilir; anvil üzerinde gerçek kontratla bir entegrasyon testi (`pytest -m chain`) | L | BE-05, BE-06, SC-11 |

### Frontend: `app/`

| ID | Görev | Tahmin | Bağımlılık |
|---|---|---|---|
| FE-30 | Bağımlılıklar: `viem` ve `wagmi` (web) eklenir; `@stellar/stellar-sdk`, `@creit.tech/stellar-wallets-kit` ve `@stellar/freighter-api` çıkarılır. `lib/stellar/` yerine `lib/chain/` (`config.ts` içinde `monadTestnet`, explorer linkleri, `shortAddress` 0x için). `env.ts` ve `.env.example` güncellenir | S | — |
| FE-31 | Cüzdan arayüzü: `WalletAdapter{connect, address, chainId, signMessage, sendTransaction, switchChain}`. XDR, `signAuthEntry` ve passphrase kalkar | S | FE-30 |
| FE-32 | Web adaptörü (`wallet.web.ts`): wagmi config (injected ve WalletConnect). Monad Testnet cüzdanda yoksa `wallet_addEthereumChain` ile eklenir | M | FE-31 |
| FE-33 | Mobil WalletConnect: `stellar` namespace'i `eip155:10143` olur; `personal_sign`, `eth_sendTransaction`, `wallet_switchEthereumChain`; `optionalNamespaces`; deeplink'ler MetaMask, Rainbow ve Trust için | M | FE-31 |
| FE-34 | Uygulama içi cüzdan: viem `generatePrivateKey` ve `privateKeyToAccount`, `expo-secure-store`, RPC üzerinden `sendTransaction`. **Web'de export edilmez.** Yedekleme ekranı ile "anahtarı göster" ve "cüzdanı unut" seçenekleri. `sep7.ts` silinir | M | FE-31 |
| FE-35 | SIWE girişi: `lib/auth/siwe.ts` (`createSiweMessage`, `/auth/nonce`, imza, `/auth/verify`). `sep10.ts` silinir. `session.loginWithSiwe`. Hata eşlemesi: 4001 → `USER_REJECTED`, yanlış zincir → otomatik `switchChain` | M | FE-32, FE-33, BE-06 |
| FE-36 | **Mevcut hataların düzeltilmesi:** `register()` `RegisterOut{user, token}` okur ve token'ı saklar (kayıt döngüsü çözülür); refresh isteği 401 köprüsünü atlar (kilitlenme çözülür); `hydrate` içindeki `JSON.parse` try/catch'e alınır; kayıt ekranı giriş ekranıyla aynı cüzdan yolunu kullanır | S | — |
| FE-37 | Tx yürütücüsü `lib/chain/tx.ts`: `build` → `pre_steps` (approve) → `sendTransaction` → `/tx/submit {pending_tx_id, tx_hash}` → `GET /tx/{id}` ile durum. UI durumları: imza bekleniyor, gönderildi, onaylandı, başarısız; her birinde explorer linki | M | FE-31, BE-07, BE-08 |
| FE-38 | `endpoints.ts` ve `types.ts`'in OpenAPI ile senkronu (yeni `/openapi.json`'dan): sayfalama offset/limit, `unreadCount` şekilleri, `markAllRead` gövdesi, `fxApi`, `tradersApi`, `ConfigOut`. Tipler el yerine `openapi-typescript` ile üretilir | M | BE-13, BE-21 |
| FE-39 | Tutar biçimi: `formatAmount` string ve decimals alır (`formatUnits`); girişte `parseUnits`. `Number()` kullanımı kaldırılır | S | FE-30 |
| FE-40 | Metinler: giriş ekranında "Connect wallet (MetaMask, Rabby, WalletConnect)", ağ etiketi "Monad Testnet", Freighter, xBull ve SEP-10 geçen metinler kaldırılır | S | FE-35 |

### Uçtan uca akış ekranları (Monad üzerinde)

Sprint 1'de Placeholder kalan ekranlardan yalnızca demo akışının ihtiyaç duyduğu dördü bu sprinte alındı. Diğerleri (FE-08 Mesajlar, FE-12 Hareketler, FE-14 İşlemler, FE-17 Trader Profili, FE-19 Bildirimler) Sprint 3'e kalıyor.

| ID | Görev | Tahmin | Bağımlılık |
|---|---|---|---|
| FE-41 | (eski FE-16 ve FE-15) İlan oluşturma ve İlanlarım; capital ilanı için **reserve** tx'i (approve ve reserve) | L | FE-37 |
| FE-42 | (eski FE-09) Sözleşme ekranı: teklif kabul, open/fund/accept, cancel, settle; durum ve bakiye; "Onaylamak için kaydır" | L | FE-37 |
| FE-43 | (eski FE-18) Cüzdan ekranı: MON ve token bakiyeleri, "Test USDC al" (`/wallet/faucet`), MON için faucet linki, adres kopyalama ve QR | M | BE-11, FE-39 |
| FE-44 | (eski FE-10 ve FE-11) Panel, iki rol için sade hâli: aktif sözleşmeler, toplam değer | M | FE-38 |

### Altyapı ve teslim

| ID | Görev | Tahmin | Bağımlılık |
|---|---|---|---|
| OPS-10 | `traderkirala-monad` compose projesi (port 8013, ayrı DB volume), `monadback.yolalapp.com` için nginx ve certbot; `.env` oluşturma betiği (`gen_env.py`) Monad değişkenleriyle | M | BE-01 |
| OPS-11 | Deployer ve platform anahtarları: deployer anahtarı sunucuda **tutulmaz**. Admin işlemleri operatör cüzdanından ya da Safe ile yapılır. Faucet mint için ayrı ve düşük yetkili bir `MINTER_KEY` kullanılır (yalnızca test tokenlarında minter) | S | SC-11 |
| OPS-12 | `scripts/e2e_testnet.py` Monad'a göre yeniden yazılır: iki cüzdanla SIWE, ilan, teklif, open, trade, settle, claim. Her gece çalışır ve raporlar | L | BE-27 |
| OPS-13 | Web deploy (eski FE-21): `expo export --platform web` ile Vercel/Netlify; `EXPO_PUBLIC_API_BASE_URL=monadback…` | S | FE-35 |
| OPS-14 | Geçiş (cutover): `mobilback.yolalapp.com` Monad backend'ine yönlendirilir, Stellar compose durdurulur (volume silinmez) | S | OPS-12 |
| OPS-15 | README ve mimari: Mermaid diyagramı, kontrat adresleri ve deploy tx'leri, kurulum ve test adımları. | M | OPS-14 |

---

## 4. Takvim (öneri)

| Gün | Kontrat | Backend | Frontend |
|---|---|---|---|
| 0 | OPS-03 | OPS-01, OPS-02 | OPS-04, FE-36 |
| 1–2 | SC-01, SC-02, SC-03, SC-09 | BE-01, BE-02, BE-12, BE-20 (taslak) | FE-30, FE-31, FE-39 |
| 3–4 | SC-04, SC-05, SC-06 | BE-06, BE-22, BE-23, BE-24, BE-25 | FE-32, FE-33, FE-34 |
| 5–6 | SC-07, SC-08, SC-10 (başlangıç) | BE-03, BE-04 (geçici ABI ile) | FE-35, FE-40 |
| 7 | SC-10, **SC-11 deploy**, SC-12 | BE-05, BE-07, BE-08 | FE-37, FE-38 |
| 8 | — | BE-09, BE-10, BE-11, BE-13, BE-14, BE-21 | FE-41, FE-43 |
| 9 | Kontrat incelemesi (§5) | BE-27, OPS-10, OPS-11 | FE-42, FE-44 |
| 10 | — | OPS-12 e2e, OPS-14 | OPS-13, OPS-15, demo provası |

**Kritik yol:** SC-02 → SC-07 → SC-11 (deploy, Gün 7) → BE-03/04/07 → FE-37 → FE-42 → OPS-12.
Kontrat deploy'u Gün 7'den sonraya kayarsa backend ve frontend `FakeMonadGateway` ile ya da anvil'e yerel deploy ile ilerler. Monad testnet deploy'u sprintin son iki gününe sıkıştırılmaz.

**Kapasite notu:** Plan en az 3 kişilik bir ekip varsayıyor (kontrat, backend, frontend birer kişi). Tek kişiyle bu kapsam yaklaşık 4 haftadır. O durumda FE-41…FE-44 ve BE-22…BE-26 Sprint 3'e kaydırılır; sprint hedefi "SIWE girişi ve bir sözleşmenin CLI ya da e2e betiğiyle uçtan uca çalışması" olarak daralır.

---

## 5. Definition of Done

1. `forge test` yeşil, `forge coverage` vault için %90 satır kapsamının üzerinde; Slither'da yüksek önem düzeyinde bulgu yok (ya da gerekçeli olarak kabul edilmiş).
2. Kontrat Monad testnet'te deploy edilmiş ve explorer'da **verify** edilmiş; adresler `deployments/monad-testnet.json`, README ve `/config`'te.
3. Backend `pytest` yeşil; `grep -ri "stellar\|soroban\|horizon\|xdr\|sep10\|stroop" app/` iş kodunda sonuç vermiyor (yalnızca migration geçmişinde kalabilir).
4. Frontend `npm run check` ve `npm run export:web` temiz; `grep -ri stellar src app` boş.
5. `e2e_testnet.py` Monad testnet'te iki gerçek cüzdanla geçiyor; tx hash'leri explorer'da görülüyor.
6. Web demosunda MetaMask ile, mobilde Expo Go ve uygulama içi cüzdanla giriş, sözleşme açma ve settle elle denenmiş, ekran kaydı alınmış.
7. Sahte veri yok (Sprint 1 kuralı devam ediyor).

---

## 6. Riskler

| Risk | Etki | Önlem |
|---|---|---|
| Spot quote ile drawdown atlatma (bilinen açık), Monad'da atomik bundle'larla kolaylaşıyor | Yüksek (mainnet) | Testnet'te kabul edilir ve belgelenir. Mainnet öncesinde oracle ya da TWAP ile `min(quote, oracle)` (Sprint 3) |
| Reentrancy (EVM'ye özgü) | Yüksek | `nonReentrant`, CEI sırası, kötü niyetli token ve router testleri (SC-10) |
| Admin anahtarı (router değişimi, upgrade) | Yüksek | Ownable2Step; router için gecikme; mainnet öncesinde Safe ve timelock |
| Monad testnet'te DEX ya da likidite yetersiz | Orta | K5: kendi mock router'ımız birincil yol |
| WalletConnect `eip155:10143` cüzdanlarda tanınmıyor | Orta | Bağlantıda `wallet_addEthereumChain`; mobilde uygulama içi cüzdan yedek yol |
| `approve` adımı UX'i uzatıyor (iki imza) | Düşük | Tam tutarlı approve ve açık adım göstergesi; sonraki sprintte permit |
| Monad RPC rate limit (25 rps) altında indexer | Orta | Blok penceresi ve geri çekilme (backoff); gerekirse ücretli RPC (QuickNode ya da Alchemy) |
| Canlı Stellar demosunun bozulması | Orta | K13: ayrı compose ve alt alan adı, cutover en son gün |
| Backend'de git geçmişi olmaması | Yüksek | OPS-01 Gün 0'da, ilk iş |

## 7. Sprint dışı (Sprint 3 adayları)

Kalan ekranlar (Mesajlar, Hareketler, Trader Profili, Bildirimler, İşlemler); EIP-2612 permit ya da Permit2; gas sponsorluğu (ERC-4337 paymaster); Reown AppKit RN ve development build; fiat on-ramp; oracle tabanlı değerleme; mainnet (chainId 143) hazırlığı ve denetim; hesap silme API'si; engelleme ve şikâyet.
