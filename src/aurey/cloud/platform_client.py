"""HTTP client for 1Claw Platform provisioning endpoints (stdlib urllib)."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from aurey.custody.secret_store import _http_error_snippet
from aurey.settings import AureySettings

_log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_S = 20.0


class HostedPlatformApiError(RuntimeError):
    """Platform HTTP or JSON contract failure (safe to log; may include brief response snippet)."""


def _dig_mapping(root: Any, *path: str) -> Any:
    cur: Any = root
    for key in path:
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(key)
    return cur


def _first_non_empty_str(*candidates: Any) -> str | None:
    for c in candidates:
        if isinstance(c, str) and c.strip():
            return c.strip()
    return None


def extract_connection_id(payload: Any) -> str | None:
    """Resolve ``connection_id`` from a top-level or ``data``-nested JSON object."""

    cid = _first_non_empty_str(
        _dig_mapping(payload, "connection_id"),
        _dig_mapping(payload, "data", "connection_id"),
    )
    return cid


def extract_claim_url(payload: Any) -> str | None:
    """Resolve a claim / onboarding URL from common Platform response shapes."""

    return _first_non_empty_str(
        _dig_mapping(payload, "claim_url"),
        _dig_mapping(payload, "data", "claim_url"),
        _dig_mapping(payload, "claimUrl"),
        _dig_mapping(payload, "data", "claimUrl"),
        _dig_mapping(payload, "url"),
        _dig_mapping(payload, "data", "url"),
    )


def extract_optional_str(payload: Any, *paths: tuple[str, ...]) -> str | None:
    for path in paths:
        v = _dig_mapping(payload, *path)
        found = _first_non_empty_str(v)
        if found is not None:
            return found
    return None


@dataclass(frozen=True)
class PlatformUpsertResult:
    connection_id: str


@dataclass(frozen=True)
class PlatformBootstrapResult:
    claim_url: str
    vault_id: str | None = None
    user_agent_id: str | None = None


class OneClawPlatformClient:
    """Small urllib-based Platform client (synthetic user upsert + connection bootstrap)."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        bu = (base_url or "").strip().rstrip("/")
        key = (api_key or "").strip()
        if not bu:
            raise ValueError("Platform base URL must not be empty.")
        if not key:
            raise ValueError("Platform API key must not be empty.")
        self._base_url = bu
        self._api_key = key
        self._timeout_s = float(timeout_s)

    @classmethod
    def from_settings(
        cls,
        settings: AureySettings,
        *,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> OneClawPlatformClient:
        key = (settings.platform_api_key or "").strip()
        if not key:
            raise ValueError("platform_api_key is required for OneClawPlatformClient.")
        return cls(
            base_url=settings.oneclaw_base_url.strip(),
            api_key=key,
            timeout_s=timeout_s,
        )

    def _post_json(self, path_suffix: str, body: dict[str, Any]) -> Any:
        url = f"{self._base_url}/{path_suffix.lstrip('/')}"
        payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
        request = Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self._timeout_s) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            snippet = _http_error_snippet(exc, max_len=800)
            _log.warning(
                "Platform POST failed HTTP %s url=%s response_preview=%s",
                exc.code,
                url,
                snippet[:240],
            )
            raise HostedPlatformApiError(
                f"Platform HTTP {exc.code} for POST {path_suffix} (response preview truncated)."
            ) from exc
        except (OSError, URLError) as exc:
            _log.warning("Platform POST network error url=%s detail=%s", url, type(exc).__name__)
            raise HostedPlatformApiError(
                f"Platform request failed for {path_suffix} ({exc})."
            ) from exc
        try:
            return json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError as exc:
            raise HostedPlatformApiError("Platform response was not valid JSON.") from exc

    def upsert_user_synthetic_email(
        self,
        *,
        email: str,
        display_name: str | None,
    ) -> PlatformUpsertResult:
        """Ensure a synthetic user exists; parse ``connection_id`` (top-level or under ``data``)."""

        body: dict[str, Any] = {"email": email}
        if display_name is not None:
            body["display_name"] = display_name
        payload = self._post_json("v1/platform/users/upsert", body)
        cid = extract_connection_id(payload)
        if cid is None:
            raise HostedPlatformApiError(
                "Upsert response missing connection_id (top-level or data)."
            )
        return PlatformUpsertResult(connection_id=cid)

    def bootstrap(self, connection_id: str, template_id: str) -> PlatformBootstrapResult:
        """Bootstrap a connection; returns ``claim_url`` and optional vault / agent ids."""

        cid = (connection_id or "").strip()
        tid = (template_id or "").strip()
        if not cid:
            raise ValueError("connection_id must not be empty.")
        if not tid:
            raise ValueError("template_id must not be empty.")
        path = f"v1/platform/connections/{cid}/bootstrap"
        payload = self._post_json(path, {"template_id": tid})
        claim = extract_claim_url(payload)
        if claim is None:
            raise HostedPlatformApiError(
                "Bootstrap JSON missing claim URL (claim_url, data.claim_url, or url)."
            )
        vault_id = extract_optional_str(payload, ("vault_id",), ("data", "vault_id"))
        user_agent_id = extract_optional_str(
            payload,
            ("user_agent_id",),
            ("data", "user_agent_id"),
            ("agent_id",),
            ("data", "agent_id"),
        )
        return PlatformBootstrapResult(
            claim_url=claim,
            vault_id=vault_id,
            user_agent_id=user_agent_id,
        )


__all__ = [
    "HostedPlatformApiError",
    "OneClawPlatformClient",
    "PlatformBootstrapResult",
    "PlatformUpsertResult",
    "extract_claim_url",
    "extract_connection_id",
]
