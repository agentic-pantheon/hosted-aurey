"""Bootstrap wiring for optional HTTP service."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from aurey.custody import FakeSecretStore
from aurey.custody.secret_store import OneClawHttpClient
from aurey.graphs import DeterministicTxPipeline
from aurey.reasoning import create_aurey_deep_agent, make_memory_checkpointer, thread_config
from aurey.reasoning.checkpointer import ManagedPostgresCheckpointer
from aurey.runtime import AureyRuntime
from aurey.service.adapters import UrllibHttpJsonClient, make_evm_rpc_factory
from aurey.service.bootstrap import AureyServiceBootstrapError, bootstrap_aurey_service_state
from aurey.service.state import AureyServiceState
from aurey.settings import AureySettings
from tests.fakes.evm_rpc import rpc_factory_from_mapping
from tests.fakes.http_client import ScriptedHttpClient


class _DummyChat(BaseChatModel):
    model_name: str = "stub"

    @property
    def _llm_type(self) -> str:
        return "dummy"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])

    def bind_tools(self, tools, **kwargs):
        return self


def test_bootstrap_raises_without_vault_id(monkeypatch):
    monkeypatch.setenv("AUREY_OCV_AGENT_API_KEY", "k")
    s = AureySettings(ocv_vault_id="")
    with pytest.raises(AureyServiceBootstrapError, match="vault id"):
        bootstrap_aurey_service_state(s)


def test_bootstrap_raises_on_missing_bootstrap_env(monkeypatch):
    monkeypatch.delenv("AUREY_OCV_AGENT_API_KEY", raising=False)
    s = AureySettings(ocv_vault_id="v1")
    with pytest.raises(AureyServiceBootstrapError, match="Operator 1Claw agent API key"):
        bootstrap_aurey_service_state(s)


def test_bootstrap_oneclaw_evm_signer_is_same_as_secret_store_client(monkeypatch):
    monkeypatch.setenv("AUREY_OCV_AGENT_API_KEY", "k")
    clients: list[OneClawHttpClient] = []

    def capture_client(*args: object, **kwargs: object) -> OneClawHttpClient:
        client = OneClawHttpClient(*args, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr("aurey.service.bootstrap.OneClawHttpClient", capture_client)
    s = AureySettings(ocv_vault_id="v-bootstrap-signer")
    state = bootstrap_aurey_service_state(s)
    assert len(clients) == 1
    assert state.runtime.oneclaw_evm_signer is clients[0]
    assert state.runtime.oneclaw_operator_http is clients[0]


def test_bootstrap_uses_postgres_when_database_url(monkeypatch):
    monkeypatch.setenv("AUREY_OCV_AGENT_API_KEY", "k")
    opened: list[str] = []

    def fake_open(url: str) -> ManagedPostgresCheckpointer:
        opened.append(url)
        cm = MagicMock()
        cm.__exit__ = MagicMock(return_value=False)
        saver = MagicMock()
        return ManagedPostgresCheckpointer(saver=saver, _cm=cm)

    monkeypatch.setattr("aurey.service.bootstrap.open_postgres_checkpointer", fake_open)
    s = AureySettings(ocv_vault_id="v-pg", database_url="postgres://stub")
    state = bootstrap_aurey_service_state(s)
    cm = state._postgres._cm
    assert opened == ["postgres://stub"]
    assert state.checkpointer is state._postgres.saver
    assert state._postgres is not None
    state.close_checkpointer()
    assert state._postgres is None
    cm.__exit__.assert_called_once()


def test_bootstrap_postgres_failure_wrapped(monkeypatch):
    monkeypatch.setenv("AUREY_OCV_AGENT_API_KEY", "k")

    def boom(url: str) -> ManagedPostgresCheckpointer:
        raise OSError("connection refused")

    monkeypatch.setattr("aurey.service.bootstrap.open_postgres_checkpointer", boom)
    s = AureySettings(ocv_vault_id="v-pg", database_url="postgres://stub")
    with pytest.raises(AureyServiceBootstrapError, match="PostgreSQL checkpointer"):
        bootstrap_aurey_service_state(s)


def test_construct_service_state_get_graph_invoke_smoke(monkeypatch):
    """Fake runtime + patched deep agent avoids live model providers."""

    monkeypatch.setattr(
        "aurey.service.state.create_aurey_deep_agent",
        lambda runtime, *, model, checkpointer=None, **kw: create_aurey_deep_agent(
            runtime,
            model=_DummyChat(),
            checkpointer=checkpointer,
            **kw,
        ),
    )

    alchemy_path = "vault/alchemy"
    signing_path = "vault/signing/local"
    settings = AureySettings(
        alchemy_api_secret_path=alchemy_path,
        wallet_signing_key_secret_path=signing_path,
        deep_agent_default_model="stub-spec",
        ocv_vault_id="ignored-for-fake-runtime",
    )
    runtime = AureyRuntime(
        settings=settings,
        secret_store=FakeSecretStore(
            {
                alchemy_path: "SECRET_FRAGMENT",
                signing_path: "0x" + "ff" * 32,
            }
        ),
        evm_rpc_factory=rpc_factory_from_mapping({}),
        http=ScriptedHttpClient(),
        tx_pipeline=DeterministicTxPipeline(),
    )
    state = AureyServiceState(
        settings=settings,
        runtime=runtime,
        checkpointer=make_memory_checkpointer(),
        default_model="stub-spec",
    )
    graph = state.get_or_create_graph("stub-spec")
    out = graph.invoke(
        {"messages": [HumanMessage(content="hi")]},
        config=thread_config("bootstrap-smoke-thread"),
    )
    assert out["messages"][-1].content == "ok"


def test_adapters_construct_runtime_dependencies():
    alchemy_path = "alchemy/path"
    s = AureySettings(alchemy_api_secret_path=alchemy_path)
    store = FakeSecretStore({alchemy_path: "alchemy-key"})
    rt = AureyRuntime(
        settings=s,
        secret_store=store,
        evm_rpc_factory=make_evm_rpc_factory(timeout_s=1.0),
        http=UrllibHttpJsonClient(timeout_s=1.0),
        tx_pipeline=DeterministicTxPipeline(),
    )
    assert rt.http is not None
    url = "https://eth-mainnet.g.alchemy.com/v2/" + rt.secret_store.get_secret(
        alchemy_path
    ).reveal()
    port = rt.evm_rpc_factory(url)
    assert port is not None
