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
from aurey.cloud.signing_keys_fetch import (
    fetch_agent_signing_keys_payload,
    signing_keys_fallback_for_row,
)
from aurey.settings import AureySettings

_log = logging.getLogger(__name__)


class SigningKeysClient(Protocol):
    def get_agent_signing_keys(self, agent_id: str) -> dict[str, Any]: ...


def _signing_keys_payload(
    platform: SigningKeysClient | OneClawPlatformClient,
    *,
    user_agent_id: str,
    settings: AureySettings | None = None,
    row: HostedPlatformUserORM | None = None,
    oneclaw_http: Any | None = None,
) -> dict[str, Any]:
    if not isinstance(platform, OneClawPlatformClient):
        return platform.get_agent_signing_keys(user_agent_id)
    fallback = (
        signing_keys_fallback_for_row(settings, row, oneclaw_http)
        if settings is not None and row is not None
        else None
    )
    return fetch_agent_signing_keys_payload(
        platform,
        user_agent_id=user_agent_id,
        fallback=fallback,
    )


def maybe_backfill_hosted_wallet_columns_from_signing_keys(
    session: Session,
    platform: SigningKeysClient | OneClawPlatformClient,
    row: HostedPlatformUserORM,
    *,
    force_evm: bool = False,
    force_sol: bool = False,
    reason: str = "",
    settings: AureySettings | None = None,
    oneclaw_http: Any | None = None,
) -> tuple[str | None, str | None]:
    """One signing-keys GET; fill empty ``wallet_address`` / ``solana_wallet_address`` when present."""

    aid = (row.user_agent_id or "").strip()
    if not aid:
        return None, None
    need_evm = force_evm or not (row.wallet_address or "").strip()
    need_sol = force_sol or not (row.solana_wallet_address or "").strip()
    if not need_evm and not need_sol:
        return None, None

    try:
        payload = _signing_keys_payload(
            platform,
            user_agent_id=aid,
            settings=settings,
            row=row,
            oneclaw_http=oneclaw_http,
        )
    except HostedPlatformApiError as exc:
        _log.debug(
            "signing-keys backfill skipped (%s) agent_id=%s reason=%s: %s",
            exc.status_code,
            aid,
            reason,
            exc,
        )
        return None, None

    eth_written: str | None = None
    sol_written: str | None = None
    if need_evm:
        eth = ethereum_address_from_signing_keys_payload(payload)
        if eth is not None:
            row.wallet_address = eth
            eth_written = eth
    if need_sol:
        sol = solana_address_from_signing_keys_payload(payload)
        if sol is not None:
            row.solana_wallet_address = sol
            sol_written = sol

    if eth_written is not None or sol_written is not None:
        session.flush()
        _log.debug(
            "hosted wallet columns backfilled agent_id=%s reason=%s evm=%s sol=%s",
            aid,
            reason,
            bool(eth_written),
            bool(sol_written),
        )
        if eth_written is not None and settings is not None:
            from aurey.cloud.hosted_invite_sender_notify import (
                maybe_notify_invite_senders_recipient_wallet_ready,
            )

            maybe_notify_invite_senders_recipient_wallet_ready(session, settings, row)
    return eth_written, sol_written


def sync_wallet_address_from_signing_keys(
    platform: SigningKeysClient | OneClawPlatformClient,
    *,
    user_agent_id: str,
    settings: AureySettings | None = None,
    row: HostedPlatformUserORM | None = None,
    oneclaw_http: Any | None = None,
) -> str | None:
    """GET signing-keys for ``user_agent_id``; return checksummed Ethereum address or None."""

    aid = (user_agent_id or "").strip()
    if not aid:
        return None
    try:
        payload = _signing_keys_payload(
            platform,
            user_agent_id=aid,
            settings=settings,
            row=row,
            oneclaw_http=oneclaw_http,
        )
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
    settings: AureySettings | None = None,
    row: HostedPlatformUserORM | None = None,
    oneclaw_http: Any | None = None,
) -> str | None:
    """GET signing-keys for ``user_agent_id``; return Solana address or None."""

    aid = (user_agent_id or "").strip()
    if not aid:
        return None
    try:
        payload = _signing_keys_payload(
            platform,
            user_agent_id=aid,
            settings=settings,
            row=row,
            oneclaw_http=oneclaw_http,
        )
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
    settings: AureySettings | None = None,
    oneclaw_http: Any | None = None,
) -> str | None:
    """When ``wallet_address`` is empty (unless ``force``), fetch signing-keys and persist."""

    if not force and (row.wallet_address or "").strip():
        return None
    eth, _ = maybe_backfill_hosted_wallet_columns_from_signing_keys(
        session,
        platform,
        row,
        force_evm=force,
        force_sol=False,
        reason=reason,
        settings=settings,
        oneclaw_http=oneclaw_http,
    )
    return eth


def maybe_backfill_solana_wallet_from_signing_keys(
    session: Session,
    platform: SigningKeysClient | OneClawPlatformClient,
    row: HostedPlatformUserORM,
    *,
    force: bool = False,
    reason: str = "",
    settings: AureySettings | None = None,
    oneclaw_http: Any | None = None,
) -> str | None:
    """When ``solana_wallet_address`` is empty (unless ``force``), fetch signing-keys and persist."""

    if not force and (row.solana_wallet_address or "").strip():
        return None
    _, sol = maybe_backfill_hosted_wallet_columns_from_signing_keys(
        session,
        platform,
        row,
        force_evm=False,
        force_sol=force,
        reason=reason,
        settings=settings,
        oneclaw_http=oneclaw_http,
    )
    return sol


__all__ = [
    "maybe_backfill_hosted_wallet_columns_from_signing_keys",
    "maybe_backfill_solana_wallet_from_signing_keys",
    "maybe_backfill_wallet_from_signing_keys",
    "sync_solana_wallet_address_from_signing_keys",
    "sync_wallet_address_from_signing_keys",
]
