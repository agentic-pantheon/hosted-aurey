"""SQLAlchemy engine and Session factory for hosted metadata (shared DB URL as checkpointer)."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from aurey.settings import AureySettings


def make_engine(settings: AureySettings) -> Engine:
    """SQLAlchemy engine using the same trimmed URL as :attr:`AureySettings.database_url`."""

    url = (settings.database_url or "").strip()
    if not url:
        raise ValueError("database_url is required to create a hosted metadata engine.")
    return create_engine(url, pool_pre_ping=True, future=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a ``sessionmaker`` — each call constructs a new :class:`~sqlalchemy.orm.Session`."""

    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


__all__ = ["make_engine", "make_session_factory"]
