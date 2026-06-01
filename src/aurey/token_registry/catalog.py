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
        market_cap_rank=None,
        source="bundled",
        trust_tier="curated",
        verified_onchain=True,
        cg_recognized=True,
    )


def _merge_key(row: TokenRow) -> tuple[str, str]:
    return (row.chain_slug, row.address.lower())


def collect_allowlist_rows(
    *,
    repository: TokenRegistryRepository | None,
) -> list[TokenRow]:
    """Curated + indexed rows from JSON and optional DB (DB does not downgrade bundled curated)."""

    merged: dict[tuple[str, str], TokenRow] = {}
    for chain_slug, symbol, name, address in iter_catalog_tokens():
        merged[_merge_key(_row_from_bundled(chain_slug, symbol, name, address))] = _row_from_bundled(
            chain_slug, symbol, name, address
        )
    if repository is not None:
        for row in repository.list_allowlist_rows():
            if row.trust_tier not in _ALLOWLIST_TIERS:
                continue
            key = _merge_key(row)
            existing = merged.get(key)
            if existing is None:
                merged[key] = row
                continue
            if existing.trust_tier == "curated":
                continue
            if row.trust_tier == "curated":
                merged[key] = row
                continue
            merged[key] = row
    return list(merged.values())


def list_on_chain(
    *,
    repository: TokenRegistryRepository | None,
    chain_slug: str,
) -> list[TokenRow]:
    slug = chain_slug.strip().lower()
    rows = [r for r in collect_allowlist_rows(repository=repository) if r.chain_slug == slug]
    rows.sort(key=lambda r: (r.symbol.upper(), r.trust_tier != "curated", r.market_cap_rank or 10**9))
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
