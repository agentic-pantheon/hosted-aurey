"""Access requests from Telegram chats outside ``AUREY_TELEGRAM_ALLOWED_CHAT_IDS``."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

_log = logging.getLogger(__name__)

from aurey.cloud.hosted_email import (
    HostedEmailError,
    normalize_contact_email,
    send_operator_access_request_email,
)
from aurey.cloud.models import HostedAccessRequestORM
from aurey.settings import AureySettings

_TELEGRAM_ACCESS_FLOW_KEY = "aurey_telegram_access_request_flow"
_TELEGRAM_ACCESS_AWAITING_EMAIL = "awaiting_contact_email"


class HostedAccessRequestError(ValueError):
    """Invalid user input during the access-request flow."""


def format_telegram_handle(*, telegram_username: str | None, telegram_user_id: int) -> str:
    if telegram_username and str(telegram_username).strip():
        handle = str(telegram_username).strip().lstrip("@")
        return f"@{handle}"
    return f"(no @username, id {telegram_user_id})"


def telegram_access_request_awaiting_email(user_data: dict[str, object]) -> bool:
    return user_data.get(_TELEGRAM_ACCESS_FLOW_KEY) == _TELEGRAM_ACCESS_AWAITING_EMAIL


def mark_telegram_access_request_awaiting_email(user_data: dict[str, object]) -> None:
    user_data[_TELEGRAM_ACCESS_FLOW_KEY] = _TELEGRAM_ACCESS_AWAITING_EMAIL


def clear_telegram_access_request_flow(user_data: dict[str, object]) -> None:
    user_data.pop(_TELEGRAM_ACCESS_FLOW_KEY, None)


def get_pending_access_request(
    session: Session,
    *,
    telegram_user_id: int,
) -> HostedAccessRequestORM | None:
    return session.scalar(
        select(HostedAccessRequestORM).where(
            HostedAccessRequestORM.telegram_user_id == telegram_user_id,
        )
    )


def require_contact_email(raw: str) -> str:
    email = normalize_contact_email(raw)
    if email is None:
        raise HostedAccessRequestError(
            "That doesn't look like a valid email. Send one address only."
        )
    return email


def submit_telegram_access_request(
    session: Session,
    settings: AureySettings,
    *,
    telegram_user_id: int,
    telegram_username: str | None,
    contact_email: str,
    telegram_chat_id: int | None,
) -> HostedAccessRequestORM:
    """Persist request and notify operator. Idempotent after successful delivery."""

    existing = get_pending_access_request(session, telegram_user_id=telegram_user_id)
    if existing is not None and existing.notified_at is not None:
        return existing

    dest = settings.hosted_operator_registration_notify_email
    if dest is None or not str(dest).strip():
        raise HostedEmailError(
            "Operator notify email is not configured "
            "(set AUREY_HOSTED_OPERATOR_REGISTRATION_NOTIFY_EMAIL)."
        )

    handle = format_telegram_handle(
        telegram_username=telegram_username,
        telegram_user_id=telegram_user_id,
    )
    if existing is None:
        row = HostedAccessRequestORM(
            telegram_user_id=telegram_user_id,
            telegram_username=(telegram_username or "").strip() or None,
            telegram_chat_id=telegram_chat_id,
            contact_email=contact_email,
            notified_at=None,
        )
        session.add(row)
        session.flush()
    else:
        row = existing
        row.contact_email = contact_email
        row.telegram_chat_id = telegram_chat_id
        if telegram_username is not None:
            row.telegram_username = (telegram_username or "").strip() or None
        session.flush()

    try:
        send_operator_access_request_email(
            settings,
            to_email=str(dest).strip(),
            contact_email=contact_email,
            telegram_handle=handle,
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_chat_id,
        )
    except HostedEmailError as exc:
        _log.warning(
            "Access-request operator email failed telegram_user_id=%s chat_id=%s: %s",
            telegram_user_id,
            telegram_chat_id,
            exc,
        )
        session.rollback()
        raise

    row.notified_at = datetime.now(UTC)
    session.flush()
    return row


def delete_telegram_access_request(
    session: Session,
    *,
    telegram_user_id: int,
) -> None:
    """Remove stored beta access request after the user's chat is allowlisted."""

    row = get_pending_access_request(session, telegram_user_id=telegram_user_id)
    if row is not None:
        session.delete(row)


