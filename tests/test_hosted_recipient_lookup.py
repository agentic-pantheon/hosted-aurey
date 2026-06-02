"""Tests for hosted recipient lookup by Telegram handle."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aurey.cloud.hosted_recipient_lookup import lookup_hosted_recipient_by_telegram_handle
from aurey.cloud.models import Base, HostedPlatformUserORM
from aurey.cloud.signing_context import aurey_invoke_context_scope, hosted_telegram_user_id_scope
from aurey.runtime import AureyRuntime
from aurey.settings import AureySettings


def _memory_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False), engine


def test_lookup_recipient_success_case_insensitive() -> None:
    factory, engine = _memory_factory()
    try:
        session = factory()
        session.add(
            HostedPlatformUserORM(
                telegram_user_id=10,
                telegram_username="Alice",
                wallet_address="0x00000000000000000000000000000000000000a1",
            ),
        )
        session.commit()
        session.close()

        settings = AureySettings(
            hosted_platform_enabled=True,
            platform_api_key="plt_x",
            telegram_bot_username="aurey_bot",
        )
        runtime = AureyRuntime(
            settings=settings,
            secret_store=object(),  # type: ignore[arg-type]
            evm_rpc_factory=lambda _c: object(),  # type: ignore[arg-type, return-value]
            http=object(),  # type: ignore[arg-type]
            tx_pipeline=object(),  # type: ignore[arg-type]
            hosted_session_factory=factory,
        )
        with hosted_telegram_user_id_scope(99):
            out = lookup_hosted_recipient_by_telegram_handle(runtime, telegram_handle="@alice")
        assert out["ok"] is True
        assert out["result"]["ethereum"] == "0x00000000000000000000000000000000000000A1"
        assert out["result"]["telegram_user_id"] == 10
    finally:
        engine.dispose()


def test_lookup_recipient_not_found_with_invite() -> None:
    factory, engine = _memory_factory()
    try:
        settings = AureySettings(
            hosted_platform_enabled=True,
            platform_api_key="plt_x",
            telegram_bot_username="aurey_bot",
        )
        runtime = AureyRuntime(
            settings=settings,
            secret_store=object(),  # type: ignore[arg-type]
            evm_rpc_factory=lambda _c: object(),  # type: ignore[arg-type, return-value]
            http=object(),  # type: ignore[arg-type]
            tx_pipeline=object(),  # type: ignore[arg-type]
            hosted_session_factory=factory,
        )
        with hosted_telegram_user_id_scope(99):
            out = lookup_hosted_recipient_by_telegram_handle(runtime, telegram_handle="@nobody")
        assert out["ok"] is False
        assert out["error"]["code"] == "recipient_not_found"
        assert "invite_deeplink" in out
        assert "t.me/aurey_bot" in out["invite_deeplink"]
        assert "invite_deeplink" in out["error"]
    finally:
        engine.dispose()


def test_lookup_recipient_not_found_invite_via_invoke_context() -> None:
    factory, engine = _memory_factory()
    try:
        settings = AureySettings(
            hosted_platform_enabled=True,
            platform_api_key="plt_x",
            telegram_bot_username="aurey_bot",
        )
        runtime = AureyRuntime(
            settings=settings,
            secret_store=object(),  # type: ignore[arg-type]
            evm_rpc_factory=lambda _c: object(),  # type: ignore[arg-type, return-value]
            http=object(),  # type: ignore[arg-type]
            tx_pipeline=object(),  # type: ignore[arg-type]
            hosted_session_factory=factory,
        )
        with aurey_invoke_context_scope({"telegram_user_id": "77"}):
            out = lookup_hosted_recipient_by_telegram_handle(runtime, telegram_handle="@nobody")
        assert out.get("invite_deeplink")
    finally:
        engine.dispose()


def test_lookup_recipient_wallet_unavailable_with_invite() -> None:
    factory, engine = _memory_factory()
    try:
        session = factory()
        session.add(
            HostedPlatformUserORM(
                telegram_user_id=20,
                telegram_username="kevinjonescreates",
                wallet_address=None,
            ),
        )
        session.commit()
        session.close()

        settings = AureySettings(
            hosted_platform_enabled=True,
            platform_api_key="plt_x",
            telegram_bot_username="aureybot",
        )
        runtime = AureyRuntime(
            settings=settings,
            secret_store=object(),  # type: ignore[arg-type]
            evm_rpc_factory=lambda _c: object(),  # type: ignore[arg-type, return-value]
            http=object(),  # type: ignore[arg-type]
            tx_pipeline=object(),  # type: ignore[arg-type]
            hosted_session_factory=factory,
        )
        with hosted_telegram_user_id_scope(99):
            out = lookup_hosted_recipient_by_telegram_handle(
                runtime,
                telegram_handle="@kevinjonescreates",
            )
        assert out["ok"] is False
        assert out["error"]["code"] == "recipient_wallet_unavailable"
        assert "invite_deeplink" in out
        assert "t.me/aureybot" in out["invite_deeplink"]
        assert "inv_" in out["invite_deeplink"] or out["invite_deeplink"].rstrip("/").endswith(
            "aureybot"
        )
    finally:
        engine.dispose()


def test_lookup_recipient_ambiguous() -> None:
    factory, engine = _memory_factory()
    try:
        session = factory()
        session.add(
            HostedPlatformUserORM(
                telegram_user_id=1,
                telegram_username="dup",
                wallet_address="0x0000000000000000000000000000000000000001",
            ),
        )
        session.add(
            HostedPlatformUserORM(
                telegram_user_id=2,
                telegram_username="Dup",
                wallet_address="0x0000000000000000000000000000000000000002",
            ),
        )
        session.commit()
        session.close()

        settings = AureySettings(hosted_platform_enabled=True, platform_api_key="plt_x")
        runtime = AureyRuntime(
            settings=settings,
            secret_store=object(),  # type: ignore[arg-type]
            evm_rpc_factory=lambda _c: object(),  # type: ignore[arg-type, return-value]
            http=object(),  # type: ignore[arg-type]
            tx_pipeline=object(),  # type: ignore[arg-type]
            hosted_session_factory=factory,
        )
        out = lookup_hosted_recipient_by_telegram_handle(runtime, telegram_handle="@dup")
        assert out["ok"] is False
        assert out["error"]["code"] == "recipient_ambiguous"
    finally:
        engine.dispose()
