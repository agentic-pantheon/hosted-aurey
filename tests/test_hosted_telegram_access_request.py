"""Telegram chat allowlist access-request flow."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from aurey.cloud.hosted_access import (
    require_contact_email,
    submit_telegram_access_request,
    telegram_access_request_flow_step,
)
from aurey.cloud.models import Base
from aurey.settings import AureySettings


@pytest.fixture
def hosted_db_factory():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def test_require_contact_email_rejects_invalid() -> None:
    with pytest.raises(ValueError):
        require_contact_email("not-an-email")


def test_access_flow_intro_then_submit(hosted_db_factory) -> None:
    settings = AureySettings(
        database_url="postgresql://127.0.0.1/test",
        telegram_allowed_chat_ids="1",
        hosted_smtp_host="smtp.test",
        hosted_email_from="noreply@test.dev",
        hosted_operator_registration_notify_email="ops@example.com",
    )
    st = MagicMock()
    st.settings = settings
    st.hosted_session_factory = hosted_db_factory

    user_data: dict[str, object] = {}
    intro = telegram_access_request_flow_step(
        st,
        telegram_user_id=42,
        telegram_username="alice",
        telegram_chat_id=999,
        message_text=None,
        user_data=user_data,
    )
    assert "limited beta" in intro
    assert "@alice" in intro

    with patch("aurey.cloud.hosted_access.send_operator_access_request_email") as send:
        thanks = telegram_access_request_flow_step(
            st,
            telegram_user_id=42,
            telegram_username="alice",
            telegram_chat_id=999,
            message_text="alice@example.com",
            user_data=user_data,
        )
        send.assert_called_once()
        assert "Thanks" in thanks

    pending = telegram_access_request_flow_step(
        st,
        telegram_user_id=42,
        telegram_username="alice",
        telegram_chat_id=999,
        message_text="other@example.com",
        user_data=user_data,
    )
    assert "already on file" in pending


def test_submit_telegram_access_request_persists(hosted_db_factory) -> None:
    settings = AureySettings(
        hosted_smtp_host="smtp.test",
        hosted_email_from="noreply@test.dev",
        hosted_operator_registration_notify_email="ops@example.com",
    )
    session = hosted_db_factory()
    with patch("aurey.cloud.hosted_access.send_operator_access_request_email"):
        row = submit_telegram_access_request(
            session,
            settings,
            telegram_user_id=7,
            telegram_username="bob",
            contact_email="bob@example.com",
            telegram_chat_id=700,
        )
        session.commit()
    assert row.contact_email == "bob@example.com"
    assert row.notified_at is not None
