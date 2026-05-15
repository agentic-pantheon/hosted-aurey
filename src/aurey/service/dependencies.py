"""FastAPI-friendly helpers to read process-scoped Aurey wiring from ASGI app state.

No ``fastapi`` import at module level so tests can import this module without ``api`` extras.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from aurey.service.state import AureyServiceState


@runtime_checkable
class _RequestWithAppState(Protocol):
    """Minimal shape of Starlette/FastAPI ``Request`` used for state reads."""

    app: Any


def get_aurey_service_state(request: _RequestWithAppState) -> AureyServiceState | None:
    """Return the bootstrapped :class:`~aurey.service.state.AureyServiceState`, if any."""

    return getattr(request.app.state, "aurey", None)


__all__ = ["get_aurey_service_state"]
