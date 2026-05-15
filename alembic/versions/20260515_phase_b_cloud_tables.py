"""Phase B: platform users, onboarding audit, bootstrap idempotency."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260515_phase_b_cloud"
down_revision: str | None = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_users",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("oneclaw_user_id", sa.String(length=96), nullable=True),
        sa.Column("connection_id", sa.String(length=128), nullable=True),
        sa.Column("onboarding_state", sa.String(length=32), nullable=False),
        sa.Column("claim_url", sa.Text(), nullable=True),
        sa.Column("vault_id", sa.String(length=96), nullable=True),
        sa.Column("agent_id", sa.String(length=96), nullable=True),
        sa.Column("display_name", sa.String(length=256), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("telegram_user_id", name="uq_platform_users_telegram_user_id"),
        sa.UniqueConstraint("connection_id", name="uq_platform_users_connection_id"),
    )
    op.create_index("ix_platform_users_telegram_user_id", "platform_users", ["telegram_user_id"])

    op.create_table(
        "onboarding_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("platform_user_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["platform_user_id"],
            ["platform_users.id"],
            name="fk_onboarding_events_platform_user",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_onboarding_events_platform_user_id", "onboarding_events", ["platform_user_id"]
    )

    op.create_table(
        "bootstrap_attempts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("connection_id", sa.String(length=128), nullable=False),
        sa.Column("template_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        sa.Column("platform_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_bootstrap_attempts_idempotency_key"),
        sa.ForeignKeyConstraint(
            ["platform_user_id"],
            ["platform_users.id"],
            name="fk_bootstrap_attempts_platform_user",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_bootstrap_attempts_connection_id", "bootstrap_attempts", ["connection_id"])
    op.create_index(
        "ix_bootstrap_attempts_platform_user_id", "bootstrap_attempts", ["platform_user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_bootstrap_attempts_platform_user_id", table_name="bootstrap_attempts")
    op.drop_index("ix_bootstrap_attempts_connection_id", table_name="bootstrap_attempts")
    op.drop_table("bootstrap_attempts")

    op.drop_index("ix_onboarding_events_platform_user_id", table_name="onboarding_events")
    op.drop_table("onboarding_events")

    op.drop_index("ix_platform_users_telegram_user_id", table_name="platform_users")
    op.drop_table("platform_users")
