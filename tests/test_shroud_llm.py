"""1Claw Shroud LLM proxy helpers (headers, cache keys, invoke preflight)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from aurey.cloud.signing_context import HostedSigningContext
from aurey.graphs import DeterministicTxPipeline
from aurey.reasoning.shroud_llm import (
    ShroudAgentCredentials,
    build_shroud_chat_model,
    hosted_shroud_llm_credentials_ready,
    parse_model_spec,
    resolve_shroud_provider_api_key_header,
)
from aurey.runtime import AureyRuntime
from aurey.service.invoke import invoke_deep_agent_turn
from aurey.service.state import deep_agent_graph_cache_key
from aurey.settings import AureySettings
from tests.fakes.evm_rpc import rpc_factory_from_mapping
from tests.fakes.http_client import ScriptedHttpClient

pytestmark = pytest.mark.preserve_llm_env


@pytest.mark.parametrize(
    ("spec", "provider", "mid"),
    [
        ("openai:gpt-4o-mini", "openai", "gpt-4o-mini"),
        ("OPENAI:o3-mini", "openai", "o3-mini"),
    ],
)
def test_parse_model_spec_openai(spec: str, provider: str, mid: str) -> None:
    assert parse_model_spec(spec) == (provider, mid)


@pytest.mark.parametrize(
    "bad",
    ["", "gpt-4o-mini", "anthropic:claude-3", "openai:", " :gpt"],
)
def test_parse_model_spec_rejects_invalid(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_model_spec(bad)


def test_resolve_provider_header_prefers_plaintext_over_vault() -> None:
    s = AureySettings(
        oneclaw_vault_id="vid-1",
        openai_api_key="sk-plain",
        openai_api_secret_path="api-keys/openai",
    )
    assert resolve_shroud_provider_api_key_header(s) == "sk-plain"


def test_resolve_provider_header_builds_vault_scheme() -> None:
    s = AureySettings(
        oneclaw_vault_id="a1111111-b222-c333-d444-e55555555555",
        openai_api_secret_path="api-keys/foo/bar",
    )
    assert resolve_shroud_provider_api_key_header(s).startswith(
        "vault://a1111111-b222-c333-d444-e55555555555/"
    )


def test_build_shroud_chat_model_passes_expected_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_chat_openai(**kw: object) -> MagicMock:
        captured.clear()
        captured.update(kw)
        return MagicMock()

    monkeypatch.setattr("aurey.reasoning.shroud_llm._SHROUD_CHAT_MODEL_FACTORY", fake_chat_openai)
    settings = AureySettings(
        llm_proxy="shroud",
        shroud_base_url="https://shroud.example.test/",
        oneclaw_vault_id="vault-uu",
        openai_api_secret_path="openai/from/vault",
    )
    creds = ShroudAgentCredentials(agent_id="agent-u", api_key="ocv_test")
    build_shroud_chat_model(
        settings,
        credentials=creds,
        provider="openai",
        model_id="gpt-zz",
        vault_id_for_header="vault-uu",
    )
    assert captured["model"] == "gpt-zz"
    assert captured["api_key"] == "shroud"
    assert captured["base_url"] == "https://shroud.example.test/v1"
    hdr = dict(captured["default_headers"])  # type: ignore[arg-type]
    assert hdr["X-Shroud-Agent-Key"] == "agent-u:ocv_test"
    assert hdr["X-Shroud-Provider"] == "openai"
    assert hdr["X-Shroud-Model"] == "gpt-zz"
    assert hdr["X-Shroud-Api-Key"].startswith("vault://vault-uu/")


def test_deep_agent_graph_cache_key_direct_vs_shroud_suffixes() -> None:
    sd = AureySettings(llm_proxy="direct")
    assert deep_agent_graph_cache_key(
        settings=sd,
        model_spec="openai:gpt",
        hosted_signing_context=None,
    ) == "openai:gpt"

    ss = AureySettings(llm_proxy="shroud")
    op = deep_agent_graph_cache_key(
        settings=ss,
        model_spec="openai:gpt",
        hosted_signing_context=None,
    )
    ctx = HostedSigningContext(
        telegram_user_id=1,
        user_agent_id="u-agent-xyz",
        agent_api_key_encrypted=None,
        agent_api_key_legacy_plaintext=None,
        wallet_address=None,
    )
    us = deep_agent_graph_cache_key(
        settings=ss,
        model_spec="openai:gpt",
        hosted_signing_context=ctx,
    )
    assert op == "openai:gpt::shroud::operator"
    assert us == "openai:gpt::shroud::hosted:u-agent-xyz"
    assert op != us


def test_hosted_credentials_ready_true_when_legacy_plaintext() -> None:
    from aurey.custody.errors import SecretNotFoundError

    signer = MagicMock(spec=["get_secret_operator_bootstrap_resolve"])
    signer.get_secret_operator_bootstrap_resolve.side_effect = SecretNotFoundError(
        "pfx/ag-legacy/agent_api_key",
    )

    settings = AureySettings(
        llm_proxy="shroud",
        oneclaw_vault_id="v-live",
        hosted_agent_api_key_path_prefix="pfx",
    )
    runtime = AureyRuntime(
        settings=settings,
        secret_store=MagicMock(),
        evm_rpc_factory=rpc_factory_from_mapping({}),
        http=ScriptedHttpClient(),
        tx_pipeline=DeterministicTxPipeline(),
        oneclaw_evm_signer=signer,
    )

    ctx = HostedSigningContext(
        telegram_user_id=7,
        user_agent_id="ag-legacy",
        agent_api_key_encrypted=None,
        agent_api_key_legacy_plaintext="ocv_from_db",
        wallet_address=None,
    )
    assert hosted_shroud_llm_credentials_ready(runtime, ctx) is True


def test_invoke_llm_credentials_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = MagicMock(spec=["settings", "default_model", "runtime", "get_or_create_graph"])
    svc.settings = AureySettings(llm_proxy="shroud")
    svc.default_model = "openai:gpt-4o-mini"
    rt = MagicMock()
    rt.settings = svc.settings
    svc.runtime = rt

    monkeypatch.setattr(
        "aurey.service.invoke.hosted_shroud_llm_credentials_ready",
        lambda _r, _c: False,
    )

    sig = HostedSigningContext(
        telegram_user_id=9,
        user_agent_id="unprovisioned",
        agent_api_key_encrypted=None,
        agent_api_key_legacy_plaintext=None,
        wallet_address=None,
    )
    result = invoke_deep_agent_turn(
        svc,
        message="hi",
        session_id="sess",
        hosted_signing_context=sig,
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "llm_credentials_unavailable"
