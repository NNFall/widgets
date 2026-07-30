from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.saas.models import (
    FunnelEvent,
    FunnelJourney,
    GenerationArtifact,
    GenerationRun,
    Project,
    ProjectVersion,
)
from builder_lab.models import Stage
from builder_lab.engines.base import BuilderEngineError
from builder_lab.worker import PostgresWorkerQueue, StageResult
from tests.builder_lab_cases.test_validation import artifact
from tests.builder_lab_cases.test_worker import _database


@pytest.mark.asyncio
async def test_final_artifact_boundary_emits_first_artifact_and_free_result_once(
    tmp_path,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    try:
        async with factory() as database, database.begin():
            journey = FunnelJourney(campaign_source="telegram")
            database.add(journey)
            await database.flush()
            project = await database.get(Project, project_id)
            project.journey_id = journey.id
            run = GenerationRun(
                project_id=project_id,
                journey_id=journey.id,
                mode="express",
                state="queued",
                progress=80,
                last_completed_stage="conversation",
                next_event_sequence=1,
                idempotency_key=(
                    f"funnel-worker-{datetime.now(timezone.utc).timestamp()}"
                ),
            )
            database.add(run)
            await database.flush()
            run_id = run.id

        claim = await queue.claim("funnel-worker")
        assert claim is not None
        candidate = artifact(revision=1, stage=Stage.MOTION_POLISH)
        await queue.stage_result(
            claim,
            StageResult(
                public_message="Free result ready",
                artifact=candidate,
            ),
        )
        assert await queue.finalize_stage(claim) is None

        async with factory() as database:
            stored_artifact = await database.scalar(
                select(GenerationArtifact).where(GenerationArtifact.run_id == run_id)
            )
            events = list(
                (
                    await database.execute(
                        select(FunnelEvent).order_by(FunnelEvent.event_type)
                    )
                ).scalars()
            )
            count = await database.scalar(select(func.count()).select_from(FunnelEvent))
            version = await database.scalar(
                select(ProjectVersion).where(ProjectVersion.project_id == project_id)
            )
            stored_project = await database.get(Project, project_id)

        assert count == 2
        assert [event.event_type for event in events] == [
            "first_artifact",
            "free_result",
        ]
        assert {event.run_id for event in events} == {run_id}
        assert {event.project_id for event in events} == {project_id}
        assert {event.artifact_id for event in events} == {stored_artifact.id}
        assert {event.journey_id for event in events} == {journey.id}
        assert version.kind == "initial"
        assert version.run_id == run_id
        assert version.artifact_id == stored_artifact.id
        assert stored_project.active_version_id == version.id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_completed_refinement_creates_child_version_and_makes_it_active(
    tmp_path,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    try:
        async with factory() as database, database.begin():
            project = await database.get(Project, project_id)
            initial_run = GenerationRun(
                project_id=project_id,
                mode="express",
                state="completed",
                progress=100,
                idempotency_key="initial-version-source",
            )
            database.add(initial_run)
            await database.flush()
            initial_candidate = artifact(revision=1, stage=Stage.MOTION_POLISH)
            initial_artifact = GenerationArtifact(
                run_id=initial_run.id,
                revision=1,
                stage=initial_candidate.stage.value,
                html=initial_candidate.body_html,
                css=initial_candidate.css,
                javascript=initial_candidate.javascript,
                config={"artifact": initial_candidate.to_dict()},
                quality_status="verified",
            )
            database.add(initial_artifact)
            await database.flush()
            initial_version = ProjectVersion(
                project_id=project_id,
                ordinal=1,
                run_id=initial_run.id,
                artifact_id=initial_artifact.id,
                kind="initial",
            )
            database.add(initial_version)
            await database.flush()
            project.active_version_id = initial_version.id

            refinement = GenerationRun(
                project_id=project_id,
                source_version_id=initial_version.id,
                change_request="Сделай приветствие короче",
                mode="express",
                state="queued",
                progress=80,
                last_completed_stage="conversation",
                next_event_sequence=1,
                idempotency_key="refinement-version-run",
            )
            database.add(refinement)
            await database.flush()
            project.active_run_id = refinement.id
            refinement_id = refinement.id

        claim = await queue.claim("refinement-worker")
        assert claim is not None
        candidate = artifact(revision=1, stage=Stage.MOTION_POLISH)
        await queue.stage_result(
            claim,
            StageResult(public_message="Refinement ready", artifact=candidate),
        )
        assert await queue.finalize_stage(claim) is None

        async with factory() as database:
            versions = list(
                (
                    await database.execute(
                        select(ProjectVersion)
                        .where(ProjectVersion.project_id == project_id)
                        .order_by(ProjectVersion.ordinal)
                    )
                ).scalars()
            )
            project = await database.get(Project, project_id)

        assert [version.kind for version in versions] == ["initial", "refinement"]
        assert versions[1].parent_version_id == versions[0].id
        assert versions[1].run_id == refinement_id
        assert versions[1].change_request == "Сделай приветствие короче"
        assert project.active_version_id == versions[1].id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_terminal_failure_emits_free_result_when_verified_artifact_survives(
    tmp_path,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    try:
        candidate = artifact(revision=1, stage=Stage.CONVERSATION)
        async with factory() as database, database.begin():
            journey = FunnelJourney(campaign_source="telegram")
            database.add(journey)
            await database.flush()
            run = GenerationRun(
                project_id=project_id,
                journey_id=journey.id,
                mode="express",
                state="queued",
                progress=80,
                last_completed_stage="conversation",
                next_event_sequence=1,
                idempotency_key=(
                    f"funnel-failed-{datetime.now(timezone.utc).timestamp()}"
                ),
            )
            database.add(run)
            await database.flush()
            run_id = run.id
            persisted = GenerationArtifact(
                run_id=run.id,
                revision=1,
                stage=Stage.CONVERSATION.value,
                html=candidate.body_html,
                css=candidate.css,
                javascript=candidate.javascript,
                config={"artifact": candidate.to_dict()},
                quality_status="verified",
            )
            database.add(persisted)
            await database.flush()
            artifact_id = persisted.id

        claim = await queue.claim("failed-funnel-worker")
        assert claim is not None
        await queue.fail_claim(
            claim,
            BuilderEngineError(
                "visual_quality_failed",
                "Visual review exhausted its repair budget",
            ),
        )

        async with factory() as database:
            event = await database.scalar(
                select(FunnelEvent).where(
                    FunnelEvent.event_key == f"free_result:run:{run_id}"
                )
            )
        assert event is not None
        assert event.artifact_id == artifact_id
        assert event.project_id == project_id
        assert event.journey_id == journey.id
    finally:
        await engine.dispose()
