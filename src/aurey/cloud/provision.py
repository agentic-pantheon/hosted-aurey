"""Orchestrate Telegram → Platform provisioning and persist hosted user rows."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from aurey.cloud.hosted_credentials import HostedVaultHttpPort, persist_hosted_agent_ocv_credentials
from aurey.cloud.hosted_email import (
    HostedEmailError,
    send_claim_invite_email,
    send_operator_new_registration_email,
)
from aurey.cloud.models import HostedPlatformUserORM
from aurey.cloud.onboarding_refresh import refresh_hosted_user_claim_state
from aurey.cloud.platform_client import (
    HostedPlatformApiError,
    OneClawPlatformClient,
    PlatformBootstrapResult,
    PlatformReissueClaimResult,
    list_signing_key_address_lines_from_payload,
)
from aurey.cloud.wallet_sync import maybe_backfill_wallet_from_signing_keys
from aurey.settings import AureySettings

_log = logging.getLogger(__name__)


class PollRefreshOnly:
    """Claim poll updated the row; bootstrap was skipped (user may still be awaiting claim)."""


POLL_REFRESH_ONLY = PollRefreshOnly()

BootstrapOutcome = PlatformBootstrapResult | None | PollRefreshOnly


class HostedProvisioningError(RuntimeError):
    """Hosted provisioning prerequisites missing or internal consistency failure."""


class HostedAwaitingEmailFlow(RuntimeError):
    """Email verification is incomplete; Telegram should prompt (/start flow), not retry Platform."""


def hosted_maybe_mail_claim_invite(
    settings: AureySettings,
    session: Session,
    row: HostedPlatformUserORM,
    *,
    prev_claim_url: str | None,
) -> None:
    """Send claim email when verified email exists and ``claim_url`` changed (throttled).

    SMTP failures log a warning — Telegram still exposes /start UX.
    """

    if not settings.hosted_require_verified_email:
        return
    if row.email_verified_at is None or not (row.email or "").strip():
        return
    if (row.onboarding_state or "").strip() != "awaiting_claim":
        return

    curl = (row.claim_url or "").strip()
    if not curl:
        return

    pv = (prev_claim_url or "").strip()
    if pv and pv == curl:
        return

    now = datetime.now(tz=UTC)
    throttle_s = settings.hosted_claim_email_throttle_seconds
    prev_sent = row.last_claim_email_sent_at
    if prev_sent is not None:
        elapsed = (now - prev_sent).total_seconds()
        if elapsed < throttle_s and pv == curl:
            return

    hint = None
    if row.telegram_username:
        hint = f"@{row.telegram_username}"

    try:
        send_claim_invite_email(
            settings,
            to_email=row.email or "",
            claim_url=curl,
            display_hint=hint,
        )
    except HostedEmailError as exc:
        _log.warning(
            "Could not deliver claim invite email telegram_user_id=%s: %s",
            row.telegram_user_id,
            exc,
        )
        return

    row.last_claim_email_sent_at = now
    session.flush()


def _telegram_handle_for_row(row: HostedPlatformUserORM) -> str:
    un = (row.telegram_username or "").strip()
    if un:
        return f"@{un.lstrip('@')}"
    return f"id:{row.telegram_user_id}"


def _wallet_address_lines_for_operator_notify(
    *,
    platform: OneClawPlatformClient | None,
    row: HostedPlatformUserORM,
    boot: PlatformBootstrapResult,
) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()

    def _add(line: str) -> None:
        if line not in seen:
            seen.add(line)
            lines.append(line)

    aid = (row.user_agent_id or boot.user_agent_id or "").strip()
    if platform is not None and aid:
        try:
            payload = platform.get_agent_signing_keys(aid)
            for line in list_signing_key_address_lines_from_payload(payload):
                _add(line)
        except HostedPlatformApiError as exc:
            _log.debug(
                "Operator notify: signing-keys unavailable agent_id=%s: %s",
                aid,
                exc,
            )

    wa = (row.wallet_address or boot.wallet_address or "").strip()
    if wa:
        _add(f"ethereum: {wa}")

    return lines


def hosted_maybe_notify_operator_new_registration(
    settings: AureySettings,
    *,
    platform: OneClawPlatformClient | None,
    row: HostedPlatformUserORM,
    boot: PlatformBootstrapResult,
    contact_email: str,
) -> None:
    """Email the operator when Platform bootstrap completes (/start flow, before claim)."""

    dest = settings.hosted_operator_registration_notify_email
    if dest is None or not str(dest).strip():
        return
    if (row.onboarding_state or "").strip() != "awaiting_claim":
        return

    user_email = (contact_email or row.email or "").strip()
    if not user_email:
        user_email = synthetic_email_for_telegram_user(
            telegram_user_id=row.telegram_user_id,
            hosted_synthetic_email_domain=settings.hosted_synthetic_email_domain,
        )

    wallet_lines = _wallet_address_lines_for_operator_notify(
        platform=platform,
        row=row,
        boot=boot,
    )
    try:
        send_operator_new_registration_email(
            settings,
            to_email=str(dest).strip(),
            user_email=user_email,
            telegram_handle=_telegram_handle_for_row(row),
            wallet_address_lines=wallet_lines,
            telegram_user_id=row.telegram_user_id,
        )
    except HostedEmailError as exc:
        _log.warning(
            "Could not deliver operator registration email telegram_user_id=%s: %s",
            row.telegram_user_id,
            exc,
        )


def effective_platform_contact_email(
    *,
    settings: AureySettings,
    telegram_user_id: int,
    row: HostedPlatformUserORM | None,
) -> str:
    """Platform ``users/upsert`` identity: verified inbox when required, else synthetic."""

    verified = (
        row is not None
        and row.email_verified_at is not None
        and (row.email or "").strip()
    )
    if settings.hosted_require_verified_email:
        if not verified:
            raise HostedAwaitingEmailFlow()
        return (row.email or "").strip().lower()

    if verified:
        return (row.email or "").strip().lower()
    return synthetic_email_for_telegram_user(
        telegram_user_id=telegram_user_id,
        hosted_synthetic_email_domain=settings.hosted_synthetic_email_domain,
    )


def ensure_hosted_telegram_row(
    session: Session,
    settings: AureySettings,
    *,
    telegram_user_id: int,
    username: str | None,
) -> HostedPlatformUserORM | None:
    """Persist minimal Telegram-hosted row when verified-email onboarding is mandatory.

    Returns ``None`` when ``hosted_require_verified_email`` is false — provisioning creates the row later.
    """

    if not settings.hosted_platform_enabled:
        raise HostedProvisioningError(
            "Hosted platform provisioning requires hosted_platform_enabled.",
        )
    existing = session.scalar(
        select(HostedPlatformUserORM).where(
            HostedPlatformUserORM.telegram_user_id == telegram_user_id,
        ),
    )
    if existing is not None:
        if username is not None and (existing.telegram_username or "") != username:
            existing.telegram_username = username
        return existing
    if not settings.hosted_require_verified_email:
        return None
    row = HostedPlatformUserORM(
        telegram_user_id=telegram_user_id,
        telegram_username=username,
        connection_id=None,
        claim_url=None,
        onboarding_state="awaiting_email",
    )
    session.add(row)
    session.flush()
    return row


def _recoverable_bootstrap_error(exc: HostedPlatformApiError) -> bool:
    code = exc.status_code
    if code is None:
        return True
    if code in (409, 422):
        return True
    return code >= 500


def _bootstrap_with_recovery(
    *,
    session: Session,
    settings: AureySettings,
    platform: OneClawPlatformClient,
    row: HostedPlatformUserORM | None,
    connection_id: str,
    template_id: str,
    upsert_identity_email: str,
    username: str | None,
) -> BootstrapOutcome:
    """Call Platform bootstrap; on conflict/server errors poll claim state when possible.

    Returns ``None`` when claim polling shows the user is already ``ready``.
    Returns ``POLL_REFRESH_ONLY`` when poll refreshed metadata / claim URL without bootstrap.
    """

    cid = (connection_id or "").strip()
    claim_before = (row.claim_url or "").strip() if row is not None else ""
    try:
        return platform.bootstrap(cid, template_id)
    except HostedPlatformApiError as exc:
        if not _recoverable_bootstrap_error(exc):
            raise
        if row is not None:
            refresh_hosted_user_claim_state(session, settings, platform, row)
            if (row.onboarding_state or "").strip() == "ready":
                return None
            claim_after = (row.claim_url or "").strip()
            if claim_after and claim_after != claim_before:
                return POLL_REFRESH_ONLY
            if _connection_bootstrapped_for_claim(row):
                _log.warning(
                    "Platform bootstrap HTTP %s for connection_id=%s; reissuing claim link.",
                    exc.status_code,
                    cid,
                )
                _reissue_claim_on_row(platform, settings, row, connection_id=cid)
                return POLL_REFRESH_ONLY
        upsert = platform.upsert_user_by_email(
            email=upsert_identity_email,
            display_name=username,
        )
        new_cid = (upsert.connection_id or "").strip()
        if new_cid and new_cid != cid:
            _log.info(
                "Platform bootstrap failed for connection_id=%s; retrying bootstrap on upsert id=%s",
                cid,
                new_cid,
            )
            if row is not None:
                row.connection_id = new_cid
            return platform.bootstrap(new_cid, template_id)
        raise HostedPlatformApiError(
            "Platform declined to renew the claim link for this connection (bootstrap failed). "
            "Claim links expire quickly—use /start again after expiry. If this persists, ask 1Claw "
            "to reset the Platform connection for this user.",
            status_code=exc.status_code,
        ) from exc


def _connection_bootstrapped_for_claim(row: HostedPlatformUserORM) -> bool:
    return bool((row.user_agent_id or "").strip() and (row.connection_id or "").strip())


def _reissue_claim_on_row(
    platform: OneClawPlatformClient,
    settings: AureySettings,
    row: HostedPlatformUserORM,
    *,
    connection_id: str,
) -> PlatformReissueClaimResult:
    cid = (connection_id or row.connection_id or "").strip()
    return_to = (settings.platform_claim_return_to or "").strip() or None
    issued = platform.reissue_claim(cid, return_to=return_to)
    row.claim_url = issued.claim_url
    if issued.connection_id:
        row.connection_id = issued.connection_id.strip()
    _log.info(
        "Platform reissue-claim connection_id=%s expires_in=%s",
        cid,
        issued.expires_in,
    )
    return issued


def _try_reissue_claim_after_poll(
    platform: OneClawPlatformClient,
    settings: AureySettings,
    row: HostedPlatformUserORM,
    *,
    connection_id: str,
) -> bool:
    """Reissue when bootstrapped but still awaiting claim. Returns True when claim URL was minted."""

    if (row.onboarding_state or "").strip() != "awaiting_claim":
        return False
    if not _connection_bootstrapped_for_claim(row):
        return False
    _reissue_claim_on_row(platform, settings, row, connection_id=connection_id)
    return True


def synthetic_email_for_telegram_user(
    *,
    telegram_user_id: int,
    hosted_synthetic_email_domain: str,
) -> str:
    """Build ``tg_<id>@<domain>`` using a normalized domain."""

    domain = (hosted_synthetic_email_domain or "").strip().strip(".")
    if not domain:
        raise ValueError("hosted_synthetic_email_domain must not be empty.")
    return f"tg_{telegram_user_id}@{domain}"


def _persist_hosted_ocv_after_bootstrap(
    *,
    settings: AureySettings,
    vault_http_client: HostedVaultHttpPort | None,
    row: HostedPlatformUserORM,
    boot: PlatformBootstrapResult,
) -> None:
    raw = boot.agent_api_key
    if raw is None or not str(raw).strip():
        return
    ocv = str(raw).strip()
    ua = (row.user_agent_id or "").strip()
    if not ua:
        row.agent_api_key = ocv
        return
    if vault_http_client is not None:
        persist_hosted_agent_ocv_credentials(
            settings=settings,
            http_client=vault_http_client,
            row=row,
            ocv=ocv,
            user_agent_id=ua,
        )
    else:
        row.agent_api_key = ocv


def ensure_telegram_user_provisioned(
    session: Session,
    settings: AureySettings,
    platform: OneClawPlatformClient | None,
    *,
    telegram_user_id: int,
    username: str | None,
    vault_http_client: HostedVaultHttpPort | None = None,
) -> tuple[HostedPlatformUserORM, bool]:
    """Upsert hosted metadata and call the Platform API when the row is incomplete.

    Returns ``(row, created_or_refreshed)``. The second value is ``False`` only when the user
    is already fully onboarded (``onboarding_state`` ``ready`` with claim metadata present);
    users still ``awaiting_claim`` poll Platform claim state first, then reissue claim when
    already bootstrapped, otherwise bootstrap for first-time provisioning.
    """

    if not settings.hosted_platform_enabled:
        raise HostedProvisioningError(
            "Hosted platform provisioning requires hosted_platform_enabled.",
        )

    api_key = (settings.platform_api_key or "").strip()
    template_id = (settings.platform_template_id or "").strip()

    existing = session.scalar(
        select(HostedPlatformUserORM).where(
            HostedPlatformUserORM.telegram_user_id == telegram_user_id,
        ),
    )

    upsert_identity_email = effective_platform_contact_email(
        settings=settings,
        telegram_user_id=telegram_user_id,
        row=existing,
    )

    if settings.hosted_require_verified_email:
        if existing is None:
            raise HostedProvisioningError(
                "No hosted onboarding row — tap /start to begin email verification.",
            )
        st_early = (existing.onboarding_state or "").strip()
        if st_early in {"awaiting_email", "awaiting_email_verification"}:
            raise HostedAwaitingEmailFlow()

    if existing is not None:
        conn_ok = bool((existing.connection_id or "").strip())
        claim_ok = bool((existing.claim_url or "").strip())
        onboarding = (existing.onboarding_state or "").strip()
        if conn_ok and claim_ok and onboarding == "ready":
            if username is not None and (existing.telegram_username or "") != username:
                existing.telegram_username = username
            return existing, False

    if not api_key or not template_id:
        raise HostedProvisioningError(
            "Hosted provisioning requires non-empty platform_api_key and platform_template_id."
        )
    if platform is None:
        raise HostedProvisioningError(
            "OneClawPlatformClient is required for hosted provisioning.",
        )

    created_or_refreshed = True
    row = existing
    connection_id = (existing.connection_id or "").strip() if existing else ""

    prof_st = ((row.onboarding_state or "").strip()) if row is not None else ""
    if (
        settings.hosted_require_verified_email
        and prof_st == "email_verified"
        and connection_id == ""
        and upsert_identity_email
    ):
        upsert_first = platform.upsert_user_by_email(
            email=upsert_identity_email,
            display_name=username,
        )
        connection_id = upsert_first.connection_id.strip()
        assert row is not None
        row.connection_id = connection_id
        session.flush()

    if not connection_id:
        upsert = platform.upsert_user_by_email(email=upsert_identity_email, display_name=username)
        connection_id = upsert.connection_id

    claim_before_refresh = (row.claim_url or "").strip() if row is not None else ""
    if (
        row is not None
        and (row.onboarding_state or "").strip() == "awaiting_claim"
        and (connection_id or "").strip()
    ):
        refresh_hosted_user_claim_state(session, settings, platform, row)
        hosted_maybe_mail_claim_invite(settings, session, row, prev_claim_url=claim_before_refresh)
        claim_mid = (row.claim_url or "").strip()
        if (row.onboarding_state or "").strip() == "ready":
            if username is not None:
                row.telegram_username = username
            session.flush()
            return row, created_or_refreshed
        if claim_mid and claim_mid != claim_before_refresh:
            if username is not None:
                row.telegram_username = username
            session.flush()
            return row, created_or_refreshed
        snap_before_reissue = claim_mid
        if _try_reissue_claim_after_poll(
            platform,
            settings,
            row,
            connection_id=connection_id,
        ):
            if username is not None:
                row.telegram_username = username
            hosted_maybe_mail_claim_invite(
                settings,
                session,
                row,
                prev_claim_url=snap_before_reissue,
            )
            session.flush()
            return row, created_or_refreshed

    boot = _bootstrap_with_recovery(
        session=session,
        settings=settings,
        platform=platform,
        row=row,
        connection_id=connection_id,
        template_id=template_id,
        upsert_identity_email=upsert_identity_email,
        username=username,
    )
    if boot is None:
        assert row is not None
        if username is not None:
            row.telegram_username = username
        hosted_maybe_mail_claim_invite(settings, session, row, prev_claim_url=claim_before_refresh)
        session.flush()
        return row, created_or_refreshed
    if boot is POLL_REFRESH_ONLY:
        assert row is not None
        if username is not None:
            row.telegram_username = username
        hosted_maybe_mail_claim_invite(settings, session, row, prev_claim_url=claim_before_refresh)
        session.flush()
        return row, created_or_refreshed

    connection_id = (row.connection_id if row is not None else connection_id) or connection_id
    connection_id = connection_id.strip()

    def _merge_wallet(addr: str | None) -> None:
        """Fill wallet_address only when absent (preferred vs overwriting user-corrected data)."""

        if not addr:
            return
        if (row.wallet_address or "").strip():
            return
        row.wallet_address = addr.strip()

    if row is None:
        row = HostedPlatformUserORM(
            telegram_user_id=telegram_user_id,
            telegram_username=username,
            connection_id=connection_id,
            claim_url=boot.claim_url,
            onboarding_state="awaiting_claim",
            vault_id=boot.vault_id,
            user_agent_id=boot.user_agent_id,
        )
        _merge_wallet(boot.wallet_address)
        session.add(row)
    else:
        row.telegram_username = username
        row.connection_id = connection_id
        row.claim_url = boot.claim_url
        row.vault_id = boot.vault_id
        row.onboarding_state = "awaiting_claim"
        if boot.user_agent_id is not None:
            row.user_agent_id = boot.user_agent_id
        _merge_wallet(boot.wallet_address)

    _persist_hosted_ocv_after_bootstrap(
        settings=settings,
        vault_http_client=vault_http_client,
        row=row,
        boot=boot,
    )

    # Bootstrap JSON may omit addresses while agent id exists; fetch once when plausible.
    if platform is not None:
        maybe_backfill_wallet_from_signing_keys(
            session,
            platform,
            row,
            reason="post_bootstrap",
        )

    session.flush()

    hosted_maybe_mail_claim_invite(settings, session, row, prev_claim_url=claim_before_refresh)
    hosted_maybe_notify_operator_new_registration(
        settings,
        platform=platform,
        row=row,
        boot=boot,
        contact_email=upsert_identity_email,
    )
    session.flush()
    return row, created_or_refreshed


__all__ = [
    "HostedAwaitingEmailFlow",
    "HostedProvisioningError",
    "POLL_REFRESH_ONLY",
    "PollRefreshOnly",
    "effective_platform_contact_email",
    "ensure_hosted_telegram_row",
    "ensure_telegram_user_provisioned",
    "hosted_maybe_mail_claim_invite",
    "hosted_maybe_notify_operator_new_registration",
    "synthetic_email_for_telegram_user",
]
