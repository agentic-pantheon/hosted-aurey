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
from aurey.graphs.evm_codec import to_checksum_evm_address
from aurey.settings import AureySettings

_log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_S = 20.0


class HostedPlatformApiError(RuntimeError):
    """Platform HTTP or JSON contract failure (safe to log; may include brief response snippet)."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


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


def _signing_keys_list_candidates(payload: Any) -> list[Any]:
    """Return raw list nodes that may hold ``[{chain, address}, ...]`` (bootstrap-style)."""

    if not isinstance(payload, Mapping):
        return []
    keys_paths: list[tuple[str, ...]] = (
        ("summary", "signing_keys"),
        ("data", "summary", "signing_keys"),
        ("signing_keys",),
        ("data", "signing_keys"),
    )
    out: list[Any] = []
    for path in keys_paths:
        node = _dig_mapping(payload, *path)
        if isinstance(node, list):
            out.append(node)
    return out


def _chain_label_ethereum(chain: Any) -> bool:
    if isinstance(chain, str):
        c = chain.strip().lower()
        if not c:
            return False
        if c == "ethereum" or c == "eth":
            return True
        if "eip155" in c:
            suffix = c.split(":")[-1]
            try:
                return int(suffix) == 1
            except ValueError:
                pass
            return "ethereum" in c
        return False
    return False


def extract_ethereum_address_from_signing_key_items(items: Any) -> str | None:
    """First checksummed ``0x`` address from signing-key entries where ``chain`` is Ethereum.

    Intended for bootstrap ``summary.signing_keys`` and ``GET .../signing-keys`` ``keys`` arrays.
    """

    if not isinstance(items, list):
        return None
    eth_items: list[Mapping[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        chain = item.get("chain") or item.get("chain_slug") or item.get("chainSlug")
        if _chain_label_ethereum(chain):
            eth_items.append(item)
    cand = eth_items if eth_items else [x for x in items if isinstance(x, Mapping)]

    for item in cand:
        if not isinstance(item, Mapping):
            continue
        raw = item.get("address") or item.get("evm_address")
        addr = raw if isinstance(raw, str) else None
        if not addr or not addr.strip():
            continue
        try:
            return to_checksum_evm_address(addr.strip())
        except ValueError:
            continue
    return None


def extract_ethereum_wallet_address_from_bootstrap_payload(payload: Any) -> str | None:
    """Best-effort Ethereum address from Platform bootstrap JSON (signing_keys blocks)."""

    for lst in _signing_keys_list_candidates(payload):
        found = extract_ethereum_address_from_signing_key_items(lst)
        if found is not None:
            return found
    return None


def extract_signing_keys_array_from_api(payload: Any) -> list[Any] | None:
    """Normalize ``GET /v1/agents/{id}/signing-keys`` body to the ``keys`` list or None."""

    if isinstance(payload, list):
        return payload  # tolerant
    if not isinstance(payload, Mapping):
        return None
    keys = payload.get("keys")
    if isinstance(keys, list):
        return keys
    inner = payload.get("data")
    if isinstance(inner, Mapping):
        keys2 = inner.get("keys")
        if isinstance(keys2, list):
            return keys2
    return None


def ethereum_address_from_signing_keys_payload(payload: Any) -> str | None:
    """Extract checksummed Ethereum address from signing-keys GET response."""

    arr = extract_signing_keys_array_from_api(payload)
    if arr is None:
        return None
    return extract_ethereum_address_from_signing_key_items(arr)


@dataclass(frozen=True)
class PlatformUpsertResult:
    connection_id: str


@dataclass(frozen=True)
class PlatformReissueClaimResult:
    """Fresh claim link from ``POST .../connections/{id}/reissue-claim``."""

    claim_url: str
    connection_id: str | None = None
    claim_token: str | None = None
    expires_in: int | None = None


@dataclass(frozen=True)
class PlatformBootstrapResult:
    """Bootstrap output; ``agent_api_key`` is the per-user ``ocv_`` from ``summary.agent_api_key`` when present."""

    claim_url: str
    vault_id: str | None = None
    user_agent_id: str | None = None
    agent_api_key: str | None = None
    wallet_address: str | None = None


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
                f"Platform HTTP {exc.code} for POST {path_suffix} (response preview truncated).",
                status_code=int(exc.code),
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

    def _get_json(self, path_suffix: str) -> Any:
        url = f"{self._base_url}/{path_suffix.lstrip('/')}"
        request = Request(
            url,
            headers={
                "Authorization": f"Bearer {self._api_key}",
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=self._timeout_s) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            snippet = _http_error_snippet(exc, max_len=800)
            _log.warning(
                "Platform GET failed HTTP %s url=%s response_preview=%s",
                exc.code,
                url,
                snippet[:240],
            )
            raise HostedPlatformApiError(
                f"Platform HTTP {exc.code} for GET {path_suffix} (response preview truncated).",
                status_code=int(exc.code),
            ) from exc
        except (OSError, URLError) as exc:
            _log.warning("Platform GET network error url=%s detail=%s", url, type(exc).__name__)
            raise HostedPlatformApiError(
                f"Platform request failed for GET {path_suffix} ({exc})."
            ) from exc
        try:
            return json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError as exc:
            raise HostedPlatformApiError("Platform GET response was not valid JSON.") from exc

    def get_connection(self, connection_id: str) -> dict[str, Any]:
        """GET ``/v1/platform/connections/{connection_id}``; returns parsed JSON object."""

        cid = (connection_id or "").strip()
        if not cid:
            raise ValueError("connection_id must not be empty.")
        payload = self._get_json(f"v1/platform/connections/{cid}")
        if not isinstance(payload, dict):
            raise HostedPlatformApiError("Platform connection response must be a JSON object.")
        return payload

    def list_app_users(self, app_id: str) -> dict[str, Any] | list[Any]:
        """GET ``/v1/platform/apps/{app_id}/users`` — returns JSON object or top-level array."""

        aid = (app_id or "").strip()
        if not aid:
            raise ValueError("app_id must not be empty.")
        payload = self._get_json(f"v1/platform/apps/{aid}/users")
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, list):
            return payload
        raise HostedPlatformApiError(
            "Platform app users response must be a JSON object or array.",
        )

    def upsert_user_by_email(
        self,
        *,
        email: str,
        display_name: str | None,
    ) -> PlatformUpsertResult:
        """Provision user via ``POST /v1/platform/users/upsert`` (email upsert identity)."""

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

    def upsert_user_synthetic_email(
        self,
        *,
        email: str,
        display_name: str | None,
    ) -> PlatformUpsertResult:
        """Backward-compatible alias for :meth:`upsert_user_by_email`."""

        return self.upsert_user_by_email(email=email, display_name=display_name)

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
            ("summary", "agent_id"),
            ("data", "summary", "agent_id"),
        )
        wallet_address = extract_ethereum_wallet_address_from_bootstrap_payload(payload)
        agent_api_key = extract_optional_str(
            payload,
            ("summary", "agent_api_key"),
            ("data", "summary", "agent_api_key"),
            ("agent_api_key",),
            ("data", "agent_api_key"),
        )
        return PlatformBootstrapResult(
            claim_url=claim,
            vault_id=vault_id,
            user_agent_id=user_agent_id,
            agent_api_key=agent_api_key,
            wallet_address=wallet_address,
        )

    def reissue_claim(
        self,
        connection_id: str,
        *,
        return_to: str | None = None,
    ) -> PlatformReissueClaimResult:
        """Mint a fresh claim URL for an already-bootstrapped connection (Platform ``plt_`` key)."""

        cid = (connection_id or "").strip()
        if not cid:
            raise ValueError("connection_id must not be empty.")
        body: dict[str, Any] = {}
        rt = (return_to or "").strip()
        if rt:
            body["return_to"] = rt
        path = f"v1/platform/connections/{cid}/reissue-claim"
        payload = self._post_json(path, body)
        claim = extract_claim_url(payload)
        if claim is None:
            raise HostedPlatformApiError(
                "Reissue-claim JSON missing claim URL (claim_url, data.claim_url, or url)."
            )
        resolved_cid = extract_connection_id(payload) or extract_optional_str(
            payload,
            ("connection_id",),
            ("data", "connection_id"),
        )
        claim_token = extract_optional_str(
            payload,
            ("claim_token",),
            ("data", "claim_token"),
        )
        expires_raw = _dig_mapping(payload, "expires_in")
        if expires_raw is None:
            expires_raw = _dig_mapping(payload, "data", "expires_in")
        expires_in: int | None = None
        if isinstance(expires_raw, int):
            expires_in = expires_raw
        elif isinstance(expires_raw, float):
            expires_in = int(expires_raw)
        return PlatformReissueClaimResult(
            claim_url=claim,
            connection_id=resolved_cid or cid,
            claim_token=claim_token,
            expires_in=expires_in,
        )

    def get_agent_signing_keys(self, agent_id: str) -> dict[str, Any]:
        """GET ``/v1/agents/{agent_id}/signing-keys``; returns parsed JSON object."""

        aid = (agent_id or "").strip()
        if not aid:
            raise ValueError("agent_id must not be empty.")
        payload = self._get_json(f"v1/agents/{aid}/signing-keys")
        if not isinstance(payload, dict):
            raise HostedPlatformApiError("Signing-keys response must be a JSON object.")
        return payload


__all__ = [
    "HostedPlatformApiError",
    "OneClawPlatformClient",
    "PlatformBootstrapResult",
    "PlatformReissueClaimResult",
    "PlatformUpsertResult",
    "ethereum_address_from_signing_keys_payload",
    "extract_claim_url",
    "extract_connection_id",
    "extract_ethereum_address_from_signing_key_items",
    "extract_ethereum_wallet_address_from_bootstrap_payload",
    "extract_signing_keys_array_from_api",
]
