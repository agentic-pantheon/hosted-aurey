"""1Claw unified EVM signing on ``OneClawHttpClient`` and fake client."""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from aurey.custody import (
    FakeOneClawClient,
    FakeSecretStore,
    OneClawHttpClient,
    OneClawSigningError,
    OneClawSignTransactionResult,
    SecretStoreUnavailableError,
)


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


def _make_urlopen_mock(
    actions: list[bytes | HTTPError],
    captured: list[Request],
) -> Callable[..., _FakeResponse]:
    """Each HTTP call consumes the next action (bytes -> response body, HTTPError -> raised)."""

    idx = {"i": 0}

    def _open(request: Request, timeout: float | None = None) -> _FakeResponse:
        captured.append(request)
        i = idx["i"]
        idx["i"] += 1
        if i >= len(actions):
            raise AssertionError("Unexpected extra HTTP call")
        act = actions[i]
        if isinstance(act, HTTPError):
            raise act
        return _FakeResponse(act)

    return _open


_MIN_WEB3_EIP1559_TX = {
    "to": "0x1",
    "data": "0x",
    "value": 0,
    "nonce": 0,
    "gas": 21_000,
    "maxFeePerGas": 30_000_000_000,
    "maxPriorityFeePerGas": 2_000_000_000,
}


def test_fake_oneclaw_client_sign_evm_transaction_records_and_returns_default():
    client = FakeOneClawClient({"x": "y"})
    tx = {"to": "0xabc", "value": "0x0"}
    result = client.sign_evm_transaction(agent_id="a1", chain="base", transaction=tx)
    assert isinstance(result, OneClawSignTransactionResult)
    assert result.signed_tx == "0xfake_signed_tx"
    assert result.tx_hash == "0xfake_tx_hash"
    assert result.from_address == "0xfake_from"
    assert result.tx_type == "2"
    assert client.sign_requests == [{"agent_id": "a1", "chain": "base", "transaction": tx}]


def test_fake_oneclaw_client_sign_respects_sign_response_and_exception():
    custom = OneClawSignTransactionResult(signed_tx="0xcust", tx_hash=None)
    client = FakeOneClawClient(sign_response=custom)
    assert client.sign_evm_transaction(agent_id="a", chain="eth", transaction={}) == custom

    client2 = FakeOneClawClient(sign_exception=RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="boom"):
        client2.sign_evm_transaction(agent_id="a", chain="eth", transaction={})


def test_oneclaw_http_sign_evm_transaction_success():
    captured: list[Request] = []
    actions = [
        json.dumps({"access_token": "jwt-one"}).encode(),
        json.dumps(
            {
                "signed_tx": " 0xdead ",
                "tx_hash": "0xhash",
                "from": " 0xfrom ",
                "tx_type": " eip1559 ",
            }
        ).encode(),
    ]
    with patch(
        "aurey.custody.secret_store.urlopen",
        side_effect=_make_urlopen_mock(actions, captured),
    ):
        client = OneClawHttpClient(base_url="https://claw.test", api_key="k")
        out = client.sign_evm_transaction(
            agent_id="my-agent",
            chain="ethereum",
            signing_key_path="wallets/hot-wallet",
            transaction={
                "to": "0x1",
                "data": "0x",
                "value": 0,
                "nonce": 0,
                "gas": 21_000,
                "maxFeePerGas": 30_000_000_000,
                "maxPriorityFeePerGas": 2_000_000_000,
            },
        )

    assert out == OneClawSignTransactionResult(
        signed_tx="0xdead",
        tx_hash="0xhash",
        from_address="0xfrom",
        tx_type="eip1559",
    )

    assert len(captured) == 2
    token_req, sign_req = captured
    assert token_req.get_full_url() == "https://claw.test/v1/auth/agent-token"
    assert json.loads(token_req.data.decode()) == {"agent_id": "my-agent", "api_key": "k"}

    assert sign_req.get_full_url() == "https://claw.test/v1/agents/my-agent/sign"
    auth = next(v for h, v in sign_req.header_items() if h.lower() == "authorization")
    assert auth == "Bearer jwt-one"
    assert json.loads(sign_req.data.decode()) == {
        "intent_type": "transaction",
        "chain": "ethereum",
        "to": "0x1",
        "data": "0x",
        "value": "0",
        "nonce": 0,
        "gas_limit": 21000,
        "tx_type": 2,
        "max_fee_per_gas": "30000000000",
        "max_priority_fee_per_gas": "2000000000",
        "signing_key_path": "wallets/hot-wallet",
    }


