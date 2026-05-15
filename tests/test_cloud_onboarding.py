"""Onboarding service (upsert + bootstrap + idempotency + Phase C claim polling)."""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from aurey.cloud.db import Base
from aurey.cloud.db.models import OnboardingEvent, OnboardingPhase, PlatformUser
from aurey.cloud.oidc import OidcSubjectTokenSigner
from aurey.cloud.onboarding import InvalidOnboardingTransition, OnboardingService
from aurey.cloud.onboarding.claim_parser import parse_claim_ready_signal
from aurey.cloud.onboarding.grant_repository import SqlGrantReferenceRepository
from aurey.cloud.onboarding.state_machine import assert_transition_allowed, coerce_phase_value
from aurey.cloud.platform import OneClawPlatformApiClient
from aurey.settings import AureySettings
from tests.fakes.http_client import ScriptedHttpClient, FailingHttpJsonClient


def _rsa_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def _engine() -> object:
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return eng


def _make_svc(*, http: ScriptedHttpClient, eng: object | None = None) -> OnboardingService:
    settings = AureySettings(
        database_url="sqlite:///:memory:",
        plt_app_id="app_9",
        plt_template_id="tpl_9",
        plt_app_api_key_secret_source="AUREY_PLT_KEY",
        oidc_issuer="https://issuer.example",
        oidc_rsa_private_key_pem_secret_source="AUREY_OIDC_PEM",
    )
    oidc = OidcSubjectTokenSigner.from_pem(
        settings.resolve_oidc_rsa_private_key_pem_optional() or "",
        issuer="https://issuer.example",
        default_audience="app_9",
    )
    factory = sessionmaker(bind=eng or _engine(), expire_on_commit=False)
    plt = OneClawPlatformApiClient(
        base_url="https://api.1claw.xyz",
        api_key=settings.resolve_plt_app_api_key_optional() or "",
        http=http,
    )
    return OnboardingService(
        settings=settings,
        session_factory=factory,
        platform=plt,
        oidc=oidc,
        grant_repository=SqlGrantReferenceRepository(),
    )


def test_onboarding_first_start_calls_upsert_and_bootstrap(monkeypatch) -> None:
    monkeypatch.setenv("AUREY_PLT_KEY", "plt_secret")
    monkeypatch.setenv("AUREY_OIDC_PEM", _rsa_pem())

    http = ScriptedHttpClient(
        [
            (
                lambda method, url, headers, json_body: "users/upsert" in url,
                {"connection_id": "conn_1", "id": "usr_1"},
            ),
            (
                lambda method, url, headers, json_body: "bootstrap" in url,
                {
                    "claim_url": "https://claim.example/x",
                    "vault_id": "vlt_a",
                    "agent_id": "agt_a",
                },
            ),
        ]
    )

    svc = _make_svc(http=http)
    out = svc.run_telegram_start(telegram_user_id=42, display_name="Alice")
    assert out.kind == "claim"
    assert "https://claim.example/x" in out.message
    assert "Wallet setup needed" in out.message
    assert len(http.calls) == 2

    session = svc._session_factory()
    try:
        row = session.scalars(
            select(PlatformUser).where(PlatformUser.telegram_user_id == 42)
        ).one()
        assert row.connection_id == "conn_1"
        assert row.onboarding_state == OnboardingPhase.AWAITING_CLAIM.value
        assert row.claim_url.startswith("https://claim.example")
    finally:
        session.close()


def test_onboarding_bootstrap_nested_summary_populates_agent_and_vault(monkeypatch) -> None:
    monkeypatch.setenv("AUREY_PLT_KEY", "plt_secret")
    monkeypatch.setenv("AUREY_OIDC_PEM", _rsa_pem())

    http = ScriptedHttpClient(
        [
            (
                lambda method, url, headers, json_body: "users/upsert" in url,
                {"connection_id": "conn_nested", "id": "usr_n"},
            ),
            (
                lambda method, url, headers, json_body: "bootstrap" in url,
                {
                    "claim_url": "https://claim.example/nested-path",
                    "summary": {"vault_id": "vlt_nested", "agent_id": "agt_nested"},
                },
            ),
        ]
    )
    svc = _make_svc(http=http)
    svc.run_telegram_start(telegram_user_id=7701, display_name="Nested")

    session = svc._session_factory()
    try:
        row = session.scalars(
            select(PlatformUser).where(PlatformUser.telegram_user_id == 7701)
        ).one()
        assert row.vault_id == "vlt_nested"
        assert row.agent_id == "agt_nested"
    finally:
        session.close()


