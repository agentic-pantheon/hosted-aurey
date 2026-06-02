"""Add sender_notified_at to hosted_send_invites

Revision ID: 202406020014
Revises: 202406020013
Create Date: 2026-06-02

"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "202406020014"
down_revision = "202406020013"
branch_labels = None
depends_on = None


def _column_names(table: str) -> set[str]:
    bind = op.get_bind()
    return {c["name"] for c in inspect(bind).get_columns(table)}


def upgrade() -> None:
    if "sender_notified_at" in _column_names("hosted_send_invites"):
        return
    op.add_column(
        "hosted_send_invites",
        sa.Column("sender_notified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    if "sender_notified_at" not in _column_names("hosted_send_invites"):
        return
    op.drop_column("hosted_send_invites", "sender_notified_at")
