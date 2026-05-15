"""Production-oriented stdlib adapters for :class:`~aurey.runtime.AureyRuntime` ports."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from aurey.graphs.ports import EvmJsonRpcPort, HttpJsonPort, HttpJsonRequestError


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

        decoded: Any = json.loads(raw.decode())
        if not isinstance(decoded, dict):
            raise RuntimeError("EVM RPC response must be a JSON object.")
        err = decoded.get("error")
        if err is not None:
            raise RuntimeError("EVM RPC returned an error envelope.")
        if "result" not in decoded:
            raise RuntimeError("EVM RPC response missing result.")

        return decoded["result"]


def make_evm_rpc_factory(timeout_s: float = 60.0) -> Callable[[str], EvmJsonRpcPort]:
    """Build a closure suitable for ``AureyRuntime.evm_rpc_factory``."""

    def _factory(url: str) -> EvmJsonRpcPort:
        return UrllibEvmJsonRpc(url, timeout_s=timeout_s)

    return _factory


__all__ = [
    "UrllibEvmJsonRpc",
    "UrllibHttpJsonClient",
    "make_evm_rpc_factory",
]
