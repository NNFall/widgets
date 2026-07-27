from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.models.contracts import ModelRequest, ModelUsage
from app.models.providers.agentrouter_qwen import AgentRouterQwenProvider
from builder_lab.models import BuilderRequest, EngineName, Stage, WidgetArtifact
from builder_lab.preview import build_preview_document
from builder_lab.prompts import ARTIFACT_JSON_SCHEMA, build_stage_prompt
from builder_lab.validation import validate_artifact


MODEL_PRICES_USD_PER_MILLION = {
    "glm-5.2": (6.0, 6.0),
    "gpt-5.5": (7.0, 7.0),
}


def estimate_cost_usd(model: str, usage: ModelUsage) -> float:
    input_price, output_price = MODEL_PRICES_USD_PER_MILLION[model]
    # The Qwen event stream reports thought tokens as a diagnostic subset of
    # output_tokens. Adding them again would double-bill hidden reasoning.
    return round(
        (usage.input_tokens * input_price + usage.output_tokens * output_price)
        / 1_000_000,
        6,
    )


def add_usage(*items: ModelUsage) -> ModelUsage:
    return ModelUsage(
        input_tokens=sum(item.input_tokens for item in items),
        output_tokens=sum(item.output_tokens for item in items),
        thinking_tokens=sum(item.thinking_tokens for item in items),
    )


def _benchmark_instruction(prompt: str) -> str:
    return (
        "Это независимый сравнительный прогон Kaigo. Создай полностью рабочий, "
        "выразительный чат-виджет, а не промежуточный набросок. Все видимые "
        "пользователю тексты должны быть на русском языке.\n\n" + prompt
    )


def _load_existing_attempt(
    output_dir: Path,
) -> tuple[WidgetArtifact | None, ModelUsage, float, list[dict[str, Any]]]:
    artifact_path = output_dir / "artifact.json"
    if not artifact_path.exists():
        return None, ModelUsage(), 0.0, []
    artifact = WidgetArtifact.from_dict(
        json.loads(artifact_path.read_text(encoding="utf-8"))
    )
    report_path = output_dir / "report.json"
    if not report_path.exists():
        return artifact, ModelUsage(), 0.0, []
    report = json.loads(report_path.read_text(encoding="utf-8"))
    usage = ModelUsage(**report.get("usage", {}))
    elapsed = float(report.get("elapsed_seconds", 0.0))
    attempts = list(report.get("attempts", []))
    if not attempts:
        attempts.append(
            {
                "attempt": 1,
                "kind": "generation",
                "elapsed_seconds": elapsed,
                "usage": asdict(usage),
                "request_id": report.get("request_id"),
                "validation_issue_count": len(validate_artifact(artifact)),
            }
        )
    return artifact, usage, elapsed, attempts


