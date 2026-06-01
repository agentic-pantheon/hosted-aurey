"""LiFi file import helpers."""

from __future__ import annotations

from aurey.token_registry.lifi_import import iter_monad_tokens_from_lifi_payload


def test_iter_monad_tokens_from_lifi_payload():
    payload = {
        "tokens": {
            "143": [
                {
                    "address": "0x754704Bc059F8C6701E684b4d1C1b431bAE2E2E2",
                    "symbol": "usdc",
                    "name": "USD Coin",
                    "decimals": 6,
                },
                {"address": "not-an-address", "symbol": "BAD"},
            ],
            "1": [{"address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "symbol": "USDC"}],
        }
    }
    rows = list(iter_monad_tokens_from_lifi_payload(payload))
    assert len(rows) == 1
    assert rows[0].chain_slug == "monad"
    assert rows[0].chain_id == 143
    assert rows[0].symbol == "USDC"
    assert rows[0].decimals == 6
