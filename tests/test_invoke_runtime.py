"""ContextVar-backed runtime overlay used during deep-agent invoke."""

from __future__ import annotations

from dataclasses import replace

from aurey.custody import FakeSecretStore
from aurey.graphs import DeterministicTxPipeline
from aurey.principal import UserPrincipal
from aurey.runtime import AureyRuntime
from aurey.service import invoke as invoke_mod
from aurey.service.invoke_runtime import (
    AureyRuntimeProxy,
    push_runtime_overlay,
    reset_runtime_overlay,
)
from aurey.settings import AureySettings
from tests.fakes.evm_rpc import rpc_factory_from_mapping
from tests.fakes.http_client import ScriptedHttpClient


def test_runtime_proxy_reads_overlay_principal() -> None:
    settings = AureySettings(ocv_vault_id="vx")
    base = AureyRuntime(
        settings=settings,
        secret_store=FakeSecretStore({}),
        evm_rpc_factory=rpc_factory_from_mapping({}),
        http=ScriptedHttpClient(),
        tx_pipeline=DeterministicTxPipeline(),
    )
    principal = UserPrincipal(
        db_user_id="11111111-1111-1111-1111-111111111111",
        user_agent_id="agt_overlay",
        grant_ref_path="vault/grant",
    )
    overlay = replace(base, principal=principal)
    proxy = AureyRuntimeProxy(base)
    tok = push_runtime_overlay(overlay)
    try:
        assert proxy.principal is principal
    finally:
        reset_runtime_overlay(tok)
    assert proxy.principal is None


def test_invoke_thread_config_carries_session_and_wallet_context(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _StubGraph:
        def invoke(self, _payload, config=None):
            captured["configurable"] = dict((config or {}).get("configurable") or {})
            return {"messages": []}

    class _StubSvc:
        default_model = "m"
        onboarding = None

        def get_or_create_graph(self, model, **kwargs):
            _ = model, kwargs
            return _StubGraph()

    monkeypatch.setattr(
        invoke_mod,
        "_invoke_graph_with_transient_retries",
        lambda graph, **kw: graph.invoke(None, config=kw["config"]),
    )

    addr = "0x2222222222222222222222222222222222222222"
    invoke_mod.invoke_deep_agent_turn(
        _StubSvc(),
        message="ping",
        session_id="user:aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        hosted_wallet_address=addr,
    )
    cfg = captured["configurable"]
    assert cfg.get("thread_id") == "user:aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert "Hosted user context" in str(cfg.get("wallet_context") or "")
    assert addr.lower() in str(cfg.get("wallet_context") or "").lower()

