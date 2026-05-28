"""Whether a portfolio row matches ``known_addresses.json`` (curated / verified)."""

from __future__ import annotations

from aurey.graphs.evm_codec import normalize_evm_address
from aurey.known_addresses.book import (
    chain_slug_in_known_catalog,
    lookup_known_token,
    lookup_known_token_by_address,
)


def is_curated_portfolio_token(
    chain: str,
    *,
    symbol: str | None,
    token_address: str | None,
) -> bool:
    """Native gas on a catalog chain, or ERC-20 contract listed in the bundled JSON."""

    slug = chain.strip().lower()
    if not slug or slug == "unknown":
        return False
    addr_raw = (token_address or "").strip()
    sym = (symbol or "").strip()
    if not addr_raw:
        if sym and lookup_known_token(slug, sym) is not None:
            return True
        return chain_slug_in_known_catalog(slug)

    try:
        normalize_evm_address(addr_raw)
    except ValueError:
        return False
    return lookup_known_token_by_address(slug, addr_raw) is not None


__all__ = ["is_curated_portfolio_token"]
