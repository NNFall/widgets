from __future__ import annotations

import secrets
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Tenant, User
from app.patterns.repository import PatternRepository
from app.saas.models import (
    GenerationArtifact,
    GenerationEvent,
    GenerationRun,
    Project,
    ProjectVersion,
)
from builder_lab.models import BuilderRequest, EngineName, Stage
from builder_lab.patterns.models import (
    CompositionPlan,
    PatternCategory,
    PatternSelection,
)
from builder_lab.patterns.registry import load_builtin_registry
from builder_lab.worker import PostgresWorkerQueue
from tests.builder_lab_cases.test_validation import artifact


POSTGRES_URL = os.getenv("KAIGO_TEST_POSTGRES_URL")


@dataclass(frozen=True, slots=True)
class SeededSource:
    project_id: UUID
    run_id: UUID
    artifact_id: UUID
    version_id: UUID
    request: BuilderRequest
    candidate: object
    plan: CompositionPlan


def _plan() -> CompositionPlan:
    pattern_ids = {
        PatternCategory.LAUNCHER: "orb-pulse",
        PatternCategory.SHELL: "compact-chat",
        PatternCategory.MESSAGES: "paired-bubbles",
        PatternCategory.COMPOSER: "single-line-pill",
        PatternCategory.MOTION: "spring-reveal",
    }
    return CompositionPlan(
        schema_version=1,
        direction_id="candidate-2",
        selections=tuple(
            PatternSelection(
                slot=slot,
                pattern_id=pattern_id,
                version=1,
                parameters={"variant": slot.value},
                reason=f"Persisted {slot.value}",
            )
            for slot, pattern_id in pattern_ids.items()
        ),
        summary="Durable composition",
    )


async def _database(path: Path):
    url = f"sqlite+aiosqlite:///{path}"
    engine = create_async_engine(url)
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
            brief="Mutable project brief",
        )
        database.add(project)
        await database.flush()
        project_id = project.id
    return url, engine, factory, project_id


async def _seed_source(factory, project_id: UUID) -> SeededSource:
    request = BuilderRequest(
        engine=EngineName.DIRECT,
        source_url="https://example.com/source",
        brief="Durable source request",
        reference_context="Captured source context",
    )
    exact = artifact(
        revision=5,
        stage=Stage.MOTION_POLISH,
        art_direction="Exact accepted art direction",
    )
    newer = artifact(
        revision=6,
        stage=Stage.MOTION_POLISH,
        art_direction="A later artifact that must not be used",
    )
    plan = _plan()
    async with factory() as database, database.begin():
        run = GenerationRun(
            project_id=project_id,
            mode="express",
            state="completed",
            progress=100,
            last_completed_stage="motion_polish",
            next_event_sequence=2,
            idempotency_key="source-run",
            finished_at=datetime.now(UTC),
        )
        database.add(run)
        await database.flush()
        database.add(
            GenerationEvent(
                id=secrets.randbits(62),
                run_id=run.id,
                sequence=1,
                event_type="run.created",
                public_message="Source created",
                payload={"request": request.to_dict()},
            )
        )
        exact_row = GenerationArtifact(
            run_id=run.id,
            revision=exact.revision,
            stage=exact.stage.value,
            html=exact.body_html,
            css=exact.css,
            javascript=exact.javascript,
            config={"artifact": exact.to_dict()},
            quality_status="verified",
        )
        newer_row = GenerationArtifact(
            run_id=run.id,
            revision=newer.revision,
            stage=newer.stage.value,
            html=newer.body_html,
            css=newer.css,
            javascript=newer.javascript,
            config={"artifact": newer.to_dict()},
            quality_status="verified",
        )
        database.add_all([exact_row, newer_row])
        await database.flush()
        version = ProjectVersion(
            project_id=project_id,
            ordinal=1,
            run_id=run.id,
            artifact_id=exact_row.id,
            kind="initial",
        )
        database.add(version)
        await database.flush()
        project = await database.get(Project, project_id)
        assert project is not None
        project.active_version_id = version.id
        project.active_run_id = run.id
        project.active_revision = exact.revision
        project.status = "free_result_ready"
        await PatternRepository(database).create_plan(
            run_id=run.id,
            plan=plan,
            registry=load_builtin_registry(),
        )
        return SeededSource(
            project_id=project_id,
            run_id=run.id,
            artifact_id=exact_row.id,
            version_id=version.id,
            request=request,
            candidate=exact,
            plan=plan,
        )


async def _seed_refinement(factory, source: SeededSource) -> UUID:
    async with factory() as database, database.begin():
        run = GenerationRun(
            project_id=source.project_id,
            mode="express",
            state="queued",
            progress=85,
            last_completed_stage="conversation",
            next_event_sequence=2,
            idempotency_key=f"refinement-{uuid4()}",
            source_version_id=source.version_id,
            change_request="Make the greeting shorter",
        )
        database.add(run)
        await database.flush()
        database.add(
            GenerationEvent(
                id=secrets.randbits(62),
                run_id=run.id,
                sequence=1,
                event_type="run.created",
                public_message="Refinement created",
                payload={"request": source.request.to_dict()},
            )
        )
        await PatternRepository(database).create_plan(
            run_id=run.id,
            plan=source.plan,
            registry=load_builtin_registry(),
        )
        project = await database.get(Project, source.project_id)
        assert project is not None
        project.active_run_id = run.id
        project.status = "queued"
        return run.id


