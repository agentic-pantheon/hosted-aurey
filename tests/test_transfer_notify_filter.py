"""Tests for peer transfer notify gating (approval vs transfer tx_execute)."""

from __future__ import annotations

from aurey.runtime import AureyRuntime, PreparedTransactionStore
from aurey.settings import AureySettings
from aurey.telegram.notifications import _should_notify_peer_transfer_execute


def _runtime() -> AureyRuntime:
    return AureyRuntime(
        settings=AureySettings(),
        secret_store=object(),  # type: ignore[arg-type]
        evm_rpc_factory=lambda _c: object(),  # type: ignore[arg-type, return-value]
        http=object(),  # type: ignore[arg-type]
        tx_pipeline=object(),  # type: ignore[arg-type]
    )


def test_skip_erc20_approval_execute() -> None:
    runtime = _runtime()
    pid = runtime.prepared_txs.put(
        kind="execute_envelope",
        payload={
            "kind": "erc20_approval",
            "chain_id": 8453,
            "from_address": "0x0000000000000000000000000000000000000001",
            "to": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
            "data": "0x",
            "value_hex": "0x0",
        },
    )
    from aurey.service.state import AureyServiceState

    state = AureyServiceState(
        settings=runtime.settings,
        runtime=runtime,
        checkpointer=object(),  # type: ignore[arg-type]
        default_model="test",
    )
    assert (
        _should_notify_peer_transfer_execute(
            state,
            inputs={"prepared_id": pid},
            peer_evm_address="0x00000000000000000000000000000000000000A2",
        )
        is False
    )


def test_notify_erc20_transfer_execute() -> None:
    runtime = _runtime()
    pid = runtime.prepared_txs.put(
        kind="execute_envelope",
        payload={
            "kind": "erc20_transfer",
            "chain_id": 8453,
            "from_address": "0x0000000000000000000000000000000000000001",
            "to": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
            "data": "0x",
            "value_hex": "0x0",
        },
    )
    from aurey.service.state import AureyServiceState

    state = AureyServiceState(
        settings=runtime.settings,
        runtime=runtime,
        checkpointer=object(),  # type: ignore[arg-type]
        default_model="test",
    )
    assert (
        _should_notify_peer_transfer_execute(
            state,
            inputs={"prepared_id": pid},
            peer_evm_address="0x00000000000000000000000000000000000000A2",
        )
        is True
    )
