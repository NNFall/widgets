from __future__ import annotations

# The script must remain directly executable from ``scripts/`` while importing
# the repository packages below.
# ruff: noqa: E402

import argparse
import asyncio
import hashlib
import json
import math
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.models.contracts import (
    BilledModelProviderError,
    ModelProviderError,
    ModelRequest,
    ModelUsage,
)
from app.models.providers.agentrouter_qwen import AgentRouterQwenProvider
from builder_lab.browser_audit import BrowserAudit, BrowserAuditError
from builder_lab.models import (
    BuilderRequest,
    DirectionProposal,
    EngineName,
    Stage,
    ValidationIssue,
    WidgetArtifact,
)
from builder_lab.patterns.models import CompositionPlan
from builder_lab.patterns.registry import load_builtin_registry
from builder_lab.patterns.resolver import resolve_composition
from builder_lab.preview import build_preview_document
from builder_lab.prompts import ARTIFACT_JSON_SCHEMA, build_stage_prompt
from builder_lab.validation import validate_artifact
from builder_lab.strict_visual_models import (
    STRICT_VISUAL_DIMENSIONS,
    STRICT_VISUAL_WEIGHTS,
    StrictVisualDimension,
)
from builder_lab.visual_models import (
    NormalizedRegion,
    VisualCategory,
    VisualFinding,
    VisualSeverity,
)


DEFAULT_PRICES: Mapping[str, tuple[float, float]] = {
    "glm-5.2": (6.0, 6.0),
    "gpt-5.5": (7.0, 7.0),
}
_VISUAL_DIMENSIONS = tuple(item.value for item in STRICT_VISUAL_DIMENSIONS)


def build_report(
    *,
    evidence: bytes,
    composition: bytes,
    visual_review: bytes | None = None,
    runs: Sequence[Mapping[str, Any]],
    usd_to_rub: float,
) -> dict[str, Any]:
    if not math.isfinite(usd_to_rub) or usd_to_rub <= 0:
        raise ValueError("usd_to_rub must be positive and finite")
    review = _parse_visual_review(visual_review, runs=runs)
    reviewed_runs = []
    for run in runs:
        reviewed = dict(run)
        if review is not None:
            model = _bounded_text(run.get("model"), "model", 128)
            reviewed["visual_score"] = review["scores"][model]
        reviewed_runs.append(reviewed)
    public_runs = tuple(
        _public_run(run, usd_to_rub=usd_to_rub) for run in reviewed_runs
    )
    input_identity = {
        "evidence_sha256": hashlib.sha256(evidence).hexdigest(),
        "composition_sha256": hashlib.sha256(composition).hexdigest(),
    }
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "input_identity": input_identity,
        "usd_to_rub": usd_to_rub,
        "runs": list(public_runs),
    }
    if review is not None:
        input_identity["visual_review_sha256"] = hashlib.sha256(
            visual_review or b""
        ).hexdigest()
        report["visual_evaluation"] = {
            "source": review["source"],
            "rubric": review["rubric"],
        }
    return report


