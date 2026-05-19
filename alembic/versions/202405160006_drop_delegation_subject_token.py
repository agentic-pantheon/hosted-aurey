"""Drop delegation_subject_token (staging /grant removed; agent-token only).

Revision ID: 202405160006
Revises: 202405160005
Create Date: 2026-05-19

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202405160006"
down_revision = "202405160005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("hosted_platform_users", "delegation_subject_token")


def downgrade() -> None:
    op.add_column(
        "hosted_platform_users",
        sa.Column("delegation_subject_token", sa.Text(), nullable=True),
    )
