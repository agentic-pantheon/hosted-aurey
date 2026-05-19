"""Orchestrate Telegram → Platform provisioning and persist hosted user rows."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from aurey.cloud.hosted_credentials import HostedVaultHttpPort, persist_hosted_agent_ocv_credentials
from aurey.cloud.models import HostedPlatformUserORM
from aurey.cloud.platform_client import OneClawPlatformClient, PlatformBootstrapResult
from aurey.cloud.wallet_sync import maybe_backfill_wallet_from_signing_keys
from aurey.settings import AureySettings


class HostedProvisioningError(RuntimeError):
    """Hosted provisioning prerequisites missing or internal consistency failure."""


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
    users still ``awaiting_claim`` always re-bootstrap so ``claim_url`` can be renewed.
    """

    if not settings.hosted_platform_enabled:
        raise HostedProvisioningError(
            "Hosted platform provisioning requires hosted_platform_enabled.",
        )

    api_key = (settings.platform_api_key or "").strip()
    template_id = (settings.platform_template_id or "").strip()
    email = synthetic_email_for_telegram_user(
        telegram_user_id=telegram_user_id,
        hosted_synthetic_email_domain=settings.hosted_synthetic_email_domain,
    )

    existing = session.scalar(
        select(HostedPlatformUserORM).where(
            HostedPlatformUserORM.telegram_user_id == telegram_user_id,
        )
    )
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

    if not connection_id:
        upsert = platform.upsert_user_synthetic_email(email=email, display_name=username)
        connection_id = upsert.connection_id

    boot = platform.bootstrap(connection_id, template_id)

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
    return row, created_or_refreshed


__all__ = [
    "HostedProvisioningError",
    "ensure_telegram_user_provisioned",
    "synthetic_email_for_telegram_user",
]
