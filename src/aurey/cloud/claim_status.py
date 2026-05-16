"""Derive Platform connection claim completion signals from defensive JSON parsing.

The live Platform API shape is not guaranteed stable; callers should treat extractors as
best-effort. When no explicit ``claimed`` marker exists, callers may treat a non-empty
``user_agent_id`` (or ``agent_id`` / nested ``agent.id``) in the payload as sufficient to
infer completion — that heuristic can false-positive if the API lists a template agent before
claim. Documented here so ``refresh_hosted_user_claim_state`` can apply it deliberately.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


def _unwrap_payload(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Prefer root, then typical ``data`` wrapper (may nest further mappings)."""

    out: list[Mapping[str, Any]] = []
    root: Mapping[str, Any] | None = payload if isinstance(payload, Mapping) else None
    if root is not None:
        out.append(root)
        inner = root.get("data")
        if isinstance(inner, Mapping):
            out.append(inner)
    return out


def _first_str(*roots: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    for m in roots:
        for k in keys:
            v = m.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def _truthy_claim_flag(*roots: Mapping[str, Any], keys: tuple[str, ...]) -> bool:
    for m in roots:
        for k in keys:
            if k not in m:
                continue
            v = m.get(k)
            if v is True:
                return True
            if isinstance(v, (int, float)) and v != 0:
                return True
            if isinstance(v, str) and v.strip().lower() in {"true", "1", "yes"}:
                return True
    return False


def _status_claimed_like(*roots: Mapping[str, Any]) -> bool:
    keys = ("status", "connection_status", "state", "onboarding_state")
    claimed_tokens = frozenset({"claimed", "complete", "completed", "ready", "active"})
    for m in roots:
        for k in keys:
            v = m.get(k)
            if isinstance(v, str) and v.strip().lower() in claimed_tokens:
                return True
    return False


def _claimed_at_present(*roots: Mapping[str, Any]) -> bool:
    for m in roots:
        v = m.get("claimed_at")
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        return True
    return False


def _non_empty_sequence(*roots: Mapping[str, Any], key: str) -> bool:
    for m in roots:
        v = m.get(key)
        if isinstance(v, Sequence) and not isinstance(v, (str, bytes)) and len(v) > 0:
            return True
    return False


def _dig_agent_id(payload: Mapping[str, Any]) -> str | None:
    """Resolve ``agent.id`` / ``data.agent.id`` when values are mappings."""

    for m in _unwrap_payload(payload):
        agent = m.get("agent")
        if isinstance(agent, Mapping):
            aid = agent.get("id")
            if isinstance(aid, str) and aid.strip():
                return aid.strip()
    return None


@dataclass(frozen=True)
class ConnectionClaimSignals:
    """Structured hints from a connection GET (or similar) JSON body."""

    looks_claimed: bool
    user_agent_id: str | None
    wallet_address: str | None
    vault_id: str | None


def connection_claim_details(payload: Mapping[str, Any]) -> ConnectionClaimSignals:
    """Merge several extractors; ``looks_claimed`` is True if any *non-id* heuristic matches.

    **Uncertainty:** A non-empty agent id alone does not set ``looks_claimed`` here; callers
    that need the documented fallback should treat ``user_agent_id is not None`` as an
    additional completion signal.
    """

    roots = _unwrap_payload(payload)

    looks = (
        _truthy_claim_flag(*roots, keys=("claimed", "is_claimed", "claim_completed"))
        or _claimed_at_present(*roots)
        or _status_claimed_like(*roots)
        or _non_empty_sequence(*roots, key="agents")
        or _non_empty_sequence(*roots, key="resources")
    )

    user_agent_id = _first_str(
        *roots,
        keys=(
            "user_agent_id",
            "userAgentId",
            "agent_id",
            "agentId",
        ),
    )
    if user_agent_id is None:
        user_agent_id = _dig_agent_id(payload)

    wallet_address = _first_str(
        *roots,
        keys=(
            "wallet_address",
            "walletAddress",
            "address",
            "evm_address",
            "evmAddress",
        ),
    )

    vault_id = _first_str(*roots, keys=("vault_id", "vaultId"))

    return ConnectionClaimSignals(
        looks_claimed=looks,
        user_agent_id=user_agent_id,
        wallet_address=wallet_address,
        vault_id=vault_id,
    )


def should_mark_connection_ready(signals: ConnectionClaimSignals) -> bool:
    """True when explicit claim heuristics match or when an agent binding appears in JSON."""

    if signals.looks_claimed:
        return True
    return bool(signals.user_agent_id)


__all__ = [
    "ConnectionClaimSignals",
    "connection_claim_details",
    "should_mark_connection_ready",
]
