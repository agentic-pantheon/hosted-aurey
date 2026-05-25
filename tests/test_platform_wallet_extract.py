"""Parsing helpers for Ethereum addresses in Platform bootstrap / signing-keys JSON."""

from __future__ import annotations

from aurey.cloud.platform_client import (
    ethereum_address_from_signing_keys_payload,
    extract_ethereum_address_from_signing_key_items,
    extract_ethereum_wallet_address_from_bootstrap_payload,
    list_signing_key_address_lines_from_payload,
)


def test_bootstrap_extracts_summary_signing_keys_ethereum() -> None:
    payload = {
        "summary": {
            "signing_keys": [
                {
                    "chain": "ethereum",
                    "address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                }
            ]
        },
        "claim_url": "https://claim/x",
    }
    addr = extract_ethereum_wallet_address_from_bootstrap_payload(payload)
    assert addr is not None
    assert addr.startswith("0x")


def test_signing_keys_endpoint_parse() -> None:
    body = {
        "keys": [
            {"chain": "Ethereum", "address": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},
            {"chain": "solana", "address": "SoMe"},
        ]
    }
    addr = ethereum_address_from_signing_keys_payload(body)
    assert addr is not None
    assert addr.startswith("0x")


def test_signing_keys_items_fallback_first_mapping() -> None:
    """Without chain discriminator, fall back to first mapping with a plausible address."""

    items = [{"address": "0xdddddddddddddddddddddddddddddddddddddddd"}]
    addr = extract_ethereum_address_from_signing_key_items(items)
    assert addr is not None


def test_list_signing_key_address_lines_includes_all_chains() -> None:
    body = {
        "keys": [
            {"chain": "Ethereum", "address": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},
            {"chain": "solana", "address": "SoMeSolanaPubkey"},
        ]
    }
    lines = list_signing_key_address_lines_from_payload(body)
    assert len(lines) == 2
    assert any(line.startswith("Ethereum:") for line in lines)
    assert any(line.startswith("solana:") for line in lines)
