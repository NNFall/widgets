from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from dataclasses import replace

import pytest

from builder_lab.engines.gemini_direct import GeminiDirectEngine
from builder_lab.models import BuilderRequest, EngineName, Stage
from builder_lab.patterns.atomic_models import (
    AtomicPatternCategory,
    AtomicPatternStatus,
    PatternCandidate,
    PatternCandidateGroup,
    PatternCandidatePlan,
)
from builder_lab.patterns.atomic_registry import (
    AtomicPatternRegistry,
    load_builtin_atomic_registry,
)
from builder_lab.patterns.candidate_resolver import (
    STAGE_PATTERN_CATEGORIES,
    PatternCandidateResolutionError,
    resolve_pattern_candidate_pack,
)
from builder_lab.prompts import build_stage_prompt
from tests.builder_lab_cases.test_validation import artifact


def _hash_assets(html: str, css: str, javascript: str) -> str:
    digest = hashlib.sha256()
    for name, value in (
        ("fragment.html", html),
        ("styles.css", css),
        ("behavior.js", javascript),
    ):
        encoded_name = name.encode("utf-8")
        encoded_value = value.encode("utf-8")
        digest.update(len(encoded_name).to_bytes(2, "big"))
        digest.update(encoded_name)
        digest.update(len(encoded_value).to_bytes(8, "big"))
        digest.update(encoded_value)
    return digest.hexdigest()


def _candidate(category: AtomicPatternCategory, *, version: int = 1) -> PatternCandidate:
    return PatternCandidate(
        pattern_id=f"{category.value.replace('_', '-')}-technical",
        version=version,
        rank=1,
        reason="Exact version matches the selected direction and runtime contract.",
    )


def _plan(*categories: AtomicPatternCategory) -> PatternCandidatePlan:
    return PatternCandidatePlan(
        schema_version=2,
        direction_id="candidate-1",
        groups=tuple(
            PatternCandidateGroup(category=category, candidates=(_candidate(category),))
            for category in categories
        ),
        summary="Persisted exact shortlist.",
    )


@pytest.mark.parametrize(
    ("stage", "expected"),
    [
        (
            Stage.FOUNDATION,
            {
                "launcher_shape",
                "shell_layout",
                "background_effect",
            },
        ),
        (Stage.IDENTITY, {"launcher_attention", "launcher_idle"}),
        (
            Stage.CONVERSATION,
            {
                "assistant_message_enter",
                "user_message_enter",
                "typing_indicator",
                "message_send",
                "composer_focus",
            },
        ),
        (
            Stage.MOTION_POLISH,
            {
                "widget_open",
                "widget_close",
                "control_hover",
                "responsive_transition",
                "background_effect",
            },
        ),
    ],
)
def test_stage_mapping_is_exact_and_pack_exposes_only_mapped_categories(
    stage: Stage,
    expected: set[str],
) -> None:
    registry = load_builtin_atomic_registry()
    plan_categories = tuple(
        dict.fromkeys(
            category
            for categories in STAGE_PATTERN_CATEGORIES.values()
            for category in categories
        )
    )
    plan = _plan(*plan_categories)

    pack = resolve_pattern_candidate_pack(plan, stage, registry)

    assert {item.category.value for item in pack.exposed_versions} == expected
    assert tuple(STAGE_PATTERN_CATEGORIES) == (
        Stage.FOUNDATION,
        Stage.IDENTITY,
        Stage.CONVERSATION,
        Stage.MOTION_POLISH,
    )


def test_stage_pack_uses_only_exact_shortlisted_versions_and_deterministic_order() -> None:
    registry = load_builtin_atomic_registry()
    plan = _plan(
        AtomicPatternCategory.BACKGROUND_EFFECT,
        AtomicPatternCategory.WIDGET_OPEN,
        AtomicPatternCategory.WIDGET_CLOSE,
        AtomicPatternCategory.CONTROL_HOVER,
        AtomicPatternCategory.RESPONSIVE_TRANSITION,
        AtomicPatternCategory.LAUNCHER_SHAPE,
    )

    pack = resolve_pattern_candidate_pack(plan, Stage.MOTION_POLISH, registry)

    assert [(item.category, item.pattern_id, item.version) for item in pack.exposed_versions] == [
        (AtomicPatternCategory.WIDGET_OPEN, "widget-open-technical", 1),
        (AtomicPatternCategory.WIDGET_CLOSE, "widget-close-technical", 1),
        (AtomicPatternCategory.CONTROL_HOVER, "control-hover-technical", 1),
        (AtomicPatternCategory.RESPONSIVE_TRANSITION, "responsive-transition-technical", 1),
        (AtomicPatternCategory.BACKGROUND_EFFECT, "background-effect-technical", 1),
    ]
    assert "launcher-shape-technical" not in pack.prompt_text


