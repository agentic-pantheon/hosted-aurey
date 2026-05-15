"""1Claw Platform API client wiring."""

from __future__ import annotations

from aurey.cloud.platform import OneClawPlatformApiClient
from tests.fakes.http_client import ScriptedHttpClient


def test_platform_client_upsert_and_bootstrap_urls() -> None:
    http = ScriptedHttpClient(
        [
            (
                lambda method, url, headers, json_body: method == "POST"
                and url.endswith("/v1/platform/users/upsert"),
                {"connection_id": "conn_abc", "id": "usr_xyz"},
            ),
            (
                lambda method, url, headers, json_body: method == "POST"
                and "/v1/platform/connections/conn_abc/bootstrap" in url,
                {
                    "claim_url": "https://claim.example/here",
                    "vault_id": "vlt_1",
                    "agent_id": "agt_1",
                },
            ),
        ]
    )
    client = OneClawPlatformApiClient(
        base_url="https://api.1claw.xyz",
        api_key="plt_test",
        http=http,
    )
    up = client.upsert_user(subject_token="jwt-here", display_name="A")
    assert up["connection_id"] == "conn_abc"
    boot = client.bootstrap_connection(connection_id="conn_abc", template_id="tpl_1")
    assert boot["claim_url"].startswith("https://claim.example")

    assert http.calls[0]["headers"]
    assert "Bearer plt_test" in http.calls[0]["headers"]["Authorization"]
    assert http.calls[0]["json_body"]["subject_token"] == "jwt-here"


def test_platform_client_get_connection_uses_expected_path() -> None:
    http = ScriptedHttpClient(
        [
            (
                lambda method, url, headers, json_body: method == "GET"
                and url.endswith("/v1/platform/connections/conn_z"),
                {"status": "ready"},
            ),
        ]
    )
    client = OneClawPlatformApiClient(
        base_url="https://api.1claw.xyz",
        api_key="plt_test",
        http=http,
    )
    body = client.get_connection(connection_id="conn_z")
    assert body["status"] == "ready"
    assert http.calls[0]["method"] == "GET"


def test_platform_client_bootstrap_normalizes_nested_summary() -> None:
    http = ScriptedHttpClient(
        [
            (
                lambda method, url, headers, json_body: method == "POST"
                and "/bootstrap" in url,
                {
                    "claim_url": "https://claim.example/nested",
                    "claim_token": "ct_abc",
                    "summary": {
                        "vault_id": "vlt_sum",
                        "agent_id": "agt_sum",
                        "policy_ids": ["pol_1"],
                    },
                },
            ),
        ]
    )
    client = OneClawPlatformApiClient(
        base_url="https://api.example",
        api_key="plt_test",
        http=http,
    )
    boot = client.bootstrap_connection(connection_id="conn_x", template_id="tpl_z")
    assert boot["vault_id"] == "vlt_sum"
    assert boot["agent_id"] == "agt_sum"
    assert boot["policy_ids"] == ["pol_1"]
    assert boot["claim_url"].startswith("https://claim.example")
    assert boot["claim_token"] == "ct_abc"


def test_platform_client_unwraps_data_envelope() -> None:
    http = ScriptedHttpClient(
        [
            (
                lambda *a, **k: True,
                {"data": {"connection_id": "conn_env", "id": "usr_env"}},
            ),
        ]
    )
    client = OneClawPlatformApiClient(
        base_url="https://example",
        api_key="plt_x",
        http=http,
    )
    up = client.upsert_user(subject_token="t", display_name=None)
    assert up["connection_id"] == "conn_env"
