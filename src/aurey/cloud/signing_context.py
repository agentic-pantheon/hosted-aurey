"""Per-request hosted signing context (Telegram / sync invoke) via :mod:`contextvars`."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

__all__ = [
    "HostedSigningContext",
    "current_hosted_signing_context",
    "hosted_signing_context_scope",
]


@dataclass(frozen=True)
class HostedSigningContext:
    """Telegram-hosted user identity for delegated 1Claw intents signing."""

    telegram_user_id: int
    user_agent_id: str
    delegation_subject_token: str


current_hosted_signing_context: ContextVar[HostedSigningContext | None] = ContextVar(
    "current_hosted_signing_context",
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
