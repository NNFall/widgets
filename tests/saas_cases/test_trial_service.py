from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.billing.service import (
    TrialCompensationDenied,
    TrialFailureKind,
    TrialService,
    TrialUnavailable,
    UnverifiedTrialUser,
)
from app.db.base import Base
from app.db.models import Tenant, User
from app.saas.models import (
    GenerationArtifact,
    GenerationEvent,
    GenerationRun,
    ModelCall,
    Project,
    TrialEntitlement,
    UsageLedger,
    UserIdentity,
)
from builder_lab.models import Stage
from tests.builder_lab_cases.test_validation import artifact

POSTGRES_URL = os.getenv("KAIGO_TEST_POSTGRES_URL")


async def _database(tmp_path, *, verified: bool = True):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'trial.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as database, database.begin():
        database.add(Tenant(id=1, name="Alpha", slug="alpha"))
        database.add(User(id=10, tenant_id=1, email="owner@example.com"))
        database.add(
            UserIdentity(
                user_id=10,
                provider="google",
                provider_subject="subject-10",
                email="owner@example.com",
                email_verified=verified,
            )
        )
        project = Project(
            tenant_id=1,
            owner_user_id=10,
            source_url="https://example.com/",
            brief="Express trial",
        )
        database.add(project)
        await database.flush()
        runs = []
        for suffix in ("a", "b"):
            run = GenerationRun(
                project_id=project.id,
                mode="express",
                state="queued",
                progress=0,
                next_event_sequence=1,
                idempotency_key=f"trial-{suffix}",
            )
            database.add(run)
            runs.append(run)
        await database.flush()
        run_ids = tuple(run.id for run in runs)
    return engine, factory, project.id, run_ids


