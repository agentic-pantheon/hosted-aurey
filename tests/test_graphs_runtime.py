"""Runtime + LangGraph coverage (fake SecretStore and scripted clients)."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

import ormsgpack

from aurey.custody import FakeOneClawClient, FakeSecretStore, OneClawEvmTransactionSigner
from aurey.custody.errors import SecretStoreUnavailableError
from aurey.graphs import (
    DeterministicTxPipeline,
    build_alchemy_graph,
    build_earn_graph,
    build_lifi_status_graph,
    build_read_graph,
    build_swap_prepare_graph,
    build_tx_execute_graph,
    build_tx_prepare_graph,
    build_tx_prepare_lifi_graph,
)
from aurey.graphs.ens_eth import (
    ENS_REGISTRY_MAINNET,
    ens_addr_calldata,
    ens_namehash,
    ens_resolver_calldata,
)
from aurey.graphs.evm_codec import to_checksum_evm_address
from aurey.graphs.ports import HttpJsonPort, HttpJsonRequestError
from aurey.runtime import AureyRuntime
from aurey.settings import AureySettings
from tests.fakes.evm_rpc import rpc_factory_from_mapping
from tests.fakes.http_client import ScriptedHttpClient


class _LifiUnauthorizedHttp(HttpJsonPort):
    def request_json(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | list[Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        _ = method, url, headers, json_body
        raise HttpJsonRequestError(
            status_code=401,
            body_text='{"message":"Invalid API key","code":1010}',
            payload={"message": "Invalid API key", "code": 1010},
        )


class _EarnHttpJsonError(HttpJsonPort):
    """Raise :class:`HttpJsonRequestError` for Earn or LiFi status requests (status-code coverage)."""

    def __init__(
        self,
        *,
        status_code: int,
        body_text: str,
        payload: dict[str, Any] | None,
    ) -> None:
        self._status_code = status_code
        self._body_text = body_text
        self._payload = payload

    def request_json(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | list[Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        _ = method, url, headers, json_body
        raise HttpJsonRequestError(
            status_code=self._status_code,
            body_text=self._body_text,
            payload=self._payload,
        )


class _UnavailableSigningKeyStore:
    def get_secret(self, path: str):
        raise SecretStoreUnavailableError(
            "/v1/auth/agent-token",
            store_name="1Claw",
            detail="Agent token exchange failed with HTTP 401. Check agent id and bootstrap API key.",
        )


def _runtime(
    *,
    secrets: dict[str, str],
    settings: AureySettings,
    http: HttpJsonPort,
    rpc_map: dict[str, object],
    oneclaw_evm_signer: OneClawEvmTransactionSigner | None = None,
    secret_store: Any | None = None,
) -> AureyRuntime:
    store = secret_store if secret_store is not None else FakeSecretStore(secrets)
    return AureyRuntime(
        settings=settings,
        secret_store=store,
        evm_rpc_factory=rpc_factory_from_mapping(rpc_map),
        http=http,
        tx_pipeline=DeterministicTxPipeline(),
        lifi_base_url="https://li.quest",
        oneclaw_evm_signer=oneclaw_evm_signer,
    )


def _ban_values() -> tuple[str, ...]:
    return (
        "INJECTED_RPC_URL_SECRET_FRAGMENT",
        "INJECTED_ALCHEMY_KEY_AAA",
        "INJECTED_LIFI_KEY_BBB",
        "0x" + "ff" * 32,
    )


def _assert_no_banned_values(payload: object) -> None:
    blob = json.dumps(payload, default=str, sort_keys=True)
    for fragment in _ban_values():
        assert fragment not in blob


def test_read_native_balance_graph():
    alchemy_path = "vault/alchemy/1"
    signing_path = "vault/signing/local"
    secrets = {
        alchemy_path: "INJECTED_ALCHEMY_KEY_AAA",
        "vault/lifi/1": "INJECTED_LIFI_KEY_BBB",
        signing_path: "0x" + "ff" * 32,
    }
    settings = AureySettings(
        alchemy_api_secret_path=alchemy_path,
        lifi_api_secret_path="vault/lifi/1",
        wallet_signing_key_secret_path=signing_path,
    )
    http = ScriptedHttpClient()
    rpc_urls: list[str] = []

    def rpc_factory(url: str):
        rpc_urls.append(url)
        return rpc_factory_from_mapping({"eth_getBalance": "0x10"})(url)

    runtime = _runtime(
        secrets=secrets,
        settings=settings,
        http=http,
        rpc_map={},
    )
    runtime = AureyRuntime(
        settings=runtime.settings,
        secret_store=runtime.secret_store,
        evm_rpc_factory=rpc_factory,
        http=runtime.http,
        tx_pipeline=runtime.tx_pipeline,
        lifi_base_url=runtime.lifi_base_url,
    )
    graph = build_read_graph(runtime)
    out = graph.invoke(
        {
            "input": {
                "operation": "native_balance",
                "chain": "ethereum",
                "wallet_address": "0x0000000000000000000000000000000000000001",
            }
        }
    )
    assert out.get("error") is None
    assert out["result"]["balance_wei_hex"] == "0x10"
    assert out["result"]["balance_wei"] == 16
    assert out["result"]["balance_eth"] == "0.000000000000000016"
    assert rpc_urls == ["https://eth-mainnet.g.alchemy.com/v2/INJECTED_ALCHEMY_KEY_AAA"]
    _assert_no_banned_values(out)


def test_read_erc20_decimals_graph():
    alchemy_path = "vault/alchemy/x"
    secrets = {alchemy_path: "INJECTED_ALCHEMY_KEY_AAA"}
    settings = AureySettings(alchemy_api_secret_path=alchemy_path)

    def eth_call(params: list) -> str:
        assert params[0]["data"] == "0x313ce567"
        assert params[1] == "latest"
        return "0x0000000000000000000000000000000000000000000000000000000000000006"

    runtime = AureyRuntime(
        settings=settings,
        secret_store=FakeSecretStore(secrets),
        evm_rpc_factory=rpc_factory_from_mapping({"eth_call": eth_call}),
        http=ScriptedHttpClient(),
        tx_pipeline=DeterministicTxPipeline(),
        lifi_base_url="https://li.quest",
    )
    graph = build_read_graph(runtime)
    tok = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    out = graph.invoke(
        {
            "input": {
                "operation": "erc20_decimals",
                "chain": "base",
                "token_address": tok,
            }
        }
    )
    assert out.get("error") is None
    assert out["result"]["decimals"] == 6
    assert out["result"]["chain_id"] == 8453
    assert out["result"]["token_address"] == tok.lower()
    _assert_no_banned_values(out)


def test_read_ens_resolve_graph():
    alchemy_path = "vault/alchemy/y"
    secrets = {alchemy_path: "INJECTED_ALCHEMY_KEY_AAA"}
    settings = AureySettings(alchemy_api_secret_path=alchemy_path)

    ens_name = "foo.eth"
    node = ens_namehash(ens_name)
    resolver_addr = "0x2222222222222222222222222222222222222222"
    resolved_wallet = "0xd8da6bf26964af9d7eed9e03e53415d37aa96045"
    padded_resolver = "0x" + "0" * 24 + resolver_addr[2:]
    padded_wallet = "0x" + "0" * 24 + resolved_wallet[2:]
    expected_registry = ENS_REGISTRY_MAINNET.lower()
    resolver_l = resolver_addr.lower()

    def eth_call(params: list) -> str:
        body = params[0]
        to_l = body["to"].lower()
        data = body["data"].lower()
        if to_l == expected_registry:
            assert data == ens_resolver_calldata(node).lower()
            return padded_resolver
        if to_l == resolver_l:
            assert data == ens_addr_calldata(node).lower()
            return padded_wallet
        raise AssertionError((to_l, data[:10]))

    runtime = AureyRuntime(
        settings=settings,
        secret_store=FakeSecretStore(secrets),
        evm_rpc_factory=rpc_factory_from_mapping({"eth_call": eth_call}),
        http=ScriptedHttpClient(),
        tx_pipeline=DeterministicTxPipeline(),
        lifi_base_url="https://li.quest",
    )
    graph = build_read_graph(runtime)
    out = graph.invoke(
        {
            "input": {
                "operation": "ens_resolve",
                "chain": "ethereum",
                "ens_name": "  FOO.ETH ",
            },
        }
    )
    assert out.get("error") is None
    assert out["result"]["name"] == ens_name
    assert out["result"]["resolved_address"] == resolved_wallet.lower()
    assert out["result"]["chain"] == "ethereum"
    assert out["result"]["chain_id"] == 1
    _assert_no_banned_values(out)


def test_read_ens_resolve_unsupported_chain():
    alchemy_path = "vault/alchemy/z"
    secrets = {alchemy_path: "INJECTED_ALCHEMY_KEY_AAA"}
    settings = AureySettings(alchemy_api_secret_path=alchemy_path)
    runtime = AureyRuntime(
        settings=settings,
        secret_store=FakeSecretStore(secrets),
        evm_rpc_factory=rpc_factory_from_mapping({}),
        http=ScriptedHttpClient(),
        tx_pipeline=DeterministicTxPipeline(),
        lifi_base_url="https://li.quest",
    )
    graph = build_read_graph(runtime)
    out = graph.invoke(
        {
            "input": {
                "operation": "ens_resolve",
                "chain": "base",
                "ens_name": "x.eth",
            },
        }
    )
    assert out.get("result") is None
    assert out["error"]["code"] == "unsupported_chain"


def test_read_ens_resolve_no_resolver_returns_ens_not_found():
    alchemy_path = "vault/alchemy/nf"
    secrets = {alchemy_path: "INJECTED_ALCHEMY_KEY_AAA"}
    settings = AureySettings(alchemy_api_secret_path=alchemy_path)
    padded_zero = "0x" + "0" * 64

    def eth_call(params: list) -> str:
        body = params[0]
        assert body["to"].lower() == ENS_REGISTRY_MAINNET.lower()
        assert body["data"].lower().startswith("0x0178b8bf")
        assert params[1] == "latest"
        return padded_zero

    runtime = AureyRuntime(
        settings=settings,
        secret_store=FakeSecretStore(secrets),
        evm_rpc_factory=rpc_factory_from_mapping({"eth_call": eth_call}),
        http=ScriptedHttpClient(),
        tx_pipeline=DeterministicTxPipeline(),
        lifi_base_url="https://li.quest",
    )
    graph = build_read_graph(runtime)
    out = graph.invoke(
        {
            "input": {
                "operation": "ens_resolve",
                "chain": "Ethereum",
                "ens_name": "does-not-exist-12345.eth",
            },
        }
    )
    assert out["error"]["code"] == "ens_not_found"


def test_read_known_address_graph():
    secrets = {}
    settings = AureySettings()
    runtime = _runtime(
        secrets=secrets,
        settings=settings,
        http=ScriptedHttpClient(),
        rpc_map={},
    )
    graph = build_read_graph(runtime)
    out = graph.invoke(
        {"input": {"operation": "known_address", "chain": "ethereum", "known_ticker": "usdc"}}
    )
    assert out["result"]["resolved_address"] == "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
    assert out["result"]["symbol"] == "USDC"
    assert out["result"]["name"] == "USD Coin"
    _assert_no_banned_values(out)


def test_alchemy_token_prices_graph():
    secrets = {"vault/alchemy": "INJECTED_ALCHEMY_KEY_AAA"}
    settings = AureySettings(alchemy_api_secret_path="vault/alchemy")

    def match_prices(**kw: object) -> bool:
        if kw.get("method") != "POST":
            return False
        url = str(kw.get("url") or "")
        if "/prices/v1/" not in url or "tokens/by-address" not in url:
            return False
        body = kw.get("json_body") or {}
        return isinstance(body, dict) and isinstance(body.get("addresses"), list)

    http = ScriptedHttpClient(
        [
            (
                match_prices,
                {
                    "data": [
                        {
                            "network": "eth-mainnet",
                            "address": "0x2222222222222222222222222222222222222222",
                            "prices": [
                                {
                                    "currency": "USD",
                                    "value": "3.14",
                                    "lastUpdatedAt": "2025-01-01T00:00:00Z",
                                }
                            ],
                            "error": None,
                        }
                    ]
                },
            )
        ]
    )
    runtime = _runtime(
        secrets=secrets,
        settings=settings,
        http=http,
        rpc_map={},
    )
    graph = build_alchemy_graph(runtime)
    out = graph.invoke(
        {
            "input": {
                "operation": "token_prices",
                "chain": "ethereum",
                "wallet_address": "0x1111111111111111111111111111111111111111",
                "token_addresses": ["0x2222222222222222222222222222222222222222"],
            }
        }
    )
    addr = "0x2222222222222222222222222222222222222222"
    assert out["result"]["prices_by_address"][addr] == "3.14"
    _assert_no_banned_values(out)


def test_alchemy_usd_notional_to_raw_graph():
    """$5 at $80k/BTC with 8 decimals rounds down to 6250 raw; includes balance check."""

    secrets = {"vault/alchemy": "INJECTED_ALCHEMY_KEY_AAA"}
    settings = AureySettings(alchemy_api_secret_path="vault/alchemy")
    wallet = "0x4444444444444444444444444444444444444444"
    tok = "0x2222222222222222222222222222222222222222"

    def match_prices(**kw: object) -> bool:
        if kw.get("method") != "POST":
            return False
        url = str(kw.get("url") or "")
        if "/prices/v1/" not in url or "tokens/by-address" not in url:
            return False
        body = kw.get("json_body") or {}
        addrs = body.get("addresses") if isinstance(body, dict) else None
        return (
            isinstance(addrs, list)
            and len(addrs) == 1
            and addrs[0].get("address", "").lower() == tok.lower()
        )

    def eth_call(params: list) -> str:
        data = params[0]["data"].lower()
        if data.startswith("0x313ce567"):
            return "0x0000000000000000000000000000000000000000000000000000000000000008"
        if data.startswith("0x70a08231"):
            return "0x000000000000000000000000000000000000000000000000000000000000f4de"
        raise AssertionError(f"unexpected eth_call {data[:12]}")

    http = ScriptedHttpClient(
        [
            (
                match_prices,
                {
                    "data": [
                        {
                            "network": "base-mainnet",
                            "address": tok.lower(),
                            "prices": [
                                {
                                    "currency": "USD",
                                    "value": "80000",
                                    "lastUpdatedAt": "2025-01-01T00:00:00Z",
                                }
                            ],
                            "error": None,
                        }
                    ],
                },
            ),
        ]
    )
    runtime = _runtime(
        secrets=secrets,
        settings=settings,
        http=http,
        rpc_map={"eth_call": eth_call},
    )
    out = build_alchemy_graph(runtime).invoke(
        {
            "input": {
                "operation": "usd_notional_to_raw",
                "chain": "base",
                "wallet_address": wallet,
                "token_address": tok,
                "usd_notional": "5",
            }
        }
    )
    assert out.get("error") is None
    res = out["result"]
    assert res["amount_raw"] == "6250"
    assert res["human_token_amount"] == "0.0000625"
    assert res["decimals"] == 8
    assert res["price_usd"] == "80000"
    assert res["usd_notional"] == "5"
    assert res["wallet_balance_raw"] == "62686"
    assert res["balance_covers_notional_amount"] is True
    _assert_no_banned_values(out)


def test_alchemy_portfolio_and_transfers_graphs():
    secrets = {"vault/alchemy": "INJECTED_ALCHEMY_KEY_AAA"}
    settings = AureySettings(alchemy_api_secret_path="vault/alchemy")
    wallet = "0x1111111111111111111111111111111111111111"

    def match_portfolio(**kw: object) -> bool:
        if kw.get("method") != "POST":
            return False
        u = str(kw.get("url") or "")
        return "/data/v1/" in u and "assets/tokens/by-address" in u

    def match_transfers_from(**kw: object) -> bool:
        body = kw.get("json_body") or {}
        params = body.get("params") if isinstance(body, dict) else None
        block = params[0] if isinstance(params, list) and params else {}
        return (
            kw.get("method") == "POST"
            and ".g.alchemy.com/v2/" in str(kw.get("url") or "")
            and isinstance(body, dict)
            and body.get("method") == "alchemy_getAssetTransfers"
            and isinstance(block, dict)
            and "fromAddress" in block
        )

    def match_transfers_to(**kw: object) -> bool:
        body = kw.get("json_body") or {}
        params = body.get("params") if isinstance(body, dict) else None
        block = params[0] if isinstance(params, list) and params else {}
        return (
            kw.get("method") == "POST"
            and ".g.alchemy.com/v2/" in str(kw.get("url") or "")
            and isinstance(body, dict)
            and body.get("method") == "alchemy_getAssetTransfers"
            and isinstance(block, dict)
            and "toAddress" in block
        )

    http = ScriptedHttpClient(
        [
            (
                match_portfolio,
                {
                    "data": {
                        "tokens": [
                            {
                                "address": wallet,
                                "network": "base-mainnet",
                                "tokenAddress": None,
                                "tokenBalance": "1000000000000000000",
                                "tokenMetadata": {
                                    "decimals": 18,
                                    "symbol": "ETH",
                                    "name": "Ether",
                                },
                            },
                            {
                                "address": wallet,
                                "network": "base-mainnet",
                                "tokenAddress": "0x2222222222222222222222222222222222222222",
                                "tokenBalance": (
                                    "0x000000000000000000000000000000000000000000000000"
                                    "0000000000000f569"
                                ),
                                "tokenMetadata": {
                                    "decimals": "6",
                                    "symbol": "USDC",
                                    "name": "USD Coin",
                                },
                            },
                            {
                                "address": wallet,
                                "network": "base-mainnet",
                                "tokenAddress": "0x3333333333333333333333333333333333333333",
                                "tokenBalance": hex(2**63),
                                "tokenMetadata": {
                                    "decimals": 18,
                                    "symbol": "HUGE",
                                    "name": "Int64 overflow balance fixture",
                                },
                            },
                        ]
                    }
                },
            ),
            (
                match_transfers_from,
                {"result": {"transfers": [{"uniqueId": "t-high", "blockNum": "0x10"}]}},
            ),
            (
                match_transfers_to,
                {"result": {"transfers": [{"uniqueId": "t-low", "blockNum": "0x5"}]}},
            ),
        ]
    )
    runtime = _runtime(secrets=secrets, settings=settings, http=http, rpc_map={})

    portfolio = build_alchemy_graph(runtime).invoke(
        {
            "input": {
                "operation": "portfolio_tokens",
                "chain": "base",
                "wallet_address": wallet,
            }
        }
    )
    assert portfolio["result"]["tokens"][0]["tokenMetadata"]["symbol"] == "ETH"
    assert portfolio["result"]["tokens"][0]["balance_raw"] == "1000000000000000000"
    assert portfolio["result"]["tokens"][0]["decimals"] == 18
    assert portfolio["result"]["tokens"][0]["balance_decimal"] == "1"
    assert portfolio["result"]["tokens"][1]["balance_raw"] == "62825"
    assert portfolio["result"]["tokens"][1]["decimals"] == 6
    assert portfolio["result"]["tokens"][1]["balance_decimal"] == "0.062825"
    assert portfolio["result"]["tokens"][2]["balance_raw"] == str(2**63)
    assert portfolio["result"]["tokens"][2]["decimals"] == 18
    ormsgpack.packb(portfolio["result"])
    _assert_no_banned_values(portfolio)

    transfers = build_alchemy_graph(runtime).invoke(
        {
            "input": {
                "operation": "transfer_history",
                "chain": "base",
                "wallet_address": wallet,
            }
        }
    )
    assert transfers["result"]["transfers"][0]["uniqueId"] == "t-high"
    assert {t["uniqueId"] for t in transfers["result"]["transfers"]} == {"t-high", "t-low"}
    _assert_no_banned_values(transfers)


def test_swap_prepare_graph():
    secrets = {"vault/lifi": "INJECTED_LIFI_KEY_BBB"}
    settings = AureySettings(lifi_api_secret_path="vault/lifi")

    def match_quote(**kw: object) -> bool:
        headers = kw.get("headers") or {}
        u = str(kw.get("url") or "")
        return (
            kw.get("method") == "GET"
            and "li.quest" in u
            and "/v1/quote?" in u
            and "fromChain=1" in u
            and "toChain=8453" in u
            and isinstance(headers, dict)
            and headers.get("x-lifi-api-key") == "INJECTED_LIFI_KEY_BBB"
            and "integrator=aurey" in u
        )

    http = ScriptedHttpClient(
        [
            (
                match_quote,
                {
                    "id": "swap-route-1",
                    "estimate": {
                        "approvalAddress": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    },
                    "action": {
                        "fromAmount": "1000000",
                        "fromToken": {
                            "address": "0x1111111111111111111111111111111111111111",
                        },
                    },
                    "transactionRequest": {
                        "to": "0x3333333333333333333333333333333333333333",
                        "data": "0x",
                    },
                },
            )
        ]
    )
    runtime = _runtime(
        secrets=secrets,
        settings=settings,
        http=http,
        rpc_map={},
    )
    out = build_swap_prepare_graph(runtime).invoke(
        {
            "input": {
                "from_chain": "ethereum",
                "to_chain": "base",
                "from_asset": "0x1111111111111111111111111111111111111111",
                "to_asset": "0x2222222222222222222222222222222222222222",
                "from_amount_wei": "1000000",
                "from_address": "0x4444444444444444444444444444444444444444",
                "to_address": "0x5555555555555555555555555555555555555555",
            }
        }
    )
    assert out["result"]["prepared"]["route_id"] == "swap-route-1"
    al = out["result"]["allowance"]
    assert al is not None
    assert al["token_address"] == "0x1111111111111111111111111111111111111111"
    assert al["spender_address"] == "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert al["amount_raw"] == "1000000"
    ctx = out["result"]["allowance_context"]
    assert ctx is not None
    assert ctx["token_address"] == al["token_address"]
    assert ctx["spender_address"] == al["spender_address"]
    assert ctx["amount_raw"] == "1000000"
    assert ctx["current_allowance_raw"] is None
    assert ctx["allowance_sufficient"] is None
    _assert_no_banned_values(out)


def test_swap_prepare_graph_quote_url_includes_slippage_order_integrator():
    """LiFi GET /v1/quote query matches OpenAPI-style integrator, slippage, order."""

    secrets = {"vault/lifi": "INJECTED_LIFI_KEY_BBB"}
    settings = AureySettings(lifi_api_secret_path="vault/lifi")
    urls: list[str] = []

    def match_quote(**kw: object) -> bool:
        urls.append(str(kw.get("url") or ""))
        return kw.get("method") == "GET" and "/v1/quote?" in str(kw.get("url") or "")

    http = ScriptedHttpClient(
        [
            (
                match_quote,
                {
                    "id": "q-slippage",
                    "transactionRequest": {
                        "to": "0x3333333333333333333333333333333333333333",
                        "data": "0x",
                    },
                },
            )
        ]
    )
    runtime = _runtime(secrets=secrets, settings=settings, http=http, rpc_map={})
    out = build_swap_prepare_graph(runtime).invoke(
        {
            "input": {
                "from_chain": "ethereum",
                "to_chain": "base",
                "from_asset": "usdc",
                "to_asset": "eth",
                "from_amount_wei": "1000000",
                "from_address": "0x4444444444444444444444444444444444444444",
                "to_address": "0x5555555555555555555555555555555555555555",
                "slippage": 0.01,
                "order": "CHEAPEST",
            }
        }
    )
    assert out["result"]["prepared"]["route_id"] == "q-slippage"
    assert len(urls) == 1
    u = urls[0]
    assert "integrator=aurey" in u
    assert "slippage=0.01" in u
    assert "order=CHEAPEST" in u
    _assert_no_banned_values(out)


def test_swap_prepare_graph_maps_native_eth_phrase_to_wrapped_weth():
    """Models paste «native ETH» as toToken — rewrite to that chain's WETH contract for LiFi."""

    settings = AureySettings(lifi_api_secret_path=None)
    urls: list[str] = []

    def capture_url(**kw: object) -> bool:
        urls.append(str(kw.get("url") or ""))
        return kw.get("method") == "GET" and "/v1/quote?" in str(kw.get("url") or "")

    http = ScriptedHttpClient(
        [
            (
                capture_url,
                {
                    "id": "q-native-eth",
                    "transactionRequest": {
                        "to": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                        "data": "0x",
                    },
                },
            )
        ]
    )
    runtime = _runtime(secrets={}, settings=settings, http=http, rpc_map={})
    out = build_swap_prepare_graph(runtime).invoke(
        {
            "input": {
                "from_chain": "base",
                "to_chain": "base",
                "from_asset": "0x0555E30da8f98308eDb960AA94C0Db47230d2b9c",
                "to_asset": "native ETH",
                "from_amount_wei": "5000000000000000000",
                "from_address": "0xc1923710468607b8b7db38a6afbb9b432744390c",
                "to_address": "0xc1923710468607b8b7db38a6afbb9b432744390c",
            }
        }
    )
    assert out.get("error") is None
    assert len(urls) == 1
    assert (
        "toToken=0x4200000000000000000000000000000000000006" in urls[0]
        or "toToken=0x4200000000000000000000000000000000000006".upper() in urls[0].upper()
    )
    _assert_no_banned_values(out)


