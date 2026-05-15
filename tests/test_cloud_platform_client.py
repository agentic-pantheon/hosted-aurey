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
