from __future__ import annotations

import asyncio
import importlib
import os
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


POSTGRES_URL = os.getenv("KAIGO_TEST_POSTGRES_URL")
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _migration() -> ModuleType:
    try:
        return importlib.import_module("migrations.versions.0017_funnel_journeys")
    except ModuleNotFoundError:
        pytest.fail("0017_funnel_journeys migration is missing")


def test_funnel_journey_migration_is_additive_after_project_versions() -> None:
    migration = _migration()
    source = Path(migration.__file__).read_text(encoding="utf-8")

    assert migration.revision == "0017_funnel_journeys"
    assert migration.down_revision == "0016_project_versions"
    assert 'create_table(\n        "funnel_journeys"' in source
    for table in (
        "anonymous_drafts",
        "oauth_states",
        "projects",
        "generation_runs",
        "payment_attempts",
        "publications",
        "funnel_events",
    ):
        assert f'"{table}"' in source
    assert '"project_id"' in source
    assert 'ondelete="SET NULL"' in source
    assert 'op.drop_table("funnel_journeys")' in source

    upgrade_source = source.split("def upgrade() -> None:", 1)[1].split(
        "def downgrade() -> None:", 1
    )[0]
    normalized_upgrade = " ".join(upgrade_source.lower().split())
    assert "op.execute(" not in upgrade_source
    assert " update " not in f" {normalized_upgrade} "
    assert " delete " not in f" {normalized_upgrade} "


def test_funnel_journey_model_is_privacy_minimal() -> None:
    from app.saas.models import FunnelJourney

    columns = {column.name for column in inspect(FunnelJourney).columns}
    assert columns == {
        "id",
        "campaign_source",
        "campaign_medium",
        "campaign_name",
        "campaign_term",
        "campaign_content",
        "started_at",
    }
    assert columns.isdisjoint(
        {
            "user_id",
            "tenant_id",
            "session_id",
            "source_url",
            "url",
            "prompt",
            "brief",
            "email",
            "ip",
            "ip_address",
            "profile",
            "payload",
        }
    )


def test_funnel_journey_models_expose_only_nullable_indexed_linkage() -> None:
    from app.saas.models import (
        AnonymousDraft,
        FunnelEvent,
        GenerationRun,
        OAuthState,
        PaymentAttempt,
        Project,
        Publication,
    )

    models = (
        AnonymousDraft,
        OAuthState,
        Project,
        GenerationRun,
        PaymentAttempt,
        Publication,
        FunnelEvent,
    )
    for model in models:
        column = inspect(model).columns.journey_id
        assert column.nullable is True
        assert column.index is True
        foreign_key = next(iter(column.foreign_keys))
        assert foreign_key.target_fullname == "funnel_journeys.id"
        assert foreign_key.ondelete == "SET NULL"

    payment_project = inspect(PaymentAttempt).columns.project_id
    assert payment_project.nullable is True
    assert payment_project.index is True
    payment_project_fk = next(iter(payment_project.foreign_keys))
    assert payment_project_fk.target_fullname == "projects.id"
    assert payment_project_fk.ondelete == "SET NULL"

    funnel_indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in FunnelEvent.__table__.indexes
    }
    assert funnel_indexes["ix_funnel_events_journey_type_occurred"] == (
        "journey_id",
        "event_type",
        "occurred_at",
    )


