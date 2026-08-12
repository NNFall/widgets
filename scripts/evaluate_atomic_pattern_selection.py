from __future__ import annotations

# ruff: noqa: E402

import argparse
import asyncio
import json
import os
import sys
import time
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping
from uuid import UUID, uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.models.lineage import ModelInvocationContext
from app.models.providers.codex_bridge import CodexBridgeProvider
from app.models.router import (
    InMemoryModelCallAudit,
    ModelPolicy,
    ModelRouter,
    ProviderTarget,
)
from builder_lab.directions import run_direction_board
from builder_lab.engines.gemini_direct import GeminiDirectEngine
from builder_lab.models import BuilderRequest, DirectionProposal, EngineName, Stage
from builder_lab.patterns.atomic_registry import load_builtin_atomic_registry
from builder_lab.patterns.candidate_planner import plan_pattern_candidates
from builder_lab.patterns.selection_evaluation import (
    build_multisite_pattern_selection_report,
    build_pattern_selection_allowlist,
    render_pattern_selection_markdown,
    summarize_pattern_selection,
)


_ROLES = (
    "persona_selector",
    "direction_candidate",
    "direction_judge",
    "composition_planner",
)


def _load_json(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain one JSON object")
    return payload


def _load_review_states(path: Path | None) -> dict[tuple[str, int], str]:
    if path is None:
        return {}
    payload = _load_json(path)
    if payload.get("schema_version") != 1 or not isinstance(
        payload.get("decisions"), list
    ):
        raise ValueError("review decisions contract is invalid")
    decisions: dict[tuple[str, int], str] = {}
    for item in payload["decisions"]:
        if not isinstance(item, Mapping):
            raise ValueError("review decision must be an object")
        key = (str(item["pattern_id"]), int(item["version"]))
        state = str(item["state"])
        if state not in {"approved", "rejected"} or key in decisions:
            raise ValueError("review decision is invalid or duplicated")
        decisions[key] = state
    return decisions


def _load_cases(path: Path) -> tuple[Mapping[str, Any], ...]:
    payload = _load_json(path)
    cases = payload.get("cases")
    if payload.get("schema_version") != 1 or not isinstance(cases, list) or not cases:
        raise ValueError("multisite cases contract is invalid")
    if any(not isinstance(item, Mapping) for item in cases):
        raise ValueError("each multisite case must be an object")
    return tuple(cases)


def _request(case: Mapping[str, Any]) -> BuilderRequest:
    return BuilderRequest(
        engine=EngineName.DIRECT,
        source_url=str(case["source_url"]),
        brief=str(case["brief"]),
        reference_context=str(case["reference_context"]),
        locale="ru",
        creativity=0.7,
        max_repairs=0,
        visual_repair_limit=0,
    )


def _engine(
    *,
    router: ModelRouter,
    role: str,
    run_id: UUID,
    stage: Stage,
    timeout_seconds: float,
) -> GeminiDirectEngine:
    return GeminiDirectEngine(
        model_router=router,
        routing_role=role,
        routing_mode="direct",
        routing_timeout_seconds=timeout_seconds,
        run_id=run_id,
        invocation_context=ModelInvocationContext(
            stage_attempt_id=None,
            stage=stage.value,
            operation=role,
        ),
        model="codex-bridge",
        thinking_level="medium",
    )


async def _evaluate_case(
    *,
    case: Mapping[str, Any],
    router: ModelRouter,
    provider: CodexBridgeProvider,
    registry,
    allowed: frozenset[tuple[str, int]],
    run_live_persona: bool,
    run_live_direction: bool,
    timeout_seconds: float,
) -> dict[str, Any]:
    run_id = uuid4()
    request = _request(case)
    persona_stage: dict[str, Any] | None = None
    direction_stage: dict[str, Any] | None = None
    started_case = time.monotonic()
    try:
        if run_live_persona:
            persona_engine = _engine(
                router=router,
                role="persona_selector",
                run_id=run_id,
                stage=Stage.ART_DIRECTION,
                timeout_seconds=timeout_seconds,
            )
            started = time.monotonic()
            persona_result = await persona_engine.select_assistant_persona(request=request)
            persona_stage = {
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "usage": persona_result.usage.to_dict(),
                "persona": persona_result.persona.to_dict(),
                "provider_request_id": persona_result.provider_request_id,
            }
            request = replace(request, assistant_persona=persona_result.persona)

        if run_live_direction:
            proposal_engine = _engine(
                router=router,
                role="direction_candidate",
                run_id=run_id,
                stage=Stage.ART_DIRECTION,
                timeout_seconds=timeout_seconds,
            )
            judge_engine = _engine(
                router=router,
                role="direction_judge",
                run_id=run_id,
                stage=Stage.ART_DIRECTION,
                timeout_seconds=timeout_seconds,
            )
            started = time.monotonic()
            board = await run_direction_board(
                proposal_engine=proposal_engine,
                judge_engine=judge_engine,
                request=request,
            )
            direction_stage = {
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "usage": board.usage.to_dict(),
                "selected": board.selected.to_dict(),
                "judgement": board.judgement.to_dict(),
                "proposals": [item.to_dict() for item in board.proposals],
            }
            selected_direction = board.selected
        else:
            selected_direction = DirectionProposal.from_dict(case["selected_direction"])

        selector_engine = _engine(
            router=router,
            role="composition_planner",
            run_id=run_id,
            stage=Stage.COMPOSITION,
            timeout_seconds=timeout_seconds,
        )
        active_catalog = tuple(
            definition.selector_dict()
            for definition in registry.definitions
            if definition.status.value == "active"
        )
        started = time.monotonic()
        result = await plan_pattern_candidates(
            selector_engine,
            request,
            selected_direction,
            registry,
            selector_catalog=active_catalog,
            effective_approved=allowed,
        )
        selector_elapsed = time.monotonic() - started
        summary = summarize_pattern_selection(
            case_id=str(case["case_id"]),
            source_url=str(case["source_url"]),
            result=result,
            registry=registry,
            effective_approved=allowed,
            elapsed_seconds=selector_elapsed,
        )
        summary["assistant_persona_stage"] = persona_stage
        summary["direction_stage"] = direction_stage
        summary["selected_direction"] = selected_direction.to_dict()
        summary["pipeline_elapsed_seconds"] = round(time.monotonic() - started_case, 3)
        return summary
    finally:
        # Stage-only test threads are archived immediately and never become
        # long-lived user generation histories.
        with suppress(Exception):
            await provider.finalize_run(str(run_id))


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    cases = _load_cases(args.cases)
    registry = load_builtin_atomic_registry()
    review_states = _load_review_states(args.review_decisions)
    allowed = build_pattern_selection_allowlist(
        registry,
        effective_review_states=review_states,
        include_ready_for_review=args.include_ready_for_review,
    )
    if not allowed:
        raise RuntimeError("pattern selector allowlist is empty")

    provider = CodexBridgeProvider(
        socket_path=args.socket_path,
        timeout_seconds=args.timeout_seconds,
    )
    target = ProviderTarget("codex_bridge", args.model, None, None)
    policies = {
        (role, "direct"): ModelPolicy(
            prompt_version="pattern-selection-eval-v1",
            targets=(target,),
        )
        for role in _ROLES
    }
    router = ModelRouter(
        providers={"codex_bridge": provider},
        policies=policies,
        audit=InMemoryModelCallAudit(),
    )
    try:
        summaries = []
        for case in cases:
            summaries.append(
                await _evaluate_case(
                    case=case,
                    router=router,
                    provider=provider,
                    registry=registry,
                    allowed=allowed,
                    run_live_persona=args.live_persona,
                    run_live_direction=args.live_direction,
                    timeout_seconds=args.timeout_seconds,
                )
            )
        report = build_multisite_pattern_selection_report(tuple(summaries))
        report["selection_mode"] = (
            "approved_plus_ready" if args.include_ready_for_review else "approved_only"
        )
        report["allowed_exact_versions"] = len(allowed)
        return report
    finally:
        await router.aclose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run bounded Kaigo persona/direction/pattern-selection stages."
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=REPO_ROOT / "data/pattern-selection/multisite-cases-v1.json",
    )
    parser.add_argument(
        "--review-decisions",
        type=Path,
        default=REPO_ROOT
        / "data/pattern-selection/user-review-decisions-2026-08-07.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "output/pattern-selection-eval-2026-08-07",
    )
    parser.add_argument(
        "--socket-path",
        default=os.getenv("KAIGO_CODEX_BRIDGE_SOCKET_PATH", "/run/kaigo-codex/bridge.sock"),
    )
    parser.add_argument(
        "--model",
        default=os.getenv("KAIGO_CODEX_BRIDGE_MODEL", "gpt-5.6-luna"),
    )
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    parser.add_argument("--include-ready-for-review", action="store_true")
    parser.add_argument("--live-persona", action="store_true")
    parser.add_argument("--live-direction", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    report = asyncio.run(_run(args))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "report.private.json"
    markdown_path = args.output_dir / "report.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_pattern_selection_markdown(report),
        encoding="utf-8",
    )
    print(json_path)
    print(markdown_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
