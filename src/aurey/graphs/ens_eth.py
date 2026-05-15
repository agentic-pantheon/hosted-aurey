"""Ethereum mainnet ENS forward resolution (registry + resolver ``addr``)."""

from __future__ import annotations

from web3 import Web3

from aurey.graphs.evm_codec import normalize_evm_address

# Mainnet ENSRegistry:
# github.com/ensdomains/ens-contracts/blob/main/deployments/mainnet/ENSRegistry.json
ENS_REGISTRY_MAINNET = normalize_evm_address("0x00000000000C2E074eC69A0dFb2997BA6C7d2e1e")

# keccak256("resolver(bytes32)")[:4], keccak256("addr(bytes32)")[:4]
_RESOLVER_SEL = "0178b8bf"
_ADDR_SEL = "3b3b57de"


def normalize_ens_query_name(name: str) -> str:
    """Normalize user-facing ENS input (trim + lower case — not full UTS-46)."""

    return name.strip().lower()


def ens_namehash(name: str) -> bytes:
    """EIP-137 ``namehash`` for a dot-separated ENS name."""

    node = bytes(32)
    for label in reversed(name.split(".")):
        if label == "":
            raise ValueError("Invalid ENS name (empty label).")
        node = Web3.keccak(node + Web3.keccak(primitive=label.encode("utf-8")))
    return node


def ens_resolver_calldata(node: bytes) -> str:
    if len(node) != 32:
        raise ValueError("name node must be 32 bytes.")
    return "0x" + _RESOLVER_SEL + node.hex()


def ens_addr_calldata(node: bytes) -> str:
    if len(node) != 32:
        raise ValueError("name node must be 32 bytes.")
    return "0x" + _ADDR_SEL + node.hex()


def decode_abi_address_word(result_hex: str) -> str:
    """Decode a single 32-byte ABI word containing an ``address`` (right-padded)."""

    raw = (result_hex or "").strip().lower()
    if not raw.startswith("0x") or len(raw) != 66:
        raise ValueError("eth_call result must be a 32-byte ABI word.")
    addr_body = raw[-40:]  # 20-byte address (right-aligned in word)
    return normalize_evm_address("0x" + addr_body)


def is_zero_address(addr: str) -> bool:
    return normalize_evm_address(addr) == normalize_evm_address(
        "0x0000000000000000000000000000000000000000"
    )
