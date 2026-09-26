"""Shared Pydantic building blocks (03-backend-tasarim §11.1; amount/address rules in 02-api-sozlesme)."""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, Generic, TypeVar

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, PlainSerializer

from app.services.chain.addresses import checksum, normalize


def _dec_to_str(v: Decimal) -> str:
    """Plain decimal string without exponent or trailing zeros: "1000", "0.000001", "0" (never "0E-7")."""
    d = Decimal(v)
    if d == 0:
        return "0"
    return format(d.normalize(), "f")


# Decimal serialized as a plain string so mobile clients never see float noise.
Amount = Annotated[Decimal, PlainSerializer(_dec_to_str, return_type=str, when_used="json")]
Units = Annotated[Decimal, PlainSerializer(_dec_to_str, return_type=str, when_used="json")]
Pct = Annotated[Decimal, PlainSerializer(_dec_to_str, return_type=str, when_used="json")]

# Input amount (K12): positive, at most 18 decimals / 60 digits; per-asset `decimals` is checked in the services.
AmountIn = Annotated[Decimal, Field(gt=0, decimal_places=18, max_digits=60)]
PctIn = Annotated[Decimal, Field(ge=0, le=100, decimal_places=2, max_digits=5)]

# Addresses (K11): input accepts lower-case or valid EIP-55, stored lower-case; output is always EIP-55 checksum.
Address = Annotated[str, AfterValidator(normalize)]
AddressOut = Annotated[str, PlainSerializer(checksum, return_type=str, when_used="json")]

# Transaction hash: 0x + 64 hex (66 chars).
TxHash = Annotated[str, Field(pattern=r"^0x[0-9a-fA-F]{64}$")]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class PageParams(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class Message(BaseModel):
    message: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict = {}


class IdResponse(BaseModel):
    id: uuid.UUID
