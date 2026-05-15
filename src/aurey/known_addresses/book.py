"""Load ``data/known_addresses.json`` (symbol → address + name per chain)."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from typing import Any, NamedTuple, cast

from aurey.graphs.chains import chain_id_for
from aurey.graphs.evm_codec import normalize_evm_address


class KnownToken(NamedTuple):
    symbol: str
    address: str
    name: str


def reload_known_addresses_for_tests() -> None:
    """Clear the in-process JSON cache (tests only)."""

    _cached_document.cache_clear()


@lru_cache(maxsize=1)
def _cached_document() -> dict[str, Any]:
    raw: Any = json.loads(
        files("aurey.data").joinpath("known_addresses.json").read_text(encoding="utf-8")
    )
    doc = cast(dict[str, Any], raw)
    _validate_and_normalize(doc)
    return doc


def load_known_addresses() -> dict[str, Any]:
    """Return the parsed catalog (copy-friendly root dict)."""

    return dict(_cached_document())


def _validate_and_normalize(doc: dict[str, Any]) -> None:
    chains = doc.get("chains")
    if not isinstance(chains, dict):
        raise ValueError("known_addresses.json: missing chains object.")

    for cid, block in chains.items():
        if not isinstance(block, dict):
            continue
        toks = block.get("tokens")
        if not isinstance(toks, dict):
            continue
        rebuilt: dict[str, Any] = {}
        for symbol, entry in toks.items():
            sym = str(symbol)
            if isinstance(entry, str):
                rebuilt[sym] = {
                    "address": normalize_evm_address(entry),
                    "name": sym,
                }
            elif isinstance(entry, dict):
                addr = entry.get("address")
                if not isinstance(addr, str):
                    raise ValueError(f"Chain {cid} token {sym}: missing address.")
                name = entry.get("name")
                rebuilt[sym] = {
                    "address": normalize_evm_address(addr),
                    "name": str(name) if name is not None else sym,
                }
            else:
                raise ValueError(f"Chain {cid} token {sym}: invalid token entry.")
        block["tokens"] = rebuilt

        protos = block.get("protocols")
        if isinstance(protos, dict):
            for _group, mapping in protos.items():
                if not isinstance(mapping, dict):
                    continue
                for pk, addr in list(mapping.items()):
                    if isinstance(addr, str) and addr.startswith("0x") and len(addr) >= 42:
                        mapping[pk] = normalize_evm_address(addr)


def lookup_known_token(chain_slug: str, ticker: str) -> KnownToken | None:
    """Resolve a ticker on a canonical chain slug (e.g. ``base``, ``ethereum``).

    Chain must exist in :mod:`aurey.graphs.chains` **and** in the bundled JSON for that id.
    """

    cid_int = chain_id_for(chain_slug.strip().lower())
    if cid_int is None:
        return None

    doc = _cached_document()
    chains = cast(dict[str, Any], doc["chains"])
    block = chains.get(str(cid_int))
    if not isinstance(block, dict):
        return None

    symbols = cast(dict[str, Any], block.get("tokens") or {})
    needle = ticker.strip().upper()
    hit_key: str | None = None
    for k in symbols:
        if str(k).upper() == needle:
            hit_key = str(k)
            break
    if hit_key is None:
        return None

    row = symbols[hit_key]
    if not isinstance(row, dict):
        return None
    addr = row.get("address")
    name = row.get("name")
    if not isinstance(addr, str):
        return None
    return KnownToken(
        symbol=hit_key,
        address=normalize_evm_address(addr),
        name=str(name) if name is not None else hit_key,
    )
