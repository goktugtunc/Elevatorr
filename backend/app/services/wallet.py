"""Cüzdan (Figma 8c) — Monad edition (03-backend-tasarim §6.4, 02-api-sozlesme §3.5 / §7).

* ``get_wallet``: native MON (``eth_getBalance``) + ``balanceOf`` of every active allow-listed ERC-20 read live
  through the chain gateway (zero balances included). One failing token degrades to ``balance="0"`` + ``error``;
  when every read fails the request is a 502 ``chain_error``.
* ``deposit_info``: EIP-681 pay URI, the native faucet link and the server-side test-token faucet state.
* ``build_transfer``: unsigned native transfer (``value`` = wei, ``data="0x"``) or ERC-20 ``transfer`` calldata,
  recorded in ``pending_transactions`` (``kind=transfer``) so ``POST /tx/submit`` can track it.
* ``faucet``: ``TestToken.mint`` sent by the backend with ``settings.minter_private_key`` (K7), one claim per
  user+asset per day (``FaucetClaim``), receipt awaited for up to 15 s.
* ``list_transactions``: ERC-20 ``Transfer`` logs touching the wallet over the last
  ``indexer_block_window × 5`` blocks (native MON value transfers are not in logs and are not listed).

Nothing Stellar survives here: no trustlines, no Horizon, no SEP-7, no friendbot, no anchor.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import (
    AppError,
    ChainError,
    InsufficientFundsError,
    NotFoundError,
    RateLimitedError,
    ValidationError,
)
from app.models import Asset, FaucetClaim, PendingTxKind, User
from app.schemas.tx import UnsignedTxOut
from app.schemas.wallet import (
    DepositInfoOut,
    FaucetOut,
    NativeBalanceOut,
    TokenBalanceOut,
    TokenFaucetAssetOut,
    TokenFaucetOut,
    WalletOut,
    WalletTransferIn,
    WalletTransferOut,
)
from app.services.agreements import record_pending, unsigned_out
from app.services.chain.addresses import ZERO_ADDRESS, checksum, normalize, same, short
from app.services.chain.amounts import MON_DECIMALS, format_amount, from_raw, mon_to_wei, to_raw, wei_to_mon
from app.services.chain.errors import FaucetDisabledError
from app.services.chain.gateway import ChainGateway
from app.services.chain.types import EventRecord, UnsignedTx

log = logging.getLogger(__name__)

FAUCET_WINDOW = timedelta(hours=24)
FAUCET_RECEIPT_WAIT_SECONDS = 15
FAUCET_RECEIPT_POLL_SECONDS = 1.0
# keccak256("Transfer(address,address,uint256)") — ERC-20 Transfer topic0 (IERC20 ABI).
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
TX_WINDOW_MULTIPLIER = 5


def now_utc() -> datetime:
    return datetime.now(UTC)


def explorer_address_url(settings: Settings, address: str) -> str:
    return f"{settings.explorer_url.rstrip('/')}/address/{checksum(address)}"


def explorer_tx_url(settings: Settings, tx_hash: str) -> str:
    return f"{settings.explorer_url.rstrip('/')}/tx/{tx_hash}"


# --- assets ------------------------------------------------------------------------------------------------


async def active_assets(db: AsyncSession, settings: Settings) -> list[Asset]:
    """Active allow-list rows of the configured chain, base assets first (02-api §4 ordering)."""
    q = (
        select(Asset)
        .where(Asset.chain_id == settings.chain_id, Asset.is_active.is_(True))
        .order_by(Asset.is_base_allowed.desc(), Asset.symbol.asc(), Asset.created_at.asc())
    )
    return list((await db.execute(q)).scalars().all())


async def get_active_asset(db: AsyncSession, settings: Settings, asset_id: uuid.UUID) -> Asset:
    asset = await db.get(Asset, asset_id)
    if asset is None or asset.chain_id != settings.chain_id or not asset.is_active:
        raise NotFoundError("Asset not found", code="asset_not_found")
    return asset


# --- balances --------------------------------------------------------------------------------------------


async def _token_row(chain: ChainGateway, holder: str, asset: Asset) -> TokenBalanceOut:
    try:
        raw = int(await chain.token_balance(asset.address, holder))
    except Exception as e:  # noqa: BLE001 - one token must not hide the others
        log.warning("wallet: balanceOf failed token=%s holder=%s: %r", asset.symbol, short(holder), e)
        err = e.code if isinstance(e, AppError) else e.__class__.__name__
        return TokenBalanceOut(
            asset_id=asset.id, address=asset.address, symbol=asset.symbol, decimals=asset.decimals,
            balance=Decimal(0), balance_raw="0", is_base_allowed=asset.is_base_allowed, error=err,
        )
    return TokenBalanceOut(
        asset_id=asset.id,
        address=asset.address,
        symbol=asset.symbol,
        decimals=asset.decimals,
        balance=from_raw(raw, asset.decimals),
        balance_raw=str(raw),
        is_base_allowed=asset.is_base_allowed,
    )


async def get_wallet(db: AsyncSession, settings: Settings, chain: ChainGateway, user: User) -> WalletOut:
    """Live balances of the caller's address: native MON + every active allow-listed token (zero included)."""
    holder = normalize(user.wallet_address)
    assets = await active_assets(db, settings)
    try:
        native_wei = int(await chain.native_balance(holder))
    except AppError:
        raise
    except Exception as e:  # noqa: BLE001 - transport failures surface as 502 chain_error
        raise ChainError("could not read the native balance", details={"error": e.__class__.__name__}) from e
    tokens = list(await asyncio.gather(*(_token_row(chain, holder, a) for a in assets)))
    if assets and all(t.error is not None for t in tokens):
        raise ChainError(
            "token balances unavailable", details={"errors": {t.symbol: t.error for t in tokens}}
        )
    return WalletOut(
        address=holder,
        chain_id=settings.chain_id,
        native=NativeBalanceOut(
            symbol=settings.native_symbol,
            decimals=settings.native_decimals,
            balance=from_raw(native_wei, settings.native_decimals),
            balance_raw=str(native_wei),
        ),
        tokens=tokens,
        explorer_url=explorer_address_url(settings, holder),
        updated_at=now_utc(),
    )


