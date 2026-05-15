"""Deep agent harness, checkpointer helpers, and compiled graph factory."""

from __future__ import annotations

from typing import Any

from aurey.reasoning.checkpointer import make_memory_checkpointer, thread_config
from aurey.reasoning.harness import (
    AUREY_DEEP_HARNESS_BASE,
    ensure_aurey_wallet_harness,
    resolve_harness_model_spec,
)

__all__ = [
    "AUREY_DEEP_HARNESS_BASE",
    "AUREY_DEEP_USER_PROMPT",
    "create_aurey_deep_agent",
    "ensure_aurey_wallet_harness",
    "make_memory_checkpointer",
    "resolve_harness_model_spec",
    "runtime_wiring_context_for_deep_agent_prompt",
    "thread_config",
    "wallet_context_for_deep_agent_prompt",
]


def __getattr__(name: str) -> Any:
    """Lazy-load deep agent to avoid import cycles (``invoke`` → ``runtime`` → ``graphs``)."""

    if name == "create_aurey_deep_agent":
        from aurey.reasoning.deep_agent import create_aurey_deep_agent

        return create_aurey_deep_agent
    if name == "AUREY_DEEP_USER_PROMPT":
        from aurey.reasoning.deep_agent import AUREY_DEEP_USER_PROMPT

        return AUREY_DEEP_USER_PROMPT
    if name == "wallet_context_for_deep_agent_prompt":
        from aurey.reasoning.deep_agent import wallet_context_for_deep_agent_prompt

        return wallet_context_for_deep_agent_prompt
    if name == "runtime_wiring_context_for_deep_agent_prompt":
        from aurey.reasoning.deep_agent import runtime_wiring_context_for_deep_agent_prompt

        return runtime_wiring_context_for_deep_agent_prompt
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
