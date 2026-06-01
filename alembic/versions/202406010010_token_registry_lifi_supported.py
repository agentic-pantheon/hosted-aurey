"""Add token_registry.lifi_supported

Revision ID: 202406010010
Revises: 202406010009
Create Date: 2026-06-01

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202406010010"
down_revision = "202406010009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "token_registry",
        sa.Column(
            "lifi_supported",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("token_registry", "lifi_supported")
