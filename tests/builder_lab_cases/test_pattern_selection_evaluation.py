from __future__ import annotations

import pytest

from builder_lab.engines.base import PatternCandidatePlanResult
from builder_lab.models import Stage, TokenUsage
from builder_lab.patterns.atomic_models import (
    AtomicPatternCategory,
    PatternCandidate,
    PatternCandidateGroup,
    PatternCandidatePlan,
)
from builder_lab.patterns.atomic_registry import load_builtin_atomic_registry
from builder_lab.patterns.atomic_quality import AtomicPatternRole, compute_atomic_quality_profile
from builder_lab.patterns.candidate_resolver import STAGE_PATTERN_CATEGORIES
from builder_lab.patterns.selection_evaluation import (
    build_multisite_pattern_selection_report,
    build_pattern_selection_allowlist,
    render_pattern_selection_markdown,
    summarize_pattern_selection,
)


def _manual_report_case(
    case_id: str = "case",
    *,
    elapsed_seconds: object = 1.0,
    top1_choices: object | None = None,
) -> dict[str, object]:
    return {
        "case_id": case_id,
        "source_url": "https://example.com/",
        "direction_id": "direction-1",
        "summary": "summary",
        "used_fallback": False,
        "diagnostic": None,
        "elapsed_seconds": elapsed_seconds,
        "usage": {"prompt_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        "provider_request_ids": [],
        "category_count": 1,
        "groups": [{"category": "widget_open", "candidates": []}],
        "top1_choices": top1_choices
        if top1_choices is not None
        else [
            {
                "category": "widget_open",
                "pattern_id": "widget-open-spring-unfold",
                "version": 1,
                "rank": 1,
                "title": "A title",
                "reason": "A reason",
                "quality_profile": {
                    "role": "signature",
                    "selector_eligible": True,
                    "rejected": False,
                },
            }
        ],
        "top1_expressive_ratio": 1.0,
        "stage_packs": {},
    }


@pytest.mark.parametrize("elapsed", [-0.1, float("nan"), float("inf"), float("-inf")])
def test_summary_rejects_negative_or_nonfinite_elapsed(elapsed: float) -> None:
    registry = load_builtin_atomic_registry()
    allowed = build_pattern_selection_allowlist(registry, include_ready_for_review=True)
    result = PatternCandidatePlanResult(
        plan=_plan(allowed, direction_id="candidate-1"),
        usage=TokenUsage(),
    )

    with pytest.raises(ValueError, match="elapsed_seconds"):
        summarize_pattern_selection(
            case_id="elapsed-check",
            source_url="https://example.com/",
            result=result,
            registry=registry,
            effective_approved=allowed,
            elapsed_seconds=elapsed,
        )


def test_multisite_report_revalidates_top1_shape() -> None:
    malformed = _manual_report_case(
        top1_choices=[{"category": "widget_open", "pattern_id": "x", "version": "1"}]
    )

    with pytest.raises(ValueError, match="version"):
        build_multisite_pattern_selection_report((malformed,))


def test_multisite_report_rejects_mutated_derived_category_count() -> None:
    mutated = _manual_report_case()
    mutated["groups"] = []
    mutated["category_count"] = 999

    with pytest.raises(ValueError, match="category_count"):
        build_multisite_pattern_selection_report((mutated,))


def test_markdown_escapes_html_table_and_newline_injection() -> None:
    hostile = _manual_report_case("<script>alert(1)</script>|\ncase")
    hostile["source_url"] = "https://example.com/?q=<script>|\n"
    hostile["top1_choices"][0]["title"] = "<script>title</script>|\n"
    hostile["top1_choices"][0]["reason"] = "close`|<script>reason</script>\nnext"
    second = _manual_report_case("second")

    markdown = render_pattern_selection_markdown(
        build_multisite_pattern_selection_report((hostile, second))
    )

    assert "<script>" not in markdown
    assert "</script>" not in markdown
    assert "reason</script>\nnext" not in markdown
    assert "close`|" not in markdown


def test_markdown_rejects_mutated_report_case_count() -> None:
    report = build_multisite_pattern_selection_report((_manual_report_case(),))
    report["case_count"] = 999

    with pytest.raises(ValueError, match="case_count"):
        render_pattern_selection_markdown(report)


def _plan(allowed: frozenset[tuple[str, int]], *, direction_id: str) -> PatternCandidatePlan:
    registry = load_builtin_atomic_registry()
    groups = []
    for category in AtomicPatternCategory:
        available = [
            definition
            for definition in registry.active_for(category)
            if (definition.pattern_id, definition.version) in allowed
        ]
        chosen = available[: min(3, len(available))]
        groups.append(
            PatternCandidateGroup(
                category=category,
                candidates=tuple(
                    PatternCandidate(
                        pattern_id=definition.pattern_id,
                        version=definition.version,
                        rank=index,
                        reason=f"Fits frozen site evidence for {category.value}.",
                    )
                    for index, definition in enumerate(chosen, start=1)
                ),
            )
        )
    return PatternCandidatePlan(
        schema_version=2,
        direction_id=direction_id,
        groups=tuple(groups),
        summary="A site-specific shortlist.",
    )


def test_reviewable_allowlist_includes_ready_but_excludes_effective_rejection() -> None:
    registry = load_builtin_atomic_registry()
    rejected = registry.resolve("background-aurora-dust", 1)
    overrides = {(rejected.pattern_id, rejected.version): "rejected"}

    approved = build_pattern_selection_allowlist(
        registry,
        effective_review_states=overrides,
        include_ready_for_review=False,
    )
    reviewable = build_pattern_selection_allowlist(
        registry,
        effective_review_states=overrides,
        include_ready_for_review=True,
    )

    expected_approved = sum(
        compute_atomic_quality_profile(item).selector_eligible
        and (
            overrides.get(
                (item.pattern_id, item.version),
                item.provenance.get("review_state"),
            )
            == "approved"
        )
        for item in registry.definitions
        if item.status.value == "active"
    )
    expected_reviewable = sum(
        compute_atomic_quality_profile(item).selector_eligible
        and (
            overrides.get(
                (item.pattern_id, item.version),
                item.provenance.get("review_state"),
            )
            in {"approved", "ready_for_review"}
        )
        for item in registry.definitions
        if item.status.value == "active"
    )
    assert len(approved) == expected_approved
    assert len(reviewable) == expected_reviewable
    assert (rejected.pattern_id, rejected.version) not in reviewable
    assert all(registry.resolve(*key).status.value == "active" for key in reviewable)
    assert all(not key[0].endswith("-technical") for key in reviewable)


def test_allowlist_malformed_review_override_fails_closed() -> None:
    registry = load_builtin_atomic_registry()
    target = registry.resolve("widget-open-spring-unfold", 1)
    overrides = {(target.pattern_id, target.version): object()}

    reviewable = build_pattern_selection_allowlist(
        registry,
        effective_review_states=overrides,
        include_ready_for_review=True,
    )

    assert (target.pattern_id, target.version) not in reviewable


def test_summary_resolves_exact_stage_packs_without_exposing_code() -> None:
    registry = load_builtin_atomic_registry()
    allowed = build_pattern_selection_allowlist(
        registry,
        include_ready_for_review=True,
    )
    result = PatternCandidatePlanResult(
        plan=_plan(allowed, direction_id="candidate-1"),
        usage=TokenUsage(prompt_tokens=120, output_tokens=80),
        provider_request_id="request-1",
        provider_request_ids=("request-1",),
        used_fallback=False,
    )

    summary = summarize_pattern_selection(
        case_id="editorial-b2b",
        source_url="https://example.com/",
        result=result,
        registry=registry,
        effective_approved=allowed,
        elapsed_seconds=1.25,
    )

    assert summary["category_count"] == 14
    assert summary["usage"]["total_tokens"] == 200
    assert summary["provider_request_ids"] == ["request-1"]
    for stage in (
        Stage.FOUNDATION,
        Stage.IDENTITY,
        Stage.CONVERSATION,
        Stage.MOTION_POLISH,
    ):
        pack = summary["stage_packs"][stage.value]
        assert set(pack["categories"]) <= {
            category.value for category in STAGE_PATTERN_CATEGORIES[stage]
        }
        assert pack["byte_length"] > 0
    serialized = str(summary)
    assert "BEGIN fragment.html" not in serialized
    assert "<div class=" not in serialized
    assert "styles.css" not in serialized


def test_multisite_report_compares_top_ranked_choices_and_markdown_is_safe() -> None:
    registry = load_builtin_atomic_registry()
    allowed = build_pattern_selection_allowlist(
        registry,
        include_ready_for_review=True,
    )
    first = summarize_pattern_selection(
        case_id="edtech",
        source_url="https://skyeng.ru/",
        result=PatternCandidatePlanResult(
            plan=_plan(allowed, direction_id="candidate-1"),
            usage=TokenUsage(),
        ),
        registry=registry,
        effective_approved=allowed,
        elapsed_seconds=0.5,
    )
    second = dict(first)
    second["case_id"] = "coffee"
    second["source_url"] = "https://simplecoffee.ru/"

    report = build_multisite_pattern_selection_report((first, second))
    markdown = render_pattern_selection_markdown(report)

    assert report["schema_version"] == 1
    assert report["case_count"] == 2
    assert report["pairwise_top1_similarity"][0]["left"] == "edtech"
    assert "Skyeng" not in markdown  # report uses stable case ids, not invented prose
    assert "edtech" in markdown
    assert "coffee" in markdown
    assert markdown.index("| coffee |") < markdown.index("## edtech")
    assert "fragment.html" not in markdown
    assert "API_KEY" not in markdown


def test_support_top1_is_not_counted_as_expressive() -> None:
    registry = load_builtin_atomic_registry()
    allowed = build_pattern_selection_allowlist(registry, include_ready_for_review=True)
    result = PatternCandidatePlanResult(
        plan=_plan(allowed, direction_id="candidate-1"),
        usage=TokenUsage(),
    )

    summary = summarize_pattern_selection(
        case_id="support-check",
        source_url="https://example.com/",
        result=result,
        registry=registry,
        effective_approved=allowed,
        elapsed_seconds=0.1,
    )

    expressive = sum(
        compute_atomic_quality_profile(
            registry.resolve(item["pattern_id"], item["version"])
        ).role
        is AtomicPatternRole.SIGNATURE
        for item in summary["top1_choices"]
    )
    assert summary["top1_expressive_ratio"] == pytest.approx(
        expressive / len(summary["top1_choices"]), abs=0.0001
    )
    assert any(
        compute_atomic_quality_profile(
            registry.resolve(item["pattern_id"], item["version"])
        ).role
        is not AtomicPatternRole.SIGNATURE
        for item in summary["top1_choices"]
    )
