from __future__ import annotations

import asyncio
import importlib
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine


POSTGRES_URL = os.getenv("KAIGO_TEST_POSTGRES_URL")
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_project_recovery_migration_adds_dispatch_marker_and_backfills_non_trial_runs(
    monkeypatch,
) -> None:
    migration = importlib.import_module(
        "migrations.versions.0007_project_recovery_hardening"
    )
    calls = []

    class FakeOp:
        def add_column(self, table, column):
            calls.append(("add_column", table, column.name, column.server_default))

        def alter_column(self, table, column, **kwargs):
            calls.append(("alter_column", table, column, kwargs))

        def execute(self, statement):
            calls.append(("execute", str(statement)))

        def drop_column(self, table, column):
            calls.append(("drop_column", table, column))

    monkeypatch.setattr(migration, "op", FakeOp())

    migration.upgrade()

    assert any(call[:3] == ("add_column", "model_calls", "provider_dispatched") for call in calls)
    backfill = next(
        call[1]
        for call in calls
        if call[0] == "execute" and "not_applicable" in call[1]
    )
    assert "not_applicable" in backfill
    assert "trial.reserve" in backfill

    calls.clear()
    migration.downgrade()
    assert calls == [("drop_column", "model_calls", "provider_dispatched")]


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URL, reason="KAIGO_TEST_POSTGRES_URL is not configured")
async def test_postgres_project_recovery_migration_backfills_historical_rows(
    monkeypatch,
) -> None:
    source_url = make_url(POSTGRES_URL)
    if source_url.drivername == "postgresql":
        source_url = source_url.set(drivername="postgresql+asyncpg")
    database_name = f"kaigo_migration_{uuid4().hex}"
    admin_url = source_url.set(database="postgres")
    target_url = source_url.set(database=database_name)
    rendered_target_url = target_url.render_as_string(hide_password=False)
    admin_engine = create_async_engine(
        admin_url.render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
    )
    target_engine = None
    database_created = False

    try:
        async with admin_engine.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        database_created = True
        monkeypatch.setenv("DATABASE_URL", rendered_target_url)
        alembic_config = Config(str(PROJECT_ROOT / "alembic.ini"))

        await asyncio.to_thread(command.upgrade, alembic_config, "0006_project_api")
        target_engine = create_async_engine(rendered_target_url)

        non_trial_run_id = uuid4()
        reserved_trial_run_id = uuid4()
        project_id = uuid4()
        model_call_id = uuid4()
        finished_at = datetime(2026, 7, 1, 10, 30, tzinfo=timezone.utc)
        async with target_engine.begin() as connection:
            await connection.execute(text(
                "INSERT INTO tenants (id, name, slug) "
                "VALUES (1, 'Migration tenant', 'migration-tenant')"
            ))
            await connection.execute(text(
                "INSERT INTO users (id, tenant_id, email, role) "
                "VALUES (1, 1, 'migration@example.com', 'tenant_admin')"
            ))
            await connection.execute(text(
                "INSERT INTO projects "
                "(id, tenant_id, owner_user_id, source_url, status) "
                "VALUES (CAST(:id AS UUID), 1, 1, 'https://example.com', 'failed')"
            ), {"id": str(project_id)})
            await connection.execute(text(
                "INSERT INTO generation_runs "
                "(id, project_id, mode, state, progress, next_event_sequence, "
                "idempotency_key, finished_at) VALUES "
                "(CAST(:non_trial_id AS UUID), CAST(:project_id AS UUID), "
                "'express', 'completed', 100, 1, 'historical-non-trial', "
                "CAST(:finished_at AS TIMESTAMPTZ)), "
                "(CAST(:reserved_id AS UUID), CAST(:project_id AS UUID), "
                "'express', 'failed', 40, 1, 'historical-reserved-trial', "
                "CAST(:finished_at AS TIMESTAMPTZ))"
            ), {
                "non_trial_id": str(non_trial_run_id),
                "reserved_id": str(reserved_trial_run_id),
                "project_id": str(project_id),
                "finished_at": finished_at,
            })
            await connection.execute(text(
                "INSERT INTO usage_ledger "
                "(id, user_id, project_id, run_id, bucket, entry_type, amount, "
                "idempotency_key, payload) VALUES "
                "(CAST(:id AS UUID), 1, CAST(:project_id AS UUID), "
                "CAST(:run_id AS UUID), 'trial', 'trial.reserve', -1, "
                "'historical-trial-reserve', CAST('{}' AS JSON))"
            ), {
                "id": str(uuid4()),
                "project_id": str(project_id),
                "run_id": str(reserved_trial_run_id),
            })
            await connection.execute(text(
                "INSERT INTO model_calls "
                "(id, run_id, provider, model, role, prompt_version, attempt, "
                "input_tokens, output_tokens, thinking_tokens, latency_ms, status, "
                "cost_microusd, pricing_snapshot, mode) VALUES "
                "(CAST(:id AS UUID), CAST(:run_id AS UUID), 'openai', 'test-model', "
                "'builder', 'v1', 1, 10, 20, 0, 50, 'completed', 100, "
                "CAST('{}' AS JSON), 'direct')"
            ), {"id": str(model_call_id), "run_id": str(reserved_trial_run_id)})

        await target_engine.dispose()
        target_engine = None
        await asyncio.to_thread(command.upgrade, alembic_config, "head")
        target_engine = create_async_engine(rendered_target_url)

        async with target_engine.connect() as connection:
            run_rows = (await connection.execute(text(
                "SELECT id, trial_settlement, trial_settled_at "
                "FROM generation_runs ORDER BY id"
            ))).mappings().all()
            rows_by_id = {str(row["id"]): row for row in run_rows}
            non_trial = rows_by_id[str(non_trial_run_id)]
            reserved_trial = rows_by_id[str(reserved_trial_run_id)]
            provider_dispatched = await connection.scalar(text(
                "SELECT provider_dispatched FROM model_calls "
                "WHERE id = CAST(:id AS UUID)"
            ), {"id": str(model_call_id)})
            current_revision = await connection.run_sync(
                lambda sync_connection: MigrationContext.configure(sync_connection).get_current_revision()
            )

        assert non_trial["trial_settlement"] == "not_applicable"
        assert non_trial["trial_settled_at"] == finished_at
        assert reserved_trial["trial_settlement"] is None
        assert reserved_trial["trial_settled_at"] is None
        assert provider_dispatched is True
        assert current_revision == "0013_pattern_registry"
    finally:
        if target_engine is not None:
            await target_engine.dispose()
        if database_created:
            async with admin_engine.connect() as connection:
                await connection.execute(text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                ), {"database_name": database_name})
                await connection.execute(text(f'DROP DATABASE "{database_name}"'))
        await admin_engine.dispose()
