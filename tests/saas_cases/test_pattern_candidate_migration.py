from __future__ import annotations

import importlib
import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine


POSTGRES_URL = os.getenv("KAIGO_TEST_POSTGRES_URL")
PROJECT_ROOT = Path(__file__).resolve().parents[2]

def test_pattern_candidate_migration_identity_and_named_contracts() -> None:
    migration = importlib.import_module(
        "migrations.versions.0018_stage_aware_pattern_library"
    )
    source = Path(migration.__file__).read_text(encoding="utf-8")
    assert migration.revision == "0018_stage_aware_pattern_library"
    assert migration.down_revision == "0017_funnel_journeys"
    for table in (
        "pattern_candidate_plans",
        "pattern_candidate_groups",
        "pattern_candidate_items",
        "pattern_stage_exposures",
        "pattern_stage_usage_claims",
        "pattern_reviews",
    ):
        assert f'"{table}"' in source
    for constraint in (
        "uq_pattern_candidate_plan_run",
        "uq_pattern_candidate_group_plan_category",
        "uq_pattern_candidate_item_group_rank",
        "uq_pattern_candidate_item_group_pattern_version",
        "uq_pattern_stage_exposure_idempotency",
        "uq_pattern_stage_usage_claim_exposure",
        "ck_pattern_candidate_plan_schema_version",
        "ck_pattern_candidate_item_rank",
        "ck_pattern_stage_exposure_stage",
        "ck_pattern_stage_usage_mode",
        "ck_pattern_review_status",
    ):
        assert constraint in source


def test_pattern_candidate_migration_fake_op_order_and_downgrade(monkeypatch) -> None:
    migration = importlib.import_module(
        "migrations.versions.0018_stage_aware_pattern_library"
    )
    calls: list[tuple[object, ...]] = []

    class FakeOp:
        def create_table(self, name, *items):
            calls.append(("create_table", name, items))

        def create_index(self, name, table, columns, **kwargs):
            calls.append(("create_index", name, table, tuple(columns), kwargs))

        def drop_index(self, name, **kwargs):
            calls.append(("drop_index", name, kwargs))

        def drop_table(self, name):
            calls.append(("drop_table", name))

    monkeypatch.setattr(migration, "op", FakeOp())
    migration.upgrade()
    assert [call[1] for call in calls if call[0] == "create_table"] == [
        "pattern_candidate_plans",
        "pattern_candidate_groups",
        "pattern_candidate_items",
        "pattern_stage_exposures",
        "pattern_stage_usage_claims",
        "pattern_reviews",
    ]

    calls.clear()
    migration.downgrade()
    assert [call[1] for call in calls if call[0] == "drop_table"] == [
        "pattern_reviews",
        "pattern_stage_usage_claims",
        "pattern_stage_exposures",
        "pattern_candidate_items",
        "pattern_candidate_groups",
        "pattern_candidate_plans",
    ]


