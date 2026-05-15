"""Onboarding service (upsert + bootstrap + idempotency)."""

from __future__ import annotations

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from aurey.cloud.db import Base
from aurey.cloud.db.models import OnboardingPhase, PlatformUser
from aurey.cloud.oidc import OidcSubjectTokenSigner
from aurey.cloud.onboarding import OnboardingService
from aurey.cloud.platform import OneClawPlatformApiClient
from aurey.settings import AureySettings
from tests.fakes.http_client import ScriptedHttpClient


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

    settings = AureySettings(
        database_url="sqlite:///:memory:",
        plt_app_id="app_9",
        plt_template_id="tpl_9",
        plt_app_api_key_secret_source="AUREY_PLT_KEY",
        oidc_issuer="https://issuer.example",
        oidc_rsa_private_key_pem_secret_source="AUREY_OIDC_PEM",
    )
    pem = settings.resolve_oidc_rsa_private_key_pem_optional() or ""
    oidc = OidcSubjectTokenSigner.from_pem(
        pem,
        issuer="https://issuer.example",
        default_audience="app_9",
    )
    eng = _engine()
    factory = sessionmaker(bind=eng, expire_on_commit=False)
    plt = OneClawPlatformApiClient(
        base_url="https://api.1claw.xyz",
        api_key=settings.resolve_plt_app_api_key_optional() or "",
        http=http,
    )
    svc = OnboardingService(settings=settings, session_factory=factory, platform=plt, oidc=oidc)

    out = svc.run_telegram_start(telegram_user_id=42, display_name="Alice")
    assert out.kind == "claim"
    assert "https://claim.example/x" in out.message
    assert len(http.calls) == 2

    session = factory()
    try:
        row = session.scalars(
            select(PlatformUser).where(PlatformUser.telegram_user_id == 42)
        ).one()
        assert row.connection_id == "conn_1"
        assert row.onboarding_state == OnboardingPhase.AWAITING_CLAIM.value
        assert row.claim_url.startswith("https://claim.example")
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
    settings = AureySettings(
        database_url="sqlite:///:memory:",
        plt_app_id="app_9",
        plt_template_id="tpl_ids",
        plt_app_api_key_secret_source="AUREY_PLT_KEY",
        oidc_issuer="https://issuer.example",
        oidc_rsa_private_key_pem_secret_source="AUREY_OIDC_PEM",
    )
    oidc = OidcSubjectTokenSigner.from_pem(
        settings.resolve_oidc_rsa_private_key_pem_optional() or "",
        issuer="https://issuer.example",
        default_audience="app_9",
    )
    eng = _engine()
    factory = sessionmaker(bind=eng, expire_on_commit=False)
    plt = OneClawPlatformApiClient(
        base_url="https://api.1claw.xyz",
        api_key=settings.resolve_plt_app_api_key_optional() or "",
        http=http,
    )
    svc = OnboardingService(settings=settings, session_factory=factory, platform=plt, oidc=oidc)

    first = svc.run_telegram_start(telegram_user_id=7, display_name=None)
    second = svc.run_telegram_start(telegram_user_id=7, display_name=None)

    assert first.kind == "claim"
    assert second.kind == "claim"
    assert len(http.calls) == 2
