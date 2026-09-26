# Mobil test (Expo Go + iOS simülatörü) — Monad Testnet

Uygulama Expo Go ile çalışacak şekilde yazıldı: özel native modül yok
(rastgelelik `expo-crypto`, güvenli depo `expo-secure-store`, pano `expo-clipboard`,
QR `react-native-qrcode-svg`; zincir erişimi `viem`, cüzdan `@walletconnect/universal-provider`).
Bu yüzden development build gerekmez. Ağ **Monad Testnet (chain 10143)**; ayrıntılar
[monad/06-monad-testnet.md](monad/06-monad-testnet.md).

## 0. Ön koşullar

| Gereken | Neden | Not |
|---|---|---|
| Çalışan backend | SIWE girişi, `/config`, ilanlar, işlem inşası | `EXPO_PUBLIC_API_BASE_URL` (yerelde `http://<Mac-IP>:8013`; `localhost` telefondan erişilemez) |
| Test MON | Gas; her zincir işlemi için | <https://faucet.monad.xyz> — cüzdan adresine gönderir |
| `EXPO_PUBLIC_WALLETCONNECT_PROJECT_ID` | Yalnızca harici cüzdan (MetaMask mobil vb.) yolu için; boşsa o seçenek görünmez | `app/.env` (git'e girmez) |
| Expo Go (telefonda) | Uygulamayı derlemeden çalıştırır | App Store / Play Store |
| Xcode | Yalnızca simülatör için; Expo Go yolu Xcode istemez | isteğe bağlı |

```bash
cd app && cp .env.example .env     # ilk kez
grep -E "API_BASE_URL|WALLETCONNECT" app/.env
```

## 1. Telefonda Expo Go ile açmak (birincil yol)

```bash
cd app
npm install
npx expo start            # terminalde QR çıkar
```

- Telefon ve Mac **aynı Wi-Fi ağında** olmalı. Değilse: `npx expo start --tunnel`.
- iOS: Kamera uygulamasıyla QR'ı okut → Expo Go açılır. Android: Expo Go → "Scan QR code".
- Elle adres: `exp://<Mac-IP>:8081`.

Beklenen: Onboarding → **Welcome** ekranı. Pill `Network: Monad Testnet`; sunucu satırı
`Server connected · <siwe_domain>` olmalı. `Server is on chain …` uyarısı çıkarsa backend
başka bir zincire ayarlıdır; `Server unreachable` ise `EXPO_PUBLIC_API_BASE_URL` yanlıştır.

## 2. Uygulama içi cüzdan (en hızlı yol)

Giriş ekranında **Use in-app wallet**: anahtar cihazda üretilir, keychain'de kalır, imza
cihazda atılır; hiçbir dış uygulama gerekmez. Elinde bir testnet gizli anahtarı varsa
**Import a private key** (0x + 64 hex).

1. Bağlanınca **Sign in with Ethereum** mesajı cihazda imzalanır (ücretsiz, fon taşımaz).
2. Kayıtlı değilse kayıt ekranına düşersin; rol seç, formu doldur.
3. **Wallet** ekranında adresi kopyala → <https://faucet.monad.xyz> → MON iste.
4. Aynı ekranda **test token faucet** (`POST /wallet/faucet`, sunucu mint eder; oran sınırı
   `next_allowed_at`) ile tUSDC benzeri test varlığı al.
5. Artık ilan aç / teklif kabul et / sözleşme aksiyonlarında `TxProgressSheet` akışını
   (Preparing → Confirm → Sent → Confirmed) görebilirsin. Explorer bağlantısı
   <https://testnet.monadvision.com>'a gider.

Sonraki açılışlarda düğme `Continue with in-app wallet · 0x…` olur. Anahtar silme:
Wallet → in-app wallet güvenlik kartı → Forget.

## 3. MetaMask mobil (WalletConnect)

`EXPO_PUBLIC_WALLETCONNECT_PROJECT_ID` doluyken giriş ekranında **Connect wallet**
(yardım: "Opens MetaMask, Rainbow or Trust over WalletConnect") görünür.

1. Telefona MetaMask (ya da Rainbow / Trust) kur; Monad Testnet'i ekle — uygulama
   `wallet_addEthereumChain` ile önerir, elle: chainId `10143` (`0x279f`), RPC
   `https://testnet-rpc.monad.xyz`, sembol `MON`, explorer `https://testnet.monadvision.com`.
2. Expo Go'da **Connect wallet** → sheet açılır: **Open MetaMask** (deep link) ya da
   QR (aynı cihazdaysa ikinci telefondan okut).
3. Cüzdanda bağlantıyı onayla; ardından SIWE imza isteği düşer → **Sign**.
4. Cüzdan yanlış ağdaysa uygulama üstte **ChainBanner** gösterir (`Switch to Monad Testnet`).
5. Zincir işlemlerinde her `Confirm the transaction in your wallet` adımında MetaMask'e geç.

**Simülatörde** MetaMask kurulamaz; QR yolu ile gerçek telefondaki MetaMask'ten
okutmak çalışır. iOS'ta uygulamadan cüzdana geçiş sonrası **Expo Go'ya elle dönmek**
gerekebilir.

### WalletConnect proje kimliği

1. <https://dashboard.reown.com> → Create project (AppKit, React Native) → **Project ID**.
2. `app/.env` → `EXPO_PUBLIC_WALLETCONNECT_PROJECT_ID=<id>` → `npx expo start --clear`.
3. **Allowlist tuzağı:** Expo Go kendi bundle id'siyle (`host.exp.Exponent`) çalışır;
   test aşamasında allowlist **boş** kalmalı. Derlenmiş uygulamada `com.traderkirala.app` eklenir.

## 4. Xcode + simülatör (isteğe bağlı)

```bash
sudo xcode-select -s /Applications/Xcode.app/Contents/Developer
sudo xcodebuild -license accept
xcodebuild -downloadPlatform iOS
cd app && npx expo start --ios       # Expo Go'yu simülatöre kurar ve açar
```

Simülatörde uygulama içi cüzdan tam çalışır (keychain simüle edilir).

## 5. Yerel zincir (backend testleri ile aynı düzen)

Testnet MON yoksa `anvil --chain-id 10143` + backend'in `FakeMonadGateway`/anvil
ayarı ile çalışılabilir: `EXPO_PUBLIC_RPC_URL=http://<Mac-IP>:8545`. `/config`
alınabildiği sürece sunucunun RPC değeri öne geçer (bkz. `app/.env.example`).

## 6. Sık karşılaşılan hatalar

- **`crypto.getRandomValues must be defined`** — polyfill sırası: giriş noktası `app/index.js`
  önce `./src/polyfills`, sonra `expo-router/entry`. Değiştirilmemeli; `npx expo start --clear`.
- **`Not enough MON to pay for gas`** — faucet'ten MON iste; bakiye Wallet ekranında.
- **`Your wallet is on another network`** — ChainBanner'daki Switch'e bas ya da cüzdanda
  Monad Testnet'i seç.
- **`The sign-in message was issued by another domain`** — backend `SIWE_DOMAIN` ile
  `EXPO_PUBLIC_API_BASE_URL` uyuşmuyor.
- **`Server is on chain …`** — backend `.env`'de `CHAIN_ID=10143` olmalı.
- **WalletConnect "pairing expired"** — sheet'i kapatıp yeniden **Connect wallet**; relay
  URI'si kısa ömürlüdür.

## 7. Bilinen sınırlar

- Expo Go'da uygulamanın kendi scheme'i (`traderkirala://`) kayıtlı değildir; WalletConnect
  dönüş adresi `Linking.createURL('/')` ile üretilir (`exp://…/--/`).
- Expo Go'ya yeni bir native bağımlılık eklenirse (ör. `react-native-quick-crypto`) bu akış
  kırılır ve development build gerekir.
- Uygulama içi cüzdan **yalnız testnet** içindir; anahtar cihazda saklanır, yedeklenmez.
