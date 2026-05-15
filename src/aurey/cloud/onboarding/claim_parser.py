"""Tolerant parsing for "claim is complete" signals from 1Claw Platform payloads.

Primary integration (documented OpenAPI):

- Rows from ``GET /v1/platform/apps/{appId}/users``: use
  :func:`parse_connected_user_claim_ready` (checks ``claimed_at`` and ``status``).

Legacy / diagnostic shapes:

- Flattened objects passed to :func:`parse_claim_ready_signal` (booleans, nested ``claim``, etc.)

We avoid trusting or persisting raw secrets; this module only answers whether the wallet /
claim flow appears done for audit metadata keys.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_TRUTHY = frozenset(
    {
        "true",
        "1",
        "yes",
        "y",
        "ready",
        "claimed",
        "complete",
        "completed",
        "active",
        "provisioned",
    }
)

# Status-like keys whose string values we compare loosely against ``_TRUTHY``.
_STATUS_HINT_KEYS: frozenset[str] = frozenset(
    {
        "status",
        "state",
        "claim_status",
        "claim_state",
        "wallet_status",
        "connection_status",
        "onboarding_status",
        "phase",
    }
)

# Boolean flags that directly indicate completion when True.
_BOOL_POSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "claimed",
        "claim_complete",
        "claim_completed",
        "ready",
        "is_ready",
        "wallet_ready",
        "provisioned",
        "bootstrap_complete",
    }
)


def _lower_str(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip().lower()
        return s or None
    return None


@dataclass(frozen=True, slots=True)
class ClaimReadyParseResult:
    ready: bool
    """Keys that contributed to the readiness decision (for safe audit metadata only)."""

    matched_keys: tuple[str, ...]


def parse_claim_ready_signal(payload: dict[str, Any]) -> ClaimReadyParseResult:
    """Return whether ``payload`` suggests the user finished the claim / wallet step."""

    matched: list[str] = []

    def _record(key: str) -> None:
        if key not in matched:
            matched.append(key)

    for key in _BOOL_POSITIVE_KEYS:
        if key in payload and payload[key] is True:
            _record(key)

    for key in _STATUS_HINT_KEYS:
        if key not in payload:
            continue
        raw = payload.get(key)
        ls = _lower_str(raw)
        if ls is None and isinstance(raw, dict):
            nested_status = _lower_str(raw.get("status") or raw.get("state"))
            if nested_status in _TRUTHY:
                _record(f"{key}.status")
        elif ls in _TRUTHY:
            _record(key)

    # Nested "claim": {"completed": true} style objects (name varies).
    for container_key in ("claim", "wallet", "connection"):
        inner = payload.get(container_key)
        if not isinstance(inner, dict):
            continue
        for nk, nv in inner.items():
            if nk in _BOOL_POSITIVE_KEYS and nv is True:
                _record(f"{container_key}.{nk}")
            nk_ls = _lower_str(nk)
            if nk_ls in {"completed", "ready", "claimed", "active"} and nv is True:
                _record(f"{container_key}.{nk}")

    ready = bool(matched)
    return ClaimReadyParseResult(ready=ready, matched_keys=tuple(sorted(matched)))


def parse_connected_user_claim_ready(record: dict[str, Any]) -> ClaimReadyParseResult:
    """Parse ``PlatformConnectedUserResponse`` rows from apps users listing."""

    claimed_at = record.get("claimed_at")
    if claimed_at is not None and str(claimed_at).strip():
        return ClaimReadyParseResult(ready=True, matched_keys=("claimed_at",))
    # Fall back to status / flag heuristics on the same row.
    return parse_claim_ready_signal(record)


__all__ = [
    "ClaimReadyParseResult",
    "parse_claim_ready_signal",
    "parse_connected_user_claim_ready",
]