def test_unknown_or_drifted_exact_version_is_rejected_without_registry_substitution() -> None:
    registry = load_builtin_atomic_registry()
    drifted = tuple(
        replace(definition, version=2)
        if definition.pattern_id == "widget-open-technical"
        else definition
        for definition in registry.definitions
    )

    with pytest.raises(PatternCandidateResolutionError, match="unknown pattern version"):
        resolve_pattern_candidate_pack(
            _plan(AtomicPatternCategory.WIDGET_OPEN),
            Stage.MOTION_POLISH,
            AtomicPatternRegistry(drifted),
        )


@pytest.mark.parametrize("status", [AtomicPatternStatus.DEPRECATED])
def test_inactive_or_rejected_exact_version_is_rejected(status: AtomicPatternStatus) -> None:
    registry = load_builtin_atomic_registry()
    changed = tuple(
        replace(definition, status=status)
        if definition.pattern_id == "widget-open-technical"
        else definition
        for definition in registry.definitions
    )

    with pytest.raises(PatternCandidateResolutionError, match="active"):
        resolve_pattern_candidate_pack(
            _plan(AtomicPatternCategory.WIDGET_OPEN),
            Stage.MOTION_POLISH,
            AtomicPatternRegistry(changed),
        )


def test_effective_approval_and_category_mismatch_are_rechecked() -> None:
    registry = load_builtin_atomic_registry()
    with pytest.raises(PatternCandidateResolutionError, match="approved"):
        resolve_pattern_candidate_pack(
            _plan(AtomicPatternCategory.WIDGET_OPEN),
            Stage.MOTION_POLISH,
            registry,
            effective_approved=set(),
        )

    mismatched = PatternCandidatePlan(
        schema_version=2,
        direction_id="candidate-1",
        groups=(
            PatternCandidateGroup(
                category=AtomicPatternCategory.WIDGET_CLOSE,
                candidates=(
                    PatternCandidate(
                        pattern_id="widget-open-technical",
                        version=1,
                        rank=1,
                        reason="wrong category",
                    ),
                ),
            ),
        ),
        summary="bad category",
    )
    with pytest.raises(PatternCandidateResolutionError, match="category"):
        resolve_pattern_candidate_pack(mismatched, Stage.MOTION_POLISH, registry)


def test_symmetric_incompatibility_is_rechecked_before_assets_are_exposed() -> None:
    registry = load_builtin_atomic_registry()
    changed = tuple(
        replace(definition, incompatible_with=("widget-close-technical",))
        if definition.pattern_id == "widget-open-technical"
        else (
            replace(definition, incompatible_with=("widget-open-technical",))
            if definition.pattern_id == "widget-close-technical"
            else definition
        )
        for definition in registry.definitions
    )

    with pytest.raises(PatternCandidateResolutionError, match="incompat"):
        resolve_pattern_candidate_pack(
            _plan(AtomicPatternCategory.WIDGET_OPEN, AtomicPatternCategory.WIDGET_CLOSE),
            Stage.MOTION_POLISH,
            AtomicPatternRegistry(changed),
        )


