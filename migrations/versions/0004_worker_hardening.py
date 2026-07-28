"""harden durable worker scheduling

Revision ID: 0004_worker_hardening
Revises: 0003_saas_foundation
Create Date: 2026-07-28
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_worker_hardening"
down_revision: Union[str, None] = "0003_saas_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "generation_runs",
        sa.Column(
            "stage_retry_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "generation_runs",
        sa.Column("retry_not_before", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_generation_runs_worker_claim",
        "generation_runs",
        [
            "state",
            "retry_not_before",
            "lease_expires_at",
            "created_at",
            "id",
        ],
        unique=False,
        postgresql_where=sa.text("state IN ('queued', 'running')"),
    )
    op.create_index(
        "ix_generation_events_run_type_sequence_desc",
        "generation_events",
        ["run_id", "event_type", sa.text("sequence DESC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_generation_events_run_type_sequence_desc",
        table_name="generation_events",
    )
    op.drop_index(
        "ix_generation_runs_worker_claim",
        table_name="generation_runs",
    )
    op.drop_column("generation_runs", "retry_not_before")
    op.drop_column("generation_runs", "stage_retry_count")
