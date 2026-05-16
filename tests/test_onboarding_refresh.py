"""Integration-ish tests: refresh onboarding from a fake Platform GET."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aurey.cloud.models import Base, HostedPlatformUserORM
from aurey.cloud.onboarding_refresh import refresh_hosted_user_claim_state
from aurey.settings import AureySettings


class _FakeConnPlatform:
    def __init__(self, payloads: dict[str, dict]) -> None:
        self.payloads = payloads
        self.get_calls: list[str] = []

    def get_connection(self, connection_id: str) -> dict:
        self.get_calls.append(connection_id)
        return dict(self.payloads[connection_id])


def _memory_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return factory(), engine


def test_refresh_marks_ready_and_persists_fields() -> None:
    settings = AureySettings(
        hosted_platform_enabled=True,
        platform_api_key="plt_test",
        platform_template_id="tmpl_x",
        oneclaw_base_url="https://platform.example",
    )
    fake = _FakeConnPlatform(
        {
            "conn-a": {
                "claimed": True,
                "user_agent_id": "agent-prod",
                "vault_id": "vault-prod",
                "wallet_address": "0x00000000000000000000000000000000000000aa",
            }
        }
    )
    session, engine = _memory_session()
    try:
        row = HostedPlatformUserORM(
            telegram_user_id=1001,
            telegram_username=None,
            connection_id="conn-a",
            claim_url="https://claim/q",
            onboarding_state="awaiting_claim",
        )
        session.add(row)
        session.commit()

        out = refresh_hosted_user_claim_state(session, settings, fake, 1001)
        assert out is not None
        session.commit()

        refreshed = session.get(HostedPlatformUserORM, row.id)
        assert refreshed is not None
        assert refreshed.onboarding_state == "ready"
        assert refreshed.user_agent_id == "agent-prod"
        assert refreshed.vault_id == "vault-prod"
        assert refreshed.wallet_address == "0x00000000000000000000000000000000000000aa"
        assert fake.get_calls == ["conn-a"]
    finally:
        session.close()
        engine.dispose()


def test_refresh_noop_when_already_ready_with_ids() -> None:
    settings = AureySettings(
        hosted_platform_enabled=True,
        platform_api_key="plt_test",
        platform_template_id="tmpl_x",
        oneclaw_base_url="https://platform.example",
    )
    fake = _FakeConnPlatform({"x": {"claimed": True}})
    session, engine = _memory_session()
    try:
        row = HostedPlatformUserORM(
            telegram_user_id=2002,
            telegram_username=None,
            connection_id="conn-skip",
            claim_url="https://claim/q",
            onboarding_state="ready",
            user_agent_id="already",
        )
        session.add(row)
        session.commit()

        out = refresh_hosted_user_claim_state(session, settings, fake, row)
        assert out is not None
        assert out.onboarding_state == "ready"
        assert fake.get_calls == []
    finally:
        session.close()
        engine.dispose()


def test_refresh_skipped_when_hosted_disabled() -> None:
    settings = AureySettings(
        hosted_platform_enabled=False,
        platform_api_key="plt_test",
        platform_template_id="tmpl_x",
    )
    fake = _FakeConnPlatform({"conn-c": {"claimed": True}})
    session, engine = _memory_session()
    try:
        row = HostedPlatformUserORM(
            telegram_user_id=4004,
            telegram_username=None,
            connection_id="conn-c",
            claim_url="https://claim/q",
            onboarding_state="awaiting_claim",
        )
        session.add(row)
        session.commit()

        refresh_hosted_user_claim_state(session, settings, fake, row)
        session.refresh(row)
        assert row.onboarding_state == "awaiting_claim"
        assert fake.get_calls == []
    finally:
        session.close()
        engine.dispose()