def test_swap_prepare_graph_skips_allowance_hint_when_on_chain_sufficient():
    """When Alchemy-backed allowance is already >= LiFi fromAmount, omit approve hint."""

    alchemy_path = "vault/alchemy/x"
    secrets = {"vault/lifi": "INJECTED_LIFI_KEY_BBB", alchemy_path: "INJECTED_ALCHEMY_KEY_AAA"}
    settings = AureySettings(
        lifi_api_secret_path="vault/lifi",
        alchemy_api_secret_path=alchemy_path,
    )
    http = ScriptedHttpClient(
        [
            (
                lambda **kw: kw.get("method") == "GET" and "/v1/quote?" in str(kw.get("url") or ""),
                {
                    "id": "swap-route-allow",
                    "estimate": {
                        "approvalAddress": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    },
                    "action": {
                        "fromAmount": "1000000",
                        "fromToken": {
                            "address": "0x1111111111111111111111111111111111111111",
                        },
                    },
                    "transactionRequest": {
                        "to": "0x3333333333333333333333333333333333333333",
                        "data": "0x",
                    },
                },
            )
        ]
    )

    def eth_call(params: list[object]) -> str:
        assert params[0]["to"] == "0x1111111111111111111111111111111111111111"
        return "0x00000000000000000000000000000000000000000000000000000000000f4240"

    runtime = _runtime(
        secrets=secrets,
        settings=settings,
        http=http,
        rpc_map={"eth_call": eth_call},
    )
    out = build_swap_prepare_graph(runtime).invoke(
        {
            "input": {
                "from_chain": "ethereum",
                "to_chain": "base",
                "from_asset": "0x1111111111111111111111111111111111111111",
                "to_asset": "0x2222222222222222222222222222222222222222",
                "from_amount_wei": "1000000",
                "from_address": "0x4444444444444444444444444444444444444444",
                "to_address": "0x5555555555555555555555555555555555555555",
            }
        }
    )
    assert out["result"]["prepared"]["route_id"] == "swap-route-allow"
    assert out["result"].get("allowance") is None
    ctx = out["result"]["allowance_context"]
    assert ctx is not None
    assert ctx["allowance_sufficient"] is True
    assert ctx["current_allowance_raw"] == "1000000"
    _assert_no_banned_values(out)


