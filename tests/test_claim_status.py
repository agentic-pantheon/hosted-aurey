"""Unit tests for connection claim JSON extractors."""

from __future__ import annotations

from aurey.cloud.claim_status import (
    ConnectionClaimSignals,
    connection_claim_details,
    should_mark_connection_ready,
)


def test_claimed_boolean_top_level() -> None:
    s = connection_claim_details({"claimed": True})
    assert s.looks_claimed is True
    assert should_mark_connection_ready(s) is True


def test_claimed_nested_data_wrapper() -> None:
    s = connection_claim_details({"data": {"is_claimed": 1}})
    assert s.looks_claimed is True


def test_claimed_at_truthy_string() -> None:
    s = connection_claim_details({"data": {"claimed_at": "2026-05-01T00:00:00Z"}})
    assert s.looks_claimed is True


def test_status_ready_like() -> None:
    assert connection_claim_details({"connection_status": "claimed"}).looks_claimed is True
    assert connection_claim_details({"data": {"state": "complete"}}).looks_claimed is True


def test_nonempty_agents_or_resources() -> None:
    assert connection_claim_details({"agents": [{}]}).looks_claimed is True
    assert connection_claim_details({"data": {"resources": ["x"]}}).looks_claimed is True


def test_user_agent_ids_and_wallet() -> None:
    s = connection_claim_details(
        {
            "data": {
                "user_agent_id": " ua-1 ",
                "wallet_address": "0xabc",
                "vault_id": "vault-99",
            }
        }
    )
    assert s.user_agent_id == "ua-1"
    assert s.wallet_address == "0xabc"
    assert s.vault_id == "vault-99"
    assert should_mark_connection_ready(s) is True


def test_agent_nested_id_fallback_for_ready() -> None:
    s = connection_claim_details({"data": {"agent": {"id": "ag-nested"}}})
    assert s.looks_claimed is False
    assert s.user_agent_id == "ag-nested"
    assert should_mark_connection_ready(s) is True


def test_no_signals() -> None:
    s = connection_claim_details({})
    assert s == ConnectionClaimSignals(
        looks_claimed=False,
        user_agent_id=None,
        wallet_address=None,
        vault_id=None,
    )
    assert should_mark_connection_ready(s) is False


def test_string_claim_completed_yes() -> None:
    s = connection_claim_details({"claim_completed": "YES"})
    assert s.looks_claimed is True
