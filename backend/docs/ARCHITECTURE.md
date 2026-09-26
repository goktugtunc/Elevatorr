# TraderKirala Backend — Architecture (Monad)

Short orientation for the service. The binding design lives in `../../docs/monad/`:
`01-kontrat-spec.md` (TraderVault, Solidity), `02-api-sozlesme.md` (HTTP contract),
`03-backend-tasarim.md` (this code, file by file), `06-monad-testnet.md` (network facts).

## 1. Stack

FastAPI + uvicorn, SQLAlchemy 2 (async, asyncpg) + Alembic, pydantic v2 / pydantic-settings, PyJWT (HS256),
web3.py v7 + eth-account (chain layer only), httpx (fx, Expo push), Postgres 16. Python 3.12.

## 2. Layers

```
routers/   thin HTTP handlers; pydantic in/out; dependencies from api_deps (DB, SettingsDep, CurrentUser, ChainDep, AdminGuard)
services/  business logic; raise app.core.errors.AppError subclasses (mapped to {code, message, details})
services/chain/  the ONLY package importing web3: gateway.py (ChainGateway Protocol), monad.py, fake.py, abi.py,
                 types.py (Terms, AgreementView, UnsignedTx, EventRecord, TxReceiptResult…), errors.py, addresses.py, amounts.py
models/    SQLAlchemy models (addresses stored lower-case, amounts as Numeric + raw ints where the contract needs them)
worker/    asyncio job loop (python -m app.worker.main): indexer, pending_tracker, reconciler, expiry, push, fx
```

`get_chain()` (`services/chain/__init__.py`) is a lazy singleton; tests inject `FakeChainGateway` with `set_chain()`.

## 3. Identity and auth

- Identity = EVM wallet address (`User.wallet_address`, lower-case in DB, checksummed in responses).
- SIWE (EIP-4361): `POST /auth/nonce` issues a nonce bound to the address (rate-limited per IP);
  `POST /auth/verify` checks the signature (`eth-account`), `domain`, `uri`, `chainId`, nonce TTL and issues a JWT
  (`sub` = address, `iss` = `SIWE_DOMAIN`). `POST /auth/refresh` up to `JWT_ABSOLUTE_TTL_DAYS`.
- Admin endpoints use `X-Admin-Key` (`AdminGuard`).

## 4. Transaction model (wallet signs, server never does)

1. A build endpoint (`POST /agreements/{id}/tx/{action}`, `/tx/trade`, `/listings/{id}/tx/reserve|release`,
   `/wallet/tx/transfer`, admin `/admin/contract/tx/{fn}`) encodes calldata with the ABI, runs `eth_estimateGas` (x1.2)
   and stores a `PendingTransaction` (`to, calldata, value, gas, action, from_address`, status `pending`,
   TTL `PENDING_TX_TTL_SECONDS`). ERC-20 `approve` needs are returned as `pre_steps`.
2. The client signs and broadcasts, then calls `POST /tx/submit {pending_id, tx_hash}`; the service waits up to
   `TX_SUBMIT_TIMEOUT_SECONDS` for the receipt, decodes the logs and applies them through the same handlers the
   indexer uses (`apply_tx_result`). Reverts are explained via `eth_call` replay -> custom-error selector -> `error_code`.
3. Not yet included -> status `submitted`; the worker `pending_tracker` job polls receipts and expires stale rows.

## 5. Indexer and reconciler

- `run_indexer_once`: from `IndexerState.block_number` to `latest - CONFIRMATIONS`, in `INDEXER_BLOCK_WINDOW` chunks
  (max `INDEXER_MAX_WINDOWS_PER_RUN` per tick); `eth_getLogs` on the vault; each event -> handler
  (AgreementOpened/Funded/Accepted/Cancelled, Traded, Settled, Claimed, Reserved/Released, config events).
  Handler failures are recorded in `failed_events` instead of being swallowed; block-hash mismatch -> reorg rewind.
- `run_reconcile_once`: re-reads `getAgreement`/`getBalances`/`valueInBase` for active agreements, snapshots value
  history and raises drawdown alerts.
- Trader statistics are derived in `services/trader_stats.py`.

## 6. Assets and prices

`assets` = allow-listed ERC-20s per `chain_id` (`symbol`, `address`, `decimals`, `is_base_allowed`, `onchain_allowed`);
seeded from `deployments/monad-testnet.json` (`scripts/seed_assets.py`) and mirrored from the vault by
`POST /admin/assets/sync-onchain`. MON is never an asset row (`is_native` is always false). Indicative USD prices come
from `USD_PRICES_JSON` (MockRouter deploy prices); USD->TRY from external fx sources with DB fallback.

## 7. Public config and health

- `GET /api/v1/config` (`routers/config.py`): chain params, contract addresses (from settings), assets, SIWE params,
  limits, live vault `getConfig()` (60 s cache; `contract_error` when unreachable).
- `GET /health` (DB), `GET /health/chain` (RPC `eth_chainId` + latest block; 503 on mismatch/failure).

## 8. Deployment

Docker compose (`db`, `api` on `127.0.0.1:8013`, `worker`), nginx site `deploy/nginx/monadback.yolalapp.com`.
`docker/entrypoint.sh`: wait for DB -> `alembic upgrade head` -> `scripts.seed_assets` -> uvicorn (or worker).
Contract deploy outputs in `deployments/*.json` are committed.

## 9. Tests

`pytest` with `FakeChainGateway` (no RPC); `-m chain` tests run against `anvil --chain-id 10143` with the Foundry
deploy script. Fixtures in `tests/conftest.py`; sample deploy JSON in `tests/fixtures/deployment.fake.json`.
