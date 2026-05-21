"""Persist and validate hosted email OTP challenges before Platform upsert."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from aurey.cloud.hosted_email import (
    HostedEmailError,
    generate_numeric_verification_code,
    normalize_contact_email,
    send_verification_code_email,
    verification_code_challenge_hash,
)
from aurey.cloud.models import HostedEmailVerificationORM, HostedPlatformUserORM

if TYPE_CHECKING:
    from aurey.settings import AureySettings

_log = logging.getLogger(__name__)

_MAX_EMAIL_LEN_STORED = 512


class HostedVerificationError(RuntimeError):
    """User-facing-ish verification failures (duplicate email, locked out, etc.)."""


def _purge_pending_for_hosted_user(session: Session, hosted_user_id: uuid.UUID) -> None:
    session.execute(
        delete(HostedEmailVerificationORM).where(
            HostedEmailVerificationORM.hosted_user_id == hosted_user_id
        )
    )


def verified_email_already_taken(
    session: Session, *, email_norm: str, exclude_user_id: uuid.UUID
) -> bool:
    existing = session.scalar(
        select(HostedPlatformUserORM).where(
            HostedPlatformUserORM.email == email_norm,
            HostedPlatformUserORM.email_verified_at.is_not(None),
            HostedPlatformUserORM.id != exclude_user_id,
        )
    )
    return existing is not None


def start_email_verification(
    session: Session,
    settings: AureySettings,
    row: HostedPlatformUserORM,
    raw_email: str,
) -> None:
    """Create OTP row, flip onboarding to awaiting_email_verification, send mail."""

    from aurey.settings import AureySettings as _AssertSettings  # noqa: F401 runtime import guard

    _ = _AssertSettings
    normalized = normalize_contact_email(raw_email)
    if normalized is None:
        raise HostedVerificationError("Invalid email address. Reply with a single valid email.")

    state = (row.onboarding_state or "").strip()
    if state == "ready":
        raise HostedVerificationError("You're already onboarded.")

    if verified_email_already_taken(session, email_norm=normalized, exclude_user_id=row.id):
        raise HostedVerificationError(
            "That email is already used by another Aurey user. Pick a different inbox.",
        )

    _purge_pending_for_hosted_user(session, row.id)
    ttl = settings.hosted_email_verification_ttl_seconds
    expires = datetime.now(tz=UTC) + timedelta(seconds=ttl)

    plaintext = generate_numeric_verification_code()
    try:
        digest = verification_code_challenge_hash(settings, plaintext)
    except HostedEmailError as exc:
        raise HostedVerificationError(str(exc)) from exc

    session.add(
        HostedEmailVerificationORM(
            hosted_user_id=row.id,
            email=normalized[:_MAX_EMAIL_LEN_STORED],
            code_hash=digest,
            expires_at=expires,
            attempt_count=0,
        ),
    )

    row.onboarding_state = "awaiting_email_verification"
    session.flush()

    try:
        send_verification_code_email(settings, to_email=normalized, code=plaintext)
    except HostedEmailError as exc:
        session.rollback()
        raise HostedVerificationError(str(exc)) from exc


def confirm_email_verification(
    session: Session, settings: AureySettings, row: HostedPlatformUserORM, raw_code: str
) -> None:
    """Verify OTP and mark ``email`` + ``email_verified_at``. Raises HostedVerificationError on failure."""

    state = (row.onboarding_state or "").strip()
    if state != "awaiting_email_verification":
        raise HostedVerificationError("No verification is pending — use /start or send your email.")

    cand = "".join(ch for ch in str(raw_code) if ch.isdigit())
    if len(cand) != 6:
        raise HostedVerificationError("Reply with your 6-digit code (digits only).")

    ver_row = session.scalar(
        select(HostedEmailVerificationORM)
        .where(HostedEmailVerificationORM.hosted_user_id == row.id)
        .order_by(HostedEmailVerificationORM.created_at.desc())
        .limit(1),
    )
    if ver_row is None:
        raise HostedVerificationError("Verification expired — send your email again.")

    email_norm = normalize_contact_email(ver_row.email) or ""
    if not email_norm:
        _purge_pending_for_hosted_user(session, row.id)
        row.onboarding_state = "awaiting_email"
        session.flush()
        raise HostedVerificationError("Verification data was invalid — send your email again.")

    if verified_email_already_taken(session, email_norm=email_norm, exclude_user_id=row.id):
        _purge_pending_for_hosted_user(session, row.id)
        row.onboarding_state = "awaiting_email"
        session.flush()
        raise HostedVerificationError(
            "That email was registered by someone else meanwhile. Pick a different address.",
        )

    now = datetime.now(tz=UTC)
    if ver_row.expires_at <= now:
        _purge_pending_for_hosted_user(session, row.id)
        row.onboarding_state = "awaiting_email"
        session.flush()
        raise HostedVerificationError("Code expired — send your email again.")

    mx = settings.hosted_email_verification_max_attempts
    if ver_row.attempt_count >= mx:
        _purge_pending_for_hosted_user(session, row.id)
        row.onboarding_state = "awaiting_email"
        session.flush()
        raise HostedVerificationError(
            "Too many wrong attempts — send your email again for a fresh code.",
        )

    expected = verification_code_challenge_hash(settings, cand)
    if secrets_compare_fail(ver_row.code_hash, expected):
        ver_row.attempt_count = int(ver_row.attempt_count or 0) + 1
        session.flush()
        raise HostedVerificationError("Incorrect code.")

    if verified_email_already_taken(session, email_norm=email_norm, exclude_user_id=row.id):
        _purge_pending_for_hosted_user(session, row.id)
        row.onboarding_state = "awaiting_email"
        session.flush()
        raise HostedVerificationError(
            "That email is already used by another user. Pick a different inbox.",
        )

    _purge_pending_for_hosted_user(session, row.id)
    row.email = email_norm[:_MAX_EMAIL_LEN_STORED]
    row.email_verified_at = now
    row.onboarding_state = "email_verified"
    session.flush()


def secrets_compare_fail(a: str, b: str) -> bool:
    """Timing-safe-ish comparison for hex hashes."""

    from hmac import compare_digest

    if len(a) != len(b):
        return True
    return not compare_digest(a, b)


def purge_pending_hosted_verifications(session: Session, hosted_user_id: uuid.UUID) -> None:
    session.execute(
        delete(HostedEmailVerificationORM).where(
            HostedEmailVerificationORM.hosted_user_id == hosted_user_id
        )
    )


__all__ = [
    "HostedVerificationError",
    "confirm_email_verification",
    "purge_pending_hosted_verifications",
    "start_email_verification",
    "verified_email_already_taken",
]