def test_onboarding_repeat_start_skips_platform_calls(monkeypatch) -> None:
    """Once ``claim_url`` exists, further starts only re-send the link (no HTTP)."""

    monkeypatch.setenv("AUREY_PLT_KEY", "plt_secret")
    monkeypatch.setenv("AUREY_OIDC_PEM", _rsa_pem())

    http = ScriptedHttpClient(
        [
            (
                lambda method, url, headers, json_body: "users/upsert" in url,
                {"connection_id": "conn_x", "id": "usr_x"},
            ),
            (
                lambda method, url, headers, json_body: "bootstrap" in url,
                {"claim_url": "https://c.example/y", "vault_id": "v1", "agent_id": "a1"},
            ),
        ]
    )
    svc = _make_svc(http=http)

    first = svc.run_telegram_start(telegram_user_id=7, display_name=None)
    second = svc.run_telegram_start(telegram_user_id=7, display_name=None)

    assert first.kind == "claim"
    assert second.kind == "claim"
    assert len(http.calls) == 2


def test_state_machine_allows_expected_edges() -> None:
    assert_transition_allowed(
        from_phase=OnboardingPhase.PENDING, to_phase=OnboardingPhase.AWAITING_CLAIM
    )
    assert_transition_allowed(
        from_phase=OnboardingPhase.AWAITING_CLAIM, to_phase=OnboardingPhase.READY
    )
    assert_transition_allowed(from_phase=OnboardingPhase.PENDING, to_phase=OnboardingPhase.PENDING)


def test_state_machine_rejects_skip_and_ready_regression() -> None:
    with pytest.raises(InvalidOnboardingTransition):
        assert_transition_allowed(from_phase=OnboardingPhase.PENDING, to_phase=OnboardingPhase.READY)
    with pytest.raises(InvalidOnboardingTransition):
        assert_transition_allowed(
            from_phase=OnboardingPhase.READY, to_phase=OnboardingPhase.AWAITING_CLAIM
        )


def test_claim_parser_matches_common_shapes() -> None:
    assert parse_claim_ready_signal({"claimed": True}).ready is True
    assert parse_claim_ready_signal({"status": "ready"}).ready is True
    assert parse_claim_ready_signal({"claim": {"completed": True}}).ready is True
    assert parse_claim_ready_signal({"connection_id": "x"}).ready is False


def test_poll_claim_marks_ready_and_sets_grant_placeholder(monkeypatch) -> None:
    monkeypatch.setenv("AUREY_PLT_KEY", "plt_secret")
    monkeypatch.setenv("AUREY_OIDC_PEM", _rsa_pem())

    http = ScriptedHttpClient(
        [
            (
                lambda method, url, headers, json_body: "users/upsert" in url,
                {"connection_id": "conn_p1", "id": "usr_p1"},
            ),
            (
                lambda method, url, headers, json_body: "bootstrap" in url,
                {"claim_url": "https://claim.example/z", "vault_id": "vlt_z", "agent_id": "agt_z"},
            ),
            (
                lambda method, url, headers, json_body: method == "GET"
                and url.endswith("/v1/platform/connections/conn_p1"),
                {"claimed": True},
            ),
        ]
    )
    svc = _make_svc(http=http)
    assert svc.run_telegram_start(telegram_user_id=99, display_name=None).kind == "claim"

    n = svc.poll_awaiting_claims(batch_limit=10)
    assert n == 1
    assert any(
        c["method"] == "GET" and "connections/conn_p1" in c["url"] for c in http.calls
    )

    session = svc._session_factory()
    try:
        row = session.scalars(
            select(PlatformUser).where(PlatformUser.telegram_user_id == 99)
        ).one()
        assert row.onboarding_state == OnboardingPhase.READY.value
        assert row.grant_ref_path is not None
        assert row.grant_ref_path.startswith("vaults/vlt_z/delegated_grants/connections/conn_p1")
        events = session.scalars(
            select(OnboardingEvent).where(OnboardingEvent.platform_user_id == row.id)
        ).all()
        types = {e.event_type for e in events}
        assert "claim_detected" in types
    finally:
        session.close()


