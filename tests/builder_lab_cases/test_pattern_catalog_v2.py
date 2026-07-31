from __future__ import annotations

import itertools

from builder_lab.patterns.models import (
    CompositionPlan,
    PatternCategory,
    PatternSelection,
)
from builder_lab.patterns.registry import (
    PatternIntegrationMode,
    load_builtin_registry,
)
from builder_lab.patterns.source import compile_runtime_source
from builder_lab.validation import validate_artifact


EXPECTED_SOURCES = {
    PatternCategory.LAUNCHER: {
        ("orb-pulse", 2),
        ("peek-tab", 2),
        ("status-capsule", 1),
        ("avatar-beacon", 1),
    },
    PatternCategory.SHELL: {
        ("compact-chat", 2),
        ("floating-card", 2),
        ("docked-rail", 1),
        ("bottom-drawer", 1),
    },
    PatternCategory.MESSAGES: {
        ("paired-bubbles", 2),
        ("advisor-cards", 2),
        ("labelled-strips", 1),
        ("avatar-thread", 1),
    },
    PatternCategory.COMPOSER: {
        ("single-line-pill", 2),
        ("multiline-soft", 2),
        ("inset-textarea", 1),
        ("stacked-compose", 1),
    },
    PatternCategory.MOTION: {
        ("spring-reveal", 2),
        ("soft-scale", 2),
        ("side-glide", 1),
        ("fade-settle", 1),
    },
}

LEGACY_HASHES = {
    ("advisor-cards", 1): "646c454e187b5fdfff5c5946744bef08b5f469e254d23678ccc8f352b27cdd88",
    ("compact-chat", 1): "b94406315369b9a8d490a5b5132661291ebf6c2a3d866c6dc2b1fca8bd594340",
    ("floating-card", 1): "27e2c771a834ecfb3be0f8c03319540171ff25a514d3bd4b26949d9db723d0fb",
    ("multiline-soft", 1): "42b5ac35c7eb08849f823aebc7283d658aeeb2f3edd3143c979301cb6e357669",
    ("orb-pulse", 1): "d3873d7f63c8f54be631d4c84f19b16acc88c5d20b3d9b1648b754c2f20559dc",
    ("paired-bubbles", 1): "6bbfe62d6b42a2d7894e750aadd3c8ae56c30ce62c40510e8eb9eadccb8d8be8",
    ("peek-tab", 1): "037939e62fbc23fc326745b29762f821b0a16d505e6141055c979945a45121e3",
    ("single-line-pill", 1): "99e89973360480d4fba022a0cb3daf36f4b1e8fec0aaad79bc16db1f83cf5f98",
    ("soft-scale", 1): "e287e2b5b4af227acfe98e743983c5b928f34fe1882d789181fd8da113056812",
    ("spring-reveal", 1): "b65d72dd3bc17b1ff2f7e591c9b8d5b369fac42b0e13ff16ac65aa27964fd95e",
}

PORTED_IDS = {
    "orb-pulse",
    "peek-tab",
    "compact-chat",
    "floating-card",
    "paired-bubbles",
    "advisor-cards",
    "single-line-pill",
    "multiline-soft",
    "spring-reveal",
    "soft-scale",
}


def _source_plan(definitions) -> CompositionPlan:
    return CompositionPlan(
        schema_version=1,
        direction_id="catalog-matrix",
        selections=tuple(
            PatternSelection(
                slot=definition.category,
                pattern_id=definition.pattern_id,
                version=definition.version,
                parameters={},
                reason="Проверяем production source каталог",
            )
            for definition in definitions
        ),
        summary="Детерминированная проверка source-каталога",
    )


def test_builtin_catalog_has_exactly_twenty_runtime_sources() -> None:
    registry = load_builtin_registry()

    assert len(registry.definitions) == 30
    assert len(registry.public_catalog()) == 10
    assert len(registry.planner_catalog()) == 20
    for category, expected in EXPECTED_SOURCES.items():
        actual = {
            (definition.pattern_id, definition.version)
            for definition in registry.selectable_for(category)
        }
        assert actual == expected


def test_runtime_source_catalog_is_deterministic_and_asset_free() -> None:
    first = load_builtin_registry().planner_catalog()
    second = load_builtin_registry().planner_catalog()

    assert first == second
    assert [item["category"] for item in first] == [
        category.value
        for category in PatternCategory
        for _item in range(4)
    ]
    assert all(item["integration_mode"] == "runtime_source" for item in first)
    assert all(item["provenance"]["review_state"] == "verified" for item in first)
    assert all(
        not {"html", "css", "javascript"}.intersection(item)
        for item in first
    )


def test_existing_ids_have_runtime_source_versions_and_legacy_hashes_are_frozen() -> None:
    registry = load_builtin_registry()

    for key, expected_hash in LEGACY_HASHES.items():
        assert registry.resolve(*key).implementation_sha256 == expected_hash
    for category, versions in EXPECTED_SOURCES.items():
        for pattern_id, version in versions:
            definition = registry.resolve(pattern_id, version)
            assert definition.category is category
            assert definition.integration_mode is PatternIntegrationMode.RUNTIME_SOURCE
            assert definition.javascript == ""
            if pattern_id in PORTED_IDS:
                assert definition.provenance["supersedes"] == {
                    "pattern_id": pattern_id,
                    "version": 1,
                }
            else:
                assert definition.provenance["supersedes"] is None


def test_message_sources_cover_initial_and_runtime_turns() -> None:
    registry = load_builtin_registry()

    for definition in registry.selectable_for(PatternCategory.MESSAGES):
        css = definition.css
        assert ".kaigo-widget__message--assistant" in css
        assert '[data-kaigo-runtime-message="assistant"]' in css
        assert '[data-kaigo-runtime-message="user"]' in css
        assert "[data-kaigo-runtime-label]" in css
        assert "[data-kaigo-runtime-content]" in css
        assert "max-width" in css


def test_composer_sources_leave_controls_and_submission_to_runtime() -> None:
    registry = load_builtin_registry()

    for definition in registry.selectable_for(PatternCategory.COMPOSER):
        css = definition.css
        assert "<form" not in definition.html.lower()
        assert "data-action" not in definition.html.lower()
        assert definition.javascript == ""
        assert ".kaigo-widget__composer textarea" in css
        assert ".kaigo-widget__composer button" in css
        assert ":focus-visible" in css or ":focus-within" in css
        assert ":disabled" in css
        assert '[data-state="pending"]' in css


def test_motion_sources_use_only_trusted_states_and_reduced_motion() -> None:
    registry = load_builtin_registry()

    for definition in registry.selectable_for(PatternCategory.MOTION):
        css = definition.css
        assert ".kaigo-preview-open" in css or "[data-open]" in css
        assert "prefers-reduced-motion: reduce" in css
        assert "setTimeout" not in definition.html
        assert definition.javascript == ""


def test_every_runtime_source_cross_slot_composition_compiles() -> None:
    registry = load_builtin_registry()
    groups = [registry.selectable_for(category) for category in PatternCategory]
    compiled = 0

    for definitions in itertools.product(*groups):
        source = compile_runtime_source(_source_plan(definitions), registry)
        assert source.javascript == ""
        assert validate_artifact(
            source.as_seed(revision=1, art_direction="Каталожная проверка")
        ) == ()
        compiled += 1

    assert compiled == 4**5
