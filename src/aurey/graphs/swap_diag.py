"""Structured swap / LiFi diagnostics (logger ``aurey.swap``, INFO-level timings).

Use these logs to separate: LangGraph/tool overhead, LiFi HTTP latency, on-chain allowance
reads, envelope build, and Web3 simulate/sign/broadcast for ``lifi_swap`` envelopes.
"""

from __future__ import annotations

import logging
from typing import Any

SWAP_LOG = logging.getLogger("aurey.swap")


def addr_short(addr: str) -> str:
    a = (addr or "").strip().lower()
    if len(a) < 20:
        return a or "(empty)"
    return f"{a[:10]}…{a[-4:]}"


def log_swap_tool(*, name: str, wall_ms: float, ok: bool | None, **fields: Any) -> None:
    """One line per LangChain tool invocation (wall clock includes graph + I/O)."""

    parts = [f"name={name}", f"wall_ms={wall_ms:.1f}", f"ok={ok}"]
    for k, v in sorted(fields.items()):
        if v is not None:
            parts.append(f"{k}={v}")
    SWAP_LOG.info("swap_tool %s", "  ".join(parts))
