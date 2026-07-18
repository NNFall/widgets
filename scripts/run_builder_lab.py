from __future__ import annotations

import logging
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiohttp import web
from dotenv import load_dotenv

from builder_lab.config import BuilderLabConfig
from builder_lab.engines.antigravity import AntigravityEngine
from builder_lab.engines.gemini_direct import GeminiDirectEngine
from builder_lab.models import EngineName
from builder_lab.orchestrator import BuilderOrchestrator
from builder_lab.store import RunStore
from builder_lab.web import create_builder_lab_app


def make_engine_factories(config: BuilderLabConfig):
    factories = {}
    if config.gemini_api_key:
        factories[EngineName.DIRECT] = lambda: GeminiDirectEngine(
            api_key=config.gemini_api_key,
            model=config.direct_model,
            base_url=config.gemini_base_url,
        )
        if config.enable_antigravity:
            factories[EngineName.ANTIGRAVITY] = lambda: AntigravityEngine(
                api_key=config.gemini_api_key,
                agent=config.antigravity_agent,
                base_url=config.gemini_base_url,
                timeout_seconds=config.antigravity_timeout_seconds,
                max_snapshot_bytes=config.antigravity_max_snapshot_bytes,
            )
    return factories


def build_app(config: BuilderLabConfig) -> web.Application:
    factories = make_engine_factories(config)
    if not factories:
        raise RuntimeError(
            "GEMINI_API_KEY (or GOOGLE_AI_API_KEY) is required to start builder-lab"
        )
    store = RunStore(
        ttl_seconds=config.run_ttl_seconds,
        max_runs=config.max_runs,
    )
    orchestrator = BuilderOrchestrator(
        store=store,
        engine_factories=factories,
    )
    return create_builder_lab_app(
        store=store,
        orchestrator=orchestrator,
        enabled_engines=tuple(factories),
        default_engine=config.default_engine,
        default_temperature=config.temperature,
        default_max_repairs=config.max_repairs,
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
