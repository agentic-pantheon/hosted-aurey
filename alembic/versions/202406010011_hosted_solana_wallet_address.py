"""Add solana_wallet_address to hosted_platform_users

Revision ID: 202406010011
Revises: 202406010010
Create Date: 2026-06-01

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202406010011"
down_revision = "202406010010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "hosted_platform_users",
        sa.Column("solana_wallet_address", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("hosted_platform_users", "solana_wallet_address")
