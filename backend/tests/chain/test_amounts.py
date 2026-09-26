"""app.services.chain.amounts — Decimal <-> uint256 raw units and SettleMath-mirroring arithmetic (K12)."""
from __future__ import annotations

import random
from decimal import Decimal

import pytest

from app.services.chain.amounts import (
    UINT256_MAX,
    AmountError,
    bps_floor,
    drawdown_floor,
    format_amount,
    from_raw,
    min_out_for_slippage,
    mon_to_wei,
    parse_amount,
    quantize,
    settle_math,
    to_raw,
    unit,
    wei_to_mon,
)


@pytest.mark.parametrize(
    ("amount", "decimals", "raw"),
    [
        ("1", 6, 1_000_000),
        ("0.000001", 6, 1),
        ("123.456789", 6, 123_456_789),
        ("1", 8, 100_000_000),
        ("0.00000001", 8, 1),
        ("1", 18, 10**18),
        ("0.000000000000000001", 18, 1),
        ("3000.5", 18, 3_000_500_000_000_000_000_000),
        (Decimal("42.5"), 6, 42_500_000),
        (7, 0, 7),
        ("0", 18, 0),
    ],
)
def test_to_raw_from_raw_roundtrip(amount, decimals, raw):
    assert to_raw(amount, decimals) == raw
    back = from_raw(raw, decimals)
    assert back == Decimal(str(amount))
    assert back.as_tuple().exponent == -decimals  # exactly `decimals` places


def test_to_raw_rejects_too_many_decimals_with_code():
    with pytest.raises(AmountError) as ei:
        to_raw("1.0000001", 6)
    assert ei.value.code == "too_many_decimals"
    assert ei.value.status_code == 422
    with pytest.raises(AmountError):
        to_raw("0.000000000000000000001", 18)


def test_to_raw_uint256_bounds():
    # NOTE: values with more than 28 significant digits (e.g. UINT256_MAX itself) hit the default Decimal context
    # precision inside amounts.py (Dalga 0); up to 28 digits everything is exact.
    assert to_raw(from_raw(10**26, 18), 18) == 10**26
    assert to_raw(Decimal(10**27), 0) == 10**27
    with pytest.raises(AmountError) as ei:
        to_raw(Decimal(10**78), 0)  # > UINT256_MAX
    assert ei.value.code == "invalid_amount"
    with pytest.raises(AmountError):
        to_raw("-1", 6)


@pytest.mark.parametrize("bad", ["abc", "NaN", "Infinity", 1.5, ""])
def test_to_raw_rejects_garbage(bad):
    with pytest.raises(AmountError):
        to_raw(bad, 6)


def test_decimals_must_be_explicit_and_bounded():
    with pytest.raises(TypeError):
        to_raw("1")  # type: ignore[call-arg]  # no default decimals
    with pytest.raises(ValueError):
        unit(19)
    with pytest.raises(ValueError):
        from_raw(1, -1)


def test_quantize_rounds_down():
    assert quantize("1.99999999", 6) == Decimal("1.999999")
    assert quantize("0.123456789", 8) == Decimal("0.12345678")
    assert quantize(Decimal("1E+2"), 6) == Decimal("100.000000")


def test_parse_amount_requires_positive_and_precision():
    assert parse_amount("10.5", 6) == Decimal("10.500000")
    with pytest.raises(AmountError):
        parse_amount("0", 6)
    with pytest.raises(AmountError):
        parse_amount("-3", 6)
    with pytest.raises(AmountError) as ei:
        parse_amount("1.1234567", 6)
    assert ei.value.code == "too_many_decimals"


def test_format_amount_is_plain_and_trimmed():
    assert format_amount(Decimal("1000")) == "1000"
    assert format_amount(Decimal("1000.000000")) == "1000"
    assert format_amount(from_raw(1, 6)) == "0.000001"
    assert format_amount(Decimal("1E+3")) == "1000"
    assert format_amount(Decimal("0E-18")) == "0"
    assert format_amount("12.3456789", 6) == "12.345678"
    assert format_amount(from_raw(1, 18)) == "0.000000000000000001"


def test_bps_helpers_match_settle_math_floor_semantics():
    assert bps_floor(10_000, 2_500) == 2_500
    assert bps_floor(999, 2_500) == 249  # floor, not round (01-spec §7)
    assert bps_floor(3, 3_333) == 0
    assert min_out_for_slippage(1_000_000, 100) == 990_000
    assert drawdown_floor(10_000_000, 2_000) == 8_000_000
    assert drawdown_floor(10_000_000, 10_000) == 0  # 10000 = disabled
    assert drawdown_floor(10**24, 100) == 10**24 * 99 // 100
    with pytest.raises(ValueError):
        bps_floor(1, 10_001)
    with pytest.raises(ValueError):
        drawdown_floor(1, 10_001)
    with pytest.raises(ValueError):
        min_out_for_slippage(1, -1)


def test_settle_math_examples():
    # profit: principal 1000, final 1200, commission 20%, platform 1% (6-decimal base token)
    s = settle_math(to_raw("1200", 6), to_raw("1000", 6), 2_000, 100)
    assert from_raw(s.profit, 6) == Decimal("200.000000")
    assert from_raw(s.trader_fee, 6) == Decimal("40.000000")
    assert from_raw(s.platform_fee, 6) == Decimal("2.000000")
    assert from_raw(s.customer_payout, 6) == Decimal("1158.000000")
    # loss: no fees, the customer takes everything left
    s = settle_math(to_raw("800", 6), to_raw("1000", 6), 2_000, 100)
    assert s.profit == 0 and s.trader_fee == 0 and s.platform_fee == 0
    assert s.customer_payout == to_raw("800", 6)
    # zero
    s = settle_math(0, to_raw("1000", 6), 5_000, 1_000)
    assert s.customer_payout == 0
    # 18-decimal values near the top of the range still work in integer arithmetic
    s = settle_math(10**40, 10**39, 5_000, 1_000)
    assert s.customer_payout + s.trader_fee + s.platform_fee == 10**40


def test_settle_math_rejects_invalid_inputs():
    with pytest.raises(ValueError):
        settle_math(-1, 0, 0, 0)
    with pytest.raises(OverflowError):
        settle_math(UINT256_MAX + 1, 0, 0, 0)


def test_settle_math_invariants_property():
    """01-spec §7 fuzz invariants for random inputs in the contract's bounds."""
    rng = random.Random(1234)
    for _ in range(3_000):
        principal = rng.randint(0, 10**40)
        final_value = rng.randint(0, 10**40)
        commission = rng.randint(0, 5_000)
        platform = rng.randint(0, 1_000)
        s = settle_math(final_value, principal, commission, platform)
        assert s.customer_payout + s.trader_fee + s.platform_fee == final_value
        assert s.trader_fee + s.platform_fee <= s.profit
        assert s.profit == max(0, final_value - principal)
        assert s.trader_fee == s.profit * commission // 10_000
        assert s.platform_fee == s.profit * platform // 10_000
        if final_value <= principal:
            assert s.customer_payout == final_value and s.trader_fee == 0 and s.platform_fee == 0
        else:
            assert s.customer_payout >= principal


def test_mon_helpers_are_18_decimals():
    assert mon_to_wei("1.5") == 1_500_000_000_000_000_000
    assert wei_to_mon(10**18) == Decimal("1.000000000000000000")
    assert format_amount(wei_to_mon(52_000_000_000)) == "0.000000052"
