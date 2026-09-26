"""FastAPI application factory.

Routers live in app.routers.<name> and MUST expose a module-level `router = APIRouter(...)`.
Domain errors (app.core.errors.AppError) are mapped to a uniform JSON error body here.
"""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import setup_logging
from app.db.session import dispose_engine, get_engine

log = logging.getLogger("app")

# Order matters: Starlette matches routes in registration order. `config` is listed first so the rich
# GET /api/v1/config (routers/config.py) is served; `meta` (no prefix) only carries /health*.
# Prefixsiz, kok seviyede sunulanlar.
ROOT_ROUTERS = {"meta", "legal"}

ROUTER_MODULES = [
    "config",  # /api/v1/config, /api/v1/fx, /api/v1/fx/convert
    "meta",  # /health, /health/chain (mounted without prefix)
    "legal",  # /legal/* — Play Store icin herkese acik hukuki sayfalar (prefixsiz)
    "auth",
    "users",
    "notifications",
    "assets",
    "admin",
    "listings",
    "discover",
    "offers",
    "conversations",
    "dashboard",
    "ratings",
    "agreements",
    "tx",
    "trades",
    "activity",
    "wallet",
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)
    get_engine()  # fail fast on bad DATABASE_URL
    log.info(
        "starting %s env=%s chain_id=%s rpc_url=%s vault=%s",
        settings.app_name,
        settings.app_env,
        settings.chain_id,
        settings.rpc_url,
        settings.vault_address or "-",
    )
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="TraderKirala API",
        version="2.0.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = rid
        start = time.perf_counter()
        response = await call_next(request)
        dur_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Request-ID"] = rid
        if request.url.path not in ("/health",):
            log.info("%s %s -> %s %.1fms rid=%s", request.method, request.url.path, response.status_code, dur_ms, rid)
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "details": exc.details},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            # jsonable_encoder: pydantic error contexts may carry Decimal / ValueError objects that json cannot dump
            content={
                "code": "validation_error",
                "message": "Invalid request",
                "details": {"errors": jsonable_encoder(exc.errors())},
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exc_handler(request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": "http_error", "message": str(exc.detail), "details": {}},
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        log.exception("unhandled error rid=%s", getattr(request.state, "request_id", ""))
        return JSONResponse(
            status_code=500, content={"code": "internal_error", "message": "Internal server error", "details": {}}
        )

    import importlib

    for name in ROUTER_MODULES:
        module = importlib.import_module(f"app.routers.{name}")
        router = module.router
        # meta router serves root-level paths (/health, /.well-known); everything else under the API prefix
        prefix = "" if name in ROOT_ROUTERS else settings.api_prefix
        app.include_router(router, prefix=prefix)

    return app


app = create_app()