def test_swap_prepare_graph_keeps_allowance_hint_when_on_chain_low():
    secrets = {"vault/lifi": "INJECTED_LIFI_KEY_BBB", "vault/alchemy/x": "INJECTED_ALCHEMY_KEY_AAA"}
    settings = AureySettings(
        lifi_api_secret_path="vault/lifi",
        alchemy_api_secret_path="vault/alchemy/x",
    )
    http = ScriptedHttpClient(
        [
            (
                lambda **kw: kw.get("method") == "GET" and "/v1/quote?" in str(kw.get("url") or ""),
                {
                    "id": "swap-low",
                    "estimate": {
                        "approvalAddress": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    },
                    "action": {
                        "fromAmount": "1000000",
                        "fromToken": {
                            "address": "0x1111111111111111111111111111111111111111",
                        },
                    },
                    "transactionRequest": {
                        "to": "0x3333333333333333333333333333333333333333",
                        "data": "0x",
                    },
                },
            )
        ]
    )

    def eth_call(_params: list[object]) -> str:
        return "0x0000000000000000000000000000000000000000000000000000000000000064"

    runtime = _runtime(
        secrets=secrets,
        settings=settings,
        http=http,
        rpc_map={"eth_call": eth_call},
    )
    out = build_swap_prepare_graph(runtime).invoke(
        {
            "input": {
                "from_chain": "ethereum",
                "to_chain": "base",
                "from_asset": "0x1111111111111111111111111111111111111111",
                "to_asset": "0x2222222222222222222222222222222222222222",
                "from_amount_wei": "1000000",
                "from_address": "0x4444444444444444444444444444444444444444",
                "to_address": "0x5555555555555555555555555555555555555555",
            }
        }
    )
    al = out["result"]["allowance"]
    assert al is not None
    assert al["amount_raw"] == "1000000"
    ctx = out["result"]["allowance_context"]
    assert ctx is not None
    assert ctx["allowance_sufficient"] is False
    assert ctx["current_allowance_raw"] == "100"
    _assert_no_banned_values(out)


