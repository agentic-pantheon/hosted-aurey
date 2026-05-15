"""Phase C: grant reference columns for delegated runtime (metadata only)."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260515_phase_c_grant_ref"
down_revision = "20260515_phase_b_cloud"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "platform_users",
        sa.Column("grant_ref_path", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "platform_users",
        sa.Column("grant_metadata", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("platform_users", "grant_metadata")
    op.drop_column("platform_users", "grant_ref_path")
