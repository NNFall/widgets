from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Tenant, User
from app.patterns.candidate_repository import PatternCandidateRepository
from app.saas.models import (
    GenerationStageAttempt,
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
    AtomicPatternCategory,
    AtomicPatternStatus,
    PatternCandidate,
    PatternCandidateGroup,
    PatternCandidatePlan,
)
from builder_lab.patterns.atomic_registry import (
    AtomicPatternRegistry,
    load_builtin_atomic_registry as _load_builtin_atomic_registry,
)
from builder_lab.patterns.atomic_quality import compute_atomic_quality_profile
from builder_lab.patterns.candidate_resolver import STAGE_PATTERN_CATEGORIES


POSTGRES_URL = os.getenv("KAIGO_TEST_POSTGRES_URL")


def load_builtin_atomic_registry() -> AtomicPatternRegistry:
    """Test registry with explicit roles and approved visual fixtures."""

    source = _load_builtin_atomic_registry()
    signature = {
        AtomicPatternCategory.WIDGET_OPEN,
        AtomicPatternCategory.WIDGET_CLOSE,
        AtomicPatternCategory.LAUNCHER_ATTENTION,
        AtomicPatternCategory.MESSAGE_SEND,
        AtomicPatternCategory.ASSISTANT_MESSAGE_ENTER,
        AtomicPatternCategory.USER_MESSAGE_ENTER,
    }
    structural = {
        AtomicPatternCategory.SHELL_LAYOUT,
        AtomicPatternCategory.RESPONSIVE_TRANSITION,
    }
    definitions = []
    for definition in source.definitions:
        role = (
            "fixture"
            if definition.pattern_id.endswith("-technical")
            else "signature"
            if definition.category in signature
            else "structural"
            if definition.category in structural
            else "support"
        )
        state = (
            definition.provenance.get("review_state")
            if definition.pattern_id.endswith("-technical")
            else "approved"
        )
        definitions.append(
            replace(
                definition,
                provenance={
                    **definition.provenance,
                    "review_state": state,
                    "pattern_role": role,
                },
            )
        )
    return AtomicPatternRegistry(tuple(definitions))


