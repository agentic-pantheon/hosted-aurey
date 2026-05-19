"""POST /v1/hosted/sync-wallet HTTP surface."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from aurey.cloud.models import Base, HostedPlatformUserORM
from aurey.cloud.platform_client import OneClawPlatformClient
from aurey.graphs.evm_codec import to_checksum_evm_address
from aurey.reasoning.checkpointer import make_memory_checkpointer
from aurey.runtime import AureyRuntime
from aurey.service.app import create_fastapi_application
from aurey.service.state import AureyServiceState
from aurey.settings import AureySettings

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    pytest.skip("FastAPI optional for this test graph", allow_module_level=True)

_ADMIN = "admin-test-token-32byteslong!!"
_WRONG_ADMIN = "z" * len(_ADMIN)


def _sqlite_hosted_svc(settings: AureySettings, runtime: AureyRuntime) -> AureyServiceState:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return AureyServiceState(
        settings=settings,
        runtime=runtime,
        checkpointer=make_memory_checkpointer(),
        default_model="openai:gpt-4o-mini",
        hosted_session_factory=factory,
    )


@pytest.fixture(name="sync_wallet_client")
def _sync_wallet_client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AUREY_DATABASE_URL", raising=False)
    monkeypatch.delenv("AUREY_HOSTED_HTTP_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("AUREY_PLATFORM_API_KEY", "plt_test")
    monkeypatch.setenv("AUREY_HOSTED_HTTP_ADMIN_TOKEN", _ADMIN)

    settings = AureySettings(
        hosted_platform_enabled=True,
        platform_api_key="plt_test",
        platform_template_id="tmpl",
    )
    runtime = MagicMock(spec=AureyRuntime)
    runtime.settings = settings
    svc = _sqlite_hosted_svc(settings, runtime)

    db = svc.hosted_session_factory()
    try:
        row = HostedPlatformUserORM(
            telegram_user_id=9001,
            telegram_username=None,
            connection_id="conn-a",
            claim_url="https://claim/x",
            onboarding_state="awaiting_claim",
            user_agent_id="agent-http-1",
        )
        db.add(row)
        db.commit()
    finally:
        db.close()

    app = create_fastapi_application(state=svc)
    with TestClient(app) as client:
        yield client, svc


def test_sync_wallet_401_on_bad_bearer(sync_wallet_client) -> None:
    client, _ = sync_wallet_client
    r = client.post(
        "/v1/hosted/sync-wallet",
        json={"telegram_user_id": 9001},
        headers={"Authorization": f"Bearer {_WRONG_ADMIN}"},
    )
    assert r.status_code == 401


def test_sync_wallet_503_when_admin_token_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUREY_HOSTED_HTTP_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("AUREY_DATABASE_URL", raising=False)

    settings = AureySettings(
        hosted_platform_enabled=True,
        platform_api_key="plt_test",
        platform_template_id="tmpl",
        hosted_http_admin_token=None,
    )
    runtime = MagicMock(spec=AureyRuntime)
    runtime.settings = settings
    svc = _sqlite_hosted_svc(settings, runtime)
    app = create_fastapi_application(state=svc)
    with TestClient(app) as client:
        r = client.post(
            "/v1/hosted/sync-wallet",
            json={"telegram_user_id": 1},
            headers={"Authorization": "Bearer x"},
        )
    assert r.status_code == 503
    assert r.json()["detail"] == "hosted_wallet_sync_disabled"


def test_sync_wallet_updates_row(sync_wallet_client, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _svc = sync_wallet_client
    expected_addr = to_checksum_evm_address("0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee")

    def _stub_keys(self: OneClawPlatformClient, agent_id: str) -> dict:
        assert agent_id == "agent-http-1"
        return {"keys": [{"chain": "ethereum", "address": expected_addr}]}

    monkeypatch.setattr(OneClawPlatformClient, "get_agent_signing_keys", _stub_keys)

    r = client.post(
        "/v1/hosted/sync-wallet",
        json={"telegram_user_id": 9001},
        headers={"Authorization": f"Bearer {_ADMIN}"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["telegram_user_id"] == 9001
    assert data["user_agent_id"] == "agent-http-1"
    assert data["wallet_address"] == expected_addr
