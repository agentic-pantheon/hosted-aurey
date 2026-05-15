"""SQLAlchemy models for platform users and onboarding audit."""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class OnboardingPhase(enum.StrEnum):
    PENDING = "pending"
    AWAITING_CLAIM = "awaiting_claim"
    READY = "ready"


class PlatformUser(Base):
    __tablename__ = "platform_users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger(), unique=True, index=True)

    oneclaw_user_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    connection_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    onboarding_state: Mapped[str] = mapped_column(
        String(32), default=OnboardingPhase.PENDING.value, nullable=False
    )

    claim_url: Mapped[str | None] = mapped_column(Text(), nullable=True)
    vault_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    agent_id: Mapped[str | None] = mapped_column(String(96), nullable=True)

    provisioned_signing_key_chains: Mapped[list[Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )

    # Delegated-grant reference only (vault path / logical locator). Never store raw tokens.
    grant_ref_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    grant_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    display_name: Mapped[str | None] = mapped_column(String(256), nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    events: Mapped[list[OnboardingEvent]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class OnboardingEvent(Base):
    __tablename__ = "onboarding_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    platform_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("platform_users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    user: Mapped[PlatformUser] = relationship(back_populates="events")


class BootstrapAttempt(Base):
    __tablename__ = "bootstrap_attempts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    connection_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    template_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    succeeded: Mapped[bool] = mapped_column(default=False, nullable=False)
    platform_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("platform_users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
