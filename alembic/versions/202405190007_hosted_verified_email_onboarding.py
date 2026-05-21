"""Hosted verified email onboarding (Telegram).

Revision ID: 202405190007
Revises: 202405160006
Create Date: 2026-05-19

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202405190007"
down_revision = "202405160006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "hosted_platform_users",
        "connection_id",
        existing_type=sa.String(length=512),
        nullable=True,
    )
    op.alter_column(
        "hosted_platform_users",
        "claim_url",
        existing_type=sa.Text(),
        nullable=True,
    )
    op.add_column(
        "hosted_platform_users",
        sa.Column("email", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "hosted_platform_users",
        sa.Column(
            "email_verified_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "hosted_platform_users",
        sa.Column(
            "last_claim_email_sent_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.create_table(
        "hosted_email_verifications",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "hosted_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "hosted_platform_users.id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("email", sa.String(length=512), nullable=False),
        sa.Column("code_hash", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        op.f("ix_hosted_email_verifications_hosted_user_id"),
        "hosted_email_verifications",
        ["hosted_user_id"],
        unique=False,
    )

    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX ix_hosted_platform_users_verified_email_lower
            ON hosted_platform_users (lower(email))
            WHERE email IS NOT NULL AND email_verified_at IS NOT NULL
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_hosted_platform_users_verified_email_lower"))
    op.drop_index(
        op.f("ix_hosted_email_verifications_hosted_user_id"),
        table_name="hosted_email_verifications",
    )
    op.drop_table("hosted_email_verifications")
    op.drop_column("hosted_platform_users", "last_claim_email_sent_at")
    op.drop_column("hosted_platform_users", "email_verified_at")
    op.drop_column("hosted_platform_users", "email")

    op.execute(
        sa.text(
            """
            UPDATE hosted_platform_users SET connection_id = ''
            WHERE connection_id IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE hosted_platform_users SET claim_url = ''
            WHERE claim_url IS NULL
            """
        )
    )
    op.alter_column(
        "hosted_platform_users",
        "connection_id",
        existing_type=sa.String(length=512),
        nullable=False,
    )
    op.alter_column(
        "hosted_platform_users",
        "claim_url",
        existing_type=sa.Text(),
        nullable=False,
    )
