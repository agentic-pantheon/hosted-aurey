"""Derive Platform connection claim completion signals from defensive JSON parsing.

The live Platform API shape is not guaranteed stable; callers should treat extractors as
best-effort. Readiness uses explicit claim markers (``claimed_at``, ``status: claimed``, etc.),
not agent id presence alone — bootstrap may expose ``agent_ids`` before the user finishes claim.
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
    # Exclude ``active``: 1Claw lists ``status: \"active\"`` before ``claimed_at`` is set.
    claimed_tokens = frozenset({"claimed", "complete", "completed", "ready"})
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


def _first_token_from_uuid_list(*roots: Mapping[str, Any], key: str) -> str | None:
    """First non-empty string element from ``agent_ids`` / ``vault_ids`` style arrays."""

    for m in roots:
        v = m.get(key)
        if isinstance(v, Sequence) and not isinstance(v, (str, bytes)) and len(v) > 0:
            first = v[0]
            if isinstance(first, str) and first.strip():
                return first.strip()
    return None


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


def user_record_for_connection_id(payload: Any, connection_id: str) -> Mapping[str, Any] | None:
    """Find the app user object whose ``connection_id`` matches (list or single-record payloads).

    Accepts common shapes: top-level arrays, ``data`` / ``users`` / ``items`` lists, or a single
    object with ``connection_id`` / nested ``connection.id``.
    """

    cid = (connection_id or "").strip()
    if not cid:
        return None

    def record_matches(rec: Mapping[str, Any]) -> bool:
        for key in ("connection_id", "connectionId"):
            v = rec.get(key)
            if isinstance(v, str) and v.strip() == cid:
                return True
        inner = rec.get("connection")
        if isinstance(inner, Mapping):
            for key in ("id", "connection_id", "connectionId"):
                v = inner.get(key)
                if isinstance(v, str) and v.strip() == cid:
                    return True
        return False

    def walk(node: Any) -> Mapping[str, Any] | None:
        if isinstance(node, Mapping):
            if record_matches(node):
                return node
            for key in ("data", "users", "items", "results", "records", "connections"):
                child = node.get(key)
                found = walk(child)
                if found is not None:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = walk(item)
                if found is not None:
                    return found
        return None

    if isinstance(payload, Mapping) and record_matches(payload):
        return payload
    return walk(payload)


def connection_claim_details(payload: Mapping[str, Any]) -> ConnectionClaimSignals:
    """Merge several extractors; ``looks_claimed`` reflects explicit completion signals only."""

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
        user_agent_id = _first_token_from_uuid_list(*roots, key="agent_ids")
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
    if vault_id is None:
        vault_id = _first_token_from_uuid_list(*roots, key="vault_ids")

    return ConnectionClaimSignals(
        looks_claimed=looks,
        user_agent_id=user_agent_id,
        wallet_address=wallet_address,
        vault_id=vault_id,
    )


def should_mark_connection_ready(signals: ConnectionClaimSignals) -> bool:
    """True when explicit claim completion signals match.

    Agent ids exist on some tenants **before** the user finishes claim (bootstrap provisioning),
    so we do **not** infer readiness from ``user_agent_id`` alone.
    """

    return signals.looks_claimed


__all__ = [
    "ConnectionClaimSignals",
    "connection_claim_details",
    "should_mark_connection_ready",
    "user_record_for_connection_id",
]
