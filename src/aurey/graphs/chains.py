"""Small chain metadata helpers (Mercury-parity naming, minimal surface)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChainInfo:
    name: str
    chain_id: int
    alchemy_network: str


CHAIN_INDEX: dict[str, ChainInfo] = {
    "arbitrum": ChainInfo("arbitrum", 42161, "arb-mainnet"),
    "avalanche": ChainInfo("avalanche", 43114, "avax-mainnet"),
    "base": ChainInfo("base", 8453, "base-mainnet"),
    "berachain": ChainInfo("berachain", 80094, "berachain-mainnet"),
    "bsc": ChainInfo("bsc", 56, "bnb-mainnet"),
    "celo": ChainInfo("celo", 42220, "celo-mainnet"),
    "ethereum": ChainInfo("ethereum", 1, "eth-mainnet"),
    "gnosis": ChainInfo("gnosis", 100, "gnosis-mainnet"),
    "katana": ChainInfo("katana", 747474, "katana-mainnet"),
    "linea": ChainInfo("linea", 59144, "linea-mainnet"),
    "mantle": ChainInfo("mantle", 5000, "mantle-mainnet"),
    "monad": ChainInfo("monad", 143, "monad-mainnet"),
    "optimism": ChainInfo("optimism", 10, "opt-mainnet"),
    "plasma": ChainInfo("plasma", 9745, "plasma-mainnet"),
    "polygon": ChainInfo("polygon", 137, "polygon-mainnet"),
    "scroll": ChainInfo("scroll", 534352, "scroll-mainnet"),
    "sonic": ChainInfo("sonic", 146, "sonic-mainnet"),
    "unichain": ChainInfo("unichain", 130, "unichain-mainnet"),
}


def chain_info(name: str) -> ChainInfo | None:
    key = name.strip().lower()
    return CHAIN_INDEX.get(key)


def alchemy_rpc_url_for_chain(name: str, api_key: str) -> str | None:
    info = chain_info(name)
    if info is None:
        return None
    return f"https://{info.alchemy_network}.g.alchemy.com/v2/{api_key}"


def chain_id_for(name: str) -> int | None:
    info = chain_info(name)
    return None if info is None else info.chain_id


def chain_name_for_id(chain_id: int) -> str | None:
    """Return canonical chain slug (e.g. ``base``) for a numeric chain id, if known."""

    for name, info in CHAIN_INDEX.items():
        if info.chain_id == chain_id:
            return name
    return None