def test_swap_prepare_graph_without_lifi_api_key():
    settings = AureySettings(lifi_api_secret_path=None)

    def match_quote(**kw: object) -> bool:
        headers = kw.get("headers") or {}
        u = str(kw.get("url") or "")
        return (
            kw.get("method") == "GET"
            and "li.quest" in u
            and "/v1/quote?" in u
            and isinstance(headers, dict)
            and "x-lifi-api-key" not in headers
            and "integrator=aurey" in u
        )

    http = ScriptedHttpClient(
        [
            (
                match_quote,
                {
                    "id": "public-quote",
                    "transactionRequest": {
                        "to": "0x3333333333333333333333333333333333333333",
                        "data": "0x",
                    },
                },
            )
        ]
    )
    runtime = _runtime(secrets={}, settings=settings, http=http, rpc_map={})
    out = build_swap_prepare_graph(runtime).invoke(
        {
            "input": {
                "from_chain": "base",
                "to_chain": "base",
                "from_asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
                "to_asset": "0x4200000000000000000000000000000000000006",
                "from_amount_wei": "1000000",
                "from_address": "0x4444444444444444444444444444444444444444",
                "to_address": "0x5555555555555555555555555555555555555555",
            }
        }
    )
    assert out["result"]["prepared"]["route_id"] == "public-quote"
    assert out["result"].get("allowance") is None
    assert out["result"].get("allowance_context") is None


def test_swap_prepare_graph_maps_lifi_http_json_errors():
    settings = AureySettings(lifi_api_secret_path="vault/lifi")
    runtime = _runtime(
        secrets={"vault/lifi": "not-a-real-key"},
        settings=settings,
        http=_LifiUnauthorizedHttp(),
        rpc_map={},
    )
    out = build_swap_prepare_graph(runtime).invoke(
        {
            "input": {
                "from_chain": "base",
                "to_chain": "base",
                "from_asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
                "to_asset": "0x4200000000000000000000000000000000000006",
                "from_amount_wei": "1000000",
                "from_address": "0x4444444444444444444444444444444444444444",
                "to_address": "0x5555555555555555555555555555555555555555",
            }
        }
    )
    err = out["error"]
    assert err["code"] == "http_error"
    assert err["details"]["http_status"] == 401
    assert err["details"]["lifi_message"] == "Invalid API key"
    assert err["details"]["lifi_code"] == 1010


