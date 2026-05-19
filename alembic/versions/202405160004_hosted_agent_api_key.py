"""Add agent_api_key (per-user 1Claw ocv_ bootstrap key) to hosted_platform_users.

The Platform bootstrap JSON may include ``summary.agent_api_key`` (prefix ``ocv_``) used as
``api_key`` in ``POST /v1/auth/agent-token`` with ``user_agent_id``. Stored plaintext is
**staging-only** — encrypt or use a vault in production.

Revision ID: 202405160004
Revises: 202405160003
Create Date: 2026-05-18

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202405160004"
down_revision = "202405160003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "hosted_platform_users",
        sa.Column("agent_api_key", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("hosted_platform_users", "agent_api_key")