def test_oneclaw_http_sign_retries_once_on_401():
    captured: list[Request] = []
    actions = [
        json.dumps({"access_token": "t1"}).encode(),
        HTTPError(
            "https://claw.test/v1/agents/a/sign",
            401,
            "Unauthorized",
            None,
            io.BytesIO(b""),
        ),
        json.dumps({"access_token": "t2"}).encode(),
        json.dumps({"signed_tx": "0xok"}).encode(),
    ]
    with patch(
        "aurey.custody.secret_store.urlopen",
        side_effect=_make_urlopen_mock(actions, captured),
    ):
        client = OneClawHttpClient(base_url="https://claw.test", api_key="k")
        out = client.sign_evm_transaction(
            agent_id="a",
            chain="base",
            transaction={
                "to": "0x2",
                "data": "0x",
                "value": 0,
                "nonce": 1,
                "gas": 21_000,
                "maxFeePerGas": 30_000_000_000,
                "maxPriorityFeePerGas": 2_000_000_000,
            },
        )

    assert out.signed_tx == "0xok"
    assert len(captured) == 4


def test_oneclaw_http_sign_validates_agent_and_chain():
    client = OneClawHttpClient(base_url="https://claw.test", api_key="k")
    with pytest.raises(ValueError, match="non-empty"):
        client.sign_evm_transaction(agent_id=" ", chain="base", transaction={})
    with pytest.raises(ValueError, match="non-empty"):
        client.sign_evm_transaction(agent_id="a", chain="  ", transaction={})


def test_oneclaw_http_sign_maps_non_401_http_to_unavailable():
    captured: list[Request] = []
    actions = [
        json.dumps({"access_token": "jwt"}).encode(),
        HTTPError(
            "https://claw.test/v1/agents/a/sign",
            503,
            "Slow",
            None,
            io.BytesIO(b""),
        ),
    ]
    with patch(
        "aurey.custody.secret_store.urlopen",
        side_effect=_make_urlopen_mock(actions, captured),
    ):
        client = OneClawHttpClient(base_url="https://claw.test", api_key="k")
        tx = dict(_MIN_WEB3_EIP1559_TX)
        with pytest.raises(SecretStoreUnavailableError) as exc:
            client.sign_evm_transaction(agent_id="a", chain="eth", transaction=tx)
        assert "503" in str(exc.value)


def test_oneclaw_http_sign_maps_bad_json_to_unavailable():
    captured: list[Request] = []
    actions = [
        json.dumps({"access_token": "jwt"}).encode(),
        b"not-json",
    ]
    with patch(
        "aurey.custody.secret_store.urlopen",
        side_effect=_make_urlopen_mock(actions, captured),
    ):
        client = OneClawHttpClient(base_url="https://claw.test", api_key="k")
        tx = dict(_MIN_WEB3_EIP1559_TX)
        with pytest.raises(SecretStoreUnavailableError):
            client.sign_evm_transaction(agent_id="a", chain="eth", transaction=tx)


def test_oneclaw_http_sign_maps_missing_signed_tx_to_signing_error():
    captured: list[Request] = []
    actions = [
        json.dumps({"access_token": "jwt"}).encode(),
        json.dumps({"tx_hash": "0x1"}).encode(),
    ]
    with patch(
        "aurey.custody.secret_store.urlopen",
        side_effect=_make_urlopen_mock(actions, captured),
    ):
        client = OneClawHttpClient(base_url="https://claw.test", api_key="k")
        with pytest.raises(OneClawSigningError):
            client.sign_evm_transaction(agent_id="a", chain="eth", transaction={})


def test_oneclaw_http_get_secret_reuses_token_until_expires_in_window():
    captured: list[Request] = []
    actions = [
        json.dumps({"access_token": "jwt-a", "expires_in": 3600}).encode(),
        json.dumps({"value": "one"}).encode(),
        json.dumps({"value": "two"}).encode(),
    ]
    mono = MagicMock(side_effect=[1000.0, 1001.0])
    with (
        patch(
            "aurey.custody.secret_store.urlopen",
            side_effect=_make_urlopen_mock(actions, captured),
        ),
        patch("aurey.custody.secret_store.time.monotonic", mono),
    ):
        client = OneClawHttpClient(
            base_url="https://claw.test",
            api_key="k",
            agent_token_expiry_skew_seconds=60.0,
        )
        assert client.get_secret(vault_id="v1", path="a/b", agent_id="agent-1") == "one"
        assert client.get_secret(vault_id="v1", path="a/c", agent_id="agent-1") == "two"

    assert len(captured) == 3
    assert json.loads(captured[0].data.decode()) == {"agent_id": "agent-1", "api_key": "k"}
    auths = [
        next(v for h, v in r.header_items() if h.lower() == "authorization") for r in captured[1:]
    ]
    assert auths == ["Bearer jwt-a", "Bearer jwt-a"]
    assert mono.call_count == 2


