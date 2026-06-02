"""Stable LangGraph thread ids for multi-turn conversations."""

from __future__ import annotations

from datetime import UTC, datetime


def telegram_daily_thread_id(
    chat_id: int | str,
    *,
    at: datetime | None = None,
) -> str:
    """Thread id for Telegram: one checkpoint thread per chat per UTC calendar day."""

    when = at or datetime.now(UTC)
    day = when.strftime("%Y-%m-%d")
    return f"telegram:{chat_id}:{day}"


__all__ = ["telegram_daily_thread_id"]
