"""Integration-ish tests: refresh onboarding from a fake Platform GET."""

from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aurey.cloud.models import Base, HostedPlatformUserORM
from aurey.cloud.onboarding_refresh import refresh_hosted_user_claim_state
from aurey.settings import AureySettings


class _FakeConnPlatform:
    def __init__(
        self,
        payloads: dict[str, dict],
        *,
        app_list_payload: Any | None = None,
        signing_keys_by_agent: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.payloads = payloads
        self.app_list_payload = app_list_payload
        self.signing_keys_by_agent = signing_keys_by_agent or {}
        self.get_calls: list[str] = []
        self.list_calls: list[str] = []
        self.signing_keys_calls: list[str] = []

    def get_connection(self, connection_id: str) -> dict:
        self.get_calls.append(connection_id)
        return dict(self.payloads[connection_id])

    def list_app_users(self, app_id: str) -> Any:
        self.list_calls.append(app_id)
        if self.app_list_payload is not None:
            return self.app_list_payload
        return {"data": []}

    def get_agent_signing_keys(self, agent_id: str) -> dict[str, Any]:
        self.signing_keys_calls.append(agent_id)
        payload = self.signing_keys_by_agent.get(agent_id)
        if payload is not None:
            return dict(payload)
        return {"keys": []}


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
        assert fake.list_calls == []
    finally:
        session.close()
        engine.dispose()


def test_refresh_updates_claim_url_from_connection_get() -> None:
    settings = AureySettings(
        hosted_platform_enabled=True,
        platform_api_key="plt_test",
        platform_template_id="tmpl_x",
    )
    fake = _FakeConnPlatform(
        {
            "conn-c": {
                "claimed": False,
                "claim_url": "https://claim.test/renewed",
            },
        },
    )
    session, engine = _memory_session()
    try:
        row = HostedPlatformUserORM(
            telegram_user_id=1002,
            telegram_username=None,
            connection_id="conn-c",
            claim_url="https://claim.test/expired",
            onboarding_state="awaiting_claim",
        )
        session.add(row)
        session.commit()

        refresh_hosted_user_claim_state(session, settings, fake, 1002)
        session.refresh(row)
        assert row.claim_url == "https://claim.test/renewed"
        assert row.onboarding_state == "awaiting_claim"
    finally:
        session.close()
        engine.dispose()


def test_refresh_merges_app_users_list_with_connection_get() -> None:
    app = "ed17c6ee-baff-4fa7-8018-267c22ea95a7"
    settings = AureySettings(
        hosted_platform_enabled=True,
        platform_api_key="plt_test",
        platform_template_id="tmpl_x",
        platform_app_id=app,
    )
    fake = _FakeConnPlatform(
        {
            "conn-a": {
                "claimed": False,
                "claim_url": "https://claim.test/from-get",
            },
        },
        app_list_payload={
            "data": [
                {
                    "connection_id": "conn-a",
                    "status": "active",
                    "agent_ids": ["agent-from-list"],
                    "vault_ids": ["vault-from-list"],
                }
            ]
        },
    )
    session, engine = _memory_session()
    try:
        row = HostedPlatformUserORM(
            telegram_user_id=1003,
            telegram_username=None,
            connection_id="conn-a",
            claim_url="https://claim.test/old",
            onboarding_state="awaiting_claim",
        )
        session.add(row)
        session.commit()

        refresh_hosted_user_claim_state(session, settings, fake, 1003)
        session.refresh(row)
        assert row.claim_url == "https://claim.test/from-get"
        assert row.user_agent_id == "agent-from-list"
        assert row.vault_id == "vault-from-list"
        assert row.onboarding_state == "awaiting_claim"
        assert fake.get_calls == ["conn-a"]
    finally:
        session.close()
        engine.dispose()


def test_refresh_marks_ready_via_app_users_list() -> None:
    app = "ed17c6ee-baff-4fa7-8018-267c22ea95a7"
    settings = AureySettings(
        hosted_platform_enabled=True,
        platform_api_key="plt_test",
        platform_template_id="tmpl_x",
        platform_app_id=app,
        oneclaw_base_url="https://platform.example",
    )
    fake = _FakeConnPlatform(
        {"conn-a": {"claimed": True, "user_agent_id": "fallback-should-not-load"}},
        app_list_payload={
            "data": [
                {
                    "connection_id": "conn-a",
                    "claimed": True,
                    "user_agent_id": "agent-from-list",
                    "vault_id": "vault-prod",
                    "wallet_address": "0x00000000000000000000000000000000000000aa",
                }
            ]
        },
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
        assert refreshed.user_agent_id == "agent-from-list"
        assert refreshed.vault_id == "vault-prod"
        assert refreshed.wallet_address == "0x00000000000000000000000000000000000000aa"
        assert fake.list_calls == [app]
        assert fake.get_calls == ["conn-a"]
    finally:
        session.close()
        engine.dispose()


def test_refresh_when_users_endpoint_returns_top_level_json_array() -> None:
    """Live API may return ``[{...}, ...]`` instead of ``{\"data\": [...]}``."""

    app = "ed17c6ee-baff-4fa7-8018-267c22ea95a7"
    settings = AureySettings(
        hosted_platform_enabled=True,
        platform_api_key="plt_test",
        platform_template_id="tmpl_x",
        platform_app_id=app,
        oneclaw_base_url="https://platform.example",
    )
    agent_uuid = "0871f7c9-9a9d-4622-b27c-4e0be08081e2"
    fake = _FakeConnPlatform(
        {"conn-a": {}},
        app_list_payload=[
            {
                "agent_ids": [agent_uuid],
                "claimed_at": "2026-05-16T15:09:52.712408+00:00",
                "connection_id": "conn-a",
                "status": "claimed",
                "vault_ids": ["ec6d4a2c-6735-4356-ab29-31337819efbe"],
            },
        ],
        signing_keys_by_agent={
            agent_uuid: {
                "keys": [
                    {
                        "chain": "ethereum",
                        "address": "0xcccccccccccccccccccccccccccccccccccccccc",
                    }
                ],
            },
        },
    )
    session, engine = _memory_session()
    try:
        row = HostedPlatformUserORM(
            telegram_user_id=707,
            telegram_username=None,
            connection_id="conn-a",
            claim_url="https://claim/q",
            onboarding_state="awaiting_claim",
        )
        session.add(row)
        session.commit()

        out = refresh_hosted_user_claim_state(session, settings, fake, 707)
        assert out is not None
        session.commit()

        refreshed = session.get(HostedPlatformUserORM, row.id)
        assert refreshed is not None
        assert refreshed.onboarding_state == "ready"
        assert refreshed.user_agent_id == "0871f7c9-9a9d-4622-b27c-4e0be08081e2"
        assert refreshed.vault_id == "ec6d4a2c-6735-4356-ab29-31337819efbe"
        assert refreshed.wallet_address is not None
        assert refreshed.wallet_address.startswith("0x")
        assert fake.signing_keys_calls == [agent_uuid]
        assert fake.get_calls == ["conn-a"]
    finally:
        session.close()
        engine.dispose()


def test_refresh_list_miss_falls_back_to_get_connection() -> None:
    app = "ed17c6ee-baff-4fa7-8018-267c22ea95a7"
    settings = AureySettings(
        hosted_platform_enabled=True,
        platform_api_key="plt_test",
        platform_template_id="tmpl_x",
        platform_app_id=app,
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
        },
        app_list_payload={"data": []},
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
        assert fake.list_calls == [app]
        assert fake.get_calls == ["conn-a"]
    finally:
        session.close()
        engine.dispose()


class _ListFailsGetOkPlatform:
    def __init__(
        self,
        get_payload: dict,
        *,
        signing_keys_by_agent: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.get_payload = get_payload
        self.signing_keys_by_agent = signing_keys_by_agent or {}
        self.list_calls: list[str] = []
        self.get_calls: list[str] = []
        self.signing_keys_calls: list[str] = []

    def list_app_users(self, app_id: str) -> Any:
        from aurey.cloud.platform_client import HostedPlatformApiError

        self.list_calls.append(app_id)
        raise HostedPlatformApiError("no list", status_code=404)

    def get_connection(self, connection_id: str) -> dict:
        self.get_calls.append(connection_id)
        return dict(self.get_payload)

    def get_agent_signing_keys(self, agent_id: str) -> dict[str, Any]:
        self.signing_keys_calls.append(agent_id)
        payload = self.signing_keys_by_agent.get(agent_id)
        if payload is not None:
            return dict(payload)
        return {"keys": []}


def test_refresh_list_error_falls_back_to_get_connection() -> None:
    app = "ed17c6ee-baff-4fa7-8018-267c22ea95a7"
    settings = AureySettings(
        hosted_platform_enabled=True,
        platform_api_key="plt_test",
        platform_template_id="tmpl_x",
        platform_app_id=app,
        oneclaw_base_url="https://platform.example",
    )
    fake = _ListFailsGetOkPlatform(
        {
            "claimed": True,
            "user_agent_id": "via-get",
        }
    )
    session, engine = _memory_session()
    try:
        row = HostedPlatformUserORM(
            telegram_user_id=6006,
            telegram_username=None,
            connection_id="conn-a",
            claim_url="https://claim/q",
            onboarding_state="awaiting_claim",
        )
        session.add(row)
        session.commit()

        out = refresh_hosted_user_claim_state(session, settings, fake, 6006)
        assert out is not None
        session.commit()

        refreshed = session.get(HostedPlatformUserORM, row.id)
        assert refreshed is not None
        assert refreshed.onboarding_state == "ready"
        assert refreshed.user_agent_id == "via-get"
        assert fake.list_calls == [app]
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
            wallet_address="0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
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


class _BoomConn404Platform:
    def get_connection(self, connection_id: str) -> dict:
        from aurey.cloud.platform_client import HostedPlatformApiError

        raise HostedPlatformApiError("Platform HTTP 404 for GET x", status_code=404)

    def list_app_users(self, app_id: str) -> Any:
        from aurey.cloud.platform_client import HostedPlatformApiError

        raise HostedPlatformApiError("Platform HTTP 404 for list users", status_code=404)

    def get_agent_signing_keys(self, agent_id: str) -> dict[str, Any]:
        return {"keys": []}


def test_refresh_keeps_row_on_platform_connection_404() -> None:
    settings = AureySettings(
        hosted_platform_enabled=True,
        platform_api_key="plt_test",
        platform_template_id="tmpl_x",
    )
    session, engine = _memory_session()
    try:
        row = HostedPlatformUserORM(
            telegram_user_id=5005,
            telegram_username=None,
            connection_id="conn-uuid",
            claim_url="https://claim/q",
            onboarding_state="awaiting_claim",
        )
        session.add(row)
        session.commit()

        refresh_hosted_user_claim_state(session, settings, _BoomConn404Platform(), row)
        session.refresh(row)
        assert row.onboarding_state == "awaiting_claim"
    finally:
        session.close()
        engine.dispose()
