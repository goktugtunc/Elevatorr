# Tasarım Sistemi ↔ Kod Eşlemesi

Kaynak: Figma `6ZxzvsYKarDglg0PGYRIgP` → sayfa **Design System** (node `0:1`). Kod: `app/src/theme/` ve `app/src/components/`.

## Renkler (`theme/colors.ts`)

| Figma swatch | Token | Hex |
|---|---|---|
| Navy 900 | `navy900` | #0B1F3A |
| Navy 700 | `navy700` | #16305C |
| Navy 050 | `navy050` | #E9EDF3 |
| Kâr Yeşili | `profit` | #16A34A |
| Zarar Kırmızısı | `loss` | #DC2626 |
| Vurgu Amber | `amber` | #F59E0B |
| Green BG / Red BG / Amber BG | `greenBg` / `redBg` / `amberBg` | #DCFCE7 / #FEE2E2 / #FEF3C7 |
| Amber Ink | `amberInk` | #92400E |
| Bg / Surface / Surface Alt / Surface Sunken | `bg` / `surface` / `surfaceAlt` / `surfaceSunken` | #F3F5F9 / #FFFFFF / #F1F3F8 / #EAEDF3 |
| Border / Border Strong | `border` / `borderStrong` | #E2E6ED / #CDD3DE |
| Text / Text 2 / Text 3 | `text` / `text2` / `text3` | #0F172A / #5B6472 / #94A0AF |

`pnlColor(value)` işaretli değerlere (K/Z) otomatik yeşil/kırmızı verir.

## Tipografi (`theme/typography.ts`) — Inter, tabular numerik

| Figma stili | Token | Boyut / ağırlık |
|---|---|---|
| Display / Extra Bold 28 | `display` | 28 / 800 |
| Heading H1 / Bold 22 | `h1` | 22 / 700 |
| Heading H2 / Semi Bold 18 | `h2` | 18 / 600 |
| Body / Regular 15 | `body`, `bodyStrong` | 15 / 400, 600 |
| Numeric / Bold 24 | `numeric`, `numericSm` | 24 / 700 (tabular), 15 / 700 |
| Caption / Regular 12 | `caption`, `captionStrong` | 12 / 400, 600 |

Fontlar `@expo-google-fonts/inter` ile `app/_layout.tsx` içinde yüklenir. Sayılar `fontVariant: ['tabular-nums']` ile hizalanır.

## Boşluk & radius (`theme/spacing.ts`)

Spacing: 4 · 8 · 12 · 16 · 20 · 24 · 32 · 40 (`xs … 4xl`). Radius: sm 8 · md 12 · lg 16 · full 999. Ekran çerçevesi 390 × 844; web'de `layout.maxContentWidth = 480` ile ortalanır.

## Bileşenler (`components/ui`, `components/layout`)

| Figma bileşeni | Kod | Notlar |
|---|---|---|
| Button (variant × size) | `<Button variant size />` | primary / secondary / ghost / danger · md 48px / sm 36px |
| Chip (active) | `<Chip active />` | seçilebilir filtre |
| Pill | `<Pill tone />` | tıklanmaz etiket |
| Risk Badge (low/mid/high) | `<RiskBadge level />` | Muhafazakâr / Dengeli / Agresif |
| Status Chip | `<StatusChip status />` | Teklif / Onay Bekliyor / Aktif / Tamamlandı / İptal |
| KPI Box | `<KpiBox />`, `<Stat />` | signed prop ile renk |
| Avatar 56/44/32 | `<Avatar size />` + `initialsOf()` | doğrulama işareti yok |
| Segmented | `<Segmented options value onChange />` | generic |
| Field / Input | `<Field label suffix multiline />` | odak/hata kenarlığı |
| List Row | `<ListRow />` | avatar + başlık + değer/meta |
| Tile / İlan Kartı | `<Card raised />` | |
| Tab Bar/Müşteri, Tab Bar/Trader | `<TabBar />` (expo-router `tabBar` prop) | ortada yükseltilmiş Keşfet |
| Risk Strip | `<RiskStrip />` | her ekranda zorunlu; `Screen`/`TabBar` çizer |
| Top Bar / Header | `<TopBar />`, `<ScreenHeader />` | |

İkonlar: `lucide-react-native` — Figma'daki `icon/*` isimleriyle eşleşir (compass, grid → LayoutGrid, briefcase, user, trending → TrendingUp, swap → ArrowLeftRight, bell, wallet, message, alert → AlertTriangle, chevronRight, arrowLeft, check, star, x, heart, upRight, downRight, users, checkCircle, sliders, more, copy, shield, lock, logout).

## Monad'a uyarlanan tasarım noktaları

- **1d Giriş:** Figma'daki MetaMask / WalletConnect / Coinbase / Trust listesi → `ConnectWalletPanel`:
  web'de tarayıcı cüzdanı (MetaMask, Rabby; EIP-6963 keşfi) + WalletConnect, mobilde WalletConnect
  (MetaMask, Rainbow, Trust) + uygulama içi cüzdan. Adresler `shortAddress('0x…')` → "0x67aD…FF19".
- **Yanlış ağ:** `ChainBanner` (amber) — cüzdan Monad Testnet (10143) dışındaysa `Switch to Monad Testnet`.
- **Cüzdan bakiyesi:** MON (gas) + allow-list ERC-20 bakiyeleri `GET /wallet`'tan; tutarlar string,
  `formatAmount` / `formatRaw` (`@/lib/chain`). Faucet kartı: MON bağlantısı + test token mint.
- **9d Sözleşme onayı:** "Slide to confirm" (`SlideToConfirm`) → `POST …/tx/{action}` calldata →
  cüzdan imzası → `POST /tx/submit`; fazlar `TxProgressSheet` ile (Preparing → Confirm → Sent → Confirmed),
  hash `ExplorerLink` ile testnet.monadvision.com'a.
- **Bildirimler:** "Sözleşmen aktifleşti" vault kontrat event'inden (indexer); işlem durumları
  `GET /tx/{pending_id}` takibinden gelir.
