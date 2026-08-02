from __future__ import annotations

import logging
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiohttp import web
from dotenv import load_dotenv

from app.models.lineage import ModelInvocationContext
from builder_lab.config import BuilderLabConfig
from builder_lab.browser_audit import BrowserAudit
from builder_lab.chat import GeminiDemoChatService
from builder_lab.engines.antigravity import AntigravityEngine
from builder_lab.engines.gemini_direct import GeminiDirectEngine
from builder_lab.models import EngineName
from builder_lab.modes import BuildModePolicy, get_mode_policy
from builder_lab.orchestrator import BuilderOrchestrator
from builder_lab.reference_pipeline import GeminiReferencePipeline
from builder_lab.store import RunStore
from builder_lab.visual_committee import VisualCriticCommittee
from builder_lab.visual_critic import GeminiVisualCritic, VisualCriticRole
from builder_lab.visual_review import GeminiRepairVerifier, GeminiVisualJudge
from builder_lab.web import (
    CHAT_SECURE_COOKIE_KEY as CHAT_SECURE_COOKIE_KEY,
    CHAT_SERVICE_KEY as CHAT_SERVICE_KEY,
    DEMO_DIR_KEY as DEMO_DIR_KEY,
    DEMO_PATH_KEY as DEMO_PATH_KEY,
    create_builder_lab_app,
)


def make_engine_factories(config: BuilderLabConfig):
    factories = {}
    if config.gemini_api_key:
        factories[EngineName.DIRECT] = lambda: GeminiDirectEngine(
            api_key=config.gemini_api_key,
            model=config.direct_model,
            thinking_level=config.builder_thinking_level,
            base_url=config.gemini_base_url,
        )
        if config.enable_antigravity:
            factories[EngineName.ANTIGRAVITY] = lambda: AntigravityEngine(
                api_key=config.gemini_api_key,
                agent=config.antigravity_agent,
                base_url=config.gemini_base_url,
                timeout_seconds=config.antigravity_timeout_seconds,
                max_snapshot_bytes=config.antigravity_max_snapshot_bytes,
                max_total_tokens=config.antigravity_max_total_tokens,
            )
    return factories


def make_visual_critic_factory(
    config: BuilderLabConfig,
    *,
    policy: BuildModePolicy | None = None,
    model_router=None,
    mode: str = "direct",
    run_id=None,
    invocation_context: ModelInvocationContext | None = None,
):
    selected_policy = policy or get_mode_policy(mode)
    critic_roles = tuple(
        VisualCriticRole(role) for role in selected_policy.critic_roles
    )

    def make_committee() -> VisualCriticCommittee:
        return VisualCriticCommittee(
            {
                role: (
                    lambda role=role: GeminiVisualCritic(
                        api_key=config.gemini_api_key,
                        model=config.visual_critic_model,
                        thinking_level=config.visual_critic_thinking_level,
                        base_url=config.gemini_base_url,
                        timeout_seconds=config.visual_critic_timeout_seconds,
                        routing_timeout_seconds=config.agentrouter_timeout_seconds,
                        role=role,
                        model_router=model_router,
                        routing_mode=selected_policy.name,
                        run_id=run_id,
                        invocation_context=invocation_context,
                    )
                )
                for role in critic_roles
            },
            judge_factory=lambda: GeminiVisualJudge(
                api_key=config.gemini_api_key,
                model=config.visual_critic_model,
                thinking_level=config.visual_critic_thinking_level,
                base_url=config.gemini_base_url,
                timeout_seconds=config.visual_critic_timeout_seconds,
                routing_timeout_seconds=config.agentrouter_timeout_seconds,
                model_router=model_router,
                routing_mode=selected_policy.name,
                routing_role=selected_policy.judge_role or "visual_judge",
                run_id=run_id,
                invocation_context=invocation_context,
            ),
        )

    return make_committee


def build_app(config: BuilderLabConfig) -> web.Application:
    factories = make_engine_factories(config)
    if not factories:
        raise RuntimeError(
            "GEMINI_API_KEY (or GOOGLE_AI_API_KEY) is required to start builder-lab"
        )
    if config.chat_secure_cookie and not config.chat_session_secret:
        raise RuntimeError(
            "KAIGO_CHAT_SESSION_SECRET is required when secure chat cookies are enabled"
        )
    store = RunStore(
        ttl_seconds=config.run_ttl_seconds,
        max_runs=config.max_runs,
    )
    reference_pipeline = GeminiReferencePipeline.from_config(config)
    orchestrator = BuilderOrchestrator(
        store=store,
        engine_factories=factories,
        visual_audit_factory=lambda: BrowserAudit(
            timeout_ms=config.browser_audit_timeout_ms,
            total_timeout_seconds=config.browser_audit_total_timeout_seconds,
        ),
        visual_critic_factory=make_visual_critic_factory(config),
        visual_repair_verifier_factory=lambda: GeminiRepairVerifier(
            api_key=config.gemini_api_key,
            model=config.visual_critic_model,
            thinking_level=config.visual_critic_thinking_level,
            base_url=config.gemini_base_url,
            timeout_seconds=config.visual_critic_timeout_seconds,
        ),
        reference_analyzer=reference_pipeline.analyze,
    )
    chat_service = GeminiDemoChatService(
        api_key=config.gemini_api_key,
        model=config.chat_model,
        thinking_level=config.chat_thinking_level,
        base_url=config.gemini_base_url,
        timeout_seconds=config.chat_timeout_seconds,
        session_ttl_seconds=config.chat_session_ttl_seconds,
        max_sessions=config.chat_max_sessions,
        rate_limit_requests=config.chat_rate_limit_requests,
        ip_rate_limit_requests=config.chat_ip_rate_limit_requests,
        rate_limit_window_seconds=config.chat_rate_limit_window_seconds,
        max_requests_per_session=config.chat_max_requests_per_session,
        global_concurrency=config.chat_global_concurrency,
    )
    return create_builder_lab_app(
        store=store,
        orchestrator=orchestrator,
        enabled_engines=tuple(factories),
        default_engine=config.default_engine,
        default_temperature=config.temperature,
        default_max_repairs=config.max_repairs,
        demo_path=Path(config.demo_path) if config.demo_path else None,
        demo_dir=Path(config.demo_dir) if config.demo_dir else None,
        chat_service=chat_service,
        chat_secure_cookie=config.chat_secure_cookie,
        chat_session_secret=config.chat_session_secret,
    )


def main() -> None:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = BuilderLabConfig.from_env()
    app = build_app(config)
    logging.getLogger(__name__).info(
        "starting Kaigo builder-lab on %s:%s; engines=%s",
        config.host,
        config.port,
        ",".join(engine.value for engine in make_engine_factories(config)),
    )
    web.run_app(
        app,
        host=config.host,
        port=config.port,
        print=None,
        handle_signals=True,
    )


if __name__ == "__main__":
    main()
