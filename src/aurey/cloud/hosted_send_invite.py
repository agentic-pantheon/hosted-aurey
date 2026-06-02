"""Send-to-invite deep links when a Telegram handle is not on Aurey yet."""

from __future__ import annotations

import html
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aurey.cloud.hosted_access import format_telegram_handle, normalize_telegram_username
from aurey.cloud.hosted_handle_claim import (
    format_handle_already_claimed_html,
    get_handle_claim_telegram_user_id,
    register_handle_claim,
)
from aurey.cloud.models import HostedPlatformUserORM, HostedSendInviteORM
from aurey.settings import AureySettings

__all__ = [
    "InviteExtras",
    "attach_invite_to_error",
    "attach_invite_to_not_found_error",
    "build_bot_onboarding_deeplink",
    "build_invite_deeplink",
    "set_invite_bot_username_cache",
    "consume_send_invite",
    "build_start_invite_welcome_if_any",
    "format_invite_wrong_account_html",
    "format_invite_welcome_html",
    "invitee_matches_invite_target",
    "load_invite_by_start_payload",
    "try_create_invite_for_not_found",
]

_INVITE_PREFIX = "inv_"
_cached_telegram_bot_username: str | None = None


def set_invite_bot_username_cache(username: str | None) -> None:
    """Set from Telegram ``getMe`` at bot startup when env username is unset."""

    global _cached_telegram_bot_username
    u = (username or "").strip().lstrip("@")
    _cached_telegram_bot_username = u or None


def _resolved_bot_username(settings: AureySettings) -> str:
    return (
        (settings.telegram_bot_username or "").strip().lstrip("@")
        or (_cached_telegram_bot_username or "").strip()
    )


@dataclass(frozen=True)
class InviteExtras:
    invite_deeplink: str | None
    invite_token: str | None


def build_invite_deeplink(settings: AureySettings, token: str) -> str | None:
    bot = _resolved_bot_username(settings)
    if not bot:
        return None
    payload = f"{_INVITE_PREFIX}{token}"
    return f"https://t.me/{bot}?start={payload}"


def build_bot_onboarding_deeplink(settings: AureySettings) -> str | None:
    """Generic ``t.me/<bot>`` link when a handle-specific ``inv_`` token is unavailable."""

    bot = _resolved_bot_username(settings)
    if not bot:
        return None
    return f"https://t.me/{bot}"


def try_create_invite_for_not_found(
    session: Session,
    settings: AureySettings,
    *,
    sender_telegram_user_id: int | None,
    target_handle_normalized: str,
) -> InviteExtras:
    """Create invite row when sender context and bot username are configured."""

    if sender_telegram_user_id is None:
        return InviteExtras(invite_deeplink=None, invite_token=None)
    now = datetime.now(tz=UTC)

    # Reuse a still-valid invite for the same (sender, target) instead of inserting a
    # new row on every failed resolve. This dedups, caps table growth, and gives the
    # sender a stable shareable link.
    existing = session.scalar(
        select(HostedSendInviteORM)
        .where(
            HostedSendInviteORM.sender_telegram_user_id == int(sender_telegram_user_id),
            HostedSendInviteORM.target_handle_normalized == target_handle_normalized,
            HostedSendInviteORM.consumed_at.is_(None),
            HostedSendInviteORM.expires_at > now,
        )
        .order_by(HostedSendInviteORM.expires_at.desc()),
    )
    if existing is not None:
        link = build_invite_deeplink(settings, existing.token)
        return InviteExtras(invite_deeplink=link, invite_token=existing.token if link else None)

    token = secrets.token_urlsafe(12)
    ttl_days = max(1, int(settings.hosted_send_invite_ttl_days))
    row = HostedSendInviteORM(
        token=token,
        sender_telegram_user_id=int(sender_telegram_user_id),
        target_handle_normalized=target_handle_normalized,
        expires_at=now + timedelta(days=ttl_days),
        consumed_at=None,
    )
    session.add(row)
    session.flush()
    link = build_invite_deeplink(settings, token)
    return InviteExtras(invite_deeplink=link, invite_token=token if link else None)


def attach_invite_to_error(
    err: dict[str, Any],
    extras: InviteExtras,
    *,
    hint: str,
) -> None:
    if extras.invite_deeplink:
        err["invite_deeplink"] = extras.invite_deeplink
        err["invite_hint"] = hint


