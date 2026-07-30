from __future__ import annotations

import asyncio
import importlib
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


POSTGRES_URL = os.getenv("KAIGO_TEST_POSTGRES_URL")
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_generation_forensics_migration_identity() -> None:
    migration = importlib.import_module(
        "migrations.versions.0014_generation_forensics"
    )

    assert migration.revision == "0014_generation_forensics"
    assert migration.down_revision == "0013_pattern_registry"


def test_generation_forensics_migration_uses_one_combined_additive_cut() -> None:
    migration = importlib.import_module(
        "migrations.versions.0014_generation_forensics"
    )
    source = Path(migration.__file__).read_text(encoding="utf-8")

    assert "generation_forensic_manifests" in source
    assert "generation_forensic_access_logs" in source
    assert "generation_stage_attempts" in source
    assert "logical_invocation_id" in source
    assert "fk_model_calls_stage_attempt_run" in source
    assert "uq_model_call_logical_fallback" in source
    assert "_kaigo_0014_legacy" in source
    assert "kaigo_0014_guard_legacy_model_call_insert" in source
    assert "trg_model_calls_0014_guard_legacy_insert" in source
    assert "NEW.logical_invocation_id := NEW.id" not in source
    assert "kaigo_0014_reconcile_legacy_model_call_update" in source
    assert "trg_model_calls_0014_legacy_terminal_update" in source
    assert "NEW.cost_state IS DISTINCT FROM OLD.cost_state" in source
    assert "NEW.cost_state := 'estimated'" in source
    assert "cost_state = 'not_billed' AND cost_microusd = 0" in source
    assert "reported" not in source.split("def upgrade()", 1)[1].split(
        "def downgrade()", 1
    )[0].split("sa.CheckConstraint", 1)[0]


async def _revision(engine: AsyncEngine) -> str | None:
    async with engine.connect() as connection:
        return await connection.run_sync(
            lambda sync: MigrationContext.configure(sync).get_current_revision()
        )


async def _columns(engine: AsyncEngine, table_name: str) -> set[str]:
    async with engine.connect() as connection:
        return await connection.run_sync(
            lambda sync: {
                row["name"] for row in inspect(sync).get_columns(table_name)
            }
        )