async def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    api_key = os.environ.get("AGENTROUTER_API_KEY", "")
    if not api_key:
        raise RuntimeError("AGENTROUTER_API_KEY is required")
    reference_context = Path(args.reference_context).read_text(encoding="utf-8")
    builder_request = BuilderRequest(
        engine=EngineName.DIRECT,
        source_url=args.source_url,
        brief=args.brief,
        reference_context=reference_context,
        locale="ru",
        creativity=0.9,
        max_repairs=3,
        visual_repair_limit=8,
    )
    provider = AgentRouterQwenProvider(
        api_key=api_key,
        timeout_seconds=args.timeout,
        working_directory=args.harness_directory,
    )
    output_dir = Path(args.output_dir) / args.model
    output_dir.mkdir(parents=True, exist_ok=True)

    artifact: WidgetArtifact | None = None
    accumulated_usage = ModelUsage()
    elapsed_seconds = 0.0
    attempts: list[dict[str, Any]] = []
    if args.repair_existing:
        artifact, accumulated_usage, elapsed_seconds, attempts = _load_existing_attempt(
            output_dir
        )
    issues = validate_artifact(artifact) if artifact else ()
    first_revision = artifact.revision + 1 if artifact else 1
    call_count = args.repair_attempts if artifact else args.repair_attempts + 1

    for offset in range(call_count):
        if artifact is not None and not issues:
            break
        revision = first_revision + offset
        prompt = _benchmark_instruction(
            build_stage_prompt(
                request=builder_request,
                stage=Stage.FOUNDATION,
                revision=revision,
                previous_artifact=artifact,
                repair_issues=issues,
            )
        )
        started = time.perf_counter()
        response = await provider.generate(
            ModelRequest(
                prompt=prompt,
                response_schema=ARTIFACT_JSON_SCHEMA,
                temperature=0.9,
                metadata={"benchmark": args.benchmark_id, "attempt": revision},
            ),
            model=args.model,
        )
        attempt_elapsed = round(time.perf_counter() - started, 3)
        elapsed_seconds = round(elapsed_seconds + attempt_elapsed, 3)
        accumulated_usage = add_usage(accumulated_usage, response.usage)
        if not isinstance(response.parsed, dict):
            raise RuntimeError("model response did not contain a JSON artifact")
        previous_revision = artifact.revision if artifact else 0
        artifact = WidgetArtifact.from_dict(response.parsed)
        issues = validate_artifact(artifact, previous_revision=previous_revision)
        attempt_number = len(attempts) + 1
        (output_dir / f"artifact-attempt-{attempt_number}.json").write_text(
            json.dumps(artifact.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (output_dir / f"preview-attempt-{attempt_number}.html").write_text(
            build_preview_document(artifact), encoding="utf-8"
        )
        attempts.append(
            {
                "attempt": attempt_number,
                "kind": "repair" if previous_revision else "generation",
                "elapsed_seconds": attempt_elapsed,
                "usage": asdict(response.usage),
                "request_id": response.request_id,
                "validation_issue_count": len(issues),
                "validation_issues": [issue.to_dict() for issue in issues],
                "change_summary": artifact.change_summary,
            }
        )

    if artifact is None:
        raise RuntimeError("benchmark did not produce an artifact")
    artifact_path = output_dir / "artifact.json"
    preview_path = output_dir / "preview.html"
    report_path = output_dir / "report.json"
    artifact_path.write_text(
        json.dumps(artifact.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    preview_path.write_text(build_preview_document(artifact), encoding="utf-8")
    report = {
        "benchmark_id": args.benchmark_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "provider": "agentrouter-qwen",
        "model": args.model,
        "source_url": args.source_url,
        "brief": args.brief,
        "elapsed_seconds": elapsed_seconds,
        "usage": asdict(accumulated_usage),
        "estimated_cost_usd": estimate_cost_usd(args.model, accumulated_usage),
        "estimated_cost_rub": round(
            estimate_cost_usd(args.model, accumulated_usage) * args.usd_to_rub, 2
        ),
        "pricing": {
            "input_usd_per_million": MODEL_PRICES_USD_PER_MILLION[args.model][0],
            "output_usd_per_million": MODEL_PRICES_USD_PER_MILLION[args.model][1],
            "source": "AgentRouter console pricing observed 2026-07-28",
        },
        "request_ids": [attempt.get("request_id") for attempt in attempts],
        "attempts": attempts,
        "validation_passed": not issues,
        "validation_issues": [issue.to_dict() for issue in issues],
        "artifact_path": str(artifact_path),
        "preview_path": str(preview_path),
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model", required=True, choices=sorted(MODEL_PRICES_USD_PER_MILLION)
    )
    parser.add_argument("--benchmark-id", default="flowwow-pure-model-v1")
    parser.add_argument("--source-url", default="https://about.flowwow.com/")
    parser.add_argument(
        "--brief",
        default=(
            "Виджет в виде цветочка: живой AI-флорист подсказывает подарок по "
            "поводу и бюджету и говорит естественно, без ощущения робота."
        ),
    )
    parser.add_argument(
        "--reference-context", default="benchmarks/agentrouter/flowwow-context.txt"
    )
    parser.add_argument(
        "--output-dir",
        default="benchmarks/agentrouter/results/flowwow-pure-model-v1",
    )
    parser.add_argument("--harness-directory", default="benchmarks/agentrouter/harness")
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--usd-to-rub", type=float, default=100.0)
    parser.add_argument("--repair-attempts", type=int, default=2)
    parser.add_argument("--repair-existing", action="store_true")
    return parser.parse_args()


def main() -> int:
    report = asyncio.run(run_benchmark(parse_args()))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
