# TraderKirala API — backend (Monad)

Non-custodial "rent a trader" marketplace: capital owners (customers) lock ERC-20 capital in the
`TraderVault` contract on **Monad Testnet**; a trader can only swap it inside the vault (through an
allow-listed router) and can never withdraw it. This FastAPI service is the off-chain half: profiles,
listings, offers, messaging, unsigned-transaction building, receipt tracking and an event indexer.

Design documents (binding): `../docs/monad/` — `01-kontrat-spec.md` (contract), `02-api-sozlesme.md`
(API contract), `03-backend-tasarim.md` (this service), `06-monad-testnet.md` (network facts).

## Architecture

```
mobile / web client ──HTTPS──▶ nginx (monadback.yolalapp.com) ──▶ api (uvicorn, FastAPI)
        │  signs tx locally                                          │  SQLAlchemy async ──▶ Postgres 16
        ▼                                                            │  web3.py (eth_call, estimateGas, getLogs)
   wallet (MetaMask / WalletConnect) ──eth_sendRawTransaction──▶ Monad RPC ◀── worker (indexer, pending
                                                                                 tracker, reconciler, fx, push)
```

- **Wallet-signed everything.** The API never holds a user key. `POST …/tx/*` endpoints return an
  `UnsignedTxOut` (`to, data, value, gas, chain_id, pre_steps`); the client signs and broadcasts, then
  reports the hash to `POST /tx/submit`, which waits for the receipt and applies the emitted events.
- **Auth = SIWE (EIP-4361).** `POST /auth/nonce` -> wallet signs the message -> `POST /auth/verify` -> JWT.
- **Chain layer** `app/services/chain/` is the only place that imports web3: `gateway.py` (Protocol),
  `monad.py` (`MonadGateway`), `fake.py` (`FakeChainGateway` for tests), `abi.py`, `types.py`, `errors.py`.
- **Indexer** (`app/services/indexer.py`, worker job) reads vault logs in block windows behind
  `CONFIRMATIONS`, decodes them with the ABI and mirrors agreement / trade / reservation state into
  Postgres; the reconciler re-reads contract views periodically.
- **Faucet** (`POST /wallet/faucet`) mints test tokens with a low-privilege `MINTER_ROLE` key (testnet only).

### Repository layout

```
app/
  main.py            FastAPI factory, ROUTER_MODULES, error mapping
  core/              config (Settings), security (JWT), errors, ratelimit, logging
  api_deps.py        DB / Settings / CurrentUser / ChainDep / AdminGuard
  routers/           one file per resource (config, meta, legal, auth, users, assets, admin, listings,
                     discover, offers, conversations, dashboard, ratings, agreements, tx, trades, activity, wallet)
  services/          business logic; chain/ = Monad gateway package
  schemas/           pydantic I/O models (02-api-sozlesme)
  models/            SQLAlchemy models; alembic/ holds migrations
  worker/main.py     background jobs (indexer, pending_tracker, reconciler, expiry, push, fx)
scripts/             seed_assets.py (from deployments JSON), gen_env.py, dev.sh, backup.sh
deployments/         monad-testnet.json (deploy output; COMMITTED — source of truth for addresses)
deploy/nginx/        monadback.yolalapp.com site config + Cloudflare real-ip include
docker/              entrypoint.sh (migrate + seed + uvicorn / worker), initdb/
tests/               pytest (FakeChainGateway by default; `-m chain` needs anvil)
```

The Solidity contracts (Foundry) live in the repo root `../contracts/`; `make abi` there regenerates
`app/services/chain/abi/*.json`.

## Setup

### Requirements

Python 3.12, Postgres 16 (or Docker), a Monad Testnet RPC (`https://testnet-rpc.monad.xyz`), and a
deployed vault (`../contracts`, `forge script script/Deploy.s.sol --rpc-url monad_testnet --broadcast`).

### Environment

Copy `.env.example` to `.env`; `python3 scripts/gen_env.py --domain monadback.yolalapp.com > .env`
generates the secrets. Key variables (`app/core/config.py`, env name = upper-cased field):

