"""Allowlist catalog listing (per-chain and grouped by symbol)."""

from __future__ import annotations

from aurey.known_addresses.book import load_known_addresses
from aurey.token_registry.catalog import list_grouped_by_symbol, list_on_chain


def test_list_on_chain_polygon_empty_without_db():
    rows = list_on_chain(repository=None, chain_slug="polygon")
    assert rows == []


def test_list_on_chain_base_from_json():
    rows = list_on_chain(repository=None, chain_slug="base")
    symbols = {r.symbol for r in rows}
    assert "USDC" in symbols
    assert "WETH" in symbols
    assert all(r.trust_tier == "curated" for r in rows)


def test_list_grouped_includes_symbol_across_chains():
    grouped = list_grouped_by_symbol(repository=None)
    assert "USDC" in grouped
    chains = {r.chain_slug for r in grouped["USDC"]}
    assert "ethereum" in chains
    assert "base" in chains


def test_bundled_catalog_token_count():
    n = sum(1 for _ in list_grouped_by_symbol(repository=None).values())
    assert n >= 19
    _ = load_known_addresses()
