"""Normalize human-readable token names for deterministic allowlist lookup."""

from __future__ import annotations


def normalize_token_lookup_name(name: str) -> str:
    """Lowercase, trim, collapse internal whitespace (exact match key)."""

    return " ".join(name.strip().lower().split())
