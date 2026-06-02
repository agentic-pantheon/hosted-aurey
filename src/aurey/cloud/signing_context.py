"""Per-request hosted signing context (Telegram / sync invoke) via :mod:`contextvars`."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Mapping

HOSTED_SIGNING_CONTEXT_REQUIRED_CODE = "hosted_signing_context_required"

__all__ = [
    "HOSTED_SIGNING_CONTEXT_REQUIRED_CODE",
    "HostedSigningContext",
    "current_hosted_signing_context",
    "current_hosted_telegram_user_id",
    "hosted_signing_missing_context_graph_error",
    "hosted_signing_missing_context_tool_error",
    "hosted_signing_context_scope",
    "aurey_invoke_context_scope",
    "current_aurey_invoke_context",
    "hosted_telegram_user_id_scope",
]


def hosted_signing_missing_context_message() -> str:
    return (
        "Hosted mode requires a bound Telegram signing context (provisioned user_agent_id). "
        "HTTP invoke without that binding cannot use oneclaw_intents signing."
    )


def hosted_signing_missing_context_tool_error() -> dict[str, str]:
    """Error dict for ``OneClawSigningPrincipal`` and tool surfaces."""

    return {
        "code": HOSTED_SIGNING_CONTEXT_REQUIRED_CODE,
        "message": hosted_signing_missing_context_message(),
    }


def hosted_signing_missing_context_graph_error() -> dict[str, Any]:
    """``GraphErrorBody``-shaped dict for LangGraph nodes."""

    from aurey.graphs.results import GraphErrorBody

    return GraphErrorBody(
        code="hosted_signing_context_required",
        message=hosted_signing_missing_context_message(),
        details=None,
    ).model_dump()


@dataclass(frozen=True)
class HostedSigningContext:
    """Telegram-hosted user identity for 1Claw intents (agent-token flow).

    ``agent_api_key_encrypted`` holds Fernet ciphertext from Postgres (backup).
    ``agent_api_key_legacy_plaintext`` is deprecated plaintext DB fallback until migrated.
    """

    telegram_user_id: int
    user_agent_id: str
    agent_api_key_encrypted: str | None = None
    agent_api_key_legacy_plaintext: str | None = None
    wallet_address: str | None = None


current_hosted_signing_context: ContextVar[HostedSigningContext | None] = ContextVar(
    "current_hosted_signing_context",
    default=None,
)

current_hosted_telegram_user_id: ContextVar[int | None] = ContextVar(
    "current_hosted_telegram_user_id",
    default=None,
)

current_aurey_invoke_context: ContextVar[Mapping[str, Any] | None] = ContextVar(
    "current_aurey_invoke_context",
    default=None,
)


@contextmanager
def hosted_signing_context_scope(ctx: HostedSigningContext) -> Iterator[None]:
    """Bind ``ctx`` for the current task/thread (tests, Telegram ``invoke``)."""

    token = current_hosted_signing_context.set(ctx)
    try:
        yield
    finally:
        current_hosted_signing_context.reset(token)


@contextmanager
def aurey_invoke_context_scope(context: Mapping[str, Any] | None) -> Iterator[None]:
    """Bind per-turn invoke context (survives tool threads when set at invoke entry)."""

    token = current_aurey_invoke_context.set(context)
    try:
        yield
    finally:
        current_aurey_invoke_context.reset(token)


@contextmanager
def hosted_telegram_user_id_scope(telegram_user_id: int | None) -> Iterator[None]:
    """Bind Telegram user id for hosted wallet lookup tools (works before onboarding ``ready``)."""

    token = current_hosted_telegram_user_id.set(telegram_user_id)
    try:
        yield
    finally:
        current_hosted_telegram_user_id.reset(token)
