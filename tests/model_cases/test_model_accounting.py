from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import null
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Tenant, User
from app.saas.models import (
    GenerationArtifact,
    GenerationRun,
    GenerationStageAttempt,
    ModelCall,
    Project,
)


START = datetime(2026, 8, 2, 10, 0, tzinfo=timezone.utc)


async def _database(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'accounting.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as database, database.begin():
        database.add(Tenant(id=1, name="Alpha", slug="alpha"))
        database.add(User(id=10, tenant_id=1, email="owner@example.com"))
        project = Project(
            tenant_id=1,
            owner_user_id=10,
            source_url="https://private-source.example/path",
        )
        database.add(project)
        await database.flush()
        run = GenerationRun(
            project_id=project.id,
            mode="direct",
            state="completed",
            progress=100,
            idempotency_key=f"accounting-{uuid4()}",
            started_at=START,
            finished_at=START + timedelta(minutes=5),
        )
        database.add(run)
        await database.flush()
        attempt = GenerationStageAttempt(
            run_id=run.id,
            stage="art_direction",
            ordinal=1,
            status="completed",
            started_at=START,
            finished_at=START + timedelta(minutes=4),
        )
        accounting_failed = GenerationStageAttempt(
            run_id=run.id,
            stage="foundation",
            ordinal=1,
            status="accounting_failed",
            started_at=START + timedelta(minutes=4),
            finished_at=START + timedelta(minutes=5),
        )
        database.add_all([attempt, accounting_failed])
        await database.flush()
        artifact = GenerationArtifact(
            run_id=run.id,
            revision=1,
            stage="art_direction",
            html="<main>safe</main>",
            css="",
            javascript="",
            config={},
            quality_status="verified",
        )
        database.add(artifact)
        await database.flush()
        ids = {
            "run": run.id,
            "attempt": attempt.id,
            "accounting_failed": accounting_failed.id,
            "artifact": artifact.id,
        }
    return engine, factory, ids


def _call(
    *,
    run_id: UUID,
    stage_attempt_id: UUID | None,
    logical_invocation_id: UUID,
    fallback_index: int,
    status: str,
    cost_state: str,
    cost_microusd: int | None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    thinking_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    role: str = "art_direction_generator",
    artifact_id: UUID | None = None,
) -> ModelCall:
    return ModelCall(
        run_id=run_id,
        stage_attempt_id=stage_attempt_id,
        logical_invocation_id=logical_invocation_id,
        operation="direction_candidate",
        semantic_attempt=1,
        candidate_id="candidate-1",
        persona="brand_strategist",
        fallback_index=fallback_index,
        actual_provider="verified-provider" if status == "completed" else None,
        actual_model="verified-model" if status == "completed" else None,
        artifact_id=artifact_id,
        provider=f"target-{fallback_index}",
        model=f"model-{fallback_index}",
        role=role,
        mode="direct",
        prompt_version="direction-v1",
        request_id="private-request-id",
        attempt=fallback_index,
        provider_dispatched=cost_state != "not_billed",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        thinking_tokens=thinking_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        latency_ms=10 * fallback_index,
        status=status,
        error_code="private-error-code" if status != "completed" else None,
        error_message="private-error-message" if status != "completed" else None,
        cost_state=cost_state,
        cost_microusd=null() if cost_microusd is None else cost_microusd,
        pricing_snapshot={"private_path": "C:/private/rates.json"},
    )


async def _seed_calls(factory, ids) -> None:
    shared = uuid4()
    async with factory() as database, database.begin():
        database.add_all(
            [
                _call(
                    run_id=ids["run"],
                    stage_attempt_id=ids["attempt"],
                    logical_invocation_id=shared,
                    fallback_index=1,
                    status="failed",
                    cost_state="reported",
                    cost_microusd=100,
                    input_tokens=10,
                    output_tokens=4,
                    thinking_tokens=1,
                    cache_read_tokens=2,
                    cache_write_tokens=1,
                ),
                _call(
                    run_id=ids["run"],
                    stage_attempt_id=ids["attempt"],
                    logical_invocation_id=shared,
                    fallback_index=2,
                    status="completed",
                    cost_state="estimated",
                    cost_microusd=200,
                    input_tokens=20,
                    output_tokens=8,
                    thinking_tokens=2,
                    cache_read_tokens=3,
                    artifact_id=ids["artifact"],
                ),
                _call(
                    run_id=ids["run"],
                    stage_attempt_id=ids["attempt"],
                    logical_invocation_id=uuid4(),
                    fallback_index=1,
                    status="timed_out",
                    cost_state="unknown",
                    cost_microusd=None,
                ),
                _call(
                    run_id=ids["run"],
                    stage_attempt_id=ids["attempt"],
                    logical_invocation_id=uuid4(),
                    fallback_index=1,
                    status="cancelled",
                    cost_state="not_billed",
                    cost_microusd=0,
                ),
                _call(
                    run_id=ids["run"],
                    stage_attempt_id=None,
                    logical_invocation_id=uuid4(),
                    fallback_index=1,
                    status="completed",
                    cost_state="estimated",
                    cost_microusd=99_999,
                    input_tokens=9_999,
                    output_tokens=999,
                    role="chat_visitor",
                ),
            ]
        )


@pytest.mark.asyncio
async def test_owner_usage_keeps_failed_attempts_and_cost_completeness(tmp_path) -> None:
    from app.models.accounting import aggregate_owner_run_usage

    engine, factory, ids = await _database(tmp_path)
    try:
        await _seed_calls(factory, ids)
        async with factory() as database:
            usage = await aggregate_owner_run_usage(database, run_id=ids["run"])

        assert usage == {
            "logical_invocations": 3,
            "provider_attempts": 4,
            "outcomes": {
                "completed": 1,
                "failed": 1,
                "timed_out": 1,
                "cancelled": 1,
            },
            "tokens": {
                "input": 30,
                "output": 12,
                "thinking": 3,
                "cache_read": 5,
                "cache_write": 1,
            },
            "known_cost_microusd": 300,
            "reported_cost_microusd": 100,
            "estimated_cost_microusd": 200,
            "unknown_cost_attempts": 1,
            "cost_complete": False,
        }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_admin_waterfall_is_ordered_nested_and_allowlisted(tmp_path) -> None:
    from app.models.accounting import load_admin_model_waterfall

    engine, factory, ids = await _database(tmp_path)
    try:
        await _seed_calls(factory, ids)
        async with factory() as database:
            waterfall = await load_admin_model_waterfall(database, run_id=ids["run"])

        assert [row["status"] for row in waterfall["stage_attempts"]] == [
            "completed",
            "accounting_failed",
        ]
        first = waterfall["stage_attempts"][0]
        assert first["stage_attempt_id"] == str(ids["attempt"])
        assert first["stage"] == "art_direction"
        assert [
            attempt["fallback_index"]
            for logical in first["logical_invocations"]
            for attempt in logical["fallbacks"]
        ] == [1, 2, 1, 1]
        assert first["logical_invocations"][0]["fallbacks"][1]["artifact_id"] == str(
            ids["artifact"]
        )
        serialized = json.dumps(waterfall, ensure_ascii=False)
        for forbidden in (
            "private-request-id",
            "private-error-message",
            "private_path",
            "C:/private",
            "prompt_version",
            "request_id",
            "error_message",
            "pricing_snapshot",
        ):
            assert forbidden not in serialized
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_lineage_validation_reports_missing_stage_and_fallback_gap(tmp_path) -> None:
    from app.models.accounting import validate_run_lineage

    engine, factory, ids = await _database(tmp_path)
    try:
        async with factory() as database, database.begin():
            database.add(
                _call(
                    run_id=ids["run"],
                    stage_attempt_id=None,
                    logical_invocation_id=uuid4(),
                    fallback_index=2,
                    status="failed",
                    cost_state="unknown",
                    cost_microusd=None,
                )
            )
        async with factory() as database:
            validation = await validate_run_lineage(database, run_id=ids["run"])

        assert validation["valid"] is False
        assert {issue["code"] for issue in validation["issues"]} == {
            "missing_stage_attempt",
            "non_contiguous_fallback_indexes",
        }
    finally:
        await engine.dispose()
