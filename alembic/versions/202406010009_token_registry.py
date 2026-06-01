"""Create token_registry

Revision ID: 202406010009
Revises: 202405250008
Create Date: 2026-06-01

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202406010009"
down_revision = "202405250008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "token_registry",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "ecosystem",
            sa.String(length=16),
            server_default=sa.text("'evm'"),
            nullable=False,
        ),
        sa.Column("chain_slug", sa.String(length=64), nullable=False),
        sa.Column("chain_id", sa.Integer(), nullable=True),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("address", sa.String(length=255), nullable=False),
        sa.Column("decimals", sa.Integer(), nullable=True),
        sa.Column("coingecko_id", sa.String(length=128), nullable=True),
        sa.Column("market_cap_rank", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("trust_tier", sa.String(length=32), nullable=False),
        sa.Column(
            "verified_onchain",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "cg_recognized",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chain_slug", "address"),
    )
    op.create_index(
        op.f("ix_token_registry_chain_slug_symbol"),
        "token_registry",
        ["chain_slug", "symbol"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_token_registry_chain_slug_symbol"),
        table_name="token_registry",
    )
    op.drop_table("token_registry")