@pytest.mark.asyncio
async def test_parallel_requests_reserve_only_one_complete_trial(tmp_path) -> None:
    engine, factory, _, run_ids = await _database(tmp_path)
    service = TrialService(factory)
    try:
        results = await asyncio.gather(
            service.reserve_trial(10, run_ids[0], request_id="request-a"),
            service.reserve_trial(10, run_ids[1], request_id="request-b"),
            return_exceptions=True,
        )

        assert sum(not isinstance(result, Exception) for result in results) == 1
        assert sum(isinstance(result, TrialUnavailable) for result in results) == 1
        async with factory() as database:
            entries = (
                await database.execute(
                    select(UsageLedger).where(
                        UsageLedger.user_id == 10,
                        UsageLedger.entry_type == "trial.reserve",
                    )
                )
            ).scalars().all()
            entitlement = (
                await database.execute(
                    select(TrialEntitlement).where(TrialEntitlement.user_id == 10)
                )
            ).scalar_one()
        assert len(entries) == 2
        assert {(entry.bucket, entry.amount) for entry in entries} == {
            ("trial_available", -1),
            ("trial_reserved", 1),
        }
        assert entitlement.state == "reserved"
        assert entitlement.reserved_units == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_same_run_and_request_are_idempotent(tmp_path) -> None:
    engine, factory, _, run_ids = await _database(tmp_path)
    service = TrialService(factory)
    try:
        first = await service.reserve_trial(10, run_ids[0], request_id="same")
        second = await service.reserve_trial(10, run_ids[0], request_id="same")
        assert second == first

        await service.consume_trial(first, reason="completed")
        await service.consume_trial(first, reason="completed")

        async with factory() as database:
            entries = (
                await database.execute(
                    select(UsageLedger)
                    .where(UsageLedger.user_id == 10)
                    .order_by(UsageLedger.created_at, UsageLedger.id)
                )
            ).scalars().all()
        assert sorted(entry.entry_type for entry in entries) == [
            "trial.debit",
            "trial.debit",
            "trial.reserve",
            "trial.reserve",
        ]
        totals = {}
        for entry in entries:
            totals[entry.bucket] = totals.get(entry.bucket, 0) + entry.amount
        assert totals == {
            "trial_available": -1,
            "trial_reserved": 0,
            "trial_consumed": 1,
        }
        assert not await service.can_start_trial(10)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unverified_user_cannot_reserve_trial(tmp_path) -> None:
    engine, factory, _, run_ids = await _database(tmp_path, verified=False)
    try:
        with pytest.raises(UnverifiedTrialUser):
            await TrialService(factory).reserve_trial(10, run_ids[0])
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_infrastructure_failure_before_any_artifact_compensates(tmp_path) -> None:
    engine, factory, _, run_ids = await _database(tmp_path)
    service = TrialService(factory)
    try:
        reservation = await service.reserve_trial(10, run_ids[0])
        assert await service.compensate_if_eligible(
            reservation,
            failure_kind=TrialFailureKind.INFRASTRUCTURE,
        )
        assert await service.compensate_if_eligible(
            reservation,
            failure_kind=TrialFailureKind.INFRASTRUCTURE,
        )
        assert await service.can_start_trial(10)

        async with factory() as database:
            entries = (
                await database.execute(
                    select(UsageLedger)
                    .where(UsageLedger.user_id == 10)
                    .order_by(UsageLedger.created_at, UsageLedger.id)
                )
            ).scalars().all()
        assert sorted(entry.entry_type for entry in entries) == [
            "trial.compensation",
            "trial.compensation",
            "trial.reserve",
            "trial.reserve",
        ]
        totals = {}
        for entry in entries:
            totals[entry.bucket] = totals.get(entry.bucket, 0) + entry.amount
        assert totals == {
            "trial_available": 0,
            "trial_reserved": 0,
        }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_kind",
    [TrialFailureKind.USER, TrialFailureKind.CONTENT, TrialFailureKind.VALIDATION],
)
async def test_user_content_and_validation_failures_consume(
    tmp_path,
    failure_kind: TrialFailureKind,
) -> None:
    engine, factory, _, run_ids = await _database(tmp_path)
    service = TrialService(factory)
    try:
        reservation = await service.reserve_trial(10, run_ids[0])
        with pytest.raises(TrialCompensationDenied):
            await service.compensate_if_eligible(
                reservation,
                failure_kind=failure_kind,
            )
        await service.consume_trial(reservation, reason=failure_kind.value)
        assert not await service.can_start_trial(10)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_settle_failure_consumes_policy_failure(tmp_path) -> None:
    engine, factory, _, run_ids = await _database(tmp_path)
    service = TrialService(factory)
    try:
        reservation = await service.reserve_trial(10, run_ids[0])

        outcome = await service.settle_failure(
            reservation,
            failure_kind=TrialFailureKind.CONTENT,
        )

        assert outcome == "consumed"
        assert not await service.can_start_trial(10)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_settle_failure_consumes_infrastructure_failure_with_draft(
    tmp_path,
) -> None:
    engine, factory, _, run_ids = await _database(tmp_path)
    service = TrialService(factory)
    try:
        reservation = await service.reserve_trial(10, run_ids[0])
        async with factory() as database, database.begin():
            run = await database.get(GenerationRun, run_ids[0])
            assert run is not None
            database.add(
                GenerationEvent(
                    id=101,
                    run_id=run.id,
                    sequence=run.next_event_sequence,
                    event_type="artifact.draft_staged",
                    public_message="Черновик сохранён",
                    payload={
                        "artifact": artifact(
                            revision=1,
                            stage=Stage.FOUNDATION,
                        ).to_dict()
                    },
                )
            )
            run.next_event_sequence += 1

        outcome = await service.settle_failure(
            reservation,
            failure_kind=TrialFailureKind.INFRASTRUCTURE,
        )

        assert outcome == "consumed"
        assert not await service.can_start_trial(10)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_restorable_draft_blocks_compensation(tmp_path) -> None:
    engine, factory, _, run_ids = await _database(tmp_path)
    service = TrialService(factory)
    try:
        reservation = await service.reserve_trial(10, run_ids[0])
        async with factory() as database, database.begin():
            run = await database.get(GenerationRun, run_ids[0])
            assert run is not None
            database.add(
                GenerationEvent(
                    id=100,
                    run_id=run.id,
                    sequence=run.next_event_sequence,
                    event_type="artifact.draft_staged",
                    public_message="Черновик сохранён",
                    payload={
                        "artifact": artifact(
                            revision=1,
                            stage=Stage.FOUNDATION,
                        ).to_dict()
                    },
                )
            )
            run.next_event_sequence += 1

        with pytest.raises(TrialCompensationDenied, match="полезный результат"):
            await service.compensate_if_eligible(
                reservation,
                failure_kind=TrialFailureKind.INFRASTRUCTURE,
            )
        assert not await service.can_start_trial(10)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_malformed_draft_does_not_block_infrastructure_compensation(
    tmp_path,
) -> None:
    engine, factory, _, run_ids = await _database(tmp_path)
    service = TrialService(factory)
    try:
        reservation = await service.reserve_trial(10, run_ids[0])
        async with factory() as database, database.begin():
            run = await database.get(GenerationRun, run_ids[0])
            assert run is not None
            database.add(
                GenerationEvent(
                    id=102,
                    run_id=run.id,
                    sequence=run.next_event_sequence,
                    event_type="artifact.draft_staged",
                    public_message="Повреждённый черновик",
                    payload={"artifact": {"invalid": True}},
                )
            )
            run.next_event_sequence += 1

        assert await service.compensate_if_eligible(
            reservation,
            failure_kind=TrialFailureKind.INFRASTRUCTURE,
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_accepted_artifact_blocks_compensation(tmp_path) -> None:
    engine, factory, _, run_ids = await _database(tmp_path)
    service = TrialService(factory)
    try:
        reservation = await service.reserve_trial(10, run_ids[0])
        async with factory() as database, database.begin():
            database.add(
                GenerationArtifact(
                    run_id=run_ids[0],
                    revision=1,
                    stage="foundation",
                    html="<main></main>",
                    css="body{}",
                    javascript="",
                    quality_status="accepted",
                )
            )

        with pytest.raises(TrialCompensationDenied, match="полезный результат"):
            await service.compensate_if_eligible(
                reservation,
                failure_kind=TrialFailureKind.PROVIDER,
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_model_usage_ledger_uses_actual_cost_without_double_counting_thinking(
    tmp_path,
) -> None:
    engine, factory, _, run_ids = await _database(tmp_path)
    service = TrialService(factory)
    try:
        async with factory() as database, database.begin():
            call = ModelCall(
                run_id=run_ids[0],
                provider="agentrouter",
                model="gpt-5.5",
                role="widget_generator",
                prompt_version="widget-v1",
                request_id="provider-request",
                attempt=1,
                input_tokens=100,
                output_tokens=40,
                thinking_tokens=12,
                latency_ms=1000,
                status="completed",
                cost_microusd=980,
                pricing_snapshot={"input": 7, "output": 7},
            )
            database.add(call)
            await database.flush()
            call_id = call.id

        first = await service.record_model_call_usage(10, call_id)
        second = await service.record_model_call_usage(10, call_id)
        assert [entry.id for entry in second] == [entry.id for entry in first]
        assert [(entry.bucket, entry.amount) for entry in first] == [
            ("tokens", -140),
            ("cost_microusd", -980),
        ]
        assert all(entry.payload["billable_tokens"] == 140 for entry in first)
        assert all(entry.payload["thinking_tokens"] == 12 for entry in first)
        assert all(entry.payload["cost_microusd"] == 980 for entry in first)

        async with factory() as database:
            records = (
                await database.execute(
                    select(UsageLedger).where(
                        UsageLedger.idempotency_key.like(f"model-call:{call_id}:%")
                    )
                )
            ).scalars().all()
        assert len(records) == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_zero_cost_model_usage_falls_back_to_billable_token_amount(tmp_path) -> None:
    engine, factory, _, run_ids = await _database(tmp_path)
    service = TrialService(factory)
    try:
        async with factory() as database, database.begin():
            call = ModelCall(
                run_id=run_ids[0],
                provider="grant",
                model="free-model",
                role="visual_critic",
                prompt_version="visual-v1",
                attempt=1,
                input_tokens=11,
                output_tokens=7,
                thinking_tokens=5,
                latency_ms=50,
                status="completed",
                cost_microusd=0,
            )
            database.add(call)
            await database.flush()
            call_id = call.id

        entries = await service.record_model_call_usage(10, call_id)
        assert len(entries) == 1
        assert entries[0].bucket == "tokens"
        assert entries[0].amount == -18
        assert entries[0].payload["billable_tokens"] == 18
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_parallel_model_usage_recording_is_idempotent(tmp_path) -> None:
    engine, factory, _, run_ids = await _database(tmp_path)
    service = TrialService(factory)
    try:
        async with factory() as database, database.begin():
            call = ModelCall(
                run_id=run_ids[0],
                provider="agentrouter",
                model="glm-5.2",
                role="widget_generator",
                prompt_version="widget-v1",
                attempt=1,
                input_tokens=20,
                output_tokens=10,
                thinking_tokens=4,
                latency_ms=200,
                status="completed",
                cost_microusd=180,
            )
            database.add(call)
            await database.flush()
            call_id = call.id

        results = await asyncio.gather(
            service.record_model_call_usage(10, call_id),
            service.record_model_call_usage(10, call_id),
            return_exceptions=True,
        )

        assert all(not isinstance(result, Exception) for result in results)
        async with factory() as database:
            entries = (
                await database.execute(
                    select(UsageLedger).where(
                        UsageLedger.idempotency_key.like(f"model-call:{call_id}:%")
                    )
                )
            ).scalars().all()
        assert len(entries) == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(not POSTGRES_URL, reason="KAIGO_TEST_POSTGRES_URL is not configured")
async def test_postgres_concurrent_trial_reservation_is_atomic() -> None:
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
            database.add(
                UserIdentity(
                    user_id=10,
                    provider="google",
                    provider_subject="postgres-subject",
                    email="owner@example.com",
                    email_verified=True,
                )
            )
            await database.flush()
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
                    progress=0,
                    next_event_sequence=1,
                    idempotency_key=f"postgres-trial-{index}",
                )
                for index in range(2)
            ]
            database.add_all(runs)
            await database.flush()
            run_ids = tuple(run.id for run in runs)

        first_service = TrialService(factory)
        second_service = TrialService(factory)
        results = await asyncio.gather(
            first_service.reserve_trial(10, run_ids[0], request_id="parallel-a"),
            second_service.reserve_trial(10, run_ids[1], request_id="parallel-b"),
            return_exceptions=True,
        )

        assert sum(not isinstance(result, Exception) for result in results) == 1
        assert sum(isinstance(result, TrialUnavailable) for result in results) == 1
        async with factory() as database:
            entries = (
                await database.execute(
                    select(UsageLedger).where(
                        UsageLedger.entry_type == "trial.reserve"
                    )
                )
            ).scalars().all()
        assert len(entries) == 2
        assert sum(entry.amount for entry in entries) == 0
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()
