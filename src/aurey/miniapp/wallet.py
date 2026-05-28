"""Resolve Mini App user's EVM wallet from hosted Postgres."""

from __future__ import annotations

from dataclasses import dataclass

from aurey.cloud.hosted_wallet_lookup import load_hosted_platform_user_row_for_telegram
from aurey.graphs.evm_codec import to_checksum_evm_address
from aurey.service.state import AureyServiceState


@dataclass(frozen=True)
class ResolvedMiniappUser:
    has_row: bool
    onboarding_state: str | None
    wallet_address: str | None


def resolve_wallet_for_telegram_user(
    state: AureyServiceState,
    *,
    telegram_user_id: int,
) -> ResolvedMiniappUser:
    """Load hosted profile and return persisted wallet checksummed when valid.

    Signing readiness (``onboarding_state == ready``) is **not** required for portfolio viewing.
    """

    cfg = state.settings
    if not cfg.hosted_platform_enabled:
        return ResolvedMiniappUser(has_row=False, onboarding_state=None, wallet_address=None)
    factory = state.hosted_session_factory
    if factory is None:
        return ResolvedMiniappUser(has_row=False, onboarding_state=None, wallet_address=None)

    db = factory()
    try:
        row = load_hosted_platform_user_row_for_telegram(
            db,
            cfg,
            telegram_user_id=telegram_user_id,
            reason="miniapp_portfolio",
            allow_wallet_backfill=False,
        )
        if row is None:
            return ResolvedMiniappUser(has_row=False, onboarding_state=None, wallet_address=None)
        st = (row.onboarding_state or "").strip() or None
        wa_raw = (row.wallet_address or "").strip()
        if not wa_raw:
            return ResolvedMiniappUser(has_row=True, onboarding_state=st, wallet_address=None)
        try:
            cs = to_checksum_evm_address(wa_raw)
        except ValueError:
            return ResolvedMiniappUser(has_row=True, onboarding_state=st, wallet_address=None)
        return ResolvedMiniappUser(has_row=True, onboarding_state=st, wallet_address=cs)
    finally:
        db.close()


__all__ = ["ResolvedMiniappUser", "resolve_wallet_for_telegram_user"]
