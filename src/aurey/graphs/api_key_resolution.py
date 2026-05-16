"""Resolve provider API keys: optional plaintext env on settings, else SecretStore paths."""

from __future__ import annotations

from typing import Any, Protocol

from aurey.custody.errors import (
    SecretNotFoundError,
    SecretStoreUnavailableError,
    secret_unavailable_graph_details,
)
from aurey.graphs.results import GraphErrorBody
from aurey.settings import AureySettings


class _SecretValue(Protocol):
    def reveal(self) -> str: ...


class _SecretStoreProto(Protocol):
    def get_secret(self, path: str) -> _SecretValue: ...


def effective_alchemy_api_key(
    settings: AureySettings,
    secret_store: _SecretStoreProto,
    *,
    extra_secret_not_configured_details: dict[str, Any] | None = None,
) -> tuple[str | None, dict[str, Any] | None]:
    """Return Alchemy API key from ``settings.alchemy_api_key`` or ``alchemy_api_secret_path``."""

    env_key = (settings.alchemy_api_key or "").strip()
    if env_key:
        return env_key, None

    path = settings.alchemy_api_secret_path
    if path is None or not str(path).strip():
        details: dict[str, Any] = {}
        if extra_secret_not_configured_details:
            details.update(extra_secret_not_configured_details)
        err = GraphErrorBody(
            code="secret_not_configured",
            message="Alchemy API secret path is not configured.",
            details=details or None,
        ).model_dump()
        return None, err

    path_s = str(path).strip()
    try:
        return secret_store.get_secret(path_s).reveal().strip(), None
    except SecretNotFoundError:
        err = GraphErrorBody(
            code="secret_not_found",
            message="Alchemy API secret could not be resolved.",
            details={"secret_kind": "alchemy_api"},
        ).model_dump()
        return None, err
    except SecretStoreUnavailableError as exc:
        err = GraphErrorBody(
            code="secret_unavailable",
            message="Secret store unavailable while resolving Alchemy API key.",
            details=secret_unavailable_graph_details(secret_kind="alchemy_api", exc=exc),
        ).model_dump()
        return None, err


def effective_lifi_api_key(
    settings: AureySettings,
    secret_store: _SecretStoreProto,
) -> tuple[str | None, dict[str, Any] | None]:
    """Resolve LiFi API key from env settings or vault path.

    Returns ``(None, None)`` when LiFi should run without authentication.
    """

    env_key = (settings.lifi_api_key or "").strip()
    if env_key:
        return env_key, None

    path = settings.lifi_api_secret_path
    if path is None or not str(path).strip():
        return None, None
    path_s = str(path).strip()
    try:
        return secret_store.get_secret(path_s).reveal(), None
    except SecretNotFoundError:
        err = GraphErrorBody(
            code="secret_not_found",
            message="LiFi API secret could not be resolved.",
            details={"secret_kind": "lifi_api"},
        ).model_dump()
        return None, err
    except SecretStoreUnavailableError as exc:
        err = GraphErrorBody(
            code="secret_unavailable",
            message="Secret store unavailable while resolving LiFi API key.",
            details=secret_unavailable_graph_details(secret_kind="lifi_api", exc=exc),
        ).model_dump()
        return None, err
