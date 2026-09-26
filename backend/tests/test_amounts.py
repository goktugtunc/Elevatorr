"""app.services.amounts — Decimal <-> i128 conversions and contract-mirroring math."""
from __future__ import annotations

import random
from decimal import Decimal

import pytest

from app.services.amounts import (
    I128_MAX,
    apply_bps,
    bps_floor,
    drawdown_floor,
    format_amount,
    from_stroops,
    min_out_for_slippage,
    quantize,
    settle_math,
    to_stroops,
)


def test_to_from_stroops_roundtrip():
    assert to_stroops(Decimal("1")) == 10_000_000
    assert to_stroops("0.0000001") == 1
    assert to_stroops(Decimal("123.4567891")) == 1_234_567_891
    assert from_stroops(1_234_567_891) == Decimal("123.4567891")
    assert from_stroops(5) == Decimal("0.0000005") and format_amount(from_stroops(5)) == "0.0000005"
    assert from_stroops(to_stroops(Decimal("42.5"))) == Decimal("42.5000000")
    assert to_stroops(Decimal("-1.5")) == -15_000_000  # pure conversion, sign preserved


def test_to_stroops_custom_decimals():
    assert to_stroops(Decimal("1.5"), decimals=6) == 1_500_000
    assert from_stroops(1_500_000, decimals=6) == Decimal("1.500000")


def test_to_stroops_rejects_precision_loss_and_garbage():
    with pytest.raises(ValueError):
        to_stroops(Decimal("1.00000001"))
    with pytest.raises(ValueError):
        to_stroops("abc")
    with pytest.raises(ValueError):
        to_stroops(Decimal("NaN"))
    with pytest.raises(ValueError):
        to_stroops(Decimal(I128_MAX))  # 2**127-1 whole tokens do not fit once scaled


def test_quantize_rounds_down():
    assert quantize(Decimal("1.99999999")) == Decimal("1.9999999")
    assert quantize("0.123456789") == Decimal("0.1234567")
    assert format_amount(Decimal("12.5")) == "12.5000000"
    assert format_amount(Decimal("1E+2")) == "100.0000000"


def test_bps_helpers_match_contract_floor_semantics():
    assert bps_floor(10_000, 2_500) == 2_500
    assert bps_floor(3, 3_333) == 0  # floor, not round
    assert apply_bps(Decimal("100"), 1_500) == Decimal("15.0000000")
    assert min_out_for_slippage(1_000_000, 100) == 990_000
    assert drawdown_floor(10_000_000, 2_000) == 8_000_000
    assert drawdown_floor(10_000_000, 10_000) == 0  # 10000 = disabled
    with pytest.raises(ValueError):
        bps_floor(1, 10_001)


def test_settle_math_examples():
    # profit: principal 1000, final 1200, commission 20%, platform 1%
    s = settle_math(to_stroops("1200"), to_stroops("1000"), 2_000, 100)
    assert from_stroops(s.profit) == Decimal("200.0000000")
    assert from_stroops(s.trader_fee) == Decimal("40.0000000")
    assert from_stroops(s.platform_fee) == Decimal("2.0000000")
    assert from_stroops(s.customer_payout) == Decimal("1158.0000000")
    # loss: no fees, customer takes everything left
    s = settle_math(to_stroops("800"), to_stroops("1000"), 2_000, 100)
    assert s.profit == 0 and s.trader_fee == 0 and s.platform_fee == 0
    assert s.customer_payout == to_stroops("800")
    # zero
    s = settle_math(0, to_stroops("1000"), 5_000, 1_000)
    assert s.customer_payout == 0


def test_settle_math_invariants_property():
    """DESIGN §1.7: customer_payout + fees == final_value and fees <= profit, for random inputs."""
    rng = random.Random(1234)
    for _ in range(2_000):
        principal = rng.randint(0, 10**18)
        final_value = rng.randint(0, 2 * 10**18)
        commission = rng.randint(0, 5_000)
        platform = rng.randint(0, 1_000)
        s = settle_math(final_value, principal, commission, platform)
        assert s.customer_payout + s.trader_fee + s.platform_fee == final_value
        assert s.trader_fee + s.platform_fee <= s.profit
        assert s.profit == max(0, final_value - principal)
        assert s.customer_payout >= 0
