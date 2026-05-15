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
from aurey.cloud.onboarding.claim_parser import parse_connected_user_claim_ready
from aurey.cloud.onboarding.grant_repository import GrantReferenceRepository
from aurey.cloud.onboarding.state_machine import (
    InvalidOnboardingTransition,
    assert_transition_allowed,
    coerce_phase_value,
)
from aurey.cloud.platform import OneClawPlatformApiClient
from aurey.principal import UserPrincipal
from aurey.settings import AureySettings

_LOG = logging.getLogger("aurey.cloud.onboarding")


@dataclass(frozen=True, slots=True)
class TelegramStartOutcome:
    """Result of the synchronous ``/start`` branch (no raw tokens in fields)."""

    kind: str  # "ready" | "claim" | "misconfigured" | "failed"
    message: str


def _telegram_subject(telegram_user_id: int) -> str:
    return f"telegram:{int(telegram_user_id)}"


def _bootstrap_idempotency_key(*, connection_id: str, template_id: str) -> str:
    return f"{template_id}:{connection_id}"


def _signing_key_chains_from_bootstrap(boot: dict[str, Any]) -> list[str] | None:
    raw = boot.get("signing_key_chains")
    if not isinstance(raw, list) or not raw:
        return None
    out: list[str] = []
    for x in raw:
        if isinstance(x, str) and x.strip():
            out.append(x.strip())
    return out or None