# --- faucet state ---------------------------------------------------------------------------------------------


def faucet_amount_for(settings: Settings, asset: Asset) -> Decimal | None:
    """Configured per-claim amount for `asset` (symbol match is case-insensitive); None = not mintable."""
    for sym, raw in settings.faucet_amounts.items():
        if sym.strip().upper() == asset.symbol.upper():
            try:
                amount = Decimal(str(raw))
            except (InvalidOperation, ValueError):
                log.warning("faucet: non-numeric amount for %s: %r", sym, raw)
                return None
            return amount if amount > 0 else None
    return None


async def faucet_state(
    db: AsyncSession, settings: Settings, user_id: uuid.UUID, asset_id: uuid.UUID, *, now: datetime | None = None
) -> tuple[int, datetime | None]:
    """(claims in the last 24 h, next_allowed_at) — `next_allowed_at` is None while a claim is still allowed."""
    now = now or now_utc()
    limit = int(settings.faucet_daily_limit)
    rows = (
        await db.execute(
            select(FaucetClaim.created_at)
            .where(
                FaucetClaim.user_id == user_id,
                FaucetClaim.asset_id == asset_id,
                FaucetClaim.created_at >= now - FAUCET_WINDOW,
            )
            .order_by(FaucetClaim.created_at.asc())
        )
    ).scalars().all()
    count = len(rows)
    if limit <= 0:
        return count, None
    if count < limit:
        return count, None
    # the (count - limit)-th oldest claim is the one that has to leave the window before a new claim fits
    return count, rows[count - limit] + FAUCET_WINDOW


async def _token_faucet(db: AsyncSession, settings: Settings, user: User, assets: list[Asset]) -> TokenFaucetOut:
    enabled = settings.minter_private_key is not None
    out: list[TokenFaucetAssetOut] = []
    now = now_utc()
    for asset in assets:
        amount = faucet_amount_for(settings, asset)
        if amount is None:
            continue
        _, next_allowed = await faucet_state(db, settings, user.id, asset.id, now=now)
        out.append(
            TokenFaucetAssetOut(
                asset_id=asset.id,
                symbol=asset.symbol,
                amount=amount,
                daily_limit=int(settings.faucet_daily_limit),
                next_allowed_at=next_allowed,
            )
        )
    return TokenFaucetOut(enabled=enabled, assets=out)


