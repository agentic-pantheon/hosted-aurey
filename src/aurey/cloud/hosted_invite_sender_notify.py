"""Notify invite senders when the recipient finishes wallet setup."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from aurey.cloud.hosted_access import format_telegram_handle, normalize_telegram_username
from aurey.cloud.models import (
    HostedHandleClaimORM,
    HostedPlatformUserORM,
    HostedSendInviteORM,
)
from aurey.settings import AureySettings

_log = logging.getLogger(__name__)

__all__ = ["maybe_notify_invite_senders_recipient_wallet_ready"]


def _target_handles_for_recipient(
    session: Session,
    recipient: HostedPlatformUserORM,
) -> set[str]:
    handles: set[str] = set()
    uname = normalize_telegram_username(recipient.telegram_username or "")
    if uname:
        handles.add(uname)
    claim_rows = session.scalars(
        select(HostedHandleClaimORM).where(
            HostedHandleClaimORM.telegram_user_id == int(recipient.telegram_user_id),
        ),
    ).all()
    for claim in claim_rows:
        h = (claim.handle_normalized or "").strip().lower()
        if h:
            handles.add(h)
    return handles


def maybe_notify_invite_senders_recipient_wallet_ready(
    session: Session,
    settings: AureySettings,
    recipient: HostedPlatformUserORM,
) -> None:
    """DM users who created send-to-invite links once the recipient has an EVM wallet."""

    if not (recipient.wallet_address or "").strip():
        return

    handles = _target_handles_for_recipient(session, recipient)
    if not handles:
        return

    now = datetime.now(tz=UTC)
    recipient_display = format_telegram_handle(
        telegram_username=recipient.telegram_username,
        telegram_user_id=recipient.telegram_user_id,
    )

    notified_senders: set[int] = set()
    for handle in handles:
        invites = list(
            session.scalars(
                select(HostedSendInviteORM).where(
                    HostedSendInviteORM.target_handle_normalized == handle,
                    HostedSendInviteORM.sender_notified_at.is_(None),
                ),
            ).all(),
        )
        for inv in invites:
            sender_tid = int(inv.sender_telegram_user_id)
            inv.sender_notified_at = now
            if sender_tid in notified_senders:
                continue
            notified_senders.add(sender_tid)
            _schedule_sender_ready_dm(
                settings,
                sender_telegram_user_id=sender_tid,
                recipient_display=recipient_display,
                target_handle=handle,
            )

    if notified_senders:
        session.flush()
        _log.info(
            "invite sender notify scheduled recipient_tid=%s handles=%s senders=%s",
            recipient.telegram_user_id,
            sorted(handles),
            sorted(notified_senders),
        )


def _schedule_sender_ready_dm(
    _settings: AureySettings,
    *,
    sender_telegram_user_id: int,
    recipient_display: str,
    target_handle: str,
) -> None:
    from aurey.telegram.notifications import schedule_invite_sender_recipient_ready_notify

    schedule_invite_sender_recipient_ready_notify(
        sender_telegram_user_id=sender_telegram_user_id,
        recipient_display=recipient_display,
        target_handle=target_handle,
    )