def test_tx_prepare_lifi_swap_graph():
    signing_path = "vault/signing/local"
    secrets = {signing_path: "0x" + "ff" * 32}
    settings = AureySettings(wallet_signing_key_secret_path=signing_path)
    runtime = _runtime(secrets=secrets, settings=settings, http=ScriptedHttpClient(), rpc_map={})
    wallet = "0xc1923710468607b8b7db38a6afbb9b432744390c"
    prepared = {
        "route_id": "4026c5d3-23c3-494d-8c1e-b1c9ba89657c:0",
        "transaction_request": {
            "to": "0x1234567890123456789012345678901234567890",
            "data": "0xcafe",
            "value": "0x0",
            "chainId": 8453,
            "from": wallet,
            "gasLimit": "0x5208",
        },
    }
    out = build_tx_prepare_lifi_graph(runtime).invoke(
        {
            "input": {
                "chain": "base",
                "from_address": wallet,
                "prepared": prepared,
            }
        }
    )
    assert out.get("error") is None
    env = out["result"]["envelope"]
    assert env["kind"] == "lifi_swap"
    assert env["chain_id"] == 8453
    assert env["to"] == "0x1234567890123456789012345678901234567890"
    assert env["data"] == "0xcafe"
    assert env["value_hex"] == "0x0"
    assert env["gas_limit_hex"] == "0x5208"
    assert env["signing_key_secret_path"] == signing_path
    assert env["signing_mode"] == "vault_key"
    assert env.get("lifi_sell_token") is None
    _assert_no_banned_values(out)


def test_tx_prepare_lifi_swap_graph_attaches_allowance_context_metadata():
    signing_path = "vault/signing/local"
    secrets = {signing_path: "0x" + "ff" * 32}
    settings = AureySettings(wallet_signing_key_secret_path=signing_path)
    runtime = _runtime(secrets=secrets, settings=settings, http=ScriptedHttpClient(), rpc_map={})
    wallet = "0xc1923710468607b8b7db38a6afbb9b432744390c"
    prepared = {
        "route_id": "4026c5d3-23c3-494d-8c1e-b1c9ba89657c:0",
        "transaction_request": {
            "to": "0x1234567890123456789012345678901234567890",
            "data": "0xcafe",
            "value": "0x0",
            "chainId": 8453,
            "from": wallet,
            "gasLimit": "0x5208",
        },
    }
    ctx = {
        "token_address": "0x1111111111111111111111111111111111111111",
        "spender_address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "amount_raw": "1000000",
        "current_allowance_raw": "2000000",
        "allowance_sufficient": True,
    }
    out = build_tx_prepare_lifi_graph(runtime).invoke(
        {
            "input": {
                "chain": "base",
                "from_address": wallet,
                "prepared": prepared,
                "allowance_context": ctx,
            }
        }
    )
    assert out.get("error") is None
    env = out["result"]["envelope"]
    assert env["lifi_sell_token"] == "0x1111111111111111111111111111111111111111"
    assert env["lifi_approval_spender"] == "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert env["lifi_sell_amount_raw"] == "1000000"
    _assert_no_banned_values(out)


def test_tx_prepare_lifi_swap_graph_flat_route_and_transaction_request():
    signing_path = "vault/signing/local"
    secrets = {signing_path: "0x" + "ff" * 32}
    settings = AureySettings(wallet_signing_key_secret_path=signing_path)
    runtime = _runtime(secrets=secrets, settings=settings, http=ScriptedHttpClient(), rpc_map={})
    wallet = "0xc1923710468607b8b7db38a6afbb9b432744390c"
    out = build_tx_prepare_lifi_graph(runtime).invoke(
        {
            "input": {
                "chain": "base",
                "from_address": wallet,
                "route_id": "f3288cb0-08fb-4b91-8b39-98f41ffad017:0",
                "transaction_request": {
                    "to": "0x1234567890123456789012345678901234567890",
                    "data": "0x",
                    "value": "0x0",
                    "chainId": 8453,
                },
            }
        }
    )
    assert out.get("error") is None
    assert out["result"]["envelope"]["kind"] == "lifi_swap"


def test_tx_prepare_lifi_swap_then_execute_roundtrip():
    signing_path = "vault/signing/local"
    secrets = {signing_path: "0x" + "ff" * 32}
    settings = AureySettings(wallet_signing_key_secret_path=signing_path)
    runtime = _runtime(secrets=secrets, settings=settings, http=ScriptedHttpClient(), rpc_map={})
    wallet = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    prepared = {
        "route_id": "lane-1",
        "transaction_request": {
            "to": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "data": "0xdeadbeef",
            "value": 0,
            "chainId": 8453,
        },
    }
    prep = build_tx_prepare_lifi_graph(runtime).invoke(
        {"input": {"chain": "base", "from_address": wallet, "prepared": prepared}}
    )
    execute = build_tx_execute_graph(runtime).invoke(
        {"input": {"envelope": prep["result"]["envelope"]}}
    )
    assert execute.get("error") is None
    assert execute["result"]["tx_hash"].startswith("0x")
    assert execute["result"]["receipt"]["status"] == 1
    _assert_no_banned_values(execute)


def test_tx_prepare_vault_key_requires_wallet_signing_path():
    settings = AureySettings(evm_signing_mode="vault_key", wallet_signing_key_secret_path=None)
    runtime = _runtime(secrets={}, settings=settings, http=ScriptedHttpClient(), rpc_map={})
    out = build_tx_prepare_graph(runtime).invoke(
        {
            "input": {
                "kind": "native_transfer",
                "chain": "base",
                "from_address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "to_address": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "value_wei": 1,
            }
        }
    )
    assert out["error"]["code"] == "secret_not_configured"


def test_tx_prepare_vault_key_rejects_whitespace_only_signing_path():
    settings = AureySettings(
        evm_signing_mode="vault_key",
        wallet_signing_key_secret_path="   ",
    )
    runtime = _runtime(secrets={}, settings=settings, http=ScriptedHttpClient(), rpc_map={})
    out = build_tx_prepare_graph(runtime).invoke(
        {
            "input": {
                "kind": "native_transfer",
                "chain": "base",
                "from_address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "to_address": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "value_wei": 1,
            }
        }
    )
    assert out["error"]["code"] == "secret_not_configured"


def test_tx_prepare_oneclaw_intents_requires_agent_id():
    settings = AureySettings(
        evm_signing_mode="oneclaw_intents",
        oneclaw_agent_id=None,
        wallet_signing_key_secret_path=None,
    )
    runtime = _runtime(secrets={}, settings=settings, http=ScriptedHttpClient(), rpc_map={})
    out = build_tx_prepare_graph(runtime).invoke(
        {
            "input": {
                "kind": "native_transfer",
                "chain": "base",
                "from_address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "to_address": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "value_wei": 1,
            }
        }
    )
    assert out["error"]["code"] == "secret_not_configured"
    assert "oneclaw_agent_id" in out["error"]["message"]


def test_tx_prepare_hosted_requires_signing_context():
    settings = AureySettings(
        evm_signing_mode="oneclaw_intents",
        hosted_platform_enabled=True,
        oneclaw_agent_id="must-not-use",
        wallet_signing_key_secret_path=None,
    )
    runtime = _runtime(secrets={}, settings=settings, http=ScriptedHttpClient(), rpc_map={})
    out = build_tx_prepare_graph(runtime).invoke(
        {
            "input": {
                "kind": "native_transfer",
                "chain": "base",
                "from_address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "to_address": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "value_wei": 1,
            }
        }
    )
    assert out.get("result") is None
    assert out["error"]["code"] == "hosted_signing_context_required"


