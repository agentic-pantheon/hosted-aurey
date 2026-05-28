"""Validate Telegram Mini App ``initData`` (HMAC-SHA256) per Bot API docs."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl


class TelegramWebAppAuthError(Exception):
    """initData failed structural check, HMAC, or freshness."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TelegramWebAppUser:
    telegram_user_id: int
    username: str | None


def validate_telegram_webapp_init_data(
    *,
    init_data_raw: str,
    bot_token: str,
    max_age_seconds: int | None = 86400,
    max_future_skew_seconds: int = 60,
    now_unix: float | None = None,
) -> TelegramWebAppUser:
    """Parse and verify ``Telegram.WebApp.initData``. Raises :class:`TelegramWebAppAuthError` on failure."""

    raw = (init_data_raw or "").strip()
    token = (bot_token or "").strip()
    if not raw:
        raise TelegramWebAppAuthError("empty_init_data", "init_data is empty.")
    if not token:
        raise TelegramWebAppAuthError("bot_token_missing", "bot token is not configured.")

    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=False)
    except ValueError:
        raise TelegramWebAppAuthError("parse_error", "init_data is not valid query form data.") from None

    kv: dict[str, str] = {k: v for k, v in pairs}

    recv_hash_hex = kv.get("hash") or kv.get("Hash")
    if not recv_hash_hex:
        raise TelegramWebAppAuthError("hash_missing", "init_data lacks hash.")

    auth_date_raw = kv.get("auth_date")
    if auth_date_raw is None or not str(auth_date_raw).strip().isdigit():
        raise TelegramWebAppAuthError("auth_date_missing", "init_data lacks auth_date.")

    auth_date = int(str(auth_date_raw).strip())

    clock = float(now_unix) if now_unix is not None else time.time()
    skew = max(0, int(max_future_skew_seconds))
    if auth_date > int(clock) + skew:
        raise TelegramWebAppAuthError("auth_date_in_future", "init_data auth_date is in the future.")

    if max_age_seconds is not None and max_age_seconds > 0:
        if clock - auth_date > max_age_seconds:
            raise TelegramWebAppAuthError("auth_date_expired", "init_data auth_date is too old.")

    data_check_pairs = sorted((k, v) for k, v in kv.items() if k != "hash")
    data_check_string = "\n".join(f"{k}={v}" for k, v in data_check_pairs)

    # secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token)
    secret_key = hmac.new(
        b"WebAppData",
        msg=token.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    expected_hash = hmac.new(
        secret_key,
        msg=data_check_string.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_hash, str(recv_hash_hex).strip().lower()):
        raise TelegramWebAppAuthError("signature_mismatch", "init_data signature is invalid.")

    user_json_raw = kv.get("user") or ""
    user_obj: dict[str, Any]
    try:
        user_obj = json.loads(user_json_raw) if user_json_raw else {}
        if not isinstance(user_obj, dict):
            raise TypeError()
    except (json.JSONDecodeError, TypeError):
        raise TelegramWebAppAuthError("user_missing", "init_data lacks valid user JSON.") from None

    uid_any = user_obj.get("id")
    try:
        uid = int(uid_any)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise TelegramWebAppAuthError("user_id_invalid", "user.id is missing or not an integer.") from None

    uname = user_obj.get("username")
    username = str(uname).strip() if isinstance(uname, str) and str(uname).strip() else None

    return TelegramWebAppUser(telegram_user_id=uid, username=username)


__all__ = [
    "TelegramWebAppAuthError",
    "TelegramWebAppUser",
    "validate_telegram_webapp_init_data",
]