def test_funnel_journey_migration_orders_additive_operations_and_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _migration()
    calls: list[tuple[object, ...]] = []

    class FakeOp:
        def create_table(self, name, *items):
            calls.append(("create_table", name, items))

        def add_column(self, table, column):
            calls.append(("add_column", table, column.name, column))

        def create_foreign_key(self, name, source, referent, local, remote, **kwargs):
            calls.append(
                (
                    "create_foreign_key",
                    name,
                    source,
                    referent,
                    tuple(local),
                    tuple(remote),
                    kwargs,
                )
            )

        def create_index(self, name, table, columns, **kwargs):
            calls.append(("create_index", name, table, tuple(columns), kwargs))

        def drop_index(self, name, **kwargs):
            calls.append(("drop_index", name, kwargs))

        def drop_constraint(self, name, table, **kwargs):
            calls.append(("drop_constraint", name, table, kwargs))

        def drop_column(self, table, column):
            calls.append(("drop_column", table, column))

        def drop_table(self, name):
            calls.append(("drop_table", name))

    monkeypatch.setattr(migration, "op", FakeOp())
    migration.upgrade()

    assert calls[0][0:2] == ("create_table", "funnel_journeys")
    assert calls[1] == (
        "create_index",
        "ix_funnel_journeys_started_at",
        "funnel_journeys",
        ("started_at",),
        {},
    )

    expected_columns = {
        ("anonymous_drafts", "journey_id"),
        ("oauth_states", "journey_id"),
        ("projects", "journey_id"),
        ("generation_runs", "journey_id"),
        ("payment_attempts", "project_id"),
        ("payment_attempts", "journey_id"),
        ("publications", "journey_id"),
        ("funnel_events", "journey_id"),
    }
    assert {
        (call[1], call[2]) for call in calls if call[0] == "add_column"
    } == expected_columns
    for call in calls:
        if call[0] == "add_column":
            assert call[3].nullable is True

    expected_foreign_keys = {
        "fk_anonymous_drafts_journey_id",
        "fk_oauth_states_journey_id",
        "fk_projects_journey_id",
        "fk_generation_runs_journey_id",
        "fk_payment_attempts_project_id",
        "fk_payment_attempts_journey_id",
        "fk_publications_journey_id",
        "fk_funnel_events_journey_id",
    }
    foreign_keys = {call[1]: call for call in calls if call[0] == "create_foreign_key"}
    assert set(foreign_keys) == expected_foreign_keys
    assert all(call[6] == {"ondelete": "SET NULL"} for call in foreign_keys.values())

    expected_indexes = {
        "ix_anonymous_drafts_journey_id",
        "ix_oauth_states_journey_id",
        "ix_projects_journey_id",
        "ix_generation_runs_journey_id",
        "ix_payment_attempts_project_id",
        "ix_payment_attempts_journey_id",
        "ix_publications_journey_id",
        "ix_funnel_events_journey_id",
        "ix_funnel_events_journey_type_occurred",
    }
    assert {
        call[1]
        for call in calls
        if call[0] == "create_index" and call[2] != "funnel_journeys"
    } == expected_indexes

    last_add = max(index for index, call in enumerate(calls) if call[0] == "add_column")
    first_fk = min(
        index for index, call in enumerate(calls) if call[0] == "create_foreign_key"
    )
    last_fk = max(
        index for index, call in enumerate(calls) if call[0] == "create_foreign_key"
    )
    first_link_index = min(
        index
        for index, call in enumerate(calls)
        if call[0] == "create_index" and call[2] != "funnel_journeys"
    )
    assert last_add < first_fk <= last_fk < first_link_index

    calls.clear()
    migration.downgrade()

    first_drop_index = min(
        index for index, call in enumerate(calls) if call[0] == "drop_index"
    )
    last_drop_index = max(
        index for index, call in enumerate(calls) if call[0] == "drop_index"
    )
    first_drop_fk = min(
        index for index, call in enumerate(calls) if call[0] == "drop_constraint"
    )
    last_drop_fk = max(
        index for index, call in enumerate(calls) if call[0] == "drop_constraint"
    )
    first_drop_column = min(
        index for index, call in enumerate(calls) if call[0] == "drop_column"
    )
    table_drop = calls.index(("drop_table", "funnel_journeys"))
    assert first_drop_index <= last_drop_index < first_drop_fk
    assert first_drop_fk <= last_drop_fk < first_drop_column < table_drop
    assert not any(call[0] == "execute" for call in calls)


