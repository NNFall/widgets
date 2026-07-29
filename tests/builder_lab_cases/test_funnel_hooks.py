from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.saas.models import FunnelEvent, GenerationArtifact, GenerationRun
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
            run = GenerationRun(
                project_id=project_id,
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

        assert count == 2
        assert [event.event_type for event in events] == [
            "first_artifact",
            "free_result",
        ]
        assert {event.run_id for event in events} == {run_id}
        assert {event.project_id for event in events} == {project_id}
        assert {event.artifact_id for event in events} == {stored_artifact.id}
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
            run = GenerationRun(
                project_id=project_id,
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
    finally:
        await engine.dispose()
