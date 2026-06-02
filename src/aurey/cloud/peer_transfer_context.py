"""Per-invoke context for Aurey-to-Aurey transfer notify (resolve → tx_execute)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

__all__ = [
    "PeerTransferRecipient",
    "clear_peer_transfer_recipient",
    "current_peer_transfer_recipient",
    "peer_transfer_recipient_scope",
    "set_peer_transfer_recipient",
]


@dataclass(frozen=True)
class PeerTransferRecipient:
    telegram_user_id: int
    telegram_handle: str
    evm_address: str


current_peer_transfer_recipient: ContextVar[PeerTransferRecipient | None] = ContextVar(
    "current_peer_transfer_recipient",
    default=None,
)


def set_peer_transfer_recipient(recipient: PeerTransferRecipient | None) -> None:
    current_peer_transfer_recipient.set(recipient)


def clear_peer_transfer_recipient() -> None:
    current_peer_transfer_recipient.set(None)


@contextmanager
def peer_transfer_recipient_scope() -> Iterator[None]:
    """Clear peer transfer recipient after an agent invoke turn."""

    try:
        yield
    finally:
        clear_peer_transfer_recipient()