async def _revision(engine: AsyncEngine) -> str | None:
    async with engine.connect() as connection:
        return await connection.run_sync(
            lambda sync: MigrationContext.configure(sync).get_current_revision()
        )


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.skipif(
    not POSTGRES_URL, reason="KAIGO_TEST_POSTGRES_URL is not configured"
)
async def test_postgres_funnel_journey_migration_preserves_legacy_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_url = make_url(POSTGRES_URL)
    if source_url.drivername == "postgresql":
        source_url = source_url.set(drivername="postgresql+asyncpg")
    database_name = f"kaigo_funnel_journey_{uuid4().hex}"
    admin_url = source_url.set(database="postgres")
    target_url = source_url.set(database=database_name)
    rendered = target_url.render_as_string(hide_password=False)
    admin_engine = create_async_engine(
        admin_url.render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
    )
    target_engine: AsyncEngine | None = None
    created = False

    draft_id = uuid4()
    oauth_id = uuid4()
    project_id = uuid4()
    run_id = uuid4()
    payment_id = uuid4()
    publication_id = uuid4()
    event_id = uuid4()
    journey_id = uuid4()

    try:
        async with admin_engine.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        created = True
        monkeypatch.setenv("DATABASE_URL", rendered)
        config = Config(str(PROJECT_ROOT / "alembic.ini"))
        await asyncio.to_thread(command.upgrade, config, "0016_project_versions")
        target_engine = create_async_engine(rendered)

        async with target_engine.begin() as connection:
            await connection.execute(
                text("INSERT INTO tenants (id,name,slug) VALUES (1,'T','t')")
            )
            await connection.execute(
                text(
                    "INSERT INTO users (id,tenant_id,email,role) "
                    "VALUES (1,1,'journey@example.com','tenant_admin')"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO anonymous_drafts "
                    "(id,source_url,campaign,claim_token_digest,expires_at) "
                    "VALUES (CAST(:id AS UUID),'https://example.com',"
                    "CAST('{}' AS JSON),'draft-digest',now() + interval '1 hour')"
                ),
                {"id": str(draft_id)},
            )
            await connection.execute(
                text(
                    "INSERT INTO oauth_states "
                    "(id,state_digest,session_binding_digest,provider,pkce_verifier,"
                    "return_path,draft_id,expires_at) VALUES "
                    "(CAST(:id AS UUID),'state-digest','session-digest','yandex',"
                    "'verifier','/studio',CAST(:draft AS UUID),now() + interval '1 hour')"
                ),
                {"id": str(oauth_id), "draft": str(draft_id)},
            )
            await connection.execute(
                text(
                    "INSERT INTO projects "
                    "(id,tenant_id,owner_user_id,source_url,status) VALUES "
                    "(CAST(:id AS UUID),1,1,'https://example.com','completed')"
                ),
                {"id": str(project_id)},
            )
            await connection.execute(
                text(
                    "INSERT INTO generation_runs "
                    "(id,project_id,mode,state,progress,next_event_sequence,"
                    "idempotency_key) VALUES (CAST(:id AS UUID),"
                    "CAST(:project AS UUID),'express','completed',100,1,'journey-run')"
                ),
                {"id": str(run_id), "project": str(project_id)},
            )
            await connection.execute(
                text(
                    "INSERT INTO payment_attempts "
                    "(id,user_id,provider,merchant_account_fingerprint,purpose,"
                    "auto_renew_requested,idempotency_key,plan_code,plan_snapshot,"
                    "plan_fingerprint,amount_minor,currency,status,payload) VALUES "
                    "(CAST(:id AS UUID),1,'yookassa',:fingerprint,'initial',false,"
                    "'journey-payment','starter_monthly',CAST('{}' AS JSON),"
                    ":fingerprint,10000,'RUB','pending',CAST('{}' AS JSON))"
                ),
                {"id": str(payment_id), "fingerprint": "f" * 64},
            )
            await connection.execute(
                text(
                    "INSERT INTO publications "
                    "(id,project_id,stable_key,allowed_domains,state) VALUES "
                    "(CAST(:id AS UUID),CAST(:project AS UUID),'journey-publication',"
                    "CAST('[]' AS JSON),'published')"
                ),
                {"id": str(publication_id), "project": str(project_id)},
            )
            await connection.execute(
                text(
                    "INSERT INTO funnel_events "
                    "(id,event_key,event_type,anonymous_draft_id,oauth_state_id,"
                    "user_id,project_id,run_id,payment_attempt_id,publication_id) "
                    "VALUES (CAST(:id AS UUID),'journey-event','run_queued',"
                    "CAST(:draft AS UUID),CAST(:oauth AS UUID),1,"
                    "CAST(:project AS UUID),CAST(:run AS UUID),"
                    "CAST(:payment AS UUID),CAST(:publication AS UUID))"
                ),
                {
                    "id": str(event_id),
                    "draft": str(draft_id),
                    "oauth": str(oauth_id),
                    "project": str(project_id),
                    "run": str(run_id),
                    "payment": str(payment_id),
                    "publication": str(publication_id),
                },
            )

        await asyncio.to_thread(command.upgrade, config, "0017_funnel_journeys")
        assert await _revision(target_engine) == "0017_funnel_journeys"

        linked_tables = (
            "anonymous_drafts",
            "oauth_states",
            "projects",
            "generation_runs",
            "payment_attempts",
            "publications",
            "funnel_events",
        )
        async with target_engine.connect() as connection:
            for table in linked_tables:
                assert (
                    await connection.scalar(text(f"SELECT count(*) FROM {table}")) == 1
                )
                assert (
                    await connection.scalar(
                        text(
                            f"SELECT count(*) FROM {table} WHERE journey_id IS NOT NULL"
                        )
                    )
                    == 0
                )
            assert (
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM payment_attempts WHERE project_id IS NOT NULL"
                    )
                )
                == 0
            )

        async with target_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO funnel_journeys "
                    "(id,campaign_source,campaign_name) "
                    "VALUES (CAST(:id AS UUID),'telegram','launch')"
                ),
                {"id": str(journey_id)},
            )
            for table in linked_tables:
                await connection.execute(
                    text(f"UPDATE {table} SET journey_id=CAST(:journey AS UUID)"),
                    {"journey": str(journey_id)},
                )
            await connection.execute(
                text("UPDATE payment_attempts SET project_id=CAST(:project AS UUID)"),
                {"project": str(project_id)},
            )
            await connection.execute(
                text("DELETE FROM funnel_journeys WHERE id=CAST(:id AS UUID)"),
                {"id": str(journey_id)},
            )

        async with target_engine.connect() as connection:
            for table in linked_tables:
                assert (
                    await connection.scalar(text(f"SELECT count(*) FROM {table}")) == 1
                )
                assert (
                    await connection.scalar(
                        text(f"SELECT count(*) FROM {table} WHERE journey_id IS NULL")
                    )
                    == 1
                )
            assert (
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM payment_attempts WHERE project_id=:project"
                    ),
                    {"project": project_id},
                )
                == 1
            )

        await asyncio.to_thread(command.downgrade, config, "0016_project_versions")
        assert await _revision(target_engine) == "0016_project_versions"
        async with target_engine.connect() as connection:
            columns = await connection.run_sync(
                lambda sync: {
                    table: {
                        column["name"] for column in inspect(sync).get_columns(table)
                    }
                    for table in linked_tables
                }
            )
            assert all("journey_id" not in names for names in columns.values())
            assert "project_id" not in columns["payment_attempts"]
            assert await connection.scalar(text("SELECT count(*) FROM projects")) == 1
            assert (
                await connection.scalar(text("SELECT count(*) FROM payment_attempts"))
                == 1
            )
            assert (
                await connection.scalar(text("SELECT count(*) FROM publications")) == 1
            )
            assert (
                await connection.scalar(text("SELECT count(*) FROM funnel_events")) == 1
            )
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
