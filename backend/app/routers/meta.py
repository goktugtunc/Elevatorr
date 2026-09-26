"""Infrastructure endpoints: health checks (02-api-sozlesme §9). Mounted without prefix.

The public client config lives in app.routers.config (`GET /api/v1/config`).
"""
from __future__ import annotations

import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.services.chain import get_chain

router = APIRouter(tags=["meta"])

API_VERSION = "2.0.0"

DB = Annotated[AsyncSession, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("/health")
async def health(db: DB):
    try:
        await db.execute(text("SELECT 1"))
        db_status = "ok"
        code = 200
    except Exception:  # pragma: no cover - only when the database is down
        db_status = "error"
        code = 503
    return JSONResponse(
        status_code=code,
        content={"status": "ok" if code == 200 else "degraded", "db": db_status, "version": API_VERSION},
    )


@router.get("/health/chain")
async def health_chain(settings: SettingsDep, chain: Any = Depends(get_chain)):
    """RPC reachability + chain id check. 200 `{ok, chain_id, block_number, latency_ms, rpc_url}`;
    503 `{ok:false, chain_id?, error, rpc_url}` (`error="chain_id_mismatch"` when the RPC is another chain)."""
    t0 = time.perf_counter()
    try:
        cid = await chain.chain_id()
        bn = await chain.latest_block()
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "error": e.__class__.__name__, "rpc_url": settings.rpc_url},
        )
    latency_ms = int((time.perf_counter() - t0) * 1000)
    if int(cid) != settings.chain_id:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "chain_id": int(cid), "error": "chain_id_mismatch", "rpc_url": settings.rpc_url},
        )
    return {
        "ok": True,
        "chain_id": int(cid),
        "block_number": int(bn),
        "latency_ms": latency_ms,
        "rpc_url": settings.rpc_url,
    }
