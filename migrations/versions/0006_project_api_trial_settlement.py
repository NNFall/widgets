"""Persist structured failure and durable trial settlement state.

Revision ID: 0006_project_api
Revises: 0005_trial_cycles
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_project_api"
down_revision = "0005_trial_cycles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("generation_runs", sa.Column("failure_category", sa.String(length=64)))
    op.add_column("generation_runs", sa.Column("trial_settlement", sa.String(length=32)))
    op.add_column("generation_runs", sa.Column("trial_settled_at", sa.DateTime(timezone=True)))
    op.create_index(
        "ix_generation_runs_unsettled_terminal",
        "generation_runs",
        ["state", "trial_settled_at", "created_at"],
        postgresql_where=sa.text("state IN ('completed', 'failed', 'cancelled') AND trial_settled_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_generation_runs_unsettled_terminal", table_name="generation_runs")
    op.drop_column("generation_runs", "trial_settled_at")
    op.drop_column("generation_runs", "trial_settlement")
    op.drop_column("generation_runs", "failure_category")
