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
from aurey.service.invoke_runtime import AureyRuntimeProxy
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

    cloud_db_engine: Any | None = field(default=None, repr=False)
    db_session_factory: Any | None = field(default=None, repr=False)
    onboarding: Any | None = field(default=None, repr=False)
    oidc_signer: Any | None = field(default=None, repr=False)

    def close_checkpointer(self) -> None:
        """Release Postgres pool/connection manager if this process opened one."""

        if self._postgres is not None:
            self._postgres.close()
            self._postgres = None
        if self.cloud_db_engine is not None:
            self.cloud_db_engine.dispose()
            self.cloud_db_engine = None

    def get_or_create_graph(
        self,
        model: str | None,
        *,
        graph_cache_suffix: str = "",
        extra_system_prompt: str | None = None,
    ) -> CompiledStateGraph[Any, Any, Any]:
        """Return a compiled deep agent keyed by resolved model identity (bounded cache).

        Compilation is expensive; callers should reuse graphs for the same model string.
        Hosted turns may supply ``graph_cache_suffix`` / ``extra_system_prompt`` so per-user
        wallet hints do not leak across Telegram identities.
        """

        spec = (model or "").strip() or self.default_model
        sfx = (graph_cache_suffix or "").strip()
        extra = (extra_system_prompt or "").strip()
        cache_key = spec if not sfx and not extra else f"{spec}\x1f{sfx}\x1f{extra}"
        with self._lock:
            existing = self._graphs.get(cache_key)
            if existing is not None:
                return existing

            proxy = AureyRuntimeProxy(self.runtime)
            compiled = create_aurey_deep_agent(
                proxy,
                model=spec,
                checkpointer=self.checkpointer,
                extra_system_prompt=extra if extra else None,
            )
            self._graphs[cache_key] = compiled
            return compiled


__all__ = ["AureyServiceState"]
