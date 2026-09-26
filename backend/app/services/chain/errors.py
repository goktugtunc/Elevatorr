"""Chain-specific error hierarchy (03-backend-tasarim §1.3). HTTP mapping is done in ``app.main``.

``VaultError`` (IntEnum, codes 1–30) lives in ``app.services.chain.abi`` (Dalga 1a); this module does not import it
so that the error types stay importable without the ABI machinery.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.core.errors import AppError, ChainError, ConflictError, ValidationError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.chain.types import RevertInfo


class ChainUnavailableError(ChainError):
    """RPC unreachable / timeout after the retry budget. ``details={"rpc": ..., "attempts": 3}``."""

    def __init__(self, message: str = "chain RPC unavailable", *, rpc: str | None = None, attempts: int | None = None,
                 details: dict[str, Any] | None = None):
        d: dict[str, Any] = dict(details or {})
        if rpc is not None:
            d.setdefault("rpc", rpc)
        if attempts is not None:
            d.setdefault("attempts", attempts)
        super().__init__(message, details=d)


class RpcRateLimitedError(ChainError):
    """RPC answered 429 / ``-32005`` and still does after backoff."""

    def __init__(self, message: str = "chain RPC rate limited", *, details: dict[str, Any] | None = None):
        super().__init__(message, details=details)


class ContractRevertError(AppError):
    """``estimateGas`` / ``eth_call`` reverted.

    ``error`` is the decoded ``VaultError`` (IntEnum from ``abi.py``) or None; ``revert`` is the decoded revert
    payload; ``error_code`` is the API string (``"vault:NotFound"``, ``"erc20:..."``, ``"reverted"``) per 02-api §3.1.
    """

    status_code = 400
    code = "contract_error"

    def __init__(self, message: str, *, revert: RevertInfo, error: Any | None = None,
                 error_code: str | None = None, details: dict[str, Any] | None = None):
        self.revert = revert
        self.error = error
        if error_code is None:
            if error is not None:
                error_code = f"vault:{getattr(error, 'name', error)}"
            elif revert.name:
                error_code = f"vault:{revert.name}" if revert.code is not None else revert.name
            else:
                error_code = "reverted"
        self.error_code = error_code
        d: dict[str, Any] = {"error_code": error_code, "contract_error_code": revert.code}
        if details:
            d.update(details)
        super().__init__(message, details=d)


class TxNotFoundError(ChainError):
    """Hash unknown to the network (internal use only; the API reports ``not_included``)."""

    status_code = 404
    code = "tx_not_found"

    def __init__(self, tx_hash: str, message: str | None = None):
        self.tx_hash = tx_hash
        super().__init__(message or f"transaction not found: {tx_hash}", details={"tx_hash": tx_hash})


class InvalidAddressError(ValidationError):
    """Not a ``0x`` + 40 hex address, or mixed-case with a broken EIP-55 checksum."""

    code = "invalid_address"

    def __init__(self, value: object, message: str | None = None):
        super().__init__(message or f"invalid address: {value!r}", details={"value": str(value)})


class AmountError(ValidationError):
    """``to_raw`` / ``parse_amount`` failure; ``code`` is ``invalid_amount`` or ``too_many_decimals``."""

    code = "invalid_amount"

    def __init__(self, message: str, *, code: str = "invalid_amount", details: dict[str, Any] | None = None):
        super().__init__(message, code=code, details=details)


class ReceiptMismatchError(ConflictError):
    """``POST /tx/submit`` receipt verification failed (03 §4.3): from/to/input/value differ from the pending row."""

    code = "receipt_mismatch"


class FaucetDisabledError(AppError):
    """``minter_private_key`` is not configured."""

    status_code = 503
    code = "faucet_disabled"

    def __init__(self, message: str = "faucet is disabled", *, details: dict[str, Any] | None = None):
        super().__init__(message, details=details)


__all__ = [
    "AmountError",
    "ChainError",
    "ChainUnavailableError",
    "ContractRevertError",
    "FaucetDisabledError",
    "InvalidAddressError",
    "ReceiptMismatchError",
    "RpcRateLimitedError",
    "TxNotFoundError",
]
