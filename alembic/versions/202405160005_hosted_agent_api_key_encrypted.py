"""Add encrypted backup column for hosted per-user agent API keys.

Revision ID: 202405160005
Revises: 202405160004
Create Date: 2026-05-19

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202405160005"
down_revision = "202405160004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "hosted_platform_users",
        sa.Column("agent_api_key_encrypted", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("hosted_platform_users", "agent_api_key_encrypted")
