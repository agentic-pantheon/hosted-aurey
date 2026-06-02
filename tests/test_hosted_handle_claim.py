"""Tests for @handle → telegram_user_id claim registry."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from aurey.cloud.hosted_handle_claim import register_handle_claim
from aurey.cloud.hosted_recipient_lookup import lookup_hosted_recipient_by_telegram_handle
from aurey.cloud.hosted_send_invite import build_start_invite_welcome_if_any
from aurey.cloud.models import Base, HostedHandleClaimORM, HostedPlatformUserORM, HostedSendInviteORM
from aurey.runtime import AureyRuntime
from aurey.settings import AureySettings


def _memory_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False), engine


def test_lookup_prefers_claim_over_stale_username_column() -> None:
    factory, engine = _memory_factory()
    try:
        session = factory()
        session.add(
            HostedPlatformUserORM(
                telegram_user_id=10,
                telegram_username="other",
                wallet_address="0x00000000000000000000000000000000000000a1",
            ),
        )
        session.add(
            HostedPlatformUserORM(
                telegram_user_id=20,
                telegram_username="droneztk",
                wallet_address="0x00000000000000000000000000000000000000b2",
            ),
        )
        register_handle_claim(
            session,
            handle_normalized="droneztk",
            telegram_user_id=10,
        )
        session.commit()
        session.close()

        runtime = AureyRuntime(
            settings=AureySettings(hosted_platform_enabled=True, platform_api_key="plt_x"),
            secret_store=object(),  # type: ignore[arg-type]
            evm_rpc_factory=lambda _c: object(),  # type: ignore[arg-type, return-value]
            http=object(),  # type: ignore[arg-type]
            tx_pipeline=object(),  # type: ignore[arg-type]
            hosted_session_factory=factory,
        )
        out = lookup_hosted_recipient_by_telegram_handle(runtime, telegram_handle="@droneztk")
        assert out["ok"] is True
        assert out["result"]["telegram_user_id"] == 10
        assert out["result"]["resolved_via_handle_claim"] is True
        assert out["result"]["ethereum"] == "0x00000000000000000000000000000000000000A1"
    finally:
        engine.dispose()


def test_invite_registers_handle_claim() -> None:
    factory, engine = _memory_factory()
    try:
        session = factory()
        now = datetime.now(tz=UTC)
        session.add(
            HostedSendInviteORM(
                token="tokc",
                sender_telegram_user_id=1,
                target_handle_normalized="droneztk",
                expires_at=now + timedelta(days=1),
            ),
        )
        session.flush()
        build_start_invite_welcome_if_any(
            session,
            AureySettings(),
            start_arg="inv_tokc",
            invitee_username="droneztk",
            invitee_telegram_user_id=42,
        )
        claim = session.scalar(
            select(HostedHandleClaimORM).where(
                HostedHandleClaimORM.handle_normalized == "droneztk",
            ),
        )
        assert claim is not None
        assert claim.telegram_user_id == 42
        session.close()
    finally:
        engine.dispose()
