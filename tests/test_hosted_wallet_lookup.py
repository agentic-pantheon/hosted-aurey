"""Telegram row load backfills Solana like EVM from signing-keys."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aurey.cloud.hosted_wallet_lookup import load_hosted_platform_user_row_for_telegram
from aurey.cloud.models import Base, HostedPlatformUserORM
from aurey.cloud.platform_client import HostedPlatformApiError
from aurey.settings import AureySettings


class _FakePlatform:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    def get_agent_signing_keys(self, agent_id: str) -> dict:
        _ = agent_id
        self.calls += 1
        return self.payload


class _HttpFallback:
    def get_agent_signing_keys_json(self, agent_id: str, *, agent_api_key: str) -> dict:
        _ = agent_id, agent_api_key
        return {
            "keys": [
                {"chain": "ethereum", "address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
                {"chain": "solana", "address": "SolTelegramLoad"},
            ]
        }


def test_telegram_row_load_backfills_solana_and_evm(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    row = HostedPlatformUserORM(
        telegram_user_id=77,
        user_agent_id="agent-77",
        wallet_address="0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    )
    session.add(row)
    session.commit()

    plat = _FakePlatform({"keys": []})

    def _plat403(*_a, **_k):
        raise HostedPlatformApiError("forbidden", status_code=403)

    plat.get_agent_signing_keys = _plat403  # type: ignore[method-assign]

    monkeypatch.setattr(
        "aurey.cloud.wallet_sync._signing_keys_payload",
        lambda *_a, **_k: {
            "keys": [{"chain": "solana", "address": "SolTelegramLoad"}],
        },
    )
    monkeypatch.setattr(
        "aurey.cloud.platform_client.OneClawPlatformClient.from_settings",
        lambda _s: plat,
    )

    settings = AureySettings(hosted_platform_enabled=True, platform_api_key="plt_x")
    loaded = load_hosted_platform_user_row_for_telegram(
        session,
        settings,
        telegram_user_id=77,
        reason="test",
        oneclaw_http=object(),
    )
    assert loaded is not None
    assert loaded.solana_wallet_address == "SolTelegramLoad"
    session.close()
    engine.dispose()
