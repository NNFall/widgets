import asyncio
import importlib
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Tenant, User
from app.saas.models import GenerationEvent, GenerationRun, Project
from builder_lab.engines.base import EngineResult
from builder_lab.models import BuilderRequest, EngineName, Stage, TokenUsage
from builder_lab.worker import (
    BuilderWorker,
    LeaseLostError,
    OrchestratorStageHandler,
    PostgresWorkerQueue,
    RunClaim,
    StageInput,
    StageResult,
)
from scripts.run_builder_worker import load_stage_handler
from tests.builder_lab_cases.test_validation import artifact


POSTGRES_URL = os.getenv("KAIGO_TEST_POSTGRES_URL")


def test_worker_module_exposes_durable_queue_contract() -> None:
    worker = importlib.import_module("builder_lab.worker")

    assert worker.PostgresWorkerQueue
    assert worker.BuilderWorker
    assert worker.RunClaim
    assert worker.LeaseLostError


def test_worker_cli_uses_configured_builtin_handler_by_default(
    monkeypatch,
) -> None:
    monkeypatch.delenv("KAIGO_BUILDER_STAGE_HANDLER", raising=False)

    async def builtin_handler(claim):
        return StageResult(public_message=f"Готов этап {claim.next_stage}")

    assert load_stage_handler(default_handler=builtin_handler) is builtin_handler


def test_worker_cli_rejects_sync_stage_handler() -> None:
    with pytest.raises(RuntimeError, match="must be an async callable"):
        load_stage_handler("builder_lab.worker:STAGE_PUBLIC_NAMES")


def test_compose_config_wires_builtin_handler_without_project_env(tmp_path) -> None:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("docker CLI is not installed")
    empty_env = tmp_path / "empty.env"
    empty_env.write_text("", encoding="utf-8")
    project_root = Path(__file__).resolve().parents[2]

    completed = subprocess.run(
        [
            docker,
            "compose",
            "--env-file",
            str(empty_env),
            "config",
        ],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "KAIGO_BUILDER_STAGE_HANDLER: builtin:orchestrator" in completed.stdout


@pytest.mark.asyncio
async def test_builtin_handler_executes_only_claimed_engine_stage() -> None:
    request = BuilderRequest(
        engine=EngineName.ANTIGRAVITY,
        brief="Build one durable stage",
    )

    class FakeQueue:
        async def stage_input(self, claim):
            return StageInput(request=request)

    class FakeEngine:
        def __init__(self):
            self.calls = []
            self.closed = False

        async def generate(self, **kwargs):
            self.calls.append(kwargs)
            return EngineResult(
                artifact=artifact(revision=1, stage=kwargs["stage"]),
                usage=TokenUsage(prompt_tokens=7, output_tokens=3),
                provider_request_id="provider-call-1",
            )

        async def cancel(self):
            return None

        async def close(self):
            self.closed = True

    engine = FakeEngine()
    handler = OrchestratorStageHandler(
        queue=FakeQueue(),
        engine_factories={EngineName.ANTIGRAVITY: lambda: engine},
        reference_analyzer=lambda _url: None,
    )
    claim = RunClaim(
        run_id=uuid4(),
        project_id=uuid4(),
        worker_id="worker",
        mode="antigravity",
        next_stage="agent_build",
        last_completed_stage="reference_analysis",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
    )

    result = await handler(claim)

    assert [call["stage"] for call in engine.calls] == [Stage.AGENT_BUILD]
    assert result.artifact is not None
    assert result.artifact.stage is Stage.AGENT_BUILD
    assert result.output_refs == ("provider-call-1",)
    assert result.usage.total_tokens == 10
    assert engine.closed


async def _database(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'worker.db'}")
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
            brief="Durable worker",
        )
        database.add(project)
        await database.flush()
        project_id = project.id
    return engine, factory, project_id


async def _queued_run(factory, project_id, *, mode="direct", state="queued") -> UUID:
    async with factory() as database, database.begin():
        run = GenerationRun(
            project_id=project_id,
            mode=mode,
            state=state,
            progress=0,
            next_event_sequence=1,
            idempotency_key=f"worker-{mode}-{datetime.now(timezone.utc).timestamp()}",
        )
        database.add(run)
        await database.flush()
        return run.id


