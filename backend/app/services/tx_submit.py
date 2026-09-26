"""`POST /tx/submit` + `GET /tx/{pending_id}` (03-backend-tasarim §4.3; 02-api-sozlesme §2.3–2.4).

The wallet broadcasts the calldata itself; the app reports the hash. We verify ownership / expiry / hash
format, mark the row `submitted` and **commit** so the row lock is not held while we poll for the receipt
(the only service that calls `db.commit()`), wait up to `settings.tx_submit_timeout_seconds`, and hand the
receipt to `indexer.finalize_receipt` (from/to/input/value check, event application, `confirmed | failed`).
No receipt in time -> `status="submitted"`; the worker's `pending_tracker` finishes the row later.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import (
    ChainError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    StateError,
    ValidationError,
)
from app.models import Agreement, PendingTransaction, PendingTxKind, PendingTxStatus, Trade, User
from app.schemas.tx import TxStatusOut
from app.services.agreements import now_utc
from app.services.chain.errors import ReceiptMismatchError
from app.services.chain.gateway import ChainGateway
from app.services.chain.types import TxReceiptResult
from app.services.indexer import IndexContext, finalize_receipt

log = logging.getLogger(__name__)

TX_HASH_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
RECEIPT_POLL_SECONDS = 1.0


def normalize_tx_hash(value: str) -> str:
    """`0x` + 64 hex, lower-case; 422 `invalid_tx_hash` otherwise."""
    v = (value or "").strip()
    if not TX_HASH_RE.match(v):
        raise ValidationError("tx_hash must be 0x + 64 hex characters", code="invalid_tx_hash", details={"value": value})
    return v.lower()


def explorer_tx_url(settings: Settings, tx_hash: str | None) -> str | None:
    if not tx_hash:
        return None
    return f"{settings.explorer_url.rstrip('/')}/tx/{tx_hash}"


async def wait_receipt(
    chain: ChainGateway, tx_hash: str, *, timeout_seconds: float, poll: float = RECEIPT_POLL_SECONDS
) -> TxReceiptResult | None:
    """Poll `get_receipt` until it returns or `timeout_seconds` pass (None). RPC errors end the wait early
    (the worker keeps tracking the row)."""
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    while True:
        try:
            receipt = await chain.get_receipt(tx_hash)
        except ChainError as e:
            log.warning("get_receipt %s failed: %s", tx_hash[:10], e.message)
            return None
        if receipt is not None:
            return receipt
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(min(poll, max(0.0, deadline - time.monotonic())) or poll)


async def _owned_pending(db: AsyncSession, user: User, pending_id: uuid.UUID, *, lock: bool = False) -> PendingTransaction:
    pending = await db.get(PendingTransaction, pending_id, with_for_update=lock, populate_existing=lock)
    if pending is None:
        raise NotFoundError("Pending transaction not found", code="pending_tx_not_found")
    if pending.user_id != user.id and not user.is_admin:
        raise ForbiddenError("This pending transaction belongs to another user", code="not_owner")
    return pending


def _expire(pending: PendingTransaction) -> None:
    pending.status = PendingTxStatus.expired
    pending.error_code = "expired"
    pending.error_message = "not sent before expires_at"
    pending.confirmed_at = now_utc()


def _raise_expired(pending: PendingTransaction) -> None:
    raise StateError(
        "pending transaction expired; build a new one",
        code="pending_tx_expired",
        details={"expires_at": pending.expires_at.isoformat()},
    )


async def tx_status_out(db: AsyncSession, settings: Settings, chain: ChainGateway | None, pending: PendingTransaction) -> TxStatusOut:
    """`TxStatusOut` (02-api §2.3): row + agreement context + `confirmations = latest_block − block_number + 1`."""
    agreement: Agreement | None = None
    if pending.agreement_id is not None:
        agreement = await db.get(Agreement, pending.agreement_id)
    trade_id: uuid.UUID | None = None
    if pending.kind is PendingTxKind.trade and pending.tx_hash and pending.status is PendingTxStatus.confirmed:
        trade_id = (
            await db.execute(select(Trade.id).where(Trade.tx_hash == pending.tx_hash).order_by(Trade.log_index).limit(1))
        ).scalar_one_or_none()
    confirmations: int | None = None
    if pending.block_number is not None and chain is not None:
        try:
            confirmations = max(0, int(await chain.latest_block()) - int(pending.block_number) + 1)
        except ChainError as e:
            log.debug("latest_block unavailable for confirmations: %s", e.message)
    result = dict(pending.result or {})
    return TxStatusOut(
        pending_tx_id=pending.id,
        kind=pending.kind,
        action=pending.action,
        status=pending.status,
        tx_hash=pending.tx_hash,
        block_number=pending.block_number,
        confirmations=confirmations,
        error_code=pending.error_code,
        error_message=pending.error_message,
        contract_error_code=pending.contract_error_code,
        explorer_url=explorer_tx_url(settings, pending.tx_hash),
        agreement_id=pending.agreement_id,
        listing_id=pending.listing_id,
        agreement_status=agreement.status if agreement is not None else None,
        onchain_id=agreement.onchain_id if agreement is not None else None,
        trade_id=trade_id,
        events=list(result.get("events") or []),
        submitted_at=pending.submitted_at,
        updated_at=pending.confirmed_at or pending.submitted_at or pending.created_at,
        expires_at=pending.expires_at,
    )


async def submit_hash(
    db: AsyncSession, settings: Settings, chain: ChainGateway, user: User, pending_tx_id: uuid.UUID, tx_hash: str
) -> TxStatusOut:
    """03 §4.3 mandatory order: validate -> lock -> idempotency/conflict checks -> `submitted` + commit ->
    wait receipt -> `finalize_receipt` -> `TxStatusOut`."""
    tx_hash = normalize_tx_hash(tx_hash)
    pending = await _owned_pending(db, user, pending_tx_id, lock=True)
    now = now_utc()

    if pending.status in (PendingTxStatus.confirmed, PendingTxStatus.failed):
        if pending.tx_hash and pending.tx_hash != tx_hash:
            raise ConflictError(
                "this pending transaction is bound to another hash",
                code="tx_hash_conflict",
                details={"tx_hash": pending.tx_hash},
            )
        return await tx_status_out(db, settings, chain, pending)  # idempotent replay
    if pending.status is PendingTxStatus.expired:
        _raise_expired(pending)
    if pending.status is PendingTxStatus.pending and pending.expires_at <= now:
        _expire(pending)
        await db.commit()
        _raise_expired(pending)
    if pending.status is PendingTxStatus.submitted and pending.tx_hash and pending.tx_hash != tx_hash:
        raise ConflictError(
            "this pending transaction was already submitted with another hash",
            code="tx_hash_conflict",
            details={"tx_hash": pending.tx_hash},
        )
    other = (
        await db.execute(
            select(PendingTransaction.id).where(PendingTransaction.tx_hash == tx_hash, PendingTransaction.id != pending.id)
        )
    ).scalar_one_or_none()
    if other is not None:
        raise ConflictError(
            "this tx_hash is already bound to another pending transaction",
            code="tx_hash_conflict",
            details={"pending_tx_id": str(other)},
        )

    if pending.status is PendingTxStatus.pending:
        pending.status = PendingTxStatus.submitted
        pending.tx_hash = tx_hash
        pending.submitted_at = now
    await db.commit()  # the row lock ends here; the receipt wait below holds nothing
    log.info("tx/submit pending=%s kind=%s hash=%s", pending.id, pending.kind.value, tx_hash[:10])

    receipt = await wait_receipt(chain, tx_hash, timeout_seconds=settings.tx_submit_timeout_seconds)
    if receipt is None:
        return await tx_status_out(db, settings, chain, pending)  # still `submitted`; pending_tracker takes over
    pending = await finalize_receipt(IndexContext(db, settings, chain), pending.id, receipt, actor_user_id=user.id)
    log.info("tx/submit pending=%s hash=%s -> %s", pending.id, tx_hash[:10], pending.status.value)
    return await tx_status_out(db, settings, chain, pending)


async def get_pending(
    db: AsyncSession, settings: Settings, chain: ChainGateway, user: User, pending_id: uuid.UUID
) -> TxStatusOut:
    """`GET /tx/{pending_id}`: ownership; `pending` past `expires_at` -> `expired`; `submitted` -> one
    `get_receipt` (no wait) and `finalize_receipt` when it is there."""
    pending = await _owned_pending(db, user, pending_id)
    if pending.status is PendingTxStatus.pending and pending.expires_at <= now_utc():
        _expire(pending)
        await db.flush()
    elif pending.status is PendingTxStatus.submitted and pending.tx_hash:
        try:
            receipt = await chain.get_receipt(pending.tx_hash)
        except ChainError as e:
            log.warning("get_pending %s: rpc unavailable: %s", pending.id, e.message)
            receipt = None
        if receipt is not None:
            try:
                pending = await finalize_receipt(
                    IndexContext(db, settings, chain), pending.id, receipt, actor_user_id=pending.user_id
                )
            except ReceiptMismatchError:
                # the row is already committed as failed(receipt_mismatch); report it instead of a 409 on a GET
                pending = await _owned_pending(db, user, pending_id, lock=True)
    return await tx_status_out(db, settings, chain, pending)


__all__ = ["explorer_tx_url", "get_pending", "normalize_tx_hash", "submit_hash", "tx_status_out", "wait_receipt"]
