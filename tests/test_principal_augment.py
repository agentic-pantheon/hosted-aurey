"""Augment base runtime with hosted-user delegated signing."""

from __future__ import annotations

import pytest

from aurey.custody import FakeSecretStore
from aurey.graphs import DeterministicTxPipeline
from aurey.principal import UserPrincipal
from aurey.principal_augment import augment_runtime_for_principal
from aurey.runtime import AureyRuntime
from aurey.settings import AureySettings
from tests.fakes.evm_rpc import rpc_factory_from_mapping
from tests.fakes.http_client import ScriptedHttpClient


def test_augment_requires_operator_http() -> None:
    settings = AureySettings(ocv_vault_id="v", oneclaw_delegated_token_scope="s")
    rt = AureyRuntime(
        settings=settings,
        secret_store=FakeSecretStore({"g": "granttok"}),
        evm_rpc_factory=rpc_factory_from_mapping({}),
        http=ScriptedHttpClient(),
        tx_pipeline=DeterministicTxPipeline(),
        oneclaw_operator_http=None,
    )
    principal = UserPrincipal(db_user_id="1", user_agent_id="a", grant_ref_path="g")
    with pytest.raises(RuntimeError, match="oneclaw_operator_http"):
        augment_runtime_for_principal(rt, principal)

