"""Application state held on :attr:`FastAPI.state` for the Aurey HTTP service."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from aurey.cloud.signing_context import HostedSigningContext
from aurey.reasoning import create_aurey_deep_agent
from aurey.reasoning.checkpointer import ManagedPostgresCheckpointer
from aurey.runtime import AureyRuntime
from aurey.settings import AureySettings


def deep_agent_graph_cache_key(
    *,
    settings: AureySettings,
    model_spec: str,
    hosted_signing_context: HostedSigningContext | None,
) -> str:
    """Cache key for compiled Deep Agents: operator Shroud vs per-hosted-user."""

    spec = model_spec.strip()
    if (settings.llm_proxy or "").strip().lower() != "shroud":
        return spec

    ctx = hosted_signing_context
    if ctx is None:
        suffix = "operator"
    else:
        uid = (ctx.user_agent_id or "").strip() or "__missing_agent__"
        suffix = f"hosted:{uid}"
    return f"{spec}::shroud::{suffix}"


@dataclass
class AureyServiceState:
    """Process-scoped Aurey dependency graph for the FastAPI boundary."""

    settings: AureySettings
    runtime: AureyRuntime
    checkpointer: BaseCheckpointSaver
    default_model: str
    hosted_session_factory: Callable[..., Any] | None = None
    _graphs: dict[str, CompiledStateGraph[Any, Any, Any]] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)
    _postgres: ManagedPostgresCheckpointer | None = field(default=None, repr=False)
    _hosted_engine: Any | None = field(default=None, repr=False)
    _httpx_client: Any | None = field(default=None, repr=False)

    def close_checkpointer(self) -> None:
        """Release Postgres pool/connection manager if this process opened one.

        Also disposes the optional hosted metadata engine, if any.
        """

        if self._postgres is not None:
            self._postgres.close()
            self._postgres = None
        if self._hosted_engine is not None:
            self._hosted_engine.dispose()
            self._hosted_engine = None
        if self._httpx_client is not None:
            self._httpx_client.close()
            self._httpx_client = None

    def get_or_create_graph(
        self,
        model: str | None,
        *,
        hosted_signing_context: HostedSigningContext | None = None,
    ) -> CompiledStateGraph[Any, Any, Any]:
        """Return a compiled deep agent keyed by resolved model + LLM routing (bounded cache).

        Compilation is expensive; callers should reuse graphs for the same model string.
        """

        spec = (model or "").strip() or self.default_model
        cache_key = deep_agent_graph_cache_key(
            settings=self.runtime.settings,
            model_spec=spec,
            hosted_signing_context=(
                hosted_signing_context
                if (self.runtime.settings.llm_proxy or "").strip().lower()
                == "shroud"
                else None
            ),
        )

        ctx_for_deep = (
            hosted_signing_context
            if (self.runtime.settings.llm_proxy or "").strip().lower() == "shroud"
            else None
        )

        with self._lock:
            existing = self._graphs.get(cache_key)
            if existing is not None:
                return existing

            compiled = create_aurey_deep_agent(
                self.runtime,
                model=spec,
                checkpointer=self.checkpointer,
                hosted_signing_context=ctx_for_deep,
            )
            self._graphs[cache_key] = compiled
            return compiled


__all__ = ["AureyServiceState", "deep_agent_graph_cache_key"]
