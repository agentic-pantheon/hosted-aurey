"""FastAPI Mini App portfolio routes."""

from __future__ import annotations

import time

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from aurey.miniapp.schemas import (
    PortfolioSummary,
    PortfolioSummaryByChain,
    PortfolioSnapshot,
    utc_now_iso,
)
from aurey.miniapp.wallet import ResolvedMiniappUser
from aurey.service.app import create_fastapi_application
from tests.test_service_api import _service_state
from tests.test_telegram_webapp_auth import _signed_init_data


def test_miniapp_config_disabled(monkeypatch):
    st = _service_state(monkeypatch)
    st.settings.telegram_miniapp_enabled = False
    with TestClient(create_fastapi_application(state=st)) as client:
        r = client.get("/v1/miniapp/config")
    assert r.status_code == 503


def test_miniapp_config_ok(monkeypatch):
    st = _service_state(monkeypatch)
    st.settings.telegram_miniapp_enabled = True
    with TestClient(create_fastapi_application(state=st)) as client:
        r = client.get("/v1/miniapp/config")
    assert r.status_code == 200
    assert r.json()["chains"]


def test_miniapp_portfolio_requires_valid_init(monkeypatch):
    st = _service_state(monkeypatch)
    st.settings.telegram_miniapp_enabled = True
    st.settings.hosted_platform_enabled = True
    st.settings.telegram_bot_token = "999:FAKE"
    with TestClient(create_fastapi_application(state=st)) as client:
        r = client.post("/v1/miniapp/portfolio", json={"init_data": "bad"})
    assert r.status_code == 401


def test_miniapp_portfolio_ok(monkeypatch):
    from aurey.miniapp.portfolio_cache_server import portfolio_snapshot_cache

    portfolio_snapshot_cache.clear()
    st = _service_state(monkeypatch)
    st.settings.telegram_miniapp_enabled = True
    st.settings.hosted_platform_enabled = True
    st.settings.telegram_bot_token = "999:FAKE"
    st.settings.telegram_allowed_chat_ids = None
    st.settings.telegram_miniapp_initdata_max_age_seconds = 3600

    snap = PortfolioSnapshot(
        wallet_address="0x1111111111111111111111111111111111111111",
        updated_at=utc_now_iso(),
        chains_queried=("base",),
        chains_available=("base",),
        summary=PortfolioSummary(total_usd=None, by_chain=[]),
        tokens=[],
        defi=[],
        errors=[],
    )

    def fake_aggregate(_runtime, *, wallet_address, chains, chart_period="month"):
        return snap

    monkeypatch.setattr("aurey.miniapp.portfolio.aggregate_portfolio_snapshot", fake_aggregate)

    def fake_resolve(_svc, *, telegram_user_id: int):
        return ResolvedMiniappUser(
            has_row=True,
            onboarding_state="awaiting_claim",
            wallet_address=snap.wallet_address,
        )

    monkeypatch.setattr("aurey.miniapp.wallet.resolve_wallet_for_telegram_user", fake_resolve)

    init = _signed_init_data(
        bot_token=st.settings.telegram_bot_token,
        user_id=42,
        auth_date=int(time.time()) - 30,
    )
    with TestClient(create_fastapi_application(state=st)) as client:
        r = client.post("/v1/miniapp/portfolio", json={"init_data": init})

    assert r.status_code == 200
    assert r.json()["wallet_address"].lower().startswith("0x111")


def test_miniapp_portfolio_allowlist_blocks(monkeypatch):
    st = _service_state(monkeypatch)
    st.settings.telegram_miniapp_enabled = True
    st.settings.hosted_platform_enabled = True
    st.settings.telegram_bot_token = "999:FAKE"
    st.settings.telegram_allowed_chat_ids = "1, 2, 3"
    monkeypatch.setattr(
        "aurey.miniapp.portfolio.aggregate_portfolio_snapshot",
        lambda **kwargs: pytest.fail("should not aggregate when blocked"),
    )
    monkeypatch.setattr(
        "aurey.miniapp.wallet.resolve_wallet_for_telegram_user",
        lambda *_a, **_k: pytest.fail("should not resolve wallet when blocked"),
    )

    init = _signed_init_data(
        bot_token=st.settings.telegram_bot_token,
        user_id=99,
        auth_date=int(time.time()) - 5,
    )
    with TestClient(create_fastapi_application(state=st)) as client:
        r = client.post("/v1/miniapp/portfolio", json={"init_data": init})

    assert r.status_code == 403
    detail = r.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("code") == "telegram_user_not_allowlisted"
