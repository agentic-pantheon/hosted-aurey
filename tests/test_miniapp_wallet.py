"""Mini App wallet resolver skips signing-keys backfill."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from aurey.miniapp.wallet import resolve_wallet_for_telegram_user


def test_miniapp_resolve_skips_wallet_backfill():
    state = MagicMock()
    state.settings.hosted_platform_enabled = True
    state.hosted_session_factory = MagicMock()
    db = MagicMock()
    state.hosted_session_factory.return_value = db

    with patch(
        "aurey.miniapp.wallet.load_hosted_platform_user_row_for_telegram",
        return_value=None,
    ) as load:
        resolve_wallet_for_telegram_user(state, telegram_user_id=42)
        load.assert_called_once()
        assert load.call_args.kwargs.get("allow_wallet_backfill") is False
