import asyncio
import importlib
import os
import signal
import shutil
import subprocess
from types import SimpleNamespace
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Tenant, User
from app.saas.models import GenerationArtifact, GenerationEvent, GenerationRun, Project
from builder_lab.engines.base import BuilderEngineError, EngineResult
from builder_lab.models import (
    BuilderRequest,
    DirectionProposal,
    DirectionRole,
    EngineName,
    Stage,
    TokenUsage,
)
from builder_lab.postgres_store import PostgresRunStore
from builder_lab.worker import (
    BuilderWorker,
    DurableVisualStore,
    LeaseLostError,
    OrchestratorStageHandler,
    PostgresWorkerQueue,
    RunClaim,
    StageInput,
    StageResult,
)
from scripts.run_builder_worker import install_signal_handlers, load_stage_handler
from scripts import run_builder_worker
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


def test_runtime_router_has_explicit_repair_and_code_review_policies() -> None:
    config = SimpleNamespace(
        gemini_api_key="test-key",
        gemini_base_url="https://example.test",
        direct_model="gemini-builder",
        reference_analyzer_model="gemini-reference",
        visual_critic_model="gemini-review",
    )
    router = run_builder_worker.make_runtime_model_router(config, None)

    assert ("repair", "direct") in router._policies
    assert ("repair", "express") in router._policies
    assert ("code_review", "direct") in router._policies
    assert ("code_review", "express") in router._policies


def test_production_repair_verifier_factory_uses_router_not_direct_gemini() -> None:
    make_factory = getattr(run_builder_worker, "make_repair_verifier_factory", None)
    assert callable(make_factory)
    config = SimpleNamespace(
        visual_critic_model="gemini-review",
        visual_critic_thinking_level="high",
        visual_critic_timeout_seconds=12,
    )
    router = SimpleNamespace(generate=object())
    run_id = uuid4()

    verifier = make_factory(
        config,
        mode="express",
        model_router=router,
        run_id=run_id,
    )()

    assert verifier._model_router is router
    assert verifier._routing_role == "code_review"
    assert verifier._routing_mode == "express"
    assert verifier._run_id == run_id
    assert verifier._client is None


@pytest.mark.asyncio
async def test_worker_run_closes_router_and_engine_when_shutdown_raises(
    monkeypatch,
) -> None:
    class FakeEngine:
        def __init__(self) -> None:
            self.disposed = False

        async def dispose(self) -> None:
            self.disposed = True

    class FakeRouter:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    class FakeWorker:
        async def run_forever(self) -> None:
            raise RuntimeError("worker failed")

        async def shutdown(self) -> None:
            raise RuntimeError("shutdown failed")

    engine = FakeEngine()
    router = FakeRouter()
    config = SimpleNamespace(
        reference_timeout_seconds=60,
        direct_model="builder",
        builder_thinking_level="high",
    )
    monkeypatch.setattr(run_builder_worker, "_database_url", lambda: "postgresql+asyncpg://test")
    monkeypatch.setattr(run_builder_worker, "create_async_engine", lambda *args, **kwargs: engine)
    monkeypatch.setattr(run_builder_worker, "async_sessionmaker", lambda *args, **kwargs: object())
    monkeypatch.setattr(run_builder_worker, "PostgresWorkerQueue", lambda *args, **kwargs: object())
    monkeypatch.setattr(run_builder_worker.BuilderLabConfig, "from_env", lambda: config)
    monkeypatch.setattr(run_builder_worker, "make_runtime_model_router", lambda *args: router)
    monkeypatch.setattr(run_builder_worker, "make_engine_factories", lambda _config: {EngineName.DIRECT: object()})
    monkeypatch.setattr(
        run_builder_worker.GeminiReferencePipeline,
        "from_config",
        lambda _config: SimpleNamespace(analyze=None),
    )
    monkeypatch.setattr(run_builder_worker, "OrchestratorStageHandler", lambda **kwargs: object())
    monkeypatch.setattr(run_builder_worker, "load_stage_handler", lambda *args, **kwargs: object())
    monkeypatch.setattr(run_builder_worker, "BuilderWorker", lambda **kwargs: FakeWorker())
    monkeypatch.setattr(run_builder_worker, "install_signal_handlers", lambda _worker: None)
    monkeypatch.delenv("KAIGO_BUILDER_STAGE_HANDLER", raising=False)

    with pytest.raises(RuntimeError, match="shutdown failed"):
        await run_builder_worker.run()

    assert router.closed
    assert engine.disposed


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
    assert "dockerfile: Dockerfile.builder-lab" in completed.stdout


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


