from __future__ import annotations

import asyncio
import importlib
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine


POSTGRES_URL = os.getenv("KAIGO_TEST_POSTGRES_URL")
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_publication_migration_replaces_revision_unique_and_adds_active_fk(monkeypatch) -> None:
    migration = importlib.import_module("migrations.versions.0008_publication_releases")
    calls = []

    class FakeOp:
        def drop_constraint(self, name, table, **kwargs):
            calls.append(("drop_constraint", name, table, kwargs))

        def create_unique_constraint(self, name, table, columns):
            calls.append(("create_unique_constraint", name, table, tuple(columns)))

        def execute(self, statement):
            calls.append(("execute", str(statement)))

        def create_foreign_key(self, name, source, referent, local, remote, **kwargs):
            calls.append((
                "create_foreign_key",
                name,
                source,
                referent,
                tuple(local),
                tuple(remote),
                kwargs,
            ))

    monkeypatch.setattr(migration, "op", FakeOp())
    migration.upgrade()

    assert ("drop_constraint", "uq_publication_release_revision", "publication_releases", {"type_": "unique"}) in calls
    assert (
        "create_unique_constraint",
        "uq_publication_release_artifact",
        "publication_releases",
        ("publication_id", "artifact_id"),
    ) in calls
    foreign_key = next(call for call in calls if call[0] == "create_foreign_key")
    assert foreign_key[1:6] == (
        "fk_publications_active_release_id",
        "publications",
        "publication_releases",
        ("active_release_id",),
        ("id",),
    )
    assert foreign_key[6] == {
        "ondelete": "SET NULL",
        "deferrable": True,
        "initially": "DEFERRED",
    }


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URL, reason="KAIGO_TEST_POSTGRES_URL is not configured")
async def test_postgres_publication_migration_and_concurrent_first_publish(monkeypatch) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.publication.service import PublicationService, PublicationUpgradeRequired
    from app.saas.models import (
        GenerationArtifact,
        GenerationRun,
        Project,
        Subscription,
        UserIdentity,
    )
    from tests.builder_lab_cases.test_validation import artifact

    source_url = make_url(POSTGRES_URL)
    if source_url.drivername == "postgresql":
        source_url = source_url.set(drivername="postgresql+asyncpg")
    database_name = f"kaigo_publication_{uuid4().hex}"
    admin_url = source_url.set(database="postgres")
    target_url = source_url.set(database=database_name)
    rendered = target_url.render_as_string(hide_password=False)
    admin_engine = create_async_engine(
        admin_url.render_as_string(hide_password=False), isolation_level="AUTOCOMMIT"
    )
    target_engine = None
    created = False
    try:
        async with admin_engine.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        created = True
        monkeypatch.setenv("DATABASE_URL", rendered)
        config = Config(str(PROJECT_ROOT / "alembic.ini"))
        await asyncio.to_thread(command.upgrade, config, "head")
        target_engine = create_async_engine(rendered)
        factory = async_sessionmaker(target_engine, expire_on_commit=False)

        async with target_engine.begin() as connection:
            await connection.execute(text(
                "INSERT INTO tenants (id,name,slug) VALUES (1,'T','t')"
            ))
            await connection.execute(text(
                "INSERT INTO users (id,tenant_id,email,role) "
                "VALUES (1,1,'x@example.com','tenant_admin')"
            ))
        async with factory() as database, database.begin():
            project = Project(
                tenant_id=1,
                owner_user_id=1,
                source_url="https://example.com/",
            )
            database.add(project)
            await database.flush()
            run = GenerationRun(
                project_id=project.id,
                mode="express",
                state="completed",
                idempotency_key="publish-concurrent",
            )
            database.add(run)
            await database.flush()
            candidate = artifact(revision=1)
            stored = GenerationArtifact(
                run_id=run.id,
                revision=1,
                stage=candidate.stage.value,
                html=candidate.body_html,
                css=candidate.css,
                javascript=candidate.javascript,
                config={"artifact": candidate.to_dict()},
                quality_status="verified",
            )
            database.add(stored)
            database.add_all([
                UserIdentity(
                    user_id=1,
                    provider="google",
                    provider_subject="publication-migration",
                    email="x@example.com",
                    email_verified=True,
                ),
                Subscription(
                    user_id=1,
                    provider="test",
                    plan_code="pro",
                    status="active",
                ),
            ])
            await database.flush()
            project_id, artifact_id = project.id, stored.id

        service = PublicationService(factory)
        first, second = await asyncio.gather(
            service.publish(
                project_id,
                actor_user_id=1,
                tenant_id=1,
                artifact_id=artifact_id,
            ),
            service.publish(
                project_id,
                actor_user_id=1,
                tenant_id=1,
                artifact_id=artifact_id,
            ),
        )
        assert first.publication_id == second.publication_id
        assert first.release_id == second.release_id

        # A revocation that already owns the subscription row must serialize
        # before publication. Without the entitlement FOR UPDATE this task
        # reads the stale active version and succeeds before revoke commits.
        revoke_session = factory()
        revoke_transaction = await revoke_session.begin()
        subscription = await revoke_session.scalar(
            select(Subscription)
            .where(Subscription.user_id == 1)
            .with_for_update()
        )
        subscription.status = "cancelled"
        await revoke_session.flush()
        publish_during_revoke = asyncio.create_task(
            service.publish(
                project_id,
                actor_user_id=1,
                tenant_id=1,
                artifact_id=artifact_id,
            )
        )
        await asyncio.sleep(0.2)
        assert not publish_during_revoke.done()
        await revoke_transaction.commit()
        await revoke_session.close()
        with pytest.raises(PublicationUpgradeRequired):
            await publish_during_revoke

        async with target_engine.connect() as connection:
            assert await connection.scalar(text("SELECT count(*) FROM publications")) == 1
            assert await connection.scalar(text("SELECT count(*) FROM publication_releases")) == 1
            revision = await connection.run_sync(
                lambda sync: MigrationContext.configure(sync).get_current_revision()
            )
            fk = await connection.scalar(text(
                "SELECT count(*) FROM pg_constraint WHERE conname = 'fk_publications_active_release_id'"
            ))
        assert revision == "0008_publication_releases"
        assert fk == 1
    finally:
        if target_engine is not None:
            await target_engine.dispose()
        if created:
            async with admin_engine.connect() as connection:
                await connection.execute(text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname=:name AND pid<>pg_backend_pid()"
                ), {"name": database_name})
                await connection.execute(text(f'DROP DATABASE "{database_name}"'))
        await admin_engine.dispose()