def test_tx_prepare_oneclaw_intents_native_envelope():
    settings = AureySettings(
        evm_signing_mode="oneclaw_intents",
        oneclaw_agent_id="agent-123",
        wallet_signing_key_secret_path="wallets/hot-wallet",
    )
    runtime = _runtime(secrets={}, settings=settings, http=ScriptedHttpClient(), rpc_map={})
    out = build_tx_prepare_graph(runtime).invoke(
        {
            "input": {
                "kind": "native_transfer",
                "chain": "base",
                "from_address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "to_address": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "value_wei": 3,
            }
        }
    )
    assert out.get("error") is None
    env = out["result"]["envelope"]
    assert env["signing_mode"] == "oneclaw_intents"
    assert env.get("signing_key_secret_path") == "wallets/hot-wallet"


def test_tx_prepare_lifi_oneclaw_intents_envelope():
    settings = AureySettings(
        evm_signing_mode="oneclaw_intents",
        oneclaw_agent_id="agent-xyz",
        wallet_signing_key_secret_path=None,
    )
    runtime = _runtime(secrets={}, settings=settings, http=ScriptedHttpClient(), rpc_map={})
    wallet = "0xc1923710468607b8b7db38a6afbb9b432744390c"
    prepared = {
        "route_id": "4026c5d3-23c3-494d-8c1e-b1c9ba89657c:0",
        "transaction_request": {
            "to": "0x1234567890123456789012345678901234567890",
            "data": "0xcafe",
            "value": "0x0",
            "chainId": 8453,
            "from": wallet,
            "gasLimit": "0x5208",
        },
    }
    out = build_tx_prepare_lifi_graph(runtime).invoke(
        {
            "input": {
                "chain": "base",
                "from_address": wallet,
                "prepared": prepared,
            }
        }
    )
    assert out.get("error") is None
    env = out["result"]["envelope"]
    assert env["kind"] == "lifi_swap"
    assert env["signing_mode"] == "oneclaw_intents"
    assert env.get("signing_key_secret_path") is None


def test_tx_prepare_and_execute_native_roundtrip():
    signing_path = "vault/signing/local"
    secrets = {
        signing_path: "0x" + "ff" * 32,
    }
    settings = AureySettings(
        wallet_signing_key_secret_path=signing_path,
    )
    runtime = _runtime(secrets=secrets, settings=settings, http=ScriptedHttpClient(), rpc_map={})

    prepare = build_tx_prepare_graph(runtime).invoke(
        {
            "input": {
                "kind": "native_transfer",
                "chain": "ethereum",
                "from_address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "to_address": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "value_wei": 5,
            }
        }
    )
    envelope = prepare["result"]["envelope"]
    assert envelope["kind"] == "native_transfer"
    assert envelope["data"] == "0x"
    assert envelope["signing_mode"] == "vault_key"
    assert envelope["signing_key_secret_path"] == signing_path
    _assert_no_banned_values(prepare)

    execute = build_tx_execute_graph(runtime).invoke({"input": {"envelope": envelope}})
    assert execute.get("error") is None
    assert execute["result"]["tx_hash"].startswith("0x")
    assert execute["result"]["receipt"]["status"] == 1
    _assert_no_banned_values(execute)


def test_tx_prepare_native_rejects_zero_recipient():
    signing_path = "vault/signing/local"
    settings = AureySettings(wallet_signing_key_secret_path=signing_path)
    runtime = _runtime(
        secrets={signing_path: "0x" + "ff" * 32},
        settings=settings,
        http=ScriptedHttpClient(),
        rpc_map={},
    )
    out = build_tx_prepare_graph(runtime).invoke(
        {
            "input": {
                "kind": "native_transfer",
                "chain": "base",
                "from_address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "to_address": "0x0000000000000000000000000000000000000000",
                "value_wei": 100,
            }
        }
    )
    assert out.get("error") is not None
    assert out["error"]["message"] == "Native transfer recipient must not be the zero address."


def test_tx_prepare_erc20_paths():
    signing_path = "vault/signing/local"
    secrets = {signing_path: "0x" + "ff" * 32}
    settings = AureySettings(wallet_signing_key_secret_path=signing_path)
    runtime = _runtime(secrets=secrets, settings=settings, http=ScriptedHttpClient(), rpc_map={})

    transfer = build_tx_prepare_graph(runtime).invoke(
        {
            "input": {
                "kind": "erc20_transfer",
                "chain": "base",
                "from_address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "token_address": "0xcccccccccccccccccccccccccccccccccccccccc",
                "to_address": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "amount_wei": 42,
            }
        }
    )
    assert transfer["result"]["envelope"]["kind"] == "erc20_transfer"
    assert transfer["result"]["envelope"]["data"].startswith("0xa9059cbb")

    approval = build_tx_prepare_graph(runtime).invoke(
        {
            "input": {
                "kind": "erc20_approval",
                "chain": "base",
                "from_address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "token_address": "0xcccccccccccccccccccccccccccccccccccccccc",
                "spender_address": "0xdddddddddddddddddddddddddddddddddddddddd",
                "amount_wei": 99,
            }
        }
    )
    assert approval["result"]["envelope"]["kind"] == "erc20_approval"
    assert approval["result"]["envelope"]["data"].startswith("0x095ea7b3")
    _assert_no_banned_values(approval)


def test_tx_execute_simulation_failure():
    signing_path = "vault/signing/local"
    secrets = {signing_path: "0x" + "ff" * 32}
    settings = AureySettings(wallet_signing_key_secret_path=signing_path)
    http = ScriptedHttpClient()
    runtime = AureyRuntime(
        settings=settings,
        secret_store=FakeSecretStore(secrets),
        evm_rpc_factory=rpc_factory_from_mapping({}),
        http=http,
        tx_pipeline=DeterministicTxPipeline(fail_stage="simulate"),
        lifi_base_url="https://li.quest",
    )
    env = {
        "kind": "native_transfer",
        "chain_id": 1,
        "from_address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "to": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "data": "0x",
        "value_hex": "0x1",
        "gas_limit_hex": None,
        "nonce": None,
        "signing_key_secret_path": signing_path,
    }
    out = build_tx_execute_graph(runtime).invoke({"input": {"envelope": env}})
    assert out.get("result") is None
    assert out["error"]["code"] == "simulation_failed"
    assert "simulation_failed" in out["error"]["message"]


def test_tx_execute_secret_unavailable_surfaces_store_detail():
    signing_path = "vault/signing/local"
    settings = AureySettings(
        evm_signing_mode="vault_key",
        wallet_signing_key_secret_path=signing_path,
    )
    runtime = _runtime(
        secrets={},
        settings=settings,
        http=ScriptedHttpClient(),
        rpc_map={},
        secret_store=_UnavailableSigningKeyStore(),
    )
    envelope = {
        "kind": "native_transfer",
        "chain_id": 1,
        "from_address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "to": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "data": "0x",
        "value_hex": "0x1",
        "gas_limit_hex": None,
        "nonce": None,
        "signing_mode": "vault_key",
        "signing_key_secret_path": signing_path,
    }
    out = build_tx_execute_graph(runtime).invoke({"input": {"envelope": envelope}})
    assert out.get("result") is None
    assert out["error"]["code"] == "secret_unavailable"
    details = out["error"]["details"]
    assert details["secret_kind"] == "signing_key"
    assert details["path"] == "/v1/auth/agent-token"
    assert "401" in details["detail"]


