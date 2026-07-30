from __future__ import annotations

import asyncio
import importlib
import json
import os
from pathlib import Path
from types import ModuleType
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.publication.service import PublicationService


POSTGRES_URL = os.getenv("KAIGO_TEST_POSTGRES_URL")
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _migration() -> ModuleType:
    try:
        return importlib.import_module("migrations.versions.0016_project_versions")
    except ModuleNotFoundError:
        pytest.fail("0016_project_versions migration is missing")


def test_project_versions_migration_follows_recurring_foundation() -> None:
    migration = _migration()

    assert migration.revision == "0016_project_versions"
    assert migration.down_revision == "0015_yookassa_recurring_foundation"


def test_project_versions_migration_is_additive_and_backfills_exact_active_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _migration()
    calls: list[tuple[object, ...]] = []

    class FakeOp:
        def create_unique_constraint(self, name, table, columns):
            calls.append(("create_unique_constraint", name, table, tuple(columns)))

        def create_table(self, name, *items):
            calls.append(("create_table", name, items))

        def create_index(self, name, table, columns, **kwargs):
            calls.append(("create_index", name, table, tuple(columns), kwargs))

        def add_column(self, table, column):
            calls.append(("add_column", table, column.name, column))

        def execute(self, statement):
            calls.append(("execute", str(statement)))

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

        def drop_constraint(self, name, table, **kwargs):
            calls.append(("drop_constraint", name, table, kwargs))

        def drop_column(self, table, column):
            calls.append(("drop_column", table, column))

        def drop_index(self, name, **kwargs):
            calls.append(("drop_index", name, kwargs))

        def drop_table(self, name):
            calls.append(("drop_table", name))

    monkeypatch.setattr(migration, "op", FakeOp())
    migration.upgrade()

    assert (
        "create_unique_constraint",
        "uq_generation_run_membership",
        "generation_runs",
        ("project_id", "id"),
    ) in calls
    assert (
        "create_unique_constraint",
        "uq_generation_artifact_membership",
        "generation_artifacts",
        ("run_id", "id"),
    ) in calls
    create_table = next(
        call for call in calls if call[:2] == ("create_table", "project_versions")
    )
    table_items = create_table[2]
    assert {item.name for item in table_items if hasattr(item, "type")} == {
        "id",
        "project_id",
        "ordinal",
        "run_id",
        "artifact_id",
        "parent_version_id",
        "kind",
        "change_request",
        "idempotency_key",
        "created_at",
    }
    assert {item.name for item in table_items if getattr(item, "name", None)} >= {
        "uq_project_version_ordinal",
        "uq_project_version_membership",
        "uq_project_version_restore_idempotency",
        "ck_project_version_ordinal_positive",
        "ck_project_version_kind",
        "ck_project_version_shape",
        "fk_project_versions_project_id",
    }
    assert {
        call[1]
        for call in calls
        if call[0] == "create_index" and call[2] == "project_versions"
    } == {
        "ix_project_versions_project_id",
        "ix_project_versions_run_id",
        "ix_project_versions_artifact_id",
        "ix_project_versions_parent_version_id",
    }
    assert {
        ("add_column", "projects", "active_version_id"),
        ("add_column", "generation_runs", "source_version_id"),
        ("add_column", "generation_runs", "change_request"),
        ("add_column", "publication_releases", "project_version_id"),
    } <= {call[:3] for call in calls if call[0] == "add_column"}

    sql = "\n".join(call[1] for call in calls if call[0] == "execute")
    normalized_sql = " ".join(sql.split()).lower()
    candidate_tiers = normalized_sql.split("union all")
    assert "insert into project_versions" in normalized_sql
    assert "row_number() over" in normalized_sql
    assert "active_run_id" in normalized_sql
    assert "active_revision" in normalized_sql
    assert "quality_status in ('accepted', 'verified')" in normalized_sql
    assert "active_release_id" in normalized_sql
    assert all(
        "quality_status in ('accepted', 'verified')" in candidate_tier
        for candidate_tier in candidate_tiers[:3]
    )
    assert all(
        "run.state = 'completed'" in candidate_tier
        for candidate_tier in candidate_tiers[:3]
    )
    assert all(
        f"{priority} as priority" in candidate_tier
        for priority, candidate_tier in enumerate(candidate_tiers[:3], start=1)
    )
    assert (
        "order by priority, revision desc, created_at desc, artifact_id"
        in normalized_sql
    )
    assert "update projects" in normalized_sql
    assert normalized_sql.index("active_revision") < normalized_sql.index(
        "quality_status in ('accepted', 'verified')"
    )
    assert normalized_sql.index("quality_status in ('accepted', 'verified')") < (
        normalized_sql.index("active_release_id")
    )
    assert "update generation_artifacts" not in normalized_sql
    assert "update generation_runs" not in normalized_sql
    assert "update publication_releases" not in normalized_sql
    assert "update publications" not in normalized_sql
    assert "delete from" not in normalized_sql

    foreign_keys = {call[1]: call for call in calls if call[0] == "create_foreign_key"}
    assert foreign_keys["fk_projects_active_version_membership"][4:6] == (
        ("id", "active_version_id"),
        ("project_id", "id"),
    )
    assert foreign_keys["fk_projects_active_version_membership"][6] == {
        "deferrable": True,
        "initially": "DEFERRED",
    }
    assert foreign_keys["fk_generation_runs_source_version_membership"][4:6] == (
        ("project_id", "source_version_id"),
        ("project_id", "id"),
    )
    assert foreign_keys["fk_generation_runs_source_version_membership"][6] == {
        "deferrable": True,
        "initially": "DEFERRED",
    }

    calls.clear()
    migration.downgrade()

    assert not any(call[0] == "execute" for call in calls)
    assert (
        "drop_table",
        "project_versions",
    ) in calls
    assert {(call[1], call[2]) for call in calls if call[0] == "drop_column"} == {
        ("publication_releases", "project_version_id"),
        ("generation_runs", "change_request"),
        ("generation_runs", "source_version_id"),
        ("projects", "active_version_id"),
    }
    assert {call[1] for call in calls if call[0] == "drop_constraint"} == {
        "fk_publication_releases_project_version_id",
        "fk_generation_runs_source_version_membership",
        "fk_projects_active_version_membership",
        "fk_project_versions_parent_membership",
        "fk_project_versions_artifact_membership",
        "fk_project_versions_run_membership",
        "uq_generation_artifact_membership",
        "uq_generation_run_membership",
    }


