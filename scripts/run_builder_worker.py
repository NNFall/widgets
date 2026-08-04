from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import os
import signal
import socket
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.providers.gemini import GeminiModelProvider
from app.models.providers.codex_bridge import CodexBridgeProvider
from app.models.providers.openai_compatible import OpenAICompatibleProvider
from app.models.lineage import ModelInvocationContext
from app.models.router import (
    ModelPolicy,
    ModelRouter,
    ProviderTarget,
    SqlModelCallAudit,
)
from app.models.structured_generation import RoutedStructuredGenerationBackend
from app.billing.service import TrialSettlementReconciler

from builder_lab.config import BuilderLabConfig
from builder_lab.browser_audit import BrowserAudit
from builder_lab.forensics.config import GenerationForensicsConfig
from builder_lab.forensics.recorder import GenerationForensicRecorder
from builder_lab.reference_pipeline import GeminiReferencePipeline
from builder_lab.engines.gemini_direct import GeminiDirectEngine
from builder_lab.modes import get_mode_policy
from builder_lab.visual_gate import VisualRepairGate
from builder_lab.visual_review import GeminiRepairVerifier
from builder_lab.worker import (
    BuilderWorker,
    DurableVisualStore,
    OrchestratorStageHandler,
    PostgresWorkerQueue,
    RunClaim,
    StageResult,
)
from scripts.run_builder_lab import make_engine_factories, make_visual_critic_factory


StageHandler = Callable[[RunClaim], Awaitable[StageResult]]
BUILTIN_STAGE_HANDLER = "builtin:orchestrator"


def make_model_invocation_context(
    claim: RunClaim,
    *,
    operation: str,
) -> ModelInvocationContext:
    return ModelInvocationContext(
        stage_attempt_id=claim.attempt_id,
        stage=str(claim.next_stage),
        operation=operation,
    )


def install_signal_handlers(
    worker: BuilderWorker,
    *,
    loop=None,
) -> None:
    """Translate process termination signals into a fenced worker shutdown."""

    event_loop = loop or asyncio.get_running_loop()

    def request_shutdown() -> None:
        event_loop.create_task(worker.shutdown())

    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            event_loop.add_signal_handler(signum, request_shutdown)
        except (NotImplementedError, RuntimeError):
            signal.signal(
                signum,
                lambda *_args, callback=request_shutdown: event_loop.call_soon_threadsafe(
                    callback
                ),
            )


def _database_url() -> str:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for the builder worker")
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if not database_url.startswith("postgresql+asyncpg://"):
        raise RuntimeError("builder worker requires PostgreSQL with asyncpg")
    return database_url


def model_routing_timeout_seconds(config) -> int:
    if bool(getattr(config, "codex_bridge_enabled", False)):
        return int(config.codex_bridge_timeout_seconds)
    return int(config.agentrouter_timeout_seconds)


def make_routed_reference_analyzer(
    *,
    reference_pipeline: GeminiReferencePipeline,
    model_router: ModelRouter,
    config: BuilderLabConfig,
):
    async def analyze(claim: RunClaim, source_url: str):
        return await reference_pipeline.analyze(
            source_url,
            structured_backend=RoutedStructuredGenerationBackend(
                router=model_router,
                role="reference_analyst",
                mode=get_mode_policy(claim.mode).name,
                run_id=claim.run_id,
                context=make_model_invocation_context(
                    claim,
                    operation="reference_analysis",
                ),
                timeout_seconds=min(
                    model_routing_timeout_seconds(config),
                    config.reference_timeout_seconds,
                ),
            ),
        )

    return analyze


def _is_async_callable(handler: object) -> bool:
    return inspect.iscoroutinefunction(handler) or inspect.iscoroutinefunction(
        getattr(handler, "__call__", None)
    )


