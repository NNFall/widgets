from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Tenant, User
from app.patterns.candidate_repository import PatternCandidateRepository
from app.saas.models import (
    GenerationRun,
    GenerationStageAttempt,
    ModelCall,
    PatternStageExposure,
    PatternStageUsageClaim,
    Project,
)
from builder_lab.config import BuilderLabConfig
from builder_lab.engines.base import EngineResult
from builder_lab.models import (
    BuilderRequest,
    DirectionProposal,
    DirectionRole,
    EngineName,
    Stage,
)
from builder_lab.patterns.atomic_models import (
    AtomicPatternCategory,
    PatternCandidate,
    PatternCandidateGroup,
    PatternCandidatePlan,
)
from builder_lab.patterns.atomic_registry import load_builtin_atomic_registry
from builder_lab.patterns.candidate_resolver import STAGE_PATTERN_CATEGORIES
from builder_lab.worker import (
    OrchestratorStageHandler,
    PostgresWorkerQueue,
    RunClaim,
    StageInput,
    StageResult,
)


def test_candidate_plan_feature_flag_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KAIGO_PATTERN_CANDIDATE_PLAN_V2_ENABLED", raising=False)
    config = BuilderLabConfig.from_env()
    assert config.pattern_candidate_plan_v2_enabled is False


def test_candidate_plan_feature_flag_accepts_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAIGO_PATTERN_CANDIDATE_PLAN_V2_ENABLED", "true")
    config = BuilderLabConfig.from_env()
    assert config.pattern_candidate_plan_v2_enabled is True