async def _add_final_artifact(factory, run_id: UUID, *, revision: int = 6) -> UUID:
    final = artifact(
        revision=revision,
        stage=Stage.MOTION_POLISH,
        art_direction="Refined accepted art direction",
    )
    async with factory() as database, database.begin():
        row = GenerationArtifact(
            run_id=run_id,
            revision=final.revision,
            stage=final.stage.value,
            html=final.body_html,
            css=final.css,
            javascript=final.javascript,
            config={"artifact": final.to_dict()},
            quality_status="verified",
        )
        database.add(row)
        await database.flush()
        return row.id


@pytest.mark.asyncio
async def test_refinement_stage_input_survives_restart_and_uses_exact_version(
    tmp_path,
) -> None:
    url, engine, factory, project_id = await _database(tmp_path / "restart.db")
    source = await _seed_source(factory, project_id)
    refinement_id = await _seed_refinement(factory, source)
    await engine.dispose()

    restarted_engine = create_async_engine(url)
    restarted_factory = async_sessionmaker(restarted_engine, expire_on_commit=False)
    queue = PostgresWorkerQueue(restarted_factory, lease_seconds=30)
    try:
        claim = await queue.claim("restart-worker")
        assert claim is not None
        assert claim.run_id == refinement_id
        assert claim.next_stage == "motion_polish"

        stage_input = await queue.stage_input(claim)

        assert stage_input.previous_artifact is not None
        assert stage_input.previous_artifact.to_dict() == source.candidate.to_dict()
        assert stage_input.context["composition_plan"] == source.plan.to_dict()
        assert (
            stage_input.context["selected_direction"]["art_direction"]
            == source.candidate.art_direction
        )
    finally:
        await restarted_engine.dispose()


