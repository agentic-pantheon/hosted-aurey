"""Request metadata helpers for Mini App HTTP handlers."""

from __future__ import annotations

from starlette.requests import Request


def client_ip_from_request(request: Request) -> str:
    """Best-effort client IP (honors ``X-Forwarded-For`` first hop when present)."""

    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    if request.client is not None and request.client.host:
        return request.client.host.strip()
    return "unknown"


__all__ = ["client_ip_from_request"]
