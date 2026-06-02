"""Tests for peer transfer notify gating (approval vs transfer tx_execute)."""

from __future__ import annotations

import json

from langchain_core.messages import ToolMessage

from aurey.cloud.peer_transfer_context import PeerTransferRecipient
from aurey.runtime import AureyRuntime, PreparedTransactionStore
from aurey.telegram.notifications import _coerce_tool_output, _peer_from_resolve_tool_output
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


def test_coerce_tool_output_json_string() -> None:
    payload = {"ok": True, "result": {"tx_hash": "0xabc"}}
    out = _coerce_tool_output(json.dumps(payload))
    assert out == payload


def test_coerce_tool_output_tool_message_json_content() -> None:
    payload = {"ok": True, "result": {"tx_hash": "0xabc"}}
    msg = ToolMessage(content=json.dumps(payload), tool_call_id="call-1")
    assert _coerce_tool_output(msg) == payload


def test_coerce_tool_output_tool_message_artifact() -> None:
    payload = {"ok": True, "result": {"tx_hash": "0xdef"}}
    msg = ToolMessage(
        content="ignored",
        tool_call_id="call-2",
        artifact=payload,
    )
    assert _coerce_tool_output(msg) == payload


def test_peer_from_resolve_tool_output() -> None:
    peer = _peer_from_resolve_tool_output(
        {
            "ok": True,
            "result": {
                "telegram_user_id": 99,
                "telegram_handle": "@bob",
                "ethereum": "0x00000000000000000000000000000000000000A1",
                "to_address": "0x00000000000000000000000000000000000000A1",
            },
        },
    )
    assert peer is not None
    assert peer.telegram_user_id == 99
    assert isinstance(peer, PeerTransferRecipient)


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
