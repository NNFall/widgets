import asyncio
import gc
from time import monotonic
from uuid import UUID

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Tenant, User
from app.saas.models import GenerationArtifact, GenerationEvent, Project
from builder_lab.models import BuilderRequest, EngineName, RunStatus, Stage, TokenUsage
from builder_lab import postgres_store as postgres_store_module
from builder_lab.postgres_store import PostgresRunStore
from builder_lab.store import ArtifactNotFound, VisualCandidateCapacityExceeded
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
        with pytest.raises(ArtifactNotFound):
            await store.visual_candidate(run.run_id)

        restored = await store.snapshot(run.run_id)
        assert restored.artifact is None
        assert restored.draft_artifact == changed_draft
        assert restored.quality_status == "needs_repair"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_repeated_visual_repair_drafts_keep_history_and_commit_latest(
    tmp_path,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    store = PostgresRunStore(factory, project_id=project_id)

    try:
        run = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
        )
        await store.commit_artifact(
            run.run_id, artifact(revision=1, stage=Stage.ART_DIRECTION)
        )
        first_draft = artifact(revision=2, stage=Stage.FOUNDATION)
        repaired_draft = artifact(
            revision=2,
            stage=Stage.FOUNDATION,
            css=first_draft.css + "\n.repaired {}",
        )

        await store.stage_visual_candidate(run.run_id, first_draft)
        await store.stage_visual_draft(run.run_id, first_draft)
        assert (await store.snapshot(run.run_id)).draft_artifact == first_draft

        await store.stage_visual_candidate(run.run_id, repaired_draft)
        await store.stage_visual_draft(run.run_id, repaired_draft)
        before_commit = await store.snapshot(run.run_id)
        assert before_commit.artifact.revision == 1
        assert before_commit.draft_artifact == repaired_draft
        assert before_commit.quality_status == "needs_repair"

        committed = await store.commit_visual_candidate(run.run_id)
        restored = await PostgresRunStore(
            factory, project_id=project_id
        ).snapshot(run.run_id)

        assert committed == repaired_draft
        assert restored.artifact == repaired_draft
        assert restored.draft_artifact is None
        assert restored.quality_status == "verified"
        async with factory() as database:
            artifact_rows = (
                await database.execute(
                    select(GenerationArtifact).where(
                        GenerationArtifact.run_id == UUID(run.run_id),
                        GenerationArtifact.revision == repaired_draft.revision,
                    )
                )
            ).scalars().all()
            draft_events = (
                await database.execute(
                    select(GenerationEvent)
                    .where(
                        GenerationEvent.run_id == UUID(run.run_id),
                        GenerationEvent.event_type == "artifact.draft_staged",
                    )
                    .order_by(GenerationEvent.sequence)
                )
            ).scalars().all()
        assert len(artifact_rows) == 1
        assert artifact_rows[0].config["artifact"] == repaired_draft.to_dict()
        assert [event.payload["artifact"] for event in draft_events] == [
            first_draft.to_dict(),
            repaired_draft.to_dict(),
        ]
        assert draft_events[0].payload["supersedes_sequence"] is None
        assert (
            draft_events[1].payload["supersedes_sequence"]
            == draft_events[0].sequence
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_verified_artifact_cannot_be_restaged_at_same_revision(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    store = PostgresRunStore(factory, project_id=project_id)

    try:
        run = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
        )
        verified = artifact(revision=1, stage=Stage.ART_DIRECTION)
        await store.commit_artifact(run.run_id, verified)

        with pytest.raises(
            ValueError, match="visual draft revision must exceed public revision"
        ):
            await store.stage_visual_draft(
                run.run_id,
                artifact(
                    revision=1,
                    stage=Stage.ART_DIRECTION,
                    css=verified.css + "\n.changed {}",
                ),
            )

        assert await store.artifact(run.run_id, 1) == verified
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stale_run_cannot_overwrite_active_project_summary(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    store = PostgresRunStore(factory, project_id=project_id)

    try:
        stale = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="First run")
        )
        active = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Second run")
        )

        await store.set_running(stale.run_id)
        await store.commit_artifact(
            stale.run_id, artifact(revision=1, stage=Stage.ART_DIRECTION)
        )
        await store.finish(
            stale.run_id,
            RunStatus.COMPLETED,
            event_type="run.completed",
            stage=Stage.ART_DIRECTION,
            message="stale done",
            revision=1,
        )

        async with factory() as database:
            project = await database.get(Project, project_id)
            assert project.active_run_id == UUID(active.run_id)
            assert project.active_revision is None
            assert project.status == "generating"

        await store.commit_artifact(
            active.run_id, artifact(revision=1, stage=Stage.ART_DIRECTION)
        )
        await store.finish(
            active.run_id,
            RunStatus.COMPLETED,
            event_type="run.completed",
            stage=Stage.ART_DIRECTION,
            message="active done",
            revision=1,
        )
        async with factory() as database:
            project = await database.get(Project, project_id)
            assert project.active_run_id == UUID(active.run_id)
            assert project.active_revision == 1
            assert project.status == "completed"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_mark_terminal_restores_elapsed_seconds(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    store = PostgresRunStore(factory, project_id=project_id)

    try:
        run = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
        )
        await store.mark_terminal(
            run.run_id,
            RunStatus.FAILED,
            error_code="provider_unavailable",
            elapsed_seconds=4.25,
        )

        restored = await PostgresRunStore(
            factory, project_id=project_id
        ).snapshot(run.run_id)
        assert restored.status is RunStatus.FAILED
        assert restored.error_code == "provider_unavailable"
        assert restored.elapsed_seconds == 4.25
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_snapshot_uses_only_bounded_event_queries(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    store = PostgresRunStore(factory, project_id=project_id)

    try:
        run = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
        )
        for index in range(12):
            await store.append_event(
                run.run_id,
                event_type="stage.progress",
                stage=Stage.FOUNDATION,
                status="running",
                message=f"step {index}",
                usage=TokenUsage(prompt_tokens=1),
            )

        statements: list[str] = []

        def record_statement(_conn, _cursor, statement, _params, _context, _many):
            statements.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", record_statement)
        try:
            restored = await store.snapshot(run.run_id)
        finally:
            event.remove(
                engine.sync_engine, "before_cursor_execute", record_statement
            )

        assert restored.usage.prompt_tokens == 12
        event_selects = [
            statement.upper()
            for statement in statements
            if "FROM GENERATION_EVENTS" in statement.upper()
        ]
        assert event_selects
        assert all(
            " LIMIT " in statement
            or "SUM(" in statement
            or "EXISTS" in statement
            for statement in event_selects
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_events_after_returns_bounded_pages(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    store = PostgresRunStore(factory, project_id=project_id)

    try:
        run = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
        )
        for index in range(105):
            await store.append_event(
                run.run_id,
                event_type="stage.progress",
                stage=Stage.FOUNDATION,
                status="running",
                message=f"step {index}",
            )

        first_page = await store.events_after(run.run_id, 0)
        second_page = await store.events_after(
            run.run_id, first_page[-1].sequence
        )
        assert len(first_page) == 100
        assert len(second_page) == 6
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_wait_for_events_does_not_poll_full_snapshot(
    tmp_path, monkeypatch
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    store = PostgresRunStore(factory, project_id=project_id)

    try:
        run = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
        )

        async def reject_snapshot(_run_id: str):
            raise AssertionError("wait_for_events must not call snapshot")

        monkeypatch.setattr(store, "snapshot", reject_snapshot)
        assert await store.wait_for_events(
            run.run_id, run.latest_sequence, timeout=0.01
        ) == ()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_wait_for_events_uses_bounded_query_load(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    store = PostgresRunStore(factory, project_id=project_id)

    try:
        run = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
        )
        statements: list[str] = []

        def record_statement(_conn, _cursor, statement, _params, _context, _many):
            statements.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", record_statement)
        started = monotonic()
        try:
            assert await store.wait_for_events(
                run.run_id, run.latest_sequence, timeout=0.75
            ) == ()
        finally:
            elapsed = monotonic() - started
            event.remove(
                engine.sync_engine, "before_cursor_execute", record_statement
            )

        assert elapsed < 1.0
        assert len(statements) <= 7
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_wait_for_events_wakes_promptly_for_terminal_event(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    store = PostgresRunStore(factory, project_id=project_id)

    try:
        run = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
        )
        started = monotonic()
        waiter = asyncio.create_task(
            store.wait_for_events(
                run.run_id, run.latest_sequence, timeout=2.0
            )
        )
        await asyncio.sleep(0.06)
        await store.mark_terminal(run.run_id, RunStatus.FAILED)

        events = await waiter
        assert monotonic() - started < 0.4
        assert [item.event_type for item in events] == ["run.terminal_marked"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_run_locks_are_evicted_after_operations(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    store = PostgresRunStore(factory, project_id=project_id)

    try:
        run = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
        )
        await store.append_event(
            run.run_id,
            event_type="stage.progress",
            stage=Stage.FOUNDATION,
            status="running",
            message="step",
        )
        key = (store._namespace, UUID(run.run_id))
        gc.collect()
        assert key not in postgres_store_module._RUN_LOCKS
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_terminal_and_cancel_paths_clear_visual_candidates(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    store = PostgresRunStore(factory, project_id=project_id)

    try:
        terminal = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Terminal")
        )
        cancelled = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Cancelled")
        )
        await store.stage_visual_candidate(
            terminal.run_id, artifact(revision=1, stage=Stage.ART_DIRECTION)
        )
        await store.stage_visual_candidate(
            cancelled.run_id, artifact(revision=1, stage=Stage.ART_DIRECTION)
        )

        await store.mark_terminal(terminal.run_id, RunStatus.FAILED)
        assert await store.request_cancel(cancelled.run_id)

        with pytest.raises(ArtifactNotFound):
            await store.visual_candidate(terminal.run_id)
        with pytest.raises(ArtifactNotFound):
            await store.visual_candidate(cancelled.run_id)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_visual_candidate_capacity_rejects_without_evicting_staged_work(
    tmp_path, monkeypatch
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    store = PostgresRunStore(factory, project_id=project_id)
    monkeypatch.setattr(postgres_store_module, "_MAX_VISUAL_CANDIDATES", 1)

    try:
        runs = [
            await store.create(
                BuilderRequest(engine=EngineName.DIRECT, brief=f"Run {index}")
            )
            for index in range(2)
        ]
        candidate = artifact(revision=1, stage=Stage.ART_DIRECTION)
        await store.stage_visual_candidate(runs[0].run_id, candidate)
        restaged = artifact(
            revision=1,
            stage=Stage.ART_DIRECTION,
            css=candidate.css + "\n.restaged {}",
        )
        await store.stage_visual_candidate(runs[0].run_id, restaged)

        with pytest.raises(
            VisualCandidateCapacityExceeded,
            match="visual candidate capacity",
        ):
            await store.stage_visual_candidate(runs[1].run_id, candidate)

        assert await store.commit_visual_candidate(runs[0].run_id) == restaged
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