def attach_invite_to_not_found_error(
    err: dict[str, Any],
    extras: InviteExtras,
) -> None:
    attach_invite_to_error(
        err,
        extras,
        hint=(
            "Share this link with the recipient so they can start Aurey and receive payments."
        ),
    )


def load_invite_by_start_payload(
    session: Session,
    *,
    start_arg: str,
) -> HostedSendInviteORM | None:
    raw = (start_arg or "").strip()
    if not raw.startswith(_INVITE_PREFIX):
        return None
    token = raw[len(_INVITE_PREFIX) :].strip()
    if not token:
        return None
    return session.scalar(
        select(HostedSendInviteORM).where(HostedSendInviteORM.token == token),
    )


def consume_send_invite(
    session: Session,
    row: HostedSendInviteORM,
) -> bool:
    """Mark invite consumed on first use. Returns False if expired."""

    now = datetime.now(tz=UTC)
    expires = row.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if expires < now:
        return False
    if row.consumed_at is None:
        row.consumed_at = now
        session.flush()
    return True


def invitee_matches_invite_target(
    invitee_username: str | None,
    target_handle_normalized: str,
) -> bool:
    """True only when the opener's Telegram @username matches the invite target."""

    inv_norm = normalize_telegram_username(invitee_username or "")
    target = (target_handle_normalized or "").strip().lower()
    return bool(inv_norm and target and inv_norm == target)


def format_invite_wrong_account_html(*, target_handle: str) -> str:
    handle = html.escape((target_handle or "").strip().lstrip("@"), quote=False)
    return (
        "<b>Aurey</b>\n"
        f"This invite is only for Telegram account <b>@{handle}</b>.\n"
        "Open the link while signed in as that account.\n"
        "You can still set up Aurey below with this account, "
        "but this invite will stay valid for the intended @handle."
    )


def build_start_invite_welcome_if_any(
    session: Session,
    settings: AureySettings,
    *,
    start_arg: str,
    invitee_username: str | None,
    invitee_telegram_user_id: int,
) -> str | None:
    """Return HTML for an invite deep link, or ``None`` if not an invite payload."""

    _ = settings
    row = load_invite_by_start_payload(session, start_arg=start_arg)
    if row is None:
        return None

    now = datetime.now(tz=UTC)
    expires = row.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if expires < now:
        return (
            "<b>Aurey</b>\n"
            "This invite link has expired. You can still set up Aurey below."
        )

    target = (row.target_handle_normalized or "").strip().lower()

    if not invitee_matches_invite_target(invitee_username, target):
        return format_invite_wrong_account_html(target_handle=row.target_handle_normalized)

    claimed_tid = get_handle_claim_telegram_user_id(session, handle_normalized=target)
    if claimed_tid is not None and claimed_tid != int(invitee_telegram_user_id):
        return format_handle_already_claimed_html(target_handle=row.target_handle_normalized)

    if not consume_send_invite(session, row):
        return (
            "<b>Aurey</b>\n"
            "This invite link has expired. You can still set up Aurey below."
        )

    sender_row = session.scalar(
        select(HostedPlatformUserORM).where(
            HostedPlatformUserORM.telegram_user_id == row.sender_telegram_user_id,
        ),
    )
    register_handle_claim(
        session,
        handle_normalized=target,
        telegram_user_id=int(invitee_telegram_user_id),
        invite_id=row.id,
    )

    sender_handle: str | None = None
    if sender_row is not None:
        sender_handle = format_telegram_handle(
            telegram_username=sender_row.telegram_username,
            telegram_user_id=sender_row.telegram_user_id,
        )
    return format_invite_welcome_html(
        sender_handle=sender_handle,
        target_handle=row.target_handle_normalized,
    )


def format_invite_welcome_html(
    *,
    sender_handle: str | None,
    target_handle: str,
) -> str:
    who = html.escape(sender_handle, quote=False) if sender_handle else "Someone"
    handle = html.escape((target_handle or "").strip().lstrip("@"), quote=False)
    return "\n".join(
        [
            f"<b>{who}</b> invited you to Aurey to receive crypto by Telegram handle.",
            f"This link was created for <b>@{handle}</b>.",
            "Finish setup below to get your wallet.",
        ],
    )