async def deposit_info(db: AsyncSession, settings: Settings, user: User) -> DepositInfoOut:
    address = normalize(user.wallet_address)
    assets = await active_assets(db, settings)
    token_faucet = await _token_faucet(db, settings, user, assets)
    sym = settings.native_symbol
    instructions = [
        f"Bu adres {settings.chain_name} üzerindeki cüzdanınızdır; {sym} ve izinli tokenları buraya gönderebilirsiniz.",
        f"{sym} işlem ücreti (gas) için gerekir; {settings.chain_name} faucet'inden alın.",
    ]
    if token_faucet.enabled and token_faucet.assets:
        names = ", ".join(a.symbol for a in token_faucet.assets)
        instructions.append(f"Test tokenları ({names}) için cüzdan ekranındaki 'Test token al' düğmesini kullanın.")
    return DepositInfoOut(
        address=address,
        chain_id=settings.chain_id,
        pay_uri=f"ethereum:{checksum(address)}@{settings.chain_id}",
        faucet_url=settings.faucet_url or None,
        token_faucet=token_faucet,
        instructions=instructions,
    )


# --- transfer (unsigned) ------------------------------------------------------------------------------------


async def _gas_reserve_wei(chain: ChainGateway, unsigned: UnsignedTx) -> int:
    """`gas × gas_price` when the gateway exposes a `gas_price()` coroutine; 0 otherwise (KARAR: the Protocol has
    no gas-price read, so the reserve is best-effort and the wallet still checks fees itself)."""
    fn = getattr(chain, "gas_price", None)
    if not callable(fn) or not unsigned.gas:
        return 0
    try:
        price = fn()
        if asyncio.iscoroutine(price):
            price = await price
        return int(unsigned.gas) * int(price)
    except Exception as e:  # noqa: BLE001 - never block a transfer on a fee read
        log.debug("wallet: gas price unavailable: %r", e)
        return 0


def _with_description(out: UnsignedTxOut, description: str) -> UnsignedTxOut:
    if "description" in type(out).model_fields and not getattr(out, "description", None):
        return out.model_copy(update={"description": description})
    return out


async def build_transfer(
    db: AsyncSession, settings: Settings, chain: ChainGateway, user: User, body: WalletTransferIn
) -> UnsignedTxOut:
    """Unsigned transfer from the caller: native MON (`asset_id` null) or ERC-20 `transfer(to, amount_raw)`."""
    sender = normalize(user.wallet_address)
    to = normalize(body.to)
    if same(to, sender):
        raise ValidationError("You cannot send to your own address", code="invalid_destination")
    try:
        native_wei = int(await chain.native_balance(sender))
    except AppError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ChainError("could not read the native balance", details={"error": e.__class__.__name__}) from e

    if body.asset_id is None:
        wei = mon_to_wei(body.amount)  # AmountError -> 422 too_many_decimals / invalid_amount
        unsigned = await chain.build_transfer(sender, to, wei)
        needed = wei + await _gas_reserve_wei(chain, unsigned)
        if native_wei < needed:
            raise InsufficientFundsError(
                f"insufficient {settings.native_symbol} balance",
                details={
                    "needed": format_amount(wei_to_mon(needed)),
                    "available": format_amount(wei_to_mon(native_wei)),
                    "needed_raw": str(needed),
                    "available_raw": str(native_wei),
                    "symbol": settings.native_symbol,
                },
            )
        symbol, decimals, asset_id = settings.native_symbol, MON_DECIMALS, None
        amount = wei_to_mon(wei)
        amount_raw = wei
    else:
        asset = await get_active_asset(db, settings, body.asset_id)
        amount_raw = to_raw(body.amount, asset.decimals)
        try:
            balance = int(await chain.token_balance(asset.address, sender))
        except AppError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ChainError("could not read the token balance", details={"error": e.__class__.__name__}) from e
        if balance < amount_raw:
            raise InsufficientFundsError(
                f"insufficient {asset.symbol} balance",
                details={
                    "needed": format_amount(from_raw(amount_raw, asset.decimals)),
                    "available": format_amount(from_raw(balance, asset.decimals)),
                    "needed_raw": str(amount_raw),
                    "available_raw": str(balance),
                    "symbol": asset.symbol,
                },
            )
        if native_wei <= 0:
            raise InsufficientFundsError(
                f"no {settings.native_symbol} for gas",
                details={"needed": "gas", "available": "0", "symbol": settings.native_symbol},
            )
        unsigned = await chain.build_token_transfer(sender, asset.address, to, amount_raw)
        symbol, decimals, asset_id = asset.symbol, asset.decimals, asset.id
        amount = from_raw(amount_raw, asset.decimals)

    description = f"{format_amount(amount)} {symbol} gönder → {short(to)}"
    payload: dict[str, Any] = {
        "description": description,
        "to": checksum(to),
        "asset_id": str(asset_id) if asset_id else None,
        "symbol": symbol,
        "decimals": decimals,
        "amount": format_amount(amount),
        "amount_raw": str(amount_raw),
        "native": asset_id is None,
    }
    pending = await record_pending(db, user, PendingTxKind.transfer, None, unsigned, payload)
    return _with_description(unsigned_out(pending, unsigned), description)


