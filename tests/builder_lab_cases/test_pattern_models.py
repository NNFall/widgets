from __future__ import annotations

import json

import pytest

from builder_lab.patterns.models import (
    CompositionPlan,
    CustomPatternEscape,
    PatternCategory,
    PatternSelection,
)


def selection(
    slot: PatternCategory | str,
    pattern_id: str,
    parameters: dict[str, object] | None = None,
) -> PatternSelection:
    return PatternSelection(
        slot=PatternCategory(slot),
        pattern_id=pattern_id,
        version=1,
        parameters=parameters or {},
        reason="Подходит выбранному арт-направлению",
    )


def complete_plan() -> CompositionPlan:
    return CompositionPlan(
        schema_version=1,
        direction_id="candidate-2",
        selections=tuple(
            selection(category, f"{category.value}-primary")
            for category in PatternCategory
        ),
        summary="Проверенная чат-композиция с выразительным движением",
    )


def test_composition_plan_requires_each_core_slot_once() -> None:
    with pytest.raises(ValueError, match="required slots"):
        CompositionPlan(
            schema_version=1,
            direction_id="candidate-2",
            selections=(selection("launcher", "orb-pulse"),),
            summary="Неполный план",
        )


def test_selection_rejects_code_in_parameters() -> None:
    with pytest.raises(ValueError, match="parameters"):
        selection("launcher", "orb-pulse", {"javascript": "alert(1)"})


def test_plan_round_trip_is_canonical() -> None:
    original = complete_plan()

    restored = CompositionPlan.from_dict(original.to_dict())

    assert restored == original
    assert json.dumps(restored.to_dict(), sort_keys=True, ensure_ascii=False)


def test_custom_escape_can_replace_exactly_one_missing_slot() -> None:
    plan = CompositionPlan(
        schema_version=1,
        direction_id="candidate-3",
        selections=tuple(
            selection(category, f"{category.value}-primary")
            for category in PatternCategory
            if category is not PatternCategory.MOTION
        ),
        summary="Контролируемая авторская анимация",
        custom_escape=CustomPatternEscape(
            slot=PatternCategory.MOTION,
            reason="Ни один проверенный motion-паттерн не выражает концепцию",
            constraints=("no-network", "reduced-motion-fallback"),
        ),
    )

    assert plan.custom_escape is not None
    assert plan.custom_escape.slot is PatternCategory.MOTION


def test_composition_plan_rejects_duplicate_slots() -> None:
    selections = [
        selection(category, f"{category.value}-primary")
        for category in PatternCategory
    ]
    selections[-1] = selection(PatternCategory.LAUNCHER, "launcher-secondary")

    with pytest.raises(ValueError, match="duplicate slots"):
        CompositionPlan(
            schema_version=1,
            direction_id="candidate-1",
            selections=tuple(selections),
            summary="Невалидный план",
        )


def test_parameters_must_be_bounded_plain_json() -> None:
    with pytest.raises(ValueError, match="parameters"):
        selection("composer", "single-line-pill", {"opacity": float("nan")})

    with pytest.raises(ValueError, match="parameters"):
        selection("composer", "single-line-pill", {"nested": {"depth": {"x": 1}}})

