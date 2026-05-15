"""Compiled LangGraph subgraphs and shared graph primitives.

Eager imports were removed so ``from aurey.graphs.ports import ...`` (used by
:class:`aurey.runtime.AureyRuntime`) does not pull in ``alchemy`` and create a
``runtime`` ↔ ``graphs`` import cycle.
"""

from __future__ import annotations

import importlib
from typing import Any

_LAZY: dict[str, tuple[str, str]] = {
    "AlchemyGraphInput": ("aurey.graphs.alchemy", "AlchemyGraphInput"),
    "build_alchemy_graph": ("aurey.graphs.alchemy", "build_alchemy_graph"),
    "Web3TxPipeline": ("aurey.graphs.evm_tx_pipeline", "Web3TxPipeline"),
    "ReadGraphInput": ("aurey.graphs.read", "ReadGraphInput"),
    "build_read_graph": ("aurey.graphs.read", "build_read_graph"),
    "GraphErrorBody": ("aurey.graphs.results", "GraphErrorBody"),
    "GraphRunResult": ("aurey.graphs.results", "GraphRunResult"),
    "LiFiAllowanceHint": ("aurey.graphs.results", "LiFiAllowanceHint"),
    "LiFiStatusInput": ("aurey.graphs.lifi_status", "LiFiStatusInput"),
    "build_lifi_status_graph": ("aurey.graphs.lifi_status", "build_lifi_status_graph"),
    "PreparedTxEnvelope": ("aurey.graphs.results", "PreparedTxEnvelope"),
    "SwapPrepareInput": ("aurey.graphs.swap_prepare", "SwapPrepareInput"),
    "build_swap_prepare_graph": ("aurey.graphs.swap_prepare", "build_swap_prepare_graph"),
    "EarnGraphInput": ("aurey.graphs.earn", "EarnGraphInput"),
    "build_earn_graph": ("aurey.graphs.earn", "build_earn_graph"),
    "DeterministicTxPipeline": ("aurey.graphs.tx_execute", "DeterministicTxPipeline"),
    "TxExecuteInput": ("aurey.graphs.tx_execute", "TxExecuteInput"),
    "build_tx_execute_graph": ("aurey.graphs.tx_execute", "build_tx_execute_graph"),
    "TxPrepareErc20Approval": ("aurey.graphs.tx_prepare", "TxPrepareErc20Approval"),
    "TxPrepareErc20Transfer": ("aurey.graphs.tx_prepare", "TxPrepareErc20Transfer"),
    "TxPrepareNative": ("aurey.graphs.tx_prepare", "TxPrepareNative"),
    "build_tx_prepare_graph": ("aurey.graphs.tx_prepare", "build_tx_prepare_graph"),
    "TxPrepareLiFiInput": ("aurey.graphs.tx_prepare_lifi", "TxPrepareLiFiInput"),
    "build_tx_prepare_lifi_graph": ("aurey.graphs.tx_prepare_lifi", "build_tx_prepare_lifi_graph"),
}

__all__ = list(_LAZY.keys())


def __getattr__(name: str) -> Any:
    spec = _LAZY.get(name)
    if spec is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mod_name, attr = spec
    return getattr(importlib.import_module(mod_name), attr)


def __dir__() -> list[str]:
    return sorted(__all__)
