"""Tests for signing-keys Platform vs agent-credential fallback."""

from __future__ import annotations

import pytest

from aurey.cloud.models import HostedPlatformUserORM
from aurey.cloud.platform_client import HostedPlatformApiError, OneClawPlatformClient
from aurey.cloud.signing_keys_fetch import (
    SigningKeysFetchFallback,
    fetch_agent_signing_keys_payload,
)
from aurey.settings import AureySettings


class _Platform403:
    def get_agent_signing_keys(self, agent_id: str) -> dict:
        raise HostedPlatformApiError("forbidden", status_code=403)


class _HttpSigningKeys:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def get_agent_signing_keys_json(self, agent_id: str, *, agent_api_key: str) -> dict:
        self.calls.append((agent_id, agent_api_key))
        return {"keys": [{"chain": "solana", "address": "SolViaOcv"}]}


def test_fetch_signing_keys_falls_back_to_agent_credential() -> None:
    http = _HttpSigningKeys()
    payload = fetch_agent_signing_keys_payload(
        _Platform403(),  # type: ignore[arg-type]
        user_agent_id="agent-1",
        fallback=SigningKeysFetchFallback(oneclaw_http=http, agent_api_key="ocv_test"),
    )
    assert payload["keys"][0]["address"] == "SolViaOcv"
    assert http.calls == [("agent-1", "ocv_test")]


def test_fetch_signing_keys_reraises_when_no_fallback() -> None:
    with pytest.raises(HostedPlatformApiError) as exc:
        fetch_agent_signing_keys_payload(
            _Platform403(),  # type: ignore[arg-type]
            user_agent_id="agent-1",
            fallback=None,
        )
    assert exc.value.status_code == 403
