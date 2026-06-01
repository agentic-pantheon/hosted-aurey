"""Helpers for EVM JSON-RPC batching with graceful fallback."""

from __future__ import annotations

from typing import Any

from aurey.graphs.ports import EvmJsonRpcPort


def rpc_call_batch(rpc: EvmJsonRpcPort, calls: list[tuple[str, list[Any]]]) -> list[Any]:
    """Invoke ``call_batch`` when implemented; otherwise sequential ``call``."""

    if not calls:
        return []
    batch = getattr(rpc, "call_batch", None)
    if callable(batch):
        return batch(calls)
    return [rpc.call(method, params) for method, params in calls]


__all__ = ["rpc_call_batch"]
