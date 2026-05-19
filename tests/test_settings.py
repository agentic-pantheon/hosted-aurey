"""AureySettings defaults and environment behavior."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aurey.settings import AureySettings, parse_telegram_allowed_chat_ids


def test_settings_defaults():
    s = AureySettings()
    assert s.oneclaw_base_url == "https://api.1claw.xyz"
    assert s.oneclaw_vault_id == ""
    assert s.oneclaw_api_key_secret_source == "AUREY_ONECLAW_BOOTSTRAP_API_KEY"
    assert s.oneclaw_agent_id is None
    assert s.alchemy_api_secret_path is None
    assert s.alchemy_api_key is None
    assert s.lifi_api_secret_path is None
    assert s.lifi_api_key is None
    assert s.lifi_integrator == "aurey"
    assert s.evm_signing_mode == "vault_key"
    assert s.evm_signing_requires_wallet_signing_key_secret_path is True
    assert s.wallet_signing_key_secret_path is None
    assert s.telegram_bot_token_secret_path is None
    assert s.telegram_bot_token is None
    assert s.telegram_allowed_chat_ids is None
    assert s.telegram_allowed_chat_id_allowlist is None
    assert s.deep_agent_default_model == "openai:gpt-4o-mini"
    assert s.database_url is None
    assert s.oneclaw_agent_token_expiry_skew_seconds == 60.0
    assert s.oneclaw_delegated_token_scope == "1claw:intents:delegated"
    assert s.platform_app_id is None
    assert s.platform_api_key is None
    assert s.platform_template_id == ""
    assert s.operator_vault_id == ""
    assert s.operator_agent_id is None
    assert s.operator_agent_api_key_secret_source == "AUREY_OPERATOR_AGENT_API_KEY"
    assert s.hosted_platform_enabled is False
    assert s.hosted_synthetic_email_domain == "hosted-aurey.local"
    assert s.hosted_oidc_issuer_url is None
    assert s.hosted_oidc_audience is None
    assert s.hosted_oidc_subject_token_ttl_seconds == 300
    assert s.hosted_http_admin_token is None


def test_settings_env_override(monkeypatch):
    monkeypatch.delenv("AUREY_ONECLAW_BASE_URL", raising=False)
    monkeypatch.delenv("AUREY_ONECLAW_VAULT_ID", raising=False)
    monkeypatch.delenv("AUREY_ALCHEMY_API_SECRET_PATH", raising=False)
    monkeypatch.delenv("AUREY_EVM_SIGNING_MODE", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AUREY_DATABASE_URL", raising=False)
    monkeypatch.setenv("AUREY_ONECLAW_BASE_URL", "https://example.invalid/v1/")
    monkeypatch.setenv("AUREY_ONECLAW_VAULT_ID", "vault-env-123")
    monkeypatch.setenv("AUREY_ALCHEMY_API_SECRET_PATH", "aurey/apis/alchemy")

    s = AureySettings()
    assert s.oneclaw_base_url == "https://example.invalid/v1/"
    assert s.oneclaw_vault_id == "vault-env-123"
    assert s.alchemy_api_secret_path == "aurey/apis/alchemy"


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


def test_resolve_oneclaw_bootstrap_api_key(monkeypatch):
    monkeypatch.setenv("CUSTOM_BOOTSTRAP", "bootstrap-value")
    s = AureySettings(oneclaw_api_key_secret_source="CUSTOM_BOOTSTRAP")
    assert s.resolve_oneclaw_bootstrap_api_key() == "bootstrap-value"


def test_resolve_oneclaw_bootstrap_api_key_trim(monkeypatch):
    monkeypatch.setenv("CUSTOM_BOOTSTRAP", "  spaced  ")
    s = AureySettings(oneclaw_api_key_secret_source="CUSTOM_BOOTSTRAP")
    assert s.resolve_oneclaw_bootstrap_api_key() == "spaced"


def test_resolve_oneclaw_bootstrap_api_key_missing(monkeypatch):
    monkeypatch.delenv("MISSING_KEY", raising=False)
    s = AureySettings(oneclaw_api_key_secret_source="MISSING_KEY")
    with pytest.raises(KeyError):
        s.resolve_oneclaw_bootstrap_api_key()


def test_resolve_oneclaw_bootstrap_api_key_empty_env(monkeypatch):
    monkeypatch.setenv("EMPTY_KEY", "")
    s = AureySettings(oneclaw_api_key_secret_source="EMPTY_KEY")
    with pytest.raises(ValueError):
        s.resolve_oneclaw_bootstrap_api_key()


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


def test_settings_platform_api_key_from_env(monkeypatch):
    monkeypatch.delenv("AUREY_PLATFORM_API_KEY", raising=False)
    monkeypatch.setenv("AUREY_PLATFORM_API_KEY", "plt_test_fake")
    s = AureySettings()
    assert s.platform_api_key == "plt_test_fake"


def test_settings_plaintext_api_keys_from_env(monkeypatch) -> None:
    for name in (
        "AUREY_ALCHEMY_API_KEY",
        "AUREY_LIFI_API_KEY",
        "AUREY_TELEGRAM_BOT_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AUREY_ALCHEMY_API_KEY", "alchemy-env")
    monkeypatch.setenv("AUREY_LIFI_API_KEY", "lifi-env")
    monkeypatch.setenv("AUREY_TELEGRAM_BOT_TOKEN", "telegram-env")
    s = AureySettings()
    assert s.alchemy_api_key == "alchemy-env"
    assert s.lifi_api_key == "lifi-env"
    assert s.telegram_bot_token == "telegram-env"


def test_settings_plaintext_api_keys_trim_and_empty(monkeypatch) -> None:
    monkeypatch.delenv("AUREY_ALCHEMY_API_KEY", raising=False)
    monkeypatch.setenv("AUREY_ALCHEMY_API_KEY", "  k  ")
    s = AureySettings()
    assert s.alchemy_api_key == "k"
    monkeypatch.setenv("AUREY_ALCHEMY_API_KEY", "   ")
    s2 = AureySettings()
    assert s2.alchemy_api_key is None


def test_resolve_operator_agent_api_key(monkeypatch):
    monkeypatch.setenv("CUSTOM_OP_KEY", "ocv_test_fake")
    s = AureySettings(operator_agent_api_key_secret_source="CUSTOM_OP_KEY")
    assert s.resolve_operator_agent_api_key() == "ocv_test_fake"


def test_settings_hosted_platform_env(monkeypatch):
    monkeypatch.delenv("AUREY_HOSTED_PLATFORM_ENABLED", raising=False)
    monkeypatch.delenv("AUREY_HOSTED_SYNTHETIC_EMAIL_DOMAIN", raising=False)
    monkeypatch.setenv("AUREY_HOSTED_PLATFORM_ENABLED", "true")
    monkeypatch.setenv("AUREY_HOSTED_SYNTHETIC_EMAIL_DOMAIN", " .example.test. ")
    s = AureySettings()
    assert s.hosted_platform_enabled is True
    assert s.hosted_synthetic_email_domain == "example.test"


def test_settings_hosted_synthetic_email_domain_rejects_empty(monkeypatch):
    monkeypatch.delenv("AUREY_HOSTED_SYNTHETIC_EMAIL_DOMAIN", raising=False)
    with pytest.raises(ValidationError):
        AureySettings(hosted_synthetic_email_domain="  . ")


def test_resolve_operator_agent_api_key_missing(monkeypatch):
    monkeypatch.delenv("MISSING_OP", raising=False)
    s = AureySettings(operator_agent_api_key_secret_source="MISSING_OP")
    with pytest.raises(KeyError):
        s.resolve_operator_agent_api_key()


def test_resolve_delegated_actor_falls_back_to_bootstrap(monkeypatch):
    monkeypatch.delenv("AUREY_OPERATOR_AGENT_API_KEY", raising=False)
    monkeypatch.setenv("AUREY_ONECLAW_BOOTSTRAP_API_KEY", "bootstrap-only")
    s = AureySettings()
    assert s.resolve_delegated_actor_api_key() == "bootstrap-only"


def test_resolve_delegated_actor_prefers_operator_when_set(monkeypatch):
    monkeypatch.setenv("AUREY_OPERATOR_AGENT_API_KEY", "ocv-preferred")
    monkeypatch.setenv("AUREY_ONECLAW_BOOTSTRAP_API_KEY", "bootstrap-backup")
    s = AureySettings()
    assert s.resolve_delegated_actor_api_key() == "ocv-preferred"
