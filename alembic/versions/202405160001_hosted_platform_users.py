"""Create hosted_platform_users

Revision ID: 202405160001
Revises:
Create Date: 2026-05-16

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202405160001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hosted_platform_users",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_username", sa.String(length=255), nullable=True),
        sa.Column("connection_id", sa.String(length=512), nullable=False),
        sa.Column("claim_url", sa.Text(), nullable=False),
        sa.Column(
            "onboarding_state",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'awaiting_claim'"),
        ),
        sa.Column("vault_id", sa.String(length=255), nullable=True),
        sa.Column("user_agent_id", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        op.f("ix_hosted_platform_users_telegram_user_id"),
        "hosted_platform_users",
        ["telegram_user_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_hosted_platform_users_telegram_user_id"),
        table_name="hosted_platform_users",
    )
    op.drop_table("hosted_platform_users")
