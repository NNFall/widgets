import asyncio
import hashlib
import importlib
import json
import os
import signal
import shutil
import subprocess
import sys
from types import SimpleNamespace
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Tenant, User
from app.models.providers.agentrouter_qwen import AgentRouterQwenProvider
from app.models.lineage import ModelInvocationContext
from app.saas.models import (
    GenerationArtifact,
    GenerationEvent,
    GenerationForensicManifest,
    GenerationRun,
    GenerationStageAttempt,
    ModelCall,
    Project,
)
from builder_lab.engines.base import (
    BuilderEngineError,
    CompositionPlanResult,
    EngineResult,
)
from builder_lab.forensics.config import GenerationForensicsConfig
from builder_lab.forensics.models import ForensicBlob
from builder_lab.forensics.recorder import GenerationForensicRecorder
from builder_lab.models import (
    BuilderRequest,
    DirectionProposal,
    DirectionRole,
    EngineName,
    Stage,
    TokenUsage,
)
from builder_lab.postgres_store import PostgresRunStore
from builder_lab.patterns.models import PatternCategory
from builder_lab.store import RunStore
from builder_lab.worker import (
    BuilderWorker,
    DurableVisualStore,
    LeaseLostError,
    OrchestratorStageHandler,
    PostgresWorkerQueue,
    RunClaim,
    StageInput,
    StageResult,
    failure_category_for_error,
)
from scripts.run_builder_worker import install_signal_handlers, load_stage_handler
from scripts import run_agentrouter_widget_benchmark, run_builder_worker
from tests.builder_lab_cases.test_validation import artifact


POSTGRES_URL = os.getenv("KAIGO_TEST_POSTGRES_URL")


def test_worker_module_exposes_durable_queue_contract() -> None:
    worker = importlib.import_module("builder_lab.worker")

    assert worker.PostgresWorkerQueue
    assert worker.BuilderWorker
    assert worker.RunClaim
    assert worker.LeaseLostError


@pytest.mark.asyncio
async def test_routed_reference_analyzer_awaits_pipeline_result() -> None:
    expected = {"source_url": "https://example.com/"}

    class FakeReferencePipeline:
        def __init__(self) -> None:
            self.backend = None

        async def analyze(self, source_url, *, structured_backend):
            self.backend = structured_backend
            assert source_url == expected["source_url"]
            return expected

    pipeline = FakeReferencePipeline()
    claim = SimpleNamespace(
        mode="direct",
        run_id=uuid4(),
        attempt_id=uuid4(),
        next_stage="reference_analysis",
    )
    config = SimpleNamespace(reference_timeout_seconds=60)

    analyzer = run_builder_worker.make_routed_reference_analyzer(
        reference_pipeline=pipeline,
        model_router=object(),
        config=config,
    )

    assert await analyzer(claim, expected["source_url"]) == expected
    assert pipeline.backend is not None
    context = pipeline.backend._context
    assert context.stage_attempt_id == claim.attempt_id
    assert context.stage == claim.next_stage
    assert context.operation == "reference_analysis"
    assert context.semantic_attempt == 1
    assert context.candidate_id is None
    assert context.persona is None


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


