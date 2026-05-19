"""1Claw unified EVM signing on ``OneClawHttpClient`` and fake client."""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from aurey.cloud.signing_context import HostedSigningContext, hosted_signing_context_scope
from aurey.custody import (
    FakeOneClawClient,
    FakeSecretStore,
    IntentsSignTransactionRequest,
    OneClawHttpClient,
    OneClawSigningError,
    OneClawSignTransactionResult,
    SecretStoreUnavailableError,
)
from aurey.custody.secret_store import _oneclaw_unified_value_eth_string
from aurey.graphs import DeterministicTxPipeline
from aurey.runtime import AureyRuntime
from aurey.settings import AureySettings
from aurey.tools.agent_tools import build_aurey_subgraph_tools
from tests.fakes.evm_rpc import rpc_factory_from_mapping
from tests.fakes.http_client import ScriptedHttpClient


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


def test_oneclaw_unified_value_eth_string_converts_wei_to_decimal_eth():
    assert _oneclaw_unified_value_eth_string(0) == "0"
    assert _oneclaw_unified_value_eth_string(1_000_000_000) == "0.000000001"
    assert _oneclaw_unified_value_eth_string(10_000_000_000_000) == "0.00001"
    assert _oneclaw_unified_value_eth_string(10**18) == "1"


def test_oneclaw_http_sign_sends_value_as_decimal_eth_not_wei():
    captured: list[Request] = []
    actions = [
        json.dumps({"access_token": "jwt-one"}).encode(),
        json.dumps({"signed_tx": "0xdead"}).encode(),
    ]
    with patch(
        "aurey.custody.secret_store.urlopen",
        side_effect=_make_urlopen_mock(actions, captured),
    ):
        client = OneClawHttpClient(base_url="https://claw.test", api_key="k")
        client.sign_evm_transaction(
            agent_id="my-agent",
            chain="base",
            transaction={
                "to": "0x1",
                "data": "0x",
                "value": 1_000_000,
                "nonce": 0,
                "gas": 21_000,
                "maxFeePerGas": 30_000_000_000,
                "maxPriorityFeePerGas": 2_000_000_000,
            },
        )
    body = json.loads(captured[1].data.decode())
    assert body["value"] == "0.000000000001"


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


