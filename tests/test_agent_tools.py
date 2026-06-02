"""LangChain tool schemas, subgraph invokes, and deep-agent factory wiring."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from aurey.custody import FakeSecretStore
from aurey.graphs import DeterministicTxPipeline, TxExecuteInput
from aurey.graphs.evm_codec import normalize_evm_address, to_checksum_evm_address
from aurey.reasoning import create_aurey_deep_agent, make_memory_checkpointer, thread_config
from aurey.reasoning import deep_agent as deep_agent_mod
from aurey.runtime import AureyRuntime
from aurey.settings import AureySettings
from aurey.tools import build_aurey_subgraph_tools, reset_user_input_context
from aurey.tools.user_input import UserQuestion, get_pending_user_questions
from tests.fakes.evm_rpc import rpc_factory_from_mapping
from tests.fakes.http_client import ScriptedHttpClient


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


class _DummyChat(BaseChatModel):
    model_name: str = "stub"

    @property
    def _llm_type(self) -> str:
        return "dummy"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])

    def bind_tools(self, tools, **kwargs):
        return self


def _tool_by_name(tools, name: str):
    for t in tools:
        if t.name == name:
            return t
    raise AssertionError(f"missing tool {name}")


@pytest.fixture(autouse=True)
def _clear_user_input_ctx():
    reset_user_input_context()
    yield
    reset_user_input_context()


def test_tool_schemas_include_expected_names_and_descriptions():
    alchemy_path = "vault/alchemy"
    signing_path = "vault/signing/local"
    secrets = {
        alchemy_path: "INJECTED_ALCHEMY_KEY_AAA",
        signing_path: "0x" + "ff" * 32,
    }
    settings = AureySettings(
        alchemy_api_secret_path=alchemy_path,
        wallet_signing_key_secret_path=signing_path,
    )
    runtime = AureyRuntime(
        settings=settings,
        secret_store=FakeSecretStore(secrets),
        evm_rpc_factory=rpc_factory_from_mapping({}),
        http=ScriptedHttpClient(),
        tx_pipeline=DeterministicTxPipeline(),
        lifi_base_url="https://li.quest",
    )
    tools = build_aurey_subgraph_tools(runtime)
    names = {t.name for t in tools}
    expected = {
        "evm_get_native_balance",
        "evm_get_erc20_decimals",
        "evm_resolve_ens",
        "resolve_known_address",
        "evm_get_erc20_balance",
        "alchemy_get_token_prices",
        "compute_token_amount_from_usd",
        "alchemy_get_portfolio_tokens",
        "alchemy_get_transfer_history",
        "earn_list_chains",
        "earn_list_protocols",
        "earn_list_vaults",
        "earn_get_vault",
        "earn_portfolio_positions",
        "earn_prepare_deposit",
        "lifi_get_status",
        "swap_prepare",
        "tx_prepare_lifi_swap",
        "tx_prepare_native_transfer",
        "tx_prepare_erc20_transfer",
        "tx_prepare_erc20_approval",
        "tx_execute",
        "resolve_hosted_recipient_by_handle",
        "get_hosted_wallet_addresses",
        "request_user_input",
    }
    assert expected <= names
    nb = _tool_by_name(tools, "evm_get_native_balance")
    assert "balance" in (nb.description or "").lower()
    rk = _tool_by_name(tools, "resolve_known_address")
    assert "ticker" in (rk.description or "").lower() or "address" in (rk.description or "").lower()


def test_evm_get_native_balance_tool_fake_runtime():
    alchemy_path = "vault/alchemy"
    signing_path = "vault/signing/local"
    secrets = {
        alchemy_path: "INJECTED_ALCHEMY_KEY_AAA",
        signing_path: "0x" + "ff" * 32,
    }
    settings = AureySettings(
        alchemy_api_secret_path=alchemy_path,
        wallet_signing_key_secret_path=signing_path,
    )
    runtime = AureyRuntime(
        settings=settings,
        secret_store=FakeSecretStore(secrets),
        evm_rpc_factory=rpc_factory_from_mapping({"eth_getBalance": "0x10"}),
        http=ScriptedHttpClient(),
        tx_pipeline=DeterministicTxPipeline(),
        lifi_base_url="https://li.quest",
    )
    tool = _tool_by_name(build_aurey_subgraph_tools(runtime), "evm_get_native_balance")
    out = tool.invoke(
        {
            "chain": "ethereum",
            "wallet_address": "0x0000000000000000000000000000000000000001",
        }
    )
    assert out["ok"] is True
    assert out["result"]["balance_wei_hex"] == "0x10"
    _assert_no_banned_values(out)


def test_resolve_known_address_tool_fake_runtime():
    signing_path = "vault/signing/local"
    secrets = {signing_path: "0x" + "ff" * 32}
    settings = AureySettings(
        wallet_signing_key_secret_path=signing_path,
    )
    runtime = AureyRuntime(
        settings=settings,
        secret_store=FakeSecretStore(secrets),
        evm_rpc_factory=rpc_factory_from_mapping({}),
        http=ScriptedHttpClient(),
        tx_pipeline=DeterministicTxPipeline(),
        lifi_base_url="https://li.quest",
    )
    tool = _tool_by_name(build_aurey_subgraph_tools(runtime), "resolve_known_address")
    out = tool.invoke({"chain": "ethereum", "known_ticker": "usdc"})
    assert out["ok"] is True
    assert out["result"]["resolved_address"] == "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
    assert out["result"]["symbol"] == "USDC"
    assert out["result"]["name"] == "USD Coin"
    _assert_no_banned_values(out)


def test_evm_get_erc20_balance_tool():
    alchemy_path = "vault/alchemy"
    secrets = {alchemy_path: "INJECTED_ALCHEMY_KEY_AAA"}
    wallet = "0x00000000000000000000000000000000000000aa"
    token = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    one_usdc_raw = 1_000_000

    def eth_call(params: list) -> str:
        data = params[0]["data"]
        if data == "0x313ce567":
            return "0x0000000000000000000000000000000000000000000000000000000000000006"
        if data.startswith("0x70a08231"):
            return "0x" + f"{one_usdc_raw:064x}"
        raise AssertionError(f"unexpected calldata {data}")

    settings = AureySettings(alchemy_api_secret_path=alchemy_path)
    runtime = AureyRuntime(
        settings=settings,
        secret_store=FakeSecretStore(secrets),
        evm_rpc_factory=rpc_factory_from_mapping({"eth_call": eth_call}),
        http=ScriptedHttpClient(),
        tx_pipeline=DeterministicTxPipeline(),
        lifi_base_url="https://li.quest",
    )
    tool = _tool_by_name(build_aurey_subgraph_tools(runtime), "evm_get_erc20_balance")
    out = tool.invoke(
        {
            "chain": "base",
            "wallet_address": wallet,
            "token_address": token,
        }
    )
    assert out["ok"] is True
    assert out["result"]["decimals"] == 6
    assert out["result"]["balance_raw"] == str(one_usdc_raw)
    assert out["result"]["balance_human"] == "1"
    assert out["result"]["token_address"] == token.lower()  # normalized, not EIP-55
    _assert_no_banned_values(out)


def test_evm_get_erc20_decimals_tool():
    alchemy_path = "vault/alchemy"
    secrets = {alchemy_path: "INJECTED_ALCHEMY_KEY_AAA"}
    usdc_base = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"

    def eth_call(params: list) -> str:
        assert params[0]["data"] == "0x313ce567"
        return "0x0000000000000000000000000000000000000000000000000000000000000006"

    runtime = AureyRuntime(
        settings=AureySettings(
            alchemy_api_secret_path=alchemy_path,
            wallet_signing_key_secret_path="vault/signing",
        ),
        secret_store=FakeSecretStore(secrets),
        evm_rpc_factory=rpc_factory_from_mapping({"eth_call": eth_call}),
        http=ScriptedHttpClient(),
        tx_pipeline=DeterministicTxPipeline(),
        lifi_base_url="https://li.quest",
    )
    tool = _tool_by_name(build_aurey_subgraph_tools(runtime), "evm_get_erc20_decimals")
    out = tool.invoke({"chain": "base", "token_address": usdc_base})
    assert out["ok"] is True
    assert out["result"]["decimals"] == 6
    assert out["result"]["token_address"] == usdc_base.lower()
    _assert_no_banned_values(out)


def test_alchemy_get_token_prices_tool_fake_runtime():
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
    runtime = AureyRuntime(
        settings=settings,
        secret_store=FakeSecretStore(secrets),
        evm_rpc_factory=rpc_factory_from_mapping({}),
        http=http,
        tx_pipeline=DeterministicTxPipeline(),
        lifi_base_url="https://li.quest",
    )
    tool = _tool_by_name(build_aurey_subgraph_tools(runtime), "alchemy_get_token_prices")
    out = tool.invoke(
        {
            "chain": "ethereum",
            "wallet_address": "0x1111111111111111111111111111111111111111",
            "token_addresses": ["0x2222222222222222222222222222222222222222"],
        }
    )
    addr = "0x2222222222222222222222222222222222222222"
    assert out["ok"] is True
    assert out["result"]["prices_by_address"][addr] == "3.14"
    _assert_no_banned_values(out)


def test_request_user_input_shape_and_context():
    signing_path = "vault/signing/local"
    secrets = {signing_path: "0x" + "ff" * 32}
    settings = AureySettings(
        wallet_signing_key_secret_path=signing_path,
    )
    runtime = AureyRuntime(
        settings=settings,
        secret_store=FakeSecretStore(secrets),
        evm_rpc_factory=rpc_factory_from_mapping({}),
        http=ScriptedHttpClient(),
        tx_pipeline=DeterministicTxPipeline(),
        lifi_base_url="https://li.quest",
    )
    tool = _tool_by_name(build_aurey_subgraph_tools(runtime), "request_user_input")
    q = UserQuestion(prompt="Which chain?", id="c1")
    out = tool.invoke({"questions": [q]})
    assert out == {"ok": True, "result": {"status": "needs_user_input", "question_count": 1}}
    pending = get_pending_user_questions()
    assert pending == [{"prompt": "Which chain?", "id": "c1"}]


def test_tx_prepare_named_tool_ignores_legacy_kind_field():
    signing_path = "vault/signing/local"
    secrets = {signing_path: "0x" + "ff" * 32}
    settings = AureySettings(wallet_signing_key_secret_path=signing_path)
    runtime = AureyRuntime(
        settings=settings,
        secret_store=FakeSecretStore(secrets),
        evm_rpc_factory=rpc_factory_from_mapping({}),
        http=ScriptedHttpClient(),
        tx_pipeline=DeterministicTxPipeline(),
        lifi_base_url="https://li.quest",
    )
    tool = _tool_by_name(build_aurey_subgraph_tools(runtime), "tx_prepare_erc20_transfer")
    out = tool.invoke(
        {
            "kind": "erc20_transfer",
            "chain": "base",
            "from_address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "token_address": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "to_address": "0xcccccccccccccccccccccccccccccccccccccccc",
            "amount_wei": 10_000,
        }
    )
    assert out["ok"] is True
    assert out["result"]["envelope"]["kind"] == "erc20_transfer"
    assert out["result"]["prepared_id"].startswith("ptx_")
    assert out["result"]["envelope"]["data_selector"] == "0xa9059cbb"
    _assert_no_banned_values(out)


def test_tx_execute_tool_accepts_tx_execute_shape():
    signing_path = "vault/signing/local"
    secrets = {signing_path: "0x" + "ff" * 32}
    settings = AureySettings(wallet_signing_key_secret_path=signing_path)
    runtime = AureyRuntime(
        settings=settings,
        secret_store=FakeSecretStore(secrets),
        evm_rpc_factory=rpc_factory_from_mapping({}),
        http=ScriptedHttpClient(),
        tx_pipeline=DeterministicTxPipeline(),
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
    TxExecuteInput.model_validate({"envelope": env})
    tool = _tool_by_name(build_aurey_subgraph_tools(runtime), "tx_execute")
    out = tool.invoke({"envelope": env})
    assert out["ok"] is True
    assert out["result"]["tx_hash"].startswith("0x")
    _assert_no_banned_values(out)


def test_tx_execute_tool_coerces_mistaken_lifi_prepared_blob():
    """Models sometimes pass ``swap_prepare`` ``prepared`` to ``tx_execute``; repair that path."""

    signing_path = "vault/signing/local"
    wallet = "0xc1923710468607b8b7db38a6afbb9b432744390c"
    secrets = {signing_path: "0x" + "ff" * 32}
    settings = AureySettings(wallet_signing_key_secret_path=signing_path)
    runtime = AureyRuntime(
        settings=settings,
        secret_store=FakeSecretStore(secrets),
        evm_rpc_factory=rpc_factory_from_mapping({}),
        http=ScriptedHttpClient(),
        tx_pipeline=DeterministicTxPipeline(),
        lifi_base_url="https://li.quest",
    )
    mistaken = {
        "route_id": "dfc10047-da37-4954-bcea-48a218182a87:0",
        "transaction_request": {
            "value": "0x0",
            "to": "0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae",
            "data": "0x",
            "chainId": 8453,
            "gasLimit": "0xff6c6",
            "from": wallet,
        },
    }
    tool = _tool_by_name(build_aurey_subgraph_tools(runtime), "tx_execute")
    out = tool.invoke({"envelope": mistaken})
    assert out["ok"] is True
    assert out["result"]["tx_hash"].startswith("0x")
    _assert_no_banned_values(out)


def test_lifi_swap_prepare_returns_compact_prepared_id_and_execute_uses_it():
    signing_path = "vault/signing/local"
    wallet = "0xc1923710468607b8b7db38a6afbb9b432744390c"
    secrets = {signing_path: "0x" + "ff" * 32}
    large_calldata = "0x5fd9ae2e" + ("00" * 2500)

    def match_lifi_quote(**kw: object) -> bool:
        return kw.get("method") == "GET" and "/v1/quote?" in str(kw.get("url") or "")

    runtime = AureyRuntime(
        settings=AureySettings(wallet_signing_key_secret_path=signing_path),
        secret_store=FakeSecretStore(secrets),
        evm_rpc_factory=rpc_factory_from_mapping({}),
        http=ScriptedHttpClient(
            [
                (
                    match_lifi_quote,
                    {
                        "id": "route-1:0",
                        "transactionRequest": {
                            "value": "0x0",
                            "to": "0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae",
                            "data": large_calldata,
                            "chainId": 8453,
                            "gasLimit": "0x5208",
                            "from": wallet,
                        },
                    },
                )
            ]
        ),
        tx_pipeline=DeterministicTxPipeline(),
        lifi_base_url="https://li.quest",
    )
    tools = build_aurey_subgraph_tools(runtime)
    swap_prepare = _tool_by_name(tools, "swap_prepare")
    prepared = swap_prepare.invoke(
        {
            "from_chain": "base",
            "to_chain": "base",
            "from_asset": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
            "to_asset": "0x4200000000000000000000000000000000000006",
            "from_amount_wei": "1000000",
            "from_address": wallet,
            "to_address": wallet,
            "slippage": 0.005,
            "order": "CHEAPEST",
        }
    )
    assert prepared["ok"] is True
    prepared_id = prepared["result"]["prepared_id"]
    assert prepared_id.startswith("ptx_")
    assert prepared["result"]["prepared"]["data_selector"] == "0x5fd9ae2e"
    assert "transaction_request" not in prepared["result"]["prepared"]
    assert large_calldata not in json.dumps(prepared, sort_keys=True)

    tx_execute = _tool_by_name(tools, "tx_execute")
    out = tx_execute.invoke({"prepared_id": prepared_id})
    assert out["ok"] is True
    assert out["result"]["tx_hash"].startswith("0x")

    legacy_shape = tx_execute.invoke({"envelope": prepared["result"]["prepared"]})
    assert legacy_shape["ok"] is True
    assert legacy_shape["result"]["tx_hash"].startswith("0x")


def test_tx_execute_tool_rejects_idempotency_key_without_envelope():
    signing_path = "vault/signing/local"
    secrets = {signing_path: "0x" + "ff" * 32}
    settings = AureySettings(wallet_signing_key_secret_path=signing_path)
    runtime = AureyRuntime(
        settings=settings,
        secret_store=FakeSecretStore(secrets),
        evm_rpc_factory=rpc_factory_from_mapping({}),
        http=ScriptedHttpClient(),
        tx_pipeline=DeterministicTxPipeline(),
        lifi_base_url="https://li.quest",
    )
    tool = _tool_by_name(build_aurey_subgraph_tools(runtime), "tx_execute")
    with pytest.raises(Exception, match="envelope"):
        tool.invoke({"idempotency_key": "usdc-to-weth-base-1"})


def test_tx_execute_tool_oneclaw_intents_requires_runtime_signer():
    settings = AureySettings(
        evm_signing_mode="oneclaw_intents",
        oneclaw_agent_id="agent-tool",
        wallet_signing_key_secret_path=None,
    )
    runtime = AureyRuntime(
        settings=settings,
        secret_store=FakeSecretStore({}),
        evm_rpc_factory=rpc_factory_from_mapping({}),
        http=ScriptedHttpClient(),
        tx_pipeline=DeterministicTxPipeline(),
        lifi_base_url="https://li.quest",
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
    tool = _tool_by_name(build_aurey_subgraph_tools(runtime), "tx_execute")
    out = tool.invoke({"envelope": envelope})
    assert out["ok"] is False
    assert out["error"]["code"] == "secret_not_configured"


def test_create_aurey_deep_agent_compiles():
    signing_path = "vault/signing/local"
    secrets = {signing_path: "0x" + "ff" * 32}
    settings = AureySettings(wallet_signing_key_secret_path=signing_path)
    runtime = AureyRuntime(
        settings=settings,
        secret_store=FakeSecretStore(secrets),
        evm_rpc_factory=rpc_factory_from_mapping({}),
        http=ScriptedHttpClient(),
        tx_pipeline=DeterministicTxPipeline(),
        lifi_base_url="https://li.quest",
    )
    graph = create_aurey_deep_agent(
        runtime,
        model=_DummyChat(),
        checkpointer=make_memory_checkpointer(),
    )
    assert graph is not None
    cfg = thread_config("session-unit-test")
    assert "configurable" in cfg and cfg["configurable"]["thread_id"] == "session-unit-test"


def test_create_aurey_deep_agent_import_error_message(monkeypatch):
    monkeypatch.setattr(deep_agent_mod, "_create_deep_agent_impl", None)
    signing_path = "vault/signing/local"
    secrets = {signing_path: "0x" + "ff" * 32}
    settings = AureySettings(wallet_signing_key_secret_path=signing_path)
    runtime = AureyRuntime(
        settings=settings,
        secret_store=FakeSecretStore(secrets),
        evm_rpc_factory=rpc_factory_from_mapping({}),
        http=ScriptedHttpClient(),
        tx_pipeline=DeterministicTxPipeline(),
        lifi_base_url="https://li.quest",
    )
    with pytest.raises(RuntimeError, match="deepagents"):
        create_aurey_deep_agent(runtime, model=_DummyChat())


def test_wallet_context_for_deep_agent_prompt_empty():
    assert deep_agent_mod.wallet_context_for_deep_agent_prompt(AureySettings()) == ""
    assert deep_agent_mod.wallet_context_for_deep_agent_prompt(
        AureySettings(deep_agent_wallet_address="  "),
    ) == ""


def test_wallet_context_for_deep_agent_prompt_valid():
    addr = "0xd8da6bf26964af9d7eed9e03e53415d37aa96045"
    out = deep_agent_mod.wallet_context_for_deep_agent_prompt(
        AureySettings(deep_agent_wallet_address=addr),
    )
    assert out
    assert "Persistent operator context" in out
    assert normalize_evm_address(addr) in out


def test_wallet_context_for_deep_agent_prompt_invalid(caplog):
    import logging as _logging

    caplog.set_level(_logging.WARNING)
    out = deep_agent_mod.wallet_context_for_deep_agent_prompt(
        AureySettings(deep_agent_wallet_address="not-an-address"),
    )
    assert out == ""
    assert any(
        "AUREY_DEEP_AGENT_WALLET_ADDRESS" in r.getMessage() for r in caplog.records
    )


def test_runtime_wiring_context_llm_direct_mode_prompt_line() -> None:
    out = deep_agent_mod.runtime_wiring_context_for_deep_agent_prompt(AureySettings(llm_proxy="direct"))
    assert "direct OpenAI-compatible API" in out


def test_runtime_wiring_context_for_deep_agent_prompt_no_identifiers_or_paths():
    s = AureySettings(
        oneclaw_base_url="https://api.example.test",
        oneclaw_vault_id="vault-xyz-do-not-show",
        oneclaw_agent_id="agent-value-must-not-appear-in-prompt",
        oneclaw_api_key_secret_source="MY_BOOT_ENV_DO_NOT_SHOW",
        alchemy_api_secret_path="vault/alchemy/leak-sensitive",
        lifi_api_secret_path=None,
        evm_signing_mode="vault_key",
        wallet_signing_key_secret_path="vault/sign/secret-branch",
        telegram_bot_token_secret_path="telegram/path",
        lifi_integrator="test-int-do-not-show",
        database_url="postgresql://user:SUPER_SECRET@localhost:5432/db",
        llm_proxy="shroud",
    )
    out = deep_agent_mod.runtime_wiring_context_for_deep_agent_prompt(s)
    assert out
    assert "vault_key" in out
    assert "base" in out and "ethereum" in out
    assert "non-default base URL" in out
    assert "vault linkage: configured" in out
    assert "hosted-agent token flow: configured" in out
    assert "vault/" not in out
    assert "MY_BOOT_ENV" not in out
    assert "test-int" not in out
    assert "SUPER_SECRET" not in out
    assert "postgresql://" not in out
    assert "agent-value-must-not-appear" not in out
    assert "vault-xyz-do-not-show" not in out
    assert "1Claw Shroud proxy" in out


def test_earn_list_vaults_tool_ok_with_trimmed_rows():
    """Vault rows match Earn graph trimming (no unknown Earn API keys in results)."""

    signing_path = "vault/signing/local"
    secrets = {signing_path: "0x" + "ff" * 32}
    settings = AureySettings(wallet_signing_key_secret_path=signing_path)

    def match_vaults(**kw: object) -> bool:
        return kw.get("method") == "GET" and "earn.li.fi/v1/vaults" in str(kw.get("url") or "")

    fat_row = {
        "address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "chainId": 8453,
        "name": "Fat row",
        "protocol": {"id": "x", "name": "Y"},
        "isTransactional": True,
        "isComposerSupported": True,
        "ignoredByTrim": {"nested": ["should", "not", "appear"]},
    }
    http = ScriptedHttpClient([(match_vaults, {"data": [fat_row], "total": 1})])
    runtime = AureyRuntime(
        settings=settings,
        secret_store=FakeSecretStore(secrets),
        evm_rpc_factory=rpc_factory_from_mapping({}),
        http=http,
        tx_pipeline=DeterministicTxPipeline(),
        lifi_base_url="https://li.quest",
    )
    tool = _tool_by_name(build_aurey_subgraph_tools(runtime), "earn_list_vaults")
    out = tool.invoke({"chain": "base", "limit": 5})
    assert out["ok"] is True
    row = out["result"]["vaults"][0]
    assert "ignoredByTrim" not in row
    assert row["address"] == "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert row.get("chain") == "base"
    _assert_no_banned_values(out)


def test_earn_prepare_deposit_tool_rejects_non_composer_vault():
    signing_path = "vault/signing/local"
    secrets = {signing_path: "0x" + "ff" * 32}
    settings = AureySettings(wallet_signing_key_secret_path=signing_path)
    vault = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    vault_path = to_checksum_evm_address(vault)

    def match_vault(**kw: object) -> bool:
        u = str(kw.get("url") or "")
        return kw.get("method") == "GET" and f"/v1/vaults/8453/{vault_path}" in u and "earn.li.fi" in u

    http = ScriptedHttpClient(
        [
            (
                match_vault,
                {
                    "address": vault,
                    "chainId": 8453,
                    "name": "No Composer",
                    "protocol": {"id": "x"},
                    "isComposerSupported": False,
                    "isTransactional": True,
                },
            )
        ]
    )
    runtime = AureyRuntime(
        settings=settings,
        secret_store=FakeSecretStore(secrets),
        evm_rpc_factory=rpc_factory_from_mapping({}),
        http=http,
        tx_pipeline=DeterministicTxPipeline(),
        lifi_base_url="https://li.quest",
    )
    tool = _tool_by_name(build_aurey_subgraph_tools(runtime), "earn_prepare_deposit")
    out = tool.invoke(
        {
            "vault_chain": "base",
            "vault_address": vault,
            "from_chain": "base",
            "from_asset": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
            "from_amount_wei": "1000000",
            "from_address": "0xcccccccccccccccccccccccccccccccccccccccc",
        }
    )
    assert out["ok"] is False
    assert out["error"]["code"] == "invalid_input"
    assert "Composer" in out["error"]["message"]
    assert http.calls and len(http.calls) == 1
    _assert_no_banned_values(out)


def test_earn_prepare_deposit_tool_non_transactional_without_composer_rejected():
    signing_path = "vault/signing/local"
    secrets = {signing_path: "0x" + "ff" * 32}
    settings = AureySettings(wallet_signing_key_secret_path=signing_path)
    vault = "0xdddddddddddddddddddddddddddddddddddddddd"
    vault_path = to_checksum_evm_address(vault)

    def match_vault(**kw: object) -> bool:
        u = str(kw.get("url") or "")
        return kw.get("method") == "GET" and "/v1/vaults/8453/" in u and vault_path in u

    http = ScriptedHttpClient(
        [
            (
                match_vault,
                {
                    "address": vault,
                    "chainId": 8453,
                    "protocol": {"id": "x"},
                    "isTransactional": False,
                },
            )
        ]
    )
    runtime = AureyRuntime(
        settings=settings,
        secret_store=FakeSecretStore(secrets),
        evm_rpc_factory=rpc_factory_from_mapping({}),
        http=http,
        tx_pipeline=DeterministicTxPipeline(),
        lifi_base_url="https://li.quest",
    )
    tool = _tool_by_name(build_aurey_subgraph_tools(runtime), "earn_prepare_deposit")
    out = tool.invoke(
        {
            "vault_chain": "base",
            "vault_address": vault,
            "from_chain": "base",
            "from_asset": "eth",
            "from_amount_wei": "1000000000000000",
            "from_address": "0xcccccccccccccccccccccccccccccccccccccccc",
        }
    )
    assert out["ok"] is False
    assert out["error"]["code"] == "invalid_input"
    assert "transactional" in out["error"]["message"].lower()


def _lifi_quote_response_stub(*, route_tag: str, tx_chain_id: int) -> dict[str, Any]:
    return {
        "id": route_tag,
        "estimate": {"approvalAddress": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
        "action": {
            "fromAmount": "1000000",
            "fromToken": {"address": "0x1111111111111111111111111111111111111111"},
        },
        "transactionRequest": {
            "to": "0x3333333333333333333333333333333333333333",
            "data": "0xdeadbeef",
            "value": "0x0",
            "chainId": tx_chain_id,
            "from": "0xcccccccccccccccccccccccccccccccccccccccc",
            "gasLimit": "0x5208",
        },
    }


def test_earn_prepare_deposit_tool_uses_vault_as_to_token_and_stores_execute_prepared_id():
    signing_path = "vault/signing/local"
    secrets = {signing_path: "0x" + "ff" * 32}
    settings = AureySettings(wallet_signing_key_secret_path=signing_path)
    wallet = "0xcccccccccccccccccccccccccccccccccccccccc"
    vault = "0x2222222222222222222222222222222222222222"

    def match_earn_vault(**kw: object) -> bool:
        u = str(kw.get("url") or "").lower()
        return kw.get("method") == "GET" and "earn.li.fi/v1/vaults/8453/" in u and vault.lower() in u

    def match_lifi_quote(**kw: object) -> bool:
        u = str(kw.get("url") or "")
        if kw.get("method") != "GET" or "/v1/quote?" not in u or "li.quest" not in u:
            return False
        q = parse_qs(urlparse(u).query)
        return q.get("toToken") == [vault.lower()] and q.get("toChain") == ["8453"]

    http = ScriptedHttpClient(
        [
            (
                match_earn_vault,
                {
                    "address": vault,
                    "chainId": 8453,
                    "name": "Composer vault",
                    "protocol": {"id": "pv"},
                    "isComposerSupported": True,
                    "isTransactional": True,
                },
            ),
            (match_lifi_quote, _lifi_quote_response_stub(route_tag="earn-dep-1", tx_chain_id=8453)),
        ]
    )
    runtime = AureyRuntime(
        settings=settings,
        secret_store=FakeSecretStore(secrets),
        evm_rpc_factory=rpc_factory_from_mapping({}),
        http=http,
        tx_pipeline=DeterministicTxPipeline(),
        lifi_base_url="https://li.quest",
    )
    tool = _tool_by_name(build_aurey_subgraph_tools(runtime), "earn_prepare_deposit")
    out = tool.invoke(
        {
            "vault_chain": "base",
            "vault_address": vault,
            "from_chain": "base",
            "from_asset": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
            "from_amount_wei": "1000000",
            "from_address": wallet,
        }
    )
    assert out["ok"] is True
    pid = out["result"]["prepared_id"]
    assert pid.startswith("ptx_")
    assert out["result"]["prepared"]["route_id"] == "earn-dep-1"
    assert out["result"]["earn_deposit"]["requires_status_polling"] is False
    assert out["result"]["earn_deposit"]["vault"]["address"] == vault


def test_earn_prepare_deposit_tool_cross_chain_sets_requires_status_polling():
    signing_path = "vault/signing/local"
    secrets = {signing_path: "0x" + "ff" * 32}
    settings = AureySettings(wallet_signing_key_secret_path=signing_path)
    wallet = "0xcccccccccccccccccccccccccccccccccccccccc"
    vault = "0x2222222222222222222222222222222222222222"

    def match_earn_vault(**kw: object) -> bool:
        u = str(kw.get("url") or "").lower()
        return kw.get("method") == "GET" and "earn.li.fi/v1/vaults/8453/" in u and vault.lower() in u

    def match_lifi_quote(**kw: object) -> bool:
        u = str(kw.get("url") or "")
        if kw.get("method") != "GET" or "/v1/quote?" not in u or "li.quest" not in u:
            return False
        q = parse_qs(urlparse(u).query)
        return q.get("fromChain") == ["1"] and q.get("toChain") == ["8453"]

    http = ScriptedHttpClient(
        [
            (
                match_earn_vault,
                {
                    "address": vault,
                    "chainId": 8453,
                    "name": "Composer vault",
                    "protocol": {"id": "pv"},
                    "isComposerSupported": True,
                    "isTransactional": True,
                },
            ),
            (match_lifi_quote, _lifi_quote_response_stub(route_tag="earn-dep-xc", tx_chain_id=1)),
        ]
    )
    runtime = AureyRuntime(
        settings=settings,
        secret_store=FakeSecretStore(secrets),
        evm_rpc_factory=rpc_factory_from_mapping({}),
        http=http,
        tx_pipeline=DeterministicTxPipeline(),
        lifi_base_url="https://li.quest",
    )
    tool = _tool_by_name(build_aurey_subgraph_tools(runtime), "earn_prepare_deposit")
    out = tool.invoke(
        {
            "vault_chain": "base",
            "vault_address": vault,
            "from_chain": "ethereum",
            "from_asset": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
            "from_amount_wei": "1000000",
            "from_address": wallet,
        }
    )
    assert out["ok"] is True
    assert out["result"]["earn_deposit"]["requires_status_polling"] is True
    _assert_no_banned_values(out)
