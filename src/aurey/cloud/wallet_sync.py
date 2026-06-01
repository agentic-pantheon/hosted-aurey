"""Backfill ``HostedPlatformUserORM`` wallet columns from 1Claw signing-keys API."""

from __future__ import annotations

import logging
from typing import Any, Protocol

from sqlalchemy.orm import Session

from aurey.cloud.models import HostedPlatformUserORM
from aurey.cloud.platform_client import (
    HostedPlatformApiError,
    OneClawPlatformClient,
    ethereum_address_from_signing_keys_payload,
    solana_address_from_signing_keys_payload,
)

_log = logging.getLogger(__name__)


class SigningKeysClient(Protocol):
    def get_agent_signing_keys(self, agent_id: str) -> dict[str, Any]: ...


def sync_wallet_address_from_signing_keys(
    platform: SigningKeysClient | OneClawPlatformClient,
    *,
    user_agent_id: str,
) -> str | None:
    """GET signing-keys for ``user_agent_id``; return checksummed Ethereum address or None."""

    aid = (user_agent_id or "").strip()
    if not aid:
        return None
    try:
        payload = platform.get_agent_signing_keys(aid)
    except HostedPlatformApiError:
        raise
    addr = ethereum_address_from_signing_keys_payload(payload)
    if addr is None:
        _log.debug("signing-keys response had no parseable Ethereum address agent_id=%s", aid)
    return addr


def sync_solana_wallet_address_from_signing_keys(
    platform: SigningKeysClient | OneClawPlatformClient,
    *,
    user_agent_id: str,
) -> str | None:
    """GET signing-keys for ``user_agent_id``; return Solana address or None."""

    aid = (user_agent_id or "").strip()
    if not aid:
        return None
    try:
        payload = platform.get_agent_signing_keys(aid)
    except HostedPlatformApiError:
        raise
    addr = solana_address_from_signing_keys_payload(payload)
    if addr is None:
        _log.debug("signing-keys response had no parseable Solana address agent_id=%s", aid)
    return addr


def maybe_backfill_wallet_from_signing_keys(
    session: Session,
    platform: SigningKeysClient | OneClawPlatformClient,
    row: HostedPlatformUserORM,
    *,
    force: bool = False,
    reason: str = "",
) -> str | None:
    """When ``wallet_address`` is empty (unless ``force``), fetch signing-keys and persist.

    Returns address written, or None when skipped / unchanged. Swallows
    :class:`HostedPlatformApiError` (log at debug) so pollers stay non-fatal.

    Caller is responsible for ``session.commit()`` when appropriate.
    """

    aid = (row.user_agent_id or "").strip()
    if not aid:
        return None
    if not force and (row.wallet_address or "").strip():
        return None

    try:
        addr = sync_wallet_address_from_signing_keys(platform, user_agent_id=aid)
    except HostedPlatformApiError as exc:
        _log.debug(
            "signing-keys backfill skipped (%s) agent_id=%s reason=%s: %s",
            exc.status_code,
            aid,
            reason,
            exc,
        )
        return None

    if addr is None:
        return None
    row.wallet_address = addr
    session.flush()
    _log.debug("wallet_address backfilled agent_id=%s reason=%s", aid, reason)
    return addr


def maybe_backfill_solana_wallet_from_signing_keys(
    session: Session,
    platform: SigningKeysClient | OneClawPlatformClient,
    row: HostedPlatformUserORM,
    *,
    force: bool = False,
    reason: str = "",
) -> str | None:
    """When ``solana_wallet_address`` is empty (unless ``force``), fetch signing-keys and persist."""

    aid = (row.user_agent_id or "").strip()
    if not aid:
        return None
    if not force and (row.solana_wallet_address or "").strip():
        return None

    try:
        addr = sync_solana_wallet_address_from_signing_keys(platform, user_agent_id=aid)
    except HostedPlatformApiError as exc:
        _log.debug(
            "signing-keys Solana backfill skipped (%s) agent_id=%s reason=%s: %s",
            exc.status_code,
            aid,
            reason,
            exc,
        )
        return None

    if addr is None:
        return None
    row.solana_wallet_address = addr
    session.flush()
    _log.debug("solana_wallet_address backfilled agent_id=%s reason=%s", aid, reason)
    return addr


__all__ = [
    "maybe_backfill_solana_wallet_from_signing_keys",
    "maybe_backfill_wallet_from_signing_keys",
    "sync_solana_wallet_address_from_signing_keys",
    "sync_wallet_address_from_signing_keys",
]