async def _revision(engine: AsyncEngine) -> str | None:
    async with engine.connect() as connection:
        return await connection.run_sync(
            lambda sync: MigrationContext.configure(sync).get_current_revision()
        )


async def _expect_integrity_error(
    engine: AsyncEngine,
    statement: str,
    parameters: dict[str, object],
) -> None:
    with pytest.raises(IntegrityError):
        async with engine.begin() as connection:
            await connection.execute(text(statement), parameters)


def _manifest(artifact_id: UUID, *, revision: int, stage: str) -> dict[str, object]:
    return {
        "version": 1,
        "artifact_id": str(artifact_id),
        "artifact": {
            "schema_version": 1,
            "revision": revision,
            "stage": stage,
            "body_html": f"<p>revision {revision}</p>",
            "css": f".revision-{revision} {{ color: black; }}",
            "javascript": "",
            "theme_tokens": {},
            "layout_contract": {},
        },
    }


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.skipif(
    not POSTGRES_URL, reason="KAIGO_TEST_POSTGRES_URL is not configured"
)
async def test_postgres_project_versions_backfill_preserves_manifest_v1_releases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_url = make_url(POSTGRES_URL)
    if source_url.drivername == "postgresql":
        source_url = source_url.set(drivername="postgresql+asyncpg")
    database_name = f"kaigo_project_versions_{uuid4().hex}"
    admin_url = source_url.set(database="postgres")
    target_url = source_url.set(database=database_name)
    rendered = target_url.render_as_string(hide_password=False)
    admin_engine = create_async_engine(
        admin_url.render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
    )
    target_engine: AsyncEngine | None = None
    created = False

    project_id = uuid4()
    run_id = uuid4()
    artifact_ids = (uuid4(), uuid4(), uuid4())
    publication_id = uuid4()
    historical_release_id = uuid4()
    active_release_id = uuid4()
    historical_manifest = _manifest(artifact_ids[2], revision=3, stage="validation")
    active_manifest = _manifest(artifact_ids[0], revision=1, stage="conversation")
    historical_checksum = PublicationService.manifest_checksum(historical_manifest)
    active_checksum = PublicationService.manifest_checksum(active_manifest)
    fallback_project_id = uuid4()
    fallback_run_id = uuid4()
    fallback_artifact_ids = (uuid4(), uuid4())
    release_project_id = uuid4()
    release_run_id = uuid4()
    release_artifact_id = uuid4()
    release_publication_id = uuid4()
    release_only_id = uuid4()
    release_manifest = _manifest(release_artifact_id, revision=1, stage="validation")
    release_checksum = PublicationService.manifest_checksum(release_manifest)

    try:
        async with admin_engine.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        created = True
        monkeypatch.setenv("DATABASE_URL", rendered)
        config = Config(str(PROJECT_ROOT / "alembic.ini"))
        await asyncio.to_thread(
            command.upgrade, config, "0015_yookassa_recurring_foundation"
        )
        target_engine = create_async_engine(rendered)

        async with target_engine.begin() as connection:
            await connection.execute(
                text("INSERT INTO tenants (id, name, slug) VALUES (1, 'T', 't')")
            )
            await connection.execute(
                text(
                    "INSERT INTO users (id, tenant_id, email, role) "
                    "VALUES (1, 1, 'versions@example.com', 'tenant_admin')"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO projects "
                    "(id, tenant_id, owner_user_id, source_url, status, "
                    "active_run_id, active_revision) VALUES "
                    "(CAST(:id AS UUID), 1, 1, 'https://example.com', "
                    "'completed', CAST(:run_id AS UUID), 2)"
                ),
                {"id": str(project_id), "run_id": str(run_id)},
            )
            await connection.execute(
                text(
                    "INSERT INTO generation_runs "
                    "(id, project_id, mode, state, progress, next_event_sequence, "
                    "idempotency_key) VALUES "
                    "(CAST(:id AS UUID), CAST(:project_id AS UUID), 'express', "
                    "'completed', 100, 1, 'project-version-backfill')"
                ),
                {"id": str(run_id), "project_id": str(project_id)},
            )
            for revision, artifact_id, stage, quality in (
                (1, artifact_ids[0], "conversation", "accepted"),
                (2, artifact_ids[1], "code", "accepted"),
                (3, artifact_ids[2], "validation", "verified"),
            ):
                manifest = _manifest(artifact_id, revision=revision, stage=stage)
                artifact = manifest["artifact"]
                assert isinstance(artifact, dict)
                await connection.execute(
                    text(
                        "INSERT INTO generation_artifacts "
                        "(id, run_id, revision, stage, html, css, javascript, "
                        "config, quality_status, provenance) VALUES "
                        "(CAST(:id AS UUID), CAST(:run_id AS UUID), :revision, "
                        ":stage, :html, :css, :javascript, CAST(:config AS JSON), "
                        ":quality, CAST('{}' AS JSON))"
                    ),
                    {
                        "id": str(artifact_id),
                        "run_id": str(run_id),
                        "revision": revision,
                        "stage": stage,
                        "html": artifact["body_html"],
                        "css": artifact["css"],
                        "javascript": artifact["javascript"],
                        "config": json.dumps({"artifact": artifact}),
                        "quality": quality,
                    },
                )
            await connection.execute(
                text(
                    "INSERT INTO publications "
                    "(id, project_id, stable_key, allowed_domains, state) VALUES "
                    "(CAST(:id AS UUID), CAST(:project_id AS UUID), :stable_key, "
                    "CAST(:domains AS JSON), 'published')"
                ),
                {
                    "id": str(publication_id),
                    "project_id": str(project_id),
                    "stable_key": "legacy-project-version-backfill",
                    "domains": json.dumps(["https://example.com"]),
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO publication_releases "
                    "(id, publication_id, artifact_id, previous_release_id, "
                    "revision, asset_manifest, checksum) VALUES "
                    "(CAST(:historical_id AS UUID), CAST(:publication_id AS UUID), "
                    "CAST(:historical_artifact AS UUID), NULL, 3, "
                    "CAST(:historical_manifest AS JSON), :historical_checksum), "
                    "(CAST(:active_id AS UUID), CAST(:publication_id AS UUID), "
                    "CAST(:active_artifact AS UUID), CAST(:historical_id AS UUID), "
                    "1, CAST(:active_manifest AS JSON), :active_checksum)"
                ),
                {
                    "historical_id": str(historical_release_id),
                    "active_id": str(active_release_id),
                    "publication_id": str(publication_id),
                    "historical_artifact": str(artifact_ids[2]),
                    "active_artifact": str(artifact_ids[0]),
                    "historical_manifest": json.dumps(historical_manifest),
                    "active_manifest": json.dumps(active_manifest),
                    "historical_checksum": historical_checksum,
                    "active_checksum": active_checksum,
                },
            )
            await connection.execute(
                text(
                    "UPDATE publications SET active_release_id=CAST(:release AS UUID) "
                    "WHERE id=CAST(:publication AS UUID)"
                ),
                {
                    "release": str(active_release_id),
                    "publication": str(publication_id),
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO projects "
                    "(id, tenant_id, owner_user_id, source_url, status, "
                    "active_run_id, active_revision) VALUES "
                    "(CAST(:fallback_project AS UUID), 1, 1, "
                    "'https://fallback.example.com', 'completed', "
                    "CAST(:fallback_run AS UUID), NULL), "
                    "(CAST(:release_project AS UUID), 1, 1, "
                    "'https://release.example.com', 'completed', NULL, NULL)"
                ),
                {
                    "fallback_project": str(fallback_project_id),
                    "fallback_run": str(fallback_run_id),
                    "release_project": str(release_project_id),
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO generation_runs "
                    "(id, project_id, mode, state, progress, next_event_sequence, "
                    "idempotency_key) VALUES "
                    "(CAST(:fallback_run AS UUID), "
                    "CAST(:fallback_project AS UUID), 'express', 'completed', "
                    "100, 1, 'fallback-project-version'), "
                    "(CAST(:release_run AS UUID), CAST(:release_project AS UUID), "
                    "'express', 'completed', 100, 1, 'release-project-version')"
                ),
                {
                    "fallback_run": str(fallback_run_id),
                    "fallback_project": str(fallback_project_id),
                    "release_run": str(release_run_id),
                    "release_project": str(release_project_id),
                },
            )
            for revision, artifact_id, quality in (
                (1, fallback_artifact_ids[0], "accepted"),
                (2, fallback_artifact_ids[1], "verified"),
            ):
                await connection.execute(
                    text(
                        "INSERT INTO generation_artifacts "
                        "(id, run_id, revision, stage, html, css, javascript, "
                        "config, quality_status, provenance) VALUES "
                        "(CAST(:id AS UUID), CAST(:run_id AS UUID), :revision, "
                        "'validation', :html, :css, '', CAST(:config AS JSON), "
                        ":quality, CAST('{}' AS JSON))"
                    ),
                    {
                        "id": str(artifact_id),
                        "run_id": str(fallback_run_id),
                        "revision": revision,
                        "html": f"<p>fallback {revision}</p>",
                        "css": f".fallback-{revision} {{ color: black; }}",
                        "config": json.dumps({}),
                        "quality": quality,
                    },
                )
            release_artifact = release_manifest["artifact"]
            assert isinstance(release_artifact, dict)
            await connection.execute(
                text(
                    "INSERT INTO generation_artifacts "
                    "(id, run_id, revision, stage, html, css, javascript, config, "
                    "quality_status, provenance) VALUES "
                    "(CAST(:id AS UUID), CAST(:run_id AS UUID), 1, 'validation', "
                    ":html, :css, '', CAST(:config AS JSON), 'accepted', "
                    "CAST('{}' AS JSON))"
                ),
                {
                    "id": str(release_artifact_id),
                    "run_id": str(release_run_id),
                    "html": release_artifact["body_html"],
                    "css": release_artifact["css"],
                    "config": json.dumps({"artifact": release_artifact}),
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO publications "
                    "(id, project_id, stable_key, allowed_domains, state) VALUES "
                    "(CAST(:id AS UUID), CAST(:project_id AS UUID), :stable_key, "
                    "CAST(:domains AS JSON), 'published')"
                ),
                {
                    "id": str(release_publication_id),
                    "project_id": str(release_project_id),
                    "stable_key": "legacy-release-only-backfill",
                    "domains": json.dumps(["https://release.example.com"]),
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO publication_releases "
                    "(id, publication_id, artifact_id, previous_release_id, "
                    "revision, asset_manifest, checksum) VALUES "
                    "(CAST(:id AS UUID), CAST(:publication_id AS UUID), "
                    "CAST(:artifact_id AS UUID), NULL, 1, "
                    "CAST(:manifest AS JSON), :checksum)"
                ),
                {
                    "id": str(release_only_id),
                    "publication_id": str(release_publication_id),
                    "artifact_id": str(release_artifact_id),
                    "manifest": json.dumps(release_manifest),
                    "checksum": release_checksum,
                },
            )
            await connection.execute(
                text(
                    "UPDATE publications SET active_release_id=CAST(:release AS UUID) "
                    "WHERE id=CAST(:publication AS UUID)"
                ),
                {
                    "release": str(release_only_id),
                    "publication": str(release_publication_id),
                },
            )

        await target_engine.dispose()
        target_engine = None
        await asyncio.to_thread(command.upgrade, config, "head")
        target_engine = create_async_engine(rendered)

        assert await _revision(target_engine) == "0016_project_versions"
        async with target_engine.connect() as connection:
            versions = (
                (
                    await connection.execute(
                        text(
                            "SELECT id, project_id, ordinal, run_id, artifact_id, "
                            "parent_version_id, kind, change_request, idempotency_key "
                            "FROM project_versions"
                        )
                    )
                )
                .mappings()
                .all()
            )
            project_rows = (
                (
                    await connection.execute(
                        text("SELECT id, active_version_id FROM projects")
                    )
                )
                .mappings()
                .all()
            )
            releases = (
                (
                    await connection.execute(
                        text(
                            "SELECT id, artifact_id, previous_release_id, revision, "
                            "asset_manifest, checksum, project_version_id "
                            "FROM publication_releases ORDER BY revision"
                        )
                    )
                )
                .mappings()
                .all()
            )

        assert len(versions) == 3
        versions_by_project = {item["project_id"]: item for item in versions}
        project_versions = {row["id"]: row["active_version_id"] for row in project_rows}
        version = versions_by_project[project_id]
        assert version["id"] == artifact_ids[1]
        assert version["project_id"] == project_id
        assert version["ordinal"] == 1
        assert version["run_id"] == run_id
        assert version["artifact_id"] == artifact_ids[1]
        assert version["parent_version_id"] is None
        assert version["kind"] == "initial"
        assert version["change_request"] is None
        assert version["idempotency_key"] is None
        assert project_versions[project_id] == version["id"]
        fallback_version = versions_by_project[fallback_project_id]
        assert fallback_version["artifact_id"] == fallback_artifact_ids[1]
        assert fallback_version["run_id"] == fallback_run_id
        assert project_versions[fallback_project_id] == fallback_version["id"]
        release_version = versions_by_project[release_project_id]
        assert release_version["artifact_id"] == release_artifact_id
        assert release_version["run_id"] == release_run_id
        assert project_versions[release_project_id] == release_version["id"]
        assert all(item["ordinal"] == 1 for item in versions)
        assert all(item["kind"] == "initial" for item in versions)
        assert len(releases) == 3
        assert all(release["project_version_id"] is None for release in releases)
        releases_by_id = {release["id"]: release for release in releases}
        assert releases_by_id[historical_release_id]["checksum"] == historical_checksum
        assert releases_by_id[historical_release_id]["asset_manifest"] == (
            historical_manifest
        )
        assert releases_by_id[active_release_id]["checksum"] == active_checksum
        assert releases_by_id[active_release_id]["asset_manifest"] == active_manifest
        assert releases_by_id[active_release_id]["previous_release_id"] == (
            historical_release_id
        )
        assert releases_by_id[release_only_id]["checksum"] == release_checksum
        assert releases_by_id[release_only_id]["asset_manifest"] == release_manifest

        service = PublicationService(
            async_sessionmaker(target_engine, expire_on_commit=False)
        )
        resolved = await service.resolve("legacy-project-version-backfill")
        assert resolved.release_id == active_release_id
        assert resolved.artifact_id == artifact_ids[0]
        release_resolved = await service.resolve("legacy-release-only-backfill")
        assert release_resolved.release_id == release_only_id
        assert release_resolved.artifact_id == release_artifact_id

        foreign_project_id = uuid4()
        foreign_run_id = uuid4()
        foreign_artifact_id = uuid4()
        foreign_version_id = uuid4()
        async with target_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO projects "
                    "(id, tenant_id, owner_user_id, source_url, status) VALUES "
                    "(CAST(:id AS UUID), 1, 1, 'https://foreign.example.com', "
                    "'completed')"
                ),
                {"id": str(foreign_project_id)},
            )
            await connection.execute(
                text(
                    "INSERT INTO generation_runs "
                    "(id, project_id, mode, state, progress, next_event_sequence, "
                    "idempotency_key) VALUES "
                    "(CAST(:id AS UUID), CAST(:project_id AS UUID), 'express', "
                    "'completed', 100, 1, 'foreign-project-version')"
                ),
                {
                    "id": str(foreign_run_id),
                    "project_id": str(foreign_project_id),
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO generation_artifacts "
                    "(id, run_id, revision, stage, html, css, javascript, config, "
                    "quality_status, provenance) VALUES "
                    "(CAST(:id AS UUID), CAST(:run_id AS UUID), 1, 'validation', "
                    "'<p>foreign</p>', '', '', CAST('{}' AS JSON), 'verified', "
                    "CAST('{}' AS JSON))"
                ),
                {"id": str(foreign_artifact_id), "run_id": str(foreign_run_id)},
            )
            await connection.execute(
                text(
                    "INSERT INTO project_versions "
                    "(id, project_id, ordinal, run_id, artifact_id, kind) VALUES "
                    "(CAST(:id AS UUID), CAST(:project_id AS UUID), 1, "
                    "CAST(:run_id AS UUID), CAST(:artifact_id AS UUID), 'initial')"
                ),
                {
                    "id": str(foreign_version_id),
                    "project_id": str(foreign_project_id),
                    "run_id": str(foreign_run_id),
                    "artifact_id": str(foreign_artifact_id),
                },
            )

        invalid_version = (
            "INSERT INTO project_versions "
            "(id, project_id, ordinal, run_id, artifact_id, parent_version_id, "
            "kind, change_request) VALUES "
            "(CAST(:id AS UUID), CAST(:project_id AS UUID), 2, "
            "CAST(:run_id AS UUID), CAST(:artifact_id AS UUID), "
            "CAST(:parent_id AS UUID), :kind, :change_request)"
        )
        await _expect_integrity_error(
            target_engine,
            invalid_version,
            {
                "id": str(uuid4()),
                "project_id": str(project_id),
                "run_id": str(foreign_run_id),
                "artifact_id": str(foreign_artifact_id),
                "parent_id": None,
                "kind": "initial",
                "change_request": None,
            },
        )
        await _expect_integrity_error(
            target_engine,
            invalid_version,
            {
                "id": str(uuid4()),
                "project_id": str(project_id),
                "run_id": str(run_id),
                "artifact_id": str(foreign_artifact_id),
                "parent_id": None,
                "kind": "initial",
                "change_request": None,
            },
        )
        await _expect_integrity_error(
            target_engine,
            invalid_version,
            {
                "id": str(uuid4()),
                "project_id": str(project_id),
                "run_id": str(run_id),
                "artifact_id": str(artifact_ids[1]),
                "parent_id": str(foreign_version_id),
                "kind": "restore",
                "change_request": None,
            },
        )
        await _expect_integrity_error(
            target_engine,
            invalid_version,
            {
                "id": str(uuid4()),
                "project_id": str(project_id),
                "run_id": str(run_id),
                "artifact_id": str(artifact_ids[1]),
                "parent_id": str(version["id"]),
                "kind": "refinement",
                "change_request": "   ",
            },
        )
        await _expect_integrity_error(
            target_engine,
            "UPDATE projects SET active_version_id=CAST(:version_id AS UUID) "
            "WHERE id=CAST(:project_id AS UUID)",
            {
                "version_id": str(foreign_version_id),
                "project_id": str(project_id),
            },
        )
        await _expect_integrity_error(
            target_engine,
            "UPDATE generation_runs "
            "SET source_version_id=CAST(:version_id AS UUID) "
            "WHERE id=CAST(:run_id AS UUID)",
            {"version_id": str(foreign_version_id), "run_id": str(run_id)},
        )

        await target_engine.dispose()
        target_engine = None
        await asyncio.to_thread(
            command.downgrade, config, "0015_yookassa_recurring_foundation"
        )
        target_engine = create_async_engine(rendered)
        assert await _revision(target_engine) == "0015_yookassa_recurring_foundation"
        async with target_engine.connect() as connection:
            tables = set(
                await connection.run_sync(lambda sync: inspect(sync).get_table_names())
            )
            release_columns = {
                column["name"]
                for column in await connection.run_sync(
                    lambda sync: inspect(sync).get_columns("publication_releases")
                )
            }
            release_count = await connection.scalar(
                text("SELECT count(*) FROM publication_releases")
            )
            artifact_count = await connection.scalar(
                text("SELECT count(*) FROM generation_artifacts")
            )
        assert "project_versions" not in tables
        assert "project_version_id" not in release_columns
        assert release_count == 3
        assert artifact_count == 7
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
