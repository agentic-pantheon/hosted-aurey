"""Tests for get_hosted_wallet_addresses tool lookup."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aurey.cloud.hosted_wallet_addresses import lookup_hosted_wallet_addresses
from aurey.cloud.models import Base, HostedPlatformUserORM
from aurey.cloud.signing_context import hosted_telegram_user_id_scope
from aurey.runtime import AureyRuntime
from aurey.settings import AureySettings


class _FakePlatform:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[str] = []

    def get_agent_signing_keys(self, agent_id: str) -> dict:
        self.calls.append(agent_id)
        return self.payload


def _memory_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False), engine


def test_lookup_hosted_solana_backfills_when_empty(monkeypatch) -> None:
    factory, engine = _memory_factory()
    try:
        session = factory()
        row = HostedPlatformUserORM(
            telegram_user_id=55,
            user_agent_id="agent-55",
        )
        session.add(row)
        session.commit()
        session.close()

        fake = _FakePlatform(
            {"keys": [{"chain": "solana", "address": "SolToolAddr"}]},
        )
        monkeypatch.setattr(
            "aurey.cloud.hosted_wallet_addresses.OneClawPlatformClient.from_settings",
            lambda _s: fake,
        )

        settings = AureySettings(hosted_platform_enabled=True, platform_api_key="plt_x")
        runtime = AureyRuntime(
            settings=settings,
            secret_store=object(),  # type: ignore[arg-type]
            evm_rpc_factory=lambda _c: object(),  # type: ignore[arg-type, return-value]
            http=object(),  # type: ignore[arg-type]
            tx_pipeline=object(),  # type: ignore[arg-type]
            hosted_session_factory=factory,
        )

        with hosted_telegram_user_id_scope(55):
            out = lookup_hosted_wallet_addresses(runtime, chain="solana")

        assert out["ok"] is True
        assert out["result"]["solana"] == "SolToolAddr"
        assert out["result"]["source"] == "signing_keys_backfill"
        assert fake.calls == ["agent-55"]

        session2 = factory()
        row2 = session2.get(HostedPlatformUserORM, row.id)
        assert row2 is not None
        assert row2.solana_wallet_address == "SolToolAddr"
        session2.close()
    finally:
        engine.dispose()


def test_lookup_hosted_solana_skips_platform_when_persisted(monkeypatch) -> None:
    factory, engine = _memory_factory()
    try:
        session = factory()
        row = HostedPlatformUserORM(
            telegram_user_id=56,
            user_agent_id="agent-56",
            solana_wallet_address="AlreadyThere",
        )
        session.add(row)
        session.commit()
        session.close()

        fake = _FakePlatform({"keys": []})
        monkeypatch.setattr(
            "aurey.cloud.hosted_wallet_addresses.OneClawPlatformClient.from_settings",
            lambda _s: fake,
        )

        settings = AureySettings(hosted_platform_enabled=True, platform_api_key="plt_x")
        runtime = AureyRuntime(
            settings=settings,
            secret_store=object(),  # type: ignore[arg-type]
            evm_rpc_factory=lambda _c: object(),  # type: ignore[arg-type, return-value]
            http=object(),  # type: ignore[arg-type]
            tx_pipeline=object(),  # type: ignore[arg-type]
            hosted_session_factory=factory,
        )

        with hosted_telegram_user_id_scope(56):
            out = lookup_hosted_wallet_addresses(runtime, chain="solana")

        assert out["ok"] is True
        assert out["result"]["solana"] == "AlreadyThere"
        assert out["result"]["source"] == "database"
        assert fake.calls == []
    finally:
        engine.dispose()
