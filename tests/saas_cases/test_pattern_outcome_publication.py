from __future__ import annotations

from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.patterns.repository import PatternOutcomeMetrics, PatternRepository
from app.publication.service import PublicationService
from app.saas.models import GenerationArtifact, PatternOutcome
from builder_lab.patterns.registry import load_builtin_registry
from tests.saas_cases.test_pattern_repository import complete_plan
from tests.saas_cases.test_publication import _seed


def _metrics(*, adopted: bool) -> PatternOutcomeMetrics:
    return PatternOutcomeMetrics(
        technical_pass=True,
        visual_score=0.8,
        repair_count=0,
        input_tokens=10,
        output_tokens=5,
        thinking_tokens=0,
        latency_ms=100,
        cost_microusd=50,
        published=False,
        adopted=adopted,
        payload={},
    )


@pytest_asyncio.fixture
async def publication_outcome_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'pattern-publication.db'}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await _seed(factory)
    try:
        yield factory, ids
    finally:
        await engine.dispose()


async def _seed_outcomes(
    factory: async_sessionmaker,
    artifact_ids: tuple[UUID, ...],
) -> dict[UUID, UUID]:
    run_by_artifact: dict[UUID, UUID] = {}
    async with factory() as database, database.begin():
        repository = PatternRepository(database)
        for index, artifact_id in enumerate(artifact_ids):
            artifact = await database.get(GenerationArtifact, artifact_id)
            assert artifact is not None
            run_by_artifact[artifact_id] = artifact.run_id
            await repository.create_plan(
                run_id=artifact.run_id,
                plan=complete_plan(),
                registry=load_builtin_registry(),
            )
            await repository.record_terminal_outcomes(
                run_id=artifact.run_id,
                final_artifact_id=artifact.id,
                model_call_id=None,
                # There is no adoption event in publication. A pre-existing
                # trustworthy signal must therefore survive these hooks.
                metrics=_metrics(adopted=index == 0),
            )
    return run_by_artifact


async def _signals_by_run(
    factory: async_sessionmaker,
) -> dict[UUID, set[tuple[bool, bool]]]:
    async with factory() as database:
        rows = (
            await database.execute(
                select(
                    PatternOutcome.run_id,
                    PatternOutcome.published,
                    PatternOutcome.adopted,
                )
            )
        ).all()
    signals: dict[UUID, set[tuple[bool, bool]]] = {}
    for run_id, published, adopted in rows:
        signals.setdefault(run_id, set()).add((published, adopted))
    return signals


@pytest.mark.asyncio
async def test_publish_and_rollback_track_only_the_active_release_outcomes(
    publication_outcome_db,
) -> None:
    factory, ids = publication_outcome_db
    artifacts = (ids["first"], ids["same_revision_other_run"])
    run_by_artifact = await _seed_outcomes(factory, artifacts)
    service = PublicationService(factory)

    first = await service.publish(
        ids["project"], actor_user_id=10, tenant_id=1, artifact_id=artifacts[0]
    )
    first_signals = await _signals_by_run(factory)
    assert first_signals[run_by_artifact[artifacts[0]]] == {(True, True)}
    assert first_signals[run_by_artifact[artifacts[1]]] == {(False, False)}

    # A terminal worker replay carries the original false publication metric.
    # It must validate immutable outcome facts without reverting live signals.
    async with factory() as database, database.begin():
        await PatternRepository(database).record_terminal_outcomes(
            run_id=run_by_artifact[artifacts[0]],
            final_artifact_id=artifacts[0],
            model_call_id=None,
            metrics=_metrics(adopted=True),
        )
    assert await _signals_by_run(factory) == first_signals

    duplicate = await service.publish(
        ids["project"], actor_user_id=10, tenant_id=1, artifact_id=artifacts[0]
    )
    assert duplicate.release_id == first.release_id
    assert await _signals_by_run(factory) == first_signals

    await service.publish(
        ids["project"], actor_user_id=10, tenant_id=1, artifact_id=artifacts[1]
    )
    second_signals = await _signals_by_run(factory)
    assert second_signals[run_by_artifact[artifacts[0]]] == {(False, True)}
    assert second_signals[run_by_artifact[artifacts[1]]] == {(True, False)}

    rolled_back = await service.rollback(
        first.publication_id,
        actor_user_id=10,
        tenant_id=1,
        target_release_id=first.release_id,
    )
    assert rolled_back.release_id == first.release_id
    rollback_signals = await _signals_by_run(factory)
    assert rollback_signals[run_by_artifact[artifacts[0]]] == {(True, True)}
    assert rollback_signals[run_by_artifact[artifacts[1]]] == {(False, False)}
