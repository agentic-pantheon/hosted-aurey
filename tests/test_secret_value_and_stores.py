"""SecretValue redaction and SecretStore implementations."""

from __future__ import annotations

import pytest

from aurey.custody import (
    EmptySecretValueError,
    FakeOneClawClient,
    FakeSecretStore,
    OneClawSecretStore,
    SecretNotFoundError,
    SecretStoreUnavailableError,
    SecretValue,
)


def test_secret_value_reveal_and_redaction():
    raw = "super-secret-material"
    sv = SecretValue(path="vault/a", _value=raw)
    assert sv.reveal() == raw
    assert raw not in repr(sv)
    assert raw not in str(sv)
    assert "<redacted>" in repr(sv)


def test_secret_value_rejects_blank_path_or_value():
    with pytest.raises(ValueError):
        SecretValue(path=" ", _value="x")
    with pytest.raises(EmptySecretValueError):
        SecretValue(path="vault/a", _value="")
    with pytest.raises(EmptySecretValueError):
        SecretValue(path="vault/a", _value="   ")


def test_fake_secret_store_hits_and_misses():
    store = FakeSecretStore({"p/one": "alpha", "p/two": "beta"})
    assert store.get_secret("p/one").reveal() == "alpha"
    with pytest.raises(SecretNotFoundError) as exc:
        store.get_secret("missing")
    assert exc.value.path == "missing"


def test_oneclaw_secret_store_delegates_to_client():
    client = FakeOneClawClient({"rpc/ethereum": "https://example.invalid/rpc"})
    store = OneClawSecretStore(
        client=client,
        vault_id="vault-from-settings",
        agent_id="agent-42",
    )
    sv = store.get_secret("rpc/ethereum")
    assert sv.path == "rpc/ethereum"
    assert sv.reveal() == "https://example.invalid/rpc"
    assert client.requests == [
        {"vault_id": "vault-from-settings", "path": "rpc/ethereum", "agent_id": "agent-42"},
    ]


def test_oneclaw_secret_store_wraps_unexpected_exceptions():
    class BrokenClient:
        def get_secret(self, *, vault_id: str, path: str, agent_id: str | None = None) -> str:
            raise RuntimeError("network boom")

    store = OneClawSecretStore(client=BrokenClient(), vault_id="v", agent_id=None)
    with pytest.raises(SecretStoreUnavailableError) as exc:
        store.get_secret("any/path")
    assert exc.value.store_name == "1Claw"
    assert exc.value.path == "any/path"
    assert exc.value.detail is None


def test_oneclaw_secret_store_requires_vault_id():
    with pytest.raises(ValueError):
        OneClawSecretStore(client=FakeOneClawClient({}), vault_id="   ", agent_id=None)


def test_tests_fakes_factory():
    from tests.fakes.secret_store import fake_oneclaw_secret_store

    store = fake_oneclaw_secret_store()
    v = store.get_secret("aurey/rpc/ethereum").reveal()
    assert "example.invalid" in v