def test_pattern_candidate_migration_has_named_fk_ondelete_and_indexes(monkeypatch) -> None:
    migration = importlib.import_module(
        "migrations.versions.0018_stage_aware_pattern_library"
    )
    calls: list[tuple[object, ...]] = []

    class FakeOp:
        def create_table(self, name, *items):
            calls.append(("create_table", name, items))

        def create_index(self, name, table, columns, **kwargs):
            calls.append(("create_index", name, table, tuple(columns), kwargs))

    monkeypatch.setattr(migration, "op", FakeOp())
    migration.upgrade()
    tables = {
        call[1]: call[2]
        for call in calls
        if call[0] == "create_table"
    }
    expected_fks = {
        "pattern_candidate_plans": {
            "fk_pattern_candidate_plans_run_id": ("generation_runs.id", "CASCADE"),
            "fk_pattern_candidate_plans_direction_artifact_id": ("generation_artifacts.id", "SET NULL"),
            "fk_pattern_candidate_plans_selector_model_call_id": ("model_calls.id", "SET NULL"),
        },
        "pattern_candidate_groups": {
            "fk_pattern_candidate_groups_plan_id": ("pattern_candidate_plans.id", "CASCADE"),
        },
        "pattern_candidate_items": {
            "fk_pattern_candidate_items_group_id": ("pattern_candidate_groups.id", "CASCADE"),
            "fk_pattern_candidate_items_pattern_version_id": ("widget_pattern_versions.id", "RESTRICT"),
        },
        "pattern_stage_exposures": {
            "fk_pattern_stage_exposures_run_id": ("generation_runs.id", "CASCADE"),
            "fk_pattern_stage_exposures_candidate_item_id": ("pattern_candidate_items.id", "CASCADE"),
            "fk_pattern_stage_exposures_model_call_id": ("model_calls.id", "SET NULL"),
        },
        "pattern_stage_usage_claims": {
            "fk_pattern_stage_usage_claims_exposure_id": ("pattern_stage_exposures.id", "CASCADE"),
            "fk_pattern_stage_usage_claims_model_call_id": ("model_calls.id", "SET NULL"),
        },
        "pattern_reviews": {
            "fk_pattern_reviews_pattern_version_id": ("widget_pattern_versions.id", "RESTRICT"),
        },
    }
    for table, named in expected_fks.items():
        fks = {
            constraint.name: (
                constraint.elements[0].target_fullname,
                constraint.ondelete,
            )
            for constraint in tables[table]
            if isinstance(constraint, sa.ForeignKeyConstraint)
        }
        assert fks == named

    indexes = {
        call[1]: (call[2], call[3])
        for call in calls
        if call[0] == "create_index"
    }
    assert indexes["ix_pattern_candidate_plans_run_id"] == (
        "pattern_candidate_plans",
        ("run_id",),
    )
    assert indexes["ix_pattern_candidate_groups_plan_id"] == (
        "pattern_candidate_groups",
        ("plan_id",),
    )
    assert indexes["ix_pattern_candidate_items_pattern_version_id"] == (
        "pattern_candidate_items",
        ("pattern_version_id",),
    )
    assert indexes["ix_pattern_stage_exposures_model_call_id"] == (
        "pattern_stage_exposures",
        ("model_call_id",),
    )
    assert indexes["ix_pattern_stage_usage_claims_model_call_id"] == (
        "pattern_stage_usage_claims",
        ("model_call_id",),
    )
    assert indexes["ix_pattern_reviews_pattern_version_created_at"] == (
        "pattern_reviews",
        ("pattern_version_id", "created_at"),
    )


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.skipif(
    not POSTGRES_URL,
    reason="KAIGO_TEST_POSTGRES_URL is not configured",
)
async def test_postgres_candidate_migration_round_trips_additive_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_url = make_url(POSTGRES_URL)
    if source_url.drivername == "postgresql":
        source_url = source_url.set(drivername="postgresql+asyncpg")
    database_name = f"kaigo_candidates_{uuid4().hex}"
    admin_url = source_url.set(database="postgres")
    target_url = source_url.set(database=database_name)
    rendered = target_url.render_as_string(hide_password=False)
    admin_engine = create_async_engine(
        admin_url.render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
    )
    target_engine = None
    created = False
    try:
        async with admin_engine.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        created = True
        monkeypatch.setenv("DATABASE_URL", rendered)
        config = Config(str(PROJECT_ROOT / "alembic.ini"))
        await asyncio.to_thread(command.upgrade, config, "0018_stage_aware_pattern_library")
        target_engine = create_async_engine(rendered)
        async with target_engine.connect() as connection:
            revision = await connection.run_sync(
                lambda sync: MigrationContext.configure(sync).get_current_revision()
            )
            tables = await connection.run_sync(
                lambda sync: set(inspect(sync).get_table_names())
            )
        assert revision == "0018_stage_aware_pattern_library"
        assert {
            "pattern_candidate_plans",
            "pattern_candidate_groups",
            "pattern_candidate_items",
            "pattern_stage_exposures",
            "pattern_stage_usage_claims",
            "pattern_reviews",
        } <= tables
        await target_engine.dispose()
        target_engine = None
        await asyncio.to_thread(command.downgrade, config, "0017_funnel_journeys")
        target_engine = create_async_engine(rendered)
        async with target_engine.connect() as connection:
            revision = await connection.run_sync(
                lambda sync: MigrationContext.configure(sync).get_current_revision()
            )
            tables = await connection.run_sync(
                lambda sync: set(inspect(sync).get_table_names())
            )
        assert revision == "0017_funnel_journeys"
        assert not {
            "pattern_candidate_plans",
            "pattern_candidate_groups",
            "pattern_candidate_items",
            "pattern_stage_exposures",
            "pattern_stage_usage_claims",
            "pattern_reviews",
        } & tables
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
