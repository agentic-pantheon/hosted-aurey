"""Platform users: provisioned_signing_key_chains from bootstrap summary."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260517_signing_key_chains"
down_revision = "20260515_phase_c_grant_ref"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "platform_users",
        sa.Column(
            "provisioned_signing_key_chains",
            sa.JSON(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("platform_users", "provisioned_signing_key_chains")
