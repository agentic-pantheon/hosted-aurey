"""Sanitized custody-layer exceptions."""

from __future__ import annotations

from typing import Any


class CustodyError(RuntimeError):
    """Base class for custody and secret-store failures."""


class SecretStoreError(CustodyError):
    """Base class for secret-store failures."""


class SecretNotFoundError(SecretStoreError):
    """Raised when a secret path is not present in the secret store."""

    def __init__(self, path: str) -> None:
        super().__init__(f"Secret not found at path '{path}'.")
        self.path = path


class EmptySecretValueError(SecretStoreError):
    """Raised when a secret exists but has no usable value."""

    def __init__(self, path: str) -> None:
        super().__init__(f"Secret at path '{path}' is empty.")
        self.path = path


class OneClawSigningError(RuntimeError):
    """Raised when 1Claw returns a signing response that cannot be used (no secrets in messages)."""


class SecretStoreUnavailableError(SecretStoreError):
    """Raised when the backing secret store cannot be reached or queried."""

    def __init__(
        self,
        path: str,
        store_name: str = "secret store",
        *,
        detail: str | None = None,
    ) -> None:
        self.path = path
        self.store_name = store_name
        self.detail = detail
        if detail is not None:
            super().__init__(f"{store_name}: {detail}")
        else:
            super().__init__(f"{store_name} could not resolve secret path '{path}'.")


def secret_unavailable_graph_details(
    *,
    secret_kind: str,
    exc: SecretStoreUnavailableError,
) -> dict[str, Any]:
    """Stable GraphError ``details`` for :class:`SecretStoreUnavailableError` (no secret values)."""

    return {
        "secret_kind": secret_kind,
        "path": exc.path,
        "store": exc.store_name,
        "detail": exc.detail if exc.detail is not None else str(exc),
    }
