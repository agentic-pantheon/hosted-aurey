"""Tests for transfer notify recipient fallback lookup."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aurey.cloud.hosted_transfer_notify_lookup import (
    lookup_peer_recipient_by_wallet,
    recipient_evm_from_transfer_execute,
)
from aurey.cloud.models import Base, HostedPlatformUserORM
from aurey.graphs.evm_codec import decode_erc20_transfer_recipient, erc20_transfer_data
from aurey.runtime import AureyRuntime
from aurey.service.state import AureyServiceState
from aurey.settings import AureySettings


def test_decode_erc20_transfer_recipient() -> None:
    to_addr = "0x00000000000000000000000000000000000000b2"
    data = erc20_transfer_data(to_addr, 500_000)
    decoded = decode_erc20_transfer_recipient(data)
    assert decoded is not None
    assert decoded.lower() == to_addr.lower()


def test_recipient_evm_from_prepared_erc20_transfer() -> None:
    runtime = AureyRuntime(
        settings=AureySettings(),
        secret_store=object(),  # type: ignore[arg-type]
        evm_rpc_factory=lambda _c: object(),  # type: ignore[arg-type, return-value]
        http=object(),  # type: ignore[arg-type]
        tx_pipeline=object(),  # type: ignore[arg-type]
    )
    to_addr = "0x00000000000000000000000000000000000000b2"
    pid = runtime.prepared_txs.put(
        kind="execute_envelope",
        payload={
            "kind": "erc20_transfer",
            "chain_id": 8453,
            "from_address": "0x0000000000000000000000000000000000000001",
            "to": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
            "data": erc20_transfer_data(to_addr, 1),
            "value_hex": "0x0",
        },
    )
    state = AureyServiceState(
        settings=runtime.settings,
        runtime=runtime,
        checkpointer=object(),  # type: ignore[arg-type]
        default_model="test",
    )
    evm = recipient_evm_from_transfer_execute(state, {"prepared_id": pid})
    assert evm is not None
    assert evm.lower() == to_addr.lower()


def test_lookup_peer_recipient_by_wallet() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    session.add(
        HostedPlatformUserORM(
            telegram_user_id=55,
            telegram_username="crunnella",
            wallet_address="0x00000000000000000000000000000000000000b2",
        ),
    )
    session.commit()
    session.close()

    runtime = AureyRuntime(
        settings=AureySettings(hosted_platform_enabled=True),
        secret_store=object(),  # type: ignore[arg-type]
        evm_rpc_factory=lambda _c: object(),  # type: ignore[arg-type, return-value]
        http=object(),  # type: ignore[arg-type]
        tx_pipeline=object(),  # type: ignore[arg-type]
        hosted_session_factory=factory,
    )
    state = AureyServiceState(
        settings=runtime.settings,
        runtime=runtime,
        checkpointer=object(),  # type: ignore[arg-type]
        default_model="test",
        hosted_session_factory=factory,
    )
    peer = lookup_peer_recipient_by_wallet(
        state,
        "0x00000000000000000000000000000000000000B2",
    )
    assert peer is not None
    assert peer.telegram_user_id == 55
    assert "crunnella" in peer.telegram_handle.lower()
    engine.dispose()
