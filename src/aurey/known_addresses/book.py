"""Load ``data/known_addresses.json`` (symbol → address + name per chain)."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from typing import Any, NamedTuple, cast

from collections.abc import Iterator

from aurey.graphs.chains import chain_id_for, chain_name_for_id
from aurey.graphs.evm_codec import normalize_evm_address
from aurey.known_addresses.names import normalize_token_lookup_name


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


def chain_slug_for_catalog_chain_id(chain_id: int, doc: dict[str, Any] | None = None) -> str | None:
    """Resolve chain slug from ``chain_name_to_id`` in the catalog, else :func:`chain_name_for_id`."""

    catalog = doc if doc is not None else _cached_document()
    mapping = catalog.get("chain_name_to_id")
    if isinstance(mapping, dict):
        for slug, raw_id in mapping.items():
            try:
                if int(raw_id) == chain_id:
                    return str(slug).strip().lower()
            except (TypeError, ValueError):
                continue
    return chain_name_for_id(chain_id)


def iter_catalog_tokens(
    doc: dict[str, Any] | None = None,
) -> Iterator[tuple[str, str, str, str]]:
    """Yield ``(chain_slug, symbol, human_name, address)`` for every token in the bundled JSON."""

    catalog = doc if doc is not None else _cached_document()
    chains = catalog.get("chains")
    if not isinstance(chains, dict):
        return

    for cid_str, block in chains.items():
        if not isinstance(block, dict):
            continue
        try:
            cid = int(cid_str)
        except ValueError:
            continue
        slug = chain_slug_for_catalog_chain_id(cid, catalog)
        if slug is None:
            continue
        toks = block.get("tokens")
        if not isinstance(toks, dict):
            continue
        for sym, row in toks.items():
            if not isinstance(row, dict):
                continue
            addr = row.get("address")
            if not isinstance(addr, str) or not addr.strip():
                continue
            try:
                norm = normalize_evm_address(addr)
            except ValueError:
                continue
            if norm == "0x0000000000000000000000000000000000000000":
                continue
            name = row.get("name")
            display = str(name).strip() if name is not None else str(sym)
            yield slug, str(sym), display, norm


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


def lookup_known_token_by_name(chain_slug: str, token_name: str) -> KnownToken | None:
    """Resolve by bundled ``name`` on a chain (exact normalized match; allowlist only)."""

    cid_int = chain_id_for(chain_slug.strip().lower())
    if cid_int is None:
        return None

    needle = normalize_token_lookup_name(token_name)
    if not needle:
        return None

    doc = _cached_document()
    chains = cast(dict[str, Any], doc["chains"])
    block = chains.get(str(cid_int))
    if not isinstance(block, dict):
        return None

    symbols = cast(dict[str, Any], block.get("tokens") or {})
    matches: list[KnownToken] = []
    for sym, row in symbols.items():
        if not isinstance(row, dict):
            continue
        addr = row.get("address")
        if not isinstance(addr, str):
            continue
        display = row.get("name")
        label = str(display) if display is not None else str(sym)
        if normalize_token_lookup_name(label) != needle:
            continue
        matches.append(
            KnownToken(
                symbol=str(sym),
                address=normalize_evm_address(addr),
                name=label,
            )
        )
    if not matches:
        return None
    matches.sort(key=lambda t: t.symbol.upper())
    return matches[0]


def lookup_known_token_by_address(chain_slug: str, token_address: str) -> KnownToken | None:
    """Resolve by contract address on a canonical chain slug."""

    cid_int = chain_id_for(chain_slug.strip().lower())
    if cid_int is None:
        return None
    try:
        needle = normalize_evm_address(token_address)
    except ValueError:
        return None

    doc = _cached_document()
    chains = cast(dict[str, Any], doc["chains"])
    block = chains.get(str(cid_int))
    if not isinstance(block, dict):
        return None

    symbols = cast(dict[str, Any], block.get("tokens") or {})
    for sym, row in symbols.items():
        if not isinstance(row, dict):
            continue
        addr = row.get("address")
        if not isinstance(addr, str):
            continue
        if normalize_evm_address(addr) == needle:
            name = row.get("name")
            return KnownToken(
                symbol=str(sym),
                address=needle,
                name=str(name) if name is not None else str(sym),
            )
    return None


def chain_slug_in_known_catalog(chain_slug: str) -> bool:
    """True when ``chain_name_to_id`` lists this slug (curated chain for native gas)."""

    doc = _cached_document()
    mapping = doc.get("chain_name_to_id")
    if not isinstance(mapping, dict):
        return False
    return chain_slug.strip().lower() in {str(k).strip().lower() for k in mapping}
