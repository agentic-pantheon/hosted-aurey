"""Security hardening for Mini App (rate limits, cache, Zerion pagination)."""

from __future__ import annotations

import time

import pytest

from aurey.miniapp.portfolio_cache_server import PortfolioSnapshotCache
from aurey.miniapp.rate_limit import SlidingWindowRateLimiter
from aurey.miniapp.schemas import (
    PortfolioSummary,
    PortfolioSnapshot,
    utc_now_iso,
)
from aurey.miniapp.zerion_client import _coerce_zerion_next_url, is_allowed_zerion_api_url
from aurey.telegram.webapp_auth import TelegramWebAppAuthError, validate_telegram_webapp_init_data
from tests.test_telegram_webapp_auth import _signed_init_data


def test_sliding_window_rate_limiter_blocks():
    rl = SlidingWindowRateLimiter(max_events=2, window_seconds=60.0)
    t = 1000.0
    assert rl.allow("a", now=t)
    assert rl.allow("a", now=t + 0.1)
    assert not rl.allow("a", now=t + 0.2)
    assert rl.allow("a", now=t + 61.0)


def test_portfolio_snapshot_cache_ttl():
    cache = PortfolioSnapshotCache(max_entries=4)
    snap = PortfolioSnapshot(
        wallet_address="0x1111111111111111111111111111111111111111",
        updated_at=utc_now_iso(),
        chains_queried=("base",),
        chains_available=("base",),
        summary=PortfolioSummary(total_usd="1", by_chain=[]),
        tokens=[],
        defi=[],
    )
    key = cache.cache_key(telegram_user_id=1, wallet_address=snap.wallet_address, chains=("base",))
    t = 500.0
    cache.set(key, snap, ttl_seconds=10.0, now=t)
    assert cache.get(key, now=t + 5) is snap
    assert cache.get(key, now=t + 11) is None


def test_zerion_next_url_only_official_host():
    assert is_allowed_zerion_api_url(
        "https://api.zerion.io/v1/wallets/0xabc/positions/?cursor=1",
    )
    assert _coerce_zerion_next_url("https://evil.example/next") is None
    assert _coerce_zerion_next_url("http://api.zerion.io/v1/x") is None


def test_init_data_rejects_future_auth_date():
    bot = "555:ABCDEF"
    now = int(time.time())
    raw = _signed_init_data(bot_token=bot, user_id=1, auth_date=now + 3600)
    with pytest.raises(TelegramWebAppAuthError) as ei:
        validate_telegram_webapp_init_data(
            init_data_raw=raw,
            bot_token=bot,
            max_future_skew_seconds=60,
            now_unix=float(now),
        )
    assert ei.value.code == "auth_date_in_future"