async def _table_names(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as connection:
        return await connection.run_sync(
            lambda sync: set(inspect(sync).get_table_names())
        )


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.skipif(
    not POSTGRES_URL, reason="KAIGO_TEST_POSTGRES_URL is not configured"
)
async def test_postgres_generation_forensics_round_trip_and_constraints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_url = make_url(POSTGRES_URL)
    if source_url.drivername == "postgresql":
        source_url = source_url.set(drivername="postgresql+asyncpg")
    database_name = f"kaigo_forensics_{uuid4().hex}"
    admin_url = source_url.set(database="postgres")
    target_url = source_url.set(database=database_name)
    rendered = target_url.render_as_string(hide_password=False)
    admin_engine = create_async_engine(
        admin_url.render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
    )
    target_engine: AsyncEngine | None = None
    created = False

    tenant_id = 1
    user_id = 1
    project_id = uuid4()
    run_a = uuid4()
    run_b = uuid4()
    event_payload = {"legacy": True, "nested": {"value": 7}}
    in_flight_call_id = uuid4()
    caught_before_dispatch_call_id = uuid4()
    unknown_terminal_call_id = uuid4()
    explicit_writer_call_id = uuid4()
    explicit_lineage_call_id = uuid4()
    explicit_writer_logical_id = uuid4()
    calls = {
        "not_dispatched": uuid4(),
        "dispatched_unknown": uuid4(),
        "estimated": uuid4(),
        "timeout_unknown": uuid4(),
    }
    estimated_snapshot = {
        "currency": "USD",
        "billing_unit_tokens": 1_000_000,
        "input_price_microusd_per_million": 2_000_000,
        "output_price_microusd_per_million": 4_000_000,
    }
    incomplete_snapshot = {"currency": "USD"}
    original_costs = {
        calls["not_dispatched"]: 0,
        calls["dispatched_unknown"]: 0,
        calls["estimated"]: 999,
        calls["timeout_unknown"]: 77,
    }
    original_statuses = {
        calls["not_dispatched"]: "failed",
        calls["dispatched_unknown"]: "failed",
        calls["estimated"]: "completed",
        calls["timeout_unknown"]: "failed",
    }

    try:
        async with admin_engine.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        created = True
        monkeypatch.setenv("DATABASE_URL", rendered)
        config = Config(str(PROJECT_ROOT / "alembic.ini"))
        await asyncio.to_thread(command.upgrade, config, "0013_pattern_registry")
        target_engine = create_async_engine(rendered)

        async with target_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO tenants (id, name, slug) "
                    "VALUES (:id, 'Forensics', 'forensics-migration')"
                ),
                {"id": tenant_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO users (id, tenant_id, email, role) "
                    "VALUES (:id, :tenant_id, 'forensics@example.com', "
                    "'tenant_admin')"
                ),
                {"id": user_id, "tenant_id": tenant_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO projects "
                    "(id, tenant_id, owner_user_id, source_url, status) "
                    "VALUES (CAST(:id AS UUID), :tenant_id, :user_id, "
                    "'https://example.com', 'draft')"
                ),
                {
                    "id": str(project_id),
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO generation_runs "
                    "(id, project_id, mode, state, progress, next_event_sequence, "
                    "idempotency_key) VALUES "
                    "(CAST(:run_a AS UUID), CAST(:project_id AS UUID), "
                    "'express', 'running', 10, 2, 'forensics-run-a'), "
                    "(CAST(:run_b AS UUID), CAST(:project_id AS UUID), "
                    "'express', 'running', 10, 1, 'forensics-run-b')"
                ),
                {
                    "run_a": str(run_a),
                    "run_b": str(run_b),
                    "project_id": str(project_id),
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO generation_events "
                    "(run_id, sequence, event_type, public_message, payload) "
                    "VALUES (CAST(:run_id AS UUID), 1, 'legacy.event', "
                    "'Legacy public text', CAST(:payload AS JSON))"
                ),
                {"run_id": str(run_a), "payload": json.dumps(event_payload)},
            )

            rows = (
                {
                    "id": calls["not_dispatched"],
                    "attempt": 0,
                    "dispatched": False,
                    "input": 0,
                    "output": 0,
                    "thinking": 0,
                    "status": "failed",
                    "error_code": "validation_failed",
                    "cost": 0,
                    "pricing": {},
                },
                {
                    "id": calls["dispatched_unknown"],
                    "attempt": 2,
                    "dispatched": True,
                    "input": 0,
                    "output": 0,
                    "thinking": 0,
                    "status": "failed",
                    "error_code": "provider_error",
                    "cost": 0,
                    "pricing": {},
                },
                {
                    "id": calls["estimated"],
                    "attempt": 3,
                    "dispatched": True,
                    "input": 1000,
                    "output": 200,
                    "thinking": 50,
                    "status": "completed",
                    "error_code": None,
                    "cost": 999,
                    "pricing": estimated_snapshot,
                },
                {
                    "id": calls["timeout_unknown"],
                    "attempt": 1,
                    "dispatched": True,
                    "input": 10,
                    "output": 0,
                    "thinking": 0,
                    "status": "failed",
                    "error_code": "generation_timeout",
                    "cost": 77,
                    "pricing": {"currency": "USD"},
                },
            )
            for row in rows:
                await connection.execute(
                    text(
                        "INSERT INTO model_calls "
                        "(id, run_id, provider, model, role, mode, prompt_version, "
                        "attempt, provider_dispatched, input_tokens, output_tokens, "
                        "thinking_tokens, latency_ms, status, error_code, "
                        "cost_microusd, pricing_snapshot) VALUES "
                        "(CAST(:id AS UUID), CAST(:run_id AS UUID), 'legacy', "
                        "'legacy-model', 'builder', 'direct', 'legacy-v1', "
                        ":attempt, :dispatched, :input, :output, :thinking, 100, "
                        ":status, :error_code, :cost, CAST(:pricing AS JSON))"
                    ),
                    {
                        **row,
                        "id": str(row["id"]),
                        "run_id": str(run_a),
                        "pricing": json.dumps(row["pricing"]),
                    },
                )
            await connection.execute(
                text(
                    "INSERT INTO model_calls "
                    "(id, run_id, provider, model, role, mode, prompt_version, "
                    "attempt, provider_dispatched, input_tokens, output_tokens, "
                    "thinking_tokens, latency_ms, status, cost_microusd, "
                    "pricing_snapshot) VALUES "
                    "(CAST(:id AS UUID), CAST(:run_id AS UUID), "
                    "'legacy-in-flight', 'legacy-model', 'builder', 'direct', "
                    "'legacy-v1', 1, true, 0, 0, 0, 1, 'dispatched', 0, "
                    "CAST('{}' AS JSON)), "
                    "(CAST(:caught_id AS UUID), CAST(:run_id AS UUID), "
                    "'legacy-caught', 'legacy-model', 'builder', 'direct', "
                    "'legacy-v1', 1, false, 0, 0, 0, 1, 'created', 0, "
                    "CAST(:caught_pricing AS JSON)), "
                    "(CAST(:unknown_id AS UUID), CAST(:run_id AS UUID), "
                    "'legacy-unknown-terminal', 'legacy-model', 'builder', "
                    "'direct', 'legacy-v1', 1, true, 0, 0, 0, 1, "
                    "'dispatched', 0, CAST('{}' AS JSON)), "
                    "(CAST(:explicit_id AS UUID), CAST(:run_id AS UUID), "
                    "'legacy-explicit', 'legacy-model', 'builder', 'direct', "
                    "'legacy-v1', 1, true, 0, 0, 0, 1, 'dispatched', 0, "
                    "CAST('{}' AS JSON)), "
                    "(CAST(:explicit_lineage_id AS UUID), "
                    "CAST(:run_id AS UUID), 'legacy-lineage', 'legacy-model', "
                    "'builder', 'direct', 'legacy-v1', 1, true, 0, 0, 0, 1, "
                    "'dispatched', 0, "
                    "CAST('{}' AS JSON))"
                ),
                {
                    "id": str(in_flight_call_id),
                    "caught_id": str(caught_before_dispatch_call_id),
                    "caught_pricing": json.dumps(estimated_snapshot),
                    "unknown_id": str(unknown_terminal_call_id),
                    "explicit_id": str(explicit_writer_call_id),
                    "explicit_lineage_id": str(explicit_lineage_call_id),
                    "run_id": str(run_a),
                },
            )

        await target_engine.dispose()
        target_engine = None
        await asyncio.to_thread(command.upgrade, config, "head")
        target_engine = create_async_engine(rendered)

        assert await _revision(target_engine) == "0017_funnel_journeys"
        assert {
            "generation_forensic_manifests",
            "generation_forensic_access_logs",
            "generation_stage_attempts",
        } <= await _table_names(target_engine)
        assert {"registry_version", "public_payload", "forensic_ref"} <= (
            await _columns(target_engine, "generation_events")
        )

        async with target_engine.connect() as connection:
            event = (
                await connection.execute(
                    text(
                        "SELECT event_type, public_message, payload, "
                        "registry_version, public_payload, forensic_ref "
                        "FROM generation_events WHERE run_id=CAST(:run_id AS UUID)"
                    ),
                    {"run_id": str(run_a)},
                )
            ).mappings().one()
            model_rows = (
                await connection.execute(
                    text(
                        "SELECT id, logical_invocation_id, operation, "
                        "semantic_attempt, fallback_index, stage_attempt_id, "
                        "actual_provider, actual_model, cache_read_tokens, "
                        "cache_write_tokens, cost_state, cost_microusd, status "
                        "FROM model_calls ORDER BY id"
                    )
                )
            ).mappings().all()

        assert event["event_type"] == "legacy.event"
        assert event["public_message"] == "Legacy public text"
        assert event["payload"] == event_payload
        assert event["registry_version"] is None
        assert event["public_payload"] is None
        assert event["forensic_ref"] is None

        by_id = {UUID(str(row["id"])): row for row in model_rows}
        assert by_id[calls["not_dispatched"]]["cost_state"] == "not_billed"
        assert by_id[calls["not_dispatched"]]["cost_microusd"] == 0
        assert by_id[calls["not_dispatched"]]["fallback_index"] == 1
        assert by_id[calls["dispatched_unknown"]]["cost_state"] == "unknown"
        assert by_id[calls["dispatched_unknown"]]["cost_microusd"] is None
        assert by_id[calls["estimated"]]["cost_state"] == "estimated"
        assert by_id[calls["estimated"]]["cost_microusd"] == 2800
        assert by_id[calls["timeout_unknown"]]["cost_state"] == "unknown"
        assert by_id[calls["timeout_unknown"]]["cost_microusd"] is None
        assert by_id[calls["timeout_unknown"]]["status"] == "timed_out"
        assert by_id[caught_before_dispatch_call_id]["cost_state"] == "not_billed"
        assert by_id[caught_before_dispatch_call_id]["cost_microusd"] == 0
        for call_id, row in by_id.items():
            assert UUID(str(row["logical_invocation_id"])) == call_id
            assert row["operation"] == "legacy_unclassified"
            assert row["semantic_attempt"] == 1
            assert row["stage_attempt_id"] is None
            assert row["actual_provider"] is None
            assert row["actual_model"] is None
            assert row["cache_read_tokens"] == 0
            assert row["cache_write_tokens"] == 0
            assert row["cost_state"] != "reported"

        async with target_engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE model_calls SET provider_dispatched=true, "
                    "input_tokens=120, output_tokens=30, thinking_tokens=10, "
                    "latency_ms=220, status='completed', cost_microusd=360 "
                    "WHERE id=CAST(:id AS UUID)"
                ),
                {"id": str(caught_before_dispatch_call_id)},
            )
        async with target_engine.connect() as connection:
            finalized_caught_call = (
                await connection.execute(
                    text(
                        "SELECT cost_state, cost_microusd, status, "
                        "pricing_snapshot FROM model_calls "
                        "WHERE id=CAST(:id AS UUID)"
                    ),
                    {"id": str(caught_before_dispatch_call_id)},
                )
            ).mappings().one()
        assert finalized_caught_call["cost_state"] == "estimated"
        assert finalized_caught_call["cost_microusd"] == 360
        assert finalized_caught_call["status"] == "completed"
        assert finalized_caught_call["pricing_snapshot"][
            "_kaigo_0014_legacy"
        ] == {"cost_microusd": 360, "status": "completed"}

        async with target_engine.begin() as connection:
            migrated_in_flight = (
                await connection.execute(
                    text(
                        "SELECT logical_invocation_id, operation, cost_state, "
                        "cost_microusd, status, pricing_snapshot "
                        "FROM model_calls WHERE id=CAST(:id AS UUID)"
                    ),
                    {"id": str(in_flight_call_id)},
                )
            ).mappings().one()
            assert migrated_in_flight["cost_state"] == "unknown"
            assert migrated_in_flight["cost_microusd"] is None
            assert migrated_in_flight["status"] == "dispatched"

            await connection.execute(
                text(
                    "UPDATE model_calls SET latency_ms=110, "
                    "pricing_snapshot=CAST(:pricing AS JSON) "
                    "WHERE id=CAST(:id AS UUID)"
                ),
                {
                    "id": str(in_flight_call_id),
                    "pricing": json.dumps(estimated_snapshot),
                },
            )
            preserved_legacy_marker = (
                await connection.execute(
                    text(
                        "SELECT pricing_snapshot FROM model_calls "
                        "WHERE id=CAST(:id AS UUID)"
                    ),
                    {"id": str(in_flight_call_id)},
                )
            ).scalar_one()
            assert preserved_legacy_marker["_kaigo_0014_legacy"] == {
                "cost_microusd": 0,
                "status": "dispatched",
            }

            await connection.execute(
                text(
                    "UPDATE model_calls SET provider='legacy-in-flight', "
                    "model='legacy-model', role='builder', mode='direct', "
                    "prompt_version='legacy-v1', request_id='legacy-request', "
                    "attempt=1, provider_dispatched=true, input_tokens=120, "
                    "output_tokens=30, thinking_tokens=10, latency_ms=220, "
                    "status='completed', error_code=NULL, error_message=NULL, "
                    "cost_microusd=360, "
                    "pricing_snapshot=CAST(:pricing AS JSON) "
                    "WHERE id=CAST(:id AS UUID)"
                ),
                {
                    "id": str(in_flight_call_id),
                    "pricing": json.dumps(estimated_snapshot),
                },
            )

        async with target_engine.connect() as connection:
            finalized_in_flight = (
                await connection.execute(
                    text(
                        "SELECT logical_invocation_id, operation, cost_state, "
                        "cost_microusd, status, pricing_snapshot "
                        "FROM model_calls WHERE id=CAST(:id AS UUID)"
                    ),
                    {"id": str(in_flight_call_id)},
                )
            ).mappings().one()
        assert UUID(str(finalized_in_flight["logical_invocation_id"])) == (
            in_flight_call_id
        )
        assert finalized_in_flight["operation"] == "legacy_unclassified"
        assert finalized_in_flight["cost_state"] == "estimated"
        assert finalized_in_flight["cost_microusd"] == 360
        assert finalized_in_flight["status"] == "completed"
        assert finalized_in_flight["pricing_snapshot"]["_kaigo_0014_legacy"] == {
            "cost_microusd": 360,
            "status": "completed",
        }

        async with target_engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE model_calls SET status='failed', "
                    "error_code='late_provider_failure', cost_microusd=200 "
                    "WHERE id=CAST(:id AS UUID)"
                ),
                {"id": str(in_flight_call_id)},
            )
        async with target_engine.connect() as connection:
            repeated_legacy_finalization = (
                await connection.execute(
                    text(
                        "SELECT cost_state, cost_microusd, status, "
                        "pricing_snapshot FROM model_calls "
                        "WHERE id=CAST(:id AS UUID)"
                    ),
                    {"id": str(in_flight_call_id)},
                )
            ).mappings().one()
        assert repeated_legacy_finalization["cost_state"] == "estimated"
        assert repeated_legacy_finalization["cost_microusd"] == 200
        assert repeated_legacy_finalization["status"] == "failed"
        assert repeated_legacy_finalization["pricing_snapshot"][
            "_kaigo_0014_legacy"
        ] == {"cost_microusd": 200, "status": "failed"}

        async with target_engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE model_calls SET provider_dispatched=true, "
                    "input_tokens=10, output_tokens=0, thinking_tokens=0, "
                    "latency_ms=75, status='failed', "
                    "error_code='provider_error', cost_microusd=77, "
                    "pricing_snapshot=CAST(:pricing AS JSON) "
                    "WHERE id=CAST(:id AS UUID)"
                ),
                {
                    "id": str(unknown_terminal_call_id),
                    "pricing": json.dumps(incomplete_snapshot),
                },
            )
        async with target_engine.connect() as connection:
            unknown_finalized = (
                await connection.execute(
                    text(
                        "SELECT cost_state, cost_microusd, status, "
                        "pricing_snapshot FROM model_calls "
                        "WHERE id=CAST(:id AS UUID)"
                    ),
                    {"id": str(unknown_terminal_call_id)},
                )
            ).mappings().one()
        assert unknown_finalized["cost_state"] == "unknown"
        assert unknown_finalized["cost_microusd"] is None
        assert unknown_finalized["status"] == "failed"
        assert unknown_finalized["pricing_snapshot"]["_kaigo_0014_legacy"] == {
            "cost_microusd": 77,
            "status": "failed",
        }

        async with target_engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE model_calls SET status='completed', "
                    "cost_state='reported', cost_microusd=456 "
                    "WHERE id=CAST(:id AS UUID)"
                ),
                {"id": str(explicit_writer_call_id)},
            )
        async with target_engine.connect() as connection:
            explicit_finalized = (
                await connection.execute(
                    text(
                        "SELECT cost_state, cost_microusd, pricing_snapshot "
                        "FROM model_calls WHERE id=CAST(:id AS UUID)"
                    ),
                    {"id": str(explicit_writer_call_id)},
                )
            ).mappings().one()
        assert explicit_finalized["cost_state"] == "reported"
        assert explicit_finalized["cost_microusd"] == 456
        assert "_kaigo_0014_legacy" not in explicit_finalized["pricing_snapshot"]

        async with target_engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE model_calls SET "
                    "logical_invocation_id=CAST(:logical AS UUID) "
                    "WHERE id=CAST(:id AS UUID)"
                ),
                {
                    "id": str(explicit_lineage_call_id),
                    "logical": str(explicit_writer_logical_id),
                },
            )
        async with target_engine.connect() as connection:
            explicit_lineage = (
                await connection.execute(
                    text(
                        "SELECT logical_invocation_id, cost_state, "
                        "cost_microusd, pricing_snapshot FROM model_calls "
                        "WHERE id=CAST(:id AS UUID)"
                    ),
                    {"id": str(explicit_lineage_call_id)},
                )
            ).mappings().one()
        assert UUID(str(explicit_lineage["logical_invocation_id"])) == (
            explicit_writer_logical_id
        )
        assert explicit_lineage["cost_state"] == "unknown"
        assert explicit_lineage["cost_microusd"] is None
        assert "_kaigo_0014_legacy" not in explicit_lineage["pricing_snapshot"]

        legacy_live_call_id = uuid4()
        with pytest.raises(IntegrityError):
            async with target_engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO model_calls "
                        "(id, run_id, provider, model, role, mode, "
                        "prompt_version, attempt, provider_dispatched, "
                        "input_tokens, output_tokens, thinking_tokens, "
                        "latency_ms, status, cost_microusd, pricing_snapshot) "
                        "VALUES (CAST(:id AS UUID), CAST(:run_id AS UUID), "
                        "'legacy-live', 'legacy-model', 'builder', 'direct', "
                        "'legacy-v1', 1, false, 0, 0, 0, 1, 'failed', 0, "
                        "CAST('{}' AS JSON))"
                    ),
                    {
                        "id": str(legacy_live_call_id),
                        "run_id": str(run_a),
                    },
                )

        forged_marker_call_id = uuid4()
        forged_snapshot = {
            **estimated_snapshot,
            "_kaigo_0014_legacy": {
                "cost_microusd": 999_999,
                "status": "completed",
            },
        }
        async with target_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO model_calls "
                    "(id, run_id, logical_invocation_id, operation, "
                    "semantic_attempt, fallback_index, provider, model, role, "
                    "mode, prompt_version, attempt, provider_dispatched, "
                    "input_tokens, output_tokens, thinking_tokens, "
                    "cache_read_tokens, cache_write_tokens, latency_ms, "
                    "status, cost_state, cost_microusd, pricing_snapshot) "
                    "VALUES (CAST(:id AS UUID), CAST(:run_id AS UUID), "
                    "CAST(:id AS UUID), 'legacy_unclassified', 1, 1, "
                    "'modern', 'modern-model', 'builder', 'direct', 'v1', 1, "
                    "true, 0, 0, 0, 0, 0, 1, 'dispatched', 'unknown', NULL, "
                    "CAST(:pricing AS JSON))"
                ),
                {
                    "id": str(forged_marker_call_id),
                    "run_id": str(run_a),
                    "pricing": json.dumps(forged_snapshot),
                },
            )
        async with target_engine.connect() as connection:
            sanitized_snapshot = await connection.scalar(
                text(
                    "SELECT pricing_snapshot FROM model_calls "
                    "WHERE id=CAST(:id AS UUID)"
                ),
                {"id": str(forged_marker_call_id)},
            )
        assert "_kaigo_0014_legacy" not in sanitized_snapshot

        async with target_engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE model_calls SET pricing_snapshot=CAST(:pricing AS JSON) "
                    "WHERE id=CAST(:id AS UUID)"
                ),
                {
                    "id": str(forged_marker_call_id),
                    "pricing": json.dumps(forged_snapshot),
                },
            )
        async with target_engine.connect() as connection:
            sanitized_update_snapshot = await connection.scalar(
                text(
                    "SELECT pricing_snapshot FROM model_calls "
                    "WHERE id=CAST(:id AS UUID)"
                ),
                {"id": str(forged_marker_call_id)},
            )
        assert "_kaigo_0014_legacy" not in sanitized_update_snapshot

        with pytest.raises(IntegrityError):
            async with target_engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE model_calls SET input_tokens=120, "
                        "output_tokens=30, status='completed', "
                        "cost_microusd=360 WHERE id=CAST(:id AS UUID)"
                    ),
                    {"id": str(forged_marker_call_id)},
                )

        cost_row_sql = text(
            "INSERT INTO model_calls "
            "(id, run_id, logical_invocation_id, operation, semantic_attempt, "
            "fallback_index, provider, model, role, mode, prompt_version, "
            "attempt, provider_dispatched, input_tokens, output_tokens, "
            "thinking_tokens, cache_read_tokens, cache_write_tokens, "
            "latency_ms, status, cost_state, cost_microusd, pricing_snapshot) "
            "VALUES (CAST(:id AS UUID), CAST(:run_id AS UUID), "
            "CAST(:logical AS UUID), 'cost_constraint', 1, 1, 'test', 'test', "
            "'builder', 'direct', 'v1', 1, :dispatched, 0, 0, 0, 0, 0, 1, "
            "'failed', :cost_state, :cost_microusd, CAST('{}' AS JSON))"
        )

        async def insert_cost_row(
            *, cost_state: str, cost_microusd: int | None, dispatched: bool
        ) -> None:
            async with target_engine.begin() as connection:
                await connection.execute(
                    cost_row_sql,
                    {
                        "id": str(uuid4()),
                        "run_id": str(run_a),
                        "logical": str(uuid4()),
                        "dispatched": dispatched,
                        "cost_state": cost_state,
                        "cost_microusd": cost_microusd,
                    },
                )

        await insert_cost_row(
            cost_state="not_billed", cost_microusd=0, dispatched=False
        )
        await insert_cost_row(
            cost_state="unknown", cost_microusd=None, dispatched=True
        )
        with pytest.raises(IntegrityError):
            await insert_cost_row(
                cost_state="not_billed", cost_microusd=1, dispatched=False
            )
        with pytest.raises(IntegrityError):
            await insert_cost_row(
                cost_state="unknown", cost_microusd=0, dispatched=True
            )

        stage_a = uuid4()
        stage_b = uuid4()
        async with target_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO generation_stage_attempts "
                    "(id, run_id, stage, ordinal, status, started_at) VALUES "
                    "(CAST(:stage_a AS UUID), CAST(:run_a AS UUID), "
                    "'foundation', 1, 'running', now()), "
                    "(CAST(:stage_b AS UUID), CAST(:run_b AS UUID), "
                    "'foundation', 1, 'running', now())"
                ),
                {
                    "stage_a": str(stage_a),
                    "stage_b": str(stage_b),
                    "run_a": str(run_a),
                    "run_b": str(run_b),
                },
            )

        with pytest.raises(IntegrityError):
            async with target_engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO model_calls "
                        "(id, run_id, stage_attempt_id, logical_invocation_id, "
                        "operation, semantic_attempt, fallback_index, provider, "
                        "model, role, mode, prompt_version, attempt, "
                        "provider_dispatched, input_tokens, output_tokens, "
                        "thinking_tokens, cache_read_tokens, cache_write_tokens, "
                        "latency_ms, status, cost_state, cost_microusd, "
                        "pricing_snapshot) VALUES "
                        "(CAST(:id AS UUID), CAST(:run_a AS UUID), "
                        "CAST(:stage_b AS UUID), CAST(:logical AS UUID), "
                        "'cross_run', 1, 1, 'test', 'test', 'builder', 'direct', "
                        "'v1', 1, true, 1, 1, 0, 0, 0, 1, 'completed', "
                        "'estimated', 1, CAST('{}' AS JSON))"
                    ),
                    {
                        "id": str(uuid4()),
                        "run_a": str(run_a),
                        "stage_b": str(stage_b),
                        "logical": str(uuid4()),
                    },
                )

        duplicate_logical = uuid4()
        async with target_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO model_calls "
                    "(id, run_id, stage_attempt_id, logical_invocation_id, "
                    "operation, semantic_attempt, fallback_index, provider, "
                    "model, role, mode, prompt_version, attempt, "
                    "provider_dispatched, input_tokens, output_tokens, "
                    "thinking_tokens, cache_read_tokens, cache_write_tokens, "
                    "latency_ms, status, cost_state, cost_microusd, "
                    "pricing_snapshot) VALUES "
                    "(CAST(:id AS UUID), CAST(:run_a AS UUID), "
                    "CAST(:stage_a AS UUID), CAST(:logical AS UUID), "
                    "'valid', 1, 1, 'test', 'test', 'builder', 'direct', 'v1', "
                    "1, true, 1, 1, 0, 0, 0, 1, 'completed', 'estimated', 1, "
                    "CAST('{}' AS JSON))"
                ),
                {
                    "id": str(uuid4()),
                    "run_a": str(run_a),
                    "stage_a": str(stage_a),
                    "logical": str(duplicate_logical),
                },
            )
        with pytest.raises(IntegrityError):
            async with target_engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO model_calls "
                        "(id, run_id, logical_invocation_id, operation, "
                        "semantic_attempt, fallback_index, provider, model, role, "
                        "mode, prompt_version, attempt, provider_dispatched, "
                        "input_tokens, output_tokens, thinking_tokens, "
                        "cache_read_tokens, cache_write_tokens, latency_ms, status, "
                        "cost_state, cost_microusd, pricing_snapshot) VALUES "
                        "(CAST(:id AS UUID), CAST(:run_a AS UUID), "
                        "CAST(:logical AS UUID), 'duplicate', 1, 1, 'test', "
                        "'test', 'builder', 'direct', 'v1', 1, true, 1, 1, 0, "
                        "0, 0, 1, 'completed', 'estimated', 1, CAST('{}' AS JSON))"
                    ),
                    {
                        "id": str(uuid4()),
                        "run_a": str(run_a),
                        "logical": str(duplicate_logical),
                    },
                )

        await target_engine.dispose()
        target_engine = None
        await asyncio.to_thread(command.downgrade, config, "0013_pattern_registry")
        target_engine = create_async_engine(rendered)
        assert await _revision(target_engine) == "0013_pattern_registry"
        assert not {
            "generation_forensic_manifests",
            "generation_forensic_access_logs",
            "generation_stage_attempts",
        } & await _table_names(target_engine)
        assert not {"registry_version", "public_payload", "forensic_ref"} & (
            await _columns(target_engine, "generation_events")
        )

        async with target_engine.connect() as connection:
            restored_event = (
                await connection.execute(
                    text(
                        "SELECT event_type, public_message, payload "
                        "FROM generation_events WHERE run_id=CAST(:run_id AS UUID)"
                    ),
                    {"run_id": str(run_a)},
                )
            ).mappings().one()
            restored_calls = (
                await connection.execute(
                    text(
                        "SELECT id, status, cost_microusd, pricing_snapshot "
                        "FROM model_calls WHERE provider='legacy'"
                    )
                )
            ).mappings().all()
            restored_in_flight = (
                await connection.execute(
                    text(
                        "SELECT status, cost_microusd, pricing_snapshot "
                        "FROM model_calls WHERE id=CAST(:id AS UUID)"
                    ),
                    {"id": str(in_flight_call_id)},
                )
            ).mappings().one()
            restored_explicit = (
                await connection.execute(
                    text(
                        "SELECT status, cost_microusd, pricing_snapshot "
                        "FROM model_calls WHERE id=CAST(:id AS UUID)"
                    ),
                    {"id": str(explicit_writer_call_id)},
                )
            ).mappings().one()
            restored_explicit_lineage = (
                await connection.execute(
                    text(
                        "SELECT status, cost_microusd, pricing_snapshot "
                        "FROM model_calls WHERE id=CAST(:id AS UUID)"
                    ),
                    {"id": str(explicit_lineage_call_id)},
                )
            ).mappings().one()
            restored_caught_call = (
                await connection.execute(
                    text(
                        "SELECT status, cost_microusd, pricing_snapshot "
                        "FROM model_calls WHERE id=CAST(:id AS UUID)"
                    ),
                    {"id": str(caught_before_dispatch_call_id)},
                )
            ).mappings().one()
            restored_unknown = (
                await connection.execute(
                    text(
                        "SELECT status, cost_microusd, pricing_snapshot "
                        "FROM model_calls WHERE id=CAST(:id AS UUID)"
                    ),
                    {"id": str(unknown_terminal_call_id)},
                )
            ).mappings().one()
        assert restored_event["payload"] == event_payload
        restored_by_id = {UUID(str(row["id"])): row for row in restored_calls}
        assert {key: row["cost_microusd"] for key, row in restored_by_id.items()} == (
            original_costs
        )
        assert {key: row["status"] for key, row in restored_by_id.items()} == (
            original_statuses
        )
        assert restored_by_id[calls["estimated"]]["pricing_snapshot"] == (
            estimated_snapshot
        )
        assert "_kaigo_0014_legacy" not in restored_by_id[
            calls["dispatched_unknown"]
        ]["pricing_snapshot"]
        assert restored_in_flight == {
            "status": "failed",
            "cost_microusd": 200,
            "pricing_snapshot": estimated_snapshot,
        }
        assert restored_unknown == {
            "status": "failed",
            "cost_microusd": 77,
            "pricing_snapshot": incomplete_snapshot,
        }
        assert restored_explicit == {
            "status": "completed",
            "cost_microusd": 456,
            "pricing_snapshot": {},
        }
        assert restored_explicit_lineage == {
            "status": "dispatched",
            "cost_microusd": 0,
            "pricing_snapshot": {},
        }
        assert restored_caught_call == {
            "status": "completed",
            "cost_microusd": 360,
            "pricing_snapshot": estimated_snapshot,
        }

        await target_engine.dispose()
        target_engine = None
        await asyncio.to_thread(command.upgrade, config, "head")
        target_engine = create_async_engine(rendered)
        assert await _revision(target_engine) == "0017_funnel_journeys"
        async with target_engine.connect() as connection:
            reupgraded = (
                await connection.execute(
                    text(
                        "SELECT cost_state, cost_microusd, status "
                        "FROM model_calls WHERE id=CAST(:id AS UUID)"
                    ),
                    {"id": str(calls["timeout_unknown"])},
                )
            ).mappings().one()
        assert reupgraded == {
            "cost_state": "unknown",
            "cost_microusd": None,
            "status": "timed_out",
        }
    finally:
        if target_engine is not None:
            await target_engine.dispose()
        if created:
            async with admin_engine.connect() as connection:
                await connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname=:name AND pid<>pg_backend_pid()"
                    ),
                    {"name": database_name},
                )
                await connection.execute(text(f'DROP DATABASE "{database_name}"'))
        await admin_engine.dispose()
