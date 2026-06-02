"""Bind Telegram @handles to ``telegram_user_id`` after a verified invite claim."""

from __future__ import annotations

import html
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from aurey.cloud.models import HostedHandleClaimORM

__all__ = [
    "format_handle_already_claimed_html",
    "get_handle_claim_telegram_user_id",
    "register_handle_claim",
]


def get_handle_claim_telegram_user_id(
    session: Session,
    *,
    handle_normalized: str,
) -> int | None:
    row = session.scalar(
        select(HostedHandleClaimORM).where(
            HostedHandleClaimORM.handle_normalized == handle_normalized,
        ),
    )
    if row is None:
        return None
    return int(row.telegram_user_id)


def register_handle_claim(
    session: Session,
    *,
    handle_normalized: str,
    telegram_user_id: int,
    invite_id: object | None = None,
) -> HostedHandleClaimORM:
    """Record that this Telegram user owns payments to ``@handle`` in Aurey."""

    existing = session.scalar(
        select(HostedHandleClaimORM).where(
            HostedHandleClaimORM.handle_normalized == handle_normalized,
        ),
    )
    now = datetime.now(tz=UTC)
    if existing is not None:
        if int(existing.telegram_user_id) != int(telegram_user_id):
            raise ValueError("handle already claimed by another telegram_user_id")
        existing.claimed_at = now
        if invite_id is not None:
            existing.source_invite_id = invite_id  # type: ignore[assignment]
        session.flush()
        return existing

    row = HostedHandleClaimORM(
        handle_normalized=handle_normalized,
        telegram_user_id=int(telegram_user_id),
        source_invite_id=invite_id,
        claimed_at=now,
    )
    session.add(row)
    session.flush()
    return row


def format_handle_already_claimed_html(*, target_handle: str) -> str:
    handle = html.escape((target_handle or "").strip().lstrip("@"), quote=False)
    return (
        "<b>Aurey</b>\n"
        f"Payments to <b>@{handle}</b> are already linked to another Telegram account in Aurey.\n"
        "If you are the intended recipient, ask the sender to create a new invite "
        "for your @username."
    )
