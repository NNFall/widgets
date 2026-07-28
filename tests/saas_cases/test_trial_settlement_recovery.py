from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.billing.service import TrialFailureKind, TrialService, TrialSettlementReconciler
from app.db.base import Base
from app.db.models import Tenant, User
from app.models.contracts import ModelRequest, ProviderCapabilities
from app.models.router import ModelPolicy, ModelRouter, ProviderTarget, SqlModelCallAudit
from app.saas.models import (
    GenerationEvent,
    GenerationRun,
    ModelCall,
    Project,
    TrialEntitlement,
    UsageLedger,
    UserIdentity,
)
from builder_lab.engines.base import BuilderEngineError
from builder_lab.worker import (
    MAX_STAGE_EXECUTIONS,
    BuilderWorker,
    PostgresWorkerQueue,
    failure_category_for_error,
    StageResult,
)

POSTGRES_URL = os.getenv("KAIGO_TEST_POSTGRES_URL")


@pytest.mark.parametrize(
    ("error_code", "category"),
    [
        ("model_unavailable", TrialFailureKind.PROVIDER),
        ("reference_capture_incomplete", TrialFailureKind.INFRASTRUCTURE),
        ("visual_quality_failed", TrialFailureKind.MODEL_INVALID_OUTPUT),
        ("reference_analysis_too_large", TrialFailureKind.CONTENT),
        ("reference_url_unsafe", TrialFailureKind.VALIDATION),
        ("internal_error", TrialFailureKind.PLATFORM),
    ],
)
def test_worker_failure_categories_are_structured(error_code, category) -> None:
    error = BuilderEngineError(error_code, "Public wording is irrelevant")
    assert failure_category_for_error(error) is category


async def _settlement_database(tmp_path, *, database_url: str | None = None):
    engine = create_async_engine(
        database_url or f"sqlite+aiosqlite:///{tmp_path / 'settlement.db'}"
    )
    async with engine.begin() as connection:
        if database_url:
            await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as database, database.begin():
        database.add(Tenant(id=1, name="Alpha", slug="alpha"))
        database.add(User(id=10, tenant_id=1, email="owner@example.com"))
        await database.flush()
        database.add(UserIdentity(
            user_id=10,
            provider="google",
            provider_subject="owner",
            email="owner@example.com",
            email_verified=True,
        ))
        project = Project(
            tenant_id=1,
            owner_user_id=10,
            source_url="https://example.com/",
        )
        database.add(project)
        await database.flush()
        runs = [
            GenerationRun(
                project_id=project.id,
                mode="express",
                state="queued",
                idempotency_key=f"settle-{suffix}",
            )
            for suffix in ("success", "provider", "cancel")
        ]
        database.add_all(runs)
        await database.flush()
        run_ids = tuple(run.id for run in runs)
    return engine, factory, run_ids


async def _reserve(factory, run_id: UUID):
    return await TrialService(factory).reserve_trial(10, run_id, request_id=str(run_id))


