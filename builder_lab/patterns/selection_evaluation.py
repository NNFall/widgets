"""Safe, stage-only evaluation helpers for atomic-pattern selection.

The evaluator deliberately persists metadata and exact references only.  Full
implementation assets are resolved transiently to prove stage scoping, but are
never copied into the JSON/Markdown report.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from datetime import UTC, datetime
from html import escape as html_escape
from itertools import combinations
from math import isfinite
from typing import Any

from ..engines.base import PatternCandidatePlanResult
from ..models import Stage
from .atomic_models import AtomicPatternStatus
from .atomic_quality import (
    AtomicPatternRole,
    compute_atomic_quality_profile,
    effective_review_state_for,
)
from .atomic_registry import AtomicPatternRegistry
from .candidate_planner import validate_pattern_candidate_plan
from .candidate_resolver import (
    STAGE_PATTERN_CATEGORIES,
    resolve_pattern_candidate_pack,
)


_REVIEWABLE_STATES = frozenset({"approved", "ready_for_review"})
_EVALUATED_STAGES = (
    Stage.FOUNDATION,
    Stage.IDENTITY,
    Stage.CONVERSATION,
    Stage.MOTION_POLISH,
)


def _effective_review_state(
    *,
    pattern_id: str,
    version: int,
    manifest_state: object,
    effective_review_states: Mapping[tuple[str, int], str] | None,
) -> str:
    if effective_review_states is None:
        state = manifest_state
    elif (pattern_id, version) in effective_review_states:
        # An explicit but malformed DB override must never be interpreted as
        # review-ready.  Fail closed to rejection rather than widening the
        # evaluator's allowlist.
        state = effective_review_states[(pattern_id, version)]
    else:
        state = manifest_state
    return state if state in {"approved", "ready_for_review", "rejected"} else "rejected"


def build_pattern_selection_allowlist(
    registry: AtomicPatternRegistry,
    *,
    effective_review_states: Mapping[tuple[str, int], str] | None = None,
    include_ready_for_review: bool = False,
) -> frozenset[tuple[str, int]]:
    """Return the exact active versions allowed in one selector experiment.

    Production callers leave ``include_ready_for_review`` false.  Evaluation
    callers may include review-ready definitions, but an explicit rejection
    always wins over manifest provenance.
    """

    allowed_states = _REVIEWABLE_STATES if include_ready_for_review else {"approved"}
    allowed: set[tuple[str, int]] = set()
    for definition in registry.definitions:
        if definition.status is not AtomicPatternStatus.ACTIVE:
            continue
        review_state = _effective_review_state(
            pattern_id=definition.pattern_id,
            version=definition.version,
            manifest_state=definition.provenance.get("review_state"),
            effective_review_states=effective_review_states,
        )
        quality = compute_atomic_quality_profile(
            definition,
            effective_review_state=review_state,
        )
        if quality.selector_eligible and review_state in allowed_states:
            allowed.add((definition.pattern_id, definition.version))
    return frozenset(allowed)


def _active_catalog(registry: AtomicPatternRegistry) -> tuple[dict[str, Any], ...]:
    return tuple(
        definition.selector_dict()
        for definition in registry.definitions
        if definition.status is AtomicPatternStatus.ACTIVE
    )


def _exact_key(item: Mapping[str, object]) -> tuple[str, int]:
    return str(item["pattern_id"]), int(item["version"])


def summarize_pattern_selection(
    *,
    case_id: str,
    source_url: str,
    result: PatternCandidatePlanResult,
    registry: AtomicPatternRegistry,
    effective_approved: Mapping[tuple[str, int], object]
    | Collection[tuple[str, int]],
    elapsed_seconds: float,
) -> dict[str, Any]:
    """Validate a live result and return a code-free exact-version summary."""

    case_id = case_id.strip()
    source_url = source_url.strip()
    if not case_id or not source_url:
        raise ValueError("case_id and source_url are required")
    if (
        isinstance(elapsed_seconds, bool)
        or not isinstance(elapsed_seconds, (int, float))
        or not isfinite(float(elapsed_seconds))
        or elapsed_seconds < 0
    ):
        raise ValueError("elapsed_seconds must be a finite non-negative number")

    catalog = _active_catalog(registry)
    plan = validate_pattern_candidate_plan(
        result.plan,
        registry=registry,
        selector_catalog=catalog,
        effective_approved=effective_approved,
    )
    groups: list[dict[str, Any]] = []
    top1: list[dict[str, Any]] = []
    for group in plan.groups:
        candidates: list[dict[str, Any]] = []
        for candidate in group.candidates:
            definition = registry.resolve(candidate.pattern_id, candidate.version)
            quality = compute_atomic_quality_profile(
                definition,
                effective_review_state=effective_review_state_for(
                    definition,
                    effective_approved,
                ),
            )
            item = {
                "pattern_id": definition.pattern_id,
                "version": definition.version,
                "rank": candidate.rank,
                "title": definition.title,
                "summary": definition.summary,
                "reason": candidate.reason,
                "technical_fixture": quality.role is AtomicPatternRole.FIXTURE,
                "quality_profile": quality.to_dict(),
            }
            candidates.append(item)
            if candidate.rank == 1:
                top1.append({"category": group.category.value, **item})
        groups.append({"category": group.category.value, "candidates": candidates})

    stage_packs: dict[str, dict[str, Any]] = {}
    for stage in _EVALUATED_STAGES:
        pack = resolve_pattern_candidate_pack(
            plan,
            stage,
            registry,
            effective_approved=effective_approved,
        )
        stage_packs[stage.value] = {
            "categories": sorted({item.category.value for item in pack.exposed_versions}),
            "exact_versions": [
                {
                    "category": item.category.value,
                    "pattern_id": item.pattern_id,
                    "version": item.version,
                    "rank": item.rank,
                }
                for item in pack.exposed_versions
            ],
            "byte_length": pack.byte_length,
            "allowed_categories": [
                category.value for category in STAGE_PATTERN_CATEGORIES[stage]
            ],
        }

    expressive_top1 = sum(
        item["quality_profile"]["role"] == AtomicPatternRole.SIGNATURE.value
        and bool(item["quality_profile"]["selector_eligible"])
        and not bool(item["quality_profile"]["rejected"])
        for item in top1
    )
    return {
        "case_id": case_id,
        "source_url": source_url,
        "direction_id": plan.direction_id,
        "summary": plan.summary,
        "used_fallback": bool(result.used_fallback),
        "diagnostic": result.diagnostic,
        "elapsed_seconds": round(float(elapsed_seconds), 3),
        "usage": result.usage.to_dict(),
        "provider_request_ids": list(result.provider_request_ids),
        "category_count": len(groups),
        "groups": groups,
        "top1_choices": top1,
        "top1_expressive_ratio": (
            round(expressive_top1 / len(top1), 4) if top1 else 0.0
        ),
        "stage_packs": stage_packs,
    }


def _top1_keys(case: Mapping[str, Any]) -> frozenset[tuple[str, str, int]]:
    choices = case.get("top1_choices")
    if not isinstance(choices, Sequence):
        raise ValueError("top1_choices must be an array")
    keys: set[tuple[str, str, int]] = set()
    for item in choices:
        if not isinstance(item, Mapping):
            raise ValueError("top1 choice must be an object")
        category = item.get("category")
        pattern_id = item.get("pattern_id")
        version = item.get("version")
        if (
            not isinstance(category, str)
            or not category.strip()
            or not isinstance(pattern_id, str)
            or not pattern_id.strip()
            or isinstance(version, bool)
            or not isinstance(version, int)
            or version <= 0
        ):
            raise ValueError("top1 choice exact version is invalid")
        keys.add((category, pattern_id, version))
    return frozenset(keys)


def _validate_summary_case(case: Mapping[str, Any]) -> None:
    """Revalidate the persisted summary contract before comparing/rendering."""

    if not isinstance(case, Mapping):
        raise ValueError("report case must be an object")
    case_id = case.get("case_id")
    source_url = case.get("source_url")
    if not isinstance(case_id, str) or not case_id.strip():
        raise ValueError("case_id must be a non-empty string")
    if not isinstance(source_url, str) or not source_url.strip():
        raise ValueError("source_url must be a non-empty string")
    elapsed = case.get("elapsed_seconds")
    if (
        isinstance(elapsed, bool)
        or not isinstance(elapsed, (int, float))
        or not isfinite(float(elapsed))
        or elapsed < 0
    ):
        raise ValueError("elapsed_seconds must be a finite non-negative number")
    category_count = case.get("category_count")
    if (
        isinstance(category_count, bool)
        or not isinstance(category_count, int)
        or category_count < 0
    ):
        raise ValueError("category_count is invalid")
    groups = case.get("groups")
    if not isinstance(groups, Sequence) or isinstance(groups, (str, bytes)):
        raise ValueError("groups must be an array")
    categories: list[str] = []
    for group in groups:
        if not isinstance(group, Mapping):
            raise ValueError("group must be an object")
        category = group.get("category")
        candidates = group.get("candidates")
        if not isinstance(category, str) or not category.strip():
            raise ValueError("group category is invalid")
        if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)):
            raise ValueError("group candidates must be an array")
        categories.append(category)
    if len(set(categories)) != len(categories):
        raise ValueError("group categories must be unique")
    if category_count != len(groups):
        raise ValueError("category_count does not match groups")
    if not isinstance(case.get("used_fallback"), bool):
        raise ValueError("used_fallback is invalid")
    usage = case.get("usage")
    if not isinstance(usage, Mapping):
        raise ValueError("case usage must be an object")
    total_tokens = usage.get("total_tokens")
    if (
        isinstance(total_tokens, bool)
        or not isinstance(total_tokens, int)
        or total_tokens < 0
    ):
        raise ValueError("usage total_tokens is invalid")
    ratio = case.get("top1_expressive_ratio")
    if (
        isinstance(ratio, bool)
        or not isinstance(ratio, (int, float))
        or not isfinite(float(ratio))
        or not 0 <= float(ratio) <= 1
    ):
        raise ValueError("top1_expressive_ratio is invalid")
    choices = case.get("top1_choices")
    if not isinstance(choices, Sequence) or isinstance(choices, (str, bytes)):
        raise ValueError("top1_choices must be an array")
    _top1_keys(case)
    top1_categories = [
        item.get("category")
        for item in choices
        if isinstance(item, Mapping)
    ]
    if (
        len(top1_categories) != len(categories)
        or set(top1_categories) != set(categories)
    ):
        raise ValueError("top1 choices do not match groups")
    expressive = 0
    for item in choices:
        if not isinstance(item, Mapping):
            raise ValueError("top1 choice must be an object")
        for field in ("title", "reason"):
            if not isinstance(item.get(field), str):
                raise ValueError(f"top1 choice {field} is invalid")
        quality = item.get("quality_profile")
        if not isinstance(quality, Mapping):
            raise ValueError("top1 choice quality_profile is invalid")
        if (
            not isinstance(quality.get("role"), str)
            or not isinstance(quality.get("selector_eligible"), bool)
            or not isinstance(quality.get("rejected"), bool)
        ):
            raise ValueError("top1 choice quality_profile is invalid")
        if (
            quality["role"] == AtomicPatternRole.SIGNATURE.value
            and quality["selector_eligible"]
            and not quality["rejected"]
        ):
            expressive += 1
    expected_ratio = expressive / len(choices) if choices else 0.0
    if abs(float(ratio) - expected_ratio) > 0.0001:
        raise ValueError("top1_expressive_ratio does not match top1 choices")


def _safe_markdown(value: object, *, code: bool = False) -> str:
    """Render untrusted report fields without table/HTML/newline injection."""

    text = " ".join(str(value).split())
    text = html_escape(text, quote=False).replace("|", "&#124;")
    if code:
        text = text.replace("`", "&#96;")
    return text


def build_multisite_pattern_selection_report(
    cases: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Combine validated per-site summaries and compare their rank-one sets."""

    normalized = tuple(dict(case) for case in cases)
    if not normalized:
        raise ValueError("at least one case is required")
    for case in normalized:
        _validate_summary_case(case)
    ids = [case["case_id"] for case in normalized]
    if any(not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("case ids must be unique and non-empty")

    similarities: list[dict[str, Any]] = []
    for left, right in combinations(normalized, 2):
        left_keys = _top1_keys(left)
        right_keys = _top1_keys(right)
        union = left_keys | right_keys
        score = len(left_keys & right_keys) / len(union) if union else 1.0
        similarities.append(
            {
                "left": left["case_id"],
                "right": right["case_id"],
                "jaccard": round(score, 4),
                "same_top1_count": len(left_keys & right_keys),
            }
        )
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "case_count": len(normalized),
        "cases": list(normalized),
        "pairwise_top1_similarity": similarities,
    }


