"""Auth I/O models — SIWE (EIP-4361) login (02-api-sozlesme §1)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import Address, AddressOut
from app.schemas.users import MeOut


class NonceIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    address: Address = Field(description="Wallet address (lower-case or EIP-55 checksum)")


class NonceOut(BaseModel):
    nonce: str
    message: str = Field(description="Exact EIP-4361 text to pass to personal_sign; send it back unchanged")
    expires_at: datetime
    chain_id: int
    domain: str


class VerifyIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000, description="NonceOut.message, byte-for-byte")
    signature: str = Field(pattern=r"^0x[0-9a-fA-F]{130}$", description="65-byte EIP-191 signature, 0x + 130 hex")


class LoginOut(BaseModel):
    token: str
    expires_at: datetime
    address: AddressOut
    registered: bool
    user: MeOut | None = None


class AuthMeOut(BaseModel):
    address: AddressOut
    registered: bool
    user: MeOut | None = None
    token_expires_at: datetime
