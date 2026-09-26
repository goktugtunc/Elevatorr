"""Transaction hash submit + pending transaction status (02-api-sozlesme §2.3–2.4)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.api_deps import DB, ChainDep, CurrentUser, SettingsDep
from app.schemas.tx import TxStatusOut, TxSubmitIn
from app.services import tx_submit

router = APIRouter(prefix="/tx", tags=["tx"])


@router.post("/submit", response_model=TxStatusOut)
async def submit(body: TxSubmitIn, db: DB, user: CurrentUser, settings: SettingsDep, chain=ChainDep) -> TxStatusOut:
    """Report the hash the wallet broadcast for `pending_tx_id`. The row is marked `submitted` at once; the
    receipt is awaited up to `tx_submit_timeout_seconds`, verified (`from/to/input/value`) and applied.
    `status=failed` carries `error_code` (`vault:<Name>` …); `status=submitted` means the wait elapsed —
    poll `GET /tx/{pending_id}`. 409 `tx_hash_conflict` / `receipt_mismatch` / `pending_tx_expired`."""
    return await tx_submit.submit_hash(db, settings, chain, user, body.pending_tx_id, body.tx_hash)


@router.get("/{pending_id}", response_model=TxStatusOut)
async def get_pending(pending_id: uuid.UUID, db: DB, user: CurrentUser, settings: SettingsDep, chain=ChainDep) -> TxStatusOut:
    """Status of one of the caller's pending transactions; `submitted` rows are checked for a receipt."""
    return await tx_submit.get_pending(db, settings, chain, user, pending_id)


__all__ = ["router"]