def render_pattern_selection_markdown(report: Mapping[str, Any]) -> str:
    """Render a compact Russian report without prompts or implementation assets."""

    if not isinstance(report, Mapping):
        raise ValueError("report must be an object")
    cases = report.get("cases")
    if not isinstance(cases, Sequence) or isinstance(cases, (str, bytes)):
        raise ValueError("report cases must be an array")
    case_count = report.get("case_count")
    if (
        isinstance(case_count, bool)
        or not isinstance(case_count, int)
        or case_count != len(cases)
    ):
        raise ValueError("report case_count does not match cases")
    lines = [
        "# Многосайтовая проверка выбора паттернов Kaigo",
        "",
        "| Сценарий | URL | Время, с | Токены | Fallback | Категории | Выразительные top-1 |",
        "|---|---|---:|---:|:---:|---:|---:|",
    ]
    normalized_cases: list[Mapping[str, Any]] = []
    for case in cases:
        _validate_summary_case(case)
        usage = case["usage"]
        normalized_cases.append(case)
        lines.append(
            f"| {_safe_markdown(case['case_id'])} | {_safe_markdown(case['source_url'])} | "
            f"{float(case['elapsed_seconds']):.2f} | {int(usage['total_tokens'])} | "
            f"{'да' if case['used_fallback'] else 'нет'} | {int(case['category_count'])} | "
            f"{float(case['top1_expressive_ratio']):.0%} |"
        )
    for case in normalized_cases:
        lines.extend(["", f"## {_safe_markdown(case['case_id'])}", ""])
        for item in case["top1_choices"]:
            lines.append(
                f"- `{_safe_markdown(item['category'], code=True)}` → "
                f"`{_safe_markdown(item['pattern_id'], code=True)}@{item['version']}` — "
                f"{_safe_markdown(item.get('title', ''))}: {_safe_markdown(item['reason'])}"
            )
    similarities = report.get("pairwise_top1_similarity", ())
    if not isinstance(similarities, Sequence) or isinstance(similarities, (str, bytes)):
        raise ValueError("pairwise_top1_similarity must be an array")
    if similarities:
        lines.extend(["", "## Различие выбора", ""])
        for row in similarities:
            if not isinstance(row, Mapping):
                raise ValueError("pairwise similarity must be an object")
            left = row.get("left")
            right = row.get("right")
            jaccard = row.get("jaccard")
            same_count = row.get("same_top1_count")
            if (
                not isinstance(left, str)
                or not isinstance(right, str)
                or isinstance(jaccard, bool)
                or not isinstance(jaccard, (int, float))
                or not isfinite(float(jaccard))
                or isinstance(same_count, bool)
                or not isinstance(same_count, int)
                or same_count < 0
            ):
                raise ValueError("pairwise similarity is invalid")
            lines.append(
                f"- {_safe_markdown(left)} ↔ {_safe_markdown(right)}: Jaccard "
                f"{float(jaccard):.2f}, совпало top-1: {same_count}."
            )
    lines.extend(
        [
            "",
            "В отчёт не включаются prompts, HTML/CSS/JavaScript, ключи и заголовки авторизации.",
            "",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "build_multisite_pattern_selection_report",
    "build_pattern_selection_allowlist",
    "render_pattern_selection_markdown",
    "summarize_pattern_selection",
]
