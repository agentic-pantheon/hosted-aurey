"""Read-through cache for ERC-20 ``decimals()`` on :class:`~aurey.runtime.AureyRuntime`."""

from __future__ import annotations

from typing import Any

from aurey.graphs.evm_codec import (
    ERC20_DECIMALS_CALLDATA,
    decode_abi_uint256_word,
    erc20_balance_of_calldata,
    normalize_evm_address,
)
from aurey.graphs.rpc_util import rpc_call_batch
from aurey.runtime import AureyRuntime


def decimals_cache_key(chain_slug: str, token_address: str) -> tuple[str, str]:
    slug = chain_slug.strip().lower()
    return slug, normalize_evm_address(token_address)


def get_cached_erc20_decimals(
    runtime: AureyRuntime,
    *,
    chain_slug: str,
    token_address: str,
    rpc: Any,
) -> int | None:
    """Return ``decimals()`` from cache or a single ``eth_call``."""

    key = decimals_cache_key(chain_slug, token_address)
    hit = runtime.decimals_cache.get(key)
    if hit is not None:
        return hit
    try:
        raw = rpc.call(
            "eth_call",
            [{"to": key[1], "data": ERC20_DECIMALS_CALLDATA}, "latest"],
        )
        if not isinstance(raw, str):
            return None
        value = decode_abi_uint256_word(raw)
        if value > 255:
            return None
        dec = int(value)
    except (ValueError, TypeError, RuntimeError):
        return None
    runtime.decimals_cache.set(key, dec)
    return dec


def fetch_erc20_decimals_and_balance_raw(
    runtime: AureyRuntime,
    *,
    chain_slug: str,
    token_address: str,
    wallet_address: str,
    rpc: Any,
) -> tuple[int | None, int | None]:
    """Fetch decimals (cached) and ``balanceOf`` with at most one RPC batch."""

    token = normalize_evm_address(token_address)
    wallet = normalize_evm_address(wallet_address)
    key = decimals_cache_key(chain_slug, token)
    cached_dec = runtime.decimals_cache.get(key)
    if cached_dec is not None:
        try:
            bal_raw = rpc.call(
                "eth_call",
                [{"to": token, "data": erc20_balance_of_calldata(wallet)}, "latest"],
            )
            if not isinstance(bal_raw, str):
                return cached_dec, None
            return cached_dec, decode_abi_uint256_word(bal_raw)
        except (ValueError, TypeError, RuntimeError):
            return cached_dec, None

    try:
        dec_raw, bal_raw = rpc_call_batch(
            rpc,
            [
                ("eth_call", [{"to": token, "data": ERC20_DECIMALS_CALLDATA}, "latest"]),
                ("eth_call", [{"to": token, "data": erc20_balance_of_calldata(wallet)}, "latest"]),
            ],
        )
    except (ValueError, TypeError, RuntimeError):
        return None, None

    if not isinstance(dec_raw, str) or not isinstance(bal_raw, str):
        return None, None
    try:
        decimals = decode_abi_uint256_word(dec_raw)
        if decimals > 255:
            return None, None
        dec_int = int(decimals)
        balance_raw = decode_abi_uint256_word(bal_raw)
    except ValueError:
        return None, None

    runtime.decimals_cache.set(key, dec_int)
    return dec_int, balance_raw


__all__ = [
    "decimals_cache_key",
    "fetch_erc20_decimals_and_balance_raw",
    "get_cached_erc20_decimals",
]