def test_runtime_router_has_explicit_repair_and_code_review_policies(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GEMINI_INPUT_PRICE_MICROUSD_PER_MILLION", "100")
    monkeypatch.setenv("GEMINI_OUTPUT_PRICE_MICROUSD_PER_MILLION", "200")
    config = SimpleNamespace(
        gemini_api_key="test-key",
        gemini_base_url="https://example.test",
        direct_model="gemini-builder",
        reference_analyzer_model="gemini-reference",
        visual_critic_model="gemini-review",
        hybrid_routing_enabled=False,
    )
    router = run_builder_worker.make_runtime_model_router(config, None)

    assert ("repair", "direct") in router._policies
    assert ("repair", "express") in router._policies
    assert ("code_review", "direct") in router._policies
    assert ("code_review", "express") in router._policies


def test_runtime_router_retries_transient_direct_gemini_builder_failure(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GEMINI_INPUT_PRICE_MICROUSD_PER_MILLION", "100")
    monkeypatch.setenv("GEMINI_OUTPUT_PRICE_MICROUSD_PER_MILLION", "200")
    config = SimpleNamespace(
        gemini_api_key="test-key",
        gemini_base_url="https://example.test",
        direct_model="gemini-builder",
        reference_analyzer_model="gemini-reference",
        visual_critic_model="gemini-review",
        hybrid_routing_enabled=False,
    )

    router = run_builder_worker.make_runtime_model_router(config, None)

    for mode in ("direct", "express"):
        for role in (
            "direction_candidate",
            "direction_judge",
            "composition_planner",
            "art_direction_generator",
            "widget_generator",
            "brand_designer",
            "conversation_designer",
            "motion_designer",
            "repair",
        ):
            targets = router._policies[(role, mode)].targets
            assert [(target.provider, target.model) for target in targets] == [
                ("gemini", "gemini-builder"),
                ("gemini", "gemini-builder"),
            ]
        assert [
            (target.provider, target.model)
            for target in router._policies[("code_review", mode)].targets
        ] == [
            ("gemini", "gemini-review"),
            ("gemini", "gemini-review"),
        ]


def test_runtime_router_maps_hybrid_roles_to_gpt_glm_and_gemini(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GEMINI_INPUT_PRICE_MICROUSD_PER_MILLION", "100")
    monkeypatch.setenv("GEMINI_OUTPUT_PRICE_MICROUSD_PER_MILLION", "200")
    config = SimpleNamespace(
        gemini_api_key="gemini-key",
        gemini_base_url="https://gemini.example",
        direct_model="gemini-builder",
        reference_analyzer_model="gemini-reference",
        visual_critic_model="gemini-vision",
        hybrid_routing_enabled=True,
        agentrouter_api_key="router-key",
        agentrouter_base_url="https://agentrouter.org/v1",
        agentrouter_timeout_seconds=180,
        agentrouter_qwen_executable="qwen",
        agentrouter_gpt_model="gpt-5.5",
        agentrouter_glm_model="glm-5.2",
        agentrouter_gpt_input_price_microusd_per_million=7_000_000,
        agentrouter_gpt_output_price_microusd_per_million=7_000_000,
        agentrouter_glm_input_price_microusd_per_million=6_000_000,
        agentrouter_glm_output_price_microusd_per_million=6_000_000,
        zenmux_api_key="zenmux-key",
        zenmux_base_url="https://zenmux.example/api/v1",
        zenmux_deepseek_model="deepseek/deepseek-v4-flash-free",
    )

    router = run_builder_worker.make_runtime_model_router(config, None)

    for mode in ("direct", "express"):
        for role in (
            "direction_candidate",
            "direction_judge",
            "composition_planner",
            "visual_judge",
            "code_review",
        ):
            targets = router._policies[(role, mode)].targets
            assert [(target.provider, target.model) for target in targets] == [
                ("agentrouter", "gpt-5.5"),
                ("zenmux", "deepseek/deepseek-v4-flash-free"),
                ("gemini", "gemini-builder"),
            ]
            assert targets[0].provider != targets[1].provider
            assert targets[0].input_price_microusd_per_million == 7_000_000
            assert targets[0].output_price_microusd_per_million == 7_000_000
        for role in (
            "art_direction_generator",
            "widget_generator",
            "brand_designer",
            "conversation_designer",
            "motion_designer",
            "repair",
        ):
            targets = router._policies[(role, mode)].targets
            assert [(target.provider, target.model) for target in targets] == [
                ("agentrouter", "glm-5.2"),
                ("zenmux", "deepseek/deepseek-v4-flash-free"),
                ("gemini", "gemini-builder"),
            ]
            assert targets[0].provider != targets[1].provider
            assert targets[0].input_price_microusd_per_million == 6_000_000
            assert targets[0].output_price_microusd_per_million == 6_000_000
        image_roles = {
            "reference_analyst": "gemini-reference",
            "conversation_ux": "gemini-vision",
            "brand_motion": "gemini-vision",
            "adversarial_customer": "gemini-vision",
        }
        for role, model in image_roles.items():
            targets = router._policies[(role, mode)].targets
            assert [(target.provider, target.model) for target in targets] == [
                ("gemini", model)
            ]

    assert router._providers["agentrouter"]._timeout_seconds == 180
    assert router._providers["zenmux"]._timeout_seconds == 180
    zenmux_target = router._policies[("direction_candidate", "direct")].targets[1]
    assert zenmux_target.input_price_microusd_per_million == 0
    assert zenmux_target.output_price_microusd_per_million == 0


def test_provider_diverse_runtime_uses_short_default_but_benchmark_can_opt_in_to_900(
    monkeypatch,
) -> None:
    provider = AgentRouterQwenProvider(api_key="router-key", executable="qwen")
    assert provider._timeout_seconds == 180
    assert provider._timeout_seconds < 900

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_agentrouter_widget_benchmark.py",
            "--model",
            "gpt-5.5",
            "--timeout",
            "900",
        ],
    )
    benchmark = run_agentrouter_widget_benchmark.parse_args()
    assert benchmark.timeout == 900


def test_runtime_router_fails_closed_when_hybrid_configuration_is_missing(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GEMINI_INPUT_PRICE_MICROUSD_PER_MILLION", "100")
    monkeypatch.setenv("GEMINI_OUTPUT_PRICE_MICROUSD_PER_MILLION", "200")
    config = SimpleNamespace(
        gemini_api_key="gemini-key",
        gemini_base_url="https://gemini.example",
        direct_model="gemini-builder",
        reference_analyzer_model="gemini-reference",
        visual_critic_model="gemini-vision",
        hybrid_routing_enabled=True,
        agentrouter_api_key=None,
        agentrouter_base_url="https://agentrouter.org/v1",
        agentrouter_timeout_seconds=900,
        agentrouter_qwen_executable="qwen",
        agentrouter_gpt_model="gpt-5.5",
        agentrouter_glm_model="glm-5.2",
        agentrouter_gpt_input_price_microusd_per_million=7_000_000,
        agentrouter_gpt_output_price_microusd_per_million=7_000_000,
        agentrouter_glm_input_price_microusd_per_million=6_000_000,
        agentrouter_glm_output_price_microusd_per_million=6_000_000,
    )

    with pytest.raises(RuntimeError, match="AGENTROUTER_API_KEY"):
        run_builder_worker.make_runtime_model_router(config, None)


def test_production_repair_verifier_factory_uses_router_not_direct_gemini() -> None:
    make_factory = getattr(run_builder_worker, "make_repair_verifier_factory", None)
    assert callable(make_factory)
    config = SimpleNamespace(
        visual_critic_model="gemini-review",
        visual_critic_thinking_level="high",
        visual_critic_timeout_seconds=12,
        agentrouter_timeout_seconds=180,
    )
    router = SimpleNamespace(generate=object())
    run_id = uuid4()
    invocation_context = ModelInvocationContext(
        stage_attempt_id=uuid4(),
        stage="foundation",
        operation="repair_verification",
    )

    verifier = make_factory(
        config,
        mode="express",
        model_router=router,
        run_id=run_id,
        invocation_context=invocation_context,
    )()

    assert verifier._model_router is router
    assert verifier._routing_role == "code_review"
    assert verifier._routing_mode == "express"
    assert verifier._run_id == run_id
    assert verifier._invocation_context is invocation_context
    assert verifier._client is None
    assert verifier.timeout_seconds == 12
    assert verifier.routing_timeout_seconds == 180


def test_worker_derives_model_context_from_claim_attempt_and_stage() -> None:
    claim = SimpleNamespace(
        attempt_id=uuid4(),
        next_stage="foundation",
    )

    context = run_builder_worker.make_model_invocation_context(
        claim,
        operation="artifact_generation",
    )

    assert context == ModelInvocationContext(
        stage_attempt_id=claim.attempt_id,
        stage="foundation",
        operation="artifact_generation",
    )


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
        generation_forensics=GenerationForensicsConfig.disabled(),
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
    monkeypatch.setenv("KAIGO_BUILDER_WORKER_BOOT_ID", "test-boot")
    monkeypatch.setenv("KAIGO_RELEASE_ID", "test-release")
    monkeypatch.setenv(
        "KAIGO_BUILDER_WORKER_IMAGE_IDENTITY", "sha256:" + "a" * 64
    )

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
            "--profile",
            "saas-worker",
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
    dispatch_order = []

    class FakeQueue:
        def __init__(self):
            self.dispatch_receipts = []

        async def stage_input(self, claim):
            return StageInput(request=request)

        async def arm_provider_dispatch(self, claim, *, provider):
            self.dispatch_receipts.append((claim, provider))
            dispatch_order.append("receipt_committed")

    class FakeEngine:
        def __init__(self):
            self.calls = []
            self.closed = False

        async def generate(self, **kwargs):
            self.calls.append(kwargs)
            dispatch_order.append("provider_dispatched")
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
    queue = FakeQueue()
    handler = OrchestratorStageHandler(
        queue=queue,
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
    assert queue.dispatch_receipts == [(claim, "antigravity")]
    assert dispatch_order == ["receipt_committed", "provider_dispatched"]


def _composition_payload() -> dict[str, object]:
    ids = {
        PatternCategory.LAUNCHER: "orb-pulse",
        PatternCategory.SHELL: "compact-chat",
        PatternCategory.MESSAGES: "paired-bubbles",
        PatternCategory.COMPOSER: "single-line-pill",
        PatternCategory.MOTION: "spring-reveal",
    }
    return {
        "schema_version": 1,
        "direction_id": "candidate-2",
        "selections": [
            {
                "slot": category.value,
                "pattern_id": pattern_id,
                "version": 1,
                "parameters": {},
                "reason": "Поддерживает выбранное направление",
            }
            for category, pattern_id in ids.items()
        ],
        "custom_escape": None,
        "summary": "Компактный брендовый консультант",
    }


def _composition_direction() -> DirectionProposal:
    return DirectionProposal(
        proposal_id="candidate-2",
        role=DirectionRole.INTERACTION_INVENTOR,
        title="Живой эксперт",
        art_direction="Компактный брендовый чат.",
        interaction_model="Launcher раскрывает panel.",
        safeguards=("Не перекрывать страницу",),
    )


@pytest.mark.asyncio
async def test_builtin_handler_persists_composition_without_artifact() -> None:
    request = BuilderRequest(engine=EngineName.DIRECT, brief="Собери виджет")
    direction = _composition_direction()

    class FakeQueue:
        async def stage_input(self, _claim):
            return StageInput(
                request=request,
                context={"selected_direction": direction.to_dict()},
            )

    class FakeEngine:
        def __init__(self):
            self.plan_calls = 0
            self.closed = False

        async def plan_composition(self, **_kwargs):
            self.plan_calls += 1
            return CompositionPlanResult(
                payload=_composition_payload(),
                usage=TokenUsage(prompt_tokens=12, output_tokens=5),
                provider_request_id="composition-call",
            )

        async def close(self):
            self.closed = True

    engine = FakeEngine()
    handler = OrchestratorStageHandler(
        queue=FakeQueue(),
        engine_factories={EngineName.DIRECT: lambda: engine},
        reference_analyzer=lambda _url: None,
    )
    claim = RunClaim(
        run_id=uuid4(),
        project_id=uuid4(),
        worker_id="worker",
        mode="direct",
        next_stage="composition",
        last_completed_stage="art_direction",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
    )

    result = await handler(claim)

    assert result.artifact is None
    assert result.context["composition_plan"] == _composition_payload()
    assert result.output_refs == ("composition-call",)
    assert result.usage.total_tokens == 17
    assert engine.plan_calls == 1
    assert engine.closed


@pytest.mark.asyncio
async def test_builtin_handler_resumes_foundation_from_durable_composition() -> None:
    request = BuilderRequest(engine=EngineName.DIRECT, brief="Собери виджет")
    direction = _composition_direction()

    class FakeQueue:
        async def stage_input(self, _claim):
            return StageInput(
                request=request,
                previous_artifact=artifact(revision=1, stage=Stage.ART_DIRECTION),
                context={
                    "selected_direction": direction.to_dict(),
                    "composition_plan": _composition_payload(),
                },
            )

    class FakeEngine:
        def __init__(self):
            self.generate_calls = []
            self.closed = False

        async def plan_composition(self, **_kwargs):
            raise AssertionError("durable resume must not replan")

        async def generate(self, **kwargs):
            self.generate_calls.append(kwargs)
            return EngineResult(
                artifact=artifact(revision=2, stage=Stage.FOUNDATION),
                provider_request_id="foundation-call",
            )

        async def close(self):
            self.closed = True

    engine = FakeEngine()
    handler = OrchestratorStageHandler(
        queue=FakeQueue(),
        engine_factories={EngineName.DIRECT: lambda: engine},
        reference_analyzer=lambda _url: None,
    )
    claim = RunClaim(
        run_id=uuid4(),
        project_id=uuid4(),
        worker_id="worker",
        mode="direct",
        next_stage="foundation",
        last_completed_stage="composition",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
    )

    result = await handler(claim)

    assert result.artifact is not None
    assert result.artifact.stage is Stage.FOUNDATION
    assert engine.generate_calls[0]["composition"].plan.to_dict() == _composition_payload()
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
                                "composition_plan": _composition_payload(),
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


async def _forensic_recorder(tmp_path, factory) -> GenerationForensicRecorder:
    return await GenerationForensicRecorder.open(
        factory,
        GenerationForensicsConfig(
            enabled=True,
            root=tmp_path / "generation-forensics",
            ttl_hours=120,
            max_bytes=32 * 1024 * 1024,
        ),
    )


def _jpeg_blob() -> ForensicBlob:
    output = BytesIO()
    Image.new("RGB", (2, 2), color=(240, 120, 60)).save(output, format="JPEG")
    data = output.getvalue()
    return ForensicBlob(
        data=data,
        mime_type="image/jpeg",
        byte_count=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


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
        project = await database.get(Project, project_id)
        assert project is not None
        project.active_run_id = run.id
        return run.id


async def _verified_terminal_artifact(factory, run_id: UUID) -> None:
    candidate = artifact(revision=1, stage=Stage.AGENT_BUILD)
    async with factory() as database, database.begin():
        database.add(
            GenerationArtifact(
                run_id=run_id,
                revision=candidate.revision,
                stage=candidate.stage.value,
                html=candidate.body_html,
                css=candidate.css,
                javascript=candidate.javascript,
                config={"artifact": candidate.to_dict()},
                quality_status="verified",
            )
        )


@pytest.mark.asyncio
async def test_worker_materializes_forensics_before_stage_handler(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    recorder = await _forensic_recorder(tmp_path, factory)
    queue = PostgresWorkerQueue(
        factory,
        lease_seconds=30,
        forensic_recorder=recorder,
    )
    run_id = await _queued_run(factory, project_id)
    claim = await queue.claim("forensic-worker")
    assert isinstance(claim, RunClaim)
    handler_observed_active = False

    async def handle(_claim: RunClaim) -> StageResult:
        nonlocal handler_observed_active
        async with factory() as database:
            manifest = await database.scalar(
                select(GenerationForensicManifest).where(
                    GenerationForensicManifest.run_id == run_id
                )
            )
        run_dir = (
            recorder.config.root / "runs" / run_id.hex[:2] / str(run_id)
        )
        handler_observed_active = bool(
            manifest is not None
            and manifest.state == "active"
            and manifest.expires_at is None
            and (run_dir / ".kaigo-generation-run.json").is_file()
            and (run_dir / "manifest.json").is_file()
        )
        return StageResult(public_message="reference ready")

    worker = BuilderWorker(
        queue=queue,
        worker_id="forensic-worker",
        stage_handler=handle,
        heartbeat_interval=1,
    )
    try:
        assert await worker._run_claim(claim) == "art_direction"
        assert handler_observed_active
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_persists_screenshot_bytes_only_in_forensic_volume(
    tmp_path,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    recorder = await _forensic_recorder(tmp_path, factory)
    queue = PostgresWorkerQueue(
        factory,
        lease_seconds=30,
        forensic_recorder=recorder,
    )
    run_id = await _queued_run(factory, project_id)
    claim = await queue.claim("forensic-worker")
    assert isinstance(claim, RunClaim)
    blob = _jpeg_blob()
    output_ref = "desktop-open-initial"
    private_diagnostic = (
        "Authorization: Bearer private-token screenshot@example.com"
    )

    try:
        await queue.append_attempt_event(
            claim,
            event_type="screenshot.captured",
            stage=Stage.MOTION_POLISH,
            status="completed",
            message="screenshot captured",
            revision=5,
            diagnostic=private_diagnostic,
            output_refs=(output_ref,),
            forensic_payload={
                "screenshot": {
                    "screenshot_id": "desktop_open_initial",
                    "sha256": blob.sha256,
                    "byte_count": blob.byte_count,
                },
                "critic": {"finding": "composer is too narrow"},
            },
            forensic_blobs=(blob,),
        )
        await queue.append_attempt_event(
            claim,
            event_type="screenshot.captured",
            stage=Stage.MOTION_POLISH,
            status="completed",
            message="same screenshot bytes captured again",
            revision=5,
            output_refs=(output_ref,),
            forensic_payload={
                "screenshot": {
                    "screenshot_id": "desktop_after_turn_1",
                    "sha256": blob.sha256,
                    "byte_count": blob.byte_count,
                },
            },
            forensic_blobs=(blob,),
        )

        async with factory() as database:
            events = (
                await database.execute(
                    select(GenerationEvent)
                    .where(
                        GenerationEvent.run_id == run_id,
                        GenerationEvent.event_type == "screenshot.captured",
                    )
                    .order_by(GenerationEvent.sequence)
                )
            ).scalars().all()
            manifest = await database.scalar(
                select(GenerationForensicManifest).where(
                    GenerationForensicManifest.run_id == run_id
                )
            )

        assert len(events) == 2
        assert all(event.forensic_ref is not None for event in events)
        event = events[0]
        assert event.public_payload == {
            "output_refs": [output_ref],
            "revision": 5,
            "stage": "motion_polish",
            "status": "completed",
        }
        assert event.payload["output_refs"] == [output_ref]
        assert blob.sha256 not in json.dumps(event.public_payload)
        assert "diagnostic" not in event.payload
        assert blob.data not in json.dumps(event.payload).encode("utf-8")
        assert manifest is not None and manifest.state == "active"

        run_dir = recorder.config.root / "runs" / run_id.hex[:2] / str(run_id)
        assert (run_dir / "blobs" / f"{blob.sha256}.jpg").read_bytes() == blob.data
        evidence = json.loads(
            (run_dir / event.forensic_ref).read_text(encoding="utf-8")
        )
        serialized_evidence = json.dumps(evidence, ensure_ascii=False)
        assert "private-token" not in serialized_evidence
        assert "screenshot@example.com" not in serialized_evidence
        assert evidence["payload"]["critic"]["finding"] == (
            "composer is too narrow"
        )
        assert evidence["payload"]["blob_refs"] == [
            f"blobs/{blob.sha256}.jpg"
        ]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_rejects_unknown_event_without_consuming_sequence(
    tmp_path,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    run_id = await _queued_run(factory, project_id)
    claim = await queue.claim("worker")
    assert isinstance(claim, RunClaim)

    try:
        async with factory() as database:
            before = await database.get(GenerationRun, run_id)
            assert before is not None
            next_sequence = before.next_event_sequence
        with pytest.raises(ValueError, match="unknown generation event type"):
            await queue.append_attempt_event(
                claim,
                event_type="legacy.private_event",
                stage=None,
                status="running",
                message="must not persist",
            )
        async with factory() as database:
            after = await database.get(GenerationRun, run_id)
            assert after is not None
            assert after.next_event_sequence == next_sequence
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_in_memory_store_discards_forensic_payload_and_blob_bytes() -> None:
    store = RunStore()
    run = await store.create(
        BuilderRequest(engine=EngineName.DIRECT, brief="memory-only")
    )
    blob = _jpeg_blob()

    event = await store.append_event(
        run.run_id,
        event_type="screenshot.captured",
        stage=Stage.MOTION_POLISH,
        status="completed",
        message="screenshot captured",
        revision=1,
        output_refs=(blob.sha256,),
        forensic_payload={"secret": "private-memory-value"},
        forensic_blobs=(blob,),
    )

    serialized_event = json.dumps(event.to_dict(), ensure_ascii=False)
    serialized_store = repr(store._runs)
    assert blob.sha256 not in serialized_event
    assert "private-memory-value" not in serialized_event
    assert "private-memory-value" not in serialized_store
    assert blob.data.hex() not in serialized_store


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
async def test_claim_stages_and_completes_the_same_persisted_attempt(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    run_id = await _queued_run(factory, project_id)
    try:
        claim = await queue.claim("worker")
        assert isinstance(claim, RunClaim)

        async with factory() as database:
            attempt = await database.get(GenerationStageAttempt, claim.attempt_id)
            assert attempt is not None
            assert attempt.run_id == run_id
            assert attempt.stage == "reference_analysis"
            assert attempt.ordinal == 1
            assert attempt.status == "running"
            assert attempt.finished_at is None

        await queue.stage_result(
            claim,
            StageResult(public_message="analysis ready"),
        )
        async with factory() as database:
            attempt = await database.get(GenerationStageAttempt, claim.attempt_id)
            assert attempt is not None
            assert attempt.status == "result_staged"
            assert attempt.finished_at is None

        assert await queue.finalize_stage(claim) == "art_direction"
        async with factory() as database:
            attempt = await database.get(GenerationStageAttempt, claim.attempt_id)
            assert attempt is not None
            assert attempt.status == "completed"
            assert attempt.finished_at is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_finalize_artifact_links_only_calls_from_exact_stage_attempt(
    tmp_path,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    run_id = await _queued_run(factory, project_id)
    try:
        reference_claim = await queue.claim("artifact-worker")
        assert isinstance(reference_claim, RunClaim)
        await queue.stage_result(
            reference_claim,
            StageResult(public_message="analysis ready"),
        )
        assert await queue.finalize_stage(reference_claim) == "art_direction"

        stale_attempt_id = uuid4()
        async with factory() as database, database.begin():
            database.add(
                GenerationStageAttempt(
                    id=stale_attempt_id,
                    run_id=run_id,
                    stage="art_direction",
                    ordinal=1,
                    status="interrupted",
                    started_at=datetime.now(timezone.utc) - timedelta(minutes=2),
                    finished_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                )
            )

        claim = await queue.continue_claim(run_id, worker_id="artifact-worker")
        assert claim.next_stage == "art_direction"
        assert claim.attempt_id != stale_attempt_id

        current_call_id = uuid4()
        stale_call_id = uuid4()
        async with factory() as database, database.begin():
            for call_id, attempt_id in (
                (current_call_id, claim.attempt_id),
                (stale_call_id, stale_attempt_id),
            ):
                database.add(
                    ModelCall(
                        id=call_id,
                        run_id=run_id,
                        stage_attempt_id=attempt_id,
                        logical_invocation_id=uuid4(),
                        operation="direction_candidate",
                        semantic_attempt=1,
                        fallback_index=1,
                        provider="test-provider",
                        model="test-model",
                        role="art_direction_generator",
                        mode="direct",
                        prompt_version="direction-v1",
                        attempt=1,
                        provider_dispatched=True,
                        input_tokens=10,
                        output_tokens=5,
                        thinking_tokens=1,
                        cache_read_tokens=2,
                        cache_write_tokens=0,
                        latency_ms=10,
                        status="completed",
                        cost_state="estimated",
                        cost_microusd=10,
                        pricing_snapshot={"currency": "USD"},
                    )
                )

        candidate = artifact(revision=1, stage=Stage.ART_DIRECTION)
        await queue.stage_result(
            claim,
            StageResult(public_message="direction ready", artifact=candidate),
        )
        assert await queue.finalize_stage(claim) == "composition"

        async with factory() as database:
            artifact_row = await database.scalar(
                select(GenerationArtifact).where(
                    GenerationArtifact.run_id == run_id,
                    GenerationArtifact.revision == 1,
                )
            )
            current_call = await database.get(ModelCall, current_call_id)
            stale_call = await database.get(ModelCall, stale_call_id)
            assert artifact_row is not None
            assert current_call is not None
            assert stale_call is not None
            assert current_call.artifact_id == artifact_row.id
            assert stale_call.artifact_id is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_ambiguous_dispatch_backfills_attempt_before_failure(
    tmp_path,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    run_id = await _queued_run(factory, project_id, mode="antigravity")
    legacy_claim = await queue.claim("legacy-worker")
    assert isinstance(legacy_claim, RunClaim)
    try:
        async with factory() as database, database.begin():
            attempt = await database.get(
                GenerationStageAttempt,
                legacy_claim.attempt_id,
            )
            assert attempt is not None
            await database.delete(attempt)
            database.add(
                ModelCall(
                    run_id=run_id,
                    provider="paid-provider",
                    model="paid-model",
                    role="reference_analysis",
                    mode="antigravity",
                    prompt_version="paid-v1",
                    request_id="legacy-provider-success",
                    attempt=1,
                    provider_dispatched=True,
                    status="completed",
                    input_tokens=100,
                    output_tokens=50,
                    thinking_tokens=0,
                    latency_ms=25,
                    cost_microusd=10,
                    pricing_snapshot={"currency": "USD"},
                )
            )
            run = await database.get(GenerationRun, run_id)
            assert run is not None
            run.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

        terminal = await queue.claim("replacement")

        assert terminal is not None
        assert not isinstance(terminal, RunClaim)
        async with factory() as database:
            run = await database.get(GenerationRun, run_id)
            attempt = await database.get(
                GenerationStageAttempt,
                legacy_claim.attempt_id,
            )
            assert run is not None
            assert run.state == "failed"
            assert run.error_code == "provider_dispatch_ambiguous"
            assert attempt is not None
            assert attempt.status == "accounting_failed"
            assert attempt.finished_at is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_cancel_claim_backfills_attempt_before_cancellation(
    tmp_path,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    run_id = await _queued_run(factory, project_id)
    legacy_claim = await queue.claim("legacy-worker")
    assert isinstance(legacy_claim, RunClaim)
    try:
        async with factory() as database, database.begin():
            attempt = await database.get(
                GenerationStageAttempt,
                legacy_claim.attempt_id,
            )
            assert attempt is not None
            await database.delete(attempt)

        assert await queue.request_cancel(run_id)
        await queue.cancel_claim(run_id, worker_id="legacy-worker")

        async with factory() as database:
            run = await database.get(GenerationRun, run_id)
            attempt = await database.get(
                GenerationStageAttempt,
                legacy_claim.attempt_id,
            )
            assert run is not None
            assert run.state == "cancelled"
            assert attempt is not None
            assert attempt.status == "cancelled"
            assert attempt.finished_at is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_compatibility_checkpoint_backfills_completed_attempt(
    tmp_path,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    run_id = await _queued_run(factory, project_id)
    legacy_claim = await queue.claim("legacy-worker")
    assert isinstance(legacy_claim, RunClaim)
    try:
        async with factory() as database, database.begin():
            attempt = await database.get(
                GenerationStageAttempt,
                legacy_claim.attempt_id,
            )
            assert attempt is not None
            await database.delete(attempt)

        next_stage = await queue.complete_stage(
            run_id,
            "reference_analysis",
            worker_id="legacy-worker",
        )

        assert next_stage == "art_direction"
        async with factory() as database:
            run = await database.get(GenerationRun, run_id)
            attempt = await database.get(
                GenerationStageAttempt,
                legacy_claim.attempt_id,
            )
            assert run is not None
            assert run.last_completed_stage == "reference_analysis"
            assert attempt is not None
            assert attempt.status == "completed"
            assert attempt.finished_at is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stage_attempt_transition_rejects_stale_started_attempt_id(
    tmp_path,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    run_id = await _queued_run(factory, project_id)
    stale = await queue.claim("stale-worker")
    assert isinstance(stale, RunClaim)
    try:
        async with factory() as database, database.begin():
            run = await database.get(GenerationRun, run_id)
            assert run is not None
            run.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        replacement = await queue.claim("replacement")
        assert isinstance(replacement, RunClaim)
        assert replacement.attempt_id != stale.attempt_id

        async with factory() as database, database.begin():
            stale_attempt = await database.get(
                GenerationStageAttempt,
                stale.attempt_id,
            )
            assert stale_attempt is not None
            stale_attempt.status = "running"
            stale_attempt.finished_at = None

        async with factory() as database, database.begin():
            with pytest.raises(LeaseLostError, match="attempt fence moved"):
                await queue._transition_stage_attempt(
                    database,
                    run_id=run_id,
                    stage="reference_analysis",
                    attempt_id=stale.attempt_id,
                    status="failed",
                    now=datetime.now(timezone.utc),
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_expired_lease_interrupts_attempt_before_replacement_claim(
    tmp_path,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    run_id = await _queued_run(factory, project_id)
    try:
        stale = await queue.claim("stale")
        assert isinstance(stale, RunClaim)
        async with factory() as database, database.begin():
            run = await database.get(GenerationRun, run_id)
            assert run is not None
            run.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

        replacement = await queue.claim("replacement")

        assert isinstance(replacement, RunClaim)
        assert replacement.attempt_id != stale.attempt_id
        async with factory() as database:
            attempts = (
                await database.execute(
                    select(GenerationStageAttempt)
                    .where(
                        GenerationStageAttempt.run_id == run_id,
                        GenerationStageAttempt.stage == "reference_analysis",
                    )
                    .order_by(GenerationStageAttempt.ordinal)
                )
            ).scalars().all()
            assert [attempt.ordinal for attempt in attempts] == [1, 2]
            assert [attempt.status for attempt in attempts] == [
                "interrupted",
                "running",
            ]
            assert attempts[0].finished_at is not None
            assert attempts[1].finished_at is None

        with pytest.raises(LeaseLostError):
            await queue.stage_result(
                stale,
                StageResult(public_message="stale result"),
            )
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
            assert run.progress == 14
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
        await _verified_terminal_artifact(factory, run_id)
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
        return StageResult(
            public_message=f"Готов этап {claim.next_stage}",
            artifact=(
                artifact(revision=1, stage=Stage.AGENT_BUILD)
                if claim.next_stage == "agent_build"
                else None
            ),
        )

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
            attempt = await database.scalar(
                select(GenerationStageAttempt).where(
                    GenerationStageAttempt.run_id == run_id
                )
            )
            assert run.state == "cancelled"
            assert run.lease_owner is None
            assert attempt is not None
            assert attempt.status == "cancelled"
            assert attempt.finished_at is not None
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
async def test_concurrent_visitor_chat_does_not_block_predispatch_stage_retry(
    tmp_path,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    run_id = await _queued_run(factory, project_id, mode="antigravity")
    first = await queue.claim("crashed-before-dispatch")
    assert first is not None

    async with factory() as database, database.begin():
        database.add(
            ModelCall(
                run_id=run_id,
                provider="chat-provider",
                model="chat-model",
                role="chat_visitor",
                mode="express",
                prompt_version="chat-v1",
                request_id="chat-call-1",
                attempt=1,
                provider_dispatched=True,
                status="completed",
                input_tokens=10,
                output_tokens=5,
                thinking_tokens=0,
                latency_ms=10,
                cost_microusd=1,
                pricing_snapshot={"currency": "USD"},
            )
        )
        run = await database.get(GenerationRun, run_id)
        assert run is not None
        run.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    try:
        replacement = await queue.claim("replacement")

        assert isinstance(replacement, RunClaim)
        assert replacement.run_id == run_id
        assert replacement.next_stage == "reference_analysis"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_expired_paid_dispatch_fails_closed_without_reexecuting_stage(
    tmp_path,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    run_id = await _queued_run(factory, project_id, mode="antigravity")
    first = await queue.claim("crashed")
    assert first is not None

    async with factory() as database, database.begin():
        started = await database.scalar(
            select(GenerationEvent).where(
                GenerationEvent.run_id == run_id,
                GenerationEvent.event_type == "stage.started",
            )
        )
        assert started is not None
        database.add(
            ModelCall(
                run_id=run_id,
                provider="paid-provider",
                model="paid-model",
                role="reference_analysis",
                mode="antigravity",
                prompt_version="paid-v1",
                request_id="provider-success-1",
                attempt=1,
                provider_dispatched=True,
                status="completed",
                input_tokens=100,
                output_tokens=50,
                thinking_tokens=0,
                latency_ms=25,
                cost_microusd=10,
                pricing_snapshot={"currency": "USD"},
                # Provider audit uses the database clock while stage events use the
                # worker clock. Dispatch reconciliation must not depend on ordering
                # timestamps from those separate clocks.
                created_at=started.created_at - timedelta(days=1),
            )
        )
        run = await database.get(GenerationRun, run_id)
        assert run is not None
        run.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    calls = 0
    settled_runs = []

    async def handle(_claim):
        nonlocal calls
        calls += 1
        return StageResult(public_message="must not run")

    async def settle(run_id):
        settled_runs.append(run_id)

    replacement = BuilderWorker(
        queue=queue,
        worker_id="replacement",
        stage_handler=handle,
        terminal_hook=settle,
    )
    try:
        assert await replacement.run_once()
        assert calls == 0
        assert settled_runs == [run_id]
        async with factory() as database:
            run = await database.get(GenerationRun, run_id)
            attempt = await database.get(
                GenerationStageAttempt,
                first.attempt_id,
            )
            assert run is not None
            model_calls = list(
                (
                    await database.execute(
                        select(ModelCall).where(ModelCall.run_id == run_id)
                    )
                ).scalars()
            )
            events = list(
                (
                    await database.execute(
                        select(GenerationEvent)
                        .where(GenerationEvent.run_id == run_id)
                        .order_by(GenerationEvent.sequence)
                    )
                ).scalars()
            )
            assert run.state == "failed"
            assert run.error_code == "provider_dispatch_ambiguous"
            assert run.failure_category == "platform"
            assert run.lease_owner is None
            assert attempt is not None
            assert attempt.status == "accounting_failed"
            assert attempt.finished_at is not None
            assert len(model_calls) == 1
            assert model_calls[0].status == "completed"
            assert [event.event_type for event in events][-2:] == [
                "stage.failed",
                "run.failed",
            ]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_expired_antigravity_dispatch_receipt_fails_closed_without_reexecution(
    tmp_path,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    run_id = await _queued_run(factory, project_id, mode="antigravity")
    first = await queue.claim("crashed")
    assert isinstance(first, RunClaim)
    assert (
        await queue.complete_stage(
            run_id,
            "reference_analysis",
            worker_id="crashed",
        )
        == "agent_build"
    )
    agent_claim = await queue.continue_claim(run_id, worker_id="crashed")

    await queue.arm_provider_dispatch(agent_claim, provider="antigravity")
    await queue.arm_provider_dispatch(agent_claim, provider="antigravity")

    async with factory() as database, database.begin():
        run = await database.get(GenerationRun, run_id)
        assert run is not None
        run.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    calls = 0

    async def handle(_claim):
        nonlocal calls
        calls += 1
        return StageResult(public_message="must not run")

    replacement = BuilderWorker(
        queue=queue,
        worker_id="replacement",
        stage_handler=handle,
    )
    try:
        assert await replacement.run_once()
        assert calls == 0
        async with factory() as database:
            run = await database.get(GenerationRun, run_id)
            receipts = list(
                (
                    await database.execute(
                        select(GenerationEvent).where(
                            GenerationEvent.run_id == run_id,
                            GenerationEvent.event_type == "provider.dispatch_armed",
                        )
                    )
                ).scalars()
            )
            assert run is not None
            assert run.state == "failed"
            assert run.error_code == "provider_dispatch_ambiguous"
            assert len(receipts) == 1
            payload = receipts[0].payload
            assert payload["run_id"] == str(run_id)
            assert payload["sequence"] == receipts[0].sequence
            assert payload["type"] == "provider.dispatch_armed"
            assert payload["message"] == "Отправка провайдеру зафиксирована"
            assert payload["stage"] == "agent_build"
            assert payload["status"] == "armed"
            assert payload["worker_id"] == "crashed"
            assert payload["attempt_id"] == str(agent_claim.attempt_id)
            assert payload["provider"] == "antigravity"
            assert datetime.fromisoformat(payload["timestamp"]).tzinfo is not None
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
        return StageResult(
            public_message=f"Готов этап {claim.next_stage}",
            artifact=(
                artifact(revision=1, stage=Stage.AGENT_BUILD)
                if claim.next_stage == "agent_build"
                else None
            ),
        )

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
            attempts = (
                await database.execute(
                    select(GenerationStageAttempt)
                    .where(
                        GenerationStageAttempt.run_id == run_id,
                        GenerationStageAttempt.stage == "reference_analysis",
                    )
                    .order_by(GenerationStageAttempt.ordinal)
                )
            ).scalars().all()
            assert calls == 3
            assert len(retries) == 2
            assert [event.payload["attempt"] for event in retries] == [1, 2]
            assert [attempt.ordinal for attempt in attempts] == [1, 2, 3]
            assert [attempt.status for attempt in attempts] == [
                "interrupted",
                "interrupted",
                "completed",
            ]
            assert all(attempt.finished_at is not None for attempt in attempts)
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
            attempt = await database.scalar(
                select(GenerationStageAttempt).where(
                    GenerationStageAttempt.run_id == run_id
                )
            )
            assert calls == 1
            assert run.state == "failed"
            assert run.error_code == "provider_unavailable"
            assert run.error_message == "Модель генерации сейчас не настроена"
            assert "secret" not in run.error_message
            assert attempt is not None
            assert attempt.status == "failed"
            assert attempt.finished_at is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_permission_denial_is_terminal_provider_failure_without_retry(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    run_id = await _queued_run(factory, project_id)
    calls = 0
    public_message = (
        "Сервис генерации недоступен из-за ограничений доступа или оплаты. "
        "Обратитесь в поддержку."
    )

    async def handle(_claim):
        nonlocal calls
        calls += 1
        raise BuilderEngineError(
            "provider_permission_denied",
            public_message,
            diagnostic="private project billing diagnostic",
        )

    worker = BuilderWorker(
        queue=queue,
        worker_id="permission-denied-worker",
        stage_handler=handle,
    )
    try:
        assert await worker.run_once()
        async with factory() as database:
            run = await database.get(GenerationRun, run_id)
            events = list(
                (
                    await database.execute(
                        select(GenerationEvent)
                        .where(GenerationEvent.run_id == run_id)
                        .order_by(GenerationEvent.sequence)
                    )
                ).scalars()
            )

        assert calls == 1
        assert run is not None
        assert run.state == "failed"
        assert run.error_code == "provider_permission_denied"
        assert run.error_message == public_message
        assert run.failure_category == "provider"
        assert not any(
            event.event_type == "stage.retry_scheduled" for event in events
        )
    finally:
        await engine.dispose()


def test_invalid_response_is_classified_as_model_invalid_output() -> None:
    category = failure_category_for_error(
        BuilderEngineError(
            "invalid_response",
            "Сервис генерации вернул некорректный ответ",
        )
    )

    assert category.value == "model_invalid_output"


@pytest.mark.asyncio
async def test_route_exhaustion_fails_once_and_persists_aggregate_usage(
    tmp_path,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    run_id = await _queued_run(factory, project_id)
    calls = 0
    diagnostic = (
        'route_attempts=[{"provider":"agentrouter","model":"gpt-5.5",'
        '"outcome":"failed","error_code":"generation_timeout"},'
        '{"provider":"gemini","model":"gemini-fallback",'
        '"outcome":"failed","error_code":"provider_unavailable"}]'
    )

    async def handle(_claim):
        nonlocal calls
        calls += 1
        raise BuilderEngineError(
            "route_exhausted",
            "Сервис генерации не смог завершить запрос доступным маршрутом",
            diagnostic=diagnostic,
            usage=TokenUsage(prompt_tokens=31, output_tokens=12, thinking_tokens=4),
        )

    worker = BuilderWorker(
        queue=queue,
        worker_id="route-exhausted-worker",
        stage_handler=handle,
    )
    try:
        assert await worker.run_once()
        async with factory() as database:
            run = await database.get(GenerationRun, run_id)
            events = list(
                (
                    await database.execute(
                        select(GenerationEvent)
                        .where(GenerationEvent.run_id == run_id)
                        .order_by(GenerationEvent.sequence)
                    )
                ).scalars()
            )
            usage_row = (
                await database.execute(
                    select(
                        func.coalesce(
                            func.sum(
                                GenerationEvent.payload["usage"][
                                    "prompt_tokens"
                                ].as_integer()
                            ),
                            0,
                        ),
                        func.coalesce(
                            func.sum(
                                GenerationEvent.payload["usage"][
                                    "output_tokens"
                                ].as_integer()
                            ),
                            0,
                        ),
                        func.coalesce(
                            func.sum(
                                GenerationEvent.payload["usage"][
                                    "thinking_tokens"
                                ].as_integer()
                            ),
                            0,
                        ),
                    ).where(GenerationEvent.run_id == run_id)
                )
            ).one()
        failed = next(event for event in events if event.event_type == "stage.failed")
        run_failed = next(event for event in events if event.event_type == "run.failed")
        assert calls == 1
        assert run is not None
        assert run.state == "failed"
        assert run.error_code == "route_exhausted"
        assert run.failure_category == "provider"
        assert not any(
            event.event_type == "stage.retry_scheduled" for event in events
        )
        assert failed.payload["usage"] == {
            "prompt_tokens": 31,
            "output_tokens": 12,
            "thinking_tokens": 4,
            "total_tokens": 47,
        }
        assert "diagnostic" not in failed.payload
        assert "usage" not in run_failed.payload
        assert tuple(int(value) for value in usage_row) == (31, 12, 4)
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
            artifact=(
                artifact(revision=1, stage=Stage.AGENT_BUILD)
                if claim.next_stage == "agent_build"
                else None
            ),
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
async def test_staged_result_recovery_reuses_attempt_and_fences_stale_worker(
    tmp_path,
) -> None:
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
        assert replacement.attempt_id == stale.attempt_id

        with pytest.raises(LeaseLostError):
            await queue.finalize_stage(stale)

        assert await queue.finalize_stage(replacement) == "art_direction"
        async with factory() as database:
            attempts = (
                await database.execute(
                    select(GenerationStageAttempt).where(
                        GenerationStageAttempt.run_id == run_id,
                        GenerationStageAttempt.stage == "reference_analysis",
                    )
                )
            ).scalars().all()
            assert len(attempts) == 1
            assert attempts[0].id == stale.attempt_id
            assert attempts[0].ordinal == 1
            assert attempts[0].status == "completed"
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


def test_stage_result_accepts_reference_completed_event() -> None:
    result = StageResult(
        public_message="Анализ исходного сайта завершён",
        events=(
            {
                "event_type": "reference.completed",
                "status": "completed",
                "message": "Анализ исходного сайта завершён",
            },
        ),
    )

    assert result.events[0]["event_type"] == "reference.completed"


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
async def test_shutdown_after_armed_antigravity_dispatch_fails_closed_and_settles(
    tmp_path,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    run_id = await _queued_run(factory, project_id, mode="antigravity")
    dispatch_armed = asyncio.Event()
    cleaned = asyncio.Event()
    settled_runs = []

    async def handle(claim):
        if claim.next_stage == "reference_analysis":
            return StageResult(public_message="analysis ready")
        await queue.arm_provider_dispatch(claim, provider="antigravity")
        dispatch_armed.set()
        try:
            await asyncio.Future()
        finally:
            cleaned.set()

    async def settle(terminal_run_id):
        settled_runs.append(terminal_run_id)

    worker = BuilderWorker(
        queue=queue,
        worker_id="terminating-after-dispatch",
        stage_handler=handle,
        heartbeat_interval=1,
        terminal_hook=settle,
    )
    run_task = asyncio.create_task(worker.run_once())
    try:
        await asyncio.wait_for(dispatch_armed.wait(), timeout=2)
        await asyncio.wait_for(worker.shutdown(), timeout=2)
        results = await asyncio.gather(run_task, return_exceptions=True)

        assert results == [True]
        assert cleaned.is_set()
        assert settled_runs == [run_id]
        assert await queue.claim("replacement") is None
        async with factory() as database:
            run = await database.get(GenerationRun, run_id)
            assert run is not None
            assert run.state == "failed"
            assert run.error_code == "provider_dispatch_ambiguous"
            assert run.lease_owner is None
    finally:
        if not run_task.done():
            run_task.cancel()
        await engine.dispose()


@pytest.mark.asyncio
async def test_heartbeat_error_cancels_and_awaits_provider_task_before_reraise(
    tmp_path,
    monkeypatch,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    queue = PostgresWorkerQueue(factory, lease_seconds=0.15)
    run_id = await _queued_run(factory, project_id, mode="antigravity")
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    allow_cleanup = asyncio.Event()
    cleaned = asyncio.Event()

    async def handle(claim):
        await queue.arm_provider_dispatch(claim, provider="antigravity")
        entered.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            await allow_cleanup.wait()
            raise
        finally:
            cleaned.set()

    async def broken_heartbeat(_run_id, *, worker_id):
        assert worker_id == "heartbeat-error"
        await entered.wait()
        raise RuntimeError("heartbeat database unavailable")

    monkeypatch.setattr(queue, "heartbeat", broken_heartbeat)
    worker = BuilderWorker(
        queue=queue,
        worker_id="heartbeat-error",
        stage_handler=handle,
        heartbeat_interval=0.01,
    )
    run_task = asyncio.create_task(worker.run_once())
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        await asyncio.wait_for(cancelled.wait(), timeout=2)

        assert worker._active_stage_task is not None
        assert not worker._active_stage_task.done()
        assert not run_task.done()
        allow_cleanup.set()

        with pytest.raises(RuntimeError, match="heartbeat database unavailable"):
            await asyncio.wait_for(run_task, timeout=2)
        assert cleaned.is_set()
        assert worker._active_stage_task is None

        async with factory() as database, database.begin():
            run = await database.get(GenerationRun, run_id)
            assert run is not None
            run.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

        replacement_calls = 0
        settled_runs = []

        async def replacement_handle(_claim):
            nonlocal replacement_calls
            replacement_calls += 1
            return StageResult(public_message="must not run")

        async def settle(terminal_run_id):
            settled_runs.append(terminal_run_id)

        replacement = BuilderWorker(
            queue=queue,
            worker_id="replacement",
            stage_handler=replacement_handle,
            terminal_hook=settle,
        )
        assert await replacement.run_once()
        assert replacement_calls == 0
        assert settled_runs == [run_id]
    finally:
        allow_cleanup.set()
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
        return StageResult(
            public_message=f"done {claim.next_stage}",
            artifact=(
                artifact(revision=1, stage=Stage.AGENT_BUILD)
                if claim.next_stage == "agent_build"
                else None
            ),
        )

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
