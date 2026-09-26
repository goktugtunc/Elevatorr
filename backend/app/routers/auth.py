"""Wallet authentication — Sign-In with Ethereum (EIP-4361): nonce → personal_sign → verify → JWT."""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.api_deps import DB, Claims, SettingsDep
from app.core.ratelimit import client_ip
from app.schemas.auth import AuthMeOut, LoginOut, NonceIn, NonceOut, VerifyIn
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/nonce", response_model=NonceOut)
async def create_nonce(body: NonceIn, request: Request, db: DB, settings: SettingsDep) -> NonceOut:
    """Step 1: server builds the SIWE message; the wallet signs `message` byte-for-byte (personal_sign)."""
    return await auth_service.create_nonce(db, settings, body.address, client_ip(request))


@router.post("/verify", response_model=LoginOut)
async def verify_nonce(body: VerifyIn, db: DB, settings: SettingsDep) -> LoginOut:
    """Step 2: verify the EIP-191 signature over the issued message, burn the nonce, issue a JWT."""
    return await auth_service.verify_siwe_login(db, settings, body.message, body.signature)


@router.get("/me", response_model=AuthMeOut)
async def me(claims: Claims, db: DB) -> AuthMeOut:
    """Who am I: valid token required, profile optional (registered=false before /users/register)."""
    return await auth_service.auth_me(db, claims)


@router.post("/refresh", response_model=LoginOut)
async def refresh(claims: Claims, db: DB, settings: SettingsDep) -> LoginOut:
    """Exchange a valid token for a fresh one; `auth_time` is kept, past the absolute ceiling → 401 session_expired."""
    return await auth_service.refresh_token(db, settings, claims)