# --- faucet -------------------------------------------------------------------------------------------------


async def _wait_receipt(chain: ChainGateway, tx_hash: str) -> Any | None:
    """Poll the receipt for up to `FAUCET_RECEIPT_WAIT_SECONDS`; None when still pending / unreadable."""
    deadline = asyncio.get_running_loop().time() + FAUCET_RECEIPT_WAIT_SECONDS
    while True:
        try:
            receipt = await chain.get_receipt(tx_hash)
        except Exception as e:  # noqa: BLE001 - the mint is already sent; report `submitted`
            log.warning("faucet: receipt poll failed for %s: %r", tx_hash, e)
            return None
        if receipt is not None:
            return receipt
        if asyncio.get_running_loop().time() >= deadline:
            return None
        await asyncio.sleep(FAUCET_RECEIPT_POLL_SECONDS)


async def faucet(
    db: AsyncSession, settings: Settings, chain: ChainGateway, user: User, asset_id: uuid.UUID
) -> FaucetOut:
    """Mint the configured test amount of `asset_id` to the caller (K7).

    The `FaucetClaim` row is written (flushed) *before* the mint so a parallel request in the same window sees
    the limit; when the mint cannot be sent (or reverts) the row is removed again.
    """
    key = settings.minter_private_key
    if not key:
        raise FaucetDisabledError()
    asset = await get_active_asset(db, settings, asset_id)
    amount = faucet_amount_for(settings, asset)
    if amount is None:
        raise ValidationError(
            f"{asset.symbol} is not mintable by the faucet",
            code="asset_not_mintable",
            details={"asset_id": str(asset.id), "symbol": asset.symbol},
        )
    if int(settings.faucet_daily_limit) <= 0:
        raise FaucetDisabledError("faucet daily limit is 0")
    amount_raw = to_raw(amount, asset.decimals)
    now = now_utc()
    _, next_allowed = await faucet_state(db, settings, user.id, asset.id, now=now)
    if next_allowed is not None:
        retry = max(0, int((next_allowed - now).total_seconds()))
        raise RateLimitedError(
            f"faucet limit reached for {asset.symbol}; try again at {next_allowed.isoformat()}",
            details={"next_allowed_at": next_allowed.isoformat(), "retry_after_seconds": retry, "symbol": asset.symbol},
        )

    claim = FaucetClaim(user_id=user.id, asset_id=asset.id, amount=amount, tx_hash=None, created_at=now)
    db.add(claim)
    await db.flush()
    try:
        tx_hash = await chain.faucet_mint(key, asset.address, user.wallet_address, amount_raw)
    except Exception as e:
        await db.delete(claim)
        await db.flush()
        if isinstance(e, AppError):
            raise
        raise ChainError("faucet mint could not be sent", details={"error": e.__class__.__name__}) from e
    tx_hash = tx_hash.lower()
    claim.tx_hash = tx_hash
    await db.flush()
    log.info("faucet: minted %s %s to %s tx=%s", format_amount(amount), asset.symbol, short(user.wallet_address), tx_hash)

    status = "submitted"
    receipt = await _wait_receipt(chain, tx_hash)
    if receipt is not None:
        if getattr(receipt, "ok", getattr(receipt, "status", 1) == 1):
            status = "confirmed"
        else:
            await db.delete(claim)
            await db.flush()
            raise ChainError(
                "faucet mint reverted",
                details={"tx_hash": tx_hash, "explorer_url": explorer_tx_url(settings, tx_hash)},
            )
    _, next_allowed = await faucet_state(db, settings, user.id, asset.id, now=now_utc())
    return FaucetOut(
        tx_hash=tx_hash,
        status=status,
        asset_id=asset.id,
        symbol=asset.symbol,
        amount=amount,
        amount_raw=str(amount_raw),
        next_allowed_at=next_allowed,
        explorer_url=explorer_tx_url(settings, tx_hash),
    )


