"""Telegram Mini App ``initData`` validation."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

from aurey.telegram.webapp_auth import (
    TelegramWebAppAuthError,
    validate_telegram_webapp_init_data,
)


def _signed_init_data(
    *,
    bot_token: str,
    user_id: int,
    auth_date: int | None = None,
) -> str:
    from urllib.parse import urlencode

    ad = auth_date if auth_date is not None else int(time.time())
    user_json = json.dumps({"id": user_id}, separators=(",", ":"))
    fields = {"auth_date": str(ad), "user": user_json}
    data_check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret_key = hmac.new(
        b"WebAppData",
        msg=bot_token.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    sig = hmac.new(
        secret_key,
        msg=data_check_string.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()
    fields["hash"] = sig
    return urlencode(fields)


def test_validate_webapp_ok():
    bot = "555:ABCDEF"
    now = int(time.time())
    raw = _signed_init_data(bot_token=bot, user_id=991, auth_date=now - 60)
    u = validate_telegram_webapp_init_data(init_data_raw=raw, bot_token=bot)
    assert u.telegram_user_id == 991


def test_validate_wrong_bot_token():
    """Signature is keyed to the BotFather token."""

    signer = "555:ABCDEF"
    wrong = "555:OTHER"
    now = int(time.time())
    raw = _signed_init_data(bot_token=signer, user_id=1, auth_date=now)
    with pytest.raises(TelegramWebAppAuthError) as ei:
        validate_telegram_webapp_init_data(init_data_raw=raw, bot_token=wrong)
    assert ei.value.code == "signature_mismatch"


def test_validate_expired():
    bot = "555:ABCDEF"
    raw = _signed_init_data(bot_token=bot, user_id=1, auth_date=1)
    now = float(time.time())
    with pytest.raises(TelegramWebAppAuthError) as ei:
        validate_telegram_webapp_init_data(
            init_data_raw=raw,
            bot_token=bot,
            max_age_seconds=100,
            now_unix=now,
        )
    assert ei.value.code == "auth_date_expired"


def test_validate_accepts_small_clock_skew():
    bot = "555:ABCDEF"
    now = int(time.time())
    raw = _signed_init_data(bot_token=bot, user_id=7, auth_date=now + 30)
    u = validate_telegram_webapp_init_data(
        init_data_raw=raw,
        bot_token=bot,
        max_future_skew_seconds=60,
        now_unix=float(now),
    )
    assert u.telegram_user_id == 7