@pytest.mark.asyncio
async def test_stage_input_loads_durable_request_under_attempt_fence(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    request = BuilderRequest(
        engine=EngineName.DIRECT,
        brief="Load the durable request",
        source_url="https://example.com/",
    )
    async with factory() as database, database.begin():
        run = GenerationRun(
            project_id=project_id,
            mode="direct",
            state="queued",
            progress=0,
            next_event_sequence=2,
            idempotency_key="worker-durable-input",
        )
        database.add(run)
        await database.flush()
        database.add(
            GenerationEvent(
                id=1,
                run_id=run.id,
                sequence=1,
                event_type="run.created",
                public_message="Запуск создан",
                payload={"request": request.to_dict()},
            )
        )
        run_id = run.id
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    try:
        claim = await queue.claim("worker")
        assert claim is not None
        assert claim.run_id == run_id

        stage_input = await queue.stage_input(claim)

        assert stage_input.request == request
        assert stage_input.previous_artifact is None
        assert stage_input.context == {}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_only_one_worker_claims_a_run(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    run_id = await _queued_run(factory, project_id)
    try:
        claims = await asyncio.gather(queue.claim("w1"), queue.claim("w2"))

        claimed = [claim for claim in claims if claim is not None]
        assert len(claimed) == 1
        assert claimed[0].run_id == run_id
        assert claimed[0].next_stage == "reference_analysis"
        async with factory() as database:
            run = await database.get(GenerationRun, run_id)
            assert run.lease_owner == claimed[0].worker_id
            assert run.current_stage == "reference_analysis"
            assert run.state == "running"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_inline_created_run_is_not_taken_from_durable_queue(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    await _queued_run(factory, project_id, state="created")
    try:
        assert await queue.claim("worker") is None
    finally:
        await engine.dispose()


def test_claim_query_uses_postgres_skip_locked() -> None:
    statement = PostgresWorkerQueue.claim_statement(
        datetime(2026, 7, 28, tzinfo=timezone.utc)
    )

    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE SKIP LOCKED" in sql


@pytest.mark.asyncio
async def test_expired_lease_resumes_from_last_completed_stage(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    run_id = await _queued_run(factory, project_id)
    try:
        first = await queue.claim("original")
        assert first is not None
        await queue.complete_stage(
            run_id, "reference_analysis", worker_id="original"
        )
        async with factory() as database, database.begin():
            run = await database.get(GenerationRun, run_id)
            run.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

        replacement = await queue.claim("replacement")

        assert replacement is not None
        assert replacement.run_id == run_id
        assert replacement.last_completed_stage == "reference_analysis"
        assert replacement.next_stage == "art_direction"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_heartbeat_and_checkpoint_require_current_live_owner(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    run_id = await _queued_run(factory, project_id)
    try:
        claim = await queue.claim("owner")
        assert claim is not None

        with pytest.raises(LeaseLostError):
            await queue.heartbeat(run_id, worker_id="intruder")
        with pytest.raises(LeaseLostError):
            await queue.complete_stage(
                run_id, "reference_analysis", worker_id="intruder"
            )

        refreshed = await queue.heartbeat(run_id, worker_id="owner")
        assert refreshed.lease_expires_at >= claim.lease_expires_at
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_checkpoint_event_and_next_stage_are_committed_together(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    run_id = await _queued_run(factory, project_id)
    try:
        assert await queue.claim("worker") is not None

        next_stage = await queue.complete_stage(
            run_id, "reference_analysis", worker_id="worker"
        )

        assert next_stage == "art_direction"
        async with factory() as database:
            run = await database.get(GenerationRun, run_id)
            events = (
                await database.execute(
                    select(GenerationEvent)
                    .where(GenerationEvent.run_id == run_id)
                    .order_by(GenerationEvent.sequence)
                )
            ).scalars().all()
            assert run.last_completed_stage == "reference_analysis"
            assert run.current_stage == "art_direction"
            assert run.progress == 16
            assert [event.event_type for event in events] == [
                "stage.started",
                "stage.completed",
            ]
            assert events[0].public_message == "Начат анализ исходного сайта"
            assert events[1].public_message == "Анализ исходного сайта завершён"
            assert events[-1].payload["next_stage"] == "art_direction"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_replayed_checkpoint_is_idempotent(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    run_id = await _queued_run(factory, project_id)
    try:
        assert await queue.claim("worker") is not None
        assert (
            await queue.complete_stage(
                run_id, "reference_analysis", worker_id="worker"
            )
            == "art_direction"
        )

        replayed = await queue.complete_stage(
            run_id, "reference_analysis", worker_id="worker"
        )

        assert replayed == "art_direction"
        async with factory() as database:
            completed = (
                await database.execute(
                    select(GenerationEvent).where(
                        GenerationEvent.run_id == run_id,
                        GenerationEvent.event_type == "stage.completed",
                    )
                )
            ).scalars().all()
            assert len(completed) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_final_checkpoint_finishes_run_and_releases_lease(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    run_id = await _queued_run(factory, project_id, mode="antigravity")
    try:
        assert await queue.claim("worker") is not None
        await queue.complete_stage(
            run_id, "reference_analysis", worker_id="worker"
        )
        final_stage = await queue.complete_stage(
            run_id, "agent_build", worker_id="worker"
        )

        assert final_stage is None
        async with factory() as database:
            run = await database.get(GenerationRun, run_id)
            events = (
                await database.execute(
                    select(GenerationEvent)
                    .where(GenerationEvent.run_id == run_id)
                    .order_by(GenerationEvent.sequence)
                )
            ).scalars().all()
            assert run.state == "completed"
            assert run.progress == 100
            assert run.lease_owner is None
            assert run.lease_expires_at is None
            assert run.finished_at is not None
            assert events[-1].event_type == "run.completed"
            assert events[-1].public_message == "Виджет готов"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_continues_all_stages_under_its_claim(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    run_id = await _queued_run(factory, project_id, mode="antigravity")
    handled = []

    async def handle(claim) -> None:
        handled.append(claim.next_stage)
        return StageResult(public_message=f"Готов этап {claim.next_stage}")

    worker = BuilderWorker(
        queue=queue,
        worker_id="worker",
        stage_handler=handle,
        heartbeat_interval=1,
    )
    try:
        assert await worker.run_once()

        assert handled == ["reference_analysis", "agent_build"]
        async with factory() as database:
            run = await database.get(GenerationRun, run_id)
            assert run.state == "completed"
            assert run.last_completed_stage == "agent_build"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cancelled_claim_never_invokes_stage_handler(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=1)
    run_id = await _queued_run(factory, project_id)
    called = False

    async def handle(_claim) -> None:
        nonlocal called
        called = True

    try:
        await queue.request_cancel(run_id)
        worker = BuilderWorker(
            queue=queue,
            worker_id="worker",
            stage_handler=handle,
            heartbeat_interval=0.05,
        )

        assert await worker.run_once()
        assert not called
        async with factory() as database:
            run = await database.get(GenerationRun, run_id)
            assert run.state == "cancelled"
            assert run.lease_owner is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_lost_lease_cancels_inflight_stage_and_replacement_resumes(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=0.15)
    run_id = await _queued_run(factory, project_id)
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def handle(_claim) -> None:
        entered.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    worker = BuilderWorker(
        queue=queue,
        worker_id="original",
        stage_handler=handle,
        heartbeat_interval=0.2,
    )
    task = asyncio.create_task(worker.run_once())
    try:
        await entered.wait()
        await asyncio.sleep(0.17)
        replacement = await queue.claim("replacement")
        assert replacement is not None

        with pytest.raises(LeaseLostError):
            await task
        assert cancelled.is_set()
        assert replacement.next_stage == "reference_analysis"
    finally:
        if not task.done():
            task.cancel()
        await engine.dispose()


@pytest.mark.asyncio
async def test_handler_crash_leaves_checkpoint_retryable_after_expiry(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=0.2)
    run_id = await _queued_run(factory, project_id)

    async def crash(_claim) -> None:
        raise RuntimeError("worker process failed")

    worker = BuilderWorker(
        queue=queue,
        worker_id="crashed",
        stage_handler=crash,
        heartbeat_interval=0.01,
    )
    try:
        with pytest.raises(RuntimeError, match="worker process failed"):
            await worker.run_once()
        await asyncio.sleep(0.21)

        replacement = await queue.claim("replacement")

        assert replacement is not None
        assert replacement.run_id == run_id
        assert replacement.next_stage == "reference_analysis"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_staged_result_survives_crash_without_reexecuting_stage(
    tmp_path, monkeypatch
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=0.2)
    run_id = await _queued_run(factory, project_id, mode="antigravity")
    calls = {"reference_analysis": 0, "agent_build": 0}

    async def handle(claim):
        calls[claim.next_stage] += 1
        return StageResult(
            public_message=f"Готов этап {claim.next_stage}",
            output_refs=(f"model-call:{claim.next_stage}",),
        )

    original_finalize = queue.finalize_stage

    async def crash_after_staging(_claim):
        raise RuntimeError("process died after persisted side effect")

    monkeypatch.setattr(queue, "finalize_stage", crash_after_staging)
    first = BuilderWorker(
        queue=queue,
        worker_id="crashed",
        stage_handler=handle,
        heartbeat_interval=0.01,
    )
    try:
        with pytest.raises(RuntimeError, match="process died"):
            await first.run_once()
        await asyncio.sleep(0.21)
        monkeypatch.setattr(queue, "finalize_stage", original_finalize)
        replacement = BuilderWorker(
            queue=queue,
            worker_id="replacement",
            stage_handler=handle,
            heartbeat_interval=0.01,
        )

        assert await replacement.run_once()

        assert calls["reference_analysis"] == 1
        assert calls["agent_build"] == 1
        async with factory() as database:
            staged = (
                await database.execute(
                    select(GenerationEvent).where(
                        GenerationEvent.run_id == run_id,
                        GenerationEvent.event_type == "stage.result_staged",
                    )
                )
            ).scalars().all()
            assert len(staged) == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stale_attempt_cannot_finalize_staged_result(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    run_id = await _queued_run(factory, project_id)
    try:
        stale = await queue.claim("stale")
        assert stale is not None
        await queue.stage_result(
            stale,
            StageResult(
                public_message="Анализ готов",
                output_refs=("model-call:reference",),
            ),
        )
        async with factory() as database, database.begin():
            run = await database.get(GenerationRun, run_id)
            run.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        replacement = await queue.claim("replacement")
        assert replacement is not None
        assert replacement.attempt_id != stale.attempt_id

        with pytest.raises(LeaseLostError):
            await queue.finalize_stage(stale)

        assert await queue.finalize_stage(replacement) == "art_direction"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.skipif(
    not POSTGRES_URL,
    reason="set KAIGO_TEST_POSTGRES_URL to a disposable PostgreSQL 15 database",
)
async def test_postgres_workers_use_skip_locked_for_single_claim() -> None:
    first_engine = create_async_engine(POSTGRES_URL)
    second_engine = create_async_engine(POSTGRES_URL)
    first_factory = async_sessionmaker(first_engine, expire_on_commit=False)
    second_factory = async_sessionmaker(second_engine, expire_on_commit=False)
    try:
        async with first_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
        async with first_factory() as database, database.begin():
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
            project_id = project.id
        run_id = await _queued_run(first_factory, project_id)
        first = PostgresWorkerQueue(first_factory, lease_seconds=30)
        second = PostgresWorkerQueue(second_factory, lease_seconds=30)

        claims = await asyncio.gather(first.claim("w1"), second.claim("w2"))

        claimed = [claim for claim in claims if claim is not None]
        assert len(claimed) == 1
        assert claimed[0].run_id == run_id
    finally:
        async with first_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await second_engine.dispose()
        await first_engine.dispose()
