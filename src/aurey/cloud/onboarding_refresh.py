"""Refresh hosted user rows from Platform connection state (claim → ready)."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from aurey.cloud.claim_status import (
    ConnectionClaimSignals,
    connection_claim_details,
    should_mark_connection_ready,
    user_record_for_connection_id,
)
from aurey.cloud.models import HostedPlatformUserORM
from aurey.cloud.platform_client import HostedPlatformApiError, extract_claim_url
from aurey.cloud.wallet_sync import maybe_backfill_wallet_from_signing_keys
from aurey.settings import AureySettings

_log = logging.getLogger(__name__)


class HostedPlatformConnectionClient(Protocol):
    """Minimal surface consumed by onboarding refresh (real client or test double)."""

    def list_app_users(self, app_id: str) -> Any: ...

    def get_connection(self, connection_id: str) -> Mapping[str, Any]: ...

    def get_agent_signing_keys(self, agent_id: str) -> dict[str, Any]: ...


def _merge_signals_into_row(row: HostedPlatformUserORM, signals: ConnectionClaimSignals) -> None:
    if signals.user_agent_id is not None:
        row.user_agent_id = signals.user_agent_id
    if signals.vault_id is not None:
        row.vault_id = signals.vault_id
    if signals.wallet_address is not None:
        row.wallet_address = signals.wallet_address


def _try_signing_keys_wallet_backfill(
    session: Session,
    settings: AureySettings,
    platform_client: HostedPlatformConnectionClient,
    row: HostedPlatformUserORM,
    *,
    reason: str,
) -> None:
    if not settings.hosted_platform_enabled:
        return
    maybe_backfill_wallet_from_signing_keys(
        session,
        platform_client,
        row,
        reason=reason,
    )


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

    When ``platform_app_id`` is configured, tries ``GET /v1/platform/apps/{app_id}/users`` and
    matches the row by ``connection_id``. Otherwise (or when no matching user entry is found),
    falls back to ``GET /v1/platform/connections/{connection_id}`` when available.

    Returns the ORM row when found, or ``None`` when the Telegram user has no hosted row.
    No-ops when the row is already ``ready`` with ``connection_id`` and ``user_agent_id`` set.
    Only ``awaiting_claim`` rows trigger polling; other states are returned without network I/O.
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
        _try_signing_keys_wallet_backfill(
            session, settings, platform_client, row, reason="ready_short_circuit",
        )
        return row

    state = (row.onboarding_state or "").strip()
    if state != "awaiting_claim":
        _try_signing_keys_wallet_backfill(
            session, settings, platform_client, row, reason="non_awaiting_state",
        )
        return row

    if not settings.hosted_platform_enabled:
        return row

    conn = (row.connection_id or "").strip()
    if not conn:
        return row

    payload: Mapping[str, Any] | None = None
    app_id = (settings.platform_app_id or "").strip()

    if app_id:
        try:
            users_payload = platform_client.list_app_users(app_id)
        except HostedPlatformApiError as exc:
            _log.debug(
                "hosted app users poll failed HTTP %s app_id=%s (%s); falling back to connection GET",
                exc.status_code,
                app_id,
                exc,
            )
        else:
            hit = user_record_for_connection_id(users_payload, conn)
            if hit is not None:
                payload = hit
                _log.debug(
                    "hosted claim poll matched connection_id=%s in app %s users list",
                    conn,
                    app_id,
                )

    if payload is None:
        try:
            payload = platform_client.get_connection(conn)
        except HostedPlatformApiError as exc:
            if exc.status_code == 404:
                _log.debug(
                    "hosted claim poll skipped HTTP 404 connection_id=%s (%s)",
                    conn,
                    exc,
                )
                _try_signing_keys_wallet_backfill(
                    session, settings, platform_client, row, reason="connection_404",
                )
                return row
            raise

    fresh_claim = extract_claim_url(payload)
    if fresh_claim:
        row.claim_url = fresh_claim

    signals = connection_claim_details(payload)
    _merge_signals_into_row(row, signals)

    if should_mark_connection_ready(signals):
        row.onboarding_state = "ready"

    session.flush()
    _try_signing_keys_wallet_backfill(
        session, settings, platform_client, row, reason="post_claim_poll",
    )
    return row


__all__ = ["HostedPlatformConnectionClient", "refresh_hosted_user_claim_state"]
