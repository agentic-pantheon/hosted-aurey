"""Merge bundled JSON + DB allowlist rows for support listings."""

from __future__ import annotations

from collections import defaultdict

from aurey.graphs.chains import chain_id_for, chain_info
from aurey.known_addresses.book import iter_catalog_tokens
from aurey.token_registry.repository import TokenRegistryRepository, TokenRow

_ALLOWLIST_TIERS = frozenset({"curated", "indexed"})


def _row_from_bundled(chain_slug: str, symbol: str, name: str, address: str) -> TokenRow:
    return TokenRow(
        chain_slug=chain_slug,
        chain_id=chain_id_for(chain_slug),
        symbol=symbol.strip().upper()[:32],
        name=name,
        address=address,
        decimals=None,
        coingecko_id=None,
        source="bundled",
        trust_tier="curated",
        verified_onchain=True,
        cg_recognized=True,
    )


def _merge_key(row: TokenRow) -> tuple[str, str]:
    if row.ecosystem == "solana" or row.chain_slug == "solana":
        return (row.chain_slug, row.address)
    return (row.chain_slug, row.address.lower())


def collect_allowlist_rows(
    *,
    repository: TokenRegistryRepository | None,
    chain_slug: str | None = None,
) -> list[TokenRow]:
    """Allowlist rows from DB (LiFi catalog) when connected; else bundled JSON fallback."""

    if repository is not None:
        return [
            r
            for r in repository.list_allowlist_rows(chain_slug=chain_slug)
            if r.trust_tier in _ALLOWLIST_TIERS
        ]

    merged: dict[tuple[str, str], TokenRow] = {}
    slug_filter = chain_slug.strip().lower() if chain_slug else None
    for chain_slug_row, symbol, name, address in iter_catalog_tokens():
        if slug_filter is not None and chain_slug_row != slug_filter:
            continue
        merged[_merge_key(_row_from_bundled(chain_slug_row, symbol, name, address))] = _row_from_bundled(
            chain_slug_row, symbol, name, address
        )
    return list(merged.values())


def list_on_chain(
    *,
    repository: TokenRegistryRepository | None,
    chain_slug: str,
) -> list[TokenRow]:
    slug = chain_slug.strip().lower()
    rows = collect_allowlist_rows(repository=repository, chain_slug=slug)
    rows.sort(key=lambda r: (r.symbol.upper(), r.trust_tier != "curated"))
    return rows


def list_grouped_by_symbol(
    *,
    repository: TokenRegistryRepository | None,
) -> dict[str, list[TokenRow]]:
    by_sym: dict[str, list[TokenRow]] = defaultdict(list)
    for row in collect_allowlist_rows(repository=repository):
        by_sym[row.symbol.upper()].append(row)
    for sym in by_sym:
        by_sym[sym].sort(key=lambda r: (r.chain_slug, r.trust_tier != "curated"))
    return dict(sorted(by_sym.items(), key=lambda kv: kv[0]))
