"""Tests for send-to-invite helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from aurey.cloud.hosted_send_invite import (
    build_invite_deeplink,
    build_start_invite_welcome_if_any,
    consume_send_invite,
    load_invite_by_start_payload,
    try_create_invite_for_not_found,
)
from aurey.cloud.models import Base, HostedPlatformUserORM, HostedSendInviteORM
from aurey.settings import AureySettings


def _memory_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False), engine


def test_build_invite_deeplink() -> None:
    settings = AureySettings(telegram_bot_username="MyBot")
    link = build_invite_deeplink(settings, "abc123")
    assert link == "https://t.me/MyBot?start=inv_abc123"


def test_create_and_load_invite() -> None:
    factory, engine = _memory_factory()
    try:
        session = factory()
        settings = AureySettings(telegram_bot_username="bot", hosted_send_invite_ttl_days=7)
        extras = try_create_invite_for_not_found(
            session,
            settings,
            sender_telegram_user_id=42,
            target_handle_normalized="bob",
        )
        assert extras.invite_deeplink is not None
        assert extras.invite_token is not None
        row = load_invite_by_start_payload(
            session,
            start_arg=f"inv_{extras.invite_token}",
        )
        assert row is not None
        assert row.target_handle_normalized == "bob"
        session.close()
    finally:
        engine.dispose()


def test_create_invite_reuses_existing_unconsumed() -> None:
    factory, engine = _memory_factory()
    try:
        session = factory()
        settings = AureySettings(telegram_bot_username="bot", hosted_send_invite_ttl_days=7)
        first = try_create_invite_for_not_found(
            session,
            settings,
            sender_telegram_user_id=42,
            target_handle_normalized="bob",
        )
        second = try_create_invite_for_not_found(
            session,
            settings,
            sender_telegram_user_id=42,
            target_handle_normalized="bob",
        )
        assert first.invite_token == second.invite_token
        count = session.scalar(
            select(func.count())
            .select_from(HostedSendInviteORM)
            .where(HostedSendInviteORM.sender_telegram_user_id == 42),
        )
        assert count == 1

        # A different sender or target still gets its own invite row.
        other = try_create_invite_for_not_found(
            session,
            settings,
            sender_telegram_user_id=43,
            target_handle_normalized="bob",
        )
        assert other.invite_token != first.invite_token
        session.close()
    finally:
        engine.dispose()


def test_create_invite_does_not_reuse_consumed() -> None:
    factory, engine = _memory_factory()
    try:
        session = factory()
        now = datetime.now(tz=UTC)
        session.add(
            HostedSendInviteORM(
                token="consumed_tok",
                sender_telegram_user_id=7,
                target_handle_normalized="carol",
                expires_at=now + timedelta(days=1),
                consumed_at=now,
            ),
        )
        session.flush()
        settings = AureySettings(telegram_bot_username="bot")
        fresh = try_create_invite_for_not_found(
            session,
            settings,
            sender_telegram_user_id=7,
            target_handle_normalized="carol",
        )
        assert fresh.invite_token is not None
        assert fresh.invite_token != "consumed_tok"
        session.close()
    finally:
        engine.dispose()


def test_start_invite_mismatch_does_not_consume() -> None:
    factory, engine = _memory_factory()
    try:
        session = factory()
        now = datetime.now(tz=UTC)
        session.add(
            HostedSendInviteORM(
                token="tok2",
                sender_telegram_user_id=1,
                target_handle_normalized="droneztk",
                expires_at=now + timedelta(days=1),
            ),
        )
        session.flush()
        html = build_start_invite_welcome_if_any(
            session,
            AureySettings(),
            start_arg="inv_tok2",
            invitee_username="other_user",
            invitee_telegram_user_id=555,
        )
        assert html is not None
        assert "only for Telegram account" in html
        assert "droneztk" in html
        row = session.scalar(select(HostedSendInviteORM).where(HostedSendInviteORM.token == "tok2"))
        assert row is not None
        assert row.consumed_at is None
        session.close()
    finally:
        engine.dispose()


def test_start_invite_welcome_and_consume() -> None:
    factory, engine = _memory_factory()
    try:
        session = factory()
        now = datetime.now(tz=UTC)
        session.add(
            HostedPlatformUserORM(
                telegram_user_id=99,
                telegram_username="sender",
            ),
        )
        session.add(
            HostedSendInviteORM(
                token="tok1",
                sender_telegram_user_id=99,
                target_handle_normalized="bob",
                expires_at=now + timedelta(days=1),
            ),
        )
        session.flush()
        settings = AureySettings()
        html = build_start_invite_welcome_if_any(
            session,
            settings,
            start_arg="inv_tok1",
            invitee_username="bob",
            invitee_telegram_user_id=100,
        )
        assert html is not None
        assert "@sender" in html
        row = session.scalar(select(HostedSendInviteORM).where(HostedSendInviteORM.token == "tok1"))
        assert row is not None
        assert row.consumed_at is not None
        assert consume_send_invite(session, row) is True
        session.close()
    finally:
        engine.dispose()