def test_pack_prompt_contains_full_exact_assets_and_runtime_authority_only_for_stage() -> None:
    registry = load_builtin_atomic_registry()
    pack = resolve_pattern_candidate_pack(
        _plan(
            AtomicPatternCategory.WIDGET_OPEN,
            AtomicPatternCategory.WIDGET_CLOSE,
            AtomicPatternCategory.CONTROL_HOVER,
            AtomicPatternCategory.RESPONSIVE_TRANSITION,
            AtomicPatternCategory.BACKGROUND_EFFECT,
            AtomicPatternCategory.LAUNCHER_SHAPE,
        ),
        Stage.MOTION_POLISH,
        registry,
    )

    for item in pack.exposed_versions:
        definition = registry.resolve(item.pattern_id, item.version)
        assert definition.ai_description in pack.prompt_text
        assert definition.technical_contract in pack.prompt_text
        assert definition.adaptation_policy.value in pack.prompt_text
        assert definition.implementation_sha256 in pack.prompt_text
        assert definition.html in pack.prompt_text
        assert definition.css in pack.prompt_text
        assert definition.javascript in pack.prompt_text
    assert "trusted runtime owns open/close/submit/message insertion" in pack.prompt_text.lower()
    assert registry.resolve("launcher-shape-technical", 1).html not in pack.prompt_text


def test_multibyte_utf8_byte_budget_is_enforced() -> None:
    registry = load_builtin_atomic_registry()
    definition = registry.resolve("widget-open-technical", 1)
    html = definition.html + ("я" * 200)
    changed = tuple(
        replace(
            item,
            html=html,
            implementation_sha256=_hash_assets(html, item.css, item.javascript),
        )
        if item.pattern_id == definition.pattern_id
        else item
        for item in registry.definitions
    )

    with pytest.raises(PatternCandidateResolutionError, match="byte"):
        resolve_pattern_candidate_pack(
            _plan(AtomicPatternCategory.WIDGET_OPEN),
            Stage.MOTION_POLISH,
            AtomicPatternRegistry(changed),
            max_bytes=300,
        )


def test_stage_prompt_candidate_pack_is_mutually_exclusive_and_legacy_callers_survive() -> None:
    registry = load_builtin_atomic_registry()
    pack = resolve_pattern_candidate_pack(
        _plan(AtomicPatternCategory.WIDGET_OPEN),
        Stage.MOTION_POLISH,
        registry,
    )
    request = BuilderRequest(
        engine=EngineName.DIRECT,
        brief="A compact assistant widget.",
        locale="en",
    )
    legacy = build_stage_prompt(
        request=request,
        stage=Stage.MOTION_POLISH,
        revision=1,
        previous_artifact=None,
        composition=type("LegacyComposition", (), {"prompt_text": "legacy composition marker"})(),
    )
    assert "legacy composition marker" in legacy
    no_bundle = build_stage_prompt(
        request=request,
        stage=Stage.MOTION_POLISH,
        revision=1,
        previous_artifact=None,
    )
    assert "Композиция паттернов ещё не выбрана для этого этапа." in no_bundle
    with pytest.raises(ValueError, match="mutually exclusive"):
        build_stage_prompt(
            request=request,
            stage=Stage.MOTION_POLISH,
            revision=1,
            previous_artifact=None,
            composition=type("LegacyComposition", (), {"prompt_text": "legacy"})(),
            pattern_candidate_pack=pack,
        )
    packed = build_stage_prompt(
        request=request,
        stage=Stage.MOTION_POLISH,
        revision=1,
        previous_artifact=None,
        pattern_candidate_pack=pack,
    )
    assert pack.prompt_text in packed
    assert "legacy composition marker" not in packed


@pytest.mark.asyncio
async def test_gemini_direct_forwards_stage_pack_to_stage_prompt() -> None:
    response = SimpleNamespace(
        parsed=artifact(revision=1, stage=Stage.FOUNDATION).to_dict(),
        usage_metadata=SimpleNamespace(
            prompt_token_count=1,
            candidates_token_count=1,
            thoughts_token_count=0,
            total_token_count=2,
        ),
        response_id="response-1",
        raw={},
        model_version="test-model",
    )
    engine = GeminiDirectEngine(
        model_router=SimpleNamespace(),
        routing_role="widget_generator",
    )
    engine._generate_structured = AsyncMock(return_value=response)
    pack = object()
    request = BuilderRequest(engine=EngineName.DIRECT, brief="A compact widget.")

    with patch(
        "builder_lab.engines.gemini_direct.build_stage_prompt",
        return_value="pack-aware prompt",
    ) as build_prompt:
        result = await engine.generate(
            request=request,
            stage=Stage.FOUNDATION,
            revision=1,
            pattern_candidate_pack=pack,
        )

    assert result.artifact.stage is Stage.FOUNDATION
    assert build_prompt.call_args.kwargs["pattern_candidate_pack"] is pack