@pytest.mark.asyncio
async def test_initial_completion_creates_and_activates_one_version_event(tmp_path) -> None:
    _, engine, factory, project_id = await _database(tmp_path / "initial.db")
    final = artifact(revision=1, stage=Stage.MOTION_POLISH)
    async with factory() as database, database.begin():
        run = GenerationRun(
            project_id=project_id,
            mode="express",
            state="queued",
            progress=85,
            last_completed_stage="conversation",
            next_event_sequence=1,
            idempotency_key="initial-terminal",
        )
        database.add(run)
        await database.flush()
        row = GenerationArtifact(
            run_id=run.id,
            revision=final.revision,
            stage=final.stage.value,
            html=final.body_html,
            css=final.css,
            javascript=final.javascript,
            config={"artifact": final.to_dict()},
            quality_status="verified",
        )
        database.add(row)
        await database.flush()
        project = await database.get(Project, project_id)
        assert project is not None
        project.active_run_id = run.id
        run_id = run.id
        artifact_id = row.id

    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    try:
        claim = await queue.claim("initial-worker")
        assert claim is not None
        assert await queue.complete_stage(
            run_id, "motion_polish", worker_id="initial-worker"
        ) is None
        async with factory() as database:
            versions = list(
                (
                    await database.execute(
                        select(ProjectVersion).where(
                            ProjectVersion.project_id == project_id
                        )
                    )
                ).scalars()
            )
            project = await database.get(Project, project_id)
            created_events = list(
                (
                    await database.execute(
                        select(GenerationEvent).where(
                            GenerationEvent.run_id == run_id,
                            GenerationEvent.event_type == "project.version_created",
                        )
                    )
                ).scalars()
            )
        assert len(versions) == 1
        assert versions[0].kind == "initial"
        assert versions[0].artifact_id == artifact_id
        assert project is not None
        assert project.active_version_id == versions[0].id
        assert project.active_run_id == run_id
        assert project.active_revision == 1
        assert project.status == "free_result_ready"
        assert len(created_events) == 1
        assert created_events[0].public_payload == {
            "version_id": str(versions[0].id),
            "ordinal": 1,
            "kind": "initial",
            "activated": True,
        }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_refinement_completion_activates_only_while_source_and_run_match(
    tmp_path,
) -> None:
    _, engine, factory, project_id = await _database(tmp_path / "refinement.db")
    source = await _seed_source(factory, project_id)
    refinement_id = await _seed_refinement(factory, source)
    final_id = await _add_final_artifact(factory, refinement_id)
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    try:
        claim = await queue.claim("refinement-worker")
        assert claim is not None
        await queue.complete_stage(
            refinement_id, "motion_polish", worker_id="refinement-worker"
        )
        async with factory() as database:
            version = await database.scalar(
                select(ProjectVersion).where(ProjectVersion.run_id == refinement_id)
            )
            project = await database.get(Project, project_id)
        assert version is not None
        assert version.kind == "refinement"
        assert version.parent_version_id == source.version_id
        assert version.change_request == "Make the greeting shorter"
        assert version.artifact_id == final_id
        assert project is not None
        assert project.active_version_id == version.id
        assert project.active_run_id == refinement_id
        assert project.active_revision == 6
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_refinement_cas_loss_keeps_restored_pointers_and_emits_conflict(
    tmp_path,
) -> None:
    _, engine, factory, project_id = await _database(tmp_path / "cas-loss.db")
    source = await _seed_source(factory, project_id)
    refinement_id = await _seed_refinement(factory, source)
    await _add_final_artifact(factory, refinement_id)
    async with factory() as database, database.begin():
        restored = ProjectVersion(
            project_id=project_id,
            ordinal=2,
            run_id=source.run_id,
            artifact_id=source.artifact_id,
            parent_version_id=source.version_id,
            kind="restore",
            idempotency_key="simulated-restore",
        )
        database.add(restored)
        await database.flush()
        project = await database.get(Project, project_id)
        assert project is not None
        project.active_version_id = restored.id
        project.active_run_id = source.run_id
        project.active_revision = 5
        restored_id = restored.id

    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    try:
        claim = await queue.claim("late-refinement-worker")
        assert claim is not None
        await queue.complete_stage(
            refinement_id,
            "motion_polish",
            worker_id="late-refinement-worker",
        )
        async with factory() as database:
            project = await database.get(Project, project_id)
            refinement = await database.scalar(
                select(ProjectVersion).where(ProjectVersion.run_id == refinement_id)
            )
            conflicts = list(
                (
                    await database.execute(
                        select(GenerationEvent).where(
                            GenerationEvent.run_id == refinement_id,
                            GenerationEvent.event_type
                            == "project.version_activation_conflict",
                        )
                    )
                ).scalars()
            )
        assert refinement is not None
        assert project is not None
        assert project.active_version_id == restored_id
        assert project.active_run_id == source.run_id
        assert project.active_revision == 5
        assert len(conflicts) == 1
        assert conflicts[0].public_payload == {
            "version_id": str(refinement.id),
            "ordinal": refinement.ordinal,
            "kind": "refinement",
            "activated": False,
        }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_terminal_replay_does_not_duplicate_version_or_created_event(tmp_path) -> None:
    _, engine, factory, project_id = await _database(tmp_path / "replay.db")
    source = await _seed_source(factory, project_id)
    refinement_id = await _seed_refinement(factory, source)
    final_id = await _add_final_artifact(factory, refinement_id)
    try:
        from app.projects import versions as version_module

        async with factory() as database, database.begin():
            run = await database.get(GenerationRun, refinement_id)
            final = await database.get(GenerationArtifact, final_id)
            repository = version_module.ProjectVersionRepository(database)
            first = await repository.materialize_completed_run(
                run=run,
                artifact=final,
                now=datetime.now(UTC),
            )
            second = await repository.materialize_completed_run(
                run=run,
                artifact=final,
                now=datetime.now(UTC),
            )
            count = await database.scalar(
                select(func.count())
                .select_from(ProjectVersion)
                .where(ProjectVersion.run_id == refinement_id)
            )
        assert first.created is True
        assert second.created is False
        assert first.version.id == second.version.id
        assert count == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.skipif(
    not POSTGRES_URL,
    reason="KAIGO_TEST_POSTGRES_URL is not configured",
)
async def test_postgres_refinement_materialization_uses_guarded_activation() -> None:
    engine = create_async_engine(POSTGRES_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as database, database.begin():
            database.add(Tenant(id=1, name="Alpha", slug="alpha"))
            await database.flush()
            database.add(User(id=10, tenant_id=1, email="owner@example.com"))
            project = Project(
                tenant_id=1,
                owner_user_id=10,
                source_url="https://example.com/",
                brief="Postgres activation fence",
            )
            database.add(project)
            await database.flush()
            project_id = project.id
        source = await _seed_source(factory, project_id)
        refinement_id = await _seed_refinement(factory, source)
        final_id = await _add_final_artifact(factory, refinement_id)
        async with factory() as database, database.begin():
            restored = ProjectVersion(
                project_id=project_id,
                ordinal=2,
                run_id=source.run_id,
                artifact_id=source.artifact_id,
                parent_version_id=source.version_id,
                kind="restore",
                idempotency_key="postgres-restore-winner",
            )
            database.add(restored)
            await database.flush()
            project = await database.get(Project, project_id)
            assert project is not None
            project.active_version_id = restored.id
            project.active_run_id = source.run_id
            restored_id = restored.id

        from app.projects import versions as version_module

        async with factory() as database, database.begin():
            run = await database.get(GenerationRun, refinement_id)
            final = await database.get(GenerationArtifact, final_id)
            result = await version_module.ProjectVersionRepository(
                database
            ).materialize_completed_run(
                run=run,
                artifact=final,
                now=datetime.now(UTC),
            )
            assert result.created is True
            assert result.activated is False

        async with factory() as database:
            project = await database.get(Project, project_id)
            detached = await database.scalar(
                select(ProjectVersion).where(ProjectVersion.run_id == refinement_id)
            )
        assert detached is not None
        assert project is not None
        assert project.active_version_id == restored_id
        assert project.active_run_id == source.run_id
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()