@pytest.mark.asyncio
async def test_feature_flag_disabled_does_not_require_candidate_repository() -> None:
    request = BuilderRequest(engine=EngineName.ANTIGRAVITY, brief="legacy")

    class Queue:
        async def stage_input(self, _claim):
            return StageInput(request=request)

        async def arm_provider_dispatch(self, *_args, **_kwargs):
            return None

    class Engine:
        async def generate(self, **kwargs):
            from builder_lab.engines.base import EngineResult
            from tests.builder_lab_cases.test_validation import artifact

            return EngineResult(artifact=artifact(revision=1, stage=kwargs["stage"]))

        async def close(self):
            return None

    handler = OrchestratorStageHandler(
        queue=Queue(),
        engine_factories={EngineName.ANTIGRAVITY: Engine},
        reference_analyzer=lambda _url: None,
        pattern_candidate_plan_v2_enabled=False,
    )
    claim = RunClaim(
        run_id=uuid4(),
        project_id=uuid4(),
        worker_id="test",
        mode="antigravity",
        next_stage="agent_build",
        last_completed_stage="reference_analysis",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    result = await handler(claim)
    assert result.artifact is not None


def _direction() -> DirectionProposal:
    return DirectionProposal(
        proposal_id="candidate-1",
        role=DirectionRole.INTERACTION_INVENTOR,
        title="Compact assistant",
        art_direction="A clear, quiet support widget.",
        interaction_model="Open a small panel without obscuring the page.",
        safeguards=("Keep the launcher visible",),
    )


def _candidate_plan() -> PatternCandidatePlan:
    registry = load_builtin_atomic_registry()
    groups = []
    for category in {
        category
        for categories in STAGE_PATTERN_CATEGORIES.values()
        for category in categories
    }:
        definition = next(
            item
            for item in registry.definitions
            if item.category is category
            and item.provenance.get("review_state") == "approved"
        )
        groups.append(
            PatternCandidateGroup(
                category=category,
                candidates=(
                    PatternCandidate(
                        pattern_id=definition.pattern_id,
                        version=definition.version,
                        rank=1,
                        reason="Matches the selected direction.",
                    ),
                ),
            )
        )
    return PatternCandidatePlan(
        schema_version=2,
        direction_id="candidate-1",
        groups=tuple(sorted(groups, key=lambda item: item.category.value)),
        summary="Persisted exact shortlist.",
    )


@pytest.mark.asyncio
async def test_enabled_antigravity_keeps_legacy_path_without_selector() -> None:
    request = BuilderRequest(engine=EngineName.ANTIGRAVITY, brief="legacy")
    selector_calls = 0

    class Queue:
        async def stage_input(self, _claim):
            return StageInput(request=request)

        async def arm_provider_dispatch(self, *_args, **_kwargs):
            return None

    class Engine:
        async def generate(self, **kwargs):
            from tests.builder_lab_cases.test_validation import artifact

            return EngineResult(artifact=artifact(revision=1, stage=kwargs["stage"]))

        async def plan_pattern_candidates(self, **_kwargs):
            nonlocal selector_calls
            selector_calls += 1
            raise AssertionError("antigravity must not use the direct selector")

        async def close(self):
            return None

    handler = OrchestratorStageHandler(
        queue=Queue(),
        engine_factories={EngineName.ANTIGRAVITY: Engine},
        reference_analyzer=lambda _url: None,
        pattern_candidate_plan_v2_enabled=True,
    )
    claim = RunClaim(
        run_id=uuid4(),
        project_id=uuid4(),
        worker_id="test",
        mode="antigravity",
        next_stage="agent_build",
        last_completed_stage="reference_analysis",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    result = await handler(claim)
    assert result.artifact is not None
    assert selector_calls == 0


@pytest.mark.asyncio
async def test_enabled_restart_uses_persisted_plan_without_selector() -> None:
    request = BuilderRequest(engine=EngineName.DIRECT, brief="resume")
    plan = _candidate_plan()
    persisted = SimpleNamespace(plan=plan, items=())
    selector_calls = 0

    class Queue:
        async def stage_input(self, _claim):
            return StageInput(
                request=request,
                previous_artifact=__import__(
                    "tests.builder_lab_cases.test_validation",
                    fromlist=["artifact"],
                ).artifact(revision=1, stage=Stage.ART_DIRECTION),
                context={"selected_direction": _direction().to_dict()},
            )

        async def load_pattern_candidate_plan(self, _run_id):
            return persisted

        async def load_effective_pattern_candidate_reviews(self, registry):
            return {
                (item.pattern_id, item.version)
                for item in registry.definitions
                if item.provenance.get("review_state") == "approved"
            }

    class Engine:
        def __init__(self):
            self.generate_calls = []

        async def generate(self, **kwargs):
            self.generate_calls.append(kwargs)
            return EngineResult(
                artifact=__import__(
                    "tests.builder_lab_cases.test_validation",
                    fromlist=["artifact"],
                ).artifact(revision=2, stage=kwargs["stage"]),
            )

        async def plan_pattern_candidates(self, **_kwargs):
            nonlocal selector_calls
            selector_calls += 1
            raise AssertionError("a persisted plan must bypass selector")

        async def close(self):
            return None

    engine = Engine()
    handler = OrchestratorStageHandler(
        queue=Queue(),
        engine_factories={EngineName.DIRECT: lambda: engine},
        reference_analyzer=lambda _url: None,
        pattern_candidate_plan_v2_enabled=True,
    )
    claim = RunClaim(
        run_id=uuid4(),
        project_id=uuid4(),
        worker_id="test",
        mode="direct",
        next_stage="foundation",
        last_completed_stage="composition",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    first = await handler(claim)
    second = await handler(claim)
    assert first.artifact is not None and second.artifact is not None
    assert selector_calls == 0
    assert engine.generate_calls[0]["pattern_candidate_pack"] is not None


@pytest.mark.asyncio
async def test_persisted_composition_plan_is_loaded_before_selector() -> None:
    request = BuilderRequest(engine=EngineName.DIRECT, brief="resume composition")
    plan = _candidate_plan()
    selector_calls = 0

    class Queue:
        async def stage_input(self, _claim):
            return StageInput(
                request=request,
                context={"selected_direction": _direction().to_dict()},
            )

        async def load_pattern_candidate_plan(self, _run_id):
            return SimpleNamespace(plan=plan)

    class Engine:
        async def plan_pattern_candidates(self, **_kwargs):
            nonlocal selector_calls
            selector_calls += 1
            raise AssertionError("persisted composition must bypass selector")

        async def close(self):
            return None

    handler = OrchestratorStageHandler(
        queue=Queue(),
        engine_factories={EngineName.DIRECT: Engine},
        reference_analyzer=lambda _url: None,
        pattern_candidate_plan_v2_enabled=True,
    )
    claim = RunClaim(
        run_id=uuid4(),
        project_id=uuid4(),
        worker_id="test",
        mode="direct",
        next_stage="composition",
        last_completed_stage="art_direction",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    result = await handler(claim)
    assert result.artifact is None
    assert result.context["pattern_candidate_plan"] == plan.to_dict()
    assert selector_calls == 0


@pytest.mark.asyncio
async def test_stage_repairs_and_visual_gate_share_same_candidate_pack() -> None:
    request = BuilderRequest(engine=EngineName.DIRECT, brief="visual")
    plan = _candidate_plan()
    persisted = SimpleNamespace(plan=plan, items=())
    previous = __import__(
        "tests.builder_lab_cases.test_validation",
        fromlist=["artifact"],
    ).artifact(revision=1, stage=Stage.CONVERSATION)
    generated_packs = []
    visual_packs = []
    validation_round = 0

    class Queue:
        async def stage_input(self, _claim):
            return StageInput(
                request=request,
                previous_artifact=previous,
                context={"selected_direction": _direction().to_dict()},
            )

        async def load_pattern_candidate_plan(self, _run_id):
            return persisted

        async def load_effective_pattern_candidate_reviews(self, registry):
            return {
                (item.pattern_id, item.version)
                for item in registry.definitions
                if item.provenance.get("review_state") == "approved"
            }

    class Engine:
        async def generate(self, **kwargs):
            generated_packs.append(kwargs["pattern_candidate_pack"])
            return EngineResult(
                artifact=__import__(
                    "tests.builder_lab_cases.test_validation",
                    fromlist=["artifact"],
                ).artifact(revision=2, stage=kwargs["stage"]),
            )

        async def close(self):
            return None

    class Gate:
        async def evaluate(self, **kwargs):
            visual_packs.append(kwargs["pattern_candidate_pack"])
            return kwargs["candidate"]

    import builder_lab.worker as worker_module

    original_validate = worker_module.validate_artifact

    def fake_validate(*_args, **_kwargs):
        nonlocal validation_round
        validation_round += 1
        if validation_round == 1:
            return (SimpleNamespace(code="test", field="css", message="repair"),)
        return ()

    worker_module.validate_artifact = fake_validate
    try:
        handler = OrchestratorStageHandler(
            queue=Queue(),
            engine_factories={EngineName.DIRECT: Engine},
            reference_analyzer=lambda _url: None,
            visual_gate_factory=lambda _claim: Gate(),
            pattern_candidate_plan_v2_enabled=True,
        )
        claim = RunClaim(
            run_id=uuid4(),
            project_id=uuid4(),
            worker_id="test",
            mode="direct",
            next_stage="motion_polish",
            last_completed_stage="conversation",
            lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        )
        result = await handler(claim)
    finally:
        worker_module.validate_artifact = original_validate
    assert result.artifact is not None
    assert len(generated_packs) == 2
    assert generated_packs[0] is generated_packs[1]
    assert visual_packs == [generated_packs[0]]


@pytest.mark.asyncio
async def test_finalize_persists_exact_stage_exposures_and_safe_usage_claim(tmp_path) -> None:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'candidate-exposure.db'}"
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    plan = _candidate_plan()
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as database, database.begin():
            database.add(Tenant(id=1, name="Alpha", slug=f"alpha-{uuid4().hex[:8]}"))
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
            run = GenerationRun(
                project_id=project.id,
                mode="direct",
                state="running",
                current_stage="foundation",
                last_completed_stage="composition",
                idempotency_key=uuid4().hex,
            )
            database.add(run)
            await database.flush()
            attempt = GenerationStageAttempt(
                run_id=run.id,
                stage="foundation",
                ordinal=1,
                status="running",
                started_at=now,
            )
            database.add(attempt)
            await database.flush()
            calls_by_operation = {}
            for operation in (
                "artifact_generation",
                "validation_repair",
                "visual_repair",
                "visual_critic",
                "visual_judge",
                "repair_verification",
            ):
                call = ModelCall(
                    run_id=run.id,
                    stage_attempt_id=attempt.id,
                    operation=operation,
                    provider="gemini",
                    model="test-model",
                    role=operation,
                    mode="direct",
                    prompt_version="test-v1",
                    request_id=f"{operation}-call",
                    status="completed",
                    provider_dispatched=True,
                )
                database.add(call)
                calls_by_operation[operation] = call
            await database.flush()
            await PatternCandidateRepository(database).create_plan(
                run_id=run.id,
                plan=plan,
                registry=load_builtin_atomic_registry(),
            )
            run_id = run.id
            attempt_id = attempt.id
            first_group = next(
                group
                for group in plan.groups
                if group.category is AtomicPatternCategory.LAUNCHER_SHAPE
            )
            usage_claim = {
                "pattern_id": first_group.candidates[0].pattern_id,
                "version": first_group.candidates[0].version,
                "usage_mode": "primary",
                "request_id": "artifact_generation-call",
            }

        queue = PostgresWorkerQueue(
            factory,
            lease_seconds=30,
            pattern_candidate_plan_v2_enabled=True,
        )
        async with factory() as database, database.begin():
            await queue._persist_pattern_stage_provenance(
                database,
                run_id=run_id,
                stage="foundation",
                result=StageResult(
                    public_message="Foundation complete",
                    context={"pattern_usage_claims": [usage_claim]},
                ),
                attempt_id=attempt_id,
            )

            critic_only_attempt = GenerationStageAttempt(
                run_id=run_id,
                stage="identity",
                ordinal=2,
                status="running",
                started_at=now,
            )
            database.add(critic_only_attempt)
            await database.flush()
            for operation in ("visual_critic", "visual_judge", "repair_verification"):
                database.add(
                    ModelCall(
                        run_id=run_id,
                        stage_attempt_id=critic_only_attempt.id,
                        operation=operation,
                        provider="gemini",
                        model="test-model",
                        role=operation,
                        mode="direct",
                        prompt_version="test-v1",
                        request_id=f"identity-{operation}-call",
                        status="completed",
                        provider_dispatched=True,
                    )
                )
            await database.flush()
            await queue._persist_pattern_stage_provenance(
                database,
                run_id=run_id,
                stage="identity",
                result=StageResult(public_message="Critic-only replay"),
                attempt_id=critic_only_attempt.id,
            )

        async with factory() as database:
            exposures = (
                await database.execute(
                    select(PatternStageExposure).where(
                        PatternStageExposure.run_id == run_id,
                        PatternStageExposure.stage == "foundation",
                    )
                )
            ).scalars().all()
            claims = (
                await database.execute(select(PatternStageUsageClaim))
            ).scalars().all()
            critic_only_exposures = (
                await database.execute(
                    select(PatternStageExposure).where(
                        PatternStageExposure.run_id == run_id,
                        PatternStageExposure.stage == "identity",
                    )
                )
            ).scalars().all()
        pack_operations = {
            "artifact_generation",
            "validation_repair",
            "visual_repair",
        }
        non_pack_operations = {
            "visual_critic",
            "visual_judge",
            "repair_verification",
        }
        assert len(exposures) == 9
        assert {row.model_call_id for row in exposures} == {
            calls_by_operation[operation].id for operation in pack_operations
        }
        assert not {
            calls_by_operation[operation].id for operation in non_pack_operations
        } & {row.model_call_id for row in exposures}
        assert len(claims) == 1
        assert claims[0].usage_mode == "primary"
        assert claims[0].model_call_id == calls_by_operation["artifact_generation"].id
        assert critic_only_exposures == []
    finally:
        await engine.dispose()
