from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy import event, null
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Tenant, User
from app.generation_timeline import load_owner_timeline_summary
from app.saas.models import (
    GenerationEvent,
    GenerationForensicManifest,
    GenerationRun,
    GenerationStageAttempt,
    ModelCall,
    Project,
)
from builder_lab.generation_events import GenerationEventType


RUN_ID = UUID("10000000-0000-0000-0000-000000000001")
PROJECT_ID = UUID("20000000-0000-0000-0000-000000000001")
START = datetime(2026, 7, 30, 9, 0, tzinfo=timezone.utc)
STAGE_STATUSES = (
    "running",
    "result_staged",
    "completed",
    "failed",
    "interrupted",
    "cancelled",
    "accounting_failed",
    "unknown",
)


def _model_call(
    ordinal: int,
    *,
    cost_state: str,
    cost_microusd: int | None,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    role: str = "private-role",
) -> ModelCall:
    return ModelCall(
        id=UUID(f"30000000-0000-0000-0000-{ordinal:012d}"),
        run_id=RUN_ID,
        logical_invocation_id=UUID(
            f"40000000-0000-0000-0000-{ordinal:012d}"
        ),
        operation="private-operation",
        semantic_attempt=1,
        fallback_index=1,
        actual_provider="private-actual-provider",
        actual_model="private-actual-model",
        provider="private-provider",
        model="private-model",
        role=role,
        mode="direct",
        prompt_version="private-prompt-version",
        request_id=f"private-request-{ordinal}",
        attempt=1,
        provider_dispatched=cost_state != "not_billed",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        thinking_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
        latency_ms=latency_ms,
        status="completed",
        error_code="private-error-code",
        error_message="private-error-message",
        cost_state=cost_state,
        cost_microusd=null() if cost_microusd is None else cost_microusd,
        pricing_snapshot={"path": "C:/private/pricing.json"},
    )


async def _seed_timeline(factory: async_sessionmaker) -> None:
    async with factory() as database, database.begin():
        database.add(Tenant(id=1, name="Alpha", slug="alpha"))
        database.add(User(id=10, tenant_id=1, email="owner@example.com"))
        database.add(User(id=11, tenant_id=1, email="other@example.com"))
        project = Project(
            id=PROJECT_ID,
            tenant_id=1,
            owner_user_id=10,
            source_url="https://private-source.example/path",
        )
        run = GenerationRun(
            id=RUN_ID,
            project_id=PROJECT_ID,
            mode="express",
            state="completed",
            idempotency_key="private-idempotency-key",
            change_request="private change request",
            lease_owner="private-worker",
            error_message="private run error",
            started_at=START + timedelta(minutes=1),
            finished_at=START + timedelta(minutes=9),
            created_at=START,
        )
        database.add_all([project, run])
        await database.flush()
        database.add_all(
            [
                GenerationEvent(
                    id=1,
                    run_id=RUN_ID,
                    sequence=1,
                    event_type="run.created",
                    public_message="private-message-marker",
                    payload={"request_id": "private-request-marker"},
                    public_payload={"status": "queued"},
                    forensic_ref="C:/private/forensic.jsonl",
                ),
                GenerationEvent(
                    id=2,
                    run_id=RUN_ID,
                    sequence=2,
                    event_type="stage.started",
                    public_message="another-private-message",
                    payload={"provider": "private-event-provider"},
                    public_payload={"status": "running", "stage": "foundation"},
                ),
                GenerationEvent(
                    id=3,
                    run_id=RUN_ID,
                    sequence=3,
                    event_type="legacy.private_event",
                    public_message="legacy-private-message",
                    payload={"checksum": "a" * 64},
                    forensic_ref="private/storage/path",
                ),
            ]
        )
        database.add_all(
            [
                GenerationStageAttempt(
                    id=UUID("50000000-0000-0000-0000-000000000001"),
                    run_id=RUN_ID,
                    stage="foundation",
                    ordinal=1,
                    status="completed",
                    started_at=START + timedelta(minutes=2),
                    finished_at=START + timedelta(minutes=3),
                ),
                GenerationStageAttempt(
                    id=UUID("50000000-0000-0000-0000-000000000002"),
                    run_id=RUN_ID,
                    stage="foundation",
                    ordinal=3,
                    status="failed",
                    started_at=START + timedelta(minutes=4),
                    finished_at=START + timedelta(minutes=7),
                ),
                GenerationStageAttempt(
                    id=UUID("50000000-0000-0000-0000-000000000003"),
                    run_id=RUN_ID,
                    stage="private_unknown_stage",
                    ordinal=2,
                    status="cancelled",
                    started_at=START + timedelta(minutes=5),
                    finished_at=START + timedelta(minutes=6),
                ),
            ]
        )
        database.add_all(
            [
                _model_call(
                    1,
                    cost_state="reported",
                    cost_microusd=1_500_000,
                    input_tokens=100,
                    output_tokens=30,
                    latency_ms=200,
                ),
                _model_call(
                    2,
                    cost_state="estimated",
                    cost_microusd=250_000,
                    input_tokens=50,
                    output_tokens=10,
                    latency_ms=100,
                ),
                _model_call(
                    3,
                    cost_state="unknown",
                    cost_microusd=None,
                    input_tokens=7,
                    output_tokens=3,
                    latency_ms=50,
                ),
                _model_call(
                    4,
                    cost_state="not_billed",
                    cost_microusd=0,
                    input_tokens=5,
                    output_tokens=1,
                    latency_ms=20,
                ),
                _model_call(
                    5,
                    cost_state="unknown",
                    cost_microusd=None,
                    input_tokens=10_000,
                    output_tokens=5_000,
                    latency_ms=20_000,
                    role="chat_visitor",
                ),
            ]
        )
        database.add(
            GenerationForensicManifest(
                run_id=RUN_ID,
                user_id=10,
                project_id=PROJECT_ID,
                storage_key="C:/private/manifest.jsonl",
                schema_version=1,
                state="completed",
                last_event_sequence=3,
                entry_count=3,
                byte_count=999,
                manifest_sha256="b" * 64,
                metadata_json={"secret": "private-manifest-marker"},
            )
        )


