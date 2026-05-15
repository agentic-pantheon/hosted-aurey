"""Telegram /start onboarding: upsert platform user + bootstrap template resources."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from aurey.cloud.db.models import BootstrapAttempt, OnboardingEvent, OnboardingPhase, PlatformUser
from aurey.cloud.oidc import OidcSubjectTokenSigner
from aurey.cloud.platform import OneClawPlatformApiClient
from aurey.settings import AureySettings

_LOG = logging.getLogger("aurey.cloud.onboarding")


@dataclass(frozen=True, slots=True)
class TelegramStartOutcome:
    """Result of the synchronous ``/start`` branch (no raw tokens in fields)."""

    kind: str  # "ready" | "claim" | "misconfigured" | "failed"
    message: str


def _coerce_phase(raw: str | None) -> OnboardingPhase:
    if not raw:
        return OnboardingPhase.PENDING
    try:
        return OnboardingPhase(str(raw))
    except ValueError:
        return OnboardingPhase.PENDING


def _telegram_subject(telegram_user_id: int) -> str:
    return f"telegram:{int(telegram_user_id)}"


def _bootstrap_idempotency_key(*, connection_id: str, template_id: str) -> str:
    return f"{template_id}:{connection_id}"


class OnboardingService:
    """Coordinates DB state with platform upsert/bootstrap (Phase B)."""

    def __init__(
        self,
        *,
        settings: AureySettings,
        session_factory: sessionmaker[Session],
        platform: OneClawPlatformApiClient,
        oidc: OidcSubjectTokenSigner,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._platform = platform
        self._oidc = oidc

    def run_telegram_start(
        self,
        *,
        telegram_user_id: int,
        display_name: str | None,
    ) -> TelegramStartOutcome:
        if not self._settings.cloud_onboarding_configured():
            return TelegramStartOutcome(
                kind="misconfigured",
                message="Onboarding is not fully configured for this deployment.",
            )

        ttl = int(self._settings.subject_token_ttl_seconds)
        template_id = (self._settings.plt_template_id or "").strip()
        aud = (self._settings.subject_token_audience or "").strip() or (
            self._settings.plt_app_id or ""
        ).strip()

        session = self._session_factory()
        try:
            return self._run_telegram_start_locked(
                session,
                telegram_user_id=telegram_user_id,
                display_name=display_name,
                template_id=template_id,
                subject_token_audience=aud,
                ttl=ttl,
            )
        finally:
            session.close()

    def _append_event(
        self,
        session: Session,
        *,
        user: PlatformUser,
        event_type: str,
        payload: dict[str, Any] | None,
    ) -> None:
        session.add(
            OnboardingEvent(
                platform_user_id=user.id,
                event_type=event_type,
                payload=payload,
            )
        )

    def _finalize_claim_reply(self, *, claim_url: str) -> TelegramStartOutcome:
        return TelegramStartOutcome(
            kind="claim",
            message=f"Finish Aurey wallet setup:\n\n{claim_url.strip()}",
        )

    def _run_telegram_start_locked(
        self,
        session: Session,
        *,
        telegram_user_id: int,
        display_name: str | None,
        template_id: str,
        subject_token_audience: str,
        ttl: int,
    ) -> TelegramStartOutcome:
        user_row = session.execute(
            select(PlatformUser).where(PlatformUser.telegram_user_id == int(telegram_user_id))
        ).scalar_one_or_none()

        if user_row is None:
            user_row = PlatformUser(telegram_user_id=int(telegram_user_id))
            session.add(user_row)
            session.flush()

        phase = _coerce_phase(user_row.onboarding_state)
        claim_url_existing = (user_row.claim_url or "").strip()

        if phase == OnboardingPhase.READY:
            return TelegramStartOutcome(
                kind="ready",
                message="Aurey is ready. Send a message to invoke the agent.",
            )

        if claim_url_existing:
            if user_row.onboarding_state != OnboardingPhase.READY.value:
                user_row.onboarding_state = OnboardingPhase.AWAITING_CLAIM.value
                session.commit()
            else:
                session.commit()
            return self._finalize_claim_reply(claim_url=claim_url_existing)

        try:
            subject_token = self._oidc.mint_subject_token(
                subject=_telegram_subject(telegram_user_id),
                audience=subject_token_audience,
                expires_in_seconds=ttl,
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.error("Minting subject token failed (%s)", type(exc).__name__)
            return TelegramStartOutcome(
                kind="failed",
                message="Could not prepare your session. Try again shortly.",
            )

        try:
            upsert = self._platform.upsert_user(
                subject_token=subject_token,
                display_name=display_name,
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.error("Platform upsert failed (%s)", type(exc).__name__)
            self._append_event(
                session,
                user=user_row,
                event_type="platform_upsert_failed",
                payload={"error_type": type(exc).__name__},
            )
            session.commit()
            return TelegramStartOutcome(
                kind="failed",
                message="Provisioning failed. Try again later.",
            )

        connection_id = str(upsert.get("connection_id") or "").strip()
        oneclaw_user_id = str(upsert.get("id") or upsert.get("user_id") or "").strip() or None
        if not connection_id:
            _LOG.error("Platform upsert returned no connection_id")
            self._append_event(
                session,
                user=user_row,
                event_type="platform_upsert_invalid",
                payload={"keys": sorted(upsert.keys())},
            )
            session.commit()
            return TelegramStartOutcome(
                kind="failed",
                message="Provisioning failed (invalid platform response).",
            )

        user_row.connection_id = connection_id
        if oneclaw_user_id:
            user_row.oneclaw_user_id = oneclaw_user_id
        if display_name and display_name.strip():
            user_row.display_name = display_name.strip()

        self._append_event(
            session,
            user=user_row,
            event_type="platform_upsert_ok",
            payload={"connection_id": connection_id},
        )

        idem = _bootstrap_idempotency_key(connection_id=connection_id, template_id=template_id)
        prior_attempt = session.execute(
            select(BootstrapAttempt).where(BootstrapAttempt.idempotency_key == idem)
        ).scalar_one_or_none()
        if prior_attempt is not None and prior_attempt.succeeded:
            claim = (user_row.claim_url or "").strip()
            session.commit()
            if claim:
                return self._finalize_claim_reply(claim_url=claim)
            _LOG.error("BootstrapAttempt succeeded but platform_users.claim_url is empty")
            return TelegramStartOutcome(
                kind="failed",
                message="Provisioning state is inconsistent. Contact support.",
            )

        try:
            boot = self._platform.bootstrap_connection(
                connection_id=connection_id,
                template_id=template_id,
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.error("Platform bootstrap failed (%s)", type(exc).__name__)
            self._append_event(
                session,
                user=user_row,
                event_type="platform_bootstrap_failed",
                payload={"error_type": type(exc).__name__, "connection_id": connection_id},
            )
            session.commit()
            return TelegramStartOutcome(
                kind="failed",
                message="Could not finish wallet provisioning. Try again later.",
            )

        claim_url = str(boot.get("claim_url") or "").strip()
        vault_id = str(boot.get("vault_id") or "").strip() or None
        agent_id = str(boot.get("agent_id") or "").strip() or None

        if not claim_url:
            _LOG.error("Platform bootstrap returned no claim_url")
            self._append_event(
                session,
                user=user_row,
                event_type="platform_bootstrap_invalid",
                payload={"connection_id": connection_id, "keys": sorted(boot.keys())},
            )
            session.commit()
            return TelegramStartOutcome(
                kind="failed",
                message="Provisioning failed (invalid platform response).",
            )

        user_row.claim_url = claim_url
        user_row.vault_id = vault_id
        user_row.agent_id = agent_id
        user_row.onboarding_state = OnboardingPhase.AWAITING_CLAIM.value

        self._append_event(
            session,
            user=user_row,
            event_type="platform_bootstrap_ok",
            payload={"connection_id": connection_id, "vault_id": vault_id, "agent_id": agent_id},
        )

        attempt = BootstrapAttempt(
            connection_id=connection_id,
            template_id=template_id,
            idempotency_key=idem,
            succeeded=True,
            platform_user_id=user_row.id,
        )
        session.add(attempt)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            _LOG.info("Bootstrap idempotency collision; reloading user row.")
            refreshed = session.execute(
                select(PlatformUser).where(PlatformUser.telegram_user_id == int(telegram_user_id))
            ).scalar_one()
            claim = (refreshed.claim_url or "").strip()
            if claim:
                return self._finalize_claim_reply(claim_url=claim)
            return TelegramStartOutcome(
                kind="failed",
                message="Provisioning conflict. Try again shortly.",
            )

        return self._finalize_claim_reply(claim_url=claim_url)


__all__ = ["OnboardingService", "TelegramStartOutcome"]
