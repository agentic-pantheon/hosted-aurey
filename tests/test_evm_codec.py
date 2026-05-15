"""evm_codec helpers."""

from __future__ import annotations

import pytest

from aurey.graphs.evm_codec import (
    ERC20_DECIMALS_CALLDATA,
    decode_abi_uint256_word,
    format_token_units,
    parse_evm_uint,
)


def test_erc20_decimals_calldata_selector():
    assert ERC20_DECIMALS_CALLDATA == "0x313ce567"


def test_decode_abi_uint256_word():
    word = "0x0000000000000000000000000000000000000000000000000000000000000006"
    assert decode_abi_uint256_word(word) == 6
    with pytest.raises(ValueError):
        decode_abi_uint256_word("0x01")
    with pytest.raises(ValueError):
        decode_abi_uint256_word("")


def test_parse_evm_uint_accepts_hex_and_decimal():
    assert (
        parse_evm_uint("0x0000000000000000000000000000000000000000000000000000000000000f569")
        == 62825
    )
    assert parse_evm_uint("62825") == 62825
    assert parse_evm_uint(62825) == 62825
    with pytest.raises(ValueError):
        parse_evm_uint("")
    with pytest.raises(ValueError):
        parse_evm_uint("-1")


def test_format_token_units_exact_decimal_string():
    assert format_token_units(62825, 6) == "0.062825"
    assert format_token_units(1000000000000000000, 18) == "1"
    assert format_token_units(123400, 4) == "12.34"
    assert format_token_units(123, 0) == "123"
