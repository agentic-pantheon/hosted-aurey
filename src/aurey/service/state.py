"""Application state held on :attr:`FastAPI.state` for the Aurey HTTP service."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from aurey.reasoning import create_aurey_deep_agent
from aurey.reasoning.checkpointer import ManagedPostgresCheckpointer
from aurey.runtime import AureyRuntime
from aurey.settings import AureySettings


@dataclass
class AureyServiceState:
    """Process-scoped Aurey dependency graph for the FastAPI boundary."""

    settings: AureySettings
    runtime: AureyRuntime
    checkpointer: BaseCheckpointSaver
    default_model: str
    _graphs: dict[str, CompiledStateGraph[Any, Any, Any]] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)
    _postgres: ManagedPostgresCheckpointer | None = field(default=None, repr=False)

    def close_checkpointer(self) -> None:
        """Release Postgres pool/connection manager if this process opened one."""

        if self._postgres is not None:
            self._postgres.close()
            self._postgres = None

    def get_or_create_graph(self, model: str | None) -> CompiledStateGraph[Any, Any, Any]:
        """Return a compiled deep agent keyed by resolved model identity (bounded cache).

        Compilation is expensive; callers should reuse graphs for the same model string.
        """

        spec = (model or "").strip() or self.default_model
        with self._lock:
            existing = self._graphs.get(spec)
            if existing is not None:
                return existing

            compiled = create_aurey_deep_agent(
                self.runtime,
                model=spec,
                checkpointer=self.checkpointer,
            )
            self._graphs[spec] = compiled
            return compiled


__all__ = ["AureyServiceState"]
