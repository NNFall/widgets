"""Persist trial reservation cycles and model call mode.

Revision ID: 0005_trial_cycles
Revises: 0004_worker_hardening
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_trial_cycles"
down_revision = "0004_worker_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "trial_entitlements",
        sa.Column("reservation_epoch", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "model_calls",
        sa.Column("mode", sa.String(length=32), nullable=False, server_default="direct"),
    )


def downgrade() -> None:
    op.drop_column("model_calls", "mode")
    op.drop_column("trial_entitlements", "reservation_epoch")
