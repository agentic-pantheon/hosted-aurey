"""Hosted provisioning against an in-memory SQLite schema."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aurey.cloud.hosted_credentials import HostedSecretsCipher, agent_api_key_secret_path
from aurey.cloud.models import Base, HostedPlatformUserORM
from aurey.cloud.platform_client import HostedPlatformApiError, PlatformBootstrapResult, PlatformReissueClaimResult, PlatformUpsertResult
from aurey.cloud.provision import HostedProvisioningError, ensure_telegram_user_provisioned
from aurey.custody.secret_store import FakeOneClawClient
from aurey.graphs.evm_codec import to_checksum_evm_address
from aurey.settings import AureySettings


class _FakePlatform:
    """Deterministic Platform client for tests."""

    def __init__(self) -> None:
        self.upserts = 0
        self.bootstraps = 0
        self.signing_keys_calls: list[str] = []

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
            agent_api_key="ocv_fake_provision_key",
            wallet_address=to_checksum_evm_address(
                "0xdddddddddddddddddddddddddddddddddddddddd",
            ),
        )

    def get_connection(self, connection_id: str) -> dict:
        _ = connection_id
        return {"claimed": False}

    def list_app_users(self, app_id: str):
        _ = app_id
        return {}

    def reissue_claim(self, connection_id: str, *, return_to: str | None = None):
        _ = return_to
        return PlatformReissueClaimResult(
            claim_url=f"https://claim.test/reissued/{connection_id}",
            connection_id=connection_id,
            expires_in=600,
        )

    def get_agent_signing_keys(self, agent_id: str) -> dict:
        self.signing_keys_calls.append(agent_id)
        return {"keys": []}


def _memory_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return factory(), engine


def test_ensure_telegram_user_provisioned_rebootstraps_while_awaiting_claim() -> None:
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
        assert row.wallet_address == to_checksum_evm_address(
            "0xdddddddddddddddddddddddddddddddddddddddd",
        )
        assert row.agent_api_key == "ocv_fake_provision_key"
        assert fake.signing_keys_calls == []

        row2, refreshed2 = ensure_telegram_user_provisioned(
            session,
            settings,
            fake,
            telegram_user_id=424242,
            username="tester",
        )
        session.commit()
        assert refreshed2 is True
        assert row2.id == row.id
        assert fake.upserts == 1
        assert fake.bootstraps == 1
        assert row2.claim_url == "https://claim.test/reissued/conn-determined"
    finally:
        session.close()
        engine.dispose()


def test_awaiting_claim_polls_ready_before_bootstrap() -> None:
    settings = AureySettings(
        hosted_platform_enabled=True,
        platform_api_key="plt_fake",
        platform_template_id="tmpl_1",
    )
    session, engine = _memory_session()
    try:
        row = HostedPlatformUserORM(
            telegram_user_id=555,
            telegram_username="u",
            connection_id="conn-claimed",
            claim_url="https://claim.test/old",
            onboarding_state="awaiting_claim",
            user_agent_id="agent-old",
        )
        session.add(row)
        session.commit()

        class _ClaimedPlatform:
            bootstraps = 0

            def upsert_user_synthetic_email(self, *, email: str, display_name: str | None):
                _ = email, display_name
                return PlatformUpsertResult(connection_id="conn-claimed")

            def bootstrap(self, connection_id: str, template_id: str) -> PlatformBootstrapResult:
                _ = connection_id, template_id
                _ClaimedPlatform.bootstraps += 1
                raise AssertionError("bootstrap should not run when poll marks ready")

            def list_app_users(self, app_id: str):
                _ = app_id
                return {}

            def get_connection(self, connection_id: str):
                return {"claimed": True, "agent_id": "agent-live", "vault_id": "vault-live"}

            def get_agent_signing_keys(self, agent_id: str):
                _ = agent_id
                return {"keys": []}

        fake = _ClaimedPlatform()
        out, refreshed = ensure_telegram_user_provisioned(
            session,
            settings,
            fake,
            telegram_user_id=555,
            username="u",
        )
        session.commit()
        assert refreshed is True
        assert out.onboarding_state == "ready"
        assert out.user_agent_id == "agent-live"
        assert _ClaimedPlatform.bootstraps == 0
    finally:
        session.close()
        engine.dispose()


def test_bootstrap_500_skips_upsert_when_connection_already_provisioned() -> None:
    settings = AureySettings(
        hosted_platform_enabled=True,
        platform_api_key="plt_fake",
        platform_template_id="tmpl_1",
        platform_app_id="app-1",
    )
    session, engine = _memory_session()
    try:
        row = HostedPlatformUserORM(
            telegram_user_id=557,
            telegram_username=None,
            connection_id="conn-stale",
            claim_url="https://claim.test/stale",
            onboarding_state="awaiting_claim",
        )
        session.add(row)
        session.commit()

        class _ActivePlatform:
            def __init__(self) -> None:
                self.bootstraps = 0
                self.upserts = 0
                self.reissues = 0

            def upsert_user_synthetic_email(self, *, email: str, display_name: str | None):
                _ = email, display_name
                self.upserts += 1
                raise AssertionError("upsert must not run when agent already provisioned")

            def bootstrap(self, connection_id: str, template_id: str) -> PlatformBootstrapResult:
                _ = connection_id, template_id
                self.bootstraps += 1
                raise HostedPlatformApiError("Platform HTTP 500", status_code=500)

            def reissue_claim(self, connection_id: str, *, return_to: str | None = None):
                _ = return_to
                self.reissues += 1
                return PlatformReissueClaimResult(
                    claim_url="https://claim.test/fresh",
                    connection_id=connection_id,
                    expires_in=600,
                )

            def list_app_users(self, app_id: str):
                _ = app_id
                return {
                    "data": [
                        {
                            "connection_id": "conn-stale",
                            "status": "active",
                            "agent_ids": ["agent-live"],
                            "vault_ids": ["vault-live"],
                        }
                    ]
                }

            def get_connection(self, connection_id: str):
                _ = connection_id
                return {"claimed": False, "claim_url": "https://claim.test/stale"}

            def get_agent_signing_keys(self, agent_id: str):
                _ = agent_id
                return {"keys": []}

        fake = _ActivePlatform()
        out, refreshed = ensure_telegram_user_provisioned(
            session,
            settings,
            fake,
            telegram_user_id=557,
            username="u",
        )
        session.commit()
        assert refreshed is True
        assert out.user_agent_id == "agent-live"
        assert out.claim_url == "https://claim.test/fresh"
        assert fake.bootstraps == 0
        assert fake.upserts == 0
        assert fake.reissues == 1
    finally:
        session.close()
        engine.dispose()


def test_bootstrap_500_recovers_via_new_connection_from_upsert() -> None:
    settings = AureySettings(
        hosted_platform_enabled=True,
        platform_api_key="plt_fake",
        platform_template_id="tmpl_1",
    )
    session, engine = _memory_session()
    try:
        row = HostedPlatformUserORM(
            telegram_user_id=556,
            telegram_username=None,
            connection_id="conn-stale",
            claim_url="https://claim.test/expired",
            onboarding_state="awaiting_claim",
        )
        session.add(row)
        session.commit()

        class _RetryPlatform:
            def __init__(self) -> None:
                self.bootstraps = 0
                self.upserts = 0

            def upsert_user_synthetic_email(self, *, email: str, display_name: str | None):
                _ = email, display_name
                self.upserts += 1
                return PlatformUpsertResult(connection_id="conn-fresh")

            def bootstrap(self, connection_id: str, template_id: str) -> PlatformBootstrapResult:
                _ = template_id
                self.bootstraps += 1
                if connection_id == "conn-stale":
                    raise HostedPlatformApiError("Platform HTTP 500", status_code=500)
                return PlatformBootstrapResult(
                    claim_url=f"https://claim.test/new/{connection_id}",
                    vault_id="vault-new",
                    user_agent_id="agent-new",
                )

            def list_app_users(self, app_id: str):
                _ = app_id
                return {}

            def get_connection(self, connection_id: str):
                _ = connection_id
                return {"claimed": False}

            def get_agent_signing_keys(self, agent_id: str):
                _ = agent_id
                return {"keys": []}

        fake = _RetryPlatform()
        out, refreshed = ensure_telegram_user_provisioned(
            session,
            settings,
            fake,
            telegram_user_id=556,
            username="u",
        )
        session.commit()
        assert refreshed is True
        assert out.connection_id == "conn-fresh"
        assert "conn-fresh" in out.claim_url
        assert fake.bootstraps == 2
        assert fake.upserts == 1
    finally:
        session.close()
        engine.dispose()


def test_ensure_telegram_user_provisioned_skips_network_when_ready() -> None:
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
        row.onboarding_state = "ready"
        session.commit()

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


def test_provision_dual_writes_ocv_when_vault_client_configured() -> None:
    from cryptography.fernet import Fernet

    raw_key = Fernet.generate_key().decode("ascii")
    settings = AureySettings(
        hosted_platform_enabled=True,
        platform_api_key="plt_fake",
        platform_template_id="tmpl_1",
        oneclaw_vault_id="vault-op",
        hosted_secrets_master_key=raw_key,
        oneclaw_human_api_token="human-test-bearer",
    )
    session, engine = _memory_session()
    try:
        fake = _FakePlatform()
        vault_fake = FakeOneClawClient()
        row, refreshed = ensure_telegram_user_provisioned(
            session,
            settings,
            fake,
            telegram_user_id=424242,
            username="tester",
            vault_http_client=vault_fake,
        )
        session.commit()
        assert refreshed is True
        assert row.agent_api_key is None
        cipher = HostedSecretsCipher.from_settings_optional(settings)
        assert cipher is not None
        assert cipher.decrypt(row.agent_api_key_encrypted or "") == "ocv_fake_provision_key"
        path = agent_api_key_secret_path("hosted/agents", "agent-x")
        assert vault_fake.put_human_calls == [
            {
                "vault_id": "vault-op",
                "path": path,
                "value": "ocv_fake_provision_key",
                "bearer_token": "human-test-bearer",
            },
        ]
    finally:
        session.close()
        engine.dispose()
