"""Import allowlist rows from a local LiFi ``/v1/tokens`` JSON export."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aurey.graphs.evm_codec import to_checksum_evm_address

MONAD_CHAIN_ID = 143
MONAD_CHAIN_SLUG = "monad"


@dataclass(frozen=True)
class LifiFileTokenEntry:
    chain_slug: str
    chain_id: int
    symbol: str
    name: str
    address: str
    decimals: int | None


def load_lifi_tokens_file(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("LiFi tokens file must be a JSON object.")
    return raw


def iter_monad_tokens_from_lifi_payload(
    payload: dict[str, Any],
) -> Iterator[LifiFileTokenEntry]:
    """Yield Monad (chain 143) tokens from a LiFi export ``tokens`` map."""

    yield from iter_chain_tokens_from_lifi_payload(
        payload,
        chain_id=MONAD_CHAIN_ID,
        chain_slug=MONAD_CHAIN_SLUG,
    )


def iter_chain_tokens_from_lifi_payload(
    payload: dict[str, Any],
    *,
    chain_id: int,
    chain_slug: str,
) -> Iterator[LifiFileTokenEntry]:
    tokens_root = payload.get("tokens")
    if not isinstance(tokens_root, dict):
        return
    entries = tokens_root.get(str(chain_id))
    if not isinstance(entries, list):
        return
    for item in entries:
        if not isinstance(item, dict):
            continue
        raw_addr = item.get("address")
        if not isinstance(raw_addr, str) or not raw_addr.strip():
            continue
        try:
            addr = to_checksum_evm_address(raw_addr)
        except ValueError:
            continue
        raw_sym = item.get("symbol")
        symbol = str(raw_sym).strip().upper() if raw_sym is not None else ""
        if not symbol:
            continue
        raw_name = item.get("name")
        name = str(raw_name).strip() if raw_name is not None else symbol
        decimals = _optional_int(item.get("decimals"))
        yield LifiFileTokenEntry(
            chain_slug=chain_slug.strip().lower(),
            chain_id=chain_id,
            symbol=symbol[:32],
            name=name[:255],
            address=addr,
            decimals=decimals,
        )


def iter_monad_tokens_from_lifi_file(path: Path) -> Iterator[LifiFileTokenEntry]:
    return iter_monad_tokens_from_lifi_payload(load_lifi_tokens_file(path))


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
