"""Resolve peer-transfer notify recipient from execute envelope or hosted DB."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from aurey.cloud.hosted_access import format_telegram_handle
from aurey.cloud.models import HostedPlatformUserORM
from aurey.cloud.peer_transfer_context import PeerTransferRecipient
from aurey.graphs.evm_codec import decode_erc20_transfer_recipient, normalize_evm_address
from aurey.service.state import AureyServiceState


def _execute_payload(state: AureyServiceState, inputs: dict[str, Any]) -> dict[str, Any] | None:
    prepared_id = inputs.get("prepared_id")
    if prepared_id:
        record = state.runtime.prepared_txs.get(str(prepared_id))
        if record is not None and record.kind == "execute_envelope":
            return dict(record.payload)
    envelope = inputs.get("envelope")
    if isinstance(envelope, dict):
        return dict(envelope)
    return None


def recipient_evm_from_transfer_execute(
    state: AureyServiceState,
    inputs: dict[str, Any],
) -> str | None:
    """Infer peer recipient ``0x`` from a successful transfer ``tx_execute`` input."""

    payload = _execute_payload(state, inputs)
    if payload is None:
        return None
    kind = str(payload.get("kind") or "").strip()
    if kind == "native_transfer":
        to_raw = payload.get("to")
        if not to_raw:
            return None
        try:
            return normalize_evm_address(str(to_raw))
        except ValueError:
            return None
    if kind == "erc20_transfer":
        return decode_erc20_transfer_recipient(str(payload.get("data") or ""))
    return None


def lookup_peer_recipient_by_wallet(
    state: AureyServiceState,
    evm_address: str,
) -> PeerTransferRecipient | None:
    """Map an on-chain recipient address to a hosted Telegram user."""

    factory = state.hosted_session_factory
    if factory is None:
        return None
    try:
        checksum = normalize_evm_address(evm_address)
    except ValueError:
        return None

    db = factory()
    try:
        row = db.scalar(
            select(HostedPlatformUserORM).where(
                func.lower(HostedPlatformUserORM.wallet_address) == checksum.lower(),
            ),
        )
        if row is None:
            return None
        display = format_telegram_handle(
            telegram_username=row.telegram_username,
            telegram_user_id=row.telegram_user_id,
        )
        return PeerTransferRecipient(
            telegram_user_id=int(row.telegram_user_id),
            telegram_handle=display,
            evm_address=checksum,
        )
    finally:
        db.close()


def execute_payload_from_tx_inputs(
    state: AureyServiceState,
    inputs: dict[str, Any],
) -> dict[str, Any] | None:
    """Resolved execute envelope from ``tx_execute`` tool inputs."""

    return _execute_payload(state, inputs)


__all__ = [
    "execute_payload_from_tx_inputs",
    "lookup_peer_recipient_by_wallet",
    "recipient_evm_from_transfer_execute",
]
