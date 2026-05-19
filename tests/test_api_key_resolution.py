"""Tests for effective Alchemy/LiFi API key resolution (env over vault)."""

from __future__ import annotations

from aurey.custody import FakeSecretStore
from aurey.graphs.api_key_resolution import effective_alchemy_api_key, effective_lifi_api_key
from aurey.settings import AureySettings


def test_effective_alchemy_prefers_env_over_vault() -> None:
    settings = AureySettings(
        alchemy_api_key="env-alchemy",
        alchemy_api_secret_path="vault/alchemy",
    )
    store = FakeSecretStore({"vault/alchemy": "vault-alchemy"})
    key, err = effective_alchemy_api_key(settings, store)
    assert err is None
    assert key == "env-alchemy"


def test_effective_alchemy_falls_back_to_vault() -> None:
    settings = AureySettings(alchemy_api_secret_path="vault/alchemy")
    store = FakeSecretStore({"vault/alchemy": "vault-alchemy"})
    key, err = effective_alchemy_api_key(settings, store)
    assert err is None
    assert key == "vault-alchemy"


def test_effective_alchemy_not_configured() -> None:
    settings = AureySettings()
    key, err = effective_alchemy_api_key(
        settings, FakeSecretStore({}), extra_secret_not_configured_details={"chain": "base"}
    )
    assert key is None
    assert err is not None
    assert err["code"] == "secret_not_configured"
    assert err.get("details", {}).get("chain") == "base"


def test_effective_lifi_env_without_path() -> None:
    settings = AureySettings(lifi_api_key="env-lifi", lifi_api_secret_path=None)
    key, err = effective_lifi_api_key(settings, FakeSecretStore({}))
    assert err is None
    assert key == "env-lifi"


def test_effective_lifi_no_key_when_unconfigured() -> None:
    settings = AureySettings()
    key, err = effective_lifi_api_key(settings, FakeSecretStore({}))
    assert err is None
    assert key is None


def test_effective_lifi_path_requires_store() -> None:
    settings = AureySettings(lifi_api_secret_path="vault/lifi")
    key, err = effective_lifi_api_key(settings, FakeSecretStore({}))
    assert key is None
    assert err is not None
    assert err["code"] == "secret_not_found"
