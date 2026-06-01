"""Load ``data/token_registry_seed.json`` for the registry seed job."""

from __future__ import annotations

import json
from collections.abc import Iterator
from functools import lru_cache
from importlib.resources import files
from typing import Any, NamedTuple, cast


class TokenRegistrySeedEntry(NamedTuple):
    chain_slug: str
    symbol: str
    name: str
    address: str
    decimals: int | None
    coingecko_id: str | None
    market_cap_rank: int | None
    source: str
    trust_tier: str


def reload_token_registry_seed_for_tests() -> None:
    """Clear the in-process JSON cache (tests only)."""

    _cached_seed_document.cache_clear()


@lru_cache(maxsize=1)
def _cached_seed_document() -> dict[str, Any]:
    raw: Any = json.loads(
        files("aurey.data")
        .joinpath("token_registry_seed.json")
        .read_text(encoding="utf-8")
    )
    doc = cast(dict[str, Any], raw)
    _validate_seed_document(doc)
    return doc


def load_token_registry_seed() -> dict[str, Any]:
    """Return the parsed seed document (shallow copy of root)."""

    return dict(_cached_seed_document())


def iter_seed_tokens(doc: dict[str, Any] | None = None) -> Iterator[TokenRegistrySeedEntry]:
    """Yield allowlist rows from the bundled seed export."""

    root = _cached_seed_document() if doc is None else doc
    tokens = root.get("tokens")
    if not isinstance(tokens, list):
        return
    for item in tokens:
        if not isinstance(item, dict):
            continue
        yield _entry_from_raw(item)


def _entry_from_raw(item: dict[str, Any]) -> TokenRegistrySeedEntry:
    chain_slug = str(item["chain_slug"]).strip().lower()
    symbol = str(item["symbol"]).strip().upper()
    name = str(item["name"]).strip()
    address = str(item["address"]).strip()
    source = str(item["source"]).strip()
    trust_tier = str(item["trust_tier"]).strip().lower()
    if trust_tier not in ("curated", "indexed"):
        raise ValueError(f"invalid trust_tier in seed bundle: {trust_tier!r}")
    decimals = _optional_int(item.get("decimals"))
    market_cap_rank = _optional_int(item.get("market_cap_rank"))
    raw_cg = item.get("coingecko_id")
    coingecko_id = None if raw_cg is None else str(raw_cg).strip() or None
    return TokenRegistrySeedEntry(
        chain_slug=chain_slug,
        symbol=symbol,
        name=name,
        address=address,
        decimals=decimals,
        coingecko_id=coingecko_id,
        market_cap_rank=market_cap_rank,
        source=source,
        trust_tier=trust_tier,
    )


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _validate_seed_document(doc: dict[str, Any]) -> None:
    tokens = doc.get("tokens")
    if not isinstance(tokens, list):
        raise ValueError("token_registry_seed.json must contain a 'tokens' array.")
    for i, item in enumerate(tokens):
        if not isinstance(item, dict):
            raise ValueError(f"seed tokens[{i}] must be an object.")
        for key in ("chain_slug", "symbol", "name", "address", "source", "trust_tier"):
            if key not in item:
                raise ValueError(f"seed tokens[{i}] missing required field {key!r}.")
