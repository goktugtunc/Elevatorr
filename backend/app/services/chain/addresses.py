"""EVM address helpers (03-backend-tasarim §1.4, rule K11).

DB stores ``normalize()`` (lower-case); API output and SIWE messages use ``checksum()`` (EIP-55); comparisons go
through ``same()``. Mixed-case input is accepted only when its checksum is valid.
"""
from __future__ import annotations

import re

from eth_utils import is_checksum_address, to_checksum_address

from app.services.chain.errors import InvalidAddressError

ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
ZERO_ADDRESS = "0x" + "00" * 20


def _is_mixed_case(hex_part: str) -> bool:
    letters = [c for c in hex_part if c.isalpha()]
    return bool(letters) and not (all(c.islower() for c in letters) or all(c.isupper() for c in letters))


def is_evm_address(value: object) -> bool:
    """``0x`` + 40 hex; when mixed-case, the EIP-55 checksum must also be valid."""
    if not isinstance(value, str):
        return False
    v = value.strip()
    if not ADDRESS_RE.match(v):
        return False
    if _is_mixed_case(v[2:]):
        return is_checksum_address(v)
    return True


def normalize(value: str) -> str:
    """Strip + validate; returns the lower-case form. Raises ``InvalidAddressError`` (422 ``invalid_address``)."""
    if not is_evm_address(value):
        raise InvalidAddressError(value)
    return value.strip().lower()


def checksum(value: str) -> str:
    """EIP-55 checksum form (API output, SIWE message, ABI encoding)."""
    return to_checksum_address(normalize(value))


def same(a: str | None, b: str | None) -> bool:
    """Case-insensitive equality; None-safe (two Nones are not "same" addresses)."""
    if a is None or b is None:
        return False
    try:
        return normalize(a) == normalize(b)
    except InvalidAddressError:
        return False


def short(value: str) -> str:
    """``0x67aD…FF19`` for logs and notifications (falls back to the raw string when not an address)."""
    try:
        c = checksum(value)
    except InvalidAddressError:
        return str(value)
    return f"{c[:6]}…{c[-4:]}"


__all__ = ["ADDRESS_RE", "ZERO_ADDRESS", "checksum", "is_evm_address", "normalize", "same", "short"]