def load_stage_handler(
    reference: str | None = None,
    *,
    default_handler: StageHandler | None = None,
) -> StageHandler:
    dotted = (
        reference
        or os.getenv("KAIGO_BUILDER_STAGE_HANDLER", "")
        or BUILTIN_STAGE_HANDLER
    ).strip()
    if dotted == BUILTIN_STAGE_HANDLER:
        if default_handler is None:
            raise RuntimeError("builtin builder stage handler is not configured")
        handler = default_handler
    elif ":" not in dotted:
        raise RuntimeError(
            "KAIGO_BUILDER_STAGE_HANDLER must name an async callable as module:attribute"
        )
    else:
        module_name, attribute_name = dotted.split(":", 1)
        if not module_name or not attribute_name:
            raise RuntimeError("KAIGO_BUILDER_STAGE_HANDLER is invalid")
        try:
            handler = getattr(importlib.import_module(module_name), attribute_name)
        except (AttributeError, ImportError) as exc:
            raise RuntimeError(
                f"cannot load builder stage handler {dotted!r}"
            ) from exc
    if not callable(handler) or not _is_async_callable(handler):
        raise RuntimeError("builder stage handler must be an async callable")
    return handler


def _positive_float(name: str, default: str) -> float:
    try:
        value = float(os.getenv(name, default))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be numeric") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value


def _required_positive_int(name: str) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        raise RuntimeError(f"{name} is required for the production generation worker")
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value


def _required_positive_int_with_fallback(primary: str, fallback: str) -> int:
    if os.getenv(primary, "").strip():
        return _required_positive_int(primary)
    return _required_positive_int(fallback)


def runtime_model_prices() -> tuple[int, int]:
    return (
        _required_positive_int_with_fallback(
            "GEMINI_BUILDER_INPUT_PRICE_MICROUSD_PER_MILLION",
            "GEMINI_INPUT_PRICE_MICROUSD_PER_MILLION",
        ),
        _required_positive_int_with_fallback(
            "GEMINI_BUILDER_OUTPUT_PRICE_MICROUSD_PER_MILLION",
            "GEMINI_OUTPUT_PRICE_MICROUSD_PER_MILLION",
        ),
    )


def worker_service_identity() -> tuple[str, str, str]:
    values: list[str] = []
    for name in (
        "KAIGO_BUILDER_WORKER_BOOT_ID",
        "KAIGO_RELEASE_ID",
        "KAIGO_BUILDER_WORKER_IMAGE_IDENTITY",
    ):
        value = os.getenv(name, "").strip()
        if not value:
            raise RuntimeError(f"{name} is required for worker readiness")
        values.append(value)
    return values[0], values[1], values[2]


