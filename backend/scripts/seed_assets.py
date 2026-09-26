"""Idempotent asset allow-list seed from the deployment record (03-backend-tasarim §7.3).

    python -m scripts.seed_assets [--file deployments/monad-testnet.json]

Source of truth, in order: the deployments JSON (01-kontrat-spec §9.4 schema) if it exists; otherwise
`settings.vault_address/router_address` + `settings.assets_json`; if neither is present the script exits.

Rows are matched by (chain_id, lower-case address). `symbol, name, decimals, category, is_base_allowed` are refreshed
on every run; the admin-controlled flags `is_active` and `onchain_allowed` are never overwritten for existing rows.
When `indexer_state["vault_events"]` does not exist yet and the deployment has a `blockNumber`, the indexer cursor is
seeded to `blockNumber - 1` so indexing starts at the deploy block.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import setup_logging
from app.db.session import dispose_engine, get_session_factory
from app.models import Asset, IndexerState, MarketCategory
from app.services.chain.addresses import normalize

log = logging.getLogger("seed_assets")

BACKEND_ROOT = Path(__file__).resolve().parents[1]
INDEXER_KEY = "vault_events"

# symbol -> (display name, category); unknown symbols fall back to (symbol, crypto)
KNOWN_TOKENS: dict[str, tuple[str, MarketCategory]] = {
    "tUSDC": ("Test USD Coin", MarketCategory.stable_fx),
    "tWETH": ("Test Wrapped Ether", MarketCategory.crypto),
    "tWBTC": ("Test Wrapped Bitcoin", MarketCategory.crypto),
}

_REFRESHED_FIELDS = ("symbol", "name", "decimals", "category", "is_base_allowed")


@dataclass(frozen=True)
class DeploymentToken:
    symbol: str
    address: str  # lower-case
    decimals: int
    is_base: bool

    @property
    def name(self) -> str:
        return KNOWN_TOKENS.get(self.symbol, (self.symbol, MarketCategory.crypto))[0]

    @property
    def category(self) -> MarketCategory:
        return KNOWN_TOKENS.get(self.symbol, (self.symbol, MarketCategory.crypto))[1]


@dataclass(frozen=True)
class Deployment:
    chain_id: int
    vault: str | None  # proxy, lower-case
    router: str | None
    tokens: list[DeploymentToken] = field(default_factory=list)
    block_number: int = 0
    source: str = "env"


def _token_from_json(raw: dict) -> DeploymentToken:
    return DeploymentToken(
        symbol=str(raw["symbol"]),
        address=normalize(str(raw["address"])),
        decimals=int(raw["decimals"]),
        is_base=bool(raw.get("isBase", raw.get("is_base", False))),
    )


def parse_deployment_json(data: dict, *, source: str = "json") -> Deployment:
    """01-kontrat-spec §9.4: `{chainId, vault:{proxy,implementation}, router, tokens[], blockNumber, …}`."""
    vault = data.get("vault")
    if isinstance(vault, dict):
        vault = vault.get("proxy")
    return Deployment(
        chain_id=int(data["chainId"]),
        vault=normalize(vault) if vault else None,
        router=normalize(data["router"]) if data.get("router") else None,
        tokens=[_token_from_json(t) for t in data.get("tokens", [])],
        block_number=int(data.get("blockNumber") or 0),
        source=source,
    )


def load_deployment(settings: Settings, file: str | None = None) -> Deployment:
    """Deployments JSON (relative paths resolve against the backend root) or the `.env` fallback."""
    path = Path(file or settings.deployments_file)
    if not path.is_absolute():
        path = BACKEND_ROOT / path
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return parse_deployment_json(data, source=str(path))
    if settings.assets_json:
        tokens = [_token_from_json(t) for t in json.loads(settings.assets_json)]
        return Deployment(
            chain_id=settings.chain_id,
            vault=settings.vault_address,
            router=settings.router_address,
            tokens=tokens,
            block_number=settings.indexer_start_block or 0,
            source="env",
        )
    raise SystemExit(f"no deployment info: {path} missing and ASSETS_JSON not set")


def check_deployment(deployment: Deployment, settings: Settings) -> None:
    """Chain id mismatch is fatal; a vault mismatch only warns (catches a stale .env)."""
    if deployment.chain_id != settings.chain_id:
        raise SystemExit(
            f"deployment chain_id {deployment.chain_id} != settings.chain_id {settings.chain_id} ({deployment.source})"
        )
    if deployment.vault and settings.vault_address and deployment.vault != settings.vault_address:
        log.warning(
            "VAULT_ADDRESS %s differs from deployment vault %s (%s)",
            settings.vault_address,
            deployment.vault,
            deployment.source,
        )


async def seed_assets(db: AsyncSession, deployment: Deployment) -> tuple[int, int]:
    """Insert missing rows / refresh descriptive fields. Flushes only (caller commits). Returns (created, updated)."""
    created = updated = 0
    for token in deployment.tokens:
        existing = (
            await db.execute(
                select(Asset).where(Asset.chain_id == deployment.chain_id, Asset.address == token.address)
            )
        ).scalar_one_or_none()
        desired = {
            "symbol": token.symbol,
            "name": token.name,
            "decimals": token.decimals,
            "category": token.category,
            "is_base_allowed": token.is_base,
        }
        if existing is None:
            db.add(
                Asset(
                    chain_id=deployment.chain_id,
                    address=token.address,
                    is_active=True,
                    onchain_allowed=False,
                    **desired,
                )
            )
            created += 1
            continue
        changed = False
        for f in _REFRESHED_FIELDS:
            if getattr(existing, f) != desired[f]:
                setattr(existing, f, desired[f])
                changed = True
        if changed:
            updated += 1
    await db.flush()
    return created, updated


async def seed_indexer_cursor(db: AsyncSession, deployment: Deployment) -> bool:
    """Create `indexer_state[vault_events]` at `blockNumber - 1` when absent; never touches an existing row."""
    if deployment.block_number <= 0:
        return False
    existing = await db.get(IndexerState, INDEXER_KEY)
    if existing is not None:
        return False
    db.add(IndexerState(key=INDEXER_KEY, block_number=deployment.block_number - 1, last_block_hash=None))
    await db.flush()
    return True


async def seed(file: str | None = None) -> tuple[int, int]:
    """Seed from the deployment record and dispose the engine on the same event loop."""
    settings = get_settings()
    deployment = load_deployment(settings, file)
    check_deployment(deployment, settings)
    try:
        async with get_session_factory()() as db:
            result = await seed_assets(db, deployment)
            if await seed_indexer_cursor(db, deployment):
                log.info("indexer cursor seeded at block %d", deployment.block_number - 1)
            await db.commit()
    finally:
        await dispose_engine()
    log.info(
        "asset seed done (source=%s, chain_id=%d, created=%d, updated=%d)",
        deployment.source,
        deployment.chain_id,
        *result,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", default=None, help="deployments JSON (default: settings.deployments_file)")
    args = parser.parse_args()
    settings = get_settings()
    setup_logging(settings.log_level)
    asyncio.run(seed(args.file))


if __name__ == "__main__":
    main()