def complete_candidate_plan() -> PatternCandidatePlan:
    registry = load_builtin_atomic_registry()
    grouped = {
        definition.category: definition
        for definition in registry.definitions
        if definition.provenance["review_state"] == "approved"
        and compute_atomic_quality_profile(definition).selector_eligible
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


async def staged_model_call(factory, run_id, stage: Stage):
    async with factory() as session, session.begin():
        attempt = GenerationStageAttempt(
            id=uuid4(),
            run_id=run_id,
            stage=stage.value,
            ordinal=1,
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        session.add(attempt)
        await session.flush()
        call = ModelCall(
            id=uuid4(),
            run_id=run_id,
            stage_attempt_id=attempt.id,
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
async def test_create_plan_rejects_technical_fixture_even_when_database_approved() -> None:
    engine, factory, run_id = await database()
    registry = load_builtin_atomic_registry()
    technical = registry.resolve("widget-open-technical", 1)
    plan = PatternCandidatePlan(
        schema_version=2,
        direction_id="candidate-technical",
        groups=(
            PatternCandidateGroup(
                category=technical.category,
                candidates=(
                    PatternCandidate(
                        pattern_id=technical.pattern_id,
                        version=technical.version,
                        rank=1,
                        reason="fixture replay",
                    ),
                ),
            ),
        ),
        summary="technical fixture replay",
    )
    try:
        async with factory() as session, session.begin():
            repository = PatternCandidateRepository(session)
            with pytest.raises(ValueError, match="quality"):
                await repository.create_plan(
                    run_id=run_id,
                    plan=plan,
                    registry=registry,
                )
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
async def test_idempotent_replay_rechecks_effective_quality_gate() -> None:
    """A previously persisted plan cannot bypass a later review rejection."""

    engine, factory, run_id = await database()
    registry = load_builtin_atomic_registry()
    plan = complete_candidate_plan()
    first_candidate = plan.groups[0].candidates[0]
    try:
        async with factory() as session, session.begin():
            repository = PatternCandidateRepository(session)
            await repository.create_plan(run_id=run_id, plan=plan, registry=registry)
            persisted = await session.scalar(
                select(WidgetPatternVersion).where(
                    WidgetPatternVersion.pattern_id == first_candidate.pattern_id,
                    WidgetPatternVersion.version == first_candidate.version,
                )
            )
            assert persisted is not None
            await repository.append_review(
                persisted.id,
                "reviewer@example.com",
                "rejected",
                "Needs a stronger reviewed candidate.",
            )

            with pytest.raises(ValueError, match="effectively approved"):
                await repository.create_plan(
                    run_id=run_id,
                    plan=plan,
                    registry=registry,
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
            assert persisted.manifest_snapshot["status"] == "deprecated"

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
    definition = next(
        item
        for item in registry.definitions
        if item.provenance["review_state"] == "approved"
    )
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
        same_call = await staged_model_call(factory, run_id, Stage.CONVERSATION)
        other_call = await staged_model_call(factory, run_b, Stage.CONVERSATION)
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
        call_id = await staged_model_call(factory, run_id, Stage.CONVERSATION)
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
        (PatternCandidateGroupRecord, {"uq_pattern_candidate_group_plan_category", "uq_pattern_candidate_group_plan_ordinal", "ck_pattern_candidate_group_ordinal", "ix_pattern_candidate_groups_category"}),
        (PatternCandidateItemRecord, {"uq_pattern_candidate_item_group_rank", "uq_pattern_candidate_item_group_pattern_version", "ck_pattern_candidate_item_rank"}),
        (PatternStageExposure, {"uq_pattern_stage_exposure_idempotency", "ck_pattern_stage_exposure_stage"}),
        (PatternStageUsageClaim, {"uq_pattern_stage_usage_claim_exposure", "ck_pattern_stage_usage_mode"}),
        (PatternReview, {"ck_pattern_review_status", "ix_pattern_reviews_status", "ix_pattern_reviews_pattern_version_created_at"}),
    ):
        constraint_names = {constraint.name for constraint in model.__table__.constraints if constraint.name}
        index_names = {index.name for index in model.__table__.indexes if index.name}
        assert required & (constraint_names | index_names) == required


@pytest.mark.asyncio
async def test_model_call_must_have_same_run_stage_attempt_for_exposure_and_claim() -> None:
    engine, factory, run_id = await database()
    try:
        stage_less = await model_call(factory, run_id)
        wrong_stage = await staged_model_call(factory, run_id, Stage.IDENTITY)
        same_stage = await staged_model_call(factory, run_id, Stage.CONVERSATION)
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
            with pytest.raises(ValueError, match="stage"):
                await repository.record_exposure(
                    run_id=run_id,
                    stage=Stage.CONVERSATION,
                    candidate_item_id=item.id,
                    model_call_id=stage_less,
                )
            with pytest.raises(ValueError, match="stage"):
                await repository.record_exposure(
                    run_id=run_id,
                    stage=Stage.CONVERSATION,
                    candidate_item_id=item.id,
                    model_call_id=wrong_stage,
                )
            exposure = await repository.record_exposure(
                run_id=run_id,
                stage=Stage.CONVERSATION,
                candidate_item_id=item.id,
                model_call_id=same_stage,
            )
            with pytest.raises(ValueError, match="stage"):
                await repository.record_usage_claim(
                    run_id=run_id,
                    stage=Stage.CONVERSATION,
                    exposure_id=exposure.id,
                    usage_mode="primary",
                    model_call_id=wrong_stage,
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_database_stable_identity_rejects_duplicate_null_model_call_exposure() -> None:
    engine, factory, run_id = await database()
    try:
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
            first = await repository.record_exposure(
                run_id=run_id,
                stage=Stage.CONVERSATION,
                candidate_item_id=item.id,
            )
            duplicate = PatternStageExposure(
                id=uuid4(),
                run_id=run_id,
                stage=Stage.CONVERSATION.value,
                candidate_item_id=item.id,
                model_call_id=None,
                idempotency_model_call_id=UUID(int=0),
            )
            session.add(duplicate)
            with pytest.raises(IntegrityError):
                await session.flush()
            assert first.id != duplicate.id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_candidate_plan_preserves_group_order_and_exact_replay() -> None:
    engine, factory, run_id = await database()
    registry = load_builtin_atomic_registry()
    plan = complete_candidate_plan()
    reversed_plan = PatternCandidatePlan(
        schema_version=plan.schema_version,
        direction_id=plan.direction_id,
        groups=tuple(reversed(plan.groups)),
        summary=plan.summary,
    )
    try:
        async with factory() as session, session.begin():
            repository = PatternCandidateRepository(session)
            first = await repository.create_plan(
                run_id=run_id,
                plan=reversed_plan,
                registry=registry,
            )
            second = await repository.create_plan(
                run_id=run_id,
                plan=reversed_plan,
                registry=registry,
            )
            assert first.id == second.id
        async with factory() as session:
            loaded = await PatternCandidateRepository(session).load_plan(run_id)
        assert loaded is not None
        assert loaded.plan == reversed_plan
    finally:
        await engine.dispose()


def test_exposure_uses_stable_non_null_idempotency_identity() -> None:
    column = PatternStageExposure.__table__.columns.get("idempotency_model_call_id")
    assert column is not None
    assert column.nullable is False
    unique_constraints = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in PatternStageExposure.__table__.constraints
        if constraint.name == "uq_pattern_stage_exposure_idempotency"
    }
    assert unique_constraints["uq_pattern_stage_exposure_idempotency"] == (
        "run_id",
        "stage",
        "candidate_item_id",
        "idempotency_model_call_id",
    )


@pytest.mark.asyncio
async def test_deleted_model_call_keeps_exposure_identity_distinct_from_no_call() -> None:
    engine, factory, run_id = await database()
    try:
        async with engine.begin() as connection:
            await connection.execute(text("PRAGMA foreign_keys=ON"))
        call_id = await staged_model_call(factory, run_id, Stage.CONVERSATION)
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
            no_call = await repository.record_exposure(
                run_id=run_id,
                stage=Stage.CONVERSATION,
                candidate_item_id=item.id,
            )
            with_call = await repository.record_exposure(
                run_id=run_id,
                stage=Stage.CONVERSATION,
                candidate_item_id=item.id,
                model_call_id=call_id,
            )
            no_call_id = no_call.id
            with_call_id = with_call.id

        async with factory() as session, session.begin():
            call = await session.get(ModelCall, call_id)
            assert call is not None
            await session.delete(call)

        async with factory() as session, session.begin():
            deleted = await session.get(PatternStageExposure, with_call_id)
            no_call = await session.get(PatternStageExposure, no_call_id)
            assert deleted is not None and no_call is not None
            assert deleted.model_call_id is None
            assert deleted.idempotency_model_call_id == call_id
            assert no_call.model_call_id is None
            assert no_call.idempotency_model_call_id == UUID(int=0)
            assert deleted.idempotency_model_call_id != no_call.idempotency_model_call_id
            repository = PatternCandidateRepository(session)
            loaded = await repository.load_plan(run_id)
            assert loaded is not None
            item = next(item for item in loaded.items if item.category.value == "assistant_message_enter")
            replay = await repository.record_exposure(
                run_id=run_id,
                stage=Stage.CONVERSATION,
                candidate_item_id=item.id,
                model_call_id=None,
            )
            assert replay.id == no_call_id
            no_call_claim = await repository.record_usage_claim(
                run_id=run_id,
                stage=Stage.CONVERSATION,
                candidate_item_id=item.id,
                usage_mode="primary",
                model_call_id=None,
            )
            assert no_call_claim.exposure_id == no_call_id
            with pytest.raises(ValueError, match="model call"):
                await repository.record_usage_claim(
                    run_id=run_id,
                    stage=Stage.CONVERSATION,
                    exposure_id=with_call_id,
                    usage_mode="primary",
                    model_call_id=None,
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_effective_review_state_rejects_unknown_explicit_version() -> None:
    engine, factory, run_id = await database()
    try:
        async with factory() as session:
            repository = PatternCandidateRepository(session)
            with pytest.raises(ValueError, match="unknown"):
                await repository.effective_review_state(uuid4())
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.skipif(
    not POSTGRES_URL,
    reason="KAIGO_TEST_POSTGRES_URL is not configured",
)
async def test_postgres_concurrent_registry_and_plan_replay() -> None:
    """Two workers race on each unique key and reload the committed winner."""
    source_url = make_url(POSTGRES_URL)
    if source_url.drivername == "postgresql":
        source_url = source_url.set(drivername="postgresql+asyncpg")
    database_name = f"kaigo_candidates_race_{uuid4().hex}"
    admin_url = source_url.set(database="postgres")
    target_url = source_url.set(database=database_name)
    rendered = target_url.render_as_string(hide_password=False)
    admin_engine = create_async_engine(
        admin_url.render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
    )
    target_engine = None
    created = False
    try:
        async with admin_engine.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        created = True
        target_engine = create_async_engine(rendered)
        async with target_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(target_engine, expire_on_commit=False)
        async with factory() as session, session.begin():
            session.add(Tenant(id=1, name="Race", slug="race"))
            await session.flush()
            session.add(User(id=10, tenant_id=1, email="race@example.com"))
            await session.flush()
            project = Project(tenant_id=1, owner_user_id=10, source_url="https://example.com/")
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

        registry = load_builtin_atomic_registry()

        async def run_race(worker):
            ready = 0
            lock = asyncio.Lock()
            gate = asyncio.Event()

            async def invoke():
                nonlocal ready
                async with lock:
                    ready += 1
                    if ready == 2:
                        gate.set()
                await gate.wait()
                return await worker()

            return await asyncio.gather(invoke(), invoke())

        async def sync_worker():
            async with factory() as session, session.begin():
                await PatternCandidateRepository(session).sync_registry(registry)
                return await session.scalar(select(WidgetPatternVersion.id).limit(1))

        sync_results = await run_race(sync_worker)
        assert sync_results[0] == sync_results[1]

        plan = complete_candidate_plan()

        async def plan_worker():
            async with factory() as session, session.begin():
                return (
                    await PatternCandidateRepository(session).create_plan(
                        run_id=run_id,
                        plan=plan,
                        registry=registry,
                    )
                ).id

        plan_results = await run_race(plan_worker)
        assert plan_results[0] == plan_results[1]
    finally:
        if target_engine is not None:
            await target_engine.dispose()
        if created:
            async with admin_engine.connect() as connection:
                await connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname=:name AND pid<>pg_backend_pid()"
                    ),
                    {"name": database_name},
                )
                await connection.execute(text(f'DROP DATABASE "{database_name}"'))
        await admin_engine.dispose()
