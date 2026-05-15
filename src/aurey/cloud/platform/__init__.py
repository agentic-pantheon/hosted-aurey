"""HTTP client for 1Claw Platform API (``plt_`` key)."""

from __future__ import annotations

from typing import Any

from aurey.graphs.ports import HttpJsonPort, HttpJsonRequestError


def _unwrap_response_payload(body: dict[str, Any]) -> dict[str, Any]:
    """Support both top-level and ``{"data": {...}}`` response shapes."""

    data = body.get("data")
    if isinstance(data, dict):
        return data
    return body


def _prefer_summary_then_top(
    *,
    outer: dict[str, Any],
    summary: dict[str, Any],
    key: str,
) -> Any:
    """Prefer ``summary[key]``, then meaningful top-level ``outer[key]``."""

    inner = summary.get(key)
    outer_v = outer.get(key)
    for candidate in (inner, outer_v):
        if candidate is None:
            continue
        if isinstance(candidate, str) and not candidate.strip():
            continue
        return candidate
    return None


def _normalize_bootstrap_payload(body: dict[str, Any]) -> dict[str, Any]:
    """Flatten ``summary`` fields so onboarding can read stable top-level keys.

    Live Platform API nests ``vault_id`` / ``agent_id`` / ``policy_ids`` under
    ``summary``; older mocks and transitional shapes may expose them top-level.

    Preference order for each flattened field is **summary first**, then top-level.
    """

    out = dict(body)
    raw_summary = body.get("summary")
    summary: dict[str, Any] = raw_summary if isinstance(raw_summary, dict) else {}

    vault_id = _prefer_summary_then_top(outer=out, summary=summary, key="vault_id")
    agent_id = _prefer_summary_then_top(outer=out, summary=summary, key="agent_id")
    policy_ids = _prefer_summary_then_top(outer=out, summary=summary, key="policy_ids")

    if vault_id is not None:
        out["vault_id"] = vault_id
    if agent_id is not None:
        out["agent_id"] = agent_id
    if policy_ids is not None:
        out["policy_ids"] = policy_ids
    claim_token = summary.get("claim_token") if "claim_token" in summary else out.get("claim_token")
    if claim_token not in (None, ""):
        out["claim_token"] = claim_token
    return out


class OneClawPlatformApiClient:
    """Minimal platform API surface for hosted onboarding (Phase B)."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        http: HttpJsonPort,
    ) -> None:
        root = (base_url or "").strip().rstrip("/")
        if not root:
            raise ValueError("Platform API base_url must not be empty.")
        if not (api_key or "").strip():
            raise ValueError("Platform API key must not be empty.")
        self._root = root
        self._api_key = api_key.strip()
        self._http = http

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
        }

    def upsert_user(
        self,
        *,
        subject_token: str,
        display_name: str | None,
    ) -> dict[str, Any]:
        url = f"{self._root}/v1/platform/users/upsert"
        body: dict[str, Any] = {"subject_token": subject_token}
        if display_name is not None and display_name.strip():
            body["display_name"] = display_name.strip()

        raw = self._http.request_json(
            method="POST",
            url=url,
            headers=self._auth_headers(),
            json_body=body,
        )
        if not isinstance(raw, dict):
            raise HttpJsonRequestError(status_code=500, body_text="platform_upsert_non_object")
        return _unwrap_response_payload(raw)

    def bootstrap_connection(
        self,
        *,
        connection_id: str,
        template_id: str,
    ) -> dict[str, Any]:
        cid = (connection_id or "").strip()
        if not cid:
            raise ValueError("connection_id must not be empty.")
        tid = (template_id or "").strip()
        if not tid:
            raise ValueError("template_id must not be empty.")

        url = f"{self._root}/v1/platform/connections/{cid}/bootstrap"
        raw = self._http.request_json(
            method="POST",
            url=url,
            headers=self._auth_headers(),
            json_body={"template_id": tid},
        )
        if not isinstance(raw, dict):
            raise HttpJsonRequestError(status_code=500, body_text="platform_bootstrap_non_object")
        return _normalize_bootstrap_payload(_unwrap_response_payload(raw))

    def get_connection(self, *, connection_id: str) -> dict[str, Any]:
        """Fetch connection details for claim polling (Phase C).

        **Assumption:** ``GET /v1/platform/connections/{connection_id}`` exists and returns a
        JSON object eventually consumed by
        :func:`aurey.cloud.onboarding.claim_parser.parse_claim_ready_signal`.
        """

        cid = (connection_id or "").strip()
        if not cid:
            raise ValueError("connection_id must not be empty.")

        url = f"{self._root}/v1/platform/connections/{cid}"
        raw = self._http.request_json(method="GET", url=url, headers=self._auth_headers())
        if not isinstance(raw, dict):
            raise HttpJsonRequestError(status_code=500, body_text="platform_connection_non_object")
        return _unwrap_response_payload(raw)


__all__ = ["OneClawPlatformApiClient"]
