"""Test doubles for :class:`aurey.graphs.ports.EvmJsonRpcPort`."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from aurey.graphs.ports import EvmJsonRpcPort


class ScriptedEvmJsonRpc(EvmJsonRpcPort):
    def __init__(self, by_method: dict[str, Any]) -> None:
        self._by_method = dict(by_method)

    def call(self, method: str, params: list[Any]) -> Any:
        handler = self._by_method.get(method)
        if handler is None:
            raise RuntimeError(f"unexpected rpc method {method!r}")
        if callable(handler):
            return handler(params)
        return handler


def rpc_factory_from_mapping(
    mapping: dict[str, Any],
) -> Callable[[str], EvmJsonRpcPort]:
    def _factory(url: str) -> EvmJsonRpcPort:
        _ = url
        return ScriptedEvmJsonRpc(dict(mapping))

    return _factory
