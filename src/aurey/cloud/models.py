"""SQLAlchemy ORM for hosted Platform metadata (Telegram user provisioning)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
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
    connection_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    claim_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    onboarding_state: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="awaiting_claim",
        server_default=text("'awaiting_claim'"),
    )
    email: Mapped[str | None] = mapped_column(String(512), nullable=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_claim_email_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    vault_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    user_agent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    agent_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
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


class HostedEmailVerificationORM(Base):
    """Ephemeral OTP row for verifying an email before Platform upsert."""

    __tablename__ = "hosted_email_verifications"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    hosted_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("hosted_platform_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(512), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class HostedAccessRequestORM(Base):
    """Access request from a Telegram user whose chat is outside the allowlist."""

    __tablename__ = "hosted_access_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    telegram_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    contact_email: Mapped[str] = mapped_column(String(320), nullable=False)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class TokenRegistryORM(Base):
    """Per-chain token metadata for symbol and address resolution."""

    __tablename__ = "token_registry"
    __table_args__ = (
        UniqueConstraint("chain_slug", "address"),
        Index("ix_token_registry_chain_slug_symbol", "chain_slug", "symbol"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    ecosystem: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="evm",
        server_default=text("'evm'"),
    )
    chain_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    chain_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    decimals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    coingecko_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    market_cap_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    trust_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    verified_onchain: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    cg_recognized: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
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


__all__ = [
    "Base",
    "HostedAccessRequestORM",
    "HostedEmailVerificationORM",
    "HostedPlatformUserORM",
    "TokenRegistryORM",
]