@pytest.mark.asyncio
async def test_owner_timeline_is_bounded_aggregate_only_and_fail_closed(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'timeline.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await _seed_timeline(factory)

    statements: list[str] = []

    def record_statement(_conn, _cursor, statement, _params, _context, _many) -> None:
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record_statement)
    try:
        async with factory() as database:
            summary = await load_owner_timeline_summary(
                database,
                run_id=RUN_ID,
                owner_user_id=10,
                tenant_id=1,
            )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record_statement)
        await engine.dispose()

    expected_events = {event_type.value: 0 for event_type in GenerationEventType}
    expected_events.update(
        {"run.created": 1, "stage.started": 1, "unknown": 1}
    )
    empty_statuses = {status: 0 for status in STAGE_STATUSES}

    assert summary == {
        "run": {
            "id": str(RUN_ID),
            "state": "completed",
            "created_at": "2026-07-30T09:00:00Z",
            "started_at": "2026-07-30T09:01:00Z",
            "finished_at": "2026-07-30T09:09:00Z",
        },
        "event_counts": expected_events,
        "stage_attempts": [
            {
                "stage": "foundation",
                "count": 2,
                "status_counts": {
                    **empty_statuses,
                    "completed": 1,
                    "failed": 1,
                },
                "min_ordinal": 1,
                "max_ordinal": 3,
                "started_at_min": "2026-07-30T09:02:00Z",
                "finished_at_max": "2026-07-30T09:07:00Z",
            },
            {
                "stage": "unknown",
                "count": 1,
                "status_counts": {**empty_statuses, "cancelled": 1},
                "min_ordinal": 2,
                "max_ordinal": 2,
                "started_at_min": "2026-07-30T09:05:00Z",
                "finished_at_max": "2026-07-30T09:06:00Z",
            },
        ],
        "model_usage": {
            "call_count": 4,
            "input_tokens": 162,
            "output_tokens": 44,
            "total_tokens": 206,
            "latency_ms_total": 370,
            "cost": {
                "reported_usd": 1.5,
                "estimated_usd": 0.25,
                "unknown_calls": 1,
                "not_billed_calls": 1,
                "known_total_usd": 1.75,
                "cost_complete": False,
            },
        },
    }

    serialized = json.dumps(summary, ensure_ascii=False)
    for private_marker in (
        "private-message",
        "private-request",
        "private-provider",
        "private-model",
        "private-role",
        "private-error",
        "private/storage",
        "private/manifest",
        "private-manifest-marker",
        "checksum",
        "forensic_ref",
        "payload",
    ):
        assert private_marker not in serialized

    selects = [
        statement.lower()
        for statement in statements
        if statement.lstrip().upper().startswith("SELECT")
    ]
    assert len(selects) == 4
    event_select = next(
        statement for statement in selects if "from generation_events" in statement
    )
    assert "group by" in event_select
    assert "payload" not in event_select
    assert "public_message" not in event_select
    assert "forensic_ref" not in event_select
    assert "generation_forensic" not in "\n".join(selects)


@pytest.mark.asyncio
async def test_owner_timeline_returns_none_before_aggregating_cross_owner_run(
    tmp_path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'scope.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await _seed_timeline(factory)

    statements: list[str] = []

    def record_statement(_conn, _cursor, statement, _params, _context, _many) -> None:
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record_statement)
    try:
        async with factory() as database:
            summary = await load_owner_timeline_summary(
                database,
                run_id=RUN_ID,
                owner_user_id=11,
                tenant_id=1,
            )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record_statement)
        await engine.dispose()

    assert summary is None
    assert len(
        [
            statement
            for statement in statements
            if statement.lstrip().upper().startswith("SELECT")
        ]
    ) == 1
