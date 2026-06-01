"""Parse LiFi ``GET /v1/tokens`` payloads for registry ``lifi_supported`` sync."""

from __future__ import annotations

from typing import Any, cast

from aurey.graphs.chains import chain_name_for_id
from aurey.graphs.evm_codec import to_checksum_evm_address
from aurey.graphs.ports import HttpJsonPort

_DEFAULT_LIFI_BASE_URL = "https://li.quest"


def fetch_lifi_tokens_payload(
    http: HttpJsonPort,
    *,
    base_url: str = _DEFAULT_LIFI_BASE_URL,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Download LiFi token catalog JSON."""

    url = f"{base_url.rstrip('/')}/v1/tokens"
    headers: dict[str, str] = {}
    if api_key and api_key.strip():
        headers["x-lifi-api-key"] = api_key.strip()
    raw = http.request_json(method="GET", url=url, headers=headers or None)
    if not isinstance(raw, dict):
        raise ValueError("LiFi /v1/tokens response must be a JSON object.")
    return cast(dict[str, Any], raw)


def build_lifi_address_set(payload: dict[str, Any]) -> set[tuple[int, str]]:
    """``(chain_id, checksum_address)`` for tokens on Aurey-supported chains only."""

    tokens_root = payload.get("tokens")
    if not isinstance(tokens_root, dict):
        return set()

    out: set[tuple[int, str]] = set()
    for chain_key, entries in tokens_root.items():
        try:
            chain_id = int(chain_key)
        except (TypeError, ValueError):
            continue
        if chain_name_for_id(chain_id) is None:
            continue
        if not isinstance(entries, list):
            continue
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
            out.add((chain_id, addr))
    return out
