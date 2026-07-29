"""persist immutable pattern versions, composition plans, and outcomes

Revision ID: 0013_pattern_registry
Revises: 0012_worker_service_readiness
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0013_pattern_registry"
down_revision = "0012_worker_service_readiness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "widget_pattern_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pattern_id", sa.String(length=80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("manifest_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("implementation_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("version > 0", name="ck_pattern_version_positive"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pattern_id", "version", name="uq_pattern_version"),
    )

    op.create_table(
        "composition_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("direction_artifact_id", sa.Uuid(), nullable=True),
        sa.Column("planner_model_call_id", sa.Uuid(), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("direction_id", sa.String(length=80), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("custom_escape", postgresql.JSONB(), nullable=True),
        sa.Column("registry_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "schema_version > 0",
            name="ck_composition_plan_schema_version_positive",
        ),
        sa.ForeignKeyConstraint(
            ["direction_artifact_id"],
            ["generation_artifacts.id"],
            name="fk_composition_plans_direction_artifact_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["planner_model_call_id"],
            ["model_calls.id"],
            name="fk_composition_plans_planner_model_call_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["generation_runs.id"],
            name="fk_composition_plans_run_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", name="uq_composition_plan_run"),
    )
    op.create_index(
        "ix_composition_plans_run_id",
        "composition_plans",
        ["run_id"],
    )

    op.create_table(
        "composition_plan_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("composition_plan_id", sa.Uuid(), nullable=False),
        sa.Column("pattern_version_id", sa.Uuid(), nullable=False),
        sa.Column("slot", sa.String(length=32), nullable=False),
        sa.Column("parameters", postgresql.JSONB(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["composition_plan_id"],
            ["composition_plans.id"],
            name="fk_composition_plan_items_plan_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["pattern_version_id"],
            ["widget_pattern_versions.id"],
            name="fk_composition_plan_items_pattern_version_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "composition_plan_id",
            "slot",
            name="uq_composition_slot",
        ),
    )
    op.create_index(
        "ix_composition_plan_items_composition_plan_id",
        "composition_plan_items",
        ["composition_plan_id"],
    )

    op.create_table(
        "pattern_outcomes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("composition_plan_item_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("final_artifact_id", sa.Uuid(), nullable=True),
        sa.Column("model_call_id", sa.Uuid(), nullable=True),
        sa.Column("technical_pass", sa.Boolean(), nullable=False),
        sa.Column("visual_score", sa.Float(), nullable=True),
        sa.Column("repair_count", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("thinking_tokens", sa.BigInteger(), nullable=False),
        sa.Column("latency_ms", sa.BigInteger(), nullable=False),
        sa.Column("cost_microusd", sa.BigInteger(), nullable=False),
        sa.Column("published", sa.Boolean(), nullable=False),
        sa.Column("adopted", sa.Boolean(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "cost_microusd >= 0",
            name="ck_pattern_outcome_cost_nonnegative",
        ),
        sa.CheckConstraint(
            "repair_count >= 0",
            name="ck_pattern_outcome_repairs_nonnegative",
        ),
        sa.CheckConstraint(
            "visual_score IS NULL OR (visual_score >= 0 AND visual_score <= 1)",
            name="ck_pattern_outcome_visual_score_range",
        ),
        sa.ForeignKeyConstraint(
            ["composition_plan_item_id"],
            ["composition_plan_items.id"],
            name="fk_pattern_outcomes_plan_item_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["final_artifact_id"],
            ["generation_artifacts.id"],
            name="fk_pattern_outcomes_final_artifact_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["model_call_id"],
            ["model_calls.id"],
            name="fk_pattern_outcomes_model_call_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["generation_runs.id"],
            name="fk_pattern_outcomes_run_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_pattern_outcome_idempotency",
        ),
    )
    op.create_index(
        "ix_pattern_outcomes_composition_plan_item_id",
        "pattern_outcomes",
        ["composition_plan_item_id"],
    )
    op.create_index(
        "ix_pattern_outcomes_run_id",
        "pattern_outcomes",
        ["run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_pattern_outcomes_run_id", table_name="pattern_outcomes")
    op.drop_index(
        "ix_pattern_outcomes_composition_plan_item_id",
        table_name="pattern_outcomes",
    )
    op.drop_table("pattern_outcomes")
    op.drop_index(
        "ix_composition_plan_items_composition_plan_id",
        table_name="composition_plan_items",
    )
    op.drop_table("composition_plan_items")
    op.drop_index("ix_composition_plans_run_id", table_name="composition_plans")
    op.drop_table("composition_plans")
    op.drop_table("widget_pattern_versions")
