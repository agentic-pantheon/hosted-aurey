"""EVM block explorer base URLs for transaction links."""

from __future__ import annotations

CHAIN_TX_EXPLORER_BASE: dict[int, str] = {
    1: "https://etherscan.io",
    8453: "https://basescan.org",
    42161: "https://arbiscan.io",
    10: "https://optimistic.etherscan.io",
    137: "https://polygonscan.com",
    56: "https://bscscan.com",
    59144: "https://lineascan.build",
    534352: "https://scrollscan.com",
    324: "https://explorer.zksync.io",
    43114: "https://snowtrace.io",
}


def explorer_tx_url(chain_id: int, tx_hash: str | None) -> str | None:
    """Full explorer URL for a transaction hash, if the chain is known."""

    base = CHAIN_TX_EXPLORER_BASE.get(chain_id)
    if base is None or not tx_hash:
        return None
    h = tx_hash.strip()
    if not h.startswith("0x") or len(h) != 66:
        return None
    return f"{base}/tx/{h}"


__all__ = ["CHAIN_TX_EXPLORER_BASE", "explorer_tx_url"]