def test_oneclaw_http_sign_skips_agent_token_when_authorization_bearer_set():
    captured: list[Request] = []
    actions = [
        json.dumps(
            {
                "signed_tx": "0xdead",
                "tx_hash": "0xhash",
                "from": "0xfrom",
                "tx_type": "2",
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
            transaction=dict(_MIN_WEB3_EIP1559_TX),
            authorization_bearer="preissued-jwt",
        )

    assert out.signed_tx == "0xdead"
    assert len(captured) == 1
    assert captured[0].get_full_url() == "https://claw.test/v1/agents/my-agent/sign"
    auth = next(v for h, v in captured[0].header_items() if h.lower() == "authorization")
    assert auth == "Bearer preissued-jwt"


def test_oneclaw_http_post_delegated_token_caches_per_subject_fingerprint_and_agent():
    captured: list[Request] = []
    subject = "subject-secret-token"
    actions = [
        json.dumps({"access_token": "jwt-d1", "expires_in": 3600}).encode(),
        json.dumps({"access_token": "jwt-d2", "expires_in": 3600}).encode(),
    ]
    mono = MagicMock(return_value=1000.0)
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
        t1 = client.post_delegated_access_token(
            actor_token="act",
            subject_token=subject,
            scope="scopes/x",
            agent_id="ag1",
        )
        t2 = client.post_delegated_access_token(
            actor_token="act",
            subject_token=subject,
            scope="scopes/x",
            agent_id="ag1",
        )
        t3 = client.post_delegated_access_token(
            actor_token="act",
            subject_token=subject,
            scope="scopes/x",
            agent_id="ag2",
        )

    assert t1 == t2 == "jwt-d1"
    assert t3 == "jwt-d2"
    assert len(captured) == 2
    assert all(r.get_full_url() == "https://claw.test/v1/auth/delegated-token" for r in captured)
    body0 = json.loads(captured[0].data.decode())
    assert body0 == {
        "subject_token": subject,
        "actor_token": "act",
        "scope": "scopes/x",
    }


def test_fake_oneclaw_client_personal_typed_intents_cover_protocol():
    c = FakeOneClawClient()
    p = c.sign_personal_message(agent_id="ag", chain="eth", message="hi")
    assert p.signature.startswith("0x")
    t = c.sign_typed_data(agent_id="ag", chain="eth", typed_data={"a": 1})
    assert t.signature.startswith("0x")
    i = c.intents_sign_transaction(
        agent_id="ag",
        request=IntentsSignTransactionRequest(chain="ethereum", to="0x1", value="0.01"),
    )
    assert i.signed_tx.startswith("0x")
    assert len(c.personal_sign_calls) == len(c.typed_data_calls) == len(c.intents_sign_calls) == 1


def test_oneclaw_http_agent_token_uses_hosted_ocv_in_signing_context():
    captured: list[Request] = []
    actions = [
        json.dumps({"access_token": "jwt-ocv", "expires_in": 3600}).encode(),
        json.dumps({"signature": "0xsig"}).encode(),
    ]
    with patch(
        "aurey.custody.secret_store.urlopen",
        side_effect=_make_urlopen_mock(actions, captured),
    ):
        client = OneClawHttpClient(
            base_url="https://claw.test",
            api_key="bootstrap_fallback_key",
        )
        ctx = HostedSigningContext(
            telegram_user_id=1,
            user_agent_id="user-agent-uuid",
            agent_api_key_legacy_plaintext="ocv_from_bootstrap_body",
        )
        with hosted_signing_context_scope(ctx):
            client.sign_personal_message(
                agent_id="user-agent-uuid",
                chain="eth",
                message="hi",
            )

    assert captured[0].get_full_url() == "https://claw.test/v1/auth/agent-token"
    body0 = json.loads(captured[0].data.decode())
    assert body0 == {
        "agent_id": "user-agent-uuid",
        "api_key": "ocv_from_bootstrap_body",
    }


def test_oneclaw_http_agent_token_ignores_ocv_when_hosted_agent_id_mismatches():
    """Hosted context is only applied when ``user_agent_id`` matches the signing call."""

    captured: list[Request] = []
    actions = [
        json.dumps({"access_token": "jwt", "expires_in": 3600}).encode(),
        json.dumps({"signature": "0xs"}).encode(),
    ]
    with patch(
        "aurey.custody.secret_store.urlopen",
        side_effect=_make_urlopen_mock(actions, captured),
    ):
        client = OneClawHttpClient(base_url="https://claw.test", api_key="bootstrap_only")
        ctx = HostedSigningContext(
            telegram_user_id=1,
            user_agent_id="different-agent",
            agent_api_key_legacy_plaintext="ocv_should_not_apply",
        )
        with hosted_signing_context_scope(ctx):
            client.sign_personal_message(agent_id="signing-agent-id", chain="eth", message="x")

    assert json.loads(captured[0].data.decode())["api_key"] == "bootstrap_only"


def test_oneclaw_http_sign_personal_message_passes_explicit_hex_message():
    """Explicit 0x + even hex is forwarded (e.g. pre-encoded challenge bytes)."""

    captured: list[Request] = []
    actions = [
        json.dumps({"access_token": "jwt-one"}).encode(),
        json.dumps({"signature": "0xsig"}).encode(),
    ]
    with patch(
        "aurey.custody.secret_store.urlopen",
        side_effect=_make_urlopen_mock(actions, captured),
    ):
        client = OneClawHttpClient(base_url="https://claw.test", api_key="k")
        client.sign_personal_message(
            agent_id="my-agent",
            chain="base",
            message="0xAbCd",
        )
    body = json.loads(captured[1].data.decode())
    assert body["message"] == "0xabcd"


def test_oneclaw_http_sign_personal_message_success_and_body():
    captured: list[Request] = []
    actions = [
        json.dumps({"access_token": "jwt-one"}).encode(),
        json.dumps({"signature": " 0xsig ", "from": " 0xsigner "}).encode(),
    ]
    with patch(
        "aurey.custody.secret_store.urlopen",
        side_effect=_make_urlopen_mock(actions, captured),
    ):
        client = OneClawHttpClient(base_url="https://claw.test", api_key="k")
        out = client.sign_personal_message(
            agent_id="my-agent",
            chain="base",
            message="hello",
            signing_key_path="wallets/x",
        )
    assert out.signature == "0xsig"
    assert out.signer_address == "0xsigner"
    assert captured[1].get_full_url() == "https://claw.test/v1/agents/my-agent/sign"
    assert json.loads(captured[1].data.decode()) == {
        "intent_type": "personal_sign",
        "chain": "base",
        "message": "0x68656c6c6f",
        "signing_key_path": "wallets/x",
    }


def test_oneclaw_http_sign_typed_data_success_and_body():
    captured: list[Request] = []
    td = {"primaryType": "Foo", "domain": {}, "types": {}, "message": {}}
    actions = [
        json.dumps({"access_token": "jwt-one"}).encode(),
        json.dumps({"signature": "0xabcd"}).encode(),
    ]
    with patch(
        "aurey.custody.secret_store.urlopen",
        side_effect=_make_urlopen_mock(actions, captured),
    ):
        client = OneClawHttpClient(base_url="https://claw.test", api_key="k")
        out = client.sign_typed_data(agent_id="a1", chain="ethereum", typed_data=td)
    assert out.signature == "0xabcd"
    payload = json.loads(captured[1].data.decode())
    assert payload["intent_type"] == "typed_data"
    assert payload["typed_data"] == td


def test_oneclaw_http_intents_sign_only_hits_transactions_sign_decimal_value():
    captured: list[Request] = []
    actions = [
        json.dumps({"access_token": "jwt-one"}).encode(),
        json.dumps(
            {"signed_tx": "0xfc", "status": "sign_only", "tx_hash": "0xh", "from": "0xf"}
        ).encode(),
    ]
    req = IntentsSignTransactionRequest(
        chain="base",
        to="0xtarget",
        value="0.25",
        data="0x",
        nonce=12,
        max_fee_per_gas="999",
        max_priority_fee_per_gas="888",
    )
    with patch(
        "aurey.custody.secret_store.urlopen",
        side_effect=_make_urlopen_mock(actions, captured),
    ):
        client = OneClawHttpClient(base_url="https://claw.test", api_key="k")
        out = client.intents_sign_transaction(agent_id="agent-x", request=req)
    assert out.signed_tx == "0xfc"
    assert out.status == "sign_only"
    assert captured[1].get_full_url() == (
        "https://claw.test/v1/agents/agent-x/transactions/sign"
    )
    body = json.loads(captured[1].data.decode())
    assert body == {
        "chain": "base",
        "to": "0xtarget",
        "value": "0.25",
        "data": "0x",
        "nonce": 12,
        "max_fee_per_gas": "999",
        "max_priority_fee_per_gas": "888",
    }


def test_oneclaw_http_personal_sign_skips_agent_token_when_authorization_bearer_set():
    captured: list[Request] = []
    actions = [json.dumps({"signature": "0xps"}).encode()]
    with patch(
        "aurey.custody.secret_store.urlopen",
        side_effect=_make_urlopen_mock(actions, captured),
    ):
        client = OneClawHttpClient(base_url="https://claw.test", api_key="k")
        out = client.sign_personal_message(
            agent_id="x",
            chain="eth",
            message="m",
            authorization_bearer="preissued-jwt",
        )
    assert out.signature == "0xps"
    assert len(captured) == 1
    assert json.loads(captured[0].data.decode())["message"] == "0x6d"
    auth = next(v for h, v in captured[0].header_items() if h.lower() == "authorization")
    assert auth == "Bearer preissued-jwt"


def test_build_aurey_subgraph_tools_includes_oneclaw_sign_tools_when_oneclaw_intents_mode():
    runtime = AureyRuntime(
        settings=AureySettings(
            evm_signing_mode="oneclaw_intents",
            alchemy_api_secret_path="p/alchemy",
            oneclaw_agent_id="stub-agent-id",
            oneclaw_vault_id="v",
        ),
        secret_store=FakeSecretStore({"p/alchemy": "x"}),
        evm_rpc_factory=rpc_factory_from_mapping({}),
        http=ScriptedHttpClient(),
        tx_pipeline=DeterministicTxPipeline(),
        lifi_base_url="https://li.quest",
        oneclaw_evm_signer=FakeOneClawClient(),
    )
    tools = build_aurey_subgraph_tools(runtime)
    names = {t.name for t in tools}
    assert "oneclaw_sign_personal_message" in names
    assert "oneclaw_sign_typed_data" in names
    assert "oneclaw_intents_sign_transaction" in names
