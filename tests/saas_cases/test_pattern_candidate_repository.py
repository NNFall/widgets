from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Tenant, User
from app.patterns.candidate_repository import PatternCandidateRepository
from app.saas.models import (
    GenerationRun,
    ModelCall,
    PatternCandidateGroupRecord,
    PatternCandidateItemRecord,
    PatternCandidatePlanRecord,
    PatternReview,
    PatternStageExposure,
    PatternStageUsageClaim,
    Project,
    WidgetPatternVersion,
)
from builder_lab.models import Stage
from builder_lab.patterns.atomic_models import (
    AtomicPatternStatus,
    PatternCandidate,
    PatternCandidateGroup,
    PatternCandidatePlan,
)
from builder_lab.patterns.atomic_registry import load_builtin_atomic_registry
from builder_lab.patterns.candidate_resolver import STAGE_PATTERN_CATEGORIES


def complete_candidate_plan() -> PatternCandidatePlan:
    registry = load_builtin_atomic_registry()
    grouped = {
        definition.category: definition
        for definition in registry.definitions
    }
    groups = tuple(
        PatternCandidateGroup(
            category=category,
            candidates=(
                PatternCandidate(
                    pattern_id=grouped[category].pattern_id,
                    version=grouped[category].version,
                    rank=1,
                    reason="Технический кандидат",
                ),
            ),
        )
        for category in sorted(grouped, key=lambda item: item.value)
    )
    return PatternCandidatePlan(
        schema_version=2,
        direction_id="candidate-2",
        groups=groups,
        summary="План атомарных кандидатов",
    )


async def database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session, session.begin():
        session.add(Tenant(id=1, name="Alpha", slug="alpha"))
        await session.flush()
        session.add(User(id=10, tenant_id=1, email="owner@example.com"))
        await session.flush()
        project = Project(
            tenant_id=1,
            owner_user_id=10,
            source_url="https://example.com/",
        )
        session.add(project)
        await session.flush()
        run = GenerationRun(
            project_id=project.id,
            mode="direct",
            state="running",
            idempotency_key=str(uuid4()),
        )
        session.add(run)
        await session.flush()
        run_id = run.id
    return engine, factory, run_id


async def second_run(factory, run_id):
    async with factory() as session, session.begin():
        project_id = await session.scalar(
            select(GenerationRun.project_id).where(GenerationRun.id == run_id)
        )
        run = GenerationRun(
            project_id=project_id,
            mode="direct",
            state="running",
            idempotency_key=str(uuid4()),
        )
        session.add(run)
        await session.flush()
        return run.id


async def model_call(factory, run_id):
    async with factory() as session, session.begin():
        call = ModelCall(
            id=uuid4(),
            run_id=run_id,
            provider="test",
            model="test-model",
            role="builder",
            mode="direct",
            prompt_version="test-v1",
            status="completed",
            cost_state="not_billed",
            cost_microusd=0,
            provider_dispatched=False,
            input_tokens=0,
            output_tokens=0,
            thinking_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
            latency_ms=0,
            pricing_snapshot={},
        )
        session.add(call)
        await session.flush()
        return call.id


