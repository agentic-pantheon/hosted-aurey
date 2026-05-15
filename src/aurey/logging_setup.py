"""Colored console logging for local runs (Rich)."""

from __future__ import annotations

import logging
import os
from typing import Any

_LOGGERS_QUIET: tuple[tuple[str, int], ...] = (
    ("httpx", logging.WARNING),
    ("httpcore", logging.WARNING),
    ("h11", logging.WARNING),
    ("openai", logging.WARNING),
    ("openai._base_client", logging.WARNING),
    ("langchain", logging.WARNING),
    ("langchain_core", logging.WARNING),
    ("langgraph", logging.WARNING),
    ("telegram", logging.INFO),
    ("telegram.ext", logging.INFO),
    ("http.client", logging.WARNING),
)


def _stderr_console() -> Any:
    from rich.console import Console

    force_color = os.environ.get("AUREY_LOG_FORCE_COLOR", "").lower() in ("1", "true", "yes")
    if force_color:
        return Console(stderr=True, force_terminal=True)
    return Console(stderr=True)


def configure_aurey_console_logging(
    *,
    level: int = logging.INFO,
    rich_tracebacks: bool = True,
) -> None:
    """Use one Rich handler on the root logger; reduces noise from HTTP / SDK loggers."""

    try:
        from rich.logging import RichHandler
    except ImportError as exc:
        raise RuntimeError(
            "Install package 'rich' for colored logging (included in aurey dependencies)."
        ) from exc

    logging.captureWarnings(True)

    root = logging.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)

    root.setLevel(level)

    handler = RichHandler(
        console=_stderr_console(),
        show_time=True,
        show_level=True,
        show_path=False,
        rich_tracebacks=rich_tracebacks,
        tracebacks_show_locals=False,
        markup=False,
        log_time_format="%Y-%m-%d %H:%M:%S",
        omit_repeated_times=False,
    )
    handler.setLevel(level)
    root.addHandler(handler)

    for name, lg_level in _LOGGERS_QUIET:
        logging.getLogger(name).setLevel(lg_level)

    logging.getLogger("asyncio").setLevel(logging.WARNING)


def uvicorn_log_config_propagate_only() -> dict[str, Any]:
    """Avoid duplicate streams: Uvicorn loggers propagate to the root Rich handler."""

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "handlers": {},
        "formatters": {},
        "loggers": {
            "uvicorn": {"handlers": [], "level": "INFO", "propagate": True},
            "uvicorn.error": {"handlers": [], "level": "INFO", "propagate": True},
            "uvicorn.access": {"handlers": [], "level": "INFO", "propagate": True},
        },
    }


__all__ = [
    "configure_aurey_console_logging",
    "uvicorn_log_config_propagate_only",
]
