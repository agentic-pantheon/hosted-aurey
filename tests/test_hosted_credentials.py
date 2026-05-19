"""Unit tests for hosted ``ocv_`` vault paths and Fernet backup."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from aurey.cloud.hosted_credentials import (
    HostedSecretsCipher,
    agent_api_key_secret_path,
    persist_hosted_agent_ocv_credentials,
    resolve_hosted_ocv_for_signing,
)
from aurey.cloud.models import HostedPlatformUserORM
from aurey.custody.secret_store import FakeOneClawClient
from aurey.settings import AureySettings


def test_agent_api_key_secret_path_normalizes_slashes() -> None:
    assert (
        agent_api_key_secret_path("hosted/agents", "uuid-1") == "hosted/agents/uuid-1/agent_api_key"
    )


def test_agent_api_key_secret_path_rejects_empty() -> None:
    with pytest.raises(ValueError):
        agent_api_key_secret_path("", "x")
    with pytest.raises(ValueError):
        agent_api_key_secret_path("pref", "")


def test_hosted_secrets_cipher_roundtrip() -> None:
    raw_key = Fernet.generate_key().decode("ascii")
    settings = AureySettings(hosted_secrets_master_key=raw_key)
    cipher = HostedSecretsCipher.from_settings_optional(settings)
    assert cipher is not None
    ct = cipher.encrypt("ocv_test_secret")
    assert cipher.decrypt(ct) == "ocv_test_secret"


def test_resolve_hosted_ocv_prefers_vault_then_decrypt_then_legacy() -> None:
    fake = FakeOneClawClient(
        secrets={
            agent_api_key_secret_path("hosted/agents", "agent-v"): "from_vault",
        },
    )
    settings = AureySettings(
        oneclaw_vault_id="vault-op",
        hosted_agent_api_key_path_prefix="hosted/agents",
        hosted_secrets_master_key=Fernet.generate_key().decode("ascii"),
    )
    cipher = HostedSecretsCipher.from_settings_optional(settings)
    assert cipher is not None
    ct = cipher.encrypt("from_db")

    out_vault = resolve_hosted_ocv_for_signing(
        settings,
        fake,
        agent_id="agent-v",
        ciphertext=ct,
        legacy_plaintext="from_legacy",
    )
    assert out_vault == "from_vault"

    fake_miss = FakeOneClawClient(secrets={})
    out_cipher = resolve_hosted_ocv_for_signing(
        settings,
        fake_miss,
        agent_id="agent-v",
        ciphertext=ct,
        legacy_plaintext="from_legacy",
    )
    assert out_cipher == "from_db"

    out_legacy = resolve_hosted_ocv_for_signing(
        settings,
        fake_miss,
        agent_id="agent-v",
        ciphertext=None,
        legacy_plaintext="from_legacy",
    )
    assert out_legacy == "from_legacy"


def test_persist_dual_write_encrypt_and_put() -> None:
    raw_key = Fernet.generate_key().decode("ascii")
    settings = AureySettings(
        oneclaw_vault_id="vault-op",
        hosted_agent_api_key_path_prefix="hosted/agents",
        hosted_secrets_master_key=raw_key,
        oneclaw_human_api_token="human-jwt-test",
    )
    fake = FakeOneClawClient()
    row = HostedPlatformUserORM(
        telegram_user_id=1,
        telegram_username=None,
        connection_id="c",
        claim_url="https://example.invalid",
        onboarding_state="awaiting_claim",
        vault_id="vx",
        user_agent_id="agent-x",
    )
    persist_hosted_agent_ocv_credentials(
        settings=settings,
        http_client=fake,
        row=row,
        ocv="ocv_save_me",
        user_agent_id="agent-x",
    )
    expected_path = agent_api_key_secret_path("hosted/agents", "agent-x")
    assert fake.put_human_calls == [
        {
            "vault_id": "vault-op",
            "path": expected_path,
            "value": "ocv_save_me",
            "bearer_token": "human-jwt-test",
        },
    ]
    cipher = HostedSecretsCipher.from_settings_optional(settings)
    assert cipher is not None
    assert row.agent_api_key is None
    assert cipher.decrypt(row.agent_api_key_encrypted or "") == "ocv_save_me"
