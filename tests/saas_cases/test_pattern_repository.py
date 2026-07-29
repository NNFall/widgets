from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Tenant, User
from app.patterns.repository import PatternOutcomeMetrics, PatternRepository
from app.saas.models import GenerationRun, PatternOutcome, Project
from builder_lab.patterns.models import (
    CompositionPlan,
    PatternCategory,
    PatternSelection,
)
from builder_lab.patterns.registry import load_builtin_registry


def complete_plan() -> CompositionPlan:
    ids = {
        PatternCategory.LAUNCHER: "orb-pulse",
        PatternCategory.SHELL: "compact-chat",
        PatternCategory.MESSAGES: "paired-bubbles",
        PatternCategory.COMPOSER: "single-line-pill",
        PatternCategory.MOTION: "spring-reveal",
    }
    return CompositionPlan(
        schema_version=1,
        direction_id="candidate-2",
        selections=tuple(
            PatternSelection(
                slot=category,
                pattern_id=pattern_id,
                version=1,
                parameters={},
                reason="Проверенный паттерн",
            )
            for category, pattern_id in ids.items()
        ),
        summary="Компактный брендовый консультант",
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


@pytest.mark.asyncio
async def test_repository_persists_and_restores_exact_plan() -> None:
    engine, factory, run_id = await database()
    plan = complete_plan()
    try:
        async with factory() as session, session.begin():
            repository = PatternRepository(session)
            persisted = await repository.create_plan(
                run_id=run_id,
                plan=plan,
                registry=load_builtin_registry(),
            )
            plan_id = persisted.id

        async with factory() as session:
            restored = await PatternRepository(session).load_plan(run_id)

        assert restored is not None
        assert restored.id == plan_id
        assert restored.plan == plan
        assert len(restored.implementation_hashes) == 5
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_one_slot_cannot_be_persisted_twice() -> None:
    engine, factory, run_id = await database()
    try:
        async with factory() as session, session.begin():
            repository = PatternRepository(session)
            plan = await repository.create_empty_plan(
                run_id=run_id,
                schema_version=1,
                direction_id="candidate-2",
                summary="Test plan",
            )
            await repository.sync_registry(load_builtin_registry())
            await repository.add_item(
                plan.id,
                "launcher",
                "orb-pulse",
                1,
                {},
                reason="First",
            )
            with pytest.raises(IntegrityError):
                await repository.add_item(
                    plan.id,
                    "launcher",
                    "peek-tab",
                    1,
                    {},
                    reason="Duplicate",
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_terminal_outcomes_are_one_per_pattern_and_idempotent() -> None:
    engine, factory, run_id = await database()
    metrics = PatternOutcomeMetrics(
        technical_pass=True,
        visual_score=0.9,
        repair_count=2,
        input_tokens=1_000,
        output_tokens=200,
        thinking_tokens=50,
        latency_ms=4_000,
        cost_microusd=12_500,
        published=False,
        adopted=False,
        payload={"quality_status": "verified"},
    )
    try:
        async with factory() as session, session.begin():
            repository = PatternRepository(session)
            await repository.create_plan(
                run_id=run_id,
                plan=complete_plan(),
                registry=load_builtin_registry(),
            )
            first = await repository.record_terminal_outcomes(
                run_id=run_id,
                final_artifact_id=None,
                model_call_id=None,
                metrics=metrics,
            )
            second = await repository.record_terminal_outcomes(
                run_id=run_id,
                final_artifact_id=None,
                model_call_id=None,
                metrics=metrics,
            )

        async with factory() as session:
            count = await session.scalar(
                select(func.count()).select_from(PatternOutcome)
            )
            rows = (
                await session.execute(
                    select(PatternOutcome).order_by(PatternOutcome.idempotency_key)
                )
            ).scalars().all()

        assert len(first) == len(second) == 5
        assert count == 5
        assert {row.repair_count for row in rows} == {2}
        assert {row.visual_score for row in rows} == {0.9}
        assert sum(row.cost_microusd for row in rows) == 12_500
        assert sum(row.input_tokens for row in rows) == 1_000
        assert sum(row.output_tokens for row in rows) == 200
        assert sum(row.thinking_tokens for row in rows) == 50
        assert sum(row.latency_ms for row in rows) == 4_000
    finally:
        await engine.dispose()
