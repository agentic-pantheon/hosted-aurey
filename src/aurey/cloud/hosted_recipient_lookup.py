"""Resolve another hosted Aurey user by Telegram @handle for peer transfers."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aurey.cloud.hosted_access import format_telegram_handle, normalize_telegram_username
from aurey.cloud.hosted_handle_claim import get_handle_claim_telegram_user_id
from aurey.cloud.hosted_send_invite import (
    attach_invite_to_error,
    build_bot_onboarding_deeplink,
    try_create_invite_for_not_found,
)
from aurey.cloud.models import HostedPlatformUserORM
from aurey.cloud.peer_transfer_context import PeerTransferRecipient, set_peer_transfer_recipient
from aurey.cloud.signing_context import (
    current_aurey_invoke_context,
    current_hosted_signing_context,
    current_hosted_telegram_user_id,
)
from aurey.graphs.evm_codec import to_checksum_evm_address
from aurey.runtime import AureyRuntime
from aurey.settings import AureySettings

__all__ = ["lookup_hosted_recipient_by_telegram_handle"]

_INVITE_HINT_NOT_FOUND = (
    "Share this link with the recipient so they can start Aurey and receive payments."
)
_INVITE_HINT_WALLET_UNAVAILABLE = (
    "Share this link so they can open Aurey and finish wallet setup (no funds sent yet)."
)
_INVITE_HINT_BOT_ONLY = (
    "Share this bot link so they can open Aurey and finish setup (no handle-specific invite)."
)


def _invite_sender_telegram_user_id() -> int | None:
    tid = current_hosted_telegram_user_id.get()
    if tid is not None:
        return tid
    hctx = current_hosted_signing_context.get()
    if hctx is not None:
        return hctx.telegram_user_id
    ctx = current_aurey_invoke_context.get()
    if ctx is not None:
        raw = ctx.get("telegram_user_id")
        if raw is not None:
            try:
                return int(str(raw).strip())
            except ValueError:
                pass
    return None


def _attach_sender_invite(
    session: Session,
    settings: AureySettings,
    err: dict[str, Any],
    *,
    target_handle_normalized: str,
    hint: str,
    allow_bot_only_fallback: bool,
) -> None:
    sender_tid = _invite_sender_telegram_user_id()
    invite_extra = try_create_invite_for_not_found(
        session,
        settings,
        sender_telegram_user_id=sender_tid,
        target_handle_normalized=target_handle_normalized,
    )
    attach_invite_to_error(err, invite_extra, hint=hint)
    if not err.get("invite_deeplink") and allow_bot_only_fallback:
        fallback = build_bot_onboarding_deeplink(settings)
        if fallback:
            err["invite_deeplink"] = fallback
            err["invite_hint"] = _INVITE_HINT_BOT_ONLY
    if not err.get("invite_deeplink"):
        if sender_tid is None:
            err["invite_unavailable_reason"] = "sender_telegram_context_missing"
        else:
            err["invite_unavailable_reason"] = "telegram_bot_username_not_configured"


def _error_payload_with_invite(err: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": False, "error": err}
    link = err.get("invite_deeplink")
    if isinstance(link, str) and link.strip():
        out["invite_deeplink"] = link.strip()
        hint = err.get("invite_hint")
        if isinstance(hint, str) and hint.strip():
            out["invite_hint"] = hint.strip()
    return out


def _hosted_db_unavailable(runtime: AureyRuntime) -> dict[str, Any] | None:
    settings = runtime.settings
    if not settings.hosted_platform_enabled:
        return {
            "ok": False,
            "error": {
                "code": "hosted_disabled",
                "message": "Hosted platform mode is not enabled on this deployment.",
            },
        }
    if runtime.hosted_session_factory is None:
        return {
            "ok": False,
            "error": {
                "code": "hosted_database_unconfigured",
                "message": "Hosted user database is not configured.",
            },
        }
    return None


def _resolve_hosted_row_for_handle(
    session: Session,
    *,
    normalized: str,
) -> tuple[HostedPlatformUserORM | None, bool, bool]:
    """Return ``(row, resolved_via_handle_claim, ambiguous_username_match)``."""

    claim_tid = get_handle_claim_telegram_user_id(session, handle_normalized=normalized)
    if claim_tid is not None:
        row = session.scalar(
            select(HostedPlatformUserORM).where(
                HostedPlatformUserORM.telegram_user_id == claim_tid,
            ),
        )
        return row, True, False

    matches = list(
        session.scalars(
            select(HostedPlatformUserORM).where(
                HostedPlatformUserORM.telegram_username.isnot(None),
                func.lower(HostedPlatformUserORM.telegram_username) == normalized,
            ),
        ).all(),
    )
    if len(matches) > 1:
        return None, False, True
    if len(matches) == 1:
        return matches[0], False, False
    return None, False, False


def _success_payload(
    row: HostedPlatformUserORM,
    *,
    eth: str,
    resolved_via_handle_claim: bool,
    requested_handle: str,
) -> dict[str, Any]:
    display = format_telegram_handle(
        telegram_username=row.telegram_username,
        telegram_user_id=row.telegram_user_id,
    )
    set_peer_transfer_recipient(
        PeerTransferRecipient(
            telegram_user_id=int(row.telegram_user_id),
            telegram_handle=display,
        ),
    )
    result: dict[str, Any] = {
        "telegram_handle": display,
        "telegram_user_id": int(row.telegram_user_id),
        "ethereum": eth,
        "to_address": eth,
        "resolved_via_handle_claim": resolved_via_handle_claim,
        "requested_handle": f"@{requested_handle}",
    }
    if resolved_via_handle_claim:
        result["recipient_binding_note"] = (
            f"@{requested_handle} is bound to Telegram user id {row.telegram_user_id} "
            "in Aurey (invite claim). Confirm with the recipient before sending."
        )
    return {"ok": True, "result": result}


def lookup_hosted_recipient_by_telegram_handle(
    runtime: AureyRuntime,
    *,
    telegram_handle: str,
) -> dict[str, Any]:
    """Find a hosted user's EVM wallet by Telegram username (case-insensitive)."""

    blocked = _hosted_db_unavailable(runtime)
    if blocked is not None:
        return blocked

    normalized = normalize_telegram_username(telegram_handle)
    if normalized is None:
        return {
            "ok": False,
            "error": {
                "code": "invalid_telegram_handle",
                "message": "Provide a non-empty Telegram handle (with or without @).",
            },
        }

    factory = runtime.hosted_session_factory
    assert factory is not None
    db = factory()
    try:
        row, via_claim, ambiguous = _resolve_hosted_row_for_handle(db, normalized=normalized)

        if ambiguous:
            return {
                "ok": False,
                "error": {
                    "code": "recipient_ambiguous",
                    "message": (
                        f"Multiple Aurey profiles match @{normalized}; "
                        "cannot choose a recipient automatically."
                    ),
                },
            }

        if row is None:
            if via_claim:
                return {
                    "ok": False,
                    "error": {
                        "code": "recipient_claim_unprovisioned",
                        "message": (
                            f"@{normalized} is claimed in Aurey but that user has not "
                            "finished bot setup yet."
                        ),
                    },
                }
            err: dict[str, Any] = {
                "code": "recipient_not_found",
                "message": (
                    f"No Aurey user with Telegram handle @{normalized}. "
                    "They must start the bot first; share an invite link if offered."
                ),
            }
            _attach_sender_invite(
                db,
                runtime.settings,
                err,
                target_handle_normalized=normalized,
                hint=_INVITE_HINT_NOT_FOUND,
                allow_bot_only_fallback=True,
            )
            db.commit()
            return _error_payload_with_invite(err)

        wa_raw = (row.wallet_address or "").strip()
        display = format_telegram_handle(
            telegram_username=row.telegram_username,
            telegram_user_id=row.telegram_user_id,
        )
        if not wa_raw:
            err_wallet: dict[str, Any] = {
                "code": "recipient_wallet_unavailable",
                "message": (
                    f"Aurey user {display} does not have an EVM wallet address on file yet."
                ),
            }
            _attach_sender_invite(
                db,
                runtime.settings,
                err_wallet,
                target_handle_normalized=normalized,
                hint=_INVITE_HINT_WALLET_UNAVAILABLE,
                allow_bot_only_fallback=True,
            )
            db.commit()
            return _error_payload_with_invite(err_wallet)
        try:
            eth = to_checksum_evm_address(wa_raw)
        except ValueError:
            return {
                "ok": False,
                "error": {
                    "code": "recipient_wallet_invalid",
                    "message": f"Aurey user {display} has an invalid stored EVM address.",
                },
            }

        return _success_payload(
            row,
            eth=eth,
            resolved_via_handle_claim=via_claim,
            requested_handle=normalized,
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
