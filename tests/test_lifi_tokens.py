"""LiFi /v1/tokens payload parsing for lifi_supported sync."""

from __future__ import annotations

from aurey.graphs.evm_codec import to_checksum_evm_address
from aurey.token_registry.lifi_tokens import build_lifi_address_set


def test_build_lifi_address_set_keeps_aurey_chains_only():
    usdc = to_checksum_evm_address("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")
    payload = {
        "tokens": {
            "1": [
                {"address": usdc},
                {"address": "0x0000000000000000000000000000000000000000"},
            ],
            "999999": [{"address": "0x1111111111111111111111111111111111111111"}],
            "8453": [{"address": "0x4200000000000000000000000000000000000006"}],
        }
    }
    s = build_lifi_address_set(payload)
    assert (1, usdc) in s
    assert (8453, to_checksum_evm_address("0x4200000000000000000000000000000000000006")) in s
    assert all(cid != 999999 for cid, _ in s)


def test_build_lifi_address_set_empty_when_missing_tokens_key():
    assert build_lifi_address_set({}) == set()
    assert build_lifi_address_set({"tokens": "nope"}) == set()
