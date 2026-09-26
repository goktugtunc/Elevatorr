"""Vault admin transactions for the operator (03-backend-tasarim §6.7; 02-api-sozlesme §3.6).

The contract owner is the operator's wallet — the server holds no key. `build_admin_tx` returns calldata
with `from_address = owner` (no pending row, `pending_tx_id = null`); `submit_admin_tx` waits for the
receipt of the hash the operator reports and checks `to == vault`, `from == owner`. The receipt's events
(`TokenSet`, `ConfigChanged`, …) are left to the indexer, which applies them idempotently.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import NotFoundError, ValidationError
from app.models import Asset
from app.schemas.admin import AdminTxOut, AdminTxSubmitOut
from app.services.agreements import json_safe, unsigned_out
from app.services.chain import abi
from app.services.chain.addresses import checksum, is_evm_address, normalize, same, short
from app.services.chain.errors import ReceiptMismatchError
from app.services.chain.gateway import ChainGateway
from app.services.tx_submit import explorer_tx_url, normalize_tx_hash, wait_receipt

log = logging.getLogger(__name__)

#: admin function (API name) -> gateway builder method
ADMIN_FUNCTIONS: dict[str, str] = {
    "set_token": "build_admin_set_token",
    "set_paused": "build_admin_set_paused",
    "set_fees": "build_admin_set_fees",
    "set_router": "build_admin_propose_router",
    "apply_router": "build_admin_apply_router",
    "set_settle_slippage": "build_admin_set_settle_slippage",
}


async def resolve_token(db: AsyncSession, settings: Settings, token: str) -> str:
    """`token` is an asset uuid (row on the configured chain) or a 0x token address -> lower-case address."""
    token = (token or "").strip()
    if is_evm_address(token):
        return normalize(token)
    try:
        asset_id = uuid.UUID(token)
    except ValueError as e:
        raise ValidationError("token must be an asset id or a 0x address", code="invalid_token") from e
    asset = await db.get(Asset, asset_id)
    if asset is None or asset.chain_id != settings.chain_id:
        raise NotFoundError("Asset not found", code="asset_not_found")
    return asset.address


def _description(function: str, args: dict[str, Any]) -> str:
    if function == "set_token":
        verb = "allow-list'e ekle" if args.get("allowed", True) else "allow-list'ten çıkar"
        base = " (taban)" if args.get("is_base") else ""
        return f"{short(args['token'])} tokenını {verb}{base}"
    if function == "set_paused":
        return "Kasayı durdur" if args.get("paused") else "Kasayı yeniden başlat"
    if function == "set_fees":
        return f"Platform ücretini {args['platform_fee_bps']} bps yap (alıcı {short(args['fee_recipient'])})"
    if function == "set_router":
        return f"Yönlendirici değişikliğini öner: {short(args['router'])} (24 saat sonra uygulanır)"
    if function == "apply_router":
        return "Bekleyen yönlendirici değişikliğini uygula"
    if function == "set_settle_slippage":
        return f"Kapanış kayma toleransını {args['bps']} bps yap"
    return function


async def build_admin_tx(settings: Settings, chain: ChainGateway, function: str, args: dict[str, Any]) -> AdminTxOut:
    """Calldata for a vault admin call with `from` = the contract owner (read live from the vault)."""
    if function not in ADMIN_FUNCTIONS:
        raise ValidationError(
            f"unknown admin function {function}", code="unknown_function", details={"allowed": list(ADMIN_FUNCTIONS)}
        )
    args = dict(args)
    if function == "set_fees":
        recipient = args.get("fee_recipient") or settings.platform_address
        if not recipient:
            raise ValidationError(
                "fee_recipient is required (PLATFORM_ADDRESS is not configured)", code="fee_recipient_required"
            )
        args["fee_recipient"] = normalize(recipient)
        bps = int(args["platform_fee_bps"])
        if bps < 0 or bps > abi.MAX_PLATFORM_FEE_BPS:
            raise ValidationError(f"platform_fee_bps must be 0..{abi.MAX_PLATFORM_FEE_BPS}", code="invalid_bps")
    if function == "set_settle_slippage":
        bps = int(args["bps"])
        if bps < 0 or bps > abi.MAX_SETTLE_SLIPPAGE_BPS:
            raise ValidationError(f"bps must be 0..{abi.MAX_SETTLE_SLIPPAGE_BPS}", code="invalid_bps")
    for key in ("token", "router"):
        if key in args:
            args[key] = normalize(args[key])

    cfg = await chain.read_config()
    owner = normalize(cfg.owner)
    if function == "set_token":
        unsigned = await chain.build_admin_set_token(owner, args["token"], bool(args["allowed"]), bool(args["is_base"]))
    elif function == "set_paused":
        unsigned = await chain.build_admin_set_paused(owner, bool(args["paused"]))
    elif function == "set_fees":
        unsigned = await chain.build_admin_set_fees(owner, int(args["platform_fee_bps"]), args["fee_recipient"])
    elif function == "set_router":
        unsigned = await chain.build_admin_propose_router(owner, args["router"])
    elif function == "apply_router":
        unsigned = await chain.build_admin_apply_router(owner)
    else:
        unsigned = await chain.build_admin_set_settle_slippage(owner, int(args["bps"]))

    base = unsigned_out(None, unsigned, description=_description(function, args))
    out = AdminTxOut(
        **base.model_dump(exclude={"pending_tx_id", "expires_at", "summary"}),
        pending_tx_id=None,
        expires_at=None,
        summary=json_safe(unsigned.summary),
        function=function,
        args={k: (checksum(v) if isinstance(v, str) and is_evm_address(v) else v) for k, v in args.items()},
        vault_address=checksum(chain.vault_address),
    )
    log.info("admin tx built fn=%s args=%s owner=%s", function, args, short(owner))
    return out


async def submit_admin_tx(settings: Settings, chain: ChainGateway, tx_hash: str) -> AdminTxSubmitOut:
    """Wait for the receipt of an owner-sent admin transaction and report it (02-api §3.6)."""
    tx_hash = normalize_tx_hash(tx_hash)
    receipt = await wait_receipt(chain, tx_hash, timeout_seconds=settings.tx_submit_timeout_seconds)
    explorer = explorer_tx_url(settings, tx_hash)
    if receipt is None:
        return AdminTxSubmitOut(tx_hash=tx_hash, status="submitted", explorer_url=explorer)
    cfg = await chain.read_config()
    if not same(receipt.to_address, chain.vault_address) or not same(receipt.from_address, cfg.owner):
        raise ReceiptMismatchError(
            "receipt is not an owner call to the vault",
            details={
                "tx_hash": tx_hash,
                "to": checksum(receipt.to_address) if receipt.to_address else None,
                "from": checksum(receipt.from_address),
                "expected_to": checksum(chain.vault_address),
                "expected_from": checksum(cfg.owner),
            },
        )
    if receipt.ok:
        log.info("admin tx confirmed hash=%s block=%s", tx_hash[:10], receipt.block_number)
        return AdminTxSubmitOut(tx_hash=tx_hash, status="confirmed", block_number=receipt.block_number, explorer_url=explorer)
    info = await chain.explain_failure(receipt)
    code = abi.error_code_of(info)
    log.warning("admin tx failed hash=%s %s: %s", tx_hash[:10], code, info.message)
    return AdminTxSubmitOut(
        tx_hash=tx_hash,
        status="failed",
        block_number=receipt.block_number,
        error_code=code,
        error_message=info.message,
        explorer_url=explorer,
    )


__all__ = ["ADMIN_FUNCTIONS", "build_admin_tx", "resolve_token", "submit_admin_tx"]
