"""On-chain ERC-20 verification (``decimals()`` is authoritative)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aurey.graphs.evm_codec import ERC20_DECIMALS_CALLDATA, decode_abi_uint256_word, normalize_evm_address

if TYPE_CHECKING:
    from aurey.runtime import AureyRuntime


def read_erc20_decimals(
    rpc: Any,
    token_address: str,
    *,
    runtime: AureyRuntime | None = None,
    chain_slug: str | None = None,
) -> int | None:
    """Return token ``decimals()`` or ``None`` when the call fails or is not ERC-20-like."""

    if runtime is not None and chain_slug:
        from aurey.graphs.cached_decimals import get_cached_erc20_decimals

        return get_cached_erc20_decimals(
            runtime,
            chain_slug=chain_slug,
            token_address=token_address,
            rpc=rpc,
        )

    token = normalize_evm_address(token_address)
    try:
        raw = rpc.call(
            "eth_call",
            [{"to": token, "data": ERC20_DECIMALS_CALLDATA}, "latest"],
        )
        if not isinstance(raw, str):
            return None
        value = decode_abi_uint256_word(raw)
        if value > 255:
            return None
        return int(value)
    except (ValueError, TypeError, Exception):
        return None
