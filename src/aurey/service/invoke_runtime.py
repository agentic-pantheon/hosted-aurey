"""Per-invoke :class:`~aurey.runtime.AureyRuntime` overlay (ContextVar-backed)."""

from __future__ import annotations

from contextvars import ContextVar, Token

from aurey.runtime import AureyRuntime

_runtime_overlay: ContextVar[AureyRuntime | None] = ContextVar(
    "aurey_runtime_overlay",
    default=None,
)


def effective_runtime(base: AureyRuntime) -> AureyRuntime:
    """Return the overlay runtime when set (e.g. per Telegram turn); else ``base``."""

    cur = _runtime_overlay.get()
    return cur if cur is not None else base


def push_runtime_overlay(rt: AureyRuntime) -> Token[AureyRuntime | None]:
    """Activate ``rt`` for the current async/task context; returns a reset token."""

    return _runtime_overlay.set(rt)


def reset_runtime_overlay(token: Token[AureyRuntime | None]) -> None:
    """Restore the previous overlay (typically in ``finally``)."""

    _runtime_overlay.reset(token)


class AureyRuntimeProxy:
    """Attribute proxy so graphs/tools resolve the effective runtime per invoke."""

    __slots__ = ("_base",)

    def __init__(self, base: AureyRuntime) -> None:
        object.__setattr__(self, "_base", base)

    def __getattribute__(self, name: str):
        if name == "_base":
            return object.__getattribute__(self, "_base")
        base: AureyRuntime = object.__getattribute__(self, "_base")
        return getattr(effective_runtime(base), name)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("AureyRuntimeProxy is read-only")


__all__ = [
    "AureyRuntimeProxy",
    "effective_runtime",
    "push_runtime_overlay",
    "reset_runtime_overlay",
]
