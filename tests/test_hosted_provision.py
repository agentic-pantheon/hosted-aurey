"""Hosted provisioning against an in-memory SQLite schema."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aurey.cloud.models import Base, HostedPlatformUserORM
from aurey.cloud.platform_client import PlatformBootstrapResult, PlatformUpsertResult
from aurey.cloud.provision import HostedProvisioningError, ensure_telegram_user_provisioned
from aurey.settings import AureySettings


class _FakePlatform:
    """Deterministic Platform client for tests."""

    def __init__(self) -> None:
        self.upserts = 0
        self.bootstraps = 0

    def upsert_user_synthetic_email(
        self,
        *,
        email: str,
        display_name: str | None,
    ) -> PlatformUpsertResult:
        _ = email, display_name
        self.upserts += 1
        return PlatformUpsertResult(connection_id="conn-determined")

    def bootstrap(self, connection_id: str, template_id: str) -> PlatformBootstrapResult:
        _ = template_id
        self.bootstraps += 1
        return PlatformBootstrapResult(
            claim_url=f"https://claim.test/host/{connection_id}",
            vault_id="vault-x",
            user_agent_id="agent-x",
        )


def _memory_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return factory(), engine


def test_ensure_telegram_user_provisioned_creates_and_skips_network_on_cache() -> None:
    settings = AureySettings(
        hosted_platform_enabled=True,
        platform_api_key="plt_fake",
        platform_template_id="tmpl_1",
    )
    session, engine = _memory_session()
    try:
        fake = _FakePlatform()
        row, refreshed = ensure_telegram_user_provisioned(
            session,
            settings,
            fake,
            telegram_user_id=424242,
            username="tester",
        )
        session.commit()
        assert refreshed is True
        assert row.connection_id == "conn-determined"
        assert row.claim_url.endswith("/conn-determined")
        assert fake.upserts == 1
        assert fake.bootstraps == 1

        row2, refreshed2 = ensure_telegram_user_provisioned(
            session,
            settings,
            fake,
            telegram_user_id=424242,
            username="tester",
        )
        session.commit()
        assert refreshed2 is False
        assert row2.id == row.id
        assert fake.upserts == 1
        assert fake.bootstraps == 1
    finally:
        session.close()
        engine.dispose()


def test_ensure_telegram_user_provisioned_disabled_raises() -> None:
    settings = AureySettings(
        hosted_platform_enabled=False,
        platform_api_key="plt_fake",
        platform_template_id="tmpl_1",
    )
    session, engine = _memory_session()
    try:
        with pytest.raises(HostedProvisioningError):
            ensure_telegram_user_provisioned(
                session,
                settings,
                None,
                telegram_user_id=1,
                username=None,
            )
    finally:
        session.close()
        engine.dispose()


def test_synthetic_email_normalizes_domain() -> None:
    from aurey.cloud.provision import synthetic_email_for_telegram_user

    e = synthetic_email_for_telegram_user(
        telegram_user_id=7,
        hosted_synthetic_email_domain=" .my.test. ",
    )
    assert e == "tg_7@my.test"


def test_partial_row_bootstraps_only() -> None:
    settings = AureySettings(
        hosted_platform_enabled=True,
        platform_api_key="plt_fake",
        platform_template_id="tmpl_1",
    )
    session, engine = _memory_session()
    try:
        partial = HostedPlatformUserORM(
            telegram_user_id=99,
            telegram_username=None,
            connection_id="existing-conn",
            claim_url="",
            onboarding_state="awaiting_claim",
        )
        session.add(partial)
        session.commit()

        fake = _FakePlatform()
        row, refreshed = ensure_telegram_user_provisioned(
            session,
            settings,
            fake,
            telegram_user_id=99,
            username="u",
        )
        session.commit()
        assert refreshed is True
        assert row.connection_id == "existing-conn"
        assert "existing-conn" in row.claim_url
        assert fake.upserts == 0
        assert fake.bootstraps == 1
    finally:
        session.close()
        engine.dispose()
