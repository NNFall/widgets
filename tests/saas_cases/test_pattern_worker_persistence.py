from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Tenant, User
from app.patterns.repository import PatternRepository
from app.saas.models import (
    CompositionPlanRecord,
    GenerationArtifact,
    GenerationEvent,
    GenerationRun,
    ModelCall,
    PatternOutcome,
    Project,
)
from builder_lab.models import TokenUsage
from builder_lab.models import Stage
from builder_lab.patterns.registry import load_builtin_registry
from builder_lab.worker import PostgresWorkerQueue, RunClaim, StageResult
from tests.builder_lab_cases.test_validation import artifact
from tests.saas_cases.test_pattern_repository import complete_plan


@pytest.mark.asyncio
async def test_composition_finalize_persists_plan_and_provenance(tmp_path) -> None:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'composition-worker.db'}"
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    plan = complete_plan()
    attempt_id = uuid4()
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as database, database.begin():
            database.add(Tenant(id=1, name="Alpha", slug="alpha"))
            await database.flush()
            database.add(User(id=10, tenant_id=1, email="owner@example.com"))
            await database.flush()
            project = Project(
                tenant_id=1,
                owner_user_id=10,
                source_url="https://example.com/",
            )
            database.add(project)
            await database.flush()
            run = GenerationRun(
                project_id=project.id,
                mode="direct",
                state="running",
                progress=28,
                current_stage="composition",
                last_completed_stage="art_direction",
                next_event_sequence=2,
                idempotency_key=str(uuid4()),
                lease_owner="planner-worker",
                lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
            )
            database.add(run)
            await database.flush()
            database.add(
                GenerationEvent(
                    id=1,
                    run_id=run.id,
                    sequence=1,
                    event_type="stage.started",
                    public_message="Начат этап композиции",
                    payload={
                        "stage": "composition",
                        "attempt_id": str(attempt_id),
                        "worker_id": "planner-worker",
                    },
                )
            )
            direction = GenerationArtifact(
                run_id=run.id,
                revision=1,
                stage="art_direction",
                config={"artifact": {"stage": "art_direction"}},
                provenance={},
            )
            database.add(direction)
            planner_call = ModelCall(
                run_id=run.id,
                provider="gemini",
                model="planner-model",
                role="composition_planner",
                mode="direct",
                prompt_version="composition-v1",
                request_id="planner-request-1",
                provider_dispatched=True,
                status="completed",
            )
            database.add(planner_call)
            await database.flush()
            run_id = run.id
            direction_id = direction.id
            model_call_id = planner_call.id

        claim = RunClaim(
            run_id=run_id,
            project_id=project.id,
            worker_id="planner-worker",
            mode="direct",
            next_stage="composition",
            last_completed_stage="art_direction",
            lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
            attempt_id=attempt_id,
        )
        queue = PostgresWorkerQueue(factory, lease_seconds=60)
        result = StageResult(
            public_message="Композиция выбрана",
            output_refs=("planner-request-1",),
            usage=TokenUsage(prompt_tokens=100, output_tokens=20),
            context={"composition_plan": plan.to_dict()},
        )

        await queue.stage_result(claim, result)
        assert await queue.finalize_stage(claim) == "foundation"

        async with factory() as database:
            record = await database.scalar(
                select(CompositionPlanRecord).where(
                    CompositionPlanRecord.run_id == run_id
                )
            )
            restored = await PatternRepository(database).load_plan(run_id)

        assert record is not None
        assert record.direction_artifact_id == direction_id
        assert record.planner_model_call_id == model_call_id
        assert restored is not None
        assert restored.plan == plan
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_terminal_finalize_records_pattern_outcome_metrics(tmp_path) -> None:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'pattern-outcomes.db'}"
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    plan = complete_plan()
    attempt_id = uuid4()
    now = datetime.now(timezone.utc)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as database, database.begin():
            database.add(Tenant(id=1, name="Alpha", slug="alpha"))
            await database.flush()
            database.add(User(id=10, tenant_id=1, email="owner@example.com"))
            await database.flush()
            project = Project(
                tenant_id=1,
                owner_user_id=10,
                source_url="https://example.com/",
            )
            database.add(project)
            await database.flush()
            run = GenerationRun(
                project_id=project.id,
                mode="direct",
                state="running",
                progress=85,
                current_stage="motion_polish",
                last_completed_stage="conversation",
                next_event_sequence=5,
                idempotency_key=str(uuid4()),
                lease_owner="final-worker",
                lease_expires_at=now + timedelta(seconds=60),
            )
            database.add(run)
            await database.flush()
            for event_id, sequence, event_type in (
                (1, 1, "stage.started"),
                (2, 2, "repair.completed"),
                (3, 3, "visual_repair.completed"),
                (4, 4, "visual_audit.completed"),
            ):
                payload = {
                    "stage": "motion_polish",
                    "attempt_id": str(attempt_id),
                    "worker_id": "final-worker",
                }
                if event_type == "visual_audit.completed":
                    payload.update(
                        {
                            "status": "completed",
                            "diagnostic": json.dumps(
                                {
                                    "verdict": "pass",
                                    "summary": "Ready",
                                    "findings": [
                                        {
                                            "finding_id": "minor-spacing",
                                            "severity": "minor",
                                            "category": "page_subordination",
                                            "screenshot_id": "desktop-open",
                                            "evidence": "Small spacing issue",
                                            "region": {
                                                "x": 0.1,
                                                "y": 0.1,
                                                "width": 0.2,
                                                "height": 0.2,
                                                "semantic_region": "panel",
                                            },
                                            "artifact_fields": ["css"],
                                            "repair_instruction": "Keep spacing balanced",
                                            "confidence": 0.8,
                                        }
                                    ],
                                }
                            ),
                        }
                    )
                database.add(
                    GenerationEvent(
                        id=event_id,
                        run_id=run.id,
                        sequence=sequence,
                        event_type=event_type,
                        public_message=event_type,
                        payload=payload,
                    )
                )
            previous = artifact(revision=4, stage=Stage.CONVERSATION)
            database.add(
                GenerationArtifact(
                    run_id=run.id,
                    revision=previous.revision,
                    stage=previous.stage.value,
                    html=previous.body_html,
                    css=previous.css,
                    javascript=previous.javascript,
                    config={"artifact": previous.to_dict()},
                    quality_status="verified",
                    provenance={},
                )
            )
            first_call = ModelCall(
                run_id=run.id,
                provider="gemini",
                model="builder-model",
                role="foundation",
                mode="direct",
                prompt_version="builder-v1",
                provider_dispatched=True,
                status="completed",
                input_tokens=800,
                output_tokens=100,
                thinking_tokens=20,
                latency_ms=3_000,
                cost_microusd=8_000,
                created_at=now - timedelta(seconds=10),
            )
            final_call = ModelCall(
                run_id=run.id,
                provider="gemini",
                model="critic-model",
                role="visual_judge",
                mode="direct",
                prompt_version="visual-v1",
                provider_dispatched=True,
                status="completed",
                input_tokens=200,
                output_tokens=50,
                thinking_tokens=10,
                latency_ms=1_000,
                cost_microusd=2_500,
                created_at=now,
            )
            database.add_all((first_call, final_call))
            await database.flush()
            await PatternRepository(database).create_plan(
                run_id=run.id,
                plan=plan,
                registry=load_builtin_registry(),
            )
            run_id = run.id
            project_id = project.id
            final_model_call_id = final_call.id

        claim = RunClaim(
            run_id=run_id,
            project_id=project_id,
            worker_id="final-worker",
            mode="direct",
            next_stage="motion_polish",
            last_completed_stage="conversation",
            lease_expires_at=now + timedelta(seconds=60),
            attempt_id=attempt_id,
        )
        final_artifact = artifact(revision=5, stage=Stage.MOTION_POLISH)
        queue = PostgresWorkerQueue(factory, lease_seconds=60)
        await queue.stage_result(
            claim,
            StageResult(
                public_message="Виджет готов",
                artifact=final_artifact,
                context={"composition_plan": plan.to_dict()},
            ),
        )
        assert await queue.finalize_stage(claim) is None

        async with factory() as database:
            outcomes = (
                await database.execute(
                    select(PatternOutcome).order_by(PatternOutcome.idempotency_key)
                )
            ).scalars().all()

        assert len(outcomes) == 5
        assert {row.technical_pass for row in outcomes} == {True}
        assert {row.repair_count for row in outcomes} == {2}
        assert sum(row.input_tokens for row in outcomes) == 1_000
        assert sum(row.output_tokens for row in outcomes) == 150
        assert sum(row.thinking_tokens for row in outcomes) == 30
        assert sum(row.latency_ms for row in outcomes) == 4_000
        assert sum(row.cost_microusd for row in outcomes) == 10_500
        assert {row.model_call_id for row in outcomes} == {final_model_call_id}
        assert all(row.final_artifact_id is not None for row in outcomes)
        assert {row.visual_score for row in outcomes} == {0.96}
    finally:
        await engine.dispose()
