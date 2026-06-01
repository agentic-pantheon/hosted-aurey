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
    _cm: AbstractContextManager[Any] | None
    _pool: Any | None = None

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None
        if self._cm is not None:
            self._cm.__exit__(None, None, None)
            self._cm = None


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


def open_postgres_checkpointer(
    conn_str: str,
    *,
    min_size: int = 1,
    max_size: int = 10,
) -> ManagedPostgresCheckpointer:
    """Open ``PostgresSaver`` from a connection pool, run ``setup()`` for DDL."""

    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    conninfo = _conn_string_for_langgraph_raw_psycopg(conn_str)
    pool = ConnectionPool(
        conninfo=conninfo,
        min_size=min_size,
        max_size=max_size,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
        open=True,
    )
    saver = PostgresSaver(pool)
    try:
        saver.setup()
    except BaseException:
        pool.close()
        raise
    return ManagedPostgresCheckpointer(saver=saver, _cm=None, _pool=pool)


def thread_config(session_id: str, **extra: Any) -> dict[str, Any]:
    """Build ``invoke`` / ``ainvoke`` config with a stable thread id plus optional fields."""

    return {"configurable": {"thread_id": session_id, **extra}}


__all__ = [
    "ManagedPostgresCheckpointer",
    "make_memory_checkpointer",
    "open_postgres_checkpointer",
    "thread_config",
]
