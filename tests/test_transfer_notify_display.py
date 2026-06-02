"""Tests for transfer received DM display fields."""

from __future__ import annotations

from aurey.cloud.transfer_notify_display import transfer_received_display_from_execute
from aurey.graphs.evm_codec import erc20_transfer_data
from aurey.runtime import AureyRuntime, PreparedTransactionStore
from aurey.service.state import AureyServiceState
from aurey.settings import AureySettings
from aurey.telegram.notifications import build_transfer_received_html


def _state() -> AureyServiceState:
    runtime = AureyRuntime(
        settings=AureySettings(),
        secret_store=object(),  # type: ignore[arg-type]
        evm_rpc_factory=lambda _c: object(),  # type: ignore[arg-type, return-value]
        http=object(),  # type: ignore[arg-type]
        tx_pipeline=object(),  # type: ignore[arg-type]
    )
    return AureyServiceState(
        settings=runtime.settings,
        runtime=runtime,
        checkpointer=object(),  # type: ignore[arg-type]
        default_model="test",
    )


def test_transfer_display_native_transfer() -> None:
    state = _state()
    pid = state.runtime.prepared_txs.put(
        kind="execute_envelope",
        payload={
            "kind": "native_transfer",
            "chain_id": 8453,
            "from_address": "0x0000000000000000000000000000000000000001",
            "to": "0x00000000000000000000000000000000000000A2",
            "data": "0x",
            "value_hex": "0xde0b6b3a7640000",
        },
    )
    display = transfer_received_display_from_execute(
        state,
        {"prepared_id": pid},
        tx_hash="0x" + "ab" * 32,
    )
    assert display is not None
    assert display.chain_label == "Base"
    assert display.token_label == "ETH"
    assert display.amount_text == "1"
    assert display.explorer_tx_url is not None
    assert "basescan.org/tx/" in display.explorer_tx_url


def test_transfer_display_erc20_transfer() -> None:
    state = _state()
    to_peer = "0x00000000000000000000000000000000000000A2"
    token = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
    amount = 1_000_000
    pid = state.runtime.prepared_txs.put(
        kind="execute_envelope",
        payload={
            "kind": "erc20_transfer",
            "chain_id": 8453,
            "from_address": "0x0000000000000000000000000000000000000001",
            "to": token,
            "data": erc20_transfer_data(to_peer, amount),
            "value_hex": "0x0",
        },
    )
    display = transfer_received_display_from_execute(
        state,
        {"prepared_id": pid},
        tx_hash="0x" + "cd" * 32,
    )
    assert display is not None
    assert display.token_label == "USDC"
    assert display.amount_text == "1"
    html = build_transfer_received_html(sender_handle="@alice", display=display)
    assert "USDC" in html
    assert "Amount:" in html
    assert "basescan.org/tx/" in html
