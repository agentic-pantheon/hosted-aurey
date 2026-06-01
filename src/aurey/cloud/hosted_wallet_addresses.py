"""Resolve persisted hosted user wallet addresses (EVM + Solana) for agent tools."""

from __future__ import annotations

import logging
from typing import Any, Literal

from sqlalchemy import select

from aurey.cloud.models import HostedPlatformUserORM
from aurey.cloud.platform_client import OneClawPlatformClient
from aurey.cloud.signing_context import (
    current_hosted_signing_context,
    current_hosted_telegram_user_id,
)
from aurey.cloud.wallet_sync import maybe_backfill_hosted_wallet_columns_from_signing_keys
from aurey.graphs.evm_codec import to_checksum_evm_address
from aurey.runtime import AureyRuntime

_log = logging.getLogger(__name__)

HostedWalletChain = Literal["ethereum", "solana", "all"]


def _resolve_telegram_user_id() -> int | None:
    tid = current_hosted_telegram_user_id.get()
    if tid is not None:
        return tid
    ctx = current_hosted_signing_context.get()
    if ctx is not None:
        return ctx.telegram_user_id
    return None


def lookup_hosted_wallet_addresses(
    runtime: AureyRuntime,
    *,
    chain: HostedWalletChain = "all",
    telegram_user_id: int | None = None,
) -> dict[str, Any]:
    """Load hosted row addresses; lazy-backfill from signing-keys when a column is empty."""

    settings = runtime.settings
    if not settings.hosted_platform_enabled:
        return {
            "ok": False,
            "error": {
                "code": "hosted_disabled",
                "message": "Hosted platform mode is not enabled on this deployment.",
            },
        }
    factory = runtime.hosted_session_factory
    if factory is None:
        return {
            "ok": False,
            "error": {
                "code": "hosted_database_unconfigured",
                "message": "Hosted user database is not configured.",
            },
        }
    tid = telegram_user_id if telegram_user_id is not None else _resolve_telegram_user_id()
    if tid is None:
        return {
            "ok": False,
            "error": {
                "code": "hosted_telegram_context_required",
                "message": (
                    "This tool requires a Telegram-hosted chat context "
                    "(telegram_user_id not bound for this turn)."
                ),
            },
        }

    want_eth = chain in ("ethereum", "all")
    want_sol = chain in ("solana", "all")
    backfilled = False

    db = factory()
    try:
        row = db.scalar(
            select(HostedPlatformUserORM).where(
                HostedPlatformUserORM.telegram_user_id == tid,
            )
        )
        if row is None:
            return {
                "ok": False,
                "error": {
                    "code": "hosted_user_not_found",
                    "message": "No hosted platform profile exists for this Telegram user yet.",
                },
            }

        aid = (row.user_agent_id or "").strip()
        need_evm = want_eth and not (row.wallet_address or "").strip()
        need_sol = want_sol and not (row.solana_wallet_address or "").strip()
        if (need_evm or need_sol) and not aid:
            return {
                "ok": False,
                "error": {
                    "code": "user_agent_missing",
                    "message": "Wallet addresses are not provisioned yet (missing user agent).",
                },
            }
        if need_evm or need_sol:
            plat = OneClawPlatformClient.from_settings(settings)
            eth_w, sol_w = maybe_backfill_hosted_wallet_columns_from_signing_keys(
                db,
                plat,
                row,
                force_evm=need_evm,
                force_sol=need_sol,
                reason="tool_get_hosted_wallet_addresses",
                settings=settings,
                oneclaw_http=runtime.oneclaw_evm_signer,
            )
            if eth_w is not None or sol_w is not None:
                backfilled = True
                db.commit()
            else:
                db.rollback()

        eth_out: str | None = None
        sol_out: str | None = None
        if want_eth:
            wa = (row.wallet_address or "").strip()
            if wa:
                try:
                    eth_out = to_checksum_evm_address(wa)
                except ValueError:
                    eth_out = wa
        if want_sol:
            sol_raw = (row.solana_wallet_address or "").strip()
            sol_out = sol_raw or None

        result: dict[str, Any] = {
            "source": "signing_keys_backfill" if backfilled else "database",
            "telegram_user_id": tid,
        }
        if want_eth:
            result["ethereum"] = eth_out
        if want_sol:
            result["solana"] = sol_out
        if want_sol and sol_out is None:
            return {
                "ok": False,
                "error": {
                    "code": "solana_address_unavailable",
                    "message": (
                        "No Solana signing key address was returned from the platform "
                        "for this user."
                    ),
                },
            }
        return {"ok": True, "result": result}
    except Exception:
        db.rollback()
        _log.debug("lookup_hosted_wallet_addresses failed", exc_info=True)
        raise
    finally:
        db.close()


__all__ = ["HostedWalletChain", "lookup_hosted_wallet_addresses"]
