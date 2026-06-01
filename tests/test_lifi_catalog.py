"""LiFi catalog import parsing."""

from __future__ import annotations

from aurey.token_registry.lifi_catalog import iter_lifi_catalog_entries, lifi_import_chain_ids


def test_iter_lifi_catalog_entries_evm_chains_only_in_chain_index():
    payload = {
        "tokens": {
            "1": [
                {
                    "address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
                    "symbol": "USDC",
                    "name": "USD Coin",
                    "decimals": 6,
                }
            ],
            "999": [{"address": "0x1111111111111111111111111111111111111111", "symbol": "X"}],
        }
    }
    rows = list(iter_lifi_catalog_entries(payload))
    assert len(rows) == 1
    assert rows[0].chain_slug == "ethereum"
    assert rows[0].ecosystem == "evm"
    assert rows[0].chain_id == 1


def test_lifi_import_includes_all_aurey_evm_chain_ids():
    assert 143 in lifi_import_chain_ids()  # monad
    assert 8453 in lifi_import_chain_ids()