def telegram_access_request_intro_message(*, telegram_handle: str) -> str:
    return (
        "Aurey is in limited beta for approved chats only.\n\n"
        f"Your Telegram handle: {telegram_handle}\n\n"
        "Reply with the email address we should use to contact you about access. "
        "We'll notify the team and follow up when you're approved."
    )


def telegram_access_request_pending_message() -> str:
    return (
        "Your access request is already on file. We'll reach out by email when you're "
        "approved.\n\n"
        "Once you're approved, send /start again in this chat — you don't need to delete "
        "the conversation. If /start still shows this message, the server allowlist may "
        "not include your Telegram chat id yet (ops adds it from the access-request email)."
    )


def telegram_access_request_submitted_message() -> str:
    return (
        "Thanks — your request was sent. We'll email you when your chat is approved. "
        "Until then, Aurey isn't available here."
    )


def _access_request_email_delivery_failure_message(settings: AureySettings) -> str:
    if not settings.hosted_email_smtp_configured():
        return (
            "Could not deliver your access request — outbound email is not configured on "
            "this server (AUREY_HOSTED_SMTP_HOST). Please try again later."
        )
    return (
        "Could not deliver your access request right now. "
        "Please try again in a few minutes."
    )


def _attempt_submit_access_request(
    db: Session,
    cfg: AureySettings,
    *,
    telegram_user_id: int,
    telegram_username: str | None,
    contact_email: str,
    telegram_chat_id: int | None,
    user_data: dict[str, object],
) -> str | None:
    """Submit and return user reply text, or ``None`` when email delivery failed."""

    try:
        submit_telegram_access_request(
            db,
            cfg,
            telegram_user_id=telegram_user_id,
            telegram_username=telegram_username,
            contact_email=contact_email,
            telegram_chat_id=telegram_chat_id,
        )
        db.commit()
        clear_telegram_access_request_flow(user_data)
        return telegram_access_request_submitted_message()
    except HostedEmailError:
        db.rollback()
        return _access_request_email_delivery_failure_message(cfg)


def telegram_access_request_flow_step(
    state: object,
    *,
    telegram_user_id: int,
    telegram_username: str | None,
    telegram_chat_id: int | None,
    message_text: str | None,
    user_data: dict[str, object],
) -> str:
    """Collect email and notify operator (sync; run via ``asyncio.to_thread``)."""

    cfg = getattr(state, "settings", None)
    factory = getattr(state, "hosted_session_factory", None)
    if cfg is None:
        raise TypeError("state must expose settings and hosted_session_factory")
    if not (cfg.database_url or "").strip() or factory is None:
        return (
            "Aurey is in limited beta and this chat is not approved yet. "
            "Access requests require a configured database on this deployment."
        )

    handle = format_telegram_handle(
        telegram_username=telegram_username,
        telegram_user_id=telegram_user_id,
    )

    db = factory()
    try:
        pending = get_pending_access_request(db, telegram_user_id=telegram_user_id)
        if pending is not None and pending.notified_at is not None:
            return telegram_access_request_pending_message()

        if message_text is not None:
            contact_email = normalize_contact_email(message_text)
            if contact_email is not None:
                submitted = _attempt_submit_access_request(
                    db,
                    cfg,
                    telegram_user_id=telegram_user_id,
                    telegram_username=telegram_username,
                    contact_email=contact_email,
                    telegram_chat_id=telegram_chat_id,
                    user_data=user_data,
                )
                if submitted is not None:
                    return submitted
            elif telegram_access_request_awaiting_email(user_data):
                try:
                    require_contact_email(message_text)
                except HostedAccessRequestError as exc:
                    return str(exc)

        mark_telegram_access_request_awaiting_email(user_data)
        db.rollback()
        return telegram_access_request_intro_message(telegram_handle=handle)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


__all__ = [
    "HostedAccessRequestError",
    "clear_telegram_access_request_flow",
    "delete_telegram_access_request",
    "format_telegram_handle",
    "get_pending_access_request",
    "mark_telegram_access_request_awaiting_email",
    "require_contact_email",
    "submit_telegram_access_request",
    "telegram_access_request_awaiting_email",
    "telegram_access_request_flow_step",
    "telegram_access_request_intro_message",
    "telegram_access_request_pending_message",
    "telegram_access_request_submitted_message",
]
