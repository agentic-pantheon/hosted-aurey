"""SQLAlchemy ORM for hosted Platform metadata (Telegram user provisioning)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, Text, Uuid, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for hosted cloud metadata tables."""


class HostedPlatformUserORM(Base):
    """Maps a Telegram user to a Platform connection and claim flow state."""

    __tablename__ = "hosted_platform_users"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    telegram_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    connection_id: Mapped[str] = mapped_column(String(512), nullable=False)
    claim_url: Mapped[str] = mapped_column(Text, nullable=False)
    onboarding_state: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="awaiting_claim",
        server_default=text("'awaiting_claim'"),
    )
    vault_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    user_agent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    agent_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    delegation_subject_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    wallet_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


__all__ = ["Base", "HostedPlatformUserORM"]
