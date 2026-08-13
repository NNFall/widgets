"""Add founder publication grants and customer contact requests.

Revision ID: 0019_founder_publication_funnel
Revises: 0018_stage_aware_pattern_library
"""

from alembic import op
import sqlalchemy as sa


revision = "0019_founder_publication_funnel"
down_revision = "0018_stage_aware_pattern_library"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "founder_access_grants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=False),
        sa.Column("source_origin", sa.String(length=2048), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("feedback_state", sa.String(length=32), server_default="requested", nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("position BETWEEN 1 AND 20", name="ck_founder_access_grant_position"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"], name="fk_founder_access_grant_subscription", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("position", name="uq_founder_access_grant_position"),
        sa.UniqueConstraint("source_origin", name="uq_founder_access_grant_origin"),
        sa.UniqueConstraint("subscription_id"),
        sa.UniqueConstraint("user_id", name="uq_founder_access_grant_user"),
    )
    op.create_index("ix_founder_access_grants_user_id", "founder_access_grants", ["user_id"])
    op.create_index("ix_founder_access_grants_project_id", "founder_access_grants", ["project_id"])
    op.add_column("usage_ledger", sa.Column("founder_grant_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_usage_ledger_founder_grant_id",
        "usage_ledger",
        "founder_access_grants",
        ["founder_grant_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_usage_ledger_founder_grant_id", "usage_ledger", ["founder_grant_id"])
    op.create_table(
        "customer_contact_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("subscription_id", sa.Uuid(), nullable=True),
        sa.Column("founder_grant_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("rating", sa.SmallInteger(), nullable=True),
        sa.Column("testimonial_allowed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind IN ('support', 'founder_feedback')", name="ck_customer_contact_request_kind"),
        sa.CheckConstraint("rating IS NULL OR rating BETWEEN 1 AND 5", name="ck_customer_contact_request_rating"),
        sa.ForeignKeyConstraint(["founder_grant_id"], ["founder_access_grants.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_customer_contact_requests_user_id", "customer_contact_requests", ["user_id"])
    op.create_index("ix_customer_contact_requests_project_id", "customer_contact_requests", ["project_id"])
    op.create_index("ix_customer_contact_requests_subscription_id", "customer_contact_requests", ["subscription_id"])
    op.create_index("ix_customer_contact_requests_founder_grant_id", "customer_contact_requests", ["founder_grant_id"])


def downgrade() -> None:
    op.drop_index("ix_customer_contact_requests_founder_grant_id", table_name="customer_contact_requests")
    op.drop_index("ix_customer_contact_requests_subscription_id", table_name="customer_contact_requests")
    op.drop_index("ix_customer_contact_requests_project_id", table_name="customer_contact_requests")
    op.drop_index("ix_customer_contact_requests_user_id", table_name="customer_contact_requests")
    op.drop_table("customer_contact_requests")
    op.drop_index("ix_usage_ledger_founder_grant_id", table_name="usage_ledger")
    op.drop_constraint("fk_usage_ledger_founder_grant_id", "usage_ledger", type_="foreignkey")
    op.drop_column("usage_ledger", "founder_grant_id")
    op.drop_index("ix_founder_access_grants_project_id", table_name="founder_access_grants")
    op.drop_index("ix_founder_access_grants_user_id", table_name="founder_access_grants")
    op.drop_table("founder_access_grants")
