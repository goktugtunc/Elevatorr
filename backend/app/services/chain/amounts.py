"""Money helpers: Decimal (human units) in the API/DB  <->  uint256 raw units on-chain (03-backend-tasarim §1.5).

Rules: never float; ROUND_DOWN only; ``decimals`` is always passed explicitly (there is no default — the token's
``asset.decimals`` decides). ``settle_math`` mirrors ``SettleMath.sol`` (01-kontrat-spec §7) in integer arithmetic.
Replaces the Stellar-era ``app/services/amounts.py`` (i128 / 7 decimals / stroops).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, InvalidOperation

from app.services.chain.errors import AmountError

UINT256_MAX = 2**256 - 1
BPS_DENOM = 10_000
ZERO = Decimal(0)
MAX_DECIMALS = 18
MON_DECIMALS = 18


def unit(decimals: int) -> Decimal:
    """Smallest representable amount: ``Decimal(1).scaleb(-decimals)``."""
    _check_decimals(decimals)
    return Decimal(1).scaleb(-decimals)


def _check_decimals(decimals: int) -> None:
    if not isinstance(decimals, int) or isinstance(decimals, bool) or decimals < 0 or decimals > MAX_DECIMALS:
        raise ValueError(f"decimals must be an int in 0..{MAX_DECIMALS}, got {decimals!r}")


def _to_decimal(amount: Decimal | int | str) -> Decimal:
    if isinstance(amount, float):
        raise AmountError("float amounts are not accepted", details={"value": repr(amount)})
    try:
        d = Decimal(str(amount).strip()) if isinstance(amount, str) else Decimal(amount)
    except (InvalidOperation, ValueError, TypeError) as e:
        raise AmountError(f"not a valid amount: {amount!r}", details={"value": str(amount)}) from e
    if not d.is_finite():
        raise AmountError(f"not a finite amount: {amount!r}", details={"value": str(amount)})
    return d


def quantize(amount: Decimal | int | str, decimals: int) -> Decimal:
    """Round DOWN to ``decimals`` places (the only rounding mode money may use here)."""
    return _to_decimal(amount).quantize(unit(decimals), rounding=ROUND_DOWN)


def to_raw(amount: Decimal | int | str, decimals: int) -> int:
    """Decimal token amount -> raw integer units (uint256).

    Strict: more precision than ``decimals`` -> ``AmountError(code="too_many_decimals")``; negative or above
    ``UINT256_MAX`` -> ``AmountError(code="invalid_amount")``.
    """
    d = _to_decimal(amount)
    _check_decimals(decimals)
    scaled = d.scaleb(decimals)
    if scaled != scaled.to_integral_value():
        raise AmountError(
            f"{d} has more than {decimals} decimal places",
            code="too_many_decimals",
            details={"value": format(d, "f"), "decimals": decimals},
        )
    raw = int(scaled)
    if raw < 0 or raw > UINT256_MAX:
        raise AmountError(f"{d} does not fit into uint256 with {decimals} decimals", details={"value": format(d, "f")})
    return raw


def from_raw(raw: int, decimals: int) -> Decimal:
    """Raw integer units -> Decimal token amount with exactly ``decimals`` places."""
    _check_decimals(decimals)
    return (Decimal(int(raw)) * unit(decimals)).quantize(unit(decimals), rounding=ROUND_DOWN)


def parse_amount(value: str | Decimal, decimals: int) -> Decimal:
    """API input: strictly positive, at most ``decimals`` fractional digits (422 ``too_many_decimals`` otherwise)."""
    d = _to_decimal(value)
    if d <= 0:
        raise AmountError("amount must be positive", details={"value": format(d, "f")})
    to_raw(d, decimals)  # validates precision and the uint256 bound
    return quantize(d, decimals)


def format_amount(amount: Decimal | int | str, decimals: int | None = None) -> str:
    """Plain (non-scientific) string with trailing zeros stripped: ``"1000"``, ``"0.000001"``, ``"0"``.

    When ``decimals`` is given the value is first quantized ROUND_DOWN to that many places.
    """
    d = _to_decimal(amount)
    if decimals is not None:
        d = d.quantize(unit(decimals), rounding=ROUND_DOWN)
    if d == 0:
        return "0"
    s = format(d.normalize(), "f")
    return s


def bps_floor(raw: int, bps: int) -> int:
    """``raw × bps / 10000`` floored, as ``SettleMath.bpsOf`` does."""
    if bps < 0 or bps > BPS_DENOM:
        raise ValueError(f"bps out of range: {bps}")
    return (int(raw) * int(bps)) // BPS_DENOM


def min_out_for_slippage(quoted_out: int, slippage_bps: int) -> int:
    """``quote × (10000 − slippage) / 10000`` floored — the ``minOut`` handed to ``trade`` / ``settle``."""
    if slippage_bps < 0 or slippage_bps > BPS_DENOM:
        raise ValueError(f"slippage_bps out of range: {slippage_bps}")
    return (int(quoted_out) * (BPS_DENOM - int(slippage_bps))) // BPS_DENOM


def drawdown_floor(principal: int, max_drawdown_bps: int) -> int:
    """``SettleMath.drawdownFloor``: ``principal × (10000 − dd) / 10000``; the vault rejects trades below it."""
    if max_drawdown_bps < 0 or max_drawdown_bps > BPS_DENOM:
        raise ValueError(f"max_drawdown_bps out of range: {max_drawdown_bps}")
    return (int(principal) * (BPS_DENOM - int(max_drawdown_bps))) // BPS_DENOM


@dataclass(frozen=True)
class Settlement:
    """All values in raw integer units (uint256)."""

    final_value: int
    profit: int
    trader_fee: int
    platform_fee: int
    customer_payout: int


def settle_math(final_value: int, principal: int, commission_bps: int, platform_fee_bps: int) -> Settlement:
    """``SettleMath.settlement`` (01-kontrat-spec §7) — floor at every step, fees only on profit:

        profit          = finalValue > principal ? finalValue − principal : 0
        traderFee       = bpsOf(profit, commissionBps)
        platformFee     = bpsOf(profit, platformFeeBps)
        customerPayout  = finalValue − traderFee − platformFee
    """
    final_value = int(final_value)
    principal = int(principal)
    if final_value < 0 or principal < 0:
        raise ValueError("amounts must be non-negative")
    if final_value > UINT256_MAX or principal > UINT256_MAX:
        raise OverflowError("uint256 overflow in settlement math")
    profit = final_value - principal if final_value > principal else 0
    trader_fee = bps_floor(profit, commission_bps)
    platform_fee = bps_floor(profit, platform_fee_bps)
    customer_payout = final_value - trader_fee - platform_fee
    if customer_payout < 0:
        raise OverflowError("uint256 underflow in settlement math (fees exceed final value)")
    return Settlement(final_value, profit, trader_fee, platform_fee, customer_payout)


def wei_to_mon(wei: int) -> Decimal:
    """Native MON: wei -> Decimal (18 places)."""
    return from_raw(wei, MON_DECIMALS)


def mon_to_wei(amount: Decimal | int | str) -> int:
    """Native MON: Decimal -> wei."""
    return to_raw(amount, MON_DECIMALS)


__all__ = [
    "BPS_DENOM",
    "MAX_DECIMALS",
    "MON_DECIMALS",
    "UINT256_MAX",
    "ZERO",
    "AmountError",
    "Settlement",
    "bps_floor",
    "drawdown_floor",
    "format_amount",
    "from_raw",
    "min_out_for_slippage",
    "mon_to_wei",
    "parse_amount",
    "quantize",
    "settle_math",
    "to_raw",
    "unit",
    "wei_to_mon",
]
