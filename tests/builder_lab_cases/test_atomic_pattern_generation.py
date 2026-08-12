from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import subprocess
import sys

from builder_lab.patterns.atomic_models import (
    AtomicPatternCategory,
    AtomicPatternStatus,
)
from builder_lab.patterns.atomic_registry import load_builtin_atomic_registry


REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG_ROOT = REPO_ROOT / "builder_lab" / "patterns" / "atomic_catalog"
SPECS_PATH = (
    REPO_ROOT / "builder_lab" / "patterns" / "atomic_specs" / "variants.json"
)
GENERATOR_PATH = REPO_ROOT / "scripts" / "build_atomic_catalog.py"
MAX_CATALOG_BYTES = 512 * 1024
BASELINE_DIRECTORIES = {
    "assistant-message-cascade-v1",
    "assistant-message-enter-technical-v1",
    "background-aurora-dust-v1",
    "background-effect-technical-v1",
    "composer-focus-smart-rail-v1",
    "composer-focus-technical-v1",
    "control-hover-magnetic-lift-v1",
    "control-hover-technical-v1",
    "launcher-attention-technical-v1",
    "launcher-breathing-halo-v1",
    "launcher-concierge-capsule-v1",
    "launcher-idle-technical-v1",
    "launcher-peek-spark-v1",
    "launcher-shape-technical-v1",
    "message-send-ripple-plane-v1",
    "message-send-technical-v1",
    "responsive-dock-sheet-v1",
    "responsive-transition-technical-v1",
    "shell-layered-concierge-v1",
    "shell-layout-technical-v1",
    "typing-editorial-pulse-v1",
    "typing-indicator-technical-v1",
    "user-message-enter-technical-v1",
    "user-message-slide-settle-v1",
    "widget-close-fold-home-v1",
    "widget-close-technical-v1",
    "widget-open-spring-unfold-v1",
    "widget-open-technical-v1",
}


def _load_specs() -> dict[str, object]:
    return json.loads(SPECS_PATH.read_text(encoding="utf-8"))


def _catalog_snapshot() -> tuple[tuple[str, bytes, int], ...]:
    return tuple(
        (
            path.relative_to(CATALOG_ROOT).as_posix(),
            path.read_bytes(),
            path.stat().st_mtime_ns,
        )
        for path in sorted(CATALOG_ROOT.rglob("*"))
        if path.is_file()
    )


def _generated_definitions() -> tuple[object, ...]:
    specs = _load_specs()
    expected_keys = {
        (item["pattern_id"], item["version"])
        for item in specs["patterns"]
    }
    registry = load_builtin_atomic_registry()
    return tuple(
        definition
        for definition in registry.definitions
        if (definition.pattern_id, definition.version) in expected_keys
    )


def _generated_definition(pattern_id: str) -> object:
    specs = _load_specs()["patterns"]
    version = next(
        item["version"] for item in specs if item["pattern_id"] == pattern_id
    )
    return load_builtin_atomic_registry().resolve(pattern_id, version)


def test_catalog_has_at_least_five_visual_patterns_per_atomic_category() -> None:
    registry = load_builtin_atomic_registry()

    assert len(registry.definitions) == 90
    for category in AtomicPatternCategory:
        visual = [
                definition
                for definition in registry.definitions
                if definition.category is category
                and definition.provenance["origin"] == "kaigo-owned"
                and definition.provenance["review_state"] in {"approved", "ready_for_review"}
                and definition.provenance.get(
                    "pattern_role",
                    "fixture" if definition.pattern_id.endswith("-technical") else "support",
                )
                != "fixture"
        ]
        assert len(visual) >= 5, category.value


def test_specs_declare_exactly_four_unique_patterns_per_category() -> None:
    specs = _load_specs()
    patterns = specs["patterns"]

    assert specs["schema_version"] == 1
    assert len(patterns) == 59
    assert Counter(item["category"] for item in patterns) == {
        category.value: (
            5
            if category
            in {
                AtomicPatternCategory.LAUNCHER_SHAPE,
                AtomicPatternCategory.SHELL_LAYOUT,
                AtomicPatternCategory.BACKGROUND_EFFECT,
            }
            else 4
        )
        for category in AtomicPatternCategory
    }
    exact_keys = [(item["pattern_id"], item["version"]) for item in patterns]
    assert len(set(exact_keys)) == len(exact_keys)
    assert {
        pattern_id for pattern_id, version in exact_keys if version == 2
    } == {
        "widget-open-portal-draw",
        "widget-open-curtain-rise",
        "widget-close-page-turn",
    }
    assert all(version in {1, 2} for _, version in exact_keys)
    assert not {
        f"{pattern_id}-v{version}" for pattern_id, version in exact_keys
    } & BASELINE_DIRECTORIES


