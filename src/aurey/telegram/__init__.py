"""Optional Telegram bot client for Aurey."""

from aurey.telegram.client import (
    TelegramConfigurationError,
    build_telegram_application,
    create_telegram_application,
    format_telegram_message,
    handle_telegram_text,
    hosted_signing_context_for_telegram_user,
    resolve_telegram_bot_token,
    telegram_message_chunks,
)

__all__ = [
    "TelegramConfigurationError",
    "build_telegram_application",
    "create_telegram_application",
    "format_telegram_message",
    "handle_telegram_text",
    "hosted_signing_context_for_telegram_user",
    "resolve_telegram_bot_token",
    "telegram_message_chunks",
]
