"""Helpers for values that must survive LangGraph checkpoint serialization.

LangGraph uses ``ormsgpack`` for serde; integer slots are signed 64-bit, so on-chain
``uint256``-sized amounts must not be stored as Python ``int`` in graph state.
"""

from __future__ import annotations


def uint256_checkpoint_str(n: int) -> str:
    """Return a base-10 string for a non-negative integer (e.g. raw token balance).

    Use for wei / raw token units that may exceed ``2**63 - 1`` when persisting
    graph checkpoints.
    """

    if n < 0:
        raise ValueError("uint256_checkpoint_str expects a non-negative int.")
    return str(n)
