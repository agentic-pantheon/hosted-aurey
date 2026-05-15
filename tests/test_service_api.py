"""HTTP surface (FastAPI) with TestClient."""

from __future__ import annotations

import json
import logging

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from aurey.custody import FakeSecretStore
from aurey.graphs import DeterministicTxPipeline
from aurey.reasoning import create_aurey_deep_agent, make_memory_checkpointer
from aurey.runtime import AureyRuntime
from aurey.service.app import InvokeResponse, create_fastapi_application
from aurey.service.bootstrap import AureyServiceBootstrapError
from aurey.service.state import AureyServiceState
from aurey.settings import AureySettings
from tests.fakes.evm_rpc import rpc_factory_from_mapping
from tests.fakes.http_client import ScriptedHttpClient


class _DummyChat(BaseChatModel):
    model_name: str = "stub"

    @property
    def _llm_type(self) -> str:
        return "dummy"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])

    def bind_tools(self, tools, **kwargs):
        return self


def _service_state(monkeypatch) -> AureyServiceState:
    monkeypatch.setattr(
        "aurey.service.state.create_aurey_deep_agent",
        lambda runtime, *, model, checkpointer=None, **kw: create_aurey_deep_agent(
            runtime,
            model=_DummyChat(),
            checkpointer=checkpointer,
            **kw,
        ),
    )
    alchemy_path = "vault/alchemy"
    signing_path = "vault/signing/local"
    secrets = {
        alchemy_path: "SECRET_FRAGMENT_SHOULD_NOT_LEAK",
        signing_path: "0x" + "ff" * 32,
    }
    settings = AureySettings(
        alchemy_api_secret_path=alchemy_path,
        wallet_signing_key_secret_path=signing_path,
    )
    runtime = AureyRuntime(
        settings=settings,
        secret_store=FakeSecretStore(secrets),
        evm_rpc_factory=rpc_factory_from_mapping({}),
        http=ScriptedHttpClient(),
        tx_pipeline=DeterministicTxPipeline(),
    )
    return AureyServiceState(
        settings=settings,
        runtime=runtime,
        checkpointer=make_memory_checkpointer(),
        default_model="stub-spec",
    )


def test_health_endpoint(monkeypatch):
    st = _service_state(monkeypatch)
    with TestClient(create_fastapi_application(state=st)) as client:
        r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_health_reports_not_ready_when_bootstrap_fails(monkeypatch):
    def boom(settings=None):
        raise AureyServiceBootstrapError("vault")

    monkeypatch.setattr("aurey.service.app.bootstrap_aurey_service_state", boom)
    with TestClient(create_fastapi_application()) as client:
        r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": False}


def test_invoke_returns_structured_ok(monkeypatch):
    st = _service_state(monkeypatch)
    with TestClient(create_fastapi_application(state=st)) as client:
        r = client.post(
            "/v1/invoke",
            json={
                "message": "hello",
                "session_id": "sess-1",
                "context": {"wallet_id": "w1"},
            },
        )
    assert r.status_code == 200
    payload = InvokeResponse.model_validate(r.json())
    assert payload.ok is True
    assert payload.session_id == "sess-1"
    assert payload.messages is not None
    assert payload.messages[-1]["content"] == "ok"
    blob = json.dumps(r.json(), sort_keys=True)
    assert "SECRET_FRAGMENT_SHOULD_NOT_LEAK" not in blob


def test_invoke_logs_turn_lines(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    st = _service_state(monkeypatch)
    with TestClient(create_fastapi_application(state=st)) as client:
        r = client.post(
            "/v1/invoke",
            json={
                "message": "hello there",
                "session_id": "sess-logging",
                "context": {"wallet_id": "w1"},
            },
        )
    assert r.status_code == 200
    turn_msgs = [r.getMessage() for r in caplog.records if r.name == "aurey.turn"]
    joined = " ".join(turn_msgs)
    assert "incoming" in joined
    assert "session=sess-logging" in joined
    assert "text=hello there" in joined
    assert "complete" in joined
    assert "preview=ok" in joined


def test_invoke_misconfigured_returns_stable_error(monkeypatch):
    monkeypatch.delenv("AUREY_OCV_AGENT_API_KEY", raising=False)
    s = AureySettings(ocv_vault_id="")
    with TestClient(create_fastapi_application(settings=s)) as client:
        r = client.post(
            "/v1/invoke",
            json={"message": "hello", "session_id": "s1"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "service_misconfigured"
    assert "AUREY" not in json.dumps(body)
    assert "vault" not in body["error"]["message"].lower()


def test_well_known_jwks_not_configured_returns_404(monkeypatch):
    st = _service_state(monkeypatch)
    with TestClient(create_fastapi_application(state=st)) as client:
        r = client.get("/.well-known/jwks.json")
    assert r.status_code == 404


def test_well_known_jwks_served_when_oidc_signer_present(monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    from aurey.cloud.oidc import OidcSubjectTokenSigner

    st = _service_state(monkeypatch)
    pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    st.oidc_signer = OidcSubjectTokenSigner.from_pem(
        pem, issuer="https://issuer.example", default_audience="app_x"
    )
    with TestClient(create_fastapi_application(state=st)) as client:
        r = client.get("/.well-known/jwks.json")
        oidc = client.get("/.well-known/openid-configuration")
    assert r.status_code == 200
    assert "keys" in r.json()
    assert oidc.status_code == 200
    assert oidc.json()["issuer"] == "https://issuer.example"


def test_invoke_agent_invoke_failure_is_generic(monkeypatch):
    st = _service_state(monkeypatch)

    def _boom(*args, **kwargs):
        raise RuntimeError("SECRET_FRAGMENT_SHOULD_NOT_LEAK")

    with TestClient(create_fastapi_application(state=st)) as client:
        monkeypatch.setattr(
            "aurey.service.state.CompiledStateGraph.invoke",
            _boom,
        )
        r = client.post("/v1/invoke", json={"message": "hello", "session_id": "s2"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "agent_invoke_failed"
    assert "SECRET_FRAGMENT" not in json.dumps(body)
