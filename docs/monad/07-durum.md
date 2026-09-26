# Monad geçişi — durum ve devam notu (26 Eyl 2026, 16:30)

Bu dosya, uygulama workflow'u (`monad-implement`, run `wf_f39f56c9-d84`) kullanıcı isteğiyle **erken durdurulduğunda** kaldığı yeri
ve bundan sonra yapılması gerekenleri kaydeder. Devam eden kişi/ajan önce bunu okur.

## 1. Bugüne kadar bitenler (commit'li)

| Ne | Nerede | Commit |
|---|---|---|
| Sunucudaki Stellar backend'i dondurma (`git init` + commit + tag `v1-stellar`) | `ssh yolal:/home/mkati/mobilapp` (commit `10214e2`) | sunucu reposu |
| Backend Stellar sürümü monorepo'ya alındı (`backend/`) | `bbfe568` | ✅ |
| Sprint planı | `SPRINT-2-MONAD.md` | ✅ |
| İnceleme + tasarım dokümanları (00–04, 06) | `docs/monad/` | `f5291e3` |
| Monad deployer cüzdanı | `0x67aD0CaE18528017B79E0aF8C4D3dd5937caFF19`, anahtar `~/.config/traderkirala/monad-testnet-deployer.env` | — (bakiye 0 MON; kullanıcı yükleyecek) |
| Yerel araçlar | Foundry 1.8.3 (`~/.foundry/bin`), uv + Python 3.12, `backend/.venv` (Stellar bağımlılıklarıyla kurulu), Postgres docker `traderkirala-pg` (127.0.0.1:5434, trader/trader, db `traderkirala` + `traderkirala_test`), `app/node_modules` | — |

## 2. Durdurulan workflow'un bıraktığı kısmi işler (bu commit'te WIP olarak var)

| İz | Paket | Durum | Kalan |
|---|---|---|---|
| Kontrat | SC1 | `contracts/lib/` (forge-std, OZ contracts + upgradeable, `--no-git`) kuruldu; `src/{interfaces,libraries,mocks}`, `script/`, `scripts/`, `test/`, `deployments/` boş dizinler; eski `contracts/README.md` silindi | **foundry.toml, remappings, tüm .sol dosyaları, export-abi.sh, README, Makefile** — spec `01-kontrat-spec.md` §1–§9 |
| Backend | Dalga 0 | `app/core/errors.py` (`StellarError→ChainError`, `RateLimitedError`), `requirements.txt` (stellar-sdk çıktı; web3/eth-account/eth-utils/hexbytes girdi), `app/services/chain/__init__.py` (get_chain/set_chain) yazıldı | `chain/types.py, errors.py, addresses.py, amounts.py`, `app/services/amounts.py` köprüsü, `schemas/common.py`, `requirements-dev.txt`, `pyproject.toml` marker; **`.venv`'e yeni bağımlılıkların kurulması** (`uv pip install --python .venv/bin/python -r requirements-dev.txt`) — 03 §11.1 |
| Frontend | Dalga 1(a) | `app/package.json`: stellar paketleri çıkarıldı, `viem@^2.56.9`, `wagmi@^2.19.5` eklendi; `npm install` yapıldı (package-lock güncel) | `lib/chain/**`, `lib/wallet/**` yeniden yazımı, `lib/stellar/` ve `wallet/sep7.ts` silme, env.ts, format.ts, storage.ts, polyfills — 04 §9 (a) |

Not: `app/src/lib/stellar/`, `app/src/lib/wallet/*` (Stellar), `app/src/lib/auth/sep10.ts`, `backend/app/services/stellar/`, `backend/contracts/` (Rust) **hâlâ duruyor**; tip kontrolü şu an stellar-sdk kaldırıldığı için kırıktır (beklenen).

## 3. Yapılması gerekenler (sıra ve dosya sahipliği spec'lerde)

Uygulama planı ve dosya sahipliği: `03-backend-tasarim.md` §11, `04-frontend-tasarim.md` §9, `01-kontrat-spec.md` Ek B.

1. **Kontrat** — SC1 (Foundry proje + tüm kontratlar derlenir + ABI export `backend/app/services/chain/abi/*.json` ve `app/src/lib/chain/abi/*.ts`) → SC2 (Foundry testleri, `forge test` yeşil) ∥ SC3 (`script/Deploy.s.sol`, anvil provası, `deployments/` şeması). Testnet'e gönderim **MON gelince**.
2. **Backend** — Dalga 0 tamamla → Dalga 1 paralel: (a) `chain/gateway.py, abi.py, monad.py, fake.py` + `tests/chain/*` (ABI'ye bağımlı → SC1 sonrası), (b) settings + modeller + Alembic `monad_cutover` + seed + env + Docker/compose/nginx (`backend/.env` yerel dosyası da), (c) SIWE auth, (d) Stellar/anchor/market silme + config/meta/legal/README → Dalga 2 paralel: (e) tx_submit/agreements/trading/listings/offers/admin, (f) indexer/trader_stats/worker, (g) wallet/dashboard/users/fx → Dalga 3: (h) testler `pytest` yeşil (alembic upgrade head yerel DB'de), (i) ruff.
3. **Frontend** — Dalga 1 paralel: (a) chain+wallet+env, (b) api tipleri/endpoints/session/siwe → Dalga 2 paralel: (c) login/register ekranları, (d) tx UI + contract/[id] + listing create/detay, (e) wallet + dashboard + listings + profile → Dalga 3: tsc/lint/export temiz, Stellar grep boş, docs/*.md Monad.
4. **Kapanış** — kök README (Mermaid), `.gitignore`, SPRINT-2 durum sütunu, DoD grep'leri.
5. **BEKLİYOR (MON gerekli):** `contracts/scripts/deploy-testnet.sh` → `deployments/monad-testnet.json` → backend `.env` VAULT/ROUTER + `seed_assets` → sunucuda `traderkirala-monad` compose (port 8013) + nginx `monadback.yolalapp.com` → frontend `.env` → e2e (`scripts/e2e_testnet.py`) → cutover.

## 4. Devam etme yolu

- Aynı workflow scripti: `~/.claude/projects/-Users-goktugtunc-Desktop-trader/17b2a1ee-778f-430b-a315-799629abfb58/workflows/scripts/monad-implement-wf_f39f56c9-d84.js` (run `wf_f39f56c9-d84`). Yarım kalan ajanlar cache'lenmedi; scripte kısmi işi bildiren bir not eklenip yeniden koşturulabilir, ya da düşük effort'la paket paket tek ajanla ilerlenebilir.
- Düşük effort'la ilerlerken önerilen sıra: SC1 → (BE Dalga 0 + FE 1a + FE 1b paralel) → BE 1(a–d) → SC2 → BE 2 → FE 2 → BE 3 → FE 3 → kapanış.
- Doğrulama komutları: `cd contracts && forge build && forge test`; `cd backend && .venv/bin/pytest -q`; `cd app && npx tsc --noEmit && npm run lint && npm run export:web`.
