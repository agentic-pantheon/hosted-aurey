"""Bundled ``known_addresses.json`` catalog."""

from __future__ import annotations

from aurey.known_addresses.book import lookup_known_token, reload_known_addresses_for_tests


def test_lookup_usdc_ethereum():
    hit = lookup_known_token("ethereum", "usdc")
    assert hit is not None
    assert hit.symbol == "USDC"
    assert hit.address == "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
    assert hit.name == "USD Coin"


def test_lookup_usdc_base():
    hit = lookup_known_token("base", "usdc")
    assert hit is not None
    assert hit.address == "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"


def test_unknown_ticker():
    assert lookup_known_token("ethereum", "not_a_real_coin_xyz") is None


def test_reload_clears_cache():
    reload_known_addresses_for_tests()