def test_reworked_signature_motion_keeps_immutable_v1_and_uses_v2() -> None:
    registry = load_builtin_atomic_registry()
    for pattern_id in (
        "widget-open-portal-draw",
        "widget-open-curtain-rise",
        "widget-close-page-turn",
    ):
        original = registry.resolve(pattern_id, 1)
        current = registry.resolve(pattern_id, 2)
        assert original.implementation_sha256 != current.implementation_sha256
        assert current.provenance["pattern_role"] == "signature"


def test_generated_patterns_are_reviewable_css_only_previews() -> None:
    definitions = _generated_definitions()

    assert len(definitions) == 59
    assert len({item.implementation_sha256 for item in definitions}) == 59
    for definition in definitions:
        directory = CATALOG_ROOT / f"{definition.pattern_id}-v{definition.version}"
        assert definition.status is AtomicPatternStatus.ACTIVE
        assert definition.provenance["origin"] == "kaigo-owned"
        assert definition.provenance["review_state"] == "ready_for_review"
        assert definition.javascript == ""
        assert not (directory / "behavior.js").exists()
        assert "data-pattern-preview" in definition.html
        assert "data-pattern-command='run'" in definition.css
        assert "data-pattern-command='replay'" in definition.css
        assert "prefers-reduced-motion: reduce" in definition.css


def test_generated_patterns_declare_selector_pattern_roles() -> None:
    signature_categories = {
        AtomicPatternCategory.WIDGET_OPEN,
        AtomicPatternCategory.WIDGET_CLOSE,
        AtomicPatternCategory.LAUNCHER_ATTENTION,
        AtomicPatternCategory.MESSAGE_SEND,
        AtomicPatternCategory.ASSISTANT_MESSAGE_ENTER,
        AtomicPatternCategory.USER_MESSAGE_ENTER,
    }
    structural_categories = {
        AtomicPatternCategory.SHELL_LAYOUT,
        AtomicPatternCategory.RESPONSIVE_TRANSITION,
    }

    for definition in _generated_definitions():
        expected = (
            "signature"
            if definition.category in signature_categories
            else "structural"
            if definition.category in structural_categories
            else "support"
        )
        assert definition.provenance["pattern_role"] == expected


def test_category_showcases_preserve_their_visual_semantics() -> None:
    definitions = _generated_definitions()
    by_category = {
        category: [item for item in definitions if item.category is category]
        for category in AtomicPatternCategory
    }

    composer = by_category[AtomicPatternCategory.COMPOSER_FOCUS]
    assert {item.pattern_id for item in composer} == {
        "composer-floating-label",
        "composer-contextual-rail",
        "composer-soft-inset",
        "composer-command-glow",
    }
    assert all("data-composer-field" in item.html for item in composer)

    typing = by_category[AtomicPatternCategory.TYPING_INDICATOR]
    assert len({item.html for item in typing}) == 4
    assert len({item.css for item in typing}) == 4
    assert all("data-typing-kind" in item.html for item in typing)
    typing_targets = {
        "typing-waveform-whisper": ".typing-mark span",
        "typing-quill-trace": ".typing-mark .quill",
        "typing-word-build": ".typing-mark span",
        "typing-orbit-dots": ".typing-mark span",
    }
    for item in typing:
        target = typing_targets[item.pattern_id]
        assert f"html[data-pattern-command='run'] {target}" in item.css
        assert f"html[data-pattern-command='replay'] {target}" in item.css

    sending = by_category[AtomicPatternCategory.MESSAGE_SEND]
    assert all("--send-duration: 1.8s" in item.css for item in sending)
    assert all("data-delivery-feedback" in item.html for item in sending)

    responsive = by_category[AtomicPatternCategory.RESPONSIVE_TRANSITION]
    assert all("data-desktop-panel" in item.html for item in responsive)
    assert all("data-mobile-sheet" in item.html for item in responsive)
    assert all("desktop → mobile" in item.html for item in responsive)

    backgrounds = by_category[AtomicPatternCategory.BACKGROUND_EFFECT]
    assert all('data-effect-scope="chat-history"' in item.html for item in backgrounds)
    assert all(".chat-history .ambient-effect" in item.css for item in backgrounds)
    assert all(".pattern-stage::before" not in item.css for item in backgrounds)


