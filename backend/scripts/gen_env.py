"""Generate a complete .env with fresh secrets (run once on a new deployment).

Usage:
    python scripts/gen_env.py [--domain monadback.yolalapp.com] [--platform-address 0x…]
                              [--vault 0x…] [--router 0x…] [--chain-id 10143] > .env

Never overwrites an existing .env by itself: it prints to stdout. The MINTER_PRIVATE_KEY is a brand-new key;
its address is written as a comment — it must be granted `MINTER_ROLE` on every TestToken before the faucet works.
"""
from __future__ import annotations

import argparse
import secrets

from eth_account import Account

DEFAULT_PLATFORM_ADDRESS = "0x67aD0CaE18528017B79E0aF8C4D3dd5937caFF19"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--domain", default="monadback.yolalapp.com")
    p.add_argument("--platform-address", default=DEFAULT_PLATFORM_ADDRESS)
    p.add_argument("--vault", default=ZERO_ADDRESS, help="TraderVault proxy address")
    p.add_argument("--router", default=ZERO_ADDRESS, help="MockRouter address")
    p.add_argument("--chain-id", type=int, default=10143)
    p.add_argument("--rpc-url", default="https://testnet-rpc.monad.xyz")
    p.add_argument("--explorer-url", default="https://testnet.monadvision.com")
    a = p.parse_args()

    minter = Account.create()
    lines = [
        "APP_ENV=prod",
        "LOG_LEVEL=INFO",
        "DOCS_ENABLED=false",
        "CORS_ORIGINS=*",
        "LEGAL_CONTACT_EMAIL=",
        "",
        f"POSTGRES_PASSWORD={secrets.token_urlsafe(32)}",
        "",
        f"JWT_SECRET={secrets.token_urlsafe(48)}",
        f"ADMIN_KEY={secrets.token_urlsafe(32)}",
        "",
        "# ---- chain ----",
        f"CHAIN_ID={a.chain_id}",
        "CHAIN_NAME=Monad Testnet",
        f"RPC_URL={a.rpc_url}",
        "WS_URL=",
        f"EXPLORER_URL={a.explorer_url}",
        "FAUCET_URL=https://faucet.monad.xyz",
        "RPC_MAX_RPS=12",
        "RPC_CALL_MAX_RPS=8",
        "",
        "# ---- contracts (deployments/monad-testnet.json is the source of truth; these must match) ----",
        f"VAULT_ADDRESS={a.vault}",
        f"ROUTER_ADDRESS={a.router}",
        f"PLATFORM_ADDRESS={a.platform_address}",
        "DEFAULT_BASE_ASSET_CODE=tUSDC",
        "CONFIRMATIONS=2",
        "INDEXER_BLOCK_WINDOW=2000",
        "INDEXER_START_BLOCK=",
        "DEPLOYMENTS_FILE=deployments/monad-testnet.json",
        "",
        "# ---- faucet: grant MINTER_ROLE on each TestToken to this address ----",
        f"# MINTER address: {minter.address}",
        f"MINTER_PRIVATE_KEY={minter.key.hex()}",
        'FAUCET_AMOUNTS={"tUSDC":"1000","tWETH":"0.5","tWBTC":"0.02"}',
        "FAUCET_DAILY_LIMIT=1",
        "",
        "# ---- auth (SIWE) ----",
        f"SIWE_DOMAIN={a.domain}",
        f"SIWE_URI=https://{a.domain}",
        "SIWE_STATEMENT=TraderKirala'ya giriş yap. Bu imza işlem yapmaz, gas harcamaz.",
        "AUTH_NONCE_TTL_SECONDS=300",
        "AUTH_NONCE_RATE_LIMIT_PER_MINUTE=20",
        "ACCESS_TOKEN_TTL_SECONDS=604800",
        "JWT_ABSOLUTE_TTL_DAYS=30",
        "",
        "# ---- transactions ----",
        "PENDING_TX_TTL_SECONDS=900",
        "TX_SUBMIT_TIMEOUT_SECONDS=20",
        "SUBMITTED_TX_TIMEOUT_MINUTES=30",
        "SETTLE_SLIPPAGE_BPS=100",
        "DEFAULT_TRADE_SLIPPAGE_BPS=100",
        "",
        "# ---- worker intervals (seconds) ----",
        "INDEXER_POLL_SECONDS=5",
        "PENDING_TRACKER_SECONDS=5",
        "RECONCILE_SECONDS=60",
        "WORKER_OFFER_EXPIRY_SECONDS=60",
        "FX_CACHE_SECONDS=600",
        "",
        "# ---- Expo push (optional) ----",
        "EXPO_PUSH_ENABLED=false",
        "EXPO_ACCESS_TOKEN=",
    ]
    print("\n".join(lines))


if __name__ == "__main__":
    main()
