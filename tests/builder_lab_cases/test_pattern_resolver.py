from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest

from builder_lab.patterns.models import (
    CompositionPlan,
    CustomPatternEscape,
    PatternCategory,
    PatternSelection,
)
from builder_lab.patterns.registry import (
    PatternIntegrationMode,
    PatternRegistry,
    load_builtin_registry,
)
from builder_lab.patterns.resolver import (
    PatternResolutionError,
    resolve_composition,
)

from ._pattern_source_fixtures import source_registry_and_plan


LEGACY_PATTERNS = {
    PatternCategory.LAUNCHER: "orb-pulse",
    PatternCategory.SHELL: "compact-chat",
    PatternCategory.MESSAGES: "paired-bubbles",
    PatternCategory.COMPOSER: "single-line-pill",
    PatternCategory.MOTION: "spring-reveal",
}


def legacy_complete_plan(**overrides: str) -> CompositionPlan:
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
            for category, pattern_id in LEGACY_PATTERNS.items()
        ),
        summary="Компактный брендовый консультант",
    )


def test_new_resolution_rejects_prompt_only_v1() -> None:
    with pytest.raises(PatternResolutionError, match="legacy_reference"):
        resolve_composition(legacy_complete_plan(), load_builtin_registry())


def test_explicit_legacy_resume_never_compiles_javascript() -> None:
    result = resolve_composition(
        legacy_complete_plan(),
        load_builtin_registry(),
        allow_legacy_reference=True,
    )

    assert result.source is None
    assert result.integration_mode is PatternIntegrationMode.LEGACY_REFERENCE
    assert "untrusted reference code" in result.prompt_text
    assert "BEGIN behavior.js" in result.prompt_text


def test_source_resolution_returns_compiled_seed(tmp_path) -> None:
    registry, plan = source_registry_and_plan(tmp_path)

    result = resolve_composition(plan, registry)

    assert result.source is not None
    assert result.source.javascript == ""
    assert result.source.source_sha256 in result.prompt_text
    assert result.source.body_html not in result.prompt_text
    assert result.source.css not in result.prompt_text
    assert result.integration_mode is PatternIntegrationMode.RUNTIME_SOURCE


def test_new_resolution_rejects_mixed_integration_modes(tmp_path) -> None:
    source_registry, source_plan = source_registry_and_plan(tmp_path)
    legacy_registry = load_builtin_registry()
    source_selections = list(source_plan.selections)
    source_selections[0] = legacy_complete_plan().selections[0]
    mixed_plan = replace(source_plan, selections=tuple(source_selections))
    mixed_registry = PatternRegistry(
        source_registry.definitions + legacy_registry.definitions
    )

    with pytest.raises(PatternResolutionError, match="mixed"):
        resolve_composition(mixed_plan, mixed_registry)


def test_new_source_resolution_rejects_custom_escape(tmp_path) -> None:
    registry, plan = source_registry_and_plan(tmp_path)
    source_with_escape = replace(
        plan,
        selections=tuple(
            selection
            for selection in plan.selections
            if selection.slot is not PatternCategory.MOTION
        ),
        custom_escape=CustomPatternEscape(
            slot=PatternCategory.MOTION,
            reason="Нужен особый сценарий движения",
            constraints=("calm-motion",),
        ),
    )

    with pytest.raises(PatternResolutionError, match="custom_escape"):
        resolve_composition(source_with_escape, registry)


def test_resolution_rejects_unknown_integration_mode(tmp_path) -> None:
    registry, plan = source_registry_and_plan(tmp_path)
    definitions = list(registry.definitions)
    definitions[0] = replace(
        definitions[0],
        integration_mode=cast(PatternIntegrationMode, "future_mode"),
    )

    with pytest.raises(PatternResolutionError, match="unsupported integration mode"):
        resolve_composition(plan, PatternRegistry(tuple(definitions)))


def test_resolver_rejects_incompatible_legacy_pair_on_resume() -> None:
    plan = legacy_complete_plan(launcher="peek-tab", shell="floating-card")

    with pytest.raises(PatternResolutionError, match="incompatible"):
        resolve_composition(
            plan,
            load_builtin_registry(),
            allow_legacy_reference=True,
        )


def test_resolver_rejects_parameter_outside_legacy_manifest_schema() -> None:
    plan = legacy_complete_plan()
    selections = list(plan.selections)
    selections[0] = PatternSelection(
        slot=PatternCategory.LAUNCHER,
        pattern_id="orb-pulse",
        version=1,
        parameters={"size_px": 500},
        reason="Невалидный размер",
    )
    invalid = replace(plan, selections=tuple(selections))

    with pytest.raises(PatternResolutionError, match="parameters"):
        resolve_composition(
            invalid,
            load_builtin_registry(),
            allow_legacy_reference=True,
        )


def test_legacy_resume_returns_only_selected_assets() -> None:
    plan = legacy_complete_plan()

    bundle = resolve_composition(
        plan,
        load_builtin_registry(),
        allow_legacy_reference=True,
    )

    assert set(bundle.pattern_ids) == {item.pattern_id for item in plan.selections}
    assert "floating-card" not in bundle.prompt_text
    assert "unused-pattern-marker" not in bundle.prompt_text
    assert bundle.prompt_text == resolve_composition(
        plan,
        load_builtin_registry(),
        allow_legacy_reference=True,
    ).prompt_text


def test_resolved_legacy_resume_bundle_is_bounded() -> None:
    bundle = resolve_composition(
        legacy_complete_plan(),
        load_builtin_registry(),
        allow_legacy_reference=True,
    )

    assert len(bundle.prompt_text.encode("utf-8")) <= 96_000
    assert len(bundle.implementation_hashes) == 5
