"""Tests for notifying invite senders when recipient wallet is ready."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from aurey.cloud.hosted_invite_sender_notify import (
    maybe_notify_invite_senders_recipient_wallet_ready,
)
from aurey.cloud.models import Base, HostedPlatformUserORM, HostedSendInviteORM
from aurey.settings import AureySettings


def _memory_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False), engine


def test_marks_invites_notified_and_schedules_once_per_sender(monkeypatch) -> None:
    factory, engine = _memory_factory()
    scheduled: list[tuple[int, str, str]] = []

    def _fake_schedule(**kwargs: object) -> None:
        scheduled.append(
            (
                int(kwargs["sender_telegram_user_id"]),  # type: ignore[arg-type]
                str(kwargs["recipient_display"]),
                str(kwargs["target_handle"]),
            ),
        )

    monkeypatch.setattr(
        "aurey.telegram.notifications.schedule_invite_sender_recipient_ready_notify",
        _fake_schedule,
    )

    try:
        session = factory()
        now = datetime.now(tz=UTC)
        session.add(
            HostedPlatformUserORM(
                telegram_user_id=200,
                telegram_username="kevinjonescreates",
                wallet_address="0x00000000000000000000000000000000000000b2",
                onboarding_state="ready",
            ),
        )
        session.add(
            HostedSendInviteORM(
                token="tok_a",
                sender_telegram_user_id=99,
                target_handle_normalized="kevinjonescreates",
                expires_at=now + timedelta(days=7),
            ),
        )
        session.add(
            HostedSendInviteORM(
                token="tok_b",
                sender_telegram_user_id=99,
                target_handle_normalized="kevinjonescreates",
                expires_at=now + timedelta(days=7),
            ),
        )
        session.flush()
        recipient = session.scalar(
            select(HostedPlatformUserORM).where(
                HostedPlatformUserORM.telegram_user_id == 200,
            ),
        )
        assert recipient is not None

        maybe_notify_invite_senders_recipient_wallet_ready(
            session,
            AureySettings(),
            recipient,
        )
        session.commit()

        assert len(scheduled) == 1
        assert scheduled[0][0] == 99
        rows = list(session.scalars(select(HostedSendInviteORM)).all())
        assert all(r.sender_notified_at is not None for r in rows)
        session.close()
    finally:
        engine.dispose()
