from __future__ import annotations

import pytest

from builder_lab.patterns.models import (
    CompositionPlan,
    PatternCategory,
    PatternSelection,
)
from builder_lab.patterns.registry import load_builtin_registry
from builder_lab.patterns.resolver import (
    PatternResolutionError,
    resolve_composition,
)


PATTERNS = {
    PatternCategory.LAUNCHER: "orb-pulse",
    PatternCategory.SHELL: "compact-chat",
    PatternCategory.MESSAGES: "paired-bubbles",
    PatternCategory.COMPOSER: "single-line-pill",
    PatternCategory.MOTION: "spring-reveal",
}


def complete_plan(**overrides: str) -> CompositionPlan:
    return CompositionPlan(
        schema_version=1,
        direction_id="candidate-2",
        selections=tuple(
            PatternSelection(
                slot=category,
                pattern_id=overrides.get(category.value, pattern_id),
                version=1,
                parameters={},
                reason="Проверенная композиция",
            )
            for category, pattern_id in PATTERNS.items()
        ),
        summary="Компактный брендовый консультант",
    )


def test_resolver_rejects_incompatible_pair() -> None:
    plan = complete_plan(launcher="peek-tab", shell="floating-card")

    with pytest.raises(PatternResolutionError, match="incompatible"):
        resolve_composition(plan, load_builtin_registry())


def test_resolver_rejects_parameter_outside_manifest_schema() -> None:
    plan = complete_plan()
    selections = list(plan.selections)
    selections[0] = PatternSelection(
        slot=PatternCategory.LAUNCHER,
        pattern_id="orb-pulse",
        version=1,
        parameters={"size_px": 500},
        reason="Невалидный размер",
    )
    invalid = CompositionPlan(
        schema_version=1,
        direction_id=plan.direction_id,
        selections=tuple(selections),
        summary=plan.summary,
    )

    with pytest.raises(PatternResolutionError, match="parameters"):
        resolve_composition(invalid, load_builtin_registry())


def test_resolver_returns_only_selected_assets() -> None:
    plan = complete_plan()

    bundle = resolve_composition(plan, load_builtin_registry())

    assert set(bundle.pattern_ids) == {item.pattern_id for item in plan.selections}
    assert "floating-card" not in bundle.prompt_text
    assert "unused-pattern-marker" not in bundle.prompt_text
    assert bundle.prompt_text == resolve_composition(
        plan,
        load_builtin_registry(),
    ).prompt_text


def test_resolved_bundle_is_bounded() -> None:
    bundle = resolve_composition(complete_plan(), load_builtin_registry())

    assert len(bundle.prompt_text.encode("utf-8")) <= 96_000
    assert len(bundle.implementation_hashes) == 5

