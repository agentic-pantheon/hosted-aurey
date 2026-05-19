"""Add wallet_address to hosted_platform_users

Revision ID: 202405160002
Revises: 202405160001
Create Date: 2026-05-16

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202405160002"
down_revision = "202405160001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "hosted_platform_users",
        sa.Column("wallet_address", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("hosted_platform_users", "wallet_address")
