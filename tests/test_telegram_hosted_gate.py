"""Hosted claim gating on Telegram message path."""

from __future__ import annotations

from unittest.mock import MagicMock

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from aurey.cloud.models import Base, HostedPlatformUserORM
from aurey.telegram.client import _hosted_user_must_finish_claim_message


def test_hosted_user_must_finish_claim_returns_blocker_when_awaiting(monkeypatch) -> None:
    monkeypatch.setattr(
        "aurey.cloud.onboarding_refresh.refresh_hosted_user_claim_state",
        lambda db, cfg, platform, telegram_user_id: db.scalar(
            select(HostedPlatformUserORM).where(
                HostedPlatformUserORM.telegram_user_id == telegram_user_id,
            ),
        ),
    )

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with factory() as s:
        s.add(
            HostedPlatformUserORM(
                telegram_user_id=99,
                telegram_username=None,
                connection_id="conn-1",
                claim_url="https://claim.test/example",
                onboarding_state="awaiting_claim",
            ),
        )
        s.commit()

    st = MagicMock()
    st.settings.hosted_platform_enabled = True
    st.settings.database_url = "postgresql://127.0.0.1/dummy"
    st.settings.hosted_require_verified_email = False
    st.hosted_session_factory = factory

    msg = _hosted_user_must_finish_claim_message(st, telegram_user_id=99)
    assert msg == (
        "Finish hosted setup first: open the claim link from /start, then message me again."
    )


def test_hosted_user_allows_agent_after_email_verified_awaiting_claim(monkeypatch) -> None:
    from datetime import UTC, datetime

    monkeypatch.setattr(
        "aurey.cloud.onboarding_refresh.refresh_hosted_user_claim_state",
        lambda db, cfg, platform, telegram_user_id: db.scalar(
            select(HostedPlatformUserORM).where(
                HostedPlatformUserORM.telegram_user_id == telegram_user_id,
            ),
        ),
    )

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with factory() as s:
        s.add(
            HostedPlatformUserORM(
                telegram_user_id=77,
                telegram_username=None,
                connection_id="conn-1",
                claim_url="https://claim.test/example",
                onboarding_state="awaiting_claim",
                email="user@example.com",
                email_verified_at=datetime.now(tz=UTC),
            ),
        )
        s.commit()

    st = MagicMock()
    st.settings.hosted_platform_enabled = True
    st.settings.database_url = "postgresql://127.0.0.1/dummy"
    st.settings.hosted_require_verified_email = True
    st.hosted_session_factory = factory

    msg = _hosted_user_must_finish_claim_message(st, telegram_user_id=77)
    assert msg is None
