"""Harden project recovery and provider cancellation accounting.

Revision ID: 0007_project_recovery
Revises: 0006_project_api
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_project_recovery"
down_revision = "0006_project_api"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "model_calls",
        sa.Column(
            "provider_dispatched",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.execute(sa.text(
        "UPDATE model_calls SET provider_dispatched = TRUE "
        "WHERE status IN ('dispatched', 'completed', 'failed', 'cancelled')"
    ))
    op.execute(sa.text(
        "UPDATE generation_runs AS generation_run "
        "SET trial_settlement = 'not_applicable', "
        "trial_settled_at = COALESCE(generation_run.finished_at, CURRENT_TIMESTAMP) "
        "WHERE generation_run.state IN ('completed', 'failed', 'cancelled') "
        "AND generation_run.trial_settled_at IS NULL "
        "AND NOT EXISTS ("
        "SELECT 1 FROM usage_ledger AS ledger "
        "WHERE ledger.run_id = generation_run.id "
        "AND ledger.entry_type = 'trial.reserve'"
        ")"
    ))


def downgrade() -> None:
    op.drop_column("model_calls", "provider_dispatched")
