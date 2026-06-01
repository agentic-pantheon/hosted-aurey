"""Bundled catalog and seed JSON for token_registry seed job."""

from __future__ import annotations

from aurey.known_addresses.book import iter_catalog_tokens, load_known_addresses
from aurey.token_registry.seed_bundle import iter_seed_tokens, load_token_registry_seed


def test_token_registry_seed_bundle_row_counts():
    doc = load_token_registry_seed()
    rows = list(iter_seed_tokens(doc))
    assert len(rows) == 266
    curated = [r for r in rows if r.trust_tier == "curated"]
    indexed = [r for r in rows if r.trust_tier == "indexed"]
    assert len(curated) == 48
    assert len(indexed) == 218
    assert all(r.coingecko_id for r in indexed)


def test_iter_catalog_tokens_includes_all_chains_and_names():
    doc = load_known_addresses()
    rows = list(iter_catalog_tokens(doc))
    assert len(rows) == 48
    slugs = {r[0] for r in rows}
    assert "optimism" in slugs
    assert "ethereum" in slugs
    assert "monad" not in slugs or len([r for r in rows if r[0] == "monad"]) == 0

    usdc_eth = next(r for r in rows if r[0] == "ethereum" and r[1] == "USDC")
    assert usdc_eth[2] == "USD Coin"
    assert usdc_eth[3].startswith("0x")
    assert len(usdc_eth[3]) == 42
