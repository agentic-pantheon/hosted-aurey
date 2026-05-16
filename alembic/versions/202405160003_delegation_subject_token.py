"""Add delegation_subject_token to hosted_platform_users

INSECURITY / STAGING WARNING: ``delegation_subject_token`` stores the raw subject /
delegation grant token in plaintext. This is intentional **only** for short-lived
staging environments — do not ship to production without encryption, KMS, or a
token vault.

Revision ID: 202405160003
Revises: 202405160002
Create Date: 2026-05-16

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202405160003"
down_revision = "202405160002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "hosted_platform_users",
        sa.Column("delegation_subject_token", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("hosted_platform_users", "delegation_subject_token")