def _parse_visual_review(
    raw: bytes | None,
    *,
    runs: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("visual review must be UTF-8 JSON") from exc
    if not isinstance(payload, Mapping) or payload.get("schema_version") != 1:
        raise ValueError("visual review schema_version must be one")
    source = _bounded_text(payload.get("source"), "visual review source", 80)
    rubric = _bounded_text(payload.get("rubric"), "visual review rubric", 80)
    models = payload.get("models")
    if not isinstance(models, Mapping):
        raise ValueError("visual review models must be an object")
    scores: dict[str, float] = {}
    for run in runs:
        model = _bounded_text(run.get("model"), "model", 128)
        row = models.get(model)
        if not isinstance(row, Mapping):
            raise ValueError(f"visual review does not cover {model}")
        assessments = row.get("assessments")
        if not isinstance(assessments, Mapping) or set(assessments) != set(
            _VISUAL_DIMENSIONS
        ):
            raise ValueError(f"visual review rubric is incomplete for {model}")
        rubric_scores: dict[StrictVisualDimension, int] = {}
        for name in _VISUAL_DIMENSIONS:
            value = assessments.get(name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= 5
            ):
                raise ValueError(f"visual review rubric score is invalid for {model}")
            rubric_scores[StrictVisualDimension(name)] = value
        observable = {
            dimension: value
            for dimension, value in rubric_scores.items()
            if value > 0
        }
        if not observable:
            derived_weighted = 0.0
        else:
            derived_weighted = sum(
                value * STRICT_VISUAL_WEIGHTS[dimension]
                for dimension, value in observable.items()
            ) / sum(STRICT_VISUAL_WEIGHTS[dimension] for dimension in observable)
        weighted_score = row.get("weighted_score")
        if (
            isinstance(weighted_score, bool)
            or not isinstance(weighted_score, (int, float))
            or not math.isclose(
                float(weighted_score), derived_weighted, rel_tol=0, abs_tol=1e-9
            )
        ):
            raise ValueError(f"visual review weighted score is not derived for {model}")
        score = row.get("normalized_score")
        if (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(float(score))
            or not 0 <= float(score) <= 1
        ):
            raise ValueError(f"visual review score is invalid for {model}")
        if not math.isclose(
            float(score), derived_weighted / 5, rel_tol=0, abs_tol=1e-9
        ):
            raise ValueError(f"visual review derived score is invalid for {model}")
        scores[model] = float(score)
    return {"source": source, "rubric": rubric, "scores": scores}


def _public_run(run: Mapping[str, Any], *, usd_to_rub: float) -> dict[str, Any]:
    usage = _usage(run.get("usage"))
    pricing = _pricing(run.get("pricing"))
    cost_usd = round(
        (
            usage["input_tokens"] * pricing["input_usd_per_million"]
            + usage["output_tokens"] * pricing["output_usd_per_million"]
        )
        / 1_000_000,
        6,
    )
    visual_score = run.get("visual_score")
    if visual_score is not None:
        if (
            isinstance(visual_score, bool)
            or not isinstance(visual_score, (int, float))
            or not math.isfinite(float(visual_score))
            or not 0 <= float(visual_score) <= 1
        ):
            raise ValueError("visual_score must be between zero and one")
        visual_score = float(visual_score)
    request_ids = run.get("request_ids", ())
    if not isinstance(request_ids, (list, tuple)) or any(
        not isinstance(value, str) or not value or len(value) > 255
        for value in request_ids
    ):
        raise ValueError("request_ids are invalid")
    return {
        "provider": _bounded_text(run.get("provider"), "provider", 64),
        "model": _bounded_text(run.get("model"), "model", 128),
        "elapsed_seconds": _nonnegative_float(
            run.get("elapsed_seconds", 0), "elapsed_seconds"
        ),
        "usage": usage,
        "cost_usd": cost_usd,
        "cost_rub": round(cost_usd * usd_to_rub, 2),
        "retry_count": _nonnegative_int(run.get("retry_count", 0), "retry_count"),
        "repair_count": _nonnegative_int(
            run.get("repair_count", 0), "repair_count"
        ),
        "browser_repair_count": _nonnegative_int(
            run.get("browser_repair_count", 0),
            "browser_repair_count",
        ),
        "visual_score": visual_score,
        "validator_passed": _strict_bool(
            run.get("validator_passed", False), "validator_passed"
        ),
        "browser_passed": _strict_bool(
            run.get("browser_passed", False), "browser_passed"
        ),
        "publishable": _strict_bool(
            run.get("publishable", False), "publishable"
        ),
        "failure_code": (
            _bounded_text(run["failure_code"], "failure_code", 80)
            if run.get("failure_code") is not None
            else None
        ),
        "request_ids": list(request_ids),
        "pricing": pricing,
    }


def _usage(raw: object) -> dict[str, int]:
    if not isinstance(raw, Mapping):
        raise ValueError("usage must be an object")
    return {
        name: _nonnegative_int(raw.get(name, 0), name)
        for name in ("input_tokens", "output_tokens", "thinking_tokens")
    }


def _pricing(raw: object) -> dict[str, float]:
    if not isinstance(raw, Mapping):
        raise ValueError("pricing must be an object")
    return {
        name: _nonnegative_float(raw.get(name), name)
        for name in ("input_usd_per_million", "output_usd_per_million")
    }


def _bounded_text(raw: object, name: str, maximum: int) -> str:
    if not isinstance(raw, str):
        raise ValueError(f"{name} must be text")
    value = raw.strip()
    if not value or len(value) > maximum or "\x00" in value:
        raise ValueError(f"{name} is invalid")
    return value


def _nonnegative_int(raw: object, name: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return raw


def _nonnegative_float(raw: object, name: str) -> float:
    if (
        isinstance(raw, bool)
        or not isinstance(raw, (int, float))
        or not math.isfinite(float(raw))
        or float(raw) < 0
    ):
        raise ValueError(f"{name} must be a non-negative finite number")
    return float(raw)


def _strict_bool(raw: object, name: str) -> bool:
    if not isinstance(raw, bool):
        raise ValueError(f"{name} must be a boolean")
    return raw


def render_markdown(report: Mapping[str, Any]) -> str:
    rows = report["runs"]
    lines = [
        "# Сравнение моделей Kaigo Agent Kernel",
        "",
        "Обе модели получили один и тот же frozen evidence bundle и composition plan.",
        "",
        "| Модель | Время, с | Токены | Стоимость, ₽ | Retry | Repairs | Browser repairs | Visual | Publishable | Ошибка |",
        "|---|---:|---:|---:|---:|---:|---:|---:|:---:|---|",
    ]
    for row in rows:
        # thinking_tokens is a diagnostic subset of output_tokens for this
        # provider, so adding it again would overstate both usage and cost.
        total = row["usage"]["input_tokens"] + row["usage"]["output_tokens"]
        score = "—" if row["visual_score"] is None else f"{row['visual_score']:.2f}"
        lines.append(
            f"| {row['model']} | {row['elapsed_seconds']:.2f} | {total} | "
            f"{row['cost_rub']:.2f} | {row['retry_count']} | "
            f"{row['repair_count']} | {row['browser_repair_count']} | {score} | "
            f"{'да' if row['publishable'] else 'нет'} | "
            f"{row['failure_code'] or '—'} |"
        )
    evaluation = report.get("visual_evaluation")
    if isinstance(evaluation, Mapping):
        evaluation_lines = [
            "",
            "Visual score получен одним независимым оценщиком для обеих моделей: "
            f"`{evaluation['source']}`, рубрика `{evaluation['rubric']}`.",
            f"Visual review SHA-256: `{report['input_identity']['visual_review_sha256']}`",
        ]
    else:
        evaluation_lines = [
            "",
            "Visual score не указан: отдельный общий визуальный оценщик не запускался.",
        ]
    lines.extend(
        [
            *evaluation_lines,
            "",
            f"Evidence SHA-256: `{report['input_identity']['evidence_sha256']}`",
            f"Composition SHA-256: `{report['input_identity']['composition_sha256']}`",
            "",
            "Полные prompts, исходный текст сайта, API-ключи и authorization headers в отчёт не записываются.",
        ]
    )
    return "\n".join(lines) + "\n"


def _load_frozen_input(evidence_bytes: bytes, composition_bytes: bytes):
    evidence = json.loads(evidence_bytes.decode("utf-8"))
    composition = json.loads(composition_bytes.decode("utf-8"))
    if not isinstance(evidence, dict) or not isinstance(composition, dict):
        raise ValueError("frozen inputs must be JSON objects")
    request = BuilderRequest(
        engine=EngineName.DIRECT,
        source_url=str(evidence.get("source_url", "")),
        brief=str(evidence["brief"]),
        reference_context=str(evidence["reference_context"]),
        locale=str(evidence.get("locale", "ru")),
        creativity=float(evidence.get("creativity", 0.9)),
        max_repairs=int(evidence.get("max_repairs", 3)),
        visual_repair_limit=int(evidence.get("visual_repair_limit", 8)),
    )
    direction = DirectionProposal.from_dict(evidence["direction"])
    plan = CompositionPlan.from_dict(composition)
    resolved = resolve_composition(plan, load_builtin_registry())
    if plan.direction_id != direction.proposal_id:
        raise ValueError("composition direction does not match frozen evidence")
    return request, direction, resolved


def _browser_failure_finding(
    error: BrowserAuditError,
    *,
    repair_number: int,
) -> VisualFinding:
    failures = error.failures or ((error.diagnostic or str(error)),)
    evidence = "\n".join(failures)
    if len(evidence) > 1_000:
        evidence = evidence[:997].rstrip() + "..."
    return VisualFinding(
        finding_id=f"browser-repair-{repair_number}",
        severity=VisualSeverity.BLOCKER,
        category=VisualCategory.RESPONSIVE_INTEGRITY,
        screenshot_id="desktop.open_initial",
        evidence=evidence,
        region=NormalizedRegion(
            x=0,
            y=0,
            width=1,
            height=1,
            semantic_region="panel",
        ),
        artifact_fields=("body_html", "css"),
        repair_instruction=(
            "Исправь все перечисленные браузером геометрические нарушения. "
            "Ни декоративные, ни интерактивные элементы не должны создавать "
            "горизонтальное переполнение или выходить за панель и viewport. Для "
            "каждого обязательного региона обеспечь scrollWidth <= clientWidth; "
            "overflow:hidden или overflow:clip сами по себе не исправляют эту "
            "метрику. Псевдоэлементы с отрицательными left/right и широкой "
            "декорацией перенеси внутрь неотрицательных границ либо замени фоном. "
            "Удали измеряемую цель 1×1 px: сохрани доступное имя поля через "
            "aria-label либо корректно исключи скрытый label из layout. Не меняй "
            "концепцию, тексты диалога и выбранные паттерны."
        ),
        confidence=1.0,
    )


async def run_target(
    *,
    target: str,
    evidence_bytes: bytes,
    composition_bytes: bytes,
    private_dir: Path,
    timeout_seconds: float,
    repair_limit: int,
    browser_repair_limit: int = 0,
    seed_artifact: WidgetArtifact | None = None,
    seed_run: Mapping[str, Any] | None = None,
    audit_factory: Callable[[], Any] = BrowserAudit,
) -> dict[str, Any]:
    provider_name, separator, model = target.partition(":")
    if separator != ":" or provider_name != "agentrouter":
        raise ValueError("only agentrouter:<model> targets are supported")
    if model not in DEFAULT_PRICES:
        raise ValueError(f"pricing is not configured for {model}")
    api_key = os.environ.get("AGENTROUTER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("AGENTROUTER_API_KEY is required")
    request, direction, composition = _load_frozen_input(
        evidence_bytes,
        composition_bytes,
    )
    provider = AgentRouterQwenProvider(
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        working_directory=private_dir,
    )
    private_dir.mkdir(parents=True, exist_ok=True)
    if seed_run is not None and seed_artifact is None:
        raise ValueError("seed_artifact is required with seed_run")
    artifact = seed_artifact
    issues: tuple[ValidationIssue, ...] = (
        validate_artifact(seed_artifact) if seed_artifact is not None else ()
    )
    if seed_run is None:
        usage = ModelUsage()
        request_ids: list[str] = []
        elapsed_before = 0.0
        retry_count = 0
        repair_count = 0
        browser_repair_count = 0
    else:
        raw_usage = _usage(seed_run.get("usage"))
        usage = ModelUsage(**raw_usage)
        raw_request_ids = seed_run.get("request_ids", ())
        if not isinstance(raw_request_ids, (list, tuple)) or any(
            not isinstance(value, str) for value in raw_request_ids
        ):
            raise ValueError("seed request_ids are invalid")
        request_ids = list(raw_request_ids)
        elapsed_before = _nonnegative_float(
            seed_run.get("elapsed_seconds", 0),
            "seed elapsed_seconds",
        )
        retry_count = _nonnegative_int(
            seed_run.get("retry_count", 0),
            "seed retry_count",
        )
        repair_count = _nonnegative_int(
            seed_run.get("repair_count", 0),
            "seed repair_count",
        )
        browser_repair_count = _nonnegative_int(
            seed_run.get("browser_repair_count", 0),
            "seed browser_repair_count",
        )
    started = time.perf_counter()
    failure_code: str | None = None

    async def generate(
        prompt: str,
        *,
        role: str,
        temperature: float,
        metadata: Mapping[str, Any],
        error_kind: str,
        error_attempt: int,
    ):
        nonlocal usage, retry_count, failure_code
        try:
            response = await provider.generate(
                ModelRequest(
                    prompt=prompt,
                    response_schema=ARTIFACT_JSON_SCHEMA,
                    temperature=temperature,
                    metadata=metadata,
                ),
                model=model,
            )
        except BilledModelProviderError as error:
            usage = ModelUsage(
                input_tokens=usage.input_tokens + error.usage.input_tokens,
                output_tokens=usage.output_tokens + error.usage.output_tokens,
                thinking_tokens=usage.thinking_tokens + error.usage.thinking_tokens,
            )
            if error.request_id:
                request_ids.append(error.request_id)
            retry_count += 1
            failure_code = error.error_code
            _write_attempt_error(
                private_dir,
                attempt=error_attempt,
                kind=error_kind,
                role=role,
                provider=provider_name,
                model=model,
                prompt=prompt,
                error_code=error.error_code,
                error=error,
                usage=error.usage,
                aggregate_usage=usage,
                request_id=error.request_id,
            )
            return None
        except ModelProviderError as error:
            retry_count += 1
            failure_code = error.error_code
            _write_attempt_error(
                private_dir,
                attempt=error_attempt,
                kind=error_kind,
                role=role,
                provider=provider_name,
                model=model,
                prompt=prompt,
                error_code=error.error_code,
                error=error,
                usage=ModelUsage(),
                aggregate_usage=usage,
                request_id=None,
            )
            return None
        usage = ModelUsage(
            input_tokens=usage.input_tokens + response.usage.input_tokens,
            output_tokens=usage.output_tokens + response.usage.output_tokens,
            thinking_tokens=usage.thinking_tokens + response.usage.thinking_tokens,
        )
        if response.request_id:
            request_ids.append(response.request_id)
        failure_code = None
        _write_attempt_result(
            private_dir,
            attempt=error_attempt,
            kind=error_kind,
            role=role,
            provider=provider_name,
            model=model,
            prompt=prompt,
            usage=response.usage,
            aggregate_usage=usage,
            request_id=response.request_id,
        )
        return response

    if seed_run is None:
        for attempt in range(repair_limit + 1):
            revision = attempt + 1
            if artifact is not None:
                repair_count += 1
            prompt = build_stage_prompt(
                request=request,
                stage=Stage.FOUNDATION,
                revision=revision,
                previous_artifact=artifact,
                repair_issues=issues,
                selected_direction=direction,
                composition=composition,
            )
            response = await generate(
                prompt,
                role=("repair" if artifact is not None else "widget_generator"),
                temperature=request.creativity,
                metadata={
                    "benchmark": "agent-kernel-frozen-v1",
                    "attempt": attempt + 1,
                },
                error_kind="attempt",
                error_attempt=attempt + 1,
            )
            if response is None:
                continue
            if not isinstance(response.parsed, dict):
                retry_count += 1
                failure_code = "invalid_response"
                continue
            previous_revision = artifact.revision if artifact else 0
            artifact = WidgetArtifact.from_dict(response.parsed)
            issues = validate_artifact(artifact, previous_revision=previous_revision)
            (private_dir / f"artifact-attempt-{attempt + 1}.json").write_text(
                json.dumps(artifact.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if not issues:
                break

    browser_passed = False
    browser_error: BrowserAuditError | None = None
    if artifact is not None and not issues:
        try:
            report = await audit_factory().audit(artifact)
            _write_audit_screenshots(private_dir, report)
            browser_passed = True
        except BrowserAuditError as error:
            browser_error = error
            failure_code = error.error_code
            if error.report is not None:
                _write_audit_screenshots(private_dir, error.report)

    for visual_attempt in range(1, browser_repair_limit + 1):
        if artifact is None or browser_passed:
            break
        repair_number = browser_repair_count + 1
        if issues:
            visual_findings: tuple[VisualFinding, ...] = ()
            repair_issues = issues
        elif browser_error is not None:
            visual_findings = (
                _browser_failure_finding(
                    browser_error,
                    repair_number=repair_number,
                ),
            )
            repair_issues = ()
        else:
            break
        repair_count += 1
        browser_repair_count += 1
        revision = artifact.revision + 1
        prompt = build_stage_prompt(
            request=request,
            stage=Stage.FOUNDATION,
            revision=revision,
            previous_artifact=artifact,
            repair_issues=repair_issues,
            visual_findings=visual_findings,
            selected_direction=direction,
            composition=composition,
        )
        response = await generate(
            prompt,
            role="repair",
            temperature=min(request.creativity, 0.35),
            metadata={
                "benchmark": "agent-kernel-frozen-v1",
                "browser_repair": repair_number,
            },
            error_kind="browser-repair",
            error_attempt=repair_number,
        )
        if response is None or not isinstance(response.parsed, dict):
            if response is not None:
                retry_count += 1
                failure_code = "invalid_response"
            continue
        previous_revision = artifact.revision
        candidate = WidgetArtifact.from_dict(response.parsed)
        candidate_issues = validate_artifact(
            candidate,
            previous_revision=previous_revision,
        )
        artifact = candidate
        issues = candidate_issues
        (private_dir / f"artifact-browser-repair-{repair_number}.json").write_text(
            json.dumps(artifact.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if issues:
            failure_code = "deterministic_validation_failed"
            browser_error = None
            continue
        try:
            report = await audit_factory().audit(artifact)
            _write_audit_screenshots(private_dir, report)
            browser_passed = True
            browser_error = None
            failure_code = None
        except BrowserAuditError as error:
            browser_error = error
            failure_code = error.error_code
            if error.report is not None:
                _write_audit_screenshots(private_dir, error.report)

    if artifact is not None:
        (private_dir / "preview.html").write_text(
            build_preview_document(artifact),
            encoding="utf-8",
        )
    elif failure_code is None:
        failure_code = "no_artifact"
    if artifact is not None and issues and failure_code is None:
        failure_code = "deterministic_validation_failed"
    (private_dir / "technical.json").write_text(
        json.dumps(
            {
                "validation_issues": [issue.to_dict() for issue in issues],
                "browser_error": browser_error.error_code if browser_error else None,
                "browser_diagnostic": (
                    browser_error.diagnostic if browser_error else None
                ),
                "browser_failures": (
                    list(browser_error.failures) if browser_error else []
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    input_price, output_price = DEFAULT_PRICES[model]
    return {
        "provider": provider_name,
        "model": model,
        "elapsed_seconds": round(
            elapsed_before + time.perf_counter() - started,
            3,
        ),
        "usage": {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "thinking_tokens": usage.thinking_tokens,
        },
        "pricing": {
            "input_usd_per_million": input_price,
            "output_usd_per_million": output_price,
        },
        "retry_count": retry_count,
        "repair_count": repair_count,
        "browser_repair_count": browser_repair_count,
        "visual_score": None,
        "validator_passed": artifact is not None and not issues,
        "browser_passed": browser_passed,
        "publishable": artifact is not None and not issues and browser_passed,
        "failure_code": failure_code,
        "request_ids": request_ids,
    }


def _write_attempt_error(
    private_dir: Path,
    *,
    attempt: int,
    kind: str = "attempt",
    role: str,
    provider: str,
    model: str,
    prompt: str,
    error_code: str,
    error: ModelProviderError,
    usage: ModelUsage,
    aggregate_usage: ModelUsage,
    request_id: str | None,
) -> None:
    _write_private_receipt(
        private_dir,
        stem=f"{kind}-{attempt}-error",
        payload={
            "attempt": attempt,
            "role": role,
            "provider": provider,
            "model": model,
            **_prompt_identity(prompt),
            "error_code": error_code,
            "error_class": type(error).__name__,
            "usage": _usage_payload(usage),
            "aggregate_usage": _usage_payload(aggregate_usage),
            "request_id": request_id,
        },
    )


def _write_attempt_result(
    private_dir: Path,
    *,
    attempt: int,
    kind: str,
    role: str,
    provider: str,
    model: str,
    prompt: str,
    usage: ModelUsage,
    aggregate_usage: ModelUsage,
    request_id: str | None,
) -> None:
    _write_private_receipt(
        private_dir,
        stem=f"{kind}-{attempt}-result",
        payload={
            "attempt": attempt,
            "role": role,
            "provider": provider,
            "model": model,
            **_prompt_identity(prompt),
            "usage": _usage_payload(usage),
            "aggregate_usage": _usage_payload(aggregate_usage),
            "request_id": request_id,
        },
    )


def _write_private_receipt(
    private_dir: Path,
    *,
    stem: str,
    payload: Mapping[str, Any],
) -> Path:
    private_dir.mkdir(parents=True, exist_ok=True)
    suffix = 1
    while True:
        name = f"{stem}.json" if suffix == 1 else f"{stem}-{suffix}.json"
        path = private_dir / name
        try:
            with path.open("x", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            return path
        except FileExistsError:
            suffix += 1


def _prompt_identity(prompt: str) -> dict[str, str | int]:
    encoded = prompt.encode("utf-8")
    return {
        "prompt_sha256": hashlib.sha256(encoded).hexdigest(),
        "prompt_bytes": len(encoded),
    }


def _usage_payload(usage: ModelUsage) -> dict[str, int]:
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "thinking_tokens": usage.thinking_tokens,
    }


def _write_audit_screenshots(private_dir: Path, report: Any) -> None:
    screenshot_dir = private_dir / "browser-screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    for screenshot in report.screenshots:
        state = screenshot.evidence.state.value.replace(".", "-")
        (screenshot_dir / f"{state}.jpg").write_bytes(screenshot.data)


def _latest_artifact(private_dir: Path) -> WidgetArtifact:
    candidates: list[WidgetArtifact] = []
    for path in (
        *private_dir.glob("artifact-attempt-*.json"),
        *private_dir.glob("artifact-browser-repair-*.json"),
    ):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            candidates.append(WidgetArtifact.from_dict(payload))
    if not candidates:
        raise ValueError(f"no resumable artifact for {private_dir.name}")
    return max(candidates, key=lambda item: item.revision)


def _write_public_report(
    *,
    path: Path,
    evidence: bytes,
    composition: bytes,
    visual_review: bytes | None,
    runs: Sequence[Mapping[str, Any]],
    usd_to_rub: float,
) -> dict[str, Any]:
    report = build_report(
        evidence=evidence,
        composition=composition,
        visual_review=visual_review,
        runs=runs,
        usd_to_rub=usd_to_rub,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    path.with_suffix(".md").write_text(
        render_markdown(report),
        encoding="utf-8",
    )
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--composition", type=Path, required=True)
    parser.add_argument("--visual-review", type=Path)
    parser.add_argument("--target", action="append", required=True)
    parser.add_argument("--usd-to-rub", type=float, default=100.0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--private-dir",
        type=Path,
        default=Path("data/benchmarks/private/agent-kernel-frozen-v1"),
    )
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--repair-limit", type=int, default=2)
    parser.add_argument("--browser-repair-limit", type=int, default=2)
    parser.add_argument("--resume-browser-repairs", action="store_true")
    return parser.parse_args()


async def _main(args: argparse.Namespace) -> int:
    evidence = args.evidence.read_bytes()
    composition = args.composition.read_bytes()
    visual_review = (
        args.visual_review.read_bytes() if args.visual_review is not None else None
    )
    existing_by_model: dict[str, Mapping[str, Any]] = {}
    if args.resume_browser_repairs:
        existing = json.loads(args.output.read_text(encoding="utf-8"))
        expected_identity = {
            "evidence_sha256": hashlib.sha256(evidence).hexdigest(),
            "composition_sha256": hashlib.sha256(composition).hexdigest(),
        }
        if visual_review is not None:
            expected_identity["visual_review_sha256"] = hashlib.sha256(
                visual_review
            ).hexdigest()
        if existing.get("input_identity") != expected_identity:
            raise ValueError("resume report does not match frozen inputs")
        rows = existing.get("runs")
        if not isinstance(rows, list):
            raise ValueError("resume report has no runs")
        existing_by_model = {
            str(row.get("model")): row
            for row in rows
            if isinstance(row, Mapping)
        }
    runs = []
    for target in args.target:
        model = target.partition(":")[2] or "unknown"
        seed_run = existing_by_model.get(model)
        seed_artifact = None
        if args.resume_browser_repairs:
            if seed_run is None:
                raise ValueError(f"resume report has no run for {model}")
            seed_artifact = _latest_artifact(args.private_dir / model)
        runs.append(
            await run_target(
                target=target,
                evidence_bytes=evidence,
                composition_bytes=composition,
                private_dir=args.private_dir / model,
                timeout_seconds=args.timeout,
                repair_limit=args.repair_limit,
                browser_repair_limit=args.browser_repair_limit,
                seed_artifact=seed_artifact,
                seed_run=seed_run,
            )
        )
        _write_public_report(
            path=args.output,
            evidence=evidence,
            composition=composition,
            visual_review=visual_review,
            runs=runs,
            usd_to_rub=args.usd_to_rub,
        )
    report = _write_public_report(
        path=args.output,
        evidence=evidence,
        composition=composition,
        visual_review=visual_review,
        runs=runs,
        usd_to_rub=args.usd_to_rub,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    return asyncio.run(_main(_parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
