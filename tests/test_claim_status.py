"""Unit tests for connection claim JSON extractors."""

from __future__ import annotations

from aurey.cloud.claim_status import (
    ConnectionClaimSignals,
    connection_claim_details,
    should_mark_connection_ready,
    user_record_for_connection_id,
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
                "claimed_at": "2026-05-01T00:00:00Z",
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


def test_agent_nested_id_requires_claim_marker_for_ready() -> None:
    s = connection_claim_details({"data": {"agent": {"id": "ag-nested"}}})
    assert s.looks_claimed is False
    assert s.user_agent_id == "ag-nested"
    assert should_mark_connection_ready(s) is False


def test_agent_nested_id_when_claimed_at_ready() -> None:
    s = connection_claim_details(
        {"data": {"claimed_at": "2026-05-01T00:00:00Z", "agent": {"id": "ag-nested"}}}
    )
    assert s.looks_claimed is True
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


def test_user_record_for_connection_id_finds_nested_list() -> None:
    hit = user_record_for_connection_id(
        {
            "data": [
                {"connection_id": "other", "user_agent_id": "a"},
                {
                    "connection_id": "want-this",
                    "user_agent_id": "b",
                },
            ]
        },
        "want-this",
    )
    assert hit is not None
    assert hit.get("user_agent_id") == "b"


def test_user_record_for_connection_id_nested_connection_object() -> None:
    hit = user_record_for_connection_id(
        {
            "users": [
                {
                    "connection": {"id": "cid-1"},
                    "user_agent_id": "ag",
                }
            ]
        },
        "cid-1",
    )
    assert hit is not None
    assert hit.get("user_agent_id") == "ag"


def test_user_record_for_connection_id_miss_returns_none() -> None:
    assert (
        user_record_for_connection_id({"data": [{"connection_id": "x"}]}, "missing")
        is None
    )


def test_oneclaw_app_users_shape_agent_ids_vault_ids() -> None:
    """Live ``GET .../apps/{id}/users`` entries often use plural id arrays."""

    row = {
        "agent_ids": ["0871f7c9-9a9d-4622-b27c-4e0be08081e2"],
        "claimed_at": "2026-05-16T15:09:52.712408+00:00",
        "connection_id": "9692779c-cb2a-4a50-a19f-3e23bce363fa",
        "status": "claimed",
        "vault_ids": ["ec6d4a2c-6735-4356-ab29-31337819efbe"],
    }
    s = connection_claim_details(row)
    assert s.looks_claimed is True
    assert s.user_agent_id == "0871f7c9-9a9d-4622-b27c-4e0be08081e2"
    assert s.vault_id == "ec6d4a2c-6735-4356-ab29-31337819efbe"
    assert should_mark_connection_ready(s) is True


def test_active_without_claimed_at_not_ready_even_with_agent_ids() -> None:
    row = {
        "agent_ids": ["cfea6a5d-7270-40d3-958e-8a49701998e4"],
        "claimed_at": None,
        "connection_id": "9db2000c-05a0-4de0-96ab-770b8fd3e986",
        "status": "active",
        "vault_ids": ["b94a03d3-a10e-4438-bec8-5097e51ee404"],
    }
    s = connection_claim_details(row)
    assert s.looks_claimed is False
    assert s.user_agent_id == "cfea6a5d-7270-40d3-958e-8a49701998e4"
    assert should_mark_connection_ready(s) is False


def test_user_record_find_in_top_level_json_array() -> None:
    payload = [
        {"connection_id": "a"},
        {"connection_id": "want", "status": "claimed", "claimed_at": "2026-05-01T00:00:00Z"},
    ]
    hit = user_record_for_connection_id(payload, "want")
    assert hit is not None
    assert hit.get("connection_id") == "want"