class OnboardingService:
    """Coordinates DB state with platform upsert/bootstrap and claim polling (Phase B–C)."""

    def __init__(
        self,
        *,
        settings: AureySettings,
        session_factory: sessionmaker[Session],
        platform: OneClawPlatformApiClient,
        oidc: OidcSubjectTokenSigner,
        grant_repository: GrantReferenceRepository,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._platform = platform
        self._oidc = oidc
        self._grants = grant_repository

    def _grant_ref_path_for_user(
        self,
        *,
        vault_id: str | None,
        connection_id: str,
        agent_id: str | None,
    ) -> str:
        """Resolvable operator-vault locator for delegated grant JWT material.

        Prefer :attr:`~aurey.settings.AureySettings.hosted_user_grant_secret_path_template`
        when set; otherwise a synthetic ID-only path (unlikely to exist in vault).
        """

        custom = self._settings.format_hosted_user_grant_secret_path(
            vault_id=vault_id,
            connection_id=connection_id,
            agent_id=agent_id,
        )
        if custom.strip():
            return custom.strip()
        vault = (vault_id or "").strip() or "unknown_vault"
        cid = (connection_id or "").strip()
        aid = (agent_id or "").strip()
        base = f"vaults/{vault}/delegated_grants/connections/{cid}"
        return f"{base}/agent/{aid}" if aid else base

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

    def blocking_agent_message_for_telegram_user(self, telegram_user_id: int | None) -> str | None:
        """When non-empty, regular Telegram turns should not invoke the deep agent yet."""

        if telegram_user_id is None:
            return None
        if not self._settings.cloud_onboarding_configured():
            return None

        session = self._session_factory()
        try:
            row = session.execute(
                select(PlatformUser).where(PlatformUser.telegram_user_id == int(telegram_user_id))
            ).scalar_one_or_none()
            if row is None:
                return None
            phase = coerce_phase_value(row.onboarding_state)
            if phase != OnboardingPhase.AWAITING_CLAIM:
                return None
            claim = (row.claim_url or "").strip()
            if not claim:
                return (
                    "Your Aurey wallet setup is not ready yet. Send /start again to refresh your "
                    "onboarding link."
                )
            return (
                "Finish wallet setup first: open the claim link from /start, complete the flow in "
                "your browser, wait a few seconds, then send /start again. I stay quiet until "
                "that step succeeds."
            )
        finally:
            session.close()

    def user_principal_for_telegram_user(self, telegram_user_id: int) -> UserPrincipal | None:
        """Return a hosted principal when the Telegram user is ``ready`` with signing metadata."""

        if not self._settings.cloud_onboarding_configured():
            return None

        session = self._session_factory()
        try:
            row = session.execute(
                select(PlatformUser).where(PlatformUser.telegram_user_id == int(telegram_user_id))
            ).scalar_one_or_none()
            if row is None:
                return None
            if coerce_phase_value(row.onboarding_state) != OnboardingPhase.READY:
                return None
            aid = (row.agent_id or "").strip()
            gpath = (row.grant_ref_path or "").strip()
            if not aid or not gpath:
                return None
            wallet: str | None = None
            meta = row.grant_metadata if isinstance(row.grant_metadata, dict) else None
            if meta:
                w = meta.get("wallet_address") or meta.get("wallet")
                if isinstance(w, str) and w.strip():
                    wallet = w.strip()
            return UserPrincipal(
                db_user_id=str(row.id),
                user_agent_id=aid,
                grant_ref_path=gpath,
                wallet_address=wallet,
            )
        finally:
            session.close()

    def poll_awaiting_claims(self, *, batch_limit: int = 50) -> int:
        """Poll platform connection state for users stuck in ``awaiting_claim``.

        Returns the number of users transitioned to ``ready`` this tick.
        """

        if not self._settings.cloud_onboarding_configured():
            return 0

        limit = max(1, int(batch_limit))
        transitioned = 0
        session = self._session_factory()
        try:
            app_id = (self._settings.plt_app_id or "").strip()
            by_connection: dict[str, dict[str, Any]] | None = None
            users_list_failure_reason: str | None = None
            if not app_id:
                users_list_failure_reason = "missing_plt_app_id"
            else:
                try:
                    member_rows = self._platform.list_app_connected_users(app_id=app_id)
                except Exception as exc:  # noqa: BLE001
                    _LOG.warning(
                        "Claim poll could not list platform users (%s)",
                        type(exc).__name__,
                    )
                    users_list_failure_reason = "platform_users_list_unavailable"
                    member_rows = None
                else:
                    by_connection = {}
                    for rec in member_rows:
                        if not isinstance(rec, dict):
                            continue
                        cc = str(rec.get("connection_id") or "").strip()
                        if cc:
                            by_connection[cc] = rec
            rows = session.scalars(
                select(PlatformUser)
                .where(PlatformUser.onboarding_state == OnboardingPhase.AWAITING_CLAIM.value)
                .order_by(PlatformUser.updated_at.asc())
                .limit(limit)
            ).all()
            for user in rows:
                cid = (user.connection_id or "").strip()
                if not cid:
                    self._append_event(
                        session,
                        user=user,
                        event_type="claim_poll_skipped",
                        payload={"reason": "missing_connection_id"},
                    )
                    session.commit()
                    continue

                if users_list_failure_reason is not None:
                    self._append_event(
                        session,
                        user=user,
                        event_type="claim_poll_users_list_failed",
                        payload={"reason": users_list_failure_reason},
                    )
                    session.commit()
                    continue

                if by_connection is None:
                    _LOG.error("Claim poll: users index missing after successful list fetch")
                    session.commit()
                    continue
                rec = by_connection.get(cid)
                if rec is None:
                    self._append_event(
                        session,
                        user=user,
                        event_type="claim_poll_missing_user_row",
                        payload={"connection_id": cid},
                    )
                    session.commit()
                    continue

                parsed = parse_connected_user_claim_ready(rec)
                if not parsed.ready:
                    self._append_event(
                        session,
                        user=user,
                        event_type="claim_poll_not_ready",
                        payload={"matched_keys": list(parsed.matched_keys)},
                    )
                    session.commit()
                    continue

                grant_path = self._grant_ref_path_for_user(
                    vault_id=user.vault_id,
                    connection_id=cid,
                    agent_id=user.agent_id,
                )
                meta = {
                    "source": "poll",
                    "signal_keys": list(parsed.matched_keys),
                }
                if self._try_transition_to_ready(
                    session,
                    user=user,
                    event_type="claim_detected",
                    event_payload={
                        "connection_id": cid,
                        "signal_keys": list(parsed.matched_keys),
                    },
                    grant_path=grant_path,
                    grant_metadata=meta,
                ):
                    transitioned += 1
                session.commit()
            return transitioned
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

    def _apply_phase(
        self,
        session: Session,
        *,
        user: PlatformUser,
        to_phase: OnboardingPhase,
        event_type: str,
        event_payload: dict[str, Any] | None,
    ) -> None:
        current = coerce_phase_value(user.onboarding_state)
        if current == to_phase:
            return
        assert_transition_allowed(from_phase=current, to_phase=to_phase)
        user.onboarding_state = to_phase.value
        self._append_event(session, user=user, event_type=event_type, payload=event_payload)

    def _try_transition_to_ready(
        self,
        session: Session,
        *,
        user: PlatformUser,
        event_type: str,
        event_payload: dict[str, Any] | None,
        grant_path: str,
        grant_metadata: dict[str, Any],
    ) -> bool:
        current = coerce_phase_value(user.onboarding_state)
        if current == OnboardingPhase.READY:
            return False
        try:
            assert_transition_allowed(from_phase=current, to_phase=OnboardingPhase.READY)
        except InvalidOnboardingTransition:
            _LOG.error(
                "Ignored illegal ready transition for user %s (phase=%s)",
                user.id,
                current.value,
            )
            self._append_event(
                session,
                user=user,
                event_type="claim_ready_ignored_invalid_phase",
                payload={"phase": current.value},
            )
            return False

        self._grants.save_grant_reference(
            session,
            user=user,
            grant_ref_path=grant_path,
            metadata=grant_metadata,
        )
        user.onboarding_state = OnboardingPhase.READY.value
        self._append_event(session, user=user, event_type=event_type, payload=event_payload)
        return True

    def _finalize_claim_reply(self, *, claim_url: str) -> TelegramStartOutcome:
        return TelegramStartOutcome(
            kind="claim",
            message=(
                "Wallet setup needed before Aurey can run on-chain actions.\n\n"
                "1) Open this link and finish the browser flow:\n"
                f"{claim_url.strip()}\n\n"
                "2) When the page confirms you're done, return here and send /start again.\n\n"
                "Until setup completes, I'll only reply with this guidance so you don't hit errors "
                "mid-flow."
            ),
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

        phase = coerce_phase_value(user_row.onboarding_state)
        claim_url_existing = (user_row.claim_url or "").strip()

        if phase == OnboardingPhase.READY:
            return TelegramStartOutcome(
                kind="ready",
                message=(
                    "Aurey is ready — wallet setup is complete. Send any message to talk to the "
                    "agent."
                ),
            )

        if claim_url_existing:
            prior_phase = phase
            if user_row.onboarding_state != OnboardingPhase.AWAITING_CLAIM.value:
                try:
                    self._apply_phase(
                        session,
                        user=user_row,
                        to_phase=OnboardingPhase.AWAITING_CLAIM,
                        event_type="onboarding_phase_coerced_awaiting_claim",
                        event_payload={"from_phase": prior_phase.value},
                    )
                except InvalidOnboardingTransition:
                    session.commit()
                    return TelegramStartOutcome(
                        kind="failed",
                        message="Your onboarding state looks inconsistent. Contact support.",
                    )
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
        chains = _signing_key_chains_from_bootstrap(boot)
        if chains is not None:
            user_row.provisioned_signing_key_chains = chains
        try:
            self._apply_phase(
                session,
                user=user_row,
                to_phase=OnboardingPhase.AWAITING_CLAIM,
                event_type="platform_bootstrap_ok",
                event_payload={
                    "connection_id": connection_id,
                    "vault_id": vault_id,
                    "agent_id": agent_id,
                    "signing_key_chain_count": len(chains) if chains else 0,
                },
            )
        except InvalidOnboardingTransition as exc:
            _LOG.error("Bootstrap phase transition rejected: %s", exc)
            session.rollback()
            return TelegramStartOutcome(
                kind="failed",
                message="Provisioning failed (unexpected onboarding state).",
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
