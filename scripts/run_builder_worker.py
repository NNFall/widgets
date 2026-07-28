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
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.providers.gemini import GeminiModelProvider
from app.models.router import (
    ModelPolicy,
    ModelRouter,
    ProviderTarget,
    SqlModelCallAudit,
)

from builder_lab.config import BuilderLabConfig
from builder_lab.browser_audit import BrowserAudit
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


def _nonnegative_int(name: str, default: str = "0") -> int:
    try:
        value = int(os.getenv(name, default))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value < 0:
        raise RuntimeError(f"{name} must not be negative")
    return value


def make_runtime_model_router(config, factory) -> ModelRouter:
    if not config.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is required for routed builder stages")
    input_rate = _nonnegative_int(
        "GEMINI_INPUT_PRICE_MICROUSD_PER_MILLION"
    )
    output_rate = _nonnegative_int(
        "GEMINI_OUTPUT_PRICE_MICROUSD_PER_MILLION"
    )
    policies = {}
    for mode in ("direct", "express"):
        policy = get_mode_policy(mode)
        for role in set(policy.model_roles.values()):
            policies[(role, policy.name)] = ModelPolicy(
                prompt_version="builder-v1",
                targets=(
                    ProviderTarget(
                        "gemini",
                        config.direct_model,
                        input_rate,
                        output_rate,
                    ),
                ),
            )
        for role in (*policy.critic_roles, policy.judge_role):
            if role is None:
                continue
            policies[(role, policy.name)] = ModelPolicy(
                prompt_version="visual-v1",
                targets=(
                    ProviderTarget(
                        "gemini",
                        config.visual_critic_model,
                        input_rate,
                        output_rate,
                    ),
                ),
            )
    return ModelRouter(
        providers={
            "gemini": GeminiModelProvider(
                api_key=config.gemini_api_key,
                base_url=config.gemini_base_url,
            )
        },
        policies=policies,
        audit=SqlModelCallAudit(factory),
    )


async def run() -> None:
    engine = create_async_engine(_database_url(), future=True, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    lease_seconds = _positive_float("KAIGO_BUILDER_LEASE_SECONDS", "90")
    heartbeat_interval = _positive_float(
        "KAIGO_BUILDER_HEARTBEAT_SECONDS", "20"
    )
    if heartbeat_interval >= lease_seconds:
        await engine.dispose()
        raise RuntimeError(
            "KAIGO_BUILDER_HEARTBEAT_SECONDS must be shorter than the lease"
        )
    worker_id = os.getenv("KAIGO_BUILDER_WORKER_ID", "").strip() or (
        f"{socket.gethostname()}-{os.getpid()}"
    )
    queue = PostgresWorkerQueue(factory, lease_seconds=lease_seconds)
    configured_handler = (
        os.getenv("KAIGO_BUILDER_STAGE_HANDLER", "").strip()
        or BUILTIN_STAGE_HANDLER
    )
    default_handler = None
    if configured_handler == BUILTIN_STAGE_HANDLER:
        config = BuilderLabConfig.from_env()
        model_router = make_runtime_model_router(config, factory)
        engine_factories = make_engine_factories(config)
        if not engine_factories:
            await engine.dispose()
            raise RuntimeError(
                "GEMINI_API_KEY (or GOOGLE_AI_API_KEY) is required for the builder worker"
            )
        reference_pipeline = GeminiReferencePipeline.from_config(config)
        default_handler = OrchestratorStageHandler(
            queue=queue,
            engine_factories=engine_factories,
            reference_analyzer=reference_pipeline.analyze,
            routed_engine_factory=lambda claim, role: GeminiDirectEngine(
                model_router=model_router,
                routing_role=role,
                routing_mode=get_mode_policy(claim.mode).name,
                run_id=claim.run_id,
                model=config.direct_model,
                thinking_level=config.builder_thinking_level,
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
                ),
                verifier_factory=lambda: GeminiRepairVerifier(
                    api_key=config.gemini_api_key,
                    model=config.visual_critic_model,
                    thinking_level=config.visual_critic_thinking_level,
                    base_url=config.gemini_base_url,
                    timeout_seconds=config.visual_critic_timeout_seconds,
                ),
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
    )
    logging.getLogger(__name__).info("starting durable builder worker %s", worker_id)
    install_signal_handlers(worker)
    try:
        await worker.run_forever()
    finally:
        await worker.shutdown()
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
