"""Bundled catalog iteration for token_registry seed."""

from __future__ import annotations

from aurey.known_addresses.book import iter_catalog_tokens, load_known_addresses


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
