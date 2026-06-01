"""Fetch agent signing-keys with Platform key, falling back to per-user ``ocv_``."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from aurey.cloud.models import HostedPlatformUserORM
from aurey.cloud.platform_client import HostedPlatformApiError, OneClawPlatformClient
from aurey.settings import AureySettings

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SigningKeysFetchFallback:
    """Per-user agent API key + HTTP client for agent-token authenticated reads."""

    oneclaw_http: Any
    agent_api_key: str


def signing_keys_fallback_for_row(
    settings: AureySettings,
    row: HostedPlatformUserORM,
    oneclaw_http: Any | None,
) -> SigningKeysFetchFallback | None:
    if oneclaw_http is None:
        return None
    from aurey.cloud.hosted_credentials import resolve_hosted_ocv_for_signing
    from aurey.custody.secret_store import OneClawHttpClient

    if not isinstance(oneclaw_http, OneClawHttpClient):
        return None
    aid = (row.user_agent_id or "").strip()
    if not aid:
        return None
    ocv = resolve_hosted_ocv_for_signing(
        settings,
        oneclaw_http,
        agent_id=aid,
        ciphertext=row.agent_api_key_encrypted,
        legacy_plaintext=row.agent_api_key,
    )
    if not ocv:
        return None
    return SigningKeysFetchFallback(oneclaw_http=oneclaw_http, agent_api_key=ocv.strip())


def fetch_agent_signing_keys_payload(
    platform: OneClawPlatformClient,
    *,
    user_agent_id: str,
    fallback: SigningKeysFetchFallback | None = None,
) -> dict[str, Any]:
    """GET signing-keys; on Platform 401/403 retry with the user's agent credential when provided."""

    aid = (user_agent_id or "").strip()
    if not aid:
        raise ValueError("user_agent_id must not be empty.")
    try:
        return platform.get_agent_signing_keys(aid)
    except HostedPlatformApiError as exc:
        if exc.status_code not in (401, 403) or fallback is None:
            raise
        client = fallback.oneclaw_http
        get_json = getattr(client, "get_agent_signing_keys_json", None)
        if not callable(get_json):
            raise
        _log.info(
            "signing-keys Platform HTTP %s for agent_id=%s; retrying with user agent credential",
            exc.status_code,
            aid,
        )
        return get_json(aid, agent_api_key=fallback.agent_api_key)


__all__ = [
    "SigningKeysFetchFallback",
    "fetch_agent_signing_keys_payload",
    "signing_keys_fallback_for_row",
]
