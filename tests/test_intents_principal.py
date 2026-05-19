"""``OneClawSigningPrincipal`` hosted vs legacy resolution."""

from __future__ import annotations

from aurey.cloud.signing_context import HostedSigningContext, hosted_signing_context_scope
from aurey.custody import FakeOneClawClient, FakeSecretStore
from aurey.custody.intents_principal import OneClawSigningPrincipal
from aurey.graphs import DeterministicTxPipeline
from aurey.runtime import AureyRuntime
from aurey.settings import AureySettings
from tests.fakes.evm_rpc import rpc_factory_from_mapping
from tests.fakes.http_client import ScriptedHttpClient


def test_resolve_hosted_only_needs_user_agent_id():
    settings = AureySettings(
        hosted_platform_enabled=True,
        evm_signing_mode="oneclaw_intents",
        oneclaw_agent_id="ignored-when-hosted-context",
    )
    runtime = AureyRuntime(
        settings=settings,
        secret_store=FakeSecretStore({}),
        evm_rpc_factory=rpc_factory_from_mapping({}),
        http=ScriptedHttpClient(),
        tx_pipeline=DeterministicTxPipeline(),
        lifi_base_url="https://li.quest",
        oneclaw_evm_signer=FakeOneClawClient(),
    )
    ctx = HostedSigningContext(
        telegram_user_id=7,
        user_agent_id=" tpl-user-agent ",
    )
    with hosted_signing_context_scope(ctx):
        principal, err = OneClawSigningPrincipal.resolve(runtime)
    assert err is None
    assert principal is not None
    assert principal.agent_id == "tpl-user-agent"
    assert principal.authorization_bearer is None


def test_resolve_hosted_errors_when_user_agent_id_missing():
    settings = AureySettings(hosted_platform_enabled=True, evm_signing_mode="oneclaw_intents")
    runtime = AureyRuntime(
        settings=settings,
        secret_store=FakeSecretStore({}),
        evm_rpc_factory=rpc_factory_from_mapping({}),
        http=ScriptedHttpClient(),
        tx_pipeline=DeterministicTxPipeline(),
        lifi_base_url="https://li.quest",
        oneclaw_evm_signer=FakeOneClawClient(),
    )
    ctx = HostedSigningContext(telegram_user_id=1, user_agent_id="")
    with hosted_signing_context_scope(ctx):
        principal, err = OneClawSigningPrincipal.resolve(runtime)
    assert principal is None
    assert err is not None
    assert err.get("code") == "secret_not_configured"
