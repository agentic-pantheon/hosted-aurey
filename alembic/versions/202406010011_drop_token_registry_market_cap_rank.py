"""Drop token_registry.market_cap_rank

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
    op.drop_column("token_registry", "market_cap_rank")


def downgrade() -> None:
    op.add_column(
        "token_registry",
        sa.Column("market_cap_rank", sa.Integer(), nullable=True),
    )
