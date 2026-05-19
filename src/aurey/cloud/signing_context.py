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
    """Telegram-hosted user identity for 1Claw intents (agent-token flow).

    ``agent_api_key_encrypted`` holds Fernet ciphertext from Postgres (backup).
    ``agent_api_key_legacy_plaintext`` is deprecated plaintext DB fallback until migrated.
    ``delegation_subject_token`` is legacy optional delegation material.
    """

    telegram_user_id: int
    user_agent_id: str
    delegation_subject_token: str | None = None
    agent_api_key_encrypted: str | None = None
    agent_api_key_legacy_plaintext: str | None = None
    wallet_address: str | None = None


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