def test_poll_claim_noop_when_not_ready(monkeypatch) -> None:
    monkeypatch.setenv("AUREY_PLT_KEY", "plt_secret")
    monkeypatch.setenv("AUREY_OIDC_PEM", _rsa_pem())

    http = ScriptedHttpClient(
        [
            (
                lambda method, url, headers, json_body: "users/upsert" in url,
                {"connection_id": "conn_nr", "id": "u1"},
            ),
            (
                lambda method, url, headers, json_body: "bootstrap" in url,
                {"claim_url": "https://claim.example/nr", "vault_id": "v1", "agent_id": "a1"},
            ),
            (
                lambda method, url, headers, json_body: method == "GET"
                and url.endswith("/v1/platform/connections/conn_nr"),
                {"status": "pending"},
            ),
        ]
    )
    svc = _make_svc(http=http)
    svc.run_telegram_start(telegram_user_id=3, display_name=None)
    assert svc.poll_awaiting_claims() == 0

    session = svc._session_factory()
    try:
        row = session.scalars(
            select(PlatformUser).where(PlatformUser.telegram_user_id == 3)
        ).one()
        assert row.onboarding_state == OnboardingPhase.AWAITING_CLAIM.value
        assert row.grant_ref_path is None
    finally:
        session.close()


def test_poll_claim_http_failure_is_tolerant(monkeypatch) -> None:
    monkeypatch.setenv("AUREY_PLT_KEY", "plt_secret")
    monkeypatch.setenv("AUREY_OIDC_PEM", _rsa_pem())

    bootstrap_http = ScriptedHttpClient(
        [
            (
                lambda method, url, headers, json_body: "users/upsert" in url,
                {"connection_id": "conn_f", "id": "u1"},
            ),
            (
                lambda method, url, headers, json_body: "bootstrap" in url,
                {"claim_url": "https://claim.example/f", "vault_id": "v1", "agent_id": "a1"},
            ),
        ]
    )
    svc = _make_svc(http=bootstrap_http)
    svc.run_telegram_start(telegram_user_id=8, display_name=None)

    svc._platform = OneClawPlatformApiClient(
        base_url="https://api.1claw.xyz",
        api_key="plt",
        http=FailingHttpJsonClient(),
    )
    assert svc.poll_awaiting_claims() == 0

    session = svc._session_factory()
    try:
        row = session.scalars(
            select(PlatformUser).where(PlatformUser.telegram_user_id == 8)
        ).one()
        last = (
            session.scalars(
                select(OnboardingEvent)
                .where(OnboardingEvent.platform_user_id == row.id)
                .order_by(OnboardingEvent.id.desc())
                .limit(1)
            ).first()
        )
        assert last is not None
        assert last.event_type == "claim_poll_http_failed"
    finally:
        session.close()


def test_user_principal_for_ready_user(monkeypatch) -> None:
    monkeypatch.setenv("AUREY_PLT_KEY", "plt_secret")
    monkeypatch.setenv("AUREY_OIDC_PEM", _rsa_pem())

    http = ScriptedHttpClient(
        [
            (
                lambda method, url, headers, json_body: "users/upsert" in url,
                {"connection_id": "conn_princ", "id": "usr_princ"},
            ),
            (
                lambda method, url, headers, json_body: "bootstrap" in url,
                {
                    "claim_url": "https://claim.example/pr",
                    "vault_id": "vlt_pr",
                    "agent_id": "agt_pr",
                },
            ),
            (
                lambda method, url, headers, json_body: method == "GET"
                and url.endswith("/v1/platform/connections/conn_princ"),
                {"claimed": True},
            ),
        ]
    )
    svc = _make_svc(http=http)
    assert svc.run_telegram_start(telegram_user_id=5001, display_name=None).kind == "claim"
    assert svc.poll_awaiting_claims() == 1
    pr = svc.user_principal_for_telegram_user(5001)
    assert pr is not None
    assert pr.user_agent_id == "agt_pr"
    assert pr.grant_ref_path.startswith("vaults/vlt_pr/delegated_grants/connections/conn_princ")


def test_user_principal_none_before_claim(monkeypatch) -> None:
    monkeypatch.setenv("AUREY_PLT_KEY", "plt_secret")
    monkeypatch.setenv("AUREY_OIDC_PEM", _rsa_pem())

    http = ScriptedHttpClient(
        [
            (
                lambda method, url, headers, json_body: "users/upsert" in url,
                {"connection_id": "conn_early", "id": "usr_early"},
            ),
            (
                lambda method, url, headers, json_body: "bootstrap" in url,
                {"claim_url": "https://claim.example/e", "vault_id": "v1", "agent_id": "a1"},
            ),
        ]
    )
    svc = _make_svc(http=http)
    svc.run_telegram_start(telegram_user_id=6002, display_name=None)
    assert svc.user_principal_for_telegram_user(6002) is None


def test_coerce_phase_value_defaults_unknown() -> None:
    assert coerce_phase_value(None) == OnboardingPhase.PENDING
    assert coerce_phase_value("nope") == OnboardingPhase.PENDING
