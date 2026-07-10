"""Build MCP-style token registry JSON documents for offline use."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aurey.token_registry.repository import TokenRow

SCHEMA_VERSION = 1


def token_row_to_export_item(row: TokenRow) -> dict[str, Any]:
    return {
        "ecosystem": row.ecosystem,
        "chain_slug": row.chain_slug,
        "chain_id": row.chain_id,
        "symbol": row.symbol,
        "name": row.name,
        "address": row.address,
        "decimals": row.decimals,
        "coingecko_id": row.coingecko_id,
        "source": row.source,
        "trust_tier": row.trust_tier,
        "verified_onchain": row.verified_onchain,
        "cg_recognized": row.cg_recognized,
        "lifi_supported": row.lifi_supported,
    }


def build_export_document(
    rows: list[TokenRow],
    *,
    source_table: str,
    trust_tier_filter: list[str] | None,
) -> dict[str, Any]:
    items = [token_row_to_export_item(r) for r in rows]
    return {
        "schema_version": SCHEMA_VERSION,
        "exported_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source_table": source_table,
        "filter": {"trust_tier": trust_tier_filter} if trust_tier_filter is not None else {},
        "row_count": len(items),
        "tokens": items,
    }


def write_export_document(path: Path, doc: dict[str, Any], *, pretty: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if pretty:
        text = json.dumps(doc, indent=2, ensure_ascii=False)
        text += "\n"
    else:
        text = json.dumps(doc, separators=(",", ":"), ensure_ascii=False)
        text += "\n"
    path.write_text(text, encoding="utf-8")


__all__ = [
    "SCHEMA_VERSION",
    "build_export_document",
    "token_row_to_export_item",
    "write_export_document",
]
