from datetime import UTC, datetime

from aurey.reasoning.conversation_thread import telegram_daily_thread_id


def test_telegram_daily_thread_id_uses_utc_day():
    at = datetime(2026, 6, 2, 23, 59, tzinfo=UTC)
    assert telegram_daily_thread_id(12345, at=at) == "telegram:12345:2026-06-02"


def test_telegram_daily_thread_id_accepts_string_chat_id():
    at = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    assert telegram_daily_thread_id("-10099", at=at) == "telegram:-10099:2026-01-01"
