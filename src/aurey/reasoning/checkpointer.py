"""LangGraph checkpoint helpers (in-memory session/thread identity)."""

from __future__ import annotations

import sys
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver


def make_memory_checkpointer() -> BaseCheckpointSaver:
    """Return a fresh in-memory saver; key runs with ``config['configurable']['thread_id']``."""

    return MemorySaver()


@dataclass
class ManagedPostgresCheckpointer:
    """Postgres-backed LangGraph saver plus context manager for teardown."""

    saver: BaseCheckpointSaver
    _cm: AbstractContextManager[Any]

    def close(self) -> None:
        self._cm.__exit__(None, None, None)


def _conn_string_for_langgraph_raw_psycopg(conn_str: str) -> str:
    """Strip SQLAlchemy's ``+psycopg`` driver suffix for LangGraph / raw psycopg.

    LangGraph's :class:`~langgraph.checkpoint.postgres.PostgresSaver` passes this
    string to ``psycopg.Connection.connect``. That parser accepts ``postgresql://``
    URIs but rejects SQLAlchemy forms like ``postgresql+psycopg://``.
    """

    url = conn_str.strip()
    for prefix_sqla, prefix_plain in (
        ("postgresql+psycopg://", "postgresql://"),
        ("postgres+psycopg://", "postgres://"),
    ):
        if url.startswith(prefix_sqla):
            return prefix_plain + url[len(prefix_sqla) :]
    return url


def open_postgres_checkpointer(conn_str: str) -> ManagedPostgresCheckpointer:
    """Open ``PostgresSaver`` from URI, run ``setup()`` for DDL, return a closable handle."""

    from langgraph.checkpoint.postgres import PostgresSaver

    cm = PostgresSaver.from_conn_string(_conn_string_for_langgraph_raw_psycopg(conn_str))
    saver = cm.__enter__()
    try:
        saver.setup()
    except BaseException:
        cm.__exit__(*sys.exc_info())
        raise
    return ManagedPostgresCheckpointer(saver=saver, _cm=cm)


def thread_config(session_id: str, **extra: Any) -> dict[str, Any]:
    """Build ``invoke`` / ``ainvoke`` config with a stable thread id plus optional fields."""

    return {"configurable": {"thread_id": session_id, **extra}}


__all__ = [
    "ManagedPostgresCheckpointer",
    "make_memory_checkpointer",
    "open_postgres_checkpointer",
    "thread_config",
]