async def _motion_polish_run(factory, project_id):
    request = BuilderRequest(
        engine=EngineName.DIRECT,
        brief="Visually verify the durable candidate",
    )
    previous = artifact(revision=4, stage=Stage.CONVERSATION)
    direction = DirectionProposal(
        proposal_id="candidate-1",
        role=DirectionRole.INTERACTION_INVENTOR,
        title="Durable visual direction",
        art_direction="A compact branded conversation widget.",
        interaction_model="The panel opens without obscuring the page.",
        safeguards=("Keep all chat controls functional",),
    )
    async with factory() as database, database.begin():
        run = GenerationRun(
            project_id=project_id,
            mode="direct",
            state="queued",
            progress=66,
            last_completed_stage="conversation",
            next_event_sequence=3,
            idempotency_key=f"worker-motion-{uuid4()}",
        )
        database.add(run)
        await database.flush()
        database.add_all(
            [
                GenerationEvent(
                    id=1,
                    run_id=run.id,
                    sequence=1,
                    event_type="run.created",
                    public_message="Запуск создан",
                    payload={"request": request.to_dict()},
                ),
                GenerationEvent(
                    id=2,
                    run_id=run.id,
                    sequence=2,
                    event_type="stage.result_staged",
                    public_message="Контекст направления сохранён",
                    payload={
                        "stage": "conversation",
                        "result": StageResult(
                            public_message="Диалог готов",
                            context={
                                "selected_direction": direction.to_dict(),
                            },
                        ).to_dict(),
                    },
                ),
                GenerationArtifact(
                    run_id=run.id,
                    revision=previous.revision,
                    stage=previous.stage.value,
                    html=previous.body_html,
                    css=previous.css,
                    javascript=previous.javascript,
                    config={"artifact": previous.to_dict()},
                    quality_status="verified",
                ),
            ]
        )
        return run.id, request, previous, direction


class _MotionEngine:
    def __init__(self):
        self.closed = False

    async def generate(self, **kwargs):
        return EngineResult(
            artifact=artifact(
                revision=kwargs["revision"],
                stage=kwargs["stage"],
                change_summary="Сырой motion polish",
            ),
            usage=TokenUsage(prompt_tokens=11, output_tokens=5),
            provider_request_id="motion-call",
        )

    async def close(self):
        self.closed = True

    async def cancel(self):
        return None


