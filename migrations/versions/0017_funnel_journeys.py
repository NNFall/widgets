"""Add privacy-minimal funnel journeys and nullable attribution linkage.

Revision ID: 0017_funnel_journeys
Revises: 0016_project_versions
"""

from alembic import op
import sqlalchemy as sa


revision = "0017_funnel_journeys"
down_revision = "0016_project_versions"
branch_labels = None
depends_on = None


_JOURNEY_LINKS = (
    ("anonymous_drafts", "fk_anonymous_drafts_journey_id"),
    ("oauth_states", "fk_oauth_states_journey_id"),
    ("projects", "fk_projects_journey_id"),
    ("generation_runs", "fk_generation_runs_journey_id"),
    ("payment_attempts", "fk_payment_attempts_journey_id"),
    ("publications", "fk_publications_journey_id"),
    ("funnel_events", "fk_funnel_events_journey_id"),
)


def upgrade() -> None:
    op.create_table(
        "funnel_journeys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("campaign_source", sa.String(length=255), nullable=True),
        sa.Column("campaign_medium", sa.String(length=255), nullable=True),
        sa.Column("campaign_name", sa.String(length=255), nullable=True),
        sa.Column("campaign_term", sa.String(length=255), nullable=True),
        sa.Column("campaign_content", sa.String(length=255), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_funnel_journeys_started_at",
        "funnel_journeys",
        ["started_at"],
    )

    for table, _constraint in _JOURNEY_LINKS:
        op.add_column(table, sa.Column("journey_id", sa.Uuid(), nullable=True))
    op.add_column(
        "payment_attempts",
        sa.Column("project_id", sa.Uuid(), nullable=True),
    )

    for table, constraint in _JOURNEY_LINKS:
        op.create_foreign_key(
            constraint,
            table,
            "funnel_journeys",
            ["journey_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_foreign_key(
        "fk_payment_attempts_project_id",
        "payment_attempts",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="SET NULL",
    )

    for table, _constraint in _JOURNEY_LINKS:
        op.create_index(
            f"ix_{table}_journey_id",
            table,
            ["journey_id"],
        )
    op.create_index(
        "ix_payment_attempts_project_id",
        "payment_attempts",
        ["project_id"],
    )
    op.create_index(
        "ix_funnel_events_journey_type_occurred",
        "funnel_events",
        ["journey_id", "event_type", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_funnel_journeys_started_at",
        table_name="funnel_journeys",
    )
    op.drop_index(
        "ix_funnel_events_journey_type_occurred",
        table_name="funnel_events",
    )
    op.drop_index(
        "ix_payment_attempts_project_id",
        table_name="payment_attempts",
    )
    for table, _constraint in reversed(_JOURNEY_LINKS):
        op.drop_index(f"ix_{table}_journey_id", table_name=table)

    op.drop_constraint(
        "fk_payment_attempts_project_id",
        "payment_attempts",
        type_="foreignkey",
    )
    for table, constraint in reversed(_JOURNEY_LINKS):
        op.drop_constraint(constraint, table, type_="foreignkey")

    op.drop_column("payment_attempts", "project_id")
    for table, _constraint in reversed(_JOURNEY_LINKS):
        op.drop_column(table, "journey_id")

    op.drop_table("funnel_journeys")
