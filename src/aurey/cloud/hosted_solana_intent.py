"""Detect user messages asking for a hosted Solana wallet address."""

from __future__ import annotations

import re

_SOLANA_WALLET_INTENT = re.compile(
    r"""
    (?:
        solana
        |
        sol\s+(?:wallet|address|pubkey|public\s+key)
    )
    .*?
    (?:
        address
        |wallet
        |pubkey
        |public\s+key
        |what(?:'s|\s+is)\s+my
        |where\s+is
        |show\s+me
    )
    |
    (?:
        what(?:'s|\s+is)\s+my
        |where\s+is\s+my
        |show\s+me\s+my
    )
    .*?
    solana
    """,
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)


def message_asks_hosted_solana_wallet(message: str) -> bool:
    """True when the user is likely asking for their provisioned Solana public address."""

    text = (message or "").strip()
    if not text:
        return False
    if _SOLANA_WALLET_INTENT.search(text):
        return True
    lowered = text.lower()
    if "solana" in lowered and any(
        phrase in lowered
        for phrase in (
            "my address",
            "my wallet",
            "wallet address",
            "sol address",
        )
    ):
        return True
    return False


__all__ = ["message_asks_hosted_solana_wallet"]
