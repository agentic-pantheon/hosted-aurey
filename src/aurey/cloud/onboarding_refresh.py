"""Refresh hosted user rows from Platform connection state (claim → ready)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from aurey.cloud.claim_status import (
    ConnectionClaimSignals,
    connection_claim_details,
    should_mark_connection_ready,
)
from aurey.cloud.models import HostedPlatformUserORM
from aurey.settings import AureySettings


class HostedPlatformConnectionClient(Protocol):
    """Minimal surface consumed by onboarding refresh (real client or test double)."""

    def get_connection(self, connection_id: str) -> Mapping[str, Any]: ...


def _merge_signals_into_row(row: HostedPlatformUserORM, signals: ConnectionClaimSignals) -> None:
    if signals.user_agent_id is not None:
        row.user_agent_id = signals.user_agent_id
    if signals.vault_id is not None:
        row.vault_id = signals.vault_id
    if signals.wallet_address is not None:
        row.wallet_address = signals.wallet_address


def _is_fully_ready_row(row: HostedPlatformUserORM) -> bool:
    return (
        (row.onboarding_state or "").strip() == "ready"
        and bool((row.connection_id or "").strip())
        and bool((row.user_agent_id or "").strip())
    )


def refresh_hosted_user_claim_state(
    session: Session,
    settings: AureySettings,
    platform_client: HostedPlatformConnectionClient,
    hosted_user: HostedPlatformUserORM | int,
) -> HostedPlatformUserORM | None:
    """Poll Platform for claim completion; transition ``awaiting_claim`` → ``ready`` when due.

    Returns the ORM row when found, or ``None`` when the Telegram user has no hosted row.
    No-ops when the row is already ``ready`` with ``connection_id`` and ``user_agent_id`` set.
    Only ``awaiting_claim`` rows trigger a GET; other states are returned without network I/O.
    """

    row: HostedPlatformUserORM | None
    if isinstance(hosted_user, HostedPlatformUserORM):
        row = hosted_user
    else:
        row = session.scalar(
            select(HostedPlatformUserORM).where(
                HostedPlatformUserORM.telegram_user_id == int(hosted_user),
            )
        )

    if row is None:
        return None

    if _is_fully_ready_row(row):
        return row

    state = (row.onboarding_state or "").strip()
    if state != "awaiting_claim":
        return row

    if not settings.hosted_platform_enabled:
        return row

    conn = (row.connection_id or "").strip()
    if not conn:
        return row

    payload = platform_client.get_connection(conn)
    signals = connection_claim_details(payload)
    _merge_signals_into_row(row, signals)

    if should_mark_connection_ready(signals):
        row.onboarding_state = "ready"

    session.flush()
    return row


__all__ = ["HostedPlatformConnectionClient", "refresh_hosted_user_claim_state"]
