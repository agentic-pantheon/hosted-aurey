"""HTTP server with the same colored logging setup as ``run_telegram.py``.

Sync: ``uv sync --group dev --extra api``
Run:  ``uv run python run_http.py --port 8001``
"""

from __future__ import annotations

import argparse
import logging

from aurey.logging_setup import configure_aurey_console_logging, uvicorn_log_config_propagate_only


def main() -> None:
    parser = argparse.ArgumentParser(description="Aurey HTTP + Rich console logs")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--log-level",
        default="debug",
        help="Root / aurey log level (debug, info, warning, …)",
    )
    parser.add_argument("--no-access-log", action="store_true")
    args = parser.parse_args()

    level = getattr(logging, args.log_level.upper(), logging.INFO)
    configure_aurey_console_logging(level=level)

    import uvicorn

    uvicorn.run(
        "aurey.service.app:app",
        factory=True,
        host=args.host,
        port=args.port,
        log_config=uvicorn_log_config_propagate_only(),
        log_level=args.log_level,
        access_log=not args.no_access_log,
    )


if __name__ == "__main__":
    main()