def test_tx_prepare_and_execute_oneclaw_intents_roundtrip():
    settings = AureySettings(
        evm_signing_mode="oneclaw_intents",
        oneclaw_agent_id="agent-oc-1",
        wallet_signing_key_secret_path=None,
    )
    signer = FakeOneClawClient()
    runtime = _runtime(
        secrets={},
        settings=settings,
        http=ScriptedHttpClient(),
        rpc_map={},
        oneclaw_evm_signer=signer,
    )
    prepare = build_tx_prepare_graph(runtime).invoke(
        {
            "input": {
                "kind": "native_transfer",
                "chain": "base",
                "from_address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "to_address": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "value_wei": 9,
            }
        }
    )
    assert prepare.get("error") is None
    envelope = prepare["result"]["envelope"]
    assert envelope["signing_mode"] == "oneclaw_intents"

    execute = build_tx_execute_graph(runtime).invoke({"input": {"envelope": envelope}})
    assert execute.get("error") is None
    assert execute["result"]["tx_hash"].startswith("0x")
    _assert_no_banned_values(execute)


def test_tx_execute_oneclaw_requires_agent_id():
    settings = AureySettings(
        evm_signing_mode="oneclaw_intents",
        oneclaw_agent_id=None,
        wallet_signing_key_secret_path=None,
    )
    signer = FakeOneClawClient()
    runtime = _runtime(
        secrets={},
        settings=settings,
        http=ScriptedHttpClient(),
        rpc_map={},
        oneclaw_evm_signer=signer,
    )
    envelope = {
        "kind": "native_transfer",
        "chain_id": 8453,
        "from_address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "to": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "data": "0x",
        "value_hex": "0x1",
        "signing_mode": "oneclaw_intents",
        "signing_key_secret_path": None,
    }
    out = build_tx_execute_graph(runtime).invoke({"input": {"envelope": envelope}})
    assert out.get("result") is None
    assert out["error"]["code"] == "secret_not_configured"
    assert "oneclaw_agent_id" in out["error"]["message"]


def test_tx_execute_oneclaw_requires_runtime_signer():
    settings = AureySettings(
        evm_signing_mode="oneclaw_intents",
        oneclaw_agent_id="agent-x",
        wallet_signing_key_secret_path=None,
    )
    runtime = _runtime(
        secrets={},
        settings=settings,
        http=ScriptedHttpClient(),
        rpc_map={},
        oneclaw_evm_signer=None,
    )
    envelope = {
        "kind": "native_transfer",
        "chain_id": 8453,
        "from_address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "to": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "data": "0x",
        "value_hex": "0x2",
        "signing_mode": "oneclaw_intents",
        "signing_key_secret_path": None,
    }
    out = build_tx_execute_graph(runtime).invoke({"input": {"envelope": envelope}})
    assert out.get("result") is None
    assert out["error"]["code"] == "secret_not_configured"
    assert "signer" in out["error"]["message"].lower()


def test_tx_execute_rejects_oneclaw_envelope_when_operator_uses_vault_key():
    signing_path = "vault/signing/local"
    secrets = {signing_path: "0x" + "ff" * 32}
    settings = AureySettings(
        evm_signing_mode="vault_key",
        wallet_signing_key_secret_path=signing_path,
    )
    runtime = _runtime(
        secrets=secrets,
        settings=settings,
        http=ScriptedHttpClient(),
        rpc_map={},
    )
    envelope = {
        "kind": "native_transfer",
        "chain_id": 8453,
        "from_address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "to": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "data": "0x",
        "value_hex": "0x1",
        "signing_mode": "oneclaw_intents",
        "signing_key_secret_path": None,
    }
    out = build_tx_execute_graph(runtime).invoke({"input": {"envelope": envelope}})
    assert out["error"]["code"] == "policy_rejected"
    assert "signing_mode" in out["error"]["message"]


def test_tx_execute_rejects_vault_envelope_when_operator_uses_oneclaw_intents():
    signing_path = "vault/signing/local"
    secrets = {signing_path: "0x" + "ff" * 32}
    settings = AureySettings(
        evm_signing_mode="oneclaw_intents",
        oneclaw_agent_id="ag-99",
        wallet_signing_key_secret_path=signing_path,
    )
    signer = FakeOneClawClient()
    runtime = _runtime(
        secrets=secrets,
        settings=settings,
        http=ScriptedHttpClient(),
        rpc_map={},
        oneclaw_evm_signer=signer,
    )
    envelope = {
        "kind": "native_transfer",
        "chain_id": 1,
        "from_address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "to": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "data": "0x",
        "value_hex": "0x1",
        "gas_limit_hex": None,
        "nonce": None,
        "signing_key_secret_path": signing_path,
        "signing_mode": "vault_key",
    }
    out = build_tx_execute_graph(runtime).invoke({"input": {"envelope": envelope}})
    assert out["error"]["code"] == "policy_rejected"
    assert "signing_mode" in out["error"]["message"]


def test_earn_list_chains_graph_success():
    secrets: dict[str, str] = {}
    settings = AureySettings()
    chains_fixture = [
        {"name": "Ethereum", "chainId": 1, "networkCaip": "eip155:1"},
        {"name": "Base", "chainId": 8453, "networkCaip": "eip155:8453"},
    ]

    def match_chains(**kw: object) -> bool:
        return kw.get("method") == "GET" and str(kw.get("url") or "").endswith("/v1/chains")

    http = ScriptedHttpClient([(match_chains, chains_fixture)])
    runtime = _runtime(secrets=secrets, settings=settings, http=http, rpc_map={})
    out = build_earn_graph(runtime).invoke({"input": {"operation": "list_chains"}})
    assert out.get("error") is None
    names = [c.get("name") for c in out["result"]["chains"]]
    assert names == ["Ethereum", "Base"]
    assert out["result"]["chains"][0].get("chain") == "ethereum"
    assert out["result"]["chains"][1].get("chain") == "base"
    _assert_no_banned_values(out)


def test_earn_list_protocols_graph_success():
    secrets: dict[str, str] = {}
    settings = AureySettings()
    protos = [
        {"id": "aave-v3", "name": "Aave", "logoUri": "https://x.test/a.png", "url": "https://aave.com"},
    ]

    def match_proto(**kw: object) -> bool:
        return kw.get("method") == "GET" and str(kw.get("url") or "").endswith("/v1/protocols")

    http = ScriptedHttpClient([(match_proto, protos)])
    runtime = _runtime(secrets=secrets, settings=settings, http=http, rpc_map={})
    out = build_earn_graph(runtime).invoke({"input": {"operation": "list_protocols"}})
    assert out.get("error") is None
    assert out["result"]["protocols"][0]["id"] == "aave-v3"
    assert out["result"]["protocols"][0]["name"] == "Aave"
    assert out["result"]["protocols"][0].get("logo_uri") == "https://x.test/a.png"
    _assert_no_banned_values(out)


def test_earn_list_vaults_graph_builds_query_params_and_api_key_header():
    lifi_path = "vault/lifi/earn"
    secrets = {lifi_path: "INJECTED_LIFI_KEY_BBB"}
    settings = AureySettings(lifi_api_secret_path=lifi_path)
    asset_hex = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"

    def match_vaults(**kw: object) -> bool:
        if kw.get("method") != "GET":
            return False
        u = str(kw.get("url") or "")
        return "earn.li.fi/v1/vaults" in u

    list_body = {
        "data": [
            {
                "address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "chainId": 8453,
                "name": "Test vault",
                "protocol": {"id": "x"},
                "isTransactional": True,
            }
        ],
        "total": 1,
        "normalizedAt": "2026-01-01T00:00:00Z",
        "nextCursor": "c2",
    }
    http = ScriptedHttpClient([(match_vaults, list_body)])
    runtime = _runtime(secrets=secrets, settings=settings, http=http, rpc_map={})
    out = build_earn_graph(runtime).invoke(
        {
            "input": {
                "operation": "list_vaults",
                "chain": "base",
                "asset": asset_hex,
                "protocol": "aave",
                "min_tvl_usd": 1000.0,
                "is_transactional": True,
                "is_redeemable": False,
                "is_composer_supported": True,
                "sort_by": "apy",
                "limit": 7,
                "cursor": "next-page",
            }
        }
    )
    assert out.get("error") is None
    call = http.calls[-1]
    assert call["headers"].get("x-lifi-api-key") == "INJECTED_LIFI_KEY_BBB"
    q = parse_qs(urlparse(call["url"]).query)
    assert q["chainId"] == ["8453"]
    assert q["asset"] == ["0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"]
    assert q["protocol"] == ["aave"]
    assert q["minTvlUsd"] == ["1000.0"]
    assert q["isTransactional"] == ["true"]
    assert q["isRedeemable"] == ["false"]
    assert q["isComposerSupported"] == ["true"]
    assert q["sortBy"] == ["apy"]
    assert q["limit"] == ["7"]
    assert q["cursor"] == ["next-page"]
    assert out["result"]["vaults"][0]["address"] == "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert out["result"]["next_cursor"] == "c2"
    blob = json.dumps(out, default=str, sort_keys=True)
    assert "INJECTED_LIFI_KEY_BBB" not in blob
    _assert_no_banned_values(out)


