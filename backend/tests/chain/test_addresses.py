"""app.services.chain.addresses — EVM address normalisation (K11)."""
from __future__ import annotations

import pytest

from app.services.chain.addresses import ZERO_ADDRESS, checksum, is_evm_address, normalize, same, short
from app.services.chain.errors import InvalidAddressError

CHECKSUMMED = "0x67aD0CaE18528017B79E0aF8C4D3dd5937caFF19"
LOWER = CHECKSUMMED.lower()
UPPER = "0x" + CHECKSUMMED[2:].upper()
BAD_CHECKSUM = "0x67ad0CaE18528017B79E0aF8C4D3dd5937caFF19"  # one letter flipped


def test_is_evm_address_accepts_lower_upper_and_valid_mixed_case():
    assert is_evm_address(LOWER)
    assert is_evm_address(UPPER)
    assert is_evm_address(CHECKSUMMED)
    assert is_evm_address(f"  {LOWER}  ")


@pytest.mark.parametrize(
    "value",
    [BAD_CHECKSUM, "0x1234", LOWER[2:], "G" * 56, "", None, 42, "0x" + "zz" * 20, ZERO_ADDRESS + "0"],
)
def test_is_evm_address_rejects_garbage_and_broken_checksum(value):
    assert not is_evm_address(value)


def test_normalize_lower_cases_and_strips():
    assert normalize(CHECKSUMMED) == LOWER
    assert normalize(UPPER) == LOWER
    assert normalize(f" {LOWER}\n") == LOWER


def test_normalize_rejects_broken_checksum_with_422_invalid_address():
    with pytest.raises(InvalidAddressError) as ei:
        normalize(BAD_CHECKSUM)
    assert ei.value.status_code == 422
    assert ei.value.code == "invalid_address"
    assert ei.value.details["value"] == BAD_CHECKSUM


def test_checksum_round_trips():
    assert checksum(LOWER) == CHECKSUMMED
    assert checksum(UPPER) == CHECKSUMMED
    assert checksum(CHECKSUMMED) == CHECKSUMMED
    with pytest.raises(InvalidAddressError):
        checksum("0x12")


def test_same_is_case_insensitive_and_none_safe():
    assert same(LOWER, CHECKSUMMED)
    assert same(UPPER, LOWER)
    assert not same(LOWER, ZERO_ADDRESS)
    assert not same(None, LOWER)
    assert not same(None, None)
    assert not same("garbage", LOWER)


def test_short_and_zero_address():
    assert short(LOWER) == "0x67aD…FF19"
    assert short("not-an-address") == "not-an-address"
    assert ZERO_ADDRESS == "0x" + "00" * 20 and is_evm_address(ZERO_ADDRESS)
