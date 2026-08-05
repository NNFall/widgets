"""Persist schema-v2 stage-aware pattern provenance and review history.

Revision ID: 0018_stage_aware_pattern_library
Revises: 0017_funnel_journeys

The six tables in this migration are additive. Legacy composition plans keep
their schema unchanged. Exposure idempotency is the tuple
``(run_id, stage, candidate_item_id, idempotency_model_call_id)``. The nullable
``model_call_id`` foreign key is retained for provenance and can be set NULL
on deletion without changing the stable idempotency identity.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0018_stage_aware_pattern_library"
down_revision = "0017_funnel_journeys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pattern_candidate_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("direction_artifact_id", sa.Uuid(), nullable=True),
        sa.Column("selector_model_call_id", sa.Uuid(), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("direction_id", sa.String(length=80), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("registry_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "schema_version = 2",
            name="ck_pattern_candidate_plan_schema_version",
        ),
        sa.CheckConstraint(
            "length(registry_digest) = 64",
            name="ck_pattern_candidate_plan_registry_digest_sha256",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["generation_runs.id"],
            name="fk_pattern_candidate_plans_run_id", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["direction_artifact_id"], ["generation_artifacts.id"],
            name="fk_pattern_candidate_plans_direction_artifact_id", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["selector_model_call_id"], ["model_calls.id"],
            name="fk_pattern_candidate_plans_selector_model_call_id", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", name="uq_pattern_candidate_plan_run"),
    )
    op.create_index("ix_pattern_candidate_plans_run_id", "pattern_candidate_plans", ["run_id"])
    op.create_index("ix_pattern_candidate_plans_direction_artifact_id", "pattern_candidate_plans", ["direction_artifact_id"])
    op.create_index("ix_pattern_candidate_plans_selector_model_call_id", "pattern_candidate_plans", ["selector_model_call_id"])

    op.create_table(
        "pattern_candidate_groups",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("stage_mapping", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["plan_id"], ["pattern_candidate_plans.id"],
            name="fk_pattern_candidate_groups_plan_id", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "category", name="uq_pattern_candidate_group_plan_category"),
        sa.UniqueConstraint("plan_id", "ordinal", name="uq_pattern_candidate_group_plan_ordinal"),
        sa.CheckConstraint("ordinal BETWEEN 1 AND 14", name="ck_pattern_candidate_group_ordinal"),
    )
    op.create_index("ix_pattern_candidate_groups_plan_id", "pattern_candidate_groups", ["plan_id"])
    op.create_index("ix_pattern_candidate_groups_category", "pattern_candidate_groups", ["category"])

    op.create_table(
        "pattern_candidate_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.Column("pattern_version_id", sa.Uuid(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("rank BETWEEN 1 AND 5", name="ck_pattern_candidate_item_rank"),
        sa.ForeignKeyConstraint(
            ["group_id"], ["pattern_candidate_groups.id"],
            name="fk_pattern_candidate_items_group_id", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["pattern_version_id"], ["widget_pattern_versions.id"],
            name="fk_pattern_candidate_items_pattern_version_id", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("group_id", "rank", name="uq_pattern_candidate_item_group_rank"),
        sa.UniqueConstraint("group_id", "pattern_version_id", name="uq_pattern_candidate_item_group_pattern_version"),
    )
    op.create_index("ix_pattern_candidate_items_group_id", "pattern_candidate_items", ["group_id"])
    op.create_index("ix_pattern_candidate_items_pattern_version_id", "pattern_candidate_items", ["pattern_version_id"])

    op.create_table(
        "pattern_stage_exposures",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("candidate_item_id", sa.Uuid(), nullable=False),
        sa.Column("model_call_id", sa.Uuid(), nullable=True),
        sa.Column("idempotency_model_call_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "stage IN ('foundation', 'identity', 'conversation', 'motion_polish')",
            name="ck_pattern_stage_exposure_stage",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["generation_runs.id"],
            name="fk_pattern_stage_exposures_run_id", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_item_id"], ["pattern_candidate_items.id"],
            name="fk_pattern_stage_exposures_candidate_item_id", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["model_call_id"], ["model_calls.id"],
            name="fk_pattern_stage_exposures_model_call_id", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id", "stage", "candidate_item_id", "idempotency_model_call_id",
            name="uq_pattern_stage_exposure_idempotency",
        ),
    )
    op.create_index("ix_pattern_stage_exposures_run_id", "pattern_stage_exposures", ["run_id"])
    op.create_index("ix_pattern_stage_exposures_candidate_item_id", "pattern_stage_exposures", ["candidate_item_id"])
    op.create_index("ix_pattern_stage_exposures_model_call_id", "pattern_stage_exposures", ["model_call_id"])

    op.create_table(
        "pattern_stage_usage_claims",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("exposure_id", sa.Uuid(), nullable=False),
        sa.Column("usage_mode", sa.String(length=16), nullable=False),
        sa.Column("model_call_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "usage_mode IN ('primary', 'combined', 'inspiration')",
            name="ck_pattern_stage_usage_mode",
        ),
        sa.ForeignKeyConstraint(
            ["exposure_id"], ["pattern_stage_exposures.id"],
            name="fk_pattern_stage_usage_claims_exposure_id", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["model_call_id"], ["model_calls.id"],
            name="fk_pattern_stage_usage_claims_model_call_id", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("exposure_id", name="uq_pattern_stage_usage_claim_exposure"),
    )
    op.create_index("ix_pattern_stage_usage_claims_exposure_id", "pattern_stage_usage_claims", ["exposure_id"])
    op.create_index("ix_pattern_stage_usage_claims_model_call_id", "pattern_stage_usage_claims", ["model_call_id"])

    op.create_table(
        "pattern_reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pattern_version_id", sa.Uuid(), nullable=False),
        sa.Column("reviewer_email", sa.String(length=320), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('approved', 'rejected')", name="ck_pattern_review_status"),
        sa.ForeignKeyConstraint(
            ["pattern_version_id"], ["widget_pattern_versions.id"],
            name="fk_pattern_reviews_pattern_version_id", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pattern_reviews_pattern_version_id", "pattern_reviews", ["pattern_version_id"])
    op.create_index("ix_pattern_reviews_pattern_version_created_at", "pattern_reviews", ["pattern_version_id", "created_at"])
    op.create_index("ix_pattern_reviews_status", "pattern_reviews", ["status"])


def downgrade() -> None:
    op.drop_index("ix_pattern_reviews_pattern_version_created_at", table_name="pattern_reviews")
    op.drop_index("ix_pattern_reviews_status", table_name="pattern_reviews")
    op.drop_index("ix_pattern_reviews_pattern_version_id", table_name="pattern_reviews")
    op.drop_table("pattern_reviews")
    op.drop_index("ix_pattern_stage_usage_claims_model_call_id", table_name="pattern_stage_usage_claims")
    op.drop_index("ix_pattern_stage_usage_claims_exposure_id", table_name="pattern_stage_usage_claims")
    op.drop_table("pattern_stage_usage_claims")
    op.drop_index("ix_pattern_stage_exposures_model_call_id", table_name="pattern_stage_exposures")
    op.drop_index("ix_pattern_stage_exposures_candidate_item_id", table_name="pattern_stage_exposures")
    op.drop_index("ix_pattern_stage_exposures_run_id", table_name="pattern_stage_exposures")
    op.drop_table("pattern_stage_exposures")
    op.drop_index("ix_pattern_candidate_items_pattern_version_id", table_name="pattern_candidate_items")
    op.drop_index("ix_pattern_candidate_items_group_id", table_name="pattern_candidate_items")
    op.drop_table("pattern_candidate_items")
    op.drop_index("ix_pattern_candidate_groups_plan_id", table_name="pattern_candidate_groups")
    op.drop_index("ix_pattern_candidate_groups_category", table_name="pattern_candidate_groups")
    op.drop_table("pattern_candidate_groups")
    op.drop_index("ix_pattern_candidate_plans_selector_model_call_id", table_name="pattern_candidate_plans")
    op.drop_index("ix_pattern_candidate_plans_direction_artifact_id", table_name="pattern_candidate_plans")
    op.drop_index("ix_pattern_candidate_plans_run_id", table_name="pattern_candidate_plans")
    op.drop_table("pattern_candidate_plans")