| Variable | Default | Meaning |
|---|---|---|
| `CHAIN_ID`, `CHAIN_NAME` | `10143`, `Monad Testnet` | `/health/chain` fails with `chain_id_mismatch` if the RPC disagrees |
| `RPC_URL`, `WS_URL`, `EXPLORER_URL`, `FAUCET_URL` | testnet defaults (06-monad-testnet.md) | published in `GET /config.chain` |
| `VAULT_ADDRESS`, `ROUTER_ADDRESS` | – | proxy + MockRouter from `deployments/monad-testnet.json`; unset -> build endpoints 503 `contract_not_configured` |
| `PLATFORM_ADDRESS` | – | fee recipient default for admin `set_fees` |
| `DEPLOYMENTS_FILE` / `ASSETS_JSON` | `deployments/monad-testnet.json` | token allow-list source for `scripts/seed_assets.py` |
| `DEFAULT_BASE_ASSET_CODE` | `tUSDC` | symbol |
| `CONFIRMATIONS`, `INDEXER_BLOCK_WINDOW`, `INDEXER_START_BLOCK` | `2`, `2000`, deploy block | indexer safety depth / `eth_getLogs` window |
| `MINTER_PRIVATE_KEY`, `FAUCET_AMOUNTS`, `FAUCET_DAILY_LIMIT` | –, `{"tUSDC":"1000",…}`, `1` | testnet faucet; empty key -> `/wallet/faucet` 503 `faucet_disabled` |
| `SIWE_DOMAIN`, `SIWE_URI`, `SIWE_STATEMENT` | `monadback.yolalapp.com`, … | SIWE message fields; `SIWE_DOMAIN` is also the JWT `iss` |
| `JWT_SECRET`, `ADMIN_KEY` | – | never logged |
| `PENDING_TX_TTL_SECONDS`, `TX_SUBMIT_TIMEOUT_SECONDS`, `SUBMITTED_TX_TIMEOUT_MINUTES` | `900`, `20`, `30` | unsigned tx validity / receipt wait / `submitted` -> `failed(not_included)` |
| `INDEXER_POLL_SECONDS`, `PENDING_TRACKER_SECONDS`, `RECONCILE_SECONDS`, `FX_CACHE_SECONDS` | `5`, `5`, `60`, `600` | worker intervals |
| `USD_PRICES_JSON`, `MON_USD_PRICE` | MockRouter prices, `null` | indicative USD prices for TL display |
| `LEGAL_CONTACT_EMAIL` | – | shown on `/legal/*` (Google Play requires it) |
| `EXPO_PUSH_ENABLED`, `EXPO_ACCESS_TOKEN` | `false` | push notifications |

### Server (Docker)

```bash
sudo docker compose build api
sudo docker compose up -d db api worker   # api entrypoint: alembic upgrade head + scripts.seed_assets, then uvicorn
curl -s localhost:8013/health/chain
curl -s localhost:8013/api/v1/config | jq '.contracts, .assets[].symbol'
```

nginx (`deploy/nginx/monadback.yolalapp.com`) terminates TLS and proxies `127.0.0.1:8013`.

### Local development

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
# Postgres on 127.0.0.1:5434 (user/password trader, DBs traderkirala + traderkirala_test), see 03-backend-tasarim §8.4
.venv/bin/alembic upgrade head
.venv/bin/python -m scripts.seed_assets            # reads DEPLOYMENTS_FILE / ASSETS_JSON
.venv/bin/uvicorn app.main:app --reload --port 8000
.venv/bin/python -m app.worker.main --list         # job table; `--once indexer fx` runs single ticks
```

`scripts/dev.sh py|ruff|alembic|test …` runs the same inside the API image.

### Deploying / upgrading the contract

1. `cd ../contracts && make setup build test`, then `forge script script/Deploy.s.sol:Deploy --rpc-url monad_testnet --broadcast --slow`
   (deployer key + `FEE_RECIPIENT`, `MINTER_ADDRESS` in env; see 01-kontrat-spec §9).
2. `scripts/deployments-from-broadcast.sh` fills `deployments/monad-testnet.json`; copy it to
   `backend/deployments/` and commit it; `make abi` refreshes the backend ABI JSON files.
3. Set `VAULT_ADDRESS` / `ROUTER_ADDRESS` in `.env`, `docker compose up -d --force-recreate api worker`.
4. `POST /api/v1/admin/assets/sync-onchain` (header `X-Admin-Key`) mirrors the vault allow-list into `assets`.

## Testing

```bash
.venv/bin/python -m pytest -q                 # FakeChainGateway, no RPC
.venv/bin/ruff check app tests scripts
ANVIL_RPC_URL=http://127.0.0.1:8545 .venv/bin/python -m pytest -q -m chain   # against anvil --chain-id 10143 + Deploy.s.sol
```

## Endpoints at a glance

`GET /health`, `GET /health/chain`, `GET /legal/*` (root); under `/api/v1`: `config`, `fx`, `auth/{nonce,verify,refresh,me}`,
`users`, `assets`, `listings`, `discover`, `offers`, `conversations`, `notifications`, `dashboard`, `ratings`,
`agreements/{id}/tx/{action}`, `tx/submit`, `tx/{pending_id}`, `trades`, `activity`, `wallet` (`/wallet`, `/wallet/deposit-info`,
`/wallet/tx/transfer`, `/wallet/faucet`, `/wallet/transactions`), `admin/*` (`X-Admin-Key`). Full contract: `../docs/monad/02-api-sozlesme.md`;
OpenAPI at `/docs` when `DOCS_ENABLED=true`.