@pytest.mark.asyncio
async def test_terminal_success_settles_trial_and_model_usage_exactly_once(tmp_path) -> None:
    engine, factory, run_ids = await _settlement_database(tmp_path)
    try:
        await _reserve(factory, run_ids[0])
        async with factory() as database, database.begin():
            run = await database.get(GenerationRun, run_ids[0])
            run.state = "completed"
            call = ModelCall(
                run_id=run.id,
                provider="gemini",
                model="gemini-builder",
                role="widget_generator",
                mode="express",
                prompt_version="builder-v1",
                input_tokens=20,
                output_tokens=10,
                thinking_tokens=2,
                latency_ms=100,
                status="completed",
                cost_microusd=90,
            )
            database.add(call)

        reconciler = TrialSettlementReconciler(factory)
        assert await reconciler.settle_run(run_ids[0]) == "consumed"
        assert await reconciler.settle_run(run_ids[0]) == "consumed"

        async with factory() as database:
            run = await database.get(GenerationRun, run_ids[0])
            entitlement = await database.scalar(
                select(TrialEntitlement).where(TrialEntitlement.user_id == 10)
            )
            model_entries = await database.scalar(
                select(func.count()).select_from(UsageLedger).where(
                    UsageLedger.model_call_id.is_not(None)
                )
            )
            assert run.trial_settlement == "consumed"
            assert run.trial_settled_at is not None
            assert entitlement.state == "consumed"
            assert model_entries == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_recovery_compensates_structured_provider_failure_before_artifact(tmp_path) -> None:
    engine, factory, run_ids = await _settlement_database(tmp_path)
    try:
        await _reserve(factory, run_ids[1])
        async with factory() as database, database.begin():
            run = await database.get(GenerationRun, run_ids[1])
            run.state = "failed"
            run.failure_category = TrialFailureKind.PROVIDER.value
            run.error_message = "public wording may change and is never parsed"

        outcomes = await TrialSettlementReconciler(factory).reconcile()
        assert outcomes == {run_ids[1]: "compensated"}
        async with factory() as database:
            entitlement = await database.scalar(
                select(TrialEntitlement).where(TrialEntitlement.user_id == 10)
            )
            run = await database.get(GenerationRun, run_ids[1])
            assert entitlement.state == "available"
            assert run.trial_settlement == "compensated"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_user_cancellation_after_model_spend_consumes_trial(tmp_path) -> None:
    engine, factory, run_ids = await _settlement_database(tmp_path)
    try:
        await _reserve(factory, run_ids[2])
        async with factory() as database, database.begin():
            run = await database.get(GenerationRun, run_ids[2])
            run.state = "cancelled"
            database.add(ModelCall(
                run_id=run.id,
                provider="gemini",
                model="gemini-builder",
                role="widget_generator",
                mode="express",
                prompt_version="builder-v1",
                input_tokens=1,
                output_tokens=0,
                thinking_tokens=0,
                latency_ms=10,
                status="failed",
                cost_microusd=0,
            ))

        outcome = await TrialSettlementReconciler(factory).settle_run(run_ids[2])
        assert outcome == "consumed"
        async with factory() as database:
            run = await database.get(GenerationRun, run_ids[2])
            assert run.trial_settlement == "consumed"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_provider_dispatch_survives_worker_cancellation_and_consumes_trial(
    tmp_path,
) -> None:
    engine, factory, run_ids = await _settlement_database(tmp_path)
    dispatched = asyncio.Event()

    class BlockingProvider:
        capabilities = ProviderCapabilities(structured_output=True)

        async def generate(self, request: ModelRequest, *, model: str):
            assert request.prompt == "dispatch before blocking"
            assert model == "blocking-model"
            dispatched.set()
            await asyncio.Future()
            raise AssertionError("unreachable")

    try:
        await _reserve(factory, run_ids[2])
        async with factory() as database, database.begin():
            (await database.get(GenerationRun, run_ids[0])).state = "completed"
            (await database.get(GenerationRun, run_ids[1])).state = "completed"
            run = await database.get(GenerationRun, run_ids[2])
            database.add(GenerationEvent(
                id=701,
                run_id=run.id,
                sequence=1,
                event_type="run.created",
                public_message="Queued",
                payload={"request": {"engine": "direct", "brief": "Build"}},
            ))
            run.next_event_sequence = 2

        router = ModelRouter(
            providers={"blocking": BlockingProvider()},
            policies={
                ("widget_generator", "express"): ModelPolicy(
                    prompt_version="cancel-v1",
                    targets=(ProviderTarget("blocking", "blocking-model", 1, 1),),
                )
            },
            audit=SqlModelCallAudit(factory),
        )

        async def handle(claim):
            await router.generate(
                role="widget_generator",
                mode="express",
                request=ModelRequest(prompt="dispatch before blocking"),
                run_id=claim.run_id,
            )
            return StageResult(public_message="unreachable")

        reconciler = TrialSettlementReconciler(factory)
        queue = PostgresWorkerQueue(factory, lease_seconds=30)
        worker = BuilderWorker(
            queue=queue,
            worker_id="cancel-accounting-worker",
            stage_handler=handle,
            heartbeat_interval=0.01,
            terminal_hook=reconciler.settle_run,
        )
        worker_task = asyncio.create_task(worker.run_once())
        await asyncio.wait_for(dispatched.wait(), timeout=2)
        assert await queue.request_cancel(run_ids[2])
        assert await asyncio.wait_for(worker_task, timeout=2)
        assert await reconciler.settle_run(run_ids[2]) == "consumed"

        async with factory() as database:
            run = await database.get(GenerationRun, run_ids[2])
            calls = list((await database.execute(
                select(ModelCall).where(ModelCall.run_id == run_ids[2])
            )).scalars())
            trial_debits = await database.scalar(
                select(func.count()).select_from(UsageLedger).where(
                    UsageLedger.run_id == run_ids[2],
                    UsageLedger.entry_type == "trial.debit",
                )
            )
            assert run.state == "cancelled"
            assert run.trial_settlement == "consumed"
            assert len(calls) == 1
            assert calls[0].provider_dispatched is True
            assert calls[0].status == "cancelled"
            assert trial_debits == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_terminal_hook_uses_structured_failure_and_recovers_settlement(tmp_path) -> None:
    engine, factory, run_ids = await _settlement_database(tmp_path)
    try:
        await _reserve(factory, run_ids[1])
        async with factory() as database, database.begin():
            (await database.get(GenerationRun, run_ids[0])).state = "completed"
            (await database.get(GenerationRun, run_ids[2])).state = "completed"
            run = await database.get(GenerationRun, run_ids[1])
            run.stage_retry_count = MAX_STAGE_EXECUTIONS - 1
            project = await database.get(Project, run.project_id)
            project.active_run_id = run.id
            database.add(GenerationEvent(
                id=501,
                run_id=run.id,
                sequence=1,
                event_type="run.created",
                public_message="Queued",
                payload={"request": {"engine": "direct", "brief": "Build", "source_url": "https://example.com/"}},
            ))
            run.next_event_sequence = 2

        async def fail_provider(_claim):
            raise BuilderEngineError("invalid_artifact", "Model output is invalid")

        reconciler = TrialSettlementReconciler(factory)
        worker = BuilderWorker(
            queue=PostgresWorkerQueue(factory, lease_seconds=30),
            worker_id="settlement-worker",
            stage_handler=fail_provider,
            heartbeat_interval=1,
            terminal_hook=reconciler.settle_run,
            terminal_reconciler=reconciler.reconcile,
        )
        assert await worker.run_once()

        async with factory() as database:
            run = await database.get(GenerationRun, run_ids[1])
            assert run.state == "failed"
            assert run.failure_category == TrialFailureKind.MODEL_INVALID_OUTPUT.value
            assert run.trial_settlement == "compensated"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_corrupt_terminal_settlement_does_not_block_later_recovery_or_claim(tmp_path) -> None:
    engine, factory, run_ids = await _settlement_database(tmp_path)
    try:
        await _reserve(factory, run_ids[1])
        async with factory() as database, database.begin():
            corrupt = await database.get(GenerationRun, run_ids[0])
            corrupt.state = "completed"
            good = await database.get(GenerationRun, run_ids[1])
            good.state = "failed"
            good.failure_category = TrialFailureKind.PROVIDER.value
            queued = await database.get(GenerationRun, run_ids[2])
            queued.stage_retry_count = MAX_STAGE_EXECUTIONS - 1
            database.add_all([
                UsageLedger(
                    user_id=10,
                    run_id=corrupt.id,
                    bucket="trial_available",
                    entry_type="trial.reserve",
                    amount=-1,
                    idempotency_key="corrupt-reservation",
                    payload={"transition_key": "corrupt"},
                ),
                GenerationEvent(
                    id=601,
                    run_id=queued.id,
                    sequence=1,
                    event_type="run.created",
                    public_message="Queued",
                    payload={"request": {"engine": "direct", "brief": "Build", "source_url": "https://example.com/"}},
                ),
            ])
            queued.next_event_sequence = 2

        handled: list[UUID] = []

        async def finish(_claim):
            handled.append(_claim.run_id)
            raise BuilderEngineError("invalid_artifact", "Model output is invalid")

        reconciler = TrialSettlementReconciler(factory)
        worker = BuilderWorker(
            queue=PostgresWorkerQueue(factory, lease_seconds=30),
            worker_id="isolated-reconcile-worker",
            stage_handler=finish,
            heartbeat_interval=1,
            terminal_hook=reconciler.settle_run,
            terminal_reconciler=reconciler.reconcile,
        )
        assert await worker.run_once()
        assert handled == [run_ids[2]]
        async with factory() as database:
            good = await database.get(GenerationRun, run_ids[1])
            assert good.trial_settlement == "compensated"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_more_than_one_page_of_non_trial_terminal_runs_cannot_starve_trial(
    tmp_path,
) -> None:
    engine, factory, run_ids = await _settlement_database(tmp_path)
    try:
        await _reserve(factory, run_ids[1])
        async with factory() as database, database.begin():
            valid = await database.get(GenerationRun, run_ids[1])
            valid.state = "failed"
            valid.failure_category = TrialFailureKind.PROVIDER.value
            valid.created_at = datetime.now(UTC)
            project_id = valid.project_id
            database.add_all([
                GenerationRun(
                    project_id=project_id,
                    mode="express",
                    state="completed",
                    idempotency_key=f"historical-non-trial-{index}",
                    created_at=datetime.now(UTC) - timedelta(days=1, seconds=index),
                )
                for index in range(105)
            ])

        outcomes = await TrialSettlementReconciler(factory).reconcile(limit=100)

        assert outcomes == {run_ids[1]: "compensated"}
        async with factory() as database:
            run = await database.get(GenerationRun, run_ids[1])
            assert run.trial_settlement == "compensated"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_mismatched_trial_reservations_are_quarantined_without_starvation(
    tmp_path,
) -> None:
    engine, factory, run_ids = await _settlement_database(tmp_path)
    try:
        await _reserve(factory, run_ids[1])
        async with factory() as database, database.begin():
            valid = await database.get(GenerationRun, run_ids[1])
            valid.state = "failed"
            valid.failure_category = TrialFailureKind.PROVIDER.value
            valid.created_at = datetime.now(UTC)
            poison_runs = [
                GenerationRun(
                    project_id=valid.project_id,
                    mode="express",
                    state="completed",
                    idempotency_key=f"mismatched-trial-{index}",
                    created_at=datetime.now(UTC) - timedelta(days=1, seconds=index),
                )
                for index in range(105)
            ]
            database.add_all(poison_runs)
            await database.flush()
            poison_ids = [run.id for run in poison_runs]
            database.add_all([
                UsageLedger(
                    user_id=10,
                    run_id=run.id,
                    bucket="trial_available",
                    entry_type="trial.reserve",
                    amount=-1,
                    idempotency_key=f"mismatched-reservation-{index}",
                    payload={
                        "transition_key": (
                            f"trial:10:{run.id.hex}:mismatch:{index}:reserve"
                        )
                    },
                )
                for index, run in enumerate(poison_runs)
            ])

        reconciler = TrialSettlementReconciler(factory)
        first_page = await reconciler.reconcile(limit=100)
        second_page = await reconciler.reconcile(limit=100)

        assert len(first_page) == 100
        assert set(first_page.values()) == {"quarantined"}
        assert second_page[run_ids[1]] == "compensated"
        assert list(second_page.values()).count("quarantined") == 5
        async with factory() as database:
            quarantined = await database.scalar(
                select(func.count()).select_from(GenerationRun).where(
                    GenerationRun.id.in_(poison_ids),
                    GenerationRun.trial_settlement == "quarantined",
                    GenerationRun.trial_settled_at.is_not(None),
                )
            )
            valid = await database.get(GenerationRun, run_ids[1])
            assert quarantined == 105
            assert valid.trial_settlement == "compensated"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_transient_settlement_failure_remains_unsettled_for_retry(
    tmp_path,
    monkeypatch,
) -> None:
    engine, factory, run_ids = await _settlement_database(tmp_path)
    try:
        await _reserve(factory, run_ids[0])
        async with factory() as database, database.begin():
            run = await database.get(GenerationRun, run_ids[0])
            run.state = "completed"

        reconciler = TrialSettlementReconciler(factory)

        async def unavailable_database(*_args, **_kwargs):
            raise ConnectionError("temporary database outage")

        monkeypatch.setattr(
            reconciler._trials,
            "consume_trial",
            unavailable_database,
        )

        assert await reconciler.reconcile() == {}
        async with factory() as database:
            run = await database.get(GenerationRun, run_ids[0])
            assert run.trial_settlement is None
            assert run.trial_settled_at is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_missing_entitlement_and_failed_reservation_mismatch_are_quarantined(
    tmp_path,
) -> None:
    engine, factory, run_ids = await _settlement_database(tmp_path)
    try:
        await _reserve(factory, run_ids[1])
        async with factory() as database, database.begin():
            database.add(User(
                id=11,
                tenant_id=1,
                email="missing-entitlement@example.com",
            ))
            failed = await database.get(GenerationRun, run_ids[0])
            failed.state = "failed"
            failed.failure_category = TrialFailureKind.PROVIDER.value
            missing = await database.get(GenerationRun, run_ids[2])
            missing.state = "completed"
            database.add_all([
                UsageLedger(
                    user_id=10,
                    run_id=failed.id,
                    bucket="trial_available",
                    entry_type="trial.reserve",
                    amount=-1,
                    idempotency_key="failed-mismatch-reservation",
                    payload={
                        "transition_key": (
                            f"trial:10:{failed.id.hex}:mismatch:reserve"
                        )
                    },
                ),
                UsageLedger(
                    user_id=11,
                    run_id=missing.id,
                    bucket="trial_available",
                    entry_type="trial.reserve",
                    amount=-1,
                    idempotency_key="missing-entitlement-reservation",
                    payload={
                        "transition_key": (
                            f"trial:11:{missing.id.hex}:missing:reserve"
                        )
                    },
                ),
            ])

        reconciler = TrialSettlementReconciler(factory)

        assert await reconciler.settle_run(run_ids[0]) == "quarantined"
        assert await reconciler.settle_run(run_ids[2]) == "quarantined"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URL, reason="KAIGO_TEST_POSTGRES_URL is not configured")
