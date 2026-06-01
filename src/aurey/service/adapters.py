"""Production HTTP/RPC adapters for :class:`~aurey.runtime.AureyRuntime` ports."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import httpx

from aurey.graphs.ports import EvmJsonRpcPort, HttpJsonPort, HttpJsonRequestError


def make_shared_httpx_client(
    *,
    timeout_s: float = 60.0,
    max_connections: int = 100,
    max_keepalive_connections: int = 20,
) -> httpx.Client:
    """Process-scoped client with connection pooling (thread-safe for concurrent requests)."""

    timeout = httpx.Timeout(timeout_s, connect=min(30.0, timeout_s))
    limits = httpx.Limits(
        max_connections=max_connections,
        max_keepalive_connections=max_keepalive_connections,
    )
    return httpx.Client(timeout=timeout, limits=limits, headers={"User-Agent": "Aurey/1.0"})


class HttpxJsonClient(HttpJsonPort):
    """JSON HTTP via a shared :class:`httpx.Client`."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    @property
    def httpx_client(self) -> httpx.Client:
        return self._client

    def close(self) -> None:
        self._client.close()

    def request_json(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | list[Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        hdrs = dict(headers or {})
        hdrs.setdefault("User-Agent", "Aurey/1.0")
        if json_body is not None:
            hdrs.setdefault("Content-Type", "application/json")
        try:
            resp = self._client.request(
                method.upper(),
                url,
                headers=hdrs,
                json=json_body,
            )
        except httpx.RequestError as exc:
            raise RuntimeError("HTTP request failed (network error).") from exc

        raw_body = resp.text
        if resp.status_code >= 400:
            payload: dict[str, Any] | None = None
            try:
                decoded = resp.json()
                if isinstance(decoded, dict):
                    payload = decoded
            except json.JSONDecodeError:
                pass
            raise HttpJsonRequestError(
                status_code=int(resp.status_code),
                body_text=raw_body[:4000],
                payload=payload,
            )

        if not raw_body:
            return {}

        decoded = resp.json()
        if isinstance(decoded, dict):
            return decoded
        if isinstance(decoded, list):
            return decoded
        raise RuntimeError("HTTP JSON response body must be an object or array.")


def _decode_single_rpc_envelope(decoded: Any) -> Any:
    if not isinstance(decoded, dict):
        raise RuntimeError("EVM RPC response must be a JSON object.")
    err = decoded.get("error")
    if err is not None:
        raise RuntimeError("EVM RPC returned an error envelope.")
    if "result" not in decoded:
        raise RuntimeError("EVM RPC response missing result.")
    return decoded["result"]


class HttpxEvmJsonRpc(EvmJsonRpcPort):
    """JSON-RPC 2.0 POST client sharing an :class:`httpx.Client` pool."""

    def __init__(self, rpc_url: str, *, client: httpx.Client) -> None:
        if not rpc_url.strip():
            raise ValueError("RPC URL must not be empty.")
        self._rpc_url = rpc_url
        self._client = client
        self._next_id = 1

    def call(self, method: str, params: list[Any]) -> Any:
        req_id = self._next_id
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": req_id}
        try:
            resp = self._client.post(
                self._rpc_url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
        except httpx.RequestError as exc:
            raise RuntimeError("EVM RPC request failed (network error).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"EVM RPC request failed ({resp.status_code}).")
        return _decode_single_rpc_envelope(resp.json())

    def call_batch(self, calls: list[tuple[str, list[Any]]]) -> list[Any]:
        if not calls:
            return []
        if len(calls) == 1:
            method, params = calls[0]
            return [self.call(method, params)]

        start_id = self._next_id
        batch_payload: list[dict[str, Any]] = []
        for offset, (method, params) in enumerate(calls):
            batch_payload.append(
                {
                    "jsonrpc": "2.0",
                    "method": method,
                    "params": params,
                    "id": start_id + offset,
                }
            )
        self._next_id = start_id + len(calls)

        try:
            resp = self._client.post(
                self._rpc_url,
                json=batch_payload,
                headers={"Content-Type": "application/json"},
            )
        except httpx.RequestError as exc:
            raise RuntimeError("EVM RPC batch request failed (network error).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"EVM RPC batch request failed ({resp.status_code}).")

        decoded = resp.json()
        if not isinstance(decoded, list):
            raise RuntimeError("EVM RPC batch response must be a JSON array.")

        by_id: dict[int, Any] = {}
        for item in decoded:
            if not isinstance(item, dict):
                continue
            req_id_raw = item.get("id")
            try:
                req_id = int(req_id_raw)
            except (TypeError, ValueError):
                continue
            by_id[req_id] = _decode_single_rpc_envelope(item)

        out: list[Any] = []
        for offset in range(len(calls)):
            rid = start_id + offset
            if rid not in by_id:
                raise RuntimeError("EVM RPC batch response missing an expected result id.")
            out.append(by_id[rid])
        return out


class UrllibHttpJsonClient(HttpJsonPort):
    """JSON HTTP requests via ``urllib`` (timeouts, raises on network/HTTP failures)."""

    def __init__(self, *, timeout_s: float = 30.0) -> None:
        self._timeout_s = timeout_s

    def request_json(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | list[Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        hdrs = dict(headers or {})
        hdrs.setdefault("User-Agent", "Aurey/1.0")
        data: bytes | None = None
        if json_body is not None:
            hdrs.setdefault("Content-Type", "application/json")
            data = json.dumps(json_body).encode()

        req = Request(url=url, data=data, headers=hdrs, method=method.upper())
        try:
            with urlopen(req, timeout=self._timeout_s) as resp:
                raw = resp.read()
        except HTTPError as exc:
            raw_body = exc.read().decode("utf-8", errors="replace")
            payload: dict[str, Any] | None = None
            try:
                decoded = json.loads(raw_body)
                if isinstance(decoded, dict):
                    payload = decoded
            except json.JSONDecodeError:
                pass
            raise HttpJsonRequestError(
                status_code=int(exc.code),
                body_text=raw_body[:4000],
                payload=payload,
            ) from exc
        except URLError as exc:
            raise RuntimeError("HTTP request failed (network error).") from exc

        if not raw:
            return {}

        decoded = json.loads(raw.decode())
        if isinstance(decoded, dict):
            return decoded
        if isinstance(decoded, list):
            return decoded
        raise RuntimeError("HTTP JSON response body must be an object or array.")


class UrllibEvmJsonRpc(EvmJsonRpcPort):
    """Minimal JSON-RPC 2.0 POST client for arbitrary EVM provider URLs."""

    def __init__(self, rpc_url: str, *, timeout_s: float = 60.0) -> None:
        if not rpc_url.strip():
            raise ValueError("RPC URL must not be empty.")

        self._rpc_url = rpc_url
        self._timeout_s = timeout_s
        self._next_id = 1

    def call(self, method: str, params: list[Any]) -> Any:
        req_id = self._next_id
        self._next_id += 1

        payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": req_id}
        body = json.dumps(payload).encode()
        req = Request(
            self._rpc_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=self._timeout_s) as resp:
                raw = resp.read()
        except HTTPError as exc:
            raise RuntimeError(f"EVM RPC request failed ({exc.code}).") from exc
        except URLError as exc:
            raise RuntimeError("EVM RPC request failed (network error).") from exc

        return _decode_single_rpc_envelope(json.loads(raw.decode()))

    def call_batch(self, calls: list[tuple[str, list[Any]]]) -> list[Any]:
        return [self.call(method, params) for method, params in calls]


def make_evm_rpc_factory(
    client: httpx.Client | None = None,
    *,
    timeout_s: float = 60.0,
) -> Callable[[str], EvmJsonRpcPort]:
    """Build a closure suitable for ``AureyRuntime.evm_rpc_factory``."""

    if client is None:

        def _urllib_factory(url: str) -> EvmJsonRpcPort:
            return UrllibEvmJsonRpc(url, timeout_s=timeout_s)

        return _urllib_factory

    def _httpx_factory(url: str) -> EvmJsonRpcPort:
        return HttpxEvmJsonRpc(url, client=client)

    return _httpx_factory


__all__ = [
    "HttpxEvmJsonRpc",
    "HttpxJsonClient",
    "UrllibEvmJsonRpc",
    "UrllibHttpJsonClient",
    "make_evm_rpc_factory",
    "make_shared_httpx_client",
]