def make_runtime_model_router(config, factory) -> ModelRouter:
    codex_enabled = bool(getattr(config, "codex_bridge_enabled", False))
    if not config.gemini_api_key and not codex_enabled:
        raise RuntimeError("GEMINI_API_KEY is required for routed builder stages")
    hybrid_enabled = bool(getattr(config, "hybrid_routing_enabled", False))
    providers = {}
    input_rate: int | None = None
    output_rate: int | None = None
    if config.gemini_api_key:
        if codex_enabled:
            try:
                input_rate, output_rate = runtime_model_prices()
            except RuntimeError:
                # A ChatGPT subscription does not expose a per-token bill, and
                # optional Gemini fallbacks may also lack a current rate card.
                # Preserve the call as unknown cost instead of inventing zero.
                input_rate, output_rate = None, None
        else:
            input_rate, output_rate = runtime_model_prices()
        providers["gemini"] = GeminiModelProvider(
            api_key=config.gemini_api_key,
            base_url=config.gemini_base_url,
        )
    if codex_enabled:
        providers["codex_bridge"] = CodexBridgeProvider(
            socket_path=config.codex_bridge_socket_path,
            timeout_seconds=config.codex_bridge_timeout_seconds,
        )
    hybrid_available = hybrid_enabled and bool(config.agentrouter_api_key)
    if hybrid_enabled and not hybrid_available and not codex_enabled:
        raise RuntimeError(
            "AGENTROUTER_API_KEY is required when hybrid routing is enabled"
        )
    if hybrid_available:
        if not config.agentrouter_api_key:
            raise AssertionError("hybrid availability requires an API key")
        if not config.agentrouter_base_url.startswith("https://"):
            raise RuntimeError(
                "AGENTROUTER_BASE_URL must use HTTPS when hybrid routing is enabled"
            )
        if not config.agentrouter_gpt_model or not config.agentrouter_glm_model:
            raise RuntimeError(
                "AgentRouter GPT and GLM model names are required for hybrid routing"
            )
        providers["agentrouter"] = OpenAICompatibleProvider(
            api_key=config.agentrouter_api_key,
            base_url=config.agentrouter_base_url,
            provider_name="agentrouter",
            timeout_seconds=config.agentrouter_timeout_seconds,
        )
        if config.zenmux_api_key:
            if not config.zenmux_base_url.startswith("https://"):
                raise RuntimeError(
                    "ZENMUX_BASE_URL must use HTTPS when hybrid routing is enabled"
                )
            providers["zenmux"] = OpenAICompatibleProvider(
                api_key=config.zenmux_api_key,
                base_url=config.zenmux_base_url,
                provider_name="zenmux",
                timeout_seconds=config.agentrouter_timeout_seconds,
            )

    def gemini_target(model: str) -> ProviderTarget:
        if "gemini" not in providers:
            raise RuntimeError("Gemini fallback is not configured")
        return ProviderTarget("gemini", model, input_rate, output_rate)

    def gemini_retry_targets(model: str) -> tuple[ProviderTarget, ...]:
        if "gemini" not in providers:
            return ()
        return (gemini_target(model), gemini_target(model))

    def gemini_single_target(model: str) -> tuple[ProviderTarget, ...]:
        return (gemini_target(model),) if "gemini" in providers else ()

    def codex_target() -> ProviderTarget:
        return ProviderTarget(
            "codex_bridge",
            config.codex_bridge_model,
            None,
            None,
        )

    def with_codex(
        targets: tuple[ProviderTarget, ...],
    ) -> tuple[ProviderTarget, ...]:
        routed = ((codex_target(),) + targets) if codex_enabled else targets
        if not routed:
            raise RuntimeError("no model provider is configured for builder stages")
        return routed

    def gpt_target() -> ProviderTarget:
        return ProviderTarget(
            "agentrouter",
            config.agentrouter_gpt_model,
            config.agentrouter_gpt_input_price_microusd_per_million,
            config.agentrouter_gpt_output_price_microusd_per_million,
        )

    def glm_target() -> ProviderTarget:
        return ProviderTarget(
            "agentrouter",
            config.agentrouter_glm_model,
            config.agentrouter_glm_input_price_microusd_per_million,
            config.agentrouter_glm_output_price_microusd_per_million,
        )

    def zenmux_target() -> ProviderTarget:
        return ProviderTarget("zenmux", config.zenmux_deepseek_model, 0, 0)

    def text_fallbacks() -> tuple[ProviderTarget, ...]:
        return (
            *((zenmux_target(),) if "zenmux" in providers else ()),
            *gemini_retry_targets(config.direct_model),
        )

    def gpt_targets() -> tuple[ProviderTarget, ...]:
        primary = (gpt_target(),) if "agentrouter" in providers else ()
        return (*primary, *text_fallbacks())

    def glm_targets() -> tuple[ProviderTarget, ...]:
        primary = (glm_target(),) if "agentrouter" in providers else ()
        return (*primary, *text_fallbacks())

    gpt_roles = {
        "direction_candidate",
        "direction_judge",
        "composition_planner",
        "visual_judge",
        "code_review",
    }
    glm_roles = {
        "art_direction_generator",
        "widget_generator",
        "brand_designer",
        "conversation_designer",
        "motion_designer",
        "repair",
    }
    policies = {}
    for mode in ("direct", "express"):
        policy = get_mode_policy(mode)
        for role in set(policy.model_roles.values()):
            targets = (
                gpt_targets()
                if hybrid_available and role in gpt_roles
                else glm_targets()
                if hybrid_available and role in glm_roles
                else (
                    gemini_single_target(config.reference_analyzer_model)
                    if role == "reference_analyst"
                    else gemini_retry_targets(config.direct_model)
                )
            )
            prompt_version = (
                "reference-v1"
                if role == "reference_analyst"
                else "builder-v1"
            )
            policies[(role, policy.name)] = ModelPolicy(
                prompt_version=prompt_version,
                targets=with_codex(targets),
            )
        for role in policy.critic_roles:
            policies[(role, policy.name)] = ModelPolicy(
                prompt_version="visual-v1",
                targets=with_codex(
                    gemini_single_target(config.visual_critic_model)
                ),
            )
        if policy.judge_role is not None:
            judge_targets = (
                gpt_targets()
                if hybrid_available
                else gemini_single_target(config.visual_critic_model)
            )
            policies[(policy.judge_role, policy.name)] = ModelPolicy(
                prompt_version="visual-judge-v1",
                targets=with_codex(judge_targets),
            )
        direction_targets = (
            gpt_targets()
            if hybrid_available
            else gemini_retry_targets(config.direct_model)
        )
        for role in ("direction_candidate", "direction_judge"):
            policies[(role, policy.name)] = ModelPolicy(
                prompt_version="direction-v1",
                targets=with_codex(direction_targets),
            )
        repair_targets = (
            glm_targets()
            if hybrid_available
            else gemini_retry_targets(config.direct_model)
        )
        policies[("repair", policy.name)] = ModelPolicy(
            prompt_version="repair-v1",
            targets=with_codex(repair_targets),
        )
        review_targets = (
            gpt_targets()
            if hybrid_available
            else gemini_retry_targets(config.visual_critic_model)
        )
        policies[("code_review", policy.name)] = ModelPolicy(
            prompt_version="code-review-v1",
            targets=with_codex(review_targets),
        )
    return ModelRouter(
        providers=providers,
        policies=policies,
        audit=SqlModelCallAudit(factory),
    )


