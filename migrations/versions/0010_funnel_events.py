"""Add privacy-minimal server-owned funnel events.

Revision ID: 0010_funnel_events
Revises: 0009_billing_foundation
"""

from alembic import op
import sqlalchemy as sa


revision = "0010_funnel_events"
down_revision = "0009_billing_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "oauth_states",
        sa.Column("session_binding_digest", sa.String(64), nullable=True),
    )
    # Pre-existing OAuth transactions cannot be safely bound retroactively.
    # Invalidate them, assign an impossible digest, then enforce the invariant.
    op.execute(
        "UPDATE oauth_states "
        "SET session_binding_digest = '!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!', "
        "consumed_at = COALESCE(consumed_at, CURRENT_TIMESTAMP) "
        "WHERE session_binding_digest IS NULL"
    )
    op.alter_column(
        "oauth_states",
        "session_binding_digest",
        existing_type=sa.String(64),
        nullable=False,
    )
    op.add_column(
        "anonymous_drafts",
        sa.Column(
            "campaign",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.create_table(
        "funnel_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("event_key", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column(
            "anonymous_draft_id",
            sa.Uuid(),
            sa.ForeignKey("anonymous_drafts.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "oauth_state_id",
            sa.Uuid(),
            sa.ForeignKey("oauth_states.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("generation_runs.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "artifact_id",
            sa.Uuid(),
            sa.ForeignKey("generation_artifacts.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "payment_attempt_id",
            sa.Uuid(),
            sa.ForeignKey("payment_attempts.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "publication_id",
            sa.Uuid(),
            sa.ForeignKey("publications.id", ondelete="SET NULL"),
        ),
        sa.Column("campaign_source", sa.String(255)),
        sa.Column("campaign_medium", sa.String(255)),
        sa.Column("campaign_name", sa.String(255)),
        sa.Column("campaign_term", sa.String(255)),
        sa.Column("campaign_content", sa.String(255)),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("event_key", name="uq_funnel_events_event_key"),
    )
    op.create_index(
        "ix_funnel_events_event_type",
        "funnel_events",
        ["event_type"],
    )
    op.create_index("ix_funnel_events_user_id", "funnel_events", ["user_id"])
    op.create_index("ix_funnel_events_project_id", "funnel_events", ["project_id"])
    op.create_index("ix_funnel_events_run_id", "funnel_events", ["run_id"])


def downgrade() -> None:
    op.drop_table("funnel_events")
    op.drop_column("anonymous_drafts", "campaign")
    op.drop_column("oauth_states", "session_binding_digest")
