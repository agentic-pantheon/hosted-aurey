"""LangGraph Postgres checkpointer expects a plain ``postgresql://`` URI for psycopg."""

from aurey.reasoning.checkpointer import _conn_string_for_langgraph_raw_psycopg


def test_strip_postgresql_psycopg_scheme() -> None:
    raw = "postgresql+psycopg://aurey:secret@127.0.0.1:5433/aurey"
    assert _conn_string_for_langgraph_raw_psycopg(raw) == "postgresql://aurey:secret@127.0.0.1:5433/aurey"


def test_strip_postgres_psycopg_scheme() -> None:
    raw = "postgres+psycopg://localhost/db"
    assert _conn_string_for_langgraph_raw_psycopg(raw) == "postgres://localhost/db"


def test_leave_plain_postgresql_unchanged() -> None:
    raw = "postgresql://u:p@localhost:5432/db"
    assert _conn_string_for_langgraph_raw_psycopg(raw) == raw
