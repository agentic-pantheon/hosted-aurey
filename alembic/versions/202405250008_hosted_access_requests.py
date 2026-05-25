"""Create hosted_access_requests

Revision ID: 202405250008
Revises: 202405190007
Create Date: 2026-05-25

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202405250008"
down_revision = "202405190007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hosted_access_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_username", sa.String(length=255), nullable=True),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("contact_email", sa.String(length=320), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_user_id"),
    )
    op.create_index(
        op.f("ix_hosted_access_requests_telegram_user_id"),
        "hosted_access_requests",
        ["telegram_user_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_hosted_access_requests_telegram_user_id"),
        table_name="hosted_access_requests",
    )
    op.drop_table("hosted_access_requests")
