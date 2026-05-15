"""Test doubles for :class:`aurey.graphs.ports.HttpJsonPort`."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from aurey.graphs.ports import HttpJsonPort


class ScriptedHttpClient(HttpJsonPort):
    """Dispatch by matcher functions; records calls for assertions."""

    def __init__(
        self,
        handlers: list[tuple[Callable[..., bool], dict[str, Any] | list[Any]]] | None = None,
    ) -> None:
        self._handlers = list(handlers or [])
        self.calls: list[dict[str, Any]] = []

    def add(self, matcher: Callable[..., bool], response: dict[str, Any] | list[Any]) -> None:
        self._handlers.append((matcher, response))

    def request_json(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | list[Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        record = {"method": method, "url": url, "headers": headers, "json_body": json_body}
        self.calls.append(record)
        for matcher, response in self._handlers:
            if matcher(method=method, url=url, headers=headers or {}, json_body=json_body):
                if isinstance(response, list):
                    return list(response)
                return dict(response)
        raise AssertionError(f"No HTTP handler matched {method} {url}")


class FailingHttpJsonClient(HttpJsonPort):
    """Raises on every request without embedding URLs or headers in the message."""

    def request_json(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | list[Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        _ = method, url, headers, json_body
        raise RuntimeError("injected_http_transport_failure")