def test_oneclaw_http_get_secret_refetches_token_after_expires_in_window():
    captured: list[Request] = []
    actions = [
        json.dumps({"access_token": "jwt-old", "expires_in": 100}).encode(),
        json.dumps({"value": "one"}).encode(),
        json.dumps({"access_token": "jwt-new", "expires_in": 100}).encode(),
        json.dumps({"value": "two"}).encode(),
    ]
    skew = 60.0
    mono = MagicMock(side_effect=[1000.0, 2000.0, 2000.0])
    with (
        patch(
            "aurey.custody.secret_store.urlopen",
            side_effect=_make_urlopen_mock(actions, captured),
        ),
        patch("aurey.custody.secret_store.time.monotonic", mono),
    ):
        client = OneClawHttpClient(
            base_url="https://claw.test",
            api_key="k",
            agent_token_expiry_skew_seconds=skew,
        )
        assert client.get_secret(vault_id="v1", path="a/b", agent_id="agent-1") == "one"
        assert client.get_secret(vault_id="v1", path="a/c", agent_id="agent-1") == "two"

    assert len(captured) == 4
    auth1 = next(v for h, v in captured[1].header_items() if h.lower() == "authorization")
    auth3 = next(v for h, v in captured[3].header_items() if h.lower() == "authorization")
    assert auth1 == "Bearer jwt-old"
    assert auth3 == "Bearer jwt-new"
    assert mono.call_count == 3


def test_oneclaw_http_delegated_sign_reuses_cached_token(monkeypatch):
    captured: list[Request] = []
    actions = [
        json.dumps({"access_token": "jwt-del", "expires_in": 3600}).encode(),
        json.dumps({"signed_tx": "0xsig1"}).encode(),
        json.dumps({"signed_tx": "0xsig2"}).encode(),
    ]
    mono = MagicMock(side_effect=[1000.0, 1001.0, 1002.0])
    with (
        patch(
            "aurey.custody.secret_store.urlopen",
            side_effect=_make_urlopen_mock(actions, captured),
        ),
        patch("aurey.custody.secret_store.time.monotonic", mono),
    ):
        client = OneClawHttpClient(
            base_url="https://claw.test",
            api_key="ocv_actor",
            agent_token_expiry_skew_seconds=60.0,
        )
        tx = dict(_MIN_WEB3_EIP1559_TX)
        r1 = client.sign_evm_transaction(
            agent_id="ag_u",
            chain="base",
            transaction=tx,
            delegated_subject_token="grantSUBJECT",
            delegated_scope="intents:sign",
        )
        r2 = client.sign_evm_transaction(
            agent_id="ag_u",
            chain="base",
            transaction=tx,
            delegated_subject_token="grantSUBJECT",
            delegated_scope="intents:sign",
        )
    assert r1.signed_tx == "0xsig1"
    assert r2.signed_tx == "0xsig2"
    assert len(captured) == 3
    body0 = json.loads(captured[0].data.decode())
    assert body0["scope"] == "intents:sign"
    assert body0["actor_token"] == "ocv_actor"
    assert body0["subject_token"] == "grantSUBJECT"
    delegated_posts = [
        r for r in captured if r.method == "POST" and "/delegated-token" in r.full_url
    ]
    assert len(delegated_posts) == 1


def test_principal_backed_signer_loads_grant_and_calls_http(monkeypatch):
    from aurey.custody.delegated_signer import PrincipalBackedOneClawSigner
    from aurey.principal import UserPrincipal

    captured: list[Request] = []
    actions = [
        json.dumps({"access_token": "jwt-del", "expires_in": 3600}).encode(),
        json.dumps({"signed_tx": "0xsigned"}).encode(),
    ]
    with patch(
        "aurey.custody.secret_store.urlopen",
        side_effect=_make_urlopen_mock(actions, captured),
    ):
        http = OneClawHttpClient(base_url="https://claw.test", api_key="actor_k")
        store = FakeSecretStore({"vault/grants/u1": "grant-body"})
        principal = UserPrincipal(
            db_user_id="11111111-1111-1111-1111-111111111111",
            user_agent_id="ag_hosted",
            grant_ref_path="vault/grants/u1",
        )
        signer = PrincipalBackedOneClawSigner(
            http=http,
            secret_store=store,
            principal=principal,
            delegated_scope="intents:sign",
        )
        tx = dict(_MIN_WEB3_EIP1559_TX)
        result = signer.sign_evm_transaction(agent_id="ag_hosted", chain="base", transaction=tx)
    assert result.signed_tx == "0xsigned"
    assert json.loads(captured[0].data.decode())["subject_token"] == "grant-body"


