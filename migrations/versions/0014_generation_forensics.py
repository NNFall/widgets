"""add generation forensics and authoritative model-call lineage

Revision ID: 0014_generation_forensics
Revises: 0013_pattern_registry
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0014_generation_forensics"
down_revision = "0013_pattern_registry"
branch_labels = None
depends_on = None


_LEGACY_MARKER = "_kaigo_0014_legacy"


def _complete_rate_card(alias: str = "model_calls") -> str:
    snapshot = f"{alias}.pricing_snapshot::jsonb"
    return (
        f"({snapshot} ->> 'currency') = 'USD' "
        f"AND ({snapshot} ->> 'billing_unit_tokens') ~ '^[1-9][0-9]*$' "
        f"AND ({snapshot} ->> 'input_price_microusd_per_million') "
        "~ '^[0-9]+$' "
        f"AND ({snapshot} ->> 'output_price_microusd_per_million') "
        "~ '^[0-9]+$'"
    )


def upgrade() -> None:
    op.add_column(
        "generation_events",
        sa.Column("registry_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "generation_events",
        sa.Column("public_payload", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "generation_events",
        sa.Column("forensic_ref", sa.String(length=512), nullable=True),
    )

    op.create_table(
        "generation_stage_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "ordinal > 0",
            name="ck_generation_stage_attempt_ordinal_positive",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'result_staged', 'completed', 'failed', "
            "'interrupted', 'cancelled', 'accounting_failed')",
            name="ck_generation_stage_attempt_status",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'result_staged') OR finished_at IS NOT NULL",
            name="ck_generation_stage_attempt_terminal_finished",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["generation_runs.id"],
            name="fk_generation_stage_attempts_run_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "stage",
            "ordinal",
            name="uq_generation_stage_attempt_run_stage_ordinal",
        ),
        sa.UniqueConstraint(
            "id",
            "run_id",
            name="uq_generation_stage_attempt_id_run",
        ),
    )
    op.create_index(
        "ix_generation_stage_attempts_run_id",
        "generation_stage_attempts",
        ["run_id"],
    )

    op.create_table(
        "generation_forensic_manifests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column(
            "last_event_sequence",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "entry_count",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "byte_count",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "schema_version > 0",
            name="ck_generation_forensic_manifest_schema_version_positive",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'active', 'completed', 'failed', "
            "'cancelled', 'degraded')",
            name="ck_generation_forensic_manifest_state",
        ),
        sa.CheckConstraint(
            "last_event_sequence >= 0 AND entry_count >= 0 AND byte_count >= 0",
            name="ck_generation_forensic_manifest_counts_nonnegative",
        ),
        sa.CheckConstraint(
            "manifest_sha256 IS NULL OR length(manifest_sha256) = 64",
            name="ck_generation_forensic_manifest_sha256_length",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["generation_runs.id"],
            name="fk_generation_forensic_manifests_run_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_generation_forensic_manifests_user_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_generation_forensic_manifests_project_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id", name="uq_generation_forensic_manifest_run"
        ),
        sa.UniqueConstraint(
            "storage_key", name="uq_generation_forensic_manifest_storage_key"
        ),
    )
    op.create_index(
        "ix_generation_forensic_manifests_run_id",
        "generation_forensic_manifests",
        ["run_id"],
    )
    op.create_index(
        "ix_generation_forensic_manifests_user_id",
        "generation_forensic_manifests",
        ["user_id"],
    )
    op.create_index(
        "ix_generation_forensic_manifests_project_id",
        "generation_forensic_manifests",
        ["project_id"],
    )
    op.create_index(
        "ix_generation_forensic_manifests_user_created_at",
        "generation_forensic_manifests",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_generation_forensic_manifests_state_expires_at",
        "generation_forensic_manifests",
        ["state", "expires_at"],
    )

    op.create_table(
        "generation_forensic_access_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("actor_email", sa.String(length=320), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN ('search', 'view', 'export')",
            name="ck_generation_forensic_access_action",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["generation_runs.id"],
            name="fk_generation_forensic_access_logs_run_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_generation_forensic_access_logs_project_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_generation_forensic_access_logs_user_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_generation_forensic_access_logs_run_id",
        "generation_forensic_access_logs",
        ["run_id"],
    )
    op.create_index(
        "ix_generation_forensic_access_logs_project_id",
        "generation_forensic_access_logs",
        ["project_id"],
    )
    op.create_index(
        "ix_generation_forensic_access_logs_user_id",
        "generation_forensic_access_logs",
        ["user_id"],
    )
    op.create_index(
        "ix_generation_forensic_access_logs_run_created_at",
        "generation_forensic_access_logs",
        ["run_id", "created_at"],
    )
    op.create_index(
        "ix_generation_forensic_access_logs_actor_created_at",
        "generation_forensic_access_logs",
        ["actor_email", "created_at"],
    )

    with op.batch_alter_table("model_calls") as batch:
        batch.add_column(sa.Column("stage_attempt_id", sa.Uuid(), nullable=True))
        batch.add_column(
            sa.Column("logical_invocation_id", sa.Uuid(), nullable=True)
        )
        batch.add_column(sa.Column("operation", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("semantic_attempt", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("candidate_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("persona", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("fallback_index", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("actual_provider", sa.String(length=64), nullable=True)
        )
        batch.add_column(
            sa.Column("actual_model", sa.String(length=128), nullable=True)
        )
        batch.add_column(sa.Column("cache_read_tokens", sa.BigInteger(), nullable=True))
        batch.add_column(
            sa.Column("cache_write_tokens", sa.BigInteger(), nullable=True)
        )
        batch.add_column(sa.Column("cost_state", sa.String(length=16), nullable=True))
        batch.alter_column(
            "cost_microusd",
            existing_type=sa.BigInteger(),
            nullable=True,
        )

    legacy_update_rate_card = _complete_rate_card("NEW")
    op.execute(
        sa.text(
            f"""
            UPDATE model_calls
            SET pricing_snapshot = jsonb_set(
                COALESCE(pricing_snapshot::jsonb, '{{}}'::jsonb),
                '{{{_LEGACY_MARKER}}}',
                jsonb_build_object(
                    'cost_microusd', cost_microusd,
                    'status', status
                ),
                true
            )::json
            """
        )
    )

    complete_rate_card = _complete_rate_card()
    op.execute(
        sa.text(
            f"""
            WITH classified AS (
                SELECT
                    id,
                    CASE
                        WHEN provider_dispatched IS FALSE THEN 'not_billed'
                        WHEN (input_tokens > 0 OR output_tokens > 0)
                             AND {complete_rate_card}
                            THEN 'estimated'
                        ELSE 'unknown'
                    END AS next_cost_state,
                    CASE
                        WHEN provider_dispatched IS FALSE THEN 0::bigint
                        WHEN (input_tokens > 0 OR output_tokens > 0)
                             AND {complete_rate_card}
                            THEN round(
                                (
                                    input_tokens::numeric
                                    * (pricing_snapshot::jsonb ->>
                                       'input_price_microusd_per_million')::numeric
                                    + output_tokens::numeric
                                    * (pricing_snapshot::jsonb ->>
                                       'output_price_microusd_per_million')::numeric
                                )
                                / (pricing_snapshot::jsonb ->>
                                   'billing_unit_tokens')::numeric
                            )::bigint
                        ELSE NULL
                    END AS next_cost_microusd
                FROM model_calls
            )
            UPDATE model_calls AS target
            SET
                logical_invocation_id = target.id,
                operation = 'legacy_unclassified',
                semantic_attempt = 1,
                fallback_index = GREATEST(target.attempt, 1),
                cache_read_tokens = 0,
                cache_write_tokens = 0,
                cost_state = classified.next_cost_state,
                cost_microusd = classified.next_cost_microusd,
                status = CASE
                    WHEN target.status = 'failed'
                         AND target.error_code = 'generation_timeout'
                        THEN 'timed_out'
                    ELSE target.status
                END
            FROM classified
            WHERE classified.id = target.id
            """
        )
    )

    with op.batch_alter_table("model_calls") as batch:
        batch.alter_column(
            "logical_invocation_id",
            existing_type=sa.Uuid(),
            nullable=False,
        )
        batch.alter_column(
            "operation",
            existing_type=sa.String(length=64),
            nullable=False,
            server_default="legacy_unclassified",
        )
        batch.alter_column(
            "semantic_attempt",
            existing_type=sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        )
        batch.alter_column(
            "fallback_index",
            existing_type=sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        )
        batch.alter_column(
            "cache_read_tokens",
            existing_type=sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        )
        batch.alter_column(
            "cache_write_tokens",
            existing_type=sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        )
        batch.alter_column(
            "cost_state",
            existing_type=sa.String(length=16),
            nullable=False,
            server_default="estimated",
        )
        batch.create_index(
            "ix_model_calls_stage_attempt_id", ["stage_attempt_id"]
        )
        batch.create_foreign_key(
            "fk_model_calls_stage_attempt_run",
            "generation_stage_attempts",
            ["stage_attempt_id", "run_id"],
            ["id", "run_id"],
            ondelete="CASCADE",
        )
        batch.create_unique_constraint(
            "uq_model_call_logical_fallback",
            ["logical_invocation_id", "fallback_index"],
        )
        batch.create_check_constraint(
            "ck_model_calls_stage_attempt_requires_run",
            "stage_attempt_id IS NULL OR run_id IS NOT NULL",
        )
        batch.create_check_constraint(
            "ck_model_calls_semantic_attempt_positive",
            "semantic_attempt > 0",
        )
        batch.create_check_constraint(
            "ck_model_calls_fallback_index_positive",
            "fallback_index > 0",
        )
        batch.create_check_constraint(
            "ck_model_calls_cache_tokens_nonnegative",
            "cache_read_tokens >= 0 AND cache_write_tokens >= 0",
        )
        batch.create_check_constraint(
            "ck_model_calls_token_subsets",
            "thinking_tokens <= output_tokens AND "
            "cache_read_tokens + cache_write_tokens <= input_tokens",
        )
        batch.create_check_constraint(
            "ck_model_calls_actual_identity_pair",
            "(actual_provider IS NULL) = (actual_model IS NULL)",
        )
        batch.create_check_constraint(
            "ck_model_calls_cost_state_value",
            "cost_state IN ('reported', 'estimated', 'unknown', 'not_billed')",
        )
        batch.create_check_constraint(
            "ck_model_calls_cost_nonnegative",
            "cost_microusd IS NULL OR cost_microusd >= 0",
        )
        batch.create_check_constraint(
            "ck_model_calls_cost_state_amount",
            "(cost_state = 'unknown' AND cost_microusd IS NULL) OR "
            "(cost_state = 'not_billed' AND cost_microusd = 0) OR "
            "(cost_state IN ('reported', 'estimated') "
            "AND cost_microusd IS NOT NULL)",
        )

    op.execute(
        sa.text(
            """
            CREATE FUNCTION kaigo_0014_guard_legacy_model_call_insert()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF NEW.pricing_snapshot IS NOT NULL THEN
                    NEW.pricing_snapshot := (
                        NEW.pricing_snapshot::jsonb - '_kaigo_0014_legacy'
                    )::json;
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_model_calls_0014_guard_legacy_insert
            BEFORE INSERT ON model_calls
            FOR EACH ROW
            EXECUTE FUNCTION kaigo_0014_guard_legacy_model_call_insert()
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION kaigo_0014_reconcile_legacy_model_call_update()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            DECLARE
                legacy_snapshot jsonb;
                legacy_cost_microusd bigint;
            BEGIN
                legacy_snapshot := OLD.pricing_snapshot::jsonb
                    -> '{_LEGACY_MARKER}';
                IF legacy_snapshot IS NULL THEN
                    NEW.pricing_snapshot := (
                        NEW.pricing_snapshot::jsonb - '{_LEGACY_MARKER}'
                    )::json;
                    RETURN NEW;
                END IF;

                IF NEW.cost_state IS DISTINCT FROM OLD.cost_state
                   OR NEW.logical_invocation_id IS DISTINCT FROM
                      OLD.logical_invocation_id
                   OR NEW.operation IS DISTINCT FROM OLD.operation
                   OR NEW.semantic_attempt IS DISTINCT FROM OLD.semantic_attempt
                   OR NEW.candidate_id IS DISTINCT FROM OLD.candidate_id
                   OR NEW.persona IS DISTINCT FROM OLD.persona
                   OR NEW.fallback_index IS DISTINCT FROM OLD.fallback_index
                   OR NEW.stage_attempt_id IS DISTINCT FROM OLD.stage_attempt_id
                   OR NEW.actual_provider IS DISTINCT FROM OLD.actual_provider
                   OR NEW.actual_model IS DISTINCT FROM OLD.actual_model
                   OR NEW.cache_read_tokens IS DISTINCT FROM OLD.cache_read_tokens
                   OR NEW.cache_write_tokens IS DISTINCT FROM
                      OLD.cache_write_tokens
                THEN
                    NEW.pricing_snapshot := (
                        NEW.pricing_snapshot::jsonb - '{_LEGACY_MARKER}'
                    )::json;
                    RETURN NEW;
                END IF;

                IF OLD.logical_invocation_id IS DISTINCT FROM OLD.id
                   OR OLD.operation <> 'legacy_unclassified'
                   OR OLD.stage_attempt_id IS NOT NULL
                   OR NEW.cost_microusd IS NULL
                   OR NEW.status NOT IN ('completed', 'failed', 'cancelled')
                THEN
                    NEW.pricing_snapshot := jsonb_set(
                        NEW.pricing_snapshot::jsonb,
                        '{{{_LEGACY_MARKER}}}',
                        legacy_snapshot,
                        true
                    )::json;
                    RETURN NEW;
                END IF;

                legacy_cost_microusd := NEW.cost_microusd;
                IF NEW.provider_dispatched IS FALSE THEN
                    NEW.cost_state := 'not_billed';
                    NEW.cost_microusd := 0;
                ELSIF (NEW.input_tokens > 0 OR NEW.output_tokens > 0)
                      AND {legacy_update_rate_card}
                THEN
                    NEW.cost_state := 'estimated';
                ELSE
                    NEW.cost_state := 'unknown';
                    NEW.cost_microusd := NULL;
                END IF;
                NEW.pricing_snapshot := jsonb_set(
                    NEW.pricing_snapshot::jsonb,
                    '{{{_LEGACY_MARKER}}}',
                    jsonb_build_object(
                        'cost_microusd', legacy_cost_microusd,
                        'status', NEW.status
                    ),
                    true
                )::json;
                RETURN NEW;
            END;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_model_calls_0014_legacy_terminal_update
            BEFORE UPDATE ON model_calls
            FOR EACH ROW
            EXECUTE FUNCTION kaigo_0014_reconcile_legacy_model_call_update()
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS "
            "trg_model_calls_0014_legacy_terminal_update ON model_calls"
        )
    )
    op.execute(
        sa.text(
            "DROP FUNCTION IF EXISTS "
            "kaigo_0014_reconcile_legacy_model_call_update()"
        )
    )
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_model_calls_0014_guard_legacy_insert "
            "ON model_calls"
        )
    )
    op.execute(
        sa.text(
            "DROP FUNCTION IF EXISTS "
            "kaigo_0014_guard_legacy_model_call_insert()"
        )
    )

    with op.batch_alter_table("model_calls") as batch:
        batch.drop_constraint(
            "ck_model_calls_cost_state_amount", type_="check"
        )
        batch.drop_constraint(
            "ck_model_calls_cost_nonnegative", type_="check"
        )
        batch.drop_constraint(
            "ck_model_calls_cost_state_value", type_="check"
        )
        batch.drop_constraint(
            "ck_model_calls_actual_identity_pair", type_="check"
        )
        batch.drop_constraint("ck_model_calls_token_subsets", type_="check")
        batch.drop_constraint(
            "ck_model_calls_cache_tokens_nonnegative", type_="check"
        )
        batch.drop_constraint(
            "ck_model_calls_fallback_index_positive", type_="check"
        )
        batch.drop_constraint(
            "ck_model_calls_semantic_attempt_positive", type_="check"
        )
        batch.drop_constraint(
            "ck_model_calls_stage_attempt_requires_run", type_="check"
        )
        batch.drop_constraint(
            "uq_model_call_logical_fallback", type_="unique"
        )
        batch.drop_constraint(
            "fk_model_calls_stage_attempt_run", type_="foreignkey"
        )
        batch.drop_index("ix_model_calls_stage_attempt_id")

    op.execute(
        sa.text(
            f"""
            UPDATE model_calls
            SET
                cost_microusd = COALESCE(
                    (pricing_snapshot::jsonb -> '{_LEGACY_MARKER}' ->>
                     'cost_microusd')::bigint,
                    cost_microusd,
                    0
                ),
                status = COALESCE(
                    pricing_snapshot::jsonb -> '{_LEGACY_MARKER}' ->> 'status',
                    status
                ),
                pricing_snapshot = (
                    pricing_snapshot::jsonb - '{_LEGACY_MARKER}'
                )::json
            """
        )
    )

    with op.batch_alter_table("model_calls") as batch:
        batch.alter_column(
            "cost_microusd",
            existing_type=sa.BigInteger(),
            nullable=False,
        )
        batch.drop_column("cost_state")
        batch.drop_column("cache_write_tokens")
        batch.drop_column("cache_read_tokens")
        batch.drop_column("actual_model")
        batch.drop_column("actual_provider")
        batch.drop_column("fallback_index")
        batch.drop_column("persona")
        batch.drop_column("candidate_id")
        batch.drop_column("semantic_attempt")
        batch.drop_column("operation")
        batch.drop_column("logical_invocation_id")
        batch.drop_column("stage_attempt_id")

    op.drop_index(
        "ix_generation_forensic_access_logs_actor_created_at",
        table_name="generation_forensic_access_logs",
    )
    op.drop_index(
        "ix_generation_forensic_access_logs_run_created_at",
        table_name="generation_forensic_access_logs",
    )
    op.drop_index(
        "ix_generation_forensic_access_logs_user_id",
        table_name="generation_forensic_access_logs",
    )
    op.drop_index(
        "ix_generation_forensic_access_logs_project_id",
        table_name="generation_forensic_access_logs",
    )
    op.drop_index(
        "ix_generation_forensic_access_logs_run_id",
        table_name="generation_forensic_access_logs",
    )
    op.drop_table("generation_forensic_access_logs")

    op.drop_index(
        "ix_generation_forensic_manifests_state_expires_at",
        table_name="generation_forensic_manifests",
    )
    op.drop_index(
        "ix_generation_forensic_manifests_user_created_at",
        table_name="generation_forensic_manifests",
    )
    op.drop_index(
        "ix_generation_forensic_manifests_project_id",
        table_name="generation_forensic_manifests",
    )
    op.drop_index(
        "ix_generation_forensic_manifests_user_id",
        table_name="generation_forensic_manifests",
    )
    op.drop_index(
        "ix_generation_forensic_manifests_run_id",
        table_name="generation_forensic_manifests",
    )
    op.drop_table("generation_forensic_manifests")

    op.drop_index(
        "ix_generation_stage_attempts_run_id",
        table_name="generation_stage_attempts",
    )
    op.drop_table("generation_stage_attempts")

    op.drop_column("generation_events", "forensic_ref")
    op.drop_column("generation_events", "public_payload")
    op.drop_column("generation_events", "registry_version")