@pytest.mark.asyncio
async def test_motion_polish_is_fenced_only_after_visual_gate_success(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    run_id, _, previous, _ = await _motion_polish_run(factory, project_id)
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    motion_engine = _MotionEngine()

    class PassingGate:
        def __init__(self):
            self.calls = 0
            self.accepted = None

        async def evaluate(self, **kwargs):
            self.calls += 1
            async with factory() as database:
                run = await database.get(GenerationRun, run_id)
                revision_five = (
                    await database.execute(
                        select(GenerationArtifact).where(
                            GenerationArtifact.run_id == run_id,
                            GenerationArtifact.revision == 5,
                        )
                    )
                ).scalar_one_or_none()
                assert run.state == "running"
                assert revision_five is None
            self.accepted = replace(
                kwargs["candidate"],
                css=kwargs["candidate"].css + "\n/* visually accepted */",
                change_summary="Визуальная проверка пройдена",
            )
            return self.accepted

    gate = PassingGate()
    handler = OrchestratorStageHandler(
        queue=queue,
        engine_factories={EngineName.DIRECT: lambda: motion_engine},
        reference_analyzer=lambda _url: None,
        visual_gate_factory=lambda _claim: gate,
    )
    worker = BuilderWorker(
        queue=queue,
        worker_id="visual-worker",
        stage_handler=handler,
        heartbeat_interval=1,
    )
    try:
        assert await worker.run_once()
        async with factory() as database:
            run = await database.get(GenerationRun, run_id)
            persisted = (
                await database.execute(
                    select(GenerationArtifact).where(
                        GenerationArtifact.run_id == run_id,
                        GenerationArtifact.revision == previous.revision + 1,
                    )
                )
            ).scalar_one()
            assert gate.calls == 1
            assert run.state == "completed"
            assert persisted.quality_status == "verified"
            assert persisted.config["artifact"] == gate.accepted.to_dict()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_visual_gate_failure_preserves_draft_and_never_completes(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    run_id, _, _, _ = await _motion_polish_run(factory, project_id)
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    durable_store = PostgresRunStore(factory, project_id=project_id)

    class FailingGate:
        def __init__(self, store):
            self.store = store

        async def evaluate(self, **kwargs):
            await self.store.append_event(
                str(run_id),
                event_type="visual_audit.completed",
                stage=Stage.MOTION_POLISH,
                status="completed",
                message="Критики завершили проверку",
                revision=kwargs["candidate"].revision,
            )
            await self.store.stage_visual_draft(
                str(run_id), kwargs["candidate"]
            )
            raise BuilderEngineError(
                "visual_quality_failed",
                "Финальная визуальная проверка виджета не пройдена",
            )

    handler = OrchestratorStageHandler(
        queue=queue,
        engine_factories={EngineName.DIRECT: _MotionEngine},
        reference_analyzer=lambda _url: None,
        visual_gate_factory=lambda claim: FailingGate(
            DurableVisualStore(queue, claim)
        ),
    )
    worker = BuilderWorker(
        queue=queue,
        worker_id="visual-worker",
        stage_handler=handler,
        heartbeat_interval=1,
    )
    try:
        assert await worker.run_once()
        async with factory() as database:
            run = await database.get(GenerationRun, run_id)
            revision_five = (
                await database.execute(
                    select(GenerationArtifact).where(
                        GenerationArtifact.run_id == run_id,
                        GenerationArtifact.revision == 5,
                    )
                )
            ).scalar_one_or_none()
            assert run.state == "failed"
            assert run.last_completed_stage == "conversation"
            assert revision_five is None
            visual_events = (
                await database.execute(
                    select(GenerationEvent).where(
                        GenerationEvent.run_id == run_id,
                        GenerationEvent.event_type == "visual_audit.completed",
                    )
                )
            ).scalars().all()
            assert len(visual_events) == 1
            terminal_events = (
                await database.execute(
                    select(GenerationEvent.event_type).where(
                        GenerationEvent.run_id == run_id,
                        GenerationEvent.event_type.in_((
                            "stage.failed",
                            "run.failed",
                        )),
                    ).order_by(GenerationEvent.sequence)
                )
            ).scalars().all()
            assert terminal_events == ["stage.failed", "run.failed"]
        draft = await durable_store.preview_artifact(str(run_id), revision=5)
        assert draft.stage is Stage.MOTION_POLISH
    finally:
        await engine.dispose()


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
    await _queued_run(factory, project_id)
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
async def test_retryable_engine_error_has_three_total_attempts_and_not_before(
    tmp_path,
    monkeypatch,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(
        factory,
        lease_seconds=30,
        retry_backoff_seconds=5,
    )
    clock = {"now": datetime(2026, 7, 28, 1, 0, tzinfo=timezone.utc)}
    monkeypatch.setattr(queue, "_now", lambda: clock["now"])
    run_id = await _queued_run(factory, project_id, mode="antigravity")
    calls = 0

    async def handle(claim):
        nonlocal calls
        if claim.next_stage == "reference_analysis":
            calls += 1
            if calls < 3:
                raise BuilderEngineError(
                    "quota_exceeded",
                    "Провайдер временно ограничил запросы",
                )
        return StageResult(public_message=f"Готов этап {claim.next_stage}")

    worker = BuilderWorker(
        queue=queue,
        worker_id="retry-worker",
        stage_handler=handle,
        heartbeat_interval=1,
    )
    try:
        assert await worker.run_once()
        assert await queue.claim("too-early") is None
        clock["now"] += timedelta(seconds=6)
        assert await worker.run_once()
        assert await queue.claim("still-too-early") is None
        clock["now"] += timedelta(seconds=11)
        assert await worker.run_once()

        async with factory() as database:
            run = await database.get(GenerationRun, run_id)
            retries = (
                await database.execute(
                    select(GenerationEvent)
                    .where(
                        GenerationEvent.run_id == run_id,
                        GenerationEvent.event_type == "stage.retry_scheduled",
                    )
                    .order_by(GenerationEvent.sequence)
                )
            ).scalars().all()
            assert calls == 3
            assert len(retries) == 2
            assert [event.payload["attempt"] for event in retries] == [1, 2]
            assert run.state == "completed"
            assert run.stage_retry_count == 0
            assert run.retry_not_before is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_third_retryable_failure_fails_run_without_fourth_attempt(
    tmp_path,
    monkeypatch,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(
        factory,
        lease_seconds=30,
        retry_backoff_seconds=5,
    )
    clock = {"now": datetime(2026, 7, 28, 2, 0, tzinfo=timezone.utc)}
    monkeypatch.setattr(queue, "_now", lambda: clock["now"])
    run_id = await _queued_run(factory, project_id, mode="antigravity")
    calls = 0

    async def handle(_claim):
        nonlocal calls
        calls += 1
        raise BuilderEngineError(
            "generation_timeout",
            "Провайдер не ответил вовремя",
        )

    worker = BuilderWorker(
        queue=queue,
        worker_id="retry-worker",
        stage_handler=handle,
        heartbeat_interval=1,
    )
    try:
        for advance in (6, 11, 0):
            assert await worker.run_once()
            clock["now"] += timedelta(seconds=advance)
        async with factory() as database:
            run = await database.get(GenerationRun, run_id)
            assert calls == 3
            assert run.state == "failed"
            assert run.error_code == "generation_timeout"
            assert run.error_message == "Провайдер не ответил вовремя"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_deterministic_engine_error_fails_immediately_with_safe_fields(
    tmp_path,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    run_id = await _queued_run(factory, project_id)
    calls = 0

    async def handle(_claim):
        nonlocal calls
        calls += 1
        raise BuilderEngineError(
            "provider_unavailable",
            "Модель генерации сейчас не настроена",
            diagnostic="secret upstream diagnostic",
        )

    worker = BuilderWorker(
        queue=queue,
        worker_id="deterministic-worker",
        stage_handler=handle,
    )
    try:
        assert await worker.run_once()
        async with factory() as database:
            run = await database.get(GenerationRun, run_id)
            assert calls == 1
            assert run.state == "failed"
            assert run.error_code == "provider_unavailable"
            assert run.error_message == "Модель генерации сейчас не настроена"
            assert "secret" not in run.error_message
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
async def test_finalize_renews_lease_from_clock_after_slow_materialization(
    tmp_path,
    monkeypatch,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=0.5)
    clock = {"now": datetime(2026, 7, 28, 3, 0, tzinfo=timezone.utc)}
    monkeypatch.setattr(queue, "_now", lambda: clock["now"])
    run_id = await _queued_run(factory, project_id)
    claim = await queue.claim("slow-finalizer")
    assert claim is not None
    await queue.stage_result(claim, StageResult(public_message="analysis ready"))
    materialize = queue._materialize_result

    async def slow_materialize(database, run, result, now):
        await materialize(database, run, result, now)
        clock["now"] += timedelta(seconds=0.6)

    monkeypatch.setattr(queue, "_materialize_result", slow_materialize)
    try:
        assert await queue.finalize_stage(claim) == "art_direction"

        continued = await queue.continue_claim(
            run_id,
            worker_id="slow-finalizer",
        )

        assert continued.next_stage == "art_direction"
        assert continued.lease_expires_at > clock["now"]
    finally:
        await engine.dispose()


def test_stage_result_rejects_unbounded_or_non_json_payloads() -> None:
    with pytest.raises(ValueError, match="output_refs"):
        StageResult(public_message="ok", output_refs=tuple(f"ref-{i}" for i in range(33)))
    with pytest.raises(ValueError, match="output_refs"):
        StageResult(public_message="ok", output_refs=(123,))
    with pytest.raises(ValueError, match="events"):
        StageResult(
            public_message="ok",
            events=tuple(
                {"event_type": "repair.completed", "message": "ok"}
                for _ in range(65)
            ),
        )
    with pytest.raises(ValueError, match="context"):
        StageResult(public_message="ok", context={"bad": float("nan")})
    with pytest.raises(ValueError, match="non-string key"):
        StageResult(public_message="ok", context={1: "bad"})
    with pytest.raises(ValueError, match="context is too large"):
        StageResult(public_message="ok", context={"blob": "x" * 131_073})
    with pytest.raises(ValueError, match="stage result is too large"):
        StageResult(
            public_message="ok",
            artifact=artifact(javascript="x" * 1_048_576),
        )
    with pytest.raises(ValueError, match="event type"):
        StageResult(
            public_message="ok",
            events=({"event_type": "run.completed", "message": "forged"},),
        )


@pytest.mark.asyncio
async def test_stage_result_boundary_rejects_wrong_stage_artifact_before_event(
    tmp_path,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    run_id, _request, _previous, _direction = await _motion_polish_run(
        factory, project_id
    )
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    try:
        claim = await queue.claim("validator")
        assert claim is not None
        assert claim.next_stage == "motion_polish"
        candidate = artifact(revision=5, stage=Stage.CONVERSATION)

        with pytest.raises(ValueError, match="artifact stage"):
            await queue.stage_result(
                claim,
                StageResult(public_message="bad", artifact=candidate),
            )
        with pytest.raises(ValueError, match="exactly one greater"):
            await queue.stage_result(
                claim,
                StageResult(
                    public_message="bad revision",
                    artifact=artifact(revision=4, stage=Stage.MOTION_POLISH),
                ),
            )
        with pytest.raises(ValueError, match="exactly one greater"):
            await queue.stage_result(
                claim,
                StageResult(
                    public_message="revision gap",
                    artifact=artifact(revision=6, stage=Stage.MOTION_POLISH),
                ),
            )
        with pytest.raises(ValueError, match="missing_region"):
            await queue.stage_result(
                claim,
                StageResult(
                    public_message="invalid fields",
                    artifact=artifact(
                        revision=5,
                        stage=Stage.MOTION_POLISH,
                        body_html="<section></section>",
                    ),
                ),
            )

        async with factory() as database:
            staged = (
                await database.execute(
                    select(GenerationEvent).where(
                        GenerationEvent.run_id == run_id,
                        GenerationEvent.event_type == "stage.result_staged",
                    )
                )
            ).scalars().all()
            assert all(event.payload.get("stage") != "motion_polish" for event in staged)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_shutdown_cancels_handler_cleanup_and_releases_lease_immediately(
    tmp_path,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    run_id = await _queued_run(factory, project_id, mode="antigravity")
    entered = asyncio.Event()
    cleaned = asyncio.Event()

    async def handle(_claim):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cleaned.set()

    worker = BuilderWorker(
        queue=queue,
        worker_id="terminating",
        stage_handler=handle,
        heartbeat_interval=1,
    )
    run_task = asyncio.create_task(worker.run_once())
    try:
        await entered.wait()
        await worker.shutdown()
        await asyncio.gather(run_task, return_exceptions=True)

        assert cleaned.is_set()
        replacement = await queue.claim("replacement")
        assert replacement is not None
        assert replacement.run_id == run_id
        assert replacement.next_stage == "reference_analysis"
        async with factory() as database:
            run = await database.get(GenerationRun, run_id)
            assert run.state == "running"
            assert run.lease_owner == "replacement"
            interrupted = (
                await database.execute(
                    select(GenerationEvent).where(
                        GenerationEvent.run_id == run_id,
                        GenerationEvent.event_type == "stage.interrupted",
                    )
                )
            ).scalars().all()
            assert len(interrupted) == 1
    finally:
        if not run_task.done():
            run_task.cancel()
        await engine.dispose()


@pytest.mark.asyncio
async def test_shutdown_during_terminal_reconcile_never_claims_new_work(
    tmp_path,
    monkeypatch,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    await _queued_run(factory, project_id)
    entered = asyncio.Event()
    release = asyncio.Event()
    claim_calls = 0
    original_claim = queue.claim

    async def blocked_reconcile():
        entered.set()
        await release.wait()

    async def counted_claim(worker_id):
        nonlocal claim_calls
        claim_calls += 1
        return await original_claim(worker_id)

    monkeypatch.setattr(queue, "claim", counted_claim)
    worker = BuilderWorker(
        queue=queue,
        worker_id="shutdown-before-claim",
        stage_handler=lambda _claim: asyncio.sleep(0),
        terminal_reconciler=blocked_reconcile,
    )
    run_task = asyncio.create_task(worker.run_once())
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        await asyncio.wait_for(worker.shutdown(), timeout=2)
        release.set()
        await asyncio.gather(run_task, return_exceptions=True)

        assert claim_calls == 0
    finally:
        release.set()
        if not run_task.done():
            run_task.cancel()
        await engine.dispose()


@pytest.mark.asyncio
async def test_cli_sigterm_handler_shuts_down_long_stage(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    await _queued_run(factory, project_id, mode="antigravity")
    entered = asyncio.Event()
    cleaned = asyncio.Event()

    async def handle(_claim):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cleaned.set()

    worker = BuilderWorker(
        queue=queue,
        worker_id="signal-worker",
        stage_handler=handle,
        heartbeat_interval=1,
    )

    class FakeLoop:
        def __init__(self):
            self.handlers = {}

        def add_signal_handler(self, signum, callback):
            self.handlers[signum] = callback

        def create_task(self, coroutine):
            return asyncio.create_task(coroutine)

    fake_loop = FakeLoop()
    install_signal_handlers(worker, loop=fake_loop)
    run_task = asyncio.create_task(worker.run_once())
    try:
        await entered.wait()
        fake_loop.handlers[signal.SIGTERM]()
        await asyncio.wait_for(cleaned.wait(), timeout=2)
        await asyncio.wait_for(run_task, timeout=2)
        assert await queue.claim("replacement") is not None
    finally:
        if not run_task.done():
            run_task.cancel()
        await engine.dispose()


@pytest.mark.asyncio
async def test_released_staged_result_is_finalized_without_reexecution(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    run_id = await _queued_run(factory, project_id, mode="antigravity")
    first = await queue.claim("terminating")
    assert first is not None
    await queue.stage_result(first, StageResult(public_message="analysis ready"))
    await queue.release_claim(first)
    calls = 0

    async def handle(claim):
        nonlocal calls
        calls += 1
        return StageResult(public_message=f"done {claim.next_stage}")

    replacement = BuilderWorker(
        queue=queue,
        worker_id="replacement",
        stage_handler=handle,
        heartbeat_interval=1,
    )
    try:
        assert await replacement.run_once()
        assert calls == 1
        async with factory() as database:
            run = await database.get(GenerationRun, run_id)
            assert run.state == "completed"
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


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.skipif(
    not POSTGRES_URL,
    reason="set KAIGO_TEST_POSTGRES_URL to a disposable PostgreSQL 15 database",
)
async def test_postgres_finalize_renews_short_lease_after_materialization(
    monkeypatch,
) -> None:
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
            await database.flush()
            project = Project(
                tenant_id=1,
                owner_user_id=10,
                source_url="https://example.com/",
            )
            database.add(project)
            await database.flush()
            project_id = project.id
        run_id = await _queued_run(factory, project_id)
        queue = PostgresWorkerQueue(factory, lease_seconds=1)
        claim = await queue.claim("postgres-slow-finalizer")
        assert claim is not None
        await queue.stage_result(
            claim,
            StageResult(public_message="analysis ready"),
        )
        materialize = queue._materialize_result

        async def slow_materialize(database, run, result, now):
            await materialize(database, run, result, now)
            await asyncio.sleep(1.1)

        monkeypatch.setattr(queue, "_materialize_result", slow_materialize)

        assert await queue.finalize_stage(claim) == "art_direction"
        continued = await queue.continue_claim(
            run_id,
            worker_id="postgres-slow-finalizer",
        )

        assert continued.next_stage == "art_direction"
        assert continued.lease_expires_at > datetime.now(timezone.utc)
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()
