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
        return _unwrap_response_payload(raw)


__all__ = ["OneClawPlatformApiClient"]