def make_repair_verifier_factory(
    config,
    *,
    mode: str,
    model_router: ModelRouter,
    run_id: UUID | None,
    invocation_context: ModelInvocationContext,
):
    return lambda: GeminiRepairVerifier(
        model=config.visual_critic_model,
        thinking_level=config.visual_critic_thinking_level,
        timeout_seconds=config.visual_critic_timeout_seconds,
        routing_timeout_seconds=model_routing_timeout_seconds(config),
        model_router=model_router,
        routing_mode=mode,
        routing_role="code_review",
        run_id=run_id,
        invocation_context=invocation_context,
    )


def make_terminal_hook(
    *,
    settle_run: Callable[[UUID], Awaitable[object]],
    model_router: ModelRouter,
) -> Callable[[UUID], Awaitable[object]]:
    async def settle_and_finalize(run_id: UUID) -> object:
        outcome = await settle_run(run_id)
        try:
            await model_router.finalize_run(run_id)
        except Exception:
            # Archival must not roll back billing/trial settlement or wedge the
            # worker. Active mappings remain durable for the next reconciliation.
            logging.getLogger(__name__).exception(
                "model provider session finalization failed for run %s",
                run_id,
            )
        return outcome

    return settle_and_finalize


async def run() -> None:
    engine = create_async_engine(_database_url(), future=True, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    model_router: ModelRouter | None = None
    worker: BuilderWorker | None = None
    try:
        lease_seconds = _positive_float("KAIGO_BUILDER_LEASE_SECONDS", "90")
        heartbeat_interval = _positive_float(
            "KAIGO_BUILDER_HEARTBEAT_SECONDS", "20"
        )
        if heartbeat_interval >= lease_seconds:
            raise RuntimeError(
                "KAIGO_BUILDER_HEARTBEAT_SECONDS must be shorter than the lease"
            )
        worker_id = os.getenv("KAIGO_BUILDER_WORKER_ID", "").strip() or (
            f"{socket.gethostname()}-{os.getpid()}"
        )
        boot_id, deployment_id, image_identity = worker_service_identity()
        worker_started_at = datetime.now(UTC)
        configured_handler = (
            os.getenv("KAIGO_BUILDER_STAGE_HANDLER", "").strip()
            or BUILTIN_STAGE_HANDLER
        )
        default_handler = None
        config: BuilderLabConfig | None = None
        if configured_handler == BUILTIN_STAGE_HANDLER:
            config = BuilderLabConfig.from_env()
            forensic_config = config.generation_forensics
        else:
            forensic_config = GenerationForensicsConfig.from_env(
                environment=os.getenv("KAIGO_ENVIRONMENT", "development")
            )
        forensic_recorder = await GenerationForensicRecorder.open(
            factory,
            forensic_config,
        )
        queue = PostgresWorkerQueue(
            factory,
            lease_seconds=lease_seconds,
            forensic_recorder=forensic_recorder,
        )
        trial_settlements = TrialSettlementReconciler(factory)
        if configured_handler == BUILTIN_STAGE_HANDLER:
            assert config is not None
            model_router = make_runtime_model_router(config, factory)
            engine_factories = make_engine_factories(config)
            if not engine_factories and not config.codex_bridge_enabled:
                raise RuntimeError(
                    "GEMINI_API_KEY (or GOOGLE_AI_API_KEY) is required for the builder worker"
                )
            reference_pipeline = GeminiReferencePipeline.from_config(config)
            default_handler = OrchestratorStageHandler(
                queue=queue,
                engine_factories=engine_factories,
                reference_analyzer=reference_pipeline.analyze,
                routed_reference_analyzer=make_routed_reference_analyzer(
                    reference_pipeline=reference_pipeline,
                    model_router=model_router,
                    config=config,
                ),
                routed_engine_factory=lambda claim, role: GeminiDirectEngine(
                    model_router=model_router,
                    run_id=claim.run_id,
                    routing_role=role,
                    routing_mode=get_mode_policy(claim.mode).name,
                    routing_timeout_seconds=model_routing_timeout_seconds(config),
                    model=config.direct_model,
                    thinking_level=config.builder_thinking_level,
                    invocation_context=make_model_invocation_context(
                        claim,
                        operation=role,
                    ),
                ),
                visual_gate_factory=lambda claim: VisualRepairGate(
                    store=DurableVisualStore(queue, claim),
                    audit_factory=lambda: BrowserAudit(
                        timeout_ms=config.browser_audit_timeout_ms,
                        total_timeout_seconds=(
                            config.browser_audit_total_timeout_seconds
                        ),
                    ),
                    critic_factory=make_visual_critic_factory(
                        config,
                        policy=get_mode_policy(claim.mode),
                        model_router=model_router,
                        run_id=claim.run_id,
                        invocation_context=make_model_invocation_context(
                            claim,
                            operation="visual_critic",
                        ),
                    ),
                    verifier_factory=make_repair_verifier_factory(
                        config,
                        mode=get_mode_policy(claim.mode).name,
                        model_router=model_router,
                        run_id=claim.run_id,
                        invocation_context=make_model_invocation_context(
                            claim,
                            operation="repair_verification",
                        ),
                    ),
                    fail_open_on_inconclusive=True,
                ),
            )
        handler = load_stage_handler(
            configured_handler,
            default_handler=default_handler,
        )
        worker = BuilderWorker(
            queue=queue,
            worker_id=worker_id,
            stage_handler=handler,
            heartbeat_interval=heartbeat_interval,
            idle_poll_interval=_positive_float("KAIGO_BUILDER_POLL_SECONDS", "0.5"),
            terminal_hook=(
                make_terminal_hook(
                    settle_run=trial_settlements.settle_run,
                    model_router=model_router,
                )
                if model_router is not None
                else trial_settlements.settle_run
            ),
            terminal_reconciler=trial_settlements.reconcile,
            service_heartbeat=lambda: queue.publish_service_heartbeat(
                worker_id=worker_id,
                boot_id=boot_id,
                deployment_id=deployment_id,
                image_identity=image_identity,
                started_at=worker_started_at,
            ),
        )
        logging.getLogger(__name__).info("starting durable builder worker %s", worker_id)
        install_signal_handlers(worker)
        await worker.run_forever()
    finally:
        try:
            if worker is not None:
                await worker.shutdown()
        finally:
            try:
                if model_router is not None:
                    await model_router.aclose()
            finally:
                await engine.dispose()


def main() -> None:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
