import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Tenant, User
from app.saas.models import Project
from builder_lab.models import BuilderRequest, EngineName, RunStatus, Stage, TokenUsage
from builder_lab.postgres_store import PostgresRunStore
from tests.builder_lab_cases.test_validation import artifact


async def _database(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'builder.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as database, database.begin():
        database.add(Tenant(id=1, name="Alpha", slug="alpha"))
        database.add(User(id=10, tenant_id=1, email="owner@example.com"))
        project = Project(
            tenant_id=1,
            owner_user_id=10,
            source_url="https://example.com/",
            brief="Premium widget",
        )
        database.add(project)
        await database.flush()
        project_id = project.id
    return engine, factory, project_id


@pytest.mark.asyncio
async def test_concurrent_appends_reserve_monotonic_sequences_and_restore(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    request = BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
    first = PostgresRunStore(factory, project_id=project_id)
    second = PostgresRunStore(factory, project_id=project_id)

    try:
        created = await first.create(request)
        await first.set_running(created.run_id)

        events = await asyncio.gather(
            *(
                (first if index % 2 == 0 else second).append_event(
                    created.run_id,
                    event_type="stage.progress",
                    stage=Stage.FOUNDATION,
                    status="running",
                    message=f"step {index}",
                    usage=TokenUsage(prompt_tokens=1),
                )
                for index in range(12)
            )
        )
        assert sorted(event.sequence for event in events) == list(range(2, 14))

        committed = artifact(revision=2, stage=Stage.FOUNDATION)
        await first.commit_artifact(created.run_id, committed)

        restored_store = PostgresRunStore(factory, project_id=project_id)
        restored = await restored_store.snapshot(created.run_id)
        restored_events = await restored_store.events_after(created.run_id, 0)

        assert restored.request == request
        assert restored.status is RunStatus.RUNNING
        assert restored.latest_sequence == 13
        assert restored.usage.prompt_tokens == 12
        assert [event.sequence for event in restored_events] == list(range(1, 14))
        assert await restored_store.artifact(created.run_id) == committed
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_artifact_json_is_immutable_and_revisioned(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    store = PostgresRunStore(factory, project_id=project_id)

    try:
        run = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
        )
        first = artifact(revision=1, stage=Stage.ART_DIRECTION)
        original_accent = first.theme_tokens["accent"]
        await store.commit_artifact(run.run_id, first)
        first.theme_tokens["accent"] = "mutated after commit"

        with pytest.raises(ValueError, match="monotonically"):
            await store.commit_artifact(run.run_id, artifact(revision=1))

        second = artifact(revision=2, stage=Stage.FOUNDATION)
        await store.commit_artifact(run.run_id, second)
        restored_store = PostgresRunStore(factory, project_id=project_id)

        assert (await restored_store.artifact(run.run_id, 1)).theme_tokens[
            "accent"
        ] == original_accent
        assert await restored_store.artifact(run.run_id) == second
    finally:
        await engine.dispose()
