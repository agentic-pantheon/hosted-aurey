"""Unit tests for Platform JSON helpers (no live HTTP)."""

from __future__ import annotations

from aurey.cloud.platform_client import extract_claim_url, extract_connection_id


def test_extract_connection_id_top_level() -> None:
    assert extract_connection_id({"connection_id": " c1 "}) == "c1"


def test_extract_connection_id_nested_data() -> None:
    assert extract_connection_id({"data": {"connection_id": "c2"}}) == "c2"


def test_extract_connection_id_missing() -> None:
    assert extract_connection_id({"data": {}}) is None


def test_extract_claim_url_variants() -> None:
    assert extract_claim_url({"claim_url": "https://claim/u"}) == "https://claim/u"
    assert extract_claim_url({"data": {"claimUrl": "https://alt"}}) == "https://alt"
    assert extract_claim_url({"data": {"url": "https://u"}}) == "https://u"