# --- transfer history (ERC-20 logs) ---------------------------------------------------------------------


def _topic_address(address: str) -> str:
    return "0x" + "00" * 12 + normalize(address)[2:]


def _counterparty_label(settings: Settings, address: str) -> str | None:
    if same(address, ZERO_ADDRESS):
        return "mint"
    if settings.vault_address and same(address, settings.vault_address):
        return "vault"
    if settings.router_address and same(address, settings.router_address):
        return "router"
    return None


def _transfer_out(settings: Settings, holder: str, asset: Asset, rec: EventRecord) -> WalletTransferOut | None:
    args = rec.args or {}
    frm = args.get("from") or args.get("_from") or args.get("sender")
    to = args.get("to") or args.get("_to") or args.get("recipient")
    value = args.get("value", args.get("amount"))
    if frm is None or to is None or value is None:
        return None
    frm, to = str(frm), str(to)
    is_from, is_to = same(frm, holder), same(to, holder)
    if is_from and is_to:
        direction, counterparty = "self", holder
    elif is_from:
        direction, counterparty = "out", to
    else:
        direction, counterparty = "in", frm
    label = _counterparty_label(settings, counterparty)
    if direction == "out" and same(counterparty, ZERO_ADDRESS):
        label = "burn"
    return WalletTransferOut(
        tx_hash=rec.tx_hash,
        block_number=rec.block_number,
        log_index=rec.log_index,
        asset_id=asset.id,
        symbol=asset.symbol,
        direction=direction,
        counterparty=normalize(counterparty),
        counterparty_label=label,
        amount=from_raw(int(value), asset.decimals),
        amount_raw=str(int(value)),
        at=rec.block_timestamp,
        explorer_url=explorer_tx_url(settings, rec.tx_hash),
    )


async def list_transactions(
    db: AsyncSession, settings: Settings, chain: ChainGateway, user: User, *, limit: int = 20, offset: int = 0
) -> tuple[list[WalletTransferOut], int]:
    """ERC-20 `Transfer` logs where the wallet is `from` or `to` (newest first) over the recent block window.

    Native MON transfers carry no log and are therefore not listed (02-api §7.4 allows omitting them).
    """
    holder = normalize(user.wallet_address)
    assets = await active_assets(db, settings)
    if not assets:
        return [], 0
    try:
        latest = int(await chain.latest_block())
    except AppError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ChainError("could not read the latest block", details={"error": e.__class__.__name__}) from e
    from_block = max(0, latest - int(settings.indexer_block_window) * TX_WINDOW_MULTIPLIER)
    topic = _topic_address(holder)
    seen: set[tuple[str, int]] = set()
    items: list[WalletTransferOut] = []
    for asset in assets:
        for topics in ([TRANSFER_TOPIC, topic], [TRANSFER_TOPIC, None, topic]):
            try:
                records = await chain.get_logs(from_block, latest, address=asset.address, topics=topics)
            except AppError:
                raise
            except Exception as e:  # noqa: BLE001
                raise ChainError("could not read transfer logs", details={"error": e.__class__.__name__}) from e
            for rec in records:
                key = (rec.tx_hash.lower(), int(rec.log_index))
                if key in seen or rec.removed:
                    continue
                out = _transfer_out(settings, holder, asset, rec)
                if out is None:
                    continue
                seen.add(key)
                items.append(out)
    items.sort(key=lambda t: (t.block_number, t.log_index), reverse=True)
    return items[offset : offset + limit], len(items)


__all__ = [
    "FAUCET_RECEIPT_WAIT_SECONDS",
    "FAUCET_WINDOW",
    "TRANSFER_TOPIC",
    "active_assets",
    "build_transfer",
    "deposit_info",
    "explorer_address_url",
    "explorer_tx_url",
    "faucet",
    "faucet_amount_for",
    "faucet_state",
    "get_active_asset",
    "get_wallet",
    "list_transactions",
]
