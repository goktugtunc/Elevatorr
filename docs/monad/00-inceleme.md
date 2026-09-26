# Monad geçişi — başlangıç incelemesi (26 Eyl 2026)

Bu dosya, Stellar sürümünün (tag `v1-stellar`, repo `backend/` taban commit'i) salt okunur incelemesinin
özetidir. Geçişi yapan herkes (insan ya da ajan) önce bunu, sonra `SPRINT-2-MONAD.md`'yi okur.
Satır numaraları `v1-stellar` tabanına göredir.

Hedef ağ: **Monad Testnet**, chainId `10143`, RPC `https://testnet-rpc.monad.xyz` (eth_chainId → `0x279f` doğrulandı),
yerel token MON (18 ondalık). Mainnet chainId `143`. Kanonik adresler (mainnet): WMON `0x3bd359C1119dA7Da1D913D1C4D2B7c461115433A`,
Multicall3 `0xcA11bde05977b3631167028862bE2a173976CA11`, Permit2 `0x000000000022d473030f116ddee9f6b43ac78ba3`.
Deployer/admin cüzdanı: `0x67aD0CaE18528017B79E0aF8C4D3dd5937caFF19` (anahtar yerel makinede, sunucuya konmaz).

---

## A. Kontrat: `backend/contracts/vault` (Rust / Soroban) — Solidity portu için referans

### Public fonksiyonlar (`vault/src/lib.rs`)

| Fonksiyon | Auth | Ne yapar / event / hata |
|---|---|---|
| `__constructor(admin, router, fee_recipient, platform_fee_bps, settle_slippage_bps)` :298 | — | Config yazar, NextId=1. fee ≤ 1000, slippage ≤ 5000 bps, aşarsa `InvalidTerms(4)` |
| `set_token(token, allowed, is_base)` :329 | admin | TokenInfo yazar; `is_base` ancak `allowed` ise. Event `TokenSet` |
| `set_router(router)` :346 | admin | Event `ConfigChanged{key:"router"}` |
| `set_fees(bps, recipient)` :355 | admin | bps ≤ 1000. Event `ConfigChanged("fees")` |
| `set_paused(bool)` :370 | admin | Event `ConfigChanged("paused")` |
| `set_settle_slippage(bps)` :379 | admin | bps ≤ 5000. Event `ConfigChanged("slippage")` |
| `upgrade(wasm_hash)` :393 | admin | Event `Upgraded` |
| `reserve(customer, token, amount, listing_ref)` :408 | customer | Pause'da çalışmaz (3). amount>0 (15), token base olmalı (5). Tokeni kontrata çeker, Reservation açar. Event `Reserved` |
| `release(id, amount)` :455 | res.customer | Pause'da da çalışır. `amount<=0` → tamamını iade eder (tehlikeli API). Hatalar 18/19/20. Event `Released` |
| `propose(trader, terms)` :504 | trader | Pause (3); `trader==terms.trader` değilse (2); `validate_terms` :60. status=Proposed. Event `Proposed` |
| `open(customer, terms)` :529 | customer | Principal'ı escrow'a alır, status=Funded. Event `Opened` |
| `open_reserved(customer, terms, res_id)` :557 | customer | Transfer yok, rezervasyondan düşer (`draw_reservation` :121; 18/19/20/21). Events `ReservationDrawn` + `Opened` |
| `fund(id)` :588 | terms.customer | Proposed→Active, token yeniden kontrol (5). Event `Activated` |
| `fund_reserved(id, res_id)` :609 | terms.customer | fund gibi, para rezervasyondan |
| `accept(id)` :629 | terms.trader | Funded→Active (7). Event `Activated` |
| `cancel(id, caller)` :645 | Proposed'da yalnız proposer; Funded'da iki taraf (17) | Funded ise principal müşteriye iade. Pause'da da çalışır. Event `Cancelled` |
| `trade(id, in, out, amt, min_out, deadline)` :680 | terms.trader | pause, Active, `now<end_time` ve `deadline>=now` (8), amt>0 & min_out>0 (15), in≠out & ikisi allow-list'te (5), bakiye (10), MAX_TOKENS=6 (11), router slippage (13/16), alınan token'ın base karşılığı 0 ise (5), drawdown (12). `last_value` güncellenir. Event `Traded` |
| `settle(id, caller, min_outs)` :801 | kademeli (aşağıda) | Active→Settled. Events `Unliquidated`* + `Settled`. Hatalar 7/9/12/13/15/16 |
| `claim(id, token)` :967 | terms.customer | Settled (7), bakiye>0 (10). Event `Claimed` |
| Views | — | `get_agreement`, `get_balances`, `value_in_base`, `get_config`, `is_token_allowed`, `next_id`, `get_reservation`, `next_reservation_id` |

Hata kodları 1–21 (`errors.rs:10-55`); backend `contract_abi.py` ve mobil bu kodları kopyalıyor.

### Mock router (`mock_router/src/lib.rs`)
Admin: `__constructor(admin)`, `set_price(in,out,num,den)` :98, `clear_price` :117, `set_strict(bool)` :126, `withdraw` :133.
Soroswap alt kümesi: `router_get_amounts_out` :143 (sabit oran, çok adım), `router_pair_for` :152, `swap_exact_tokens_for_tokens` :156 (deadline kontrolü, input'u `to`'dan çeker, output'u kendi bakiyesinden öder). `RouterError` 1–7.

### Veri modeli ve storage
- `Agreement` (`types.rs:103`): id, terms, status, proposer, created/start/end, `tokens: Vec<Address>` (base 0. sırada, ≤6), settled_at, final_value, trader_fee, platform_fee, customer_payout, `last_value`.
- `Terms` :85: customer, trader, base_token, `principal: i128`, duration (1 gün–3 yıl), commission (≤5000), max_drawdown (100–10000; 10000=kapalı), `listing_ref: BytesN<32>`.
- `Reservation` :68, `ReservationStatus` (Open/Released/Consumed). `Config` :131. `TokenInfo{allowed,is_base}` :142.
- Storage: instance (Config, NextId, NextReservationId, AllowedToken), persistent (Agreement, Reservation, Balance(id,token)). TTL uzatmaları Solidity'de kalkar.

### Ekonomik mantık
- Settle bölüşümü (`math.rs:50`): `profit=max(final−principal,0)`; trader ve platform payı `profit×bps/1e4` aşağı yuvarlanır; müşteri kalanı alır; zararda fee yok. **Platform fee settle anında Config'ten okunuyor** (`lib.rs:920`), anlaşmaya sabitlenmiyor.
- Drawdown tabanı `principal×(1e4−dd)/1e4` (`math.rs:22`); trade sonrası (`lib.rs:760`) ve trader settle'ında (:903) kontrol.
- Settle kademeleri (:814-831): (1) taraflar her an, `min_outs` uzunluğu base dışı token sayısına eşit ve olduğu gibi kullanılır; (2) admin yalnız `end_time` sonrası, `min_out=max(min_outs[i], quote×(1−slip))`; (3) keeper (herkes) `end_time+7 gün` sonrası, imza yok, ek şart `final_value ≥ last_value×(1−slip)` (:909).
- In-kind dust: base karşılığı 0 olan pozisyon müşteriye token olarak gönderilir (`try_transfer` :866); başarısızsa bakiye kontratta kalır ("orphan"), müşteri `claim` ile alır; `final_value`'ya dahil değil.
- Fee transferi başarısız olursa tutar müşteri payına eklenir (:928-935).
- Swap çıkışı `credited=min(bildirilen, gerçek bakiye farkı)` (:272).
- Pause: propose/open/fund/accept/trade/reserve durur; cancel/settle/claim/release çalışır.
- Reserve/release: para kontratta, bağlanma muhasebe. Rezervasyondan açılmış anlaşma cancel edilirse para **cüzdana** döner.
- Soroswap: `router.rs:8` trait (swap, getAmountsOut, pairFor). `swap_via_router` :228 tek adımlı path `[in,out]`. Değerleme `quote` :173 / `valuation` :196: her base dışı bakiye `getAmountsOut([token, base])` ile; quote hatası → 0. Spot fiyat, oracle yok.

### Testler (47 vault + 3 mock) → Foundry'ye taşınacak
constructor_sets_config_and_ids, constructor_rejects_platform_fee_above_cap, constructor_rejects_slippage_above_cap, admin_token_allow_list, admin_config_changes_emit_events_and_validate, admin_functions_reject_non_admin, upgrade_admin_path_reaches_deployer, lifecycle_customer_initiated, lifecycle_trader_initiated_with_profit, settle_with_loss_pays_no_fees, settle_without_trades_returns_principal, settle_keeper_path_opens_after_grace_with_reference_floor, settle_by_admin_after_expiry_requires_auth_and_honours_min_outs, settle_by_trader_cannot_go_below_drawdown_floor, settle_post_expiry_by_party_still_uses_their_min_outs, settle_validation, fee_leg_that_cannot_be_received_is_folded_into_customer_payout, paused_blocks_new_activity_only, invalid_terms_are_rejected, token_allow_list_is_enforced, max_tokens_bound_and_slot_reuse, trade_router_slippage_failure_rolls_back, trade_recheck_catches_router_that_ignores_min_out, trade_drawdown_breach_rolls_back, trade_rejects_token_without_route_back_to_base, trade_input_validation, lifecycle_functions_reject_wrong_signer, cancel_proposed_only_by_proposer, cancel_funded_refunds_customer, ttl_is_extended_on_writes (silinir), views_on_missing_agreement, value_in_base_counts_unquotable_token_as_zero, settle_delivers_unquotable_dust_in_kind_on_every_path, settle_orphans_unreceivable_in_kind_leg_until_claimed, ids_are_sequential_across_propose_and_open, settlement_math_invariants (fuzz), drawdown_and_slippage_math, reserve_locks_capital_and_release_gives_it_back, reserve_validation, release_works_while_paused, open_reserved_draws_the_principal_without_touching_the_wallet, fund_reserved_activates_a_proposed_agreement, reserved_funding_rejects_a_reservation_that_does_not_fit, a_reservation_bigger_than_the_principal_keeps_the_rest_locked, reserved_agreement_settles_and_pays_the_customer, cancelling_a_reserved_agreement_refunds_the_wallet, reservation_ids_are_their_own_sequence; mock: quote_and_swap, errors, admin_only.
Eklenecek: reentrancy (kötü niyetli token/router), UUPS upgrade, farklı decimals'lı tokenlar, invariant (toplam bakiye = kontrat bakiyesi).

### Riskler (Solidity'de düzeltilecek)
1. Drawdown tek işlemde atlatılabilir (spot quote). Testnet'te kabul, belgelenir; mainnet öncesi oracle/TWAP.
2. Admin + kötü router = süresi dolmuş anlaşmaları boşaltma. Admin settle yoluna `lastValue` tabanı; `setRouter` gecikmeli.
3. Platform fee sonradan değişebiliyor → Agreement'a sabitle.
4. Keeper yolunda canlılık (in-kind'a düşen token → kalıcı SlippageExceeded) — belgelenir.
5. `release(0)` = tamamını iade → ayrı `releaseAll`, `amount==0` hata.
6. **Reentrancy (EVM)**: swap, fee ödemesi, in-kind transfer dış çağrı. `nonReentrant` + CEI. Settle'da `remove_balance` swap'tan sonra (:893) → Solidity'de sıra ters.
7. Fee-on-transfer/rebasing token allow-list dışı.

---

## B. Backend: zincir katmanı (FastAPI + Postgres + worker)

### Mimari
- compose: `db` (pg16), `api` (uvicorn 2 worker, `127.0.0.1:8012`), `worker` (`python -m app.worker.main`, tek kopya).
- Worker işleri (`app/worker/main.py:150-156`): indexer (5 sn, getEvents→DB), reconciler (60 sn), anchor_sync (20 sn), expiry, push, fx.
- Zincir katmanı tembel singleton `get_soroban()/get_horizon()` (`app/services/stellar/__init__.py:21-52`); testler fake enjekte eder.
- İşlem döngüsü: servis `soroban.build_*` → simülasyon + imzasız XDR + hash `PendingTransaction`'a (`agreements.py:361-373`) → mobil imzalar → `POST /tx/submit` → `_verify_envelope` (`tx_submit.py:50-62`) → send + poll (60 sn) → `apply_tx_result` (`tx_submit.py:102-144`) → worker aynı olayları idempotent işler (`indexer.py:889-951`).

### Stellar'a bağlı modüller → Monad karşılığı
| Modül | Ne yapıyor | Monad | Efor |
|---|---|---|---|
| `stellar/soroban.py` (885) | RPC, simulate/send/poll/get_events/read/build_* | web3.py `AsyncWeb3`: eth_call, estimate_gas, receipt, eth_getLogs | L |
| `soroban.py:357-395 _parse_meta` | event çıkarımı | `receipt.logs` + ABI decode | M |
| `soroban.py:498-565` Soroswap quote | router_get_amounts_out | UniswapV2 `getAmountsOut` | M |
| `soroban.py:814-875 build_payment/change_trust` | ödeme/trustline | ERC-20 transfer / native; trustline silinir | S |
| `stellar/contract_abi.py` (996) | SCVal, VaultError, 12 event | ABI JSON + custom error selector eşlemesi | L |
| `stellar/xdr_utils.py` | stroop, strkey | `eth_utils` | S |
| `stellar/horizon.py` (455) | hesap, bakiye, ödeme geçmişi, friendbot | eth_getBalance, balanceOf; geçmiş Transfer logları | M |
| `stellar/sep10.py` | SEP-10, ed25519 | SIWE + `Account.recover_message` | M |
| `stellar/admin_tx.py` | admin çağrı, platform pubkey | ABI encode; `PLATFORM_ADDRESS` | S |
| `stellar/types.py` | AssetRef, EventRecord, TxResult, UnsignedTx | EventRecord(block, log_index), UnsignedTx{to,data,value,chain_id} | S |
| `stellar/fake.py` (1207) | bellek içi kontrat kopyası | `FakeMonadGateway` (durum makinesi taşınır, `fake.py:344-585`) | L |
| `services/tx_submit.py` | imzalı XDR doğrula/gönder | yeni model (aşağıda) | M |
| `services/indexer.py` (1159) | cursor'lı getEvents | eth_getLogs pencereleri + onay derinliği + reorg | M |
| `services/agreements.py:124-138,426-483,505-532` | Terms, build, XLM fiyatı | aynı; **approve adımı** eklenir | M |
| `services/trading.py:81,140-299` | C… çözümleme, build_trade | 0x kontrolü | S |
| `services/wallet.py` | Horizon bakiye, SEP-7 (:477), friendbot (:422), trustline (:535-545) | RPC bakiye; EIP-681; faucet | M |
| `services/anchor.py` (1809) | SEP-1/10/24/6/12, stellar_sdk import (:51-58) | **kaldırılır** | S |
| `services/amounts.py`, `schemas/common.py` | 7 ondalık, i128 | uint256, token başına decimals | M |
| `core/config.py` | passphrase, Horizon, Soroswap | CHAIN_ID, RPC_URL, VAULT_ADDRESS, ROUTER_ADDRESS, CONFIRMATIONS | S |
| `routers/config.py:86-121` | mobil config | chainId, rpc, explorer, adresler | S |
| `routers/meta.py:36-52 /health/stellar` | Horizon ping | `/health/chain` eth_blockNumber | S |
| `requirements.txt` | stellar-sdk 16.1 | web3>=7, eth-account, siwe | S |

**İmzalama modeli (kritik):** EVM cüzdanları `eth_signTransaction` vermez; nonce/gas cüzdanda. Yeni akış: backend `{to, data, value, chain_id, gas}` döner → uygulama `eth_sendTransaction` → `POST /tx/submit {pending_tx_id, tx_hash}` → backend receipt'te `from==user`, `to==vault`, `input==calldata` doğrular. `tx_hash` yapım anında boş; `_verify_envelope` baştan yazılır.

### Auth (mevcut)
- `POST /auth/nonce`: 32 hex nonce, TTL 300 sn (`services/auth.py:100-113`). Mesaj `"elevator-login:"+nonce` (`auth.py:33-38`), `/config`'te `auth.login_message_prefix`.
- `POST /auth/verify`: nonce `FOR UPDATE`, kullanılmış/süresi dolmuş kontrolü, ed25519 base64 imza (`sep10.py:177-190`), nonce yakılır, JWT (`auth.py:116-136`).
- `GET/POST /auth/sep10` (`sep10.py:69-87, 113-159`) → silinir.
- JWT (`core/security.py:27-61`): HS256, `sub`=adres, `uid`, `role`, `iss`=web_auth_domain, 7 gün. `/auth/refresh` her geçerli token'ı yeniler (revocation yok). `/auth/me`.
- Kullanıcı araması büyük/küçük harf duyarlı (`api_deps.py:47-48`, `users.py:75-76`) → EVM adresleri normalize edilmeli.
- `AuthNonce.public_key String(56)` → `address String(42)`.

### Sırlar
- Pool keys artık çalışan kodda yok (`_parked/`). `POOL_KEY_ENCRYPTION_KEY` yalnız anchor JWT'lerini şifreliyor (`models/anchor.py:19`, `anchor.py:982,997`) → anchor kalkınca gereksiz. `POOL_FUNDING_XLM` kullanılmıyor. `PLATFORM_SECRET` yalnız pubkey türetmek için (`admin_tx.py:44-54`) → `PLATFORM_ADDRESS` yeter.
- Friendbot: `config.py:143-147`, `horizon.py:347-366`, `wallet.py:422`, `e2e_testnet.py`.

### Tutar/adres varsayımları
- 7 ondalık: `amounts.py:12-16`, `xdr_utils.py:26-47`, `schemas/common.py:10-15,28` (`AmountIn decimal_places=7`), `models/asset.py:28` (decimals default 7), `trading.py:53,148`, `indexer.py:671,684,699`, `config.py:68`, 35× `Numeric(30,7)`.
- Strkey doğrulama: `sep10.py:71,108,179`, `xdr_utils.py:108`, `auth.py:41-44,178`, `admin_tx.py:34-41,59-62`, `routers/admin.py:137-139`, `services/admin.py:252,273`, `trading.py:81`, `anchor.py:263-275`, `schemas/admin.py:57,166`, `schemas/wallet.py:120,123,138`, `schemas/anchor.py:107`, `schemas/auth.py:23,35`.

### DB şeması (değişecek)
| Kolon | Bugün | Monad |
|---|---|---|
| `users.stellar_address` (`user.py:19`) | String(56) | `wallet_address` String(42) küçük harf unique |
| `auth_nonces.public_key`, `agreements.settled_by` | String(56) | String(42) |
| `assets.contract_id`, `assets.issuer`, `canonical`, `is_native`, `network` | | `address String(42)`; issuer/canonical/is_native drop; `chain_id` int |
| tüm tx hash kolonları (agreement:64-67, listing:51-52, trade:39, pending:33) | String(64) | **String(66)** |
| `trades.ledger`, `agreements.last_event_ledger`, `indexer_state.ledger` | | `block_number BigInteger` |
| `indexer_state.cursor` | RPC cursor | `last_block_hash` (reorg) |
| `trades.onchain_seq` | event sırası | `log_index` (unique (tx_hash, log_index)) |
| `pending_transactions.unsigned_xdr` | Text | `to_address`, `calldata`, `value`; `tx_hash` nullable |
| tüm `Numeric(30,7)` | 7 ondalık | `Numeric(78,18)` |
| anchor tabloları | | drop |
Migration: tek, irreversible; ayna tabloları truncate; users temizlenir (cüzdanlar değişiyor). `seed_assets.py` deploy JSON'undan.

### Testler
Stellar'a özgü: test_contract_abi, test_soroban_live, test_fake_gateways, test_anchor (737), test_auth, test_wallet, conftest (`Keypair.random()`), helpers_agreements (XDR imza), test_agreements_flow/test_indexer/test_notifications_admin fake'e bağlı. Zincirden bağımsız: test_amounts, dashboard, discover, listings, messages, offers, users.
Fake gateway deseni korunmalı: `FakeMonadGateway` aynı durum makinesi, calldata/receipt/log üretir. **Fake'te reserve/release/open_reserved/fund_reserved yok** (test dışı).

### Hatalar / güvenlik
1. Indexer hata veren olayı yutup cursor'ı ilerletiyor (`indexer.py:925-928`) → olay kaybı.
2. `/tx/submit` pending satırını `FOR UPDATE` ile 60 sn poll boyunca kilitliyor (`tx_submit.py:105,141`).
3. `/auth/refresh` sonsuz uzatma, `jti` yok (`auth.py:80-82`).
4. `/auth/nonce` rate limitsiz (`auth.py:100-113`).
5. Nonce mesajında domain/chain yok (`auth.py:33`) → SIWE çözer.
6. `cors_origins=["*"]`, `docs_enabled=True` (`config.py:31-32`).
7. Tek `X-Admin-Key` (`api_deps.py:95-97`); `/admin/contract/tx/submit` her imzalı XDR'ı relay ediyor.
8. `PLATFORM_SECRET` gereksiz sıcak secret.
9. SEP-10 hata metni sızıntısı (`sep10.py:126`).
10. `anchor.py` stellar_sdk'yı doğrudan import ediyor.
11. Monad'a özgü: reorg/onay derinliği, approve race, adres normalizasyonu.
Kalite: `.dockerignore` tests ve .bak'ı dışlamıyor; `docs/ARCHITECTURE.md` eski pool tasarımı; "Elevator", `elevator-login:`, `mobilapp` isim kalıntıları.

---

## C. Backend: iş katmanı (zincirden bağımsız kısım, ~%70)

### Domain
- `User`: tek cüzdan, tek rol (`customer`/`trader`), `is_admin`. Trader istatistikleri kullanıcı satırında.
- `Listing`: `capital` (customer: tutar, varlık, süre, `max_loss_bps`) / `service` (trader: komisyon, `min_capital`, beklenen getiri). Capital `draft` doğar, reserve indexer'dan geçince `active` (indexer.py:673). `active↔paused`, `draft|active|paused→closed`; kilitli sermaye varsa kapatma engelli (listings.py:392-419).
- `Offer`: `trader_to_customer` / `customer_to_trader`; `pending→accepted|rejected|withdrawn|expired`; kabulde `draft` Agreement + Conversation (offers.py:316-385).
- `Agreement`: `draft→proposed` (trader) veya `funded` (customer) → `active` → `settled|cancelled|failed`. Yetkiler agreements.py:247-277. Yanında `AgreementBalance`, `AgreementValueSnapshot`.
- `Trade` (indexer yazar, sadece `note` düzenlenir), `Conversation/Message`, `Rating` (sözleşme başına bir, customer verir), `Notification`, `Interaction/Follow/Favorite`, `Asset`, `PendingTransaction`, `Anchor*`.

### Endpoint grupları (A: bağımsız, H: hafif, Ağ: ağır)
Auth: sep10 GET/POST, nonce, verify [Ağ]; me, refresh [A]. Users: register, me GET/PATCH, by-username, `/users/{id}` [H stellar_address]; traders, ratings, follow [A]; `/traders/{id}/profile` [H ledger/contract_id]. Listings: CRUD, mine, mine/counts, saved, pause/resume/close [A]; `/{id}/tx/reserve|release` [Ağ]. Discover [A]. Offers [A] (listing_ref sha256 → bytes32). Agreements: list [H], `GET /{id}` [H→Ağ canlı yenileme], `/{id}/tx/{action}`, `/tx/trade`, `/quote` [Ağ], trades, value-history [H]. Ratings, Trades PATCH [A]. Conversations (7), Notifications (6), Activity [A]. Dashboard [H token_balance]. Config [Ağ], fx [H]. Market candles [Ağ Horizon]. Assets [H]. Admin: stats/users/agreements [A], assets [H], sync-onchain/indexer/contract/* [Ağ]. Tx, Wallet (4), Anchor (11) [Ağ]. Meta: health [A], health/stellar [Ağ]. Legal (6) [H metin].

### Stellar sızıntıları (iş kodu)
- Adres: models/user.py:19, api_deps.py:47-48, security.py:20,36, schemas/users.py:132, schemas/agreements.py:30.
- Tutar: amounts.py:1-16,32-53; schemas/common.py:28; 35× Numeric(30,7); listings.py:43,315; trading.py:180-255; dashboard router:37.
- Hash String(64): agreement.py:64-67, listing.py:51-52, trade.py:39, pending_transaction.py:33.
- Ledger: agreement.py:84, trade.py:40, schemas trades.py:68, users.py:257, tx.py:57, admin.py:20,124-139.
- Varlık: asset.py:25-55, schemas/assets.py:17-45, routers/admin.py:41,139-150, services/admin.py:43,273.
- XLM/USDC: agreements.py:504-555, fx.py:9,35-41, config.py:68,78, market.py:28,46-51, routers/market.py:19, trading.py:111, trade.py:54.
- Trustline/SEP: enums.py:113-114, enums.py:141-177. Zincir durum eşlemesi enums.py:96-102.
- Config: routers/config.py:33-34,89-100; meta.py:36-52. Legal: legal.py:97,107,115-117,127,153,198,327.
- Trader istatistik hesabı indexer.py:1100-1139 (ayrı servise taşınmalı).

### Güvenlik / hatalar (iş katmanı)
1. `GET /traders/{id}/profile` token istemiyor; `live_positions` müşteri adı/anapara (users.py:361-394); `recent_trades` `notify_investors=false` olanları veriyor (users.py:415-425). `GET /users/{id}` `budget_amount` açık (schemas/users.py:132,141).
2. `PATCH /listings/{id}` rezerve edilmiş capital ilanında `amount` değişebiliyor (listings.py:358-389).
3. Teklif kabulünde ilan kapanmıyor, diğer teklifler reddedilmiyor (offers.py:316-385); rezerv yetmezse sessizce cüzdana düşüyor (agreements.py:393-409).
4. Admin: statik `X-Admin-Key` (api_deps.py:95); `PATCH /admin/users` rol değiştiriyor (routers/admin.py:105-120); `is_admin` taraf yetkisi gibi (messages.py:25,164; listings.py:171).
5. Rate limit yok; `GET /agreements/{id}` her çağrıda RPC + DB yazımı (routers/agreements.py:57).
6. `avatar_url` doğrulanmıyor (schemas/users.py:84,202); `GET /listings/{id}` draft/closed herkese açık, GET view sayacı artırıyor.
7. `create_offer` yalnız `offer_id IS NULL` konuşmayı yeniden kullanıyor (offers.py:214-228).
8. JWT 7 gün, revocation yok. 9. Teklifte `max_loss_bps` 10000'e çekilebiliyor (offers.py:147-151).
Kalite: testler create_all ile, Alembic test edilmiyor; test_listings.py:70,184 muhtemelen kırık (draft değişikliği). Eksik: hesap silme API'si, engelleme/şikâyet; fx placeholder; `max_drawdown_bps` gerçek tepe-düşüş değil (indexer.py:1131).

---

## D. Frontend (`app/`, Expo SDK 57)

### Ekranlar
Gerçek API: `(auth)/login` (config, sep10, me, refresh), `(auth)/register/details` (users/register — hatalı), `(customer)/discover` (discover, action, follow), `(trader)/discover` (discover, action, offers). Yerel: onboarding, register/role. **Placeholder (15):** customer dashboard/activity/listings/profile; trader dashboard/listings/profile/trades; contract/[id], listing/[id], listing/create, messages/index, messages/[id], notifications, trader/[id], wallet.

### Stellar'a bağlı dosyalar
- `wallet/types.ts:28-46` (XDR arayüzü), `wallet/wallet.web.ts` (Wallets Kit, Freighter), `wallet/wallet.ts:29-34`, `wallet/local.ts` (Keypair, S… anahtar), `wallet/sep7.ts` (tamamı, ölü), `wallet/walletconnect.ts:30,33,79,191,194,247` (`stellar:testnet`, `stellar_signXDR`), `wallet/deeplinks.ts:30-45`.
- `auth/sep10.ts` (tamamı); `auth/jwt.ts:9` (kalabilir).
- `stellar/config.ts:10-20,23`, `stellar/clients.ts` (kullanılmıyor).
- `store/session.ts:5,153,173` (`loginWithSep10`, `stellar_address`).
- `polyfills.ts` (crypto.getRandomValues, TextEncoder, Buffer) — viem için de gerekli, kalır.
- `env.ts:11-25`, `.env.example`: STELLAR_*, CONTRACT_*_ID, ANCHOR_HOME_DOMAIN, RELAYER_ADDRESS.
- `shortAddress` (`ListingCard.tsx:28`, `details.tsx:173`; ListingCard'a UUID veriliyor). `formatAmount` `Number()` kullanıyor.
- Metinler: `login.tsx:113,124,154,160,171-180,185`, `WalletConnectSheet.tsx:46,60`, `errors.ts:3,68`, `walletconnect.ts:44`, `details.tsx:138`.

### endpoints.ts ↔ OpenAPI uyuşmazlıkları
- `POST /users/register` → `RegisterOut{user, token, expires_at}`; frontend `MeOut` sanıyor (`endpoints.ts:62`). **Kayıt döngüsü.**
- `txApi.submit` `{xdr, pending_id}` gönderiyor; şema `{pending_tx_id, signed_xdr}`. `buildTx/buildTradeTx/walletApi.build*` `{xdr,pending_id}` bekliyor; gerçek `UnsignedTxOut{unsigned_xdr, pending_tx_id,…}`.
- `notificationsApi.unreadCount` `{count}` bekliyor, gerçek `{unread, by_category}`; `conversationsApi.unreadCount` gerçek `{conversations, messages}`.
- Sayfalama: listings/traders/activity/notifications frontend cursor, API `limit/offset+total`; messages `after/before/has_more`; offers `box` (inbox/outbox/all), `role` değil.
- `markAllRead` gövdesiz; `MarkReadIn` zorunlu. `fxApi.convert` `amount/from/to`, API `amount_usd`.
- Tipler: `tradersApi.list` → `TraderCardOut`; `tradersApi.profile` → `TraderProfileOut`; `listingsApi.byId` → `ListingDetailOut`; `FollowOut.follower_count` eksik; `ConfigOut` eksik.
- 34 OpenAPI işlemi frontend'de yok (18 admin, `POST /conversations`, `PATCH /listings/{id}`, `/listings/{id}/tx/reserve|release`, `/market/candles`, `/anchor/*`, `GET /users/{id}`, `PATCH /trades/{id}`, `/health/stellar`, `GET /assets/{id}`, `GET /agreements/{id}/rating`, `GET /notifications/{id}`).

### Hatalar
1. Kayıt döngüsü: `session.ts:172-176` `profile.role` undefined → `index.tsx:20` register/role'e; `RegisterOut.token` saklanmıyor.
2. Refresh kilitlenmesi: `authApi.refresh` `auth=true` (`endpoints.ts:57`) → 401 → `client.ts:86` `authBridge.refresh()` → `refreshInFlight` kendini bekliyor (`session.ts:217`).
3. `session.ts:84` `JSON.parse` try/catch dışında. 4. `hydrate` (`session.ts:104`) `registered=false`.
5. Mobilde gizli yerel cüzdan: `details.tsx:139,186` → `signIn()` → `connectWallet()` mod verilmeden → `local` (`wallet.ts:23,67`).
6. Trader discover: teklif sheet iptal edilirse kart kayboluyor (`(trader)/discover.tsx:55-58`).
Ölü kod: `sep7.ts`, `stellar/clients.ts`, `explorer*`, `signAuthEntry`, `authApi.nonce/verifyNonce`, `formatTRY`, `requireEnv`, `env.contracts`; `wallet.ts:10` var olmayan `lib/auth/sep7.ts`'e atıf; docs `backend-sozlesme.md` yok.
Güvenlik: web'de `secureStorage` = `localStorage` (`storage.ts:13-18`); `localWallet` web'de export (`wallet/index.ts:9`) → özel anahtar localStorage'a düşebilir; yedekleme UI yok; WC `namespaces` yerine `optionalNamespaces`.
UX: sign-out yok, yerel cüzdanı unutma yok, ağ değiştirme yok.

### Monad karşılıkları
Zincir config: `viem/chains` `monadTestnet`. Web: wagmi v2 + viem (injected + WalletConnect). Mobil: mevcut `UniversalProvider`, namespace `eip155:10143`, `personal_sign`/`eth_sendTransaction`/`eth_signTypedData_v4`/`wallet_switchEthereumChain`, `optionalNamespaces` (Expo Go'da çalışır). Reown AppKit RN → native bağımlılık, dev build gerekir (ertelendi). Uygulama içi cüzdan: `generatePrivateKey`+`privateKeyToAccount`+`expo-secure-store`, web'de kapalı. Giriş SIWE (`viem/siwe` `createSiweMessage`, `personal_sign`). SEP-7 → silinir (EIP-681 yalnız ödeme QR). Hata: 4001→USER_REJECTED, 4902/zincir uyuşmazlığı→switch/addChain. Biçim: `getAddress`, `formatUnits/parseUnits`.
