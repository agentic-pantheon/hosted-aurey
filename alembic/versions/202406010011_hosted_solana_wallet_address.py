"""Add solana_wallet_address to hosted_platform_users

Revision ID: 202406010012
Revises: 202406010011
Create Date: 2026-06-01

"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "202406010012"
down_revision = "202406010011"
branch_labels = None
depends_on = None


def _column_names(table: str) -> set[str]:
    bind = op.get_bind()
    return {c["name"] for c in inspect(bind).get_columns(table)}


def upgrade() -> None:
    if "solana_wallet_address" in _column_names("hosted_platform_users"):
        return
    op.add_column(
        "hosted_platform_users",
        sa.Column("solana_wallet_address", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    if "solana_wallet_address" not in _column_names("hosted_platform_users"):
        return
    op.drop_column("hosted_platform_users", "solana_wallet_address")
