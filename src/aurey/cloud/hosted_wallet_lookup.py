"""Load hosted platform user row + optional wallet backfill from Platform signing-keys.

Shared between Telegram Deep Agent invokes and Telegram Mini App read-only portfolio.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aurey.cloud.models import HostedPlatformUserORM
from aurey.settings import AureySettings

_log = logging.getLogger(__name__)


def backfill_hosted_wallets_from_signing_keys_if_empty(
    session: Session,
    settings: AureySettings,
    row: HostedPlatformUserORM,
    *,
    reason: str,
    oneclaw_http: Any | None = None,
) -> None:
    """If EVM or Solana columns are empty, one signing-keys GET may fill both (same as provision).

    On success commits ``session``. On failure rolls back and logs at debug.
    """

    if (row.wallet_address or "").strip() and (row.solana_wallet_address or "").strip():
        return
    if not (row.user_agent_id or "").strip():
        return

    from aurey.cloud.platform_client import OneClawPlatformClient
    from aurey.cloud.wallet_sync import maybe_backfill_hosted_wallet_columns_from_signing_keys

    try:
        plat = OneClawPlatformClient.from_settings(settings)
        maybe_backfill_hosted_wallet_columns_from_signing_keys(
            session,
            plat,
            row,
            reason=reason,
            settings=settings,
            oneclaw_http=oneclaw_http,
        )
        session.flush()
        session.commit()
    except Exception:
        session.rollback()
        _log.debug(
            "hosted wallet backfill failed for telegram_user_id=%s (%s)",
            row.telegram_user_id,
            reason,
            exc_info=True,
        )


def backfill_hosted_wallet_from_signing_keys_if_empty(
    session: Session,
    settings: AureySettings,
    row: HostedPlatformUserORM,
    *,
    reason: str,
    oneclaw_http: Any | None = None,
) -> None:
    """Alias for :func:`backfill_hosted_wallets_from_signing_keys_if_empty`."""

    backfill_hosted_wallets_from_signing_keys_if_empty(
        session,
        settings,
        row,
        reason=reason,
        oneclaw_http=oneclaw_http,
    )


def load_hosted_platform_user_row_for_telegram(
    session: Session,
    settings: AureySettings,
    *,
    telegram_user_id: int,
    reason: str,
    allow_wallet_backfill: bool = True,
    oneclaw_http: Any | None = None,
) -> HostedPlatformUserORM | None:
    """Fetch ``hosted_platform_users`` row and optional signing-keys wallet backfill."""

    row = session.scalar(
        select(HostedPlatformUserORM).where(
            HostedPlatformUserORM.telegram_user_id == telegram_user_id,
        )
    )
    if row is None:
        return None
    if allow_wallet_backfill:
        backfill_hosted_wallets_from_signing_keys_if_empty(
            session,
            settings,
            row,
            reason=reason,
            oneclaw_http=oneclaw_http,
        )
    return row


__all__ = [
    "backfill_hosted_wallet_from_signing_keys_if_empty",
    "backfill_hosted_wallets_from_signing_keys_if_empty",
    "load_hosted_platform_user_row_for_telegram",
]
