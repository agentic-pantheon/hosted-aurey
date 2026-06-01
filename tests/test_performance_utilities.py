"""Tests for performance-related caches, RPC batching, and latency helpers."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from aurey.custody.caching_secret_store import CachingSecretStore
from aurey.custody.errors import SecretNotFoundError
from aurey.custody.secret_store import FakeSecretStore, SecretValue
from aurey.graphs.rpc_util import rpc_call_batch
from aurey.util.ttl_lru_cache import TtlLruCache
from tests.fakes.evm_rpc import ScriptedEvmJsonRpc


def test_ttl_lru_cache_hit_miss_and_expiry() -> None:
    cache: TtlLruCache[str, int] = TtlLruCache(maxsize=2, ttl_s=0.05)
    cache.set("a", 1)
    assert cache.get("a") == 1
    assert cache.get("missing") is None
    time.sleep(0.06)
    assert cache.get("a") is None


def test_caching_secret_store_caches_success_not_errors() -> None:
    inner = FakeSecretStore({"vault/alchemy": "key-one"})
    cached = CachingSecretStore(inner, ttl_s=60.0)
    first = cached.get_secret("vault/alchemy").reveal()
    second = cached.get_secret("vault/alchemy").reveal()
    assert first == second == "key-one"
    assert len(inner._secrets) == 1

    broken = FakeSecretStore({})
    cached2 = CachingSecretStore(broken, ttl_s=60.0)
    with pytest.raises(SecretNotFoundError):
        cached2.get_secret("nope")
    with pytest.raises(SecretNotFoundError):
        cached2.get_secret("nope")


def test_rpc_call_batch_scripted_order() -> None:
    rpc = ScriptedEvmJsonRpc({"eth_call": lambda _p: "0x" + "0" * 64})
    out = rpc_call_batch(
        rpc,
        [
            ("eth_call", [{"to": "0x1", "data": "0x"}]),
            ("eth_call", [{"to": "0x2", "data": "0x"}]),
        ],
    )
    assert len(out) == 2


def test_fetch_erc20_decimals_and_balance_uses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    from aurey.graphs.cached_decimals import fetch_erc20_decimals_and_balance_raw
    from aurey.runtime import AureyRuntime
    from aurey.settings import AureySettings
    from aurey.util.ttl_lru_cache import TtlLruCache
    from tests.fakes.evm_rpc import rpc_factory_from_mapping

    dec_word = "0x" + format(6, "064x")
    bal_word = "0x" + format(1_000_000, "064x")
    calls: list[str] = []

    def eth_call(params):
        calls.append(params[0]["data"][:10])
        if params[0]["data"].startswith("0x313ce567"):
            return dec_word
        return bal_word

    runtime = AureyRuntime(
        settings=AureySettings(),
        secret_store=FakeSecretStore({}),
        evm_rpc_factory=rpc_factory_from_mapping({"eth_call": eth_call}),
        http=MagicMock(),
        tx_pipeline=MagicMock(),
        decimals_cache=TtlLruCache(maxsize=16, ttl_s=3600.0),
    )
    rpc = runtime.evm_rpc_factory("http://rpc.example")

    d1, b1 = fetch_erc20_decimals_and_balance_raw(
        runtime,
        chain_slug="base",
        token_address="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        wallet_address="0x0000000000000000000000000000000000000001",
        rpc=rpc,
    )
    assert d1 == 6 and b1 == 1_000_000
    assert len(calls) == 2

    d2, b2 = fetch_erc20_decimals_and_balance_raw(
        runtime,
        chain_slug="base",
        token_address="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        wallet_address="0x0000000000000000000000000000000000000001",
        rpc=rpc,
    )
    assert d2 == 6 and b2 == 1_000_000
    assert len(calls) == 3