async def test_postgres_terminal_settlement_is_atomic_and_recovers_missing_marker() -> None:
    engine, factory, run_ids = await _settlement_database(None, database_url=POSTGRES_URL)
    try:
        await _reserve(factory, run_ids[0])
        async with factory() as database, database.begin():
            run = await database.get(GenerationRun, run_ids[0])
            run.state = "completed"
            database.add(ModelCall(
                run_id=run.id,
                provider="gemini",
                model="gemini-builder",
                role="widget_generator",
                mode="express",
                prompt_version="builder-v1",
                input_tokens=5,
                output_tokens=3,
                thinking_tokens=0,
                latency_ms=20,
                status="completed",
                cost_microusd=10,
            ))

        reconciler = TrialSettlementReconciler(factory)
        outcomes = await asyncio.gather(
            reconciler.settle_run(run_ids[0]),
            reconciler.settle_run(run_ids[0]),
        )
        assert outcomes == ["consumed", "consumed"]

        async with factory() as database, database.begin():
            run = await database.get(GenerationRun, run_ids[0])
            run.trial_settlement = None
            run.trial_settled_at = None
        assert await reconciler.reconcile() == {run_ids[0]: "consumed"}

        async with factory() as database:
            assert await database.scalar(
                select(func.count()).select_from(UsageLedger).where(
                    UsageLedger.model_call_id.is_not(None)
                )
            ) == 2
            assert await database.scalar(
                select(func.count()).select_from(UsageLedger).where(
                    UsageLedger.entry_type == "trial.debit"
                )
            ) == 2
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()