@pytest.mark.asyncio
async def test_candidate_plan_round_trips_without_live_registry() -> None:
    engine, factory, run_id = await database()
    plan = complete_candidate_plan()
    try:
        async with factory() as session, session.begin():
            repository = PatternCandidateRepository(session)
            persisted = await repository.create_plan(
                run_id=run_id,
                plan=plan,
                registry=load_builtin_atomic_registry(),
            )
            persisted_id = persisted.id

        async with factory() as session:
            loaded = await PatternCandidateRepository(session).load_plan(run_id)

        assert loaded is not None
        assert loaded.id == persisted_id
        assert loaded.plan == plan
        assert len(loaded.items) == len(plan.groups)
        assert all(item.implementation_sha256 for item in loaded.items)
        assert set(loaded.stage_values) == {stage.value for stage in STAGE_PATTERN_CATEGORIES}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_candidate_plan_is_idempotent_and_rejects_conflicting_replay() -> None:
    engine, factory, run_id = await database()
    plan = complete_candidate_plan()
    try:
        async with factory() as session, session.begin():
            repository = PatternCandidateRepository(session)
            first = await repository.create_plan(
                run_id=run_id,
                plan=plan,
                registry=load_builtin_atomic_registry(),
            )
            second = await repository.create_plan(
                run_id=run_id,
                plan=plan,
                registry=load_builtin_atomic_registry(),
            )
            assert first.id == second.id
            conflicting = PatternCandidatePlan(
                schema_version=2,
                direction_id=plan.direction_id,
                groups=plan.groups,
                summary="Другой план",
            )
            with pytest.raises(ValueError, match="conflicting candidate plan"):
                await repository.create_plan(
                    run_id=run_id,
                    plan=conflicting,
                    registry=load_builtin_atomic_registry(),
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_exposure_and_usage_claim_require_selected_same_run_item() -> None:
    engine, factory, run_id = await database()
    plan = complete_candidate_plan()
    try:
        async with factory() as session, session.begin():
            repository = PatternCandidateRepository(session)
            await repository.create_plan(
                run_id=run_id,
                plan=plan,
                registry=load_builtin_atomic_registry(),
            )
            loaded = await repository.load_plan(run_id)
            assert loaded is not None
            item = next(
                item
                for item in loaded.items
                if item.category.value == "assistant_message_enter"
            )
            exposure = await repository.record_exposure(
                run_id=run_id,
                stage=Stage.CONVERSATION,
                candidate_item_id=item.id,
            )
            repeated = await repository.record_exposure(
                run_id=run_id,
                stage=Stage.CONVERSATION,
                candidate_item_id=item.id,
            )
            assert repeated.id == exposure.id
            claim = await repository.record_usage_claim(
                run_id=run_id,
                stage=Stage.CONVERSATION,
                candidate_item_id=item.id,
                usage_mode="primary",
            )
            assert claim.exposure_id == exposure.id
            with pytest.raises(ValueError, match="not exposed"):
                await repository.record_usage_claim(
                    run_id=run_id,
                    stage=Stage.IDENTITY,
                    candidate_item_id=item.id,
                    usage_mode="combined",
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sync_registry_updates_lifecycle_but_rejects_full_snapshot_or_hash_drift() -> None:
    engine, factory, run_id = await database()
    registry = load_builtin_atomic_registry()
    definition = registry.definitions[0]
    try:
        async with factory() as session, session.begin():
            repository = PatternCandidateRepository(session)
            await repository.sync_registry(registry)
            persisted = await session.scalar(
                select(WidgetPatternVersion).where(
                    WidgetPatternVersion.pattern_id == definition.pattern_id,
                    WidgetPatternVersion.version == definition.version,
                )
            )
            assert persisted is not None
            assert persisted.description == definition.summary
            assert "html" not in persisted.manifest_snapshot

            lifecycle = replace(definition, status=AtomicPatternStatus.DEPRECATED)
            await repository.sync_registry(
                type(registry)(
                    tuple(lifecycle if item is definition else item for item in registry.definitions)
                )
            )
            await session.refresh(persisted)
            assert persisted.status == "deprecated"

            changed_summary = replace(definition, summary="Changed pattern summary")
            with pytest.raises(ValueError, match="drift"):
                await repository.sync_registry(
                    type(registry)(
                        tuple(changed_summary if item is definition else item for item in registry.definitions)
                    )
                )
            changed_hash = replace(definition, implementation_sha256="f" * 64)
            with pytest.raises(ValueError, match="drift|hash"):
                await repository.sync_registry(
                    type(registry)(
                        tuple(changed_hash if item is definition else item for item in registry.definitions)
                    )
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_effective_review_uses_latest_override_and_imported_fallback() -> None:
    engine, factory, run_id = await database()
    registry = load_builtin_atomic_registry()
    definition = registry.definitions[0]
    try:
        async with factory() as session, session.begin():
            repository = PatternCandidateRepository(session)
            await repository.sync_registry(registry)
            persisted = await session.scalar(
                select(WidgetPatternVersion).where(
                    WidgetPatternVersion.pattern_id == definition.pattern_id,
                    WidgetPatternVersion.version == definition.version,
                )
            )
            assert persisted is not None
            assert await repository.effective_review_state(persisted.id) == "approved"
            first = await repository.append_review(
                persisted.id,
                "reviewer@example.com",
                "rejected",
                "Не готово",
            )
            first.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
            await session.flush()
            second = await repository.append_review(
                persisted.id,
                "reviewer@example.com",
                "approved",
                "Проверено",
            )
            second.created_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
            await session.flush()
            assert await repository.effective_review_state(persisted.id) == "approved"

            ready = replace(
                definition,
                pattern_id="review-fallback-technical",
                provenance={"origin": "kaigo-owned", "review_state": "ready_for_review"},
            )
            ready_registry = type(registry)(
                tuple(ready if item is definition else item for item in registry.definitions)
            )
            await repository.sync_registry(ready_registry)
            ready_row = await session.scalar(
                select(WidgetPatternVersion).where(
                    WidgetPatternVersion.pattern_id == ready.pattern_id,
                    WidgetPatternVersion.version == ready.version,
                )
            )
            assert ready_row is not None
            assert await repository.effective_review_state(ready_row.id) == "ready_for_review"

            with pytest.raises(ValueError, match="email"):
                await repository.append_review(ready_row.id, "bad", "approved")
            with pytest.raises(ValueError, match="status"):
                await repository.append_review(ready_row.id, "reviewer@example.com", "ready_for_review")
            with pytest.raises(ValueError, match="comment"):
                await repository.append_review(ready_row.id, "reviewer@example.com", "approved", "x" * 4001)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_exposure_rejects_cross_run_item_wrong_stage_and_model_call() -> None:
    engine, factory, run_id = await database()
    registry = load_builtin_atomic_registry()
    plan = complete_candidate_plan()
    run_b = await second_run(factory, run_id)
    try:
        async with factory() as session, session.begin():
            repository = PatternCandidateRepository(session)
            await repository.create_plan(run_id=run_id, plan=plan, registry=registry)
            await repository.create_plan(run_id=run_b, plan=plan, registry=registry)
            first = await repository.load_plan(run_id)
            other = await repository.load_plan(run_b)
            assert first is not None and other is not None
            conversation_item = next(
                item for item in first.items if item.category.value == "assistant_message_enter"
            )
            other_item = next(item for item in other.items if item.category.value == "assistant_message_enter")
            with pytest.raises(ValueError, match="stage"):
                await repository.record_exposure(
                    run_id=run_id,
                    stage=Stage.IDENTITY,
                    candidate_item_id=conversation_item.id,
                )
            with pytest.raises(ValueError, match="run"):
                await repository.record_exposure(
                    run_id=run_id,
                    stage=Stage.CONVERSATION,
                    candidate_item_id=other_item.id,
                )
        same_call = await model_call(factory, run_id)
        other_call = await model_call(factory, run_b)
        async with factory() as session, session.begin():
            repository = PatternCandidateRepository(session)
            first = await repository.load_plan(run_id)
            assert first is not None
            item = next(item for item in first.items if item.category.value == "assistant_message_enter")
            exposure = await repository.record_exposure(
                run_id=run_id,
                stage=Stage.CONVERSATION,
                candidate_item_id=item.id,
                model_call_id=same_call,
            )
            repeated = await repository.record_exposure(
                run_id=run_id,
                stage=Stage.CONVERSATION,
                candidate_item_id=item.id,
                model_call_id=same_call,
            )
            assert repeated.id == exposure.id
            with pytest.raises(ValueError, match="model call"):
                await repository.record_exposure(
                    run_id=run_id,
                    stage=Stage.CONVERSATION,
                    candidate_item_id=item.id,
                    model_call_id=other_call,
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_usage_claim_modes_are_exclusive_and_model_call_consistent() -> None:
    engine, factory, run_id = await database()
    try:
        call_id = await model_call(factory, run_id)
        async with factory() as session, session.begin():
            repository = PatternCandidateRepository(session)
            await repository.create_plan(
                run_id=run_id,
                plan=complete_candidate_plan(),
                registry=load_builtin_atomic_registry(),
            )
            loaded = await repository.load_plan(run_id)
            assert loaded is not None
            item = next(item for item in loaded.items if item.category.value == "assistant_message_enter")
            exposure = await repository.record_exposure(
                run_id=run_id,
                stage=Stage.CONVERSATION,
                candidate_item_id=item.id,
                model_call_id=call_id,
            )
            with pytest.raises(ValueError, match="usage"):
                await repository.record_usage_claim(
                    run_id=run_id,
                    stage=Stage.CONVERSATION,
                    candidate_item_id=item.id,
                    usage_mode="unknown",
                    model_call_id=call_id,
                )
            with pytest.raises(ValueError, match="model call"):
                await repository.record_usage_claim(
                    run_id=run_id,
                    stage=Stage.CONVERSATION,
                    exposure_id=exposure.id,
                    usage_mode="primary",
                    model_call_id=None,
                )
            claim = await repository.record_usage_claim(
                run_id=run_id,
                stage=Stage.CONVERSATION,
                exposure_id=exposure.id,
                usage_mode="primary",
                model_call_id=call_id,
            )
            assert claim.id == (
                await repository.record_usage_claim(
                    run_id=run_id,
                    stage=Stage.CONVERSATION,
                    exposure_id=exposure.id,
                    usage_mode="primary",
                    model_call_id=call_id,
                )
            ).id
            with pytest.raises(ValueError, match="different usage"):
                await repository.record_usage_claim(
                    run_id=run_id,
                    stage=Stage.CONVERSATION,
                    exposure_id=exposure.id,
                    usage_mode="combined",
                    model_call_id=call_id,
                )
    finally:
        await engine.dispose()


def test_candidate_models_expose_named_indexes_and_constraints() -> None:
    for model, required in (
        (PatternCandidatePlanRecord, {"uq_pattern_candidate_plan_run", "ck_pattern_candidate_plan_schema_version"}),
        (PatternCandidateGroupRecord, {"uq_pattern_candidate_group_plan_category"}),
        (PatternCandidateItemRecord, {"uq_pattern_candidate_item_group_rank", "uq_pattern_candidate_item_group_pattern_version", "ck_pattern_candidate_item_rank"}),
        (PatternStageExposure, {"uq_pattern_stage_exposure_idempotency", "ck_pattern_stage_exposure_stage"}),
        (PatternStageUsageClaim, {"uq_pattern_stage_usage_claim_exposure", "ck_pattern_stage_usage_mode"}),
        (PatternReview, {"ck_pattern_review_status", "ix_pattern_reviews_pattern_version_created_at"}),
    ):
        constraint_names = {constraint.name for constraint in model.__table__.constraints if constraint.name}
        index_names = {index.name for index in model.__table__.indexes if index.name}
        assert required & (constraint_names | index_names) == required
