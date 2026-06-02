"""Run Aurey's Telegram bot (long polling). This is separate from the HTTP server (Uvicorn).

Sync deps: ``uv sync --group dev --extra telegram``
To keep Uvicorn too: ``uv sync --group dev --extra telegram --extra api``
"""

from __future__ import annotations

import argparse
import logging

from aurey.logging_setup import configure_aurey_console_logging, resolve_log_level

_log = logging.getLogger("aurey.telegram.runner")


def main() -> None:
    parser = argparse.ArgumentParser(description="Aurey Telegram + Rich console logs")
    parser.add_argument(
        "--log-level",
        default=None,
        help="Root log level (default: AUREY_LOG_LEVEL or info). Debug floods RPC logs.",
    )
    args = parser.parse_args()

    if args.log_level:
        level = getattr(logging, args.log_level.upper(), logging.INFO)
    else:
        level = resolve_log_level(default=logging.INFO)
    configure_aurey_console_logging(level=level)

    from telegram.error import Conflict

    from aurey.telegram import create_telegram_application

    _log.info("Building Telegram application …")
    app = create_telegram_application()
    _log.info(
        "Starting long polling (Ctrl+C to stop). Per-message traces use logger aurey.turn. "
        "If you see Conflict: terminate every other runner using this bot token (second "
        "terminal, staging deploy, another IDE task)."
    )
    try:
        app.run_polling()
    except Conflict as exc:
        _log.error(
            "Telegram rejected long polling (Conflict): another process already calls "
            "getUpdates for this bot. Stop duplicates (another run_telegram.py, Cursor "
            "terminal, Railway worker with polling, …) — only one poller may run."
        )
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
