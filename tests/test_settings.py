"""AureySettings defaults and environment behavior."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aurey.settings import AureySettings, parse_telegram_allowed_chat_ids


def test_settings_defaults():
    s = AureySettings()
    assert s.plt_oneclaw_base_url == "https://api.1claw.xyz"
    assert s.plt_app_id == ""
    assert s.plt_template_id == ""
    assert s.plt_app_api_key_secret_source is None
    assert s.oidc_issuer == ""
    assert s.subject_token_audience == ""
    assert s.subject_token_ttl_seconds == 300
    assert s.oidc_rsa_private_key_pem_secret_source is None
    assert s.ocv_oneclaw_base_url == "https://api.1claw.xyz"
    assert s.ocv_vault_id == ""
    assert s.ocv_agent_api_key_secret_source == "AUREY_OCV_AGENT_API_KEY"
    assert s.ocv_agent_id is None
    assert s.alchemy_api_secret_path is None
    assert s.lifi_api_secret_path is None
    assert s.lifi_integrator == "aurey"
    assert s.evm_signing_mode == "vault_key"
    assert s.evm_signing_requires_wallet_signing_key_secret_path is True
    assert s.wallet_signing_key_secret_path is None
    assert s.telegram_bot_token_secret_path is None
    assert s.telegram_allowed_chat_ids is None
    assert s.telegram_allowed_chat_id_allowlist is None
    assert s.deep_agent_default_model == "openai:gpt-4o-mini"
    assert s.database_url is None
    assert s.ocv_agent_token_expiry_skew_seconds == 60.0
    assert s.oneclaw_delegated_token_scope == "intents:sign"
    assert s.cloud_hosted_intents_signing_enabled is False
    assert s.hosted_user_grant_secret_path_template is None


def test_settings_env_override(monkeypatch):
    monkeypatch.delenv("AUREY_OCV_ONECLAW_BASE_URL", raising=False)
    monkeypatch.delenv("AUREY_OCV_VAULT_ID", raising=False)
    monkeypatch.delenv("AUREY_ALCHEMY_API_SECRET_PATH", raising=False)
    monkeypatch.delenv("AUREY_EVM_SIGNING_MODE", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AUREY_DATABASE_URL", raising=False)
    monkeypatch.setenv("AUREY_OCV_ONECLAW_BASE_URL", "https://example.invalid/v1/")
    monkeypatch.setenv("AUREY_OCV_VAULT_ID", "vault-env-123")
    monkeypatch.setenv("AUREY_ALCHEMY_API_SECRET_PATH", "aurey/apis/alchemy")

    s = AureySettings()
    assert s.ocv_oneclaw_base_url == "https://example.invalid/v1/"
    assert s.ocv_vault_id == "vault-env-123"
    assert s.alchemy_api_secret_path == "aurey/apis/alchemy"


def test_settings_plt_env_override(monkeypatch):
    monkeypatch.delenv("AUREY_PLT_APP_ID", raising=False)
    monkeypatch.delenv("AUREY_PLT_TEMPLATE_ID", raising=False)
    monkeypatch.delenv("AUREY_PLT_ONECLAW_BASE_URL", raising=False)
    monkeypatch.setenv("AUREY_PLT_APP_ID", "app_abc")
    monkeypatch.setenv("AUREY_PLT_TEMPLATE_ID", "tpl_xyz")
    monkeypatch.setenv("AUREY_PLT_ONECLAW_BASE_URL", "https://plt.example/v0")

    s = AureySettings()
    assert s.plt_app_id == "app_abc"
    assert s.plt_template_id == "tpl_xyz"
    assert s.plt_oneclaw_base_url == "https://plt.example/v0"


def test_settings_evm_signing_mode_env_override(monkeypatch):
    monkeypatch.delenv("AUREY_EVM_SIGNING_MODE", raising=False)
    monkeypatch.setenv("AUREY_EVM_SIGNING_MODE", "oneclaw_intents")

    s = AureySettings()
    assert s.evm_signing_mode == "oneclaw_intents"
    assert s.evm_signing_requires_wallet_signing_key_secret_path is False


def test_settings_evm_signing_mode_invalid_rejected(monkeypatch):
    monkeypatch.setenv("AUREY_EVM_SIGNING_MODE", "bogus")

    with pytest.raises(ValidationError):
        AureySettings()


def test_settings_ocv_agent_api_key_secret_source_empty_rejected(monkeypatch) -> None:
    monkeypatch.delenv("AUREY_OCV_AGENT_API_KEY_SECRET_SOURCE", raising=False)
    with pytest.raises(ValidationError):
        AureySettings(ocv_agent_api_key_secret_source="   ")


def test_resolve_plt_app_api_key_optional_unset(monkeypatch) -> None:
    monkeypatch.delenv("AUREY_PLT_APP_API_KEY_SECRET_SOURCE", raising=False)
    s = AureySettings()
    assert s.resolve_plt_app_api_key_optional() is None


def test_resolve_plt_app_api_key_optional_reads_named_env(monkeypatch) -> None:
    monkeypatch.setenv("AUREY_PLT_APP_API_KEY_SECRET_SOURCE", "MY_PLT_KEY")
    monkeypatch.setenv("MY_PLT_KEY", "plt-secret")
    s = AureySettings()
    assert s.resolve_plt_app_api_key_optional() == "plt-secret"


def test_settings_database_url_from_database_url_env(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AUREY_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgres://user:pass@db:5432/app")
    s = AureySettings()
    assert s.database_url == "postgres://user:pass@db:5432/app"


def test_settings_database_url_aurey_prefixed(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AUREY_DATABASE_URL", raising=False)
    monkeypatch.setenv("AUREY_DATABASE_URL", "postgres://local/aurey")
    s = AureySettings()
    assert s.database_url == "postgres://local/aurey"


def test_resolve_ocv_agent_api_key(monkeypatch):
    monkeypatch.setenv("CUSTOM_BOOTSTRAP", "bootstrap-value")
    s = AureySettings(ocv_agent_api_key_secret_source="CUSTOM_BOOTSTRAP")
    assert s.resolve_ocv_agent_api_key() == "bootstrap-value"


def test_resolve_ocv_agent_api_key_trim(monkeypatch):
    monkeypatch.setenv("CUSTOM_BOOTSTRAP", "  spaced  ")
    s = AureySettings(ocv_agent_api_key_secret_source="CUSTOM_BOOTSTRAP")
    assert s.resolve_ocv_agent_api_key() == "spaced"


def test_resolve_ocv_agent_api_key_missing(monkeypatch):
    monkeypatch.delenv("MISSING_KEY", raising=False)
    s = AureySettings(ocv_agent_api_key_secret_source="MISSING_KEY")
    with pytest.raises(KeyError):
        s.resolve_ocv_agent_api_key()


def test_resolve_ocv_agent_api_key_empty_env(monkeypatch):
    monkeypatch.setenv("EMPTY_KEY", "")
    s = AureySettings(ocv_agent_api_key_secret_source="EMPTY_KEY")
    with pytest.raises(ValueError):
        s.resolve_ocv_agent_api_key()


def test_parse_telegram_allowed_chat_ids() -> None:
    assert parse_telegram_allowed_chat_ids(None) is None
    assert parse_telegram_allowed_chat_ids("") is None
    assert parse_telegram_allowed_chat_ids("  \t  ") is None
    assert parse_telegram_allowed_chat_ids("1, -100") == frozenset({1, -100})
    assert parse_telegram_allowed_chat_ids("1 -100") == frozenset({1, -100})
    assert parse_telegram_allowed_chat_ids("1,,2") == frozenset({1, 2})
    with pytest.raises(ValueError):
        parse_telegram_allowed_chat_ids("1,notint")


def test_settings_telegram_allowed_chat_ids_construct(monkeypatch) -> None:
    monkeypatch.delenv("AUREY_TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    s = AureySettings(telegram_allowed_chat_ids=" 42 , -99 ")
    assert s.telegram_allowed_chat_ids == "42 , -99"
    assert s.telegram_allowed_chat_id_allowlist == frozenset({42, -99})


def test_settings_telegram_allowed_chat_ids_invalid(monkeypatch) -> None:
    monkeypatch.delenv("AUREY_TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    with pytest.raises(ValidationError):
        AureySettings(telegram_allowed_chat_ids="1,bogus")


def test_cloud_onboarding_configured_requires_database_url(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AUREY_DATABASE_URL", raising=False)
    monkeypatch.setenv("AUREY_PLT_KEY", "plt")
    s = AureySettings(
        plt_app_id="app",
        plt_template_id="tpl",
        plt_app_api_key_secret_source="AUREY_PLT_KEY",
        oidc_issuer="https://issuer.example",
        oidc_rsa_private_key_pem_secret_source="PEMVAR",
    )
    monkeypatch.setenv("PEMVAR", "not-a-valid-pem-for-this-test")
    assert s.cloud_onboarding_configured() is False


def test_resolve_oidc_rsa_private_key_optional(monkeypatch) -> None:
    monkeypatch.setenv("PEMVAR", "  -----BEGIN RSA-----  \n")
    s = AureySettings(oidc_rsa_private_key_pem_secret_source="PEMVAR")
    assert s.resolve_oidc_rsa_private_key_pem_optional() == "-----BEGIN RSA-----"


def test_settings_telegram_allowed_chat_ids_env(monkeypatch) -> None:
    monkeypatch.delenv("AUREY_TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.setenv("AUREY_TELEGRAM_ALLOWED_CHAT_IDS", "7,-8")
    s = AureySettings()
    assert s.telegram_allowed_chat_id_allowlist == frozenset({7, -8})
