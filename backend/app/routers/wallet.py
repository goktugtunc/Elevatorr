"""Cüzdan endpoints (Figma 8c) — Monad edition (02-api-sozlesme §3.5, §7)."""
from __future__ import annotations

from fastapi import APIRouter

from app.api_deps import DB, ChainDep, CurrentUser, SettingsDep
from app.api_paging import PageDep
from app.schemas.common import Page
from app.schemas.tx import UnsignedTxOut
from app.schemas.wallet import (
    DepositInfoOut,
    FaucetIn,
    FaucetOut,
    WalletOut,
    WalletTransferIn,
    WalletTransferOut,
)
from app.services import wallet as wallet_service

router = APIRouter(prefix="/wallet", tags=["wallet"])


@router.get("", response_model=WalletOut)
async def get_wallet(db: DB, settings: SettingsDep, user: CurrentUser, chain=ChainDep) -> WalletOut:
    """Live balances of the caller's address: native MON + every active allow-listed token (zero included)."""
    return await wallet_service.get_wallet(db, settings, chain, user)


@router.get("/deposit-info", response_model=DepositInfoOut)
async def deposit_info(db: DB, settings: SettingsDep, user: CurrentUser) -> DepositInfoOut:
    """Address, EIP-681 pay URI (QR), native faucet link and the test-token faucet state."""
    return await wallet_service.deposit_info(db, settings, user)


@router.post("/tx/transfer", response_model=UnsignedTxOut)
async def transfer(
    body: WalletTransferIn, db: DB, settings: SettingsDep, user: CurrentUser, chain=ChainDep
) -> UnsignedTxOut:
    """Unsigned transfer from the caller: native MON (`asset_id` null) or ERC-20 `transfer`. The wallet sends it
    with `eth_sendTransaction`; report the hash through `POST /tx/submit`."""
    return await wallet_service.build_transfer(db, settings, chain, user, body)


@router.post("/faucet", response_model=FaucetOut)
async def faucet(body: FaucetIn, db: DB, settings: SettingsDep, user: CurrentUser, chain=ChainDep) -> FaucetOut:
    """Server-side `TestToken.mint` of the configured amount to the caller (once per asset per day)."""
    return await wallet_service.faucet(db, settings, chain, user, body.asset_id)


@router.get("/transactions", response_model=Page[WalletTransferOut])
async def list_transactions(
    db: DB, settings: SettingsDep, user: CurrentUser, page: PageDep, chain=ChainDep
) -> Page[WalletTransferOut]:
    """Recent ERC-20 `Transfer` logs touching the wallet (newest first); native MON transfers are not listed."""
    items, total = await wallet_service.list_transactions(
        db, settings, chain, user, limit=page.limit, offset=page.offset
    )
    return Page[WalletTransferOut](items=items, total=total, limit=page.limit, offset=page.offset)


__all__ = ["router"]
