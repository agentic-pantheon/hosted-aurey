"""hosted_send_invites + index on hosted_platform_users.telegram_username

Revision ID: 202406020012
Revises: 202406010012
Create Date: 2026-06-02

"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "202406020012"
down_revision = "202406010012"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    bind = op.get_bind()
    return set(inspect(bind).get_table_names())


def _index_names(table: str) -> set[str]:
    bind = op.get_bind()
    return {idx["name"] for idx in inspect(bind).get_indexes(table)}


def upgrade() -> None:
    if "hosted_send_invites" not in _table_names():
        op.create_table(
            "hosted_send_invites",
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("token", sa.String(length=64), nullable=False),
            sa.Column("sender_telegram_user_id", sa.BigInteger(), nullable=False),
            sa.Column("target_handle_normalized", sa.String(length=255), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
        )
    invite_indexes = _index_names("hosted_send_invites") if "hosted_send_invites" in _table_names() else set()
    token_ix = op.f("ix_hosted_send_invites_token")
    if token_ix not in invite_indexes:
        op.create_index(
            token_ix,
            "hosted_send_invites",
            ["token"],
            unique=True,
        )

    user_ix = op.f("ix_hosted_platform_users_telegram_username")
    if user_ix not in _index_names("hosted_platform_users"):
        op.create_index(
            user_ix,
            "hosted_platform_users",
            ["telegram_username"],
            unique=False,
        )


def downgrade() -> None:
    user_ix = op.f("ix_hosted_platform_users_telegram_username")
    if user_ix in _index_names("hosted_platform_users"):
        op.drop_index(user_ix, table_name="hosted_platform_users")
    if "hosted_send_invites" in _table_names():
        token_ix = op.f("ix_hosted_send_invites_token")
        if token_ix in _index_names("hosted_send_invites"):
            op.drop_index(token_ix, table_name="hosted_send_invites")
        op.drop_table("hosted_send_invites")
