"""Tests for MCP token registry export documents."""

from __future__ import annotations

from aurey.token_registry.export_document import build_export_document, token_row_to_export_item
from aurey.token_registry.repository import TokenRow


def test_build_export_document_row_count() -> None:
    row = TokenRow(
        chain_slug="base",
        chain_id=8453,
        symbol="USDC",
        name="USD Coin",
        address="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        decimals=6,
        coingecko_id="usd-coin",
        source="bundled",
        trust_tier="curated",
        verified_onchain=True,
        cg_recognized=True,
        lifi_supported=True,
    )
    doc = build_export_document(
        [row],
        source_table="token_registry",
        trust_tier_filter=["curated", "indexed"],
    )
    assert doc["schema_version"] == 1
    assert doc["row_count"] == 1
    assert doc["tokens"] == [token_row_to_export_item(row)]
