"""Tests for wallet_sync backfill helpers."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aurey.cloud.models import Base, HostedPlatformUserORM
from aurey.cloud.wallet_sync import (
    maybe_backfill_hosted_wallet_columns_from_signing_keys,
    maybe_backfill_solana_wallet_from_signing_keys,
    maybe_backfill_wallet_from_signing_keys,
)


class _FakeSigningKeysPlatform:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[str] = []

    def get_agent_signing_keys(self, agent_id: str) -> dict:
        self.calls.append(agent_id)
        return self.payload


def _memory_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return factory(), engine


def test_maybe_backfill_solana_skips_when_column_populated() -> None:
    session, engine = _memory_session()
    try:
        row = HostedPlatformUserORM(
            telegram_user_id=1,
            user_agent_id="agent-1",
            solana_wallet_address="ExistingSol",
        )
        session.add(row)
        session.flush()
        plat = _FakeSigningKeysPlatform(
            {"keys": [{"chain": "solana", "address": "NewSol"}]},
        )
        out = maybe_backfill_solana_wallet_from_signing_keys(
            session,
            plat,
            row,
            reason="test",
        )
        assert out is None
        assert plat.calls == []
        assert row.solana_wallet_address == "ExistingSol"
    finally:
        engine.dispose()


def test_maybe_backfill_solana_persists_from_signing_keys() -> None:
    session, engine = _memory_session()
    try:
        row = HostedPlatformUserORM(
            telegram_user_id=2,
            user_agent_id="agent-2",
        )
        session.add(row)
        session.flush()
        plat = _FakeSigningKeysPlatform(
            {
                "keys": [
                    {"chain": "ethereum", "address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
                    {"chain": "solana", "address": "SolBackfillAddr"},
                ]
            },
        )
        out = maybe_backfill_solana_wallet_from_signing_keys(
            session,
            plat,
            row,
            reason="test",
        )
        assert out == "SolBackfillAddr"
        assert row.solana_wallet_address == "SolBackfillAddr"
        assert plat.calls == ["agent-2"]
    finally:
        engine.dispose()


def test_maybe_backfill_evm_and_solana_independent() -> None:
    session, engine = _memory_session()
    try:
        row = HostedPlatformUserORM(
            telegram_user_id=3,
            user_agent_id="agent-3",
        )
        session.add(row)
        session.flush()
        plat = _FakeSigningKeysPlatform(
            {
                "keys": [
                    {"chain": "ethereum", "address": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},
                    {"chain": "solana", "address": "SolBoth"},
                ]
            },
        )
        evm, sol = maybe_backfill_hosted_wallet_columns_from_signing_keys(
            session,
            plat,
            row,
            reason="test",
        )
        assert evm is not None and evm.startswith("0x")
        assert sol == "SolBoth"
        assert len(plat.calls) == 1
    finally:
        engine.dispose()
