"""hosted_handle_claims: bind @handle to telegram_user_id

Revision ID: 202406020013
Revises: 202406020012
Create Date: 2026-06-02

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202406020013"
down_revision = "202406020012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hosted_handle_claims",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("handle_normalized", sa.String(length=255), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("source_invite_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "claimed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_invite_id"],
            ["hosted_send_invites.id"],
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        op.f("ix_hosted_handle_claims_handle_normalized"),
        "hosted_handle_claims",
        ["handle_normalized"],
        unique=True,
    )
    op.create_index(
        op.f("ix_hosted_handle_claims_telegram_user_id"),
        "hosted_handle_claims",
        ["telegram_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_hosted_handle_claims_telegram_user_id"),
        table_name="hosted_handle_claims",
    )
    op.drop_index(
        op.f("ix_hosted_handle_claims_handle_normalized"),
        table_name="hosted_handle_claims",
    )
    op.drop_table("hosted_handle_claims")
