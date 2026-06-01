"""LiFi ``/v1/tokens`` catalog → registry rows (EVM + optional Solana)."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from aurey.graphs.chains import CHAIN_INDEX, chain_name_for_id
from aurey.graphs.evm_codec import to_checksum_evm_address
from aurey.graphs.ports import HttpJsonPort
from aurey.token_registry.lifi_tokens import _DEFAULT_LIFI_BASE_URL, fetch_lifi_tokens_payload

# LiFi chain id for Solana mainnet (does not fit Postgres INTEGER; use chain_id=NULL).
LIFI_SOLANA_CHAIN_ID = 11511110810916497016029180535

_SOLANA_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


@dataclass(frozen=True)
class LifiCatalogEntry:
    ecosystem: str
    chain_slug: str
    chain_id: int | None
    symbol: str
    name: str
    address: str
    decimals: int | None


def lifi_import_chain_ids() -> frozenset[int]:
    """EVM chain ids from :data:`CHAIN_INDEX` plus Solana when requested."""

    ids = {info.chain_id for info in CHAIN_INDEX.values()}
    return frozenset(ids)


def lifi_import_chain_slugs() -> frozenset[str]:
    slugs = set(CHAIN_INDEX.keys())
    slugs.add("solana")
    return frozenset(slugs)


def fetch_lifi_tokens_payload_for_import(
    http: HttpJsonPort,
    *,
    base_url: str = _DEFAULT_LIFI_BASE_URL,
    api_key: str | None = None,
    extra_chain_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Full catalog plus optional ``?chains=`` fetches (e.g. Solana)."""

    merged: dict[str, Any] = {"tokens": {}}
    full = fetch_lifi_tokens_payload(http, base_url=base_url, api_key=api_key)
    root = full.get("tokens")
    if isinstance(root, dict):
        merged["tokens"].update(root)

    want = list(extra_chain_ids or [])
    if LIFI_SOLANA_CHAIN_ID not in want:
        want.append(LIFI_SOLANA_CHAIN_ID)
    for cid in want:
        if cid in lifi_import_chain_ids():
            continue
        url = f"{base_url.rstrip('/')}/v1/tokens?chains={cid}"
        headers: dict[str, str] = {}
        if api_key and api_key.strip():
            headers["x-lifi-api-key"] = api_key.strip()
        try:
            raw = http.request_json(method="GET", url=url, headers=headers or None)
        except Exception:
            continue
        if not isinstance(raw, dict):
            continue
        part = raw.get("tokens")
        if isinstance(part, dict):
            for key, entries in part.items():
                if isinstance(entries, list):
                    merged["tokens"][str(key)] = entries
    return merged


def iter_lifi_catalog_entries(payload: dict[str, Any]) -> Iterator[LifiCatalogEntry]:
    """Yield importable tokens for Aurey-supported EVM chains and Solana (if present)."""

    tokens_root = payload.get("tokens")
    if not isinstance(tokens_root, dict):
        return

    for chain_key, entries in tokens_root.items():
        if not isinstance(entries, list):
            continue
        try:
            numeric_chain_id = int(chain_key)
        except (TypeError, ValueError):
            continue

        chain_slug = chain_name_for_id(numeric_chain_id)
        ecosystem = "evm"
        stored_chain_id: int | None = numeric_chain_id

        if chain_slug is None and numeric_chain_id == LIFI_SOLANA_CHAIN_ID:
            chain_slug = "solana"
            ecosystem = "solana"
            stored_chain_id = None
        elif chain_slug is None:
            continue

        for item in entries:
            if not isinstance(item, dict):
                continue
            entry = _entry_from_lifi_item(
                item,
                ecosystem=ecosystem,
                chain_slug=chain_slug,
                chain_id=stored_chain_id,
            )
            if entry is not None:
                yield entry


def _entry_from_lifi_item(
    item: dict[str, Any],
    *,
    ecosystem: str,
    chain_slug: str,
    chain_id: int | None,
) -> LifiCatalogEntry | None:
    raw_addr = item.get("address")
    if not isinstance(raw_addr, str) or not raw_addr.strip():
        return None
    try:
        if ecosystem == "solana":
            address = normalize_solana_address(raw_addr)
        else:
            address = to_checksum_evm_address(raw_addr)
    except ValueError:
        return None

    raw_sym = item.get("symbol")
    symbol = str(raw_sym).strip().upper() if raw_sym is not None else ""
    if not symbol:
        return None
    raw_name = item.get("name")
    name = str(raw_name).strip() if raw_name is not None else symbol
    decimals = _optional_int(item.get("decimals"))

    return LifiCatalogEntry(
        ecosystem=ecosystem,
        chain_slug=chain_slug,
        chain_id=chain_id,
        symbol=symbol[:32],
        name=name[:255],
        address=address,
        decimals=decimals,
    )


def normalize_solana_address(addr: str) -> str:
    s = addr.strip()
    if not _SOLANA_ADDRESS_RE.match(s):
        raise ValueError("invalid Solana mint address")
    return s


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
