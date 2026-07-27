import asyncio
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Tenant, User
from app.saas.models import GenerationArtifact, Project
from builder_lab.models import BuilderRequest, EngineName, RunStatus, Stage, TokenUsage
from builder_lab.postgres_store import PostgresRunStore
from builder_lab.store_protocol import RunStoreProtocol
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


@pytest.mark.asyncio
async def test_committing_staged_draft_promotes_identical_revision(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    store = PostgresRunStore(factory, project_id=project_id)

    try:
        run = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
        )
        await store.commit_artifact(
            run.run_id, artifact(revision=1, stage=Stage.ART_DIRECTION)
        )
        candidate = artifact(revision=2, stage=Stage.FOUNDATION)
        await store.stage_visual_candidate(run.run_id, candidate)
        await store.stage_visual_draft(run.run_id, candidate)

        committed = await store.commit_visual_candidate(run.run_id)

        restored = await PostgresRunStore(
            factory, project_id=project_id
        ).snapshot(run.run_id)
        assert committed == candidate
        assert restored.artifact == candidate
        assert restored.draft_artifact is None
        assert restored.quality_status == "verified"
        async with factory() as database:
            rows = (
                await database.execute(
                    select(GenerationArtifact).where(
                        GenerationArtifact.run_id == UUID(run.run_id),
                        GenerationArtifact.revision == candidate.revision,
                    )
                )
            ).scalars().all()
        assert len(rows) == 1
        assert rows[0].quality_status == "verified"
        assert rows[0].config["artifact"] == candidate.to_dict()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_draft_promotion_rejects_changed_candidate_at_same_revision(
    tmp_path,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    store = PostgresRunStore(factory, project_id=project_id)

    try:
        run = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
        )
        candidate = artifact(revision=1, stage=Stage.ART_DIRECTION)
        changed_draft = artifact(
            revision=1,
            stage=Stage.ART_DIRECTION,
            css=candidate.css + "\n.changed {}",
        )
        await store.stage_visual_candidate(run.run_id, candidate)
        await store.stage_visual_draft(run.run_id, changed_draft)

        with pytest.raises(ValueError, match="differs from persisted draft"):
            await store.commit_visual_candidate(run.run_id)

        restored = await store.snapshot(run.run_id)
        assert restored.artifact is None
        assert restored.draft_artifact == changed_draft
        assert restored.quality_status == "needs_repair"
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "live_read_method",
    ("artifact", "preview_artifact", "events_after", "wait_for_events"),
)
def test_orchestrator_store_protocol_excludes_live_read_methods(
    live_read_method: str,
) -> None:
    assert live_read_method not in RunStoreProtocol.__dict__
