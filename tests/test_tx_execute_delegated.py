"""Hosted oneclaw_intents path on ``tx_execute`` (agent-token flow, no per-user delegation token)."""

from __future__ import annotations

from aurey.cloud.signing_context import HostedSigningContext, hosted_signing_context_scope
from aurey.custody import FakeOneClawClient, FakeSecretStore
from aurey.graphs import DeterministicTxPipeline
from aurey.graphs.tx_execute import build_tx_execute_graph
from aurey.runtime import AureyRuntime
from aurey.settings import AureySettings
from tests.fakes.evm_rpc import rpc_factory_from_mapping
from tests.fakes.http_client import ScriptedHttpClient


def test_tx_execute_hosted_uses_agent_token_path_no_delegated_exchange(monkeypatch):
    """Bootstrap api_key + user_agent_id: sign with ``authorization_bearer=None`` (agent JWT flow)."""

    monkeypatch.setenv("AUREY_OPERATOR_AGENT_API_KEY", "ocv_operator_test")

    oneclaw = FakeOneClawClient(delegated_jwt="unused-in-hosted-path")
    settings = AureySettings(
        hosted_platform_enabled=True,
        evm_signing_mode="oneclaw_intents",
        operator_agent_api_key_secret_source="AUREY_OPERATOR_AGENT_API_KEY",
    )
    runtime = AureyRuntime(
        settings=settings,
        secret_store=FakeSecretStore({}),
        evm_rpc_factory=rpc_factory_from_mapping({}),
        http=ScriptedHttpClient(),
        tx_pipeline=DeterministicTxPipeline(),
        lifi_base_url="https://li.quest",
        oneclaw_evm_signer=oneclaw,
    )
    graph = build_tx_execute_graph(runtime)
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

    ctx = HostedSigningContext(
        telegram_user_id=1,
        user_agent_id="user-agent-99",
    )
    with hosted_signing_context_scope(ctx):
        out = graph.invoke({"input": {"envelope": envelope}})

    assert "error" not in out
    assert out["result"]["tx_hash"].startswith("0x")
    assert len(oneclaw.delegated_calls) == 0
    assert len(oneclaw.sign_requests) == 1
    assert oneclaw.sign_requests[0].get("authorization_bearer") is None
    assert oneclaw.sign_requests[0]["agent_id"] == "user-agent-99"


def test_tx_execute_hosted_works_without_legacy_delegation_subject_token(monkeypatch):
    """Explicit empty/legacy delegation field does not trigger delegated-token POST."""

    monkeypatch.delenv("AUREY_OPERATOR_AGENT_API_KEY", raising=False)
    monkeypatch.setenv("AUREY_ONECLAW_BOOTSTRAP_API_KEY", "same-as-operator-bootstrap")

    oneclaw = FakeOneClawClient(delegated_jwt="unused")
    settings = AureySettings(
        hosted_platform_enabled=True,
        evm_signing_mode="oneclaw_intents",
    )
    runtime = AureyRuntime(
        settings=settings,
        secret_store=FakeSecretStore({}),
        evm_rpc_factory=rpc_factory_from_mapping({}),
        http=ScriptedHttpClient(),
        tx_pipeline=DeterministicTxPipeline(),
        lifi_base_url="https://li.quest",
        oneclaw_evm_signer=oneclaw,
    )
    graph = build_tx_execute_graph(runtime)
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

    ctx = HostedSigningContext(
        telegram_user_id=1,
        user_agent_id="agent-42",
        delegation_subject_token=None,
    )
    with hosted_signing_context_scope(ctx):
        out = graph.invoke({"input": {"envelope": envelope}})

    assert "error" not in out
    assert len(oneclaw.delegated_calls) == 0
    assert oneclaw.sign_requests[0].get("authorization_bearer") is None
