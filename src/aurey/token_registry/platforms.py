"""CoinGecko ``platform_id`` <-> Aurey chain slug (EVM chains in :mod:`aurey.graphs.chains`)."""

from __future__ import annotations

from aurey.graphs.chains import CHAIN_INDEX

# CoinGecko asset platform id -> Aurey slug
CG_PLATFORM_TO_SLUG: dict[str, str] = {
    "ethereum": "ethereum",
    "base": "base",
    "arbitrum-one": "arbitrum",
    "optimistic-ethereum": "optimism",
    "polygon-pos": "polygon",
    "binance-smart-chain": "bsc",
    "avalanche": "avalanche",
    "gnosis": "gnosis",
    "linea": "linea",
    "scroll": "scroll",
}

SLUG_TO_CG_PLATFORM: dict[str, str] = {v: k for k, v in CG_PLATFORM_TO_SLUG.items()}


def chain_slug_for_cg_platform(platform_id: str) -> str | None:
    slug = CG_PLATFORM_TO_SLUG.get(platform_id.strip())
    if slug is None:
        return None
    return slug if slug in CHAIN_INDEX else None


def cg_platform_for_chain_slug(chain_slug: str) -> str | None:
    key = chain_slug.strip().lower()
    if key not in CHAIN_INDEX:
        return None
    return SLUG_TO_CG_PLATFORM.get(key)