def test_signature_open_patterns_expose_independent_phases_and_no_elastic_motion() -> None:
    portal = _generated_definition("widget-open-portal-draw")
    curtain = _generated_definition("widget-open-curtain-rise")

    assert all(
        marker in portal.html
        for marker in (
            'data-portal-part="contour"',
            'data-portal-part="surface"',
            'data-portal-part="content"',
        )
    )
    assert all(
        marker in portal.css
        for marker in (
            "@keyframes portal_draw-contour",
            "@keyframes portal_draw-surface",
            "@keyframes portal_draw-content",
            ".portal-contour",
            ".portal-surface",
            ".portal-content",
        )
    )

    assert curtain.html.count("data-curtain-layer=") == 2
    assert 'data-curtain-part="content"' in curtain.html
    assert all(
        marker in curtain.css
        for marker in (
            "@keyframes curtain_rise-back",
            "@keyframes curtain_rise-front",
            "@keyframes curtain_rise-content",
            ".curtain-layer-back",
            ".curtain-layer-front",
            ".curtain-content",
        )
    )

    for definition in (portal, curtain):
        css = definition.css.lower()
        assert "bounce" not in css
        assert "elastic" not in css
        assert "infinite" not in css


def test_signature_page_turn_is_a_safe_standalone_3d_closure() -> None:
    definition = _generated_definition("widget-close-page-turn")

    assert all(
        marker in definition.html
        for marker in (
            'data-page-turn-part="sheet"',
            'data-page-turn-part="face"',
            'data-page-turn-part="shadow"',
        )
    )
    assert all(
        marker in definition.css
        for marker in (
            "@keyframes page_turn-close",
            "transform-style:preserve-3d",
            "perspective:1100px",
            "transform-origin:100% 50%",
            "backface-visibility:hidden",
            "rotateY(-96deg)",
            "@media (prefers-reduced-motion: reduce)",
        )
    )
    assert "infinite" not in definition.css.lower()
    assert "bounce" not in definition.css.lower()
    assert "elastic" not in definition.css.lower()


def test_launcher_origami_kite_uses_layered_geometry_and_a_safe_hit_area() -> None:
    definition = _generated_definition("launcher-origami-kite")

    assert "launcher-detail" in definition.html
    assert "launcher-symbol" in definition.html
    assert all(
        marker in definition.css
        for marker in (
            "clip-path:polygon(8% 48%,38% 8%,94% 24%,72% 88%,32% 96%)",
            ".launcher::before",
            ".launcher::after",
            "min-width:44px",
            "min-height:44px",
            "prefers-reduced-motion: reduce",
        )
    )


def test_shell_context_ribbon_has_a_responsive_top_context_and_dialogue_canvas() -> None:
    definition = _generated_definition("shell-context-ribbon")

    assert all(
        marker in definition.html
        for marker in (
            "context-ribbon-shell",
            'data-shell-ribbon="context"',
            'data-shell-main="dialogue"',
        )
    )
    assert all(
        marker in definition.css
        for marker in (
            "grid-template:56px 1fr / 1fr",
            ".shell-ribbon",
            "min-width:0",
            "overflow:hidden",
            "@media(max-width:430px)",
            "prefers-reduced-motion: reduce",
        )
    )


def test_background_history_lantern_stays_scoped_to_chat_history() -> None:
    definition = _generated_definition("background-history-lantern")

    assert 'data-effect-scope="chat-history"' in definition.html
    assert definition.html.count("data-lantern-layer=") == 3
    assert all(
        marker in definition.css
        for marker in (
            ".chat-history .ambient-effect",
            ".lantern-glow",
            ".lantern-path",
            "@keyframes history_lantern-ambient",
            "prefers-reduced-motion: reduce",
        )
    )
    assert ".pattern-stage::before" not in definition.css


def test_generator_check_is_deterministic_and_never_writes() -> None:
    before = _catalog_snapshot()
    command = [sys.executable, str(GENERATOR_PATH), "--check"]

    first = subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    between = _catalog_snapshot()
    second = subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    after = _catalog_snapshot()

    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr
    assert first.stdout == second.stdout
    assert first.stderr == second.stderr
    assert before == between == after


def test_complete_atomic_catalog_stays_below_the_hard_size_limit() -> None:
    total_bytes = sum(path.stat().st_size for path in CATALOG_ROOT.rglob("*") if path.is_file())

    assert total_bytes < MAX_CATALOG_BYTES
