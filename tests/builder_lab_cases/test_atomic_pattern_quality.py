from __future__ import annotations

from builder_lab.patterns.atomic_quality import (
    AtomicPatternRole,
    MIN_SELECTOR_QUALITY_SCORE,
    compute_atomic_quality_profile,
)
from builder_lab.patterns.atomic_registry import load_builtin_atomic_registry
from dataclasses import replace
import pytest


def test_technical_fixture_is_never_selector_eligible_even_when_approved() -> None:
    definition = load_builtin_atomic_registry().resolve("widget-open-technical", 1)

    profile = compute_atomic_quality_profile(definition)

    assert profile.role is AtomicPatternRole.FIXTURE
    assert profile.selector_eligible is False
    assert profile.quality_score == 0
    assert profile.rejected is False
    assert profile.keyframe_count == 0
    assert profile.reduced_motion_present is False


def test_technical_suffix_cannot_be_spoofed_into_visual_role() -> None:
    definition = load_builtin_atomic_registry().resolve("widget-open-technical", 1)
    spoofed = replace(
        definition,
        provenance={**definition.provenance, "pattern_role": "signature"},
    )

    profile = compute_atomic_quality_profile(spoofed)

    assert profile.role is AtomicPatternRole.FIXTURE
    assert profile.selector_eligible is False


@pytest.mark.parametrize("role_value", [None, "not-a-role"])
def test_missing_or_invalid_explicit_pattern_role_fails_closed(
    role_value: str | None,
) -> None:
    definition = load_builtin_atomic_registry().resolve("widget-open-spring-unfold", 1)
    provenance = dict(definition.provenance)
    if role_value is None:
        provenance.pop("pattern_role", None)
    else:
        provenance["pattern_role"] = role_value

    profile = compute_atomic_quality_profile(
        replace(definition, provenance=provenance)
    )

    assert profile.selector_eligible is False
    assert profile.quality_score == 0


def test_nontechnical_signature_requires_real_motion_and_scores_deterministically() -> None:
    definition = load_builtin_atomic_registry().resolve("widget-open-spring-unfold", 1)
    definition = replace(
        definition,
        provenance={**definition.provenance, "pattern_role": "signature"},
    )

    profile = compute_atomic_quality_profile(definition)

    assert profile.role is AtomicPatternRole.SIGNATURE
    assert profile.selector_eligible is True
    assert profile.quality_score >= MIN_SELECTOR_QUALITY_SCORE
    assert profile.keyframe_count >= 1
    assert profile.css_bytes >= 512
    assert profile.reduced_motion_present is True


def test_comments_and_padding_do_not_create_motion_quality() -> None:
    """Only executable CSS/markup contributes to the quality profile."""

    registry = load_builtin_atomic_registry()
    definition = registry.resolve("widget-open-spring-unfold", 1)
    padded = replace(
        definition,
        html="<!-- " + ("visible-looking padding " * 100) + " --> <div></div>",
        css="/* @keyframes fake { from { opacity: 0 } to { opacity: 1 } } */"
        + ("/* padding */" * 300)
        + " .only-padding { /* animation: fake 3s; */ }",
    )

    profile = compute_atomic_quality_profile(padded)

    assert profile.keyframe_count == 0
    assert profile.reduced_motion_present is False
    assert profile.css_bytes < 512
    assert profile.selector_eligible is False


def test_signature_profile_reports_executable_motion_metrics() -> None:
    definition = load_builtin_atomic_registry().resolve("widget-open-spring-unfold", 1)
    definition = replace(
        definition,
        provenance={**definition.provenance, "pattern_role": "signature"},
    )

    profile = compute_atomic_quality_profile(definition)

    assert profile.animated_target_count >= 2
    assert profile.animation_rule_count >= 1
    assert profile.keyframe_phase_count >= 2
    assert profile.duration_band_ok is True


def test_unreferenced_keyframes_and_animation_name_tokens_do_not_pass_gate() -> None:
    """Only named, executable motion may contribute to selector eligibility."""

    definition = load_builtin_atomic_registry().resolve("widget-open-spring-unfold", 1)
    synthetic = replace(
        definition,
        css=(
            "@keyframes real-motion { from { opacity: 0 } to { opacity: 1 } }"
            ".first, .second { animation-name: fake-1000ms; "
            "animation-duration: 1000ms; }"
            + ".unused-padding { color: #123456; " + ("padding: 1px; " * 80) + "}"
            + "@media (prefers-reduced-motion: reduce) { "
            ".first, .second { animation: none; } }"
        ),
        provenance={**definition.provenance, "pattern_role": "signature"},
    )

    profile = compute_atomic_quality_profile(synthetic)

    assert profile.keyframe_count == 0
    assert profile.keyframe_phase_count == 0
    assert profile.animated_target_count == 0
    assert profile.duration_band_ok is False
    assert profile.css_bytes < 512
    assert profile.selector_eligible is False