def test_earn_get_vault_graph_normalizes_address_and_null_apy_caps():
    secrets: dict[str, str] = {}
    settings = AureySettings()
    vault_addr_mixed = "0xAbCdEf1234567890AbCdEf1234567890aBcDef12"
    vault_addr_cs = to_checksum_evm_address(vault_addr_mixed)
    matched_urls: list[str] = []

    def match_detail(**kw: object) -> bool:
        u = str(kw.get("url") or "")
        if not (kw.get("method") == "GET" and "earn.li.fi/v1/vaults/8453/" in u):
            return False
        matched_urls.append(u)
        return u.rstrip("/").endswith(vault_addr_cs)

    raw_vault = {
        "address": vault_addr_mixed,
        "chainId": 8453,
        "name": "Nullable fields",
        "protocol": {"id": "p"},
        "analytics": None,
        "caps": None,
        "isTransactional": True,
        "isComposerSupported": True,
    }
    http = ScriptedHttpClient([(match_detail, raw_vault)])
    runtime = _runtime(secrets=secrets, settings=settings, http=http, rpc_map={})
    out = build_earn_graph(runtime).invoke(
        {
            "input": {
                "operation": "get_vault",
                "chain": "base",
                "vault_address": f"  {vault_addr_mixed} ",
            }
        }
    )
    assert out.get("error") is None
    assert matched_urls
    v = out["result"]["vault"]
    assert v.get("address") == vault_addr_mixed
    assert v.get("caps") is None
    assert "apy" not in (v.get("analytics") or {})
    _assert_no_banned_values(out)


def test_earn_portfolio_positions_graph_nullable_address_protocol_balance_usd():
    secrets: dict[str, str] = {}
    settings = AureySettings()
    wallet = "0x1111111111111111111111111111111111111111"

    def match_portfolio(**kw: object) -> bool:
        u = str(kw.get("url") or "")
        return (
            kw.get("method") == "GET"
            and f"/v1/portfolio/{wallet}/positions" in u
            and "earn.li.fi" in u
        )

    body = {
        "positions": [
            {
                "chainId": 8453,
                "address": None,
                "protocolName": None,
                "balanceUsd": None,
                "asset": {"symbol": "??"},
            }
        ]
    }
    http = ScriptedHttpClient([(match_portfolio, body)])
    runtime = _runtime(secrets=secrets, settings=settings, http=http, rpc_map={})
    out = build_earn_graph(runtime).invoke(
        {"input": {"operation": "portfolio_positions", "wallet_address": wallet}}
    )
    assert out.get("error") is None
    row = out["result"]["positions"][0]
    assert row["address"] is None
    assert row["protocol_name"] is None
    assert row["balance_usd"] is None
    assert row["asset"].get("symbol") == "??"
    _assert_no_banned_values(out)


def test_earn_graph_http_errors_map_json_and_avoid_secret_echo():
    lifi_path = "vault/lifi/earn-err"
    secrets = {lifi_path: "INJECTED_LIFI_KEY_BBB"}
    settings = AureySettings(lifi_api_secret_path=lifi_path)

    cases: list[tuple[int, dict[str, Any] | None, str]] = [
        (400, {"message": "Invalid filter", "statusCode": 400}, "earn_status_code"),
        (404, {"message": "Not found", "code": "NOPE"}, "earn_message"),
        (
            429,
            {"message": "Too many requests; retry later", "statusCode": 429},
            "earn_message",
        ),
    ]
    for status_code, payload, detail_key in cases:
        runtime = _runtime(
            secrets=secrets,
            settings=settings,
            http=_EarnHttpJsonError(
                status_code=status_code,
                body_text=json.dumps(payload or {}),
                payload=payload,
            ),
            rpc_map={},
        )
        out = build_earn_graph(runtime).invoke({"input": {"operation": "list_chains"}})
        assert out.get("result") is None
        err = out["error"]
        assert err["code"] == "http_error"
        assert err["details"]["http_status"] == status_code
        assert detail_key in err["details"]
        blob = json.dumps(out, default=str, sort_keys=True)
        assert "INJECTED_LIFI_KEY_BBB" not in blob
        _assert_no_banned_values(out)


def test_lifi_status_graph_query_params_and_status_variants():
    secrets: dict[str, str] = {}
    settings = AureySettings()

    def match_status(**kw: object) -> bool:
        return kw.get("method") == "GET" and "li.quest/v1/status" in str(kw.get("url") or "")

    def run_case(payload: dict[str, Any]) -> dict[str, Any]:
        http = ScriptedHttpClient([(match_status, payload)])
        runtime = _runtime(secrets=secrets, settings=settings, http=http, rpc_map={})
        return build_lifi_status_graph(runtime).invoke(
            {
                "input": {
                    "tx_hash": "0xabc",
                    "from_chain": "ethereum",
                    "to_chain": "base",
                    "bridge": "across",
                }
            }
        )

    for raw, expected_status in (
        ({"status": "DONE", "substatus": "COMPLETED"}, "DONE"),
        ({"status": "PENDING", "substatus": "WAIT_SOURCE_CONFIRMATIONS"}, "PENDING"),
        ({"status": "FAILED", "substatus": "NOT_FOUND"}, "FAILED"),
    ):
        out = run_case(raw)
        assert out.get("error") is None
        assert out["result"]["status"] == expected_status
        _assert_no_banned_values(out)

    captured: dict[str, str] = {}

    def capture_url(**kw: object) -> bool:
        if kw.get("method") == "GET" and "li.quest/v1/status" in str(kw.get("url") or ""):
            captured["url"] = str(kw.get("url") or "")
            return True
        return False

    http = ScriptedHttpClient(
        [
            (
                capture_url,
                {"status": "PENDING", "substatus": "WAIT_DESTINATION_TRANSACTION"},
            )
        ]
    )
    runtime = _runtime(secrets=secrets, settings=settings, http=http, rpc_map={})
    out = build_lifi_status_graph(runtime).invoke(
        {
            "input": {
                "tx_hash": "0xdeadbeef",
                "from_chain": "ethereum",
                "to_chain": "base",
                "bridge": "across",
            }
        }
    )
    assert out.get("error") is None
    q = parse_qs(urlparse(captured["url"]).query)
    assert q["txHash"] == ["0xdeadbeef"]
    assert q["fromChain"] == ["1"]
    assert q["toChain"] == ["8453"]
    assert q["bridge"] == ["across"]
    _assert_no_banned_values(out)


def test_lifi_status_graph_http_error_maps_lifi_message():
    runtime = _runtime(
        secrets={},
        settings=AureySettings(),
        http=_EarnHttpJsonError(
            status_code=404,
            body_text='{"message":"Transfer not found","errorCode":"NOT_FOUND"}',
            payload={"message": "Transfer not found", "errorCode": "NOT_FOUND"},
        ),
        rpc_map={},
    )
    out = build_lifi_status_graph(runtime).invoke({"input": {"tx_hash": "0x1"}})
    assert out.get("result") is None
    assert out["error"]["code"] == "http_error"
    assert out["error"]["details"]["http_status"] == 404
    assert out["error"]["details"]["lifi_message"] == "Transfer not found"
    assert out["error"]["details"]["lifi_code"] == "NOT_FOUND"
    _assert_no_banned_values(out)
