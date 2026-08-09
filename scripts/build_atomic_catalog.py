#!/usr/bin/env python3
"""Build or verify Kaigo's generated schema-v3 atomic visual catalog."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from builder_lab.patterns.atomic_models import (  # noqa: E402
    AdaptationPolicy,
    AtomicPatternCategory,
)
from builder_lab.patterns.atomic_registry import (  # noqa: E402
    AtomicPatternRegistry,
    AtomicPatternRegistryError,
    compute_implementation_hash,
)
from builder_lab.patterns.atomic_templates import (  # noqa: E402
    PatternVisualSpec,
    render_pattern,
)


SPECS_PATH = REPO_ROOT / "builder_lab" / "patterns" / "atomic_specs" / "variants.json"
CATALOG_ROOT = REPO_ROOT / "builder_lab" / "patterns" / "atomic_catalog"
MAX_CATALOG_BYTES = 512 * 1024
SPEC_FIELDS = frozenset(
    {
        "pattern_id",
        "version",
        "category",
        "variant",
        "title",
        "summary",
        "concept",
        "adaptation_policy",
        "accent",
        "accent_alt",
        "ink",
    }
)
IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
COLOR_RE = re.compile(r"^#[0-9a-f]{6}$")
EXPECTED_VARIANTS = {
    "launcher_shape": {
        "faceted_pebble",
        "orbit_notch",
        "portrait_lozenge",
        "split_medallion",
        "origami_kite",
    },
    "launcher_idle": {
        "tidal_breathe",
        "compass_drift",
        "satin_shimmer",
        "status_orbit",
    },
    "launcher_attention": {
        "inbox_nudge",
        "ribbon_peek",
        "signal_petal",
        "greeting_arc",
    },
    "shell_layout": {
        "dialogue_gallery",
        "concierge_column",
        "split_context",
        "pocket_agenda",
        "context_ribbon",
    },
    "widget_open": {
        "curtain_rise",
        "focus_bloom",
        "card_deal",
        "portal_draw",
    },
    "widget_close": {
        "satin_collapse",
        "dock_return",
        "page_turn",
        "orbit_recede",
    },
    "background_effect": {
        "chat_constellation",
        "message_topography",
        "thread_spotlight",
        "conversation_ribbons",
        "history_lantern",
    },
    "assistant_message_enter": {
        "ink_reveal",
        "card_bloom",
        "line_compose",
        "stamp_settle",
    },
    "user_message_enter": {
        "arc_arrival",
        "satin_drop",
        "snap_stack",
        "receipt_lock",
    },
    "typing_indicator": {
        "waveform_whisper",
        "quill_trace",
        "word_build",
        "orbit_dots",
    },
    "message_send": {
        "seal_release",
        "route_confirm",
        "press_lift",
        "luminous_delivery",
    },
    "composer_focus": {
        "floating_label",
        "contextual_rail",
        "soft_inset",
        "command_glow",
    },
    "control_hover": {
        "caption_reveal",
        "ring_track",
        "soft_tilt",
        "ink_fill",
    },
    "responsive_transition": {
        "device_morph",
        "panel_to_sheet",
        "width_choreography",
        "layout_relay",
    },
}
_SIGNATURE_CATEGORIES = frozenset(
    {
        "widget_open",
        "widget_close",
        "launcher_attention",
        "message_send",
        "assistant_message_enter",
        "user_message_enter",
    }
)
_STRUCTURAL_CATEGORIES = frozenset({"shell_layout", "responsive_transition"})


class AtomicCatalogBuildError(ValueError):
    """The generated catalog cannot be built without breaking its contract."""


def _required_text(value: object, *, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise AtomicCatalogBuildError(f"{field} must be text")
    result = value.strip()
    if not result or len(result) > maximum or "\x00" in result:
        raise AtomicCatalogBuildError(f"{field} is invalid")
    return result


def _load_specs(path: Path = SPECS_PATH) -> tuple[PatternVisualSpec, ...]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AtomicCatalogBuildError("atomic specs are not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "patterns"}:
        raise AtomicCatalogBuildError("atomic specs fields do not match the contract")
    if payload["schema_version"] != 1 or not isinstance(payload["patterns"], list):
        raise AtomicCatalogBuildError("atomic specs schema is invalid")

    specs: list[PatternVisualSpec] = []
    for index, raw in enumerate(payload["patterns"]):
        if not isinstance(raw, dict) or set(raw) != SPEC_FIELDS:
            raise AtomicCatalogBuildError(f"pattern spec {index} fields are invalid")
        pattern_id = _required_text(raw["pattern_id"], field="pattern_id", maximum=80)
        if not IDENTIFIER_RE.fullmatch(pattern_id):
            raise AtomicCatalogBuildError(f"pattern spec {index} id is invalid")
        if raw["version"] != 1 or isinstance(raw["version"], bool):
            raise AtomicCatalogBuildError(f"pattern spec {index} must be version 1")
        category = _required_text(raw["category"], field="category", maximum=80)
        variant = _required_text(raw["variant"], field="variant", maximum=80)
        try:
            AtomicPatternCategory(category)
            AdaptationPolicy(raw["adaptation_policy"])
        except (TypeError, ValueError) as exc:
            raise AtomicCatalogBuildError(
                f"pattern spec {index} category or policy is invalid"
            ) from exc
        if variant not in EXPECTED_VARIANTS.get(category, set()):
            raise AtomicCatalogBuildError(f"pattern spec {index} variant is invalid")
        colors = {
            name: _required_text(raw[name], field=name, maximum=7)
            for name in ("accent", "accent_alt", "ink")
        }
        if any(not COLOR_RE.fullmatch(color) for color in colors.values()):
            raise AtomicCatalogBuildError(f"pattern spec {index} color is invalid")
        specs.append(
            PatternVisualSpec(
                pattern_id=pattern_id,
                version=1,
                category=category,
                variant=variant,
                title=_required_text(raw["title"], field="title", maximum=200),
                summary=_required_text(raw["summary"], field="summary", maximum=500),
                concept=_required_text(raw["concept"], field="concept", maximum=1_500),
                adaptation_policy=_required_text(
                    raw["adaptation_policy"],
                    field="adaptation_policy",
                    maximum=20,
                ),
                accent=colors["accent"],
                accent_alt=colors["accent_alt"],
                ink=colors["ink"],
            )
        )

    exact_keys = [(spec.pattern_id, spec.version) for spec in specs]
    if len(specs) != 59 or len(set(exact_keys)) != 59:
        raise AtomicCatalogBuildError("atomic specs require 59 unique exact versions")
    category_counts = Counter(spec.category for spec in specs)
    expected_counts = {
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
    if category_counts != expected_counts:
        raise AtomicCatalogBuildError("atomic specs require four patterns per category")
    variants = {
        category: {spec.variant for spec in specs if spec.category == category}
        for category in EXPECTED_VARIANTS
    }
    if variants != EXPECTED_VARIANTS:
        raise AtomicCatalogBuildError("atomic specs must cover every named visual variant")
    return tuple(sorted(specs, key=lambda spec: (spec.category, spec.pattern_id)))


def _ai_description(spec: PatternVisualSpec) -> str:
    return (
        f"{spec.concept} В Pattern Lab композиция показывает исходное состояние, "
        "основную фазу и устойчивое завершение, поэтому визуальный смысл читается "
        "без скриптов и пояснений разработчика. Модель может адаптировать оттенки, "
        "масштаб и темп под бренд, сохраняя иерархию, доступный контраст и заявленную "
        f"идею: {spec.summary.lower()}"
    )


def _pattern_role(spec: PatternVisualSpec) -> str:
    if spec.category in _SIGNATURE_CATEGORIES:
        return "signature"
    if spec.category in _STRUCTURAL_CATEGORIES:
        return "structural"
    return "support"


def _manifest(spec: PatternVisualSpec, implementation_sha256: str) -> dict[str, object]:
    return {
        "schema_version": 3,
        "pattern_id": spec.pattern_id,
        "version": spec.version,
        "category": spec.category,
        "status": "active",
        "title": spec.title,
        "summary": spec.summary,
        "ai_description": _ai_description(spec),
        "technical_contract": "chat-runtime-atomic-v1",
        "adaptation_policy": spec.adaptation_policy,
        "incompatible_with": [],
        "implementation_sha256": implementation_sha256,
        "provenance": {
            "origin": "kaigo-owned",
            "review_state": "ready_for_review",
            "pattern_role": _pattern_role(spec),
        },
    }


def _directory_name(spec: PatternVisualSpec) -> str:
    return f"{spec.pattern_id}-v{spec.version}"


def _write_staged_pattern(root: Path, spec: PatternVisualSpec) -> None:
    directory = root / _directory_name(spec)
    directory.mkdir()
    assets = render_pattern(spec)
    (directory / "fragment.html").write_bytes(assets.html.encode("utf-8"))
    (directory / "styles.css").write_bytes(assets.css.encode("utf-8"))
    implementation_sha256 = compute_implementation_hash(directory)
    manifest = json.dumps(
        _manifest(spec, implementation_sha256),
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    (directory / "manifest.json").write_bytes(manifest.encode("utf-8"))


def _catalog_size(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _copy_preserved_catalog(
    source: Path,
    destination: Path,
    generated_directories: set[str],
) -> None:
    for entry in sorted(source.iterdir(), key=lambda item: item.name):
        if entry.name in generated_directories:
            continue
        shutil.copytree(entry, destination / entry.name)


def _stage_complete_catalog(
    root: Path,
    specs: tuple[PatternVisualSpec, ...],
) -> tuple[Path, AtomicPatternRegistry, int]:
    staged = root / "atomic_catalog"
    staged.mkdir()
    generated_directories = {_directory_name(spec) for spec in specs}
    _copy_preserved_catalog(CATALOG_ROOT, staged, generated_directories)
    for spec in specs:
        _write_staged_pattern(staged, spec)

    registry = AtomicPatternRegistry.load(staged)
    expected_keys = {(spec.pattern_id, spec.version) for spec in specs}
    if not expected_keys.issubset(registry.index):
        raise AtomicCatalogBuildError("staged catalog is missing generated exact versions")
    total_bytes = _catalog_size(staged)
    if total_bytes >= MAX_CATALOG_BYTES:
        raise AtomicCatalogBuildError(
            f"staged atomic catalog is {total_bytes} bytes; hard limit is below {MAX_CATALOG_BYTES}"
        )
    return staged, registry, total_bytes


def _directory_difference(expected: Path, actual: Path) -> str | None:
    if not actual.exists():
        return "directory is missing"
    if actual.is_symlink() or not actual.is_dir():
        return "path is not a regular directory"
    expected_files = {
        path.name: path.read_bytes() for path in expected.iterdir() if path.is_file()
    }
    actual_entries = tuple(actual.iterdir())
    if any(entry.is_symlink() or not entry.is_file() for entry in actual_entries):
        return "directory contains a non-regular asset"
    actual_files = {path.name: path.read_bytes() for path in actual_entries}
    if set(actual_files) != set(expected_files):
        return "asset names differ"
    for name in sorted(expected_files):
        if actual_files[name] != expected_files[name]:
            return f"{name} differs"
    return None


def _differences(
    staged: Path,
    specs: Iterable[PatternVisualSpec],
) -> tuple[str, ...]:
    result: list[str] = []
    for spec in specs:
        name = _directory_name(spec)
        difference = _directory_difference(staged / name, CATALOG_ROOT / name)
        if difference is not None:
            result.append(f"{name}: {difference}")
    return tuple(result)


def _install_missing(
    staged: Path,
    specs: tuple[PatternVisualSpec, ...],
) -> int:
    differences = _differences(staged, specs)
    unsafe = tuple(
        difference
        for difference in differences
        if not difference.endswith(": directory is missing")
    )
    if unsafe:
        raise AtomicCatalogBuildError(
            "refusing to rewrite an existing exact pattern version: " + unsafe[0]
        )

    installed = 0
    for spec in specs:
        name = _directory_name(spec)
        target = CATALOG_ROOT / name
        if target.exists():
            continue
        shutil.copytree(staged / name, target)
        installed += 1
    return installed


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build deterministic schema-v3 atomic visual patterns.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify byte-identical generated assets without writing to the catalog.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        specs = _load_specs()
        AtomicPatternRegistry.load(CATALOG_ROOT)
        with tempfile.TemporaryDirectory(prefix="kaigo-atomic-catalog-") as temporary:
            staged, registry, total_bytes = _stage_complete_catalog(
                Path(temporary),
                specs,
            )
            if args.check:
                differences = _differences(staged, specs)
                if differences:
                    for difference in differences:
                        print(f"OUTDATED: {difference}", file=sys.stderr)
                    return 1
                installed = 0
            else:
                installed = _install_missing(staged, specs)

        if not args.check:
            live_registry = AtomicPatternRegistry.load(CATALOG_ROOT)
            live_bytes = _catalog_size(CATALOG_ROOT)
            if len(live_registry.definitions) != len(registry.definitions):
                raise AtomicCatalogBuildError("installed catalog count differs from staging")
            if live_bytes != total_bytes:
                raise AtomicCatalogBuildError("installed catalog bytes differ from staging")
        mode = "check" if args.check else "build"
        print(
            f"atomic catalog {mode} OK: patterns={len(registry.definitions)} "
            f"generated={len(specs)} installed={installed} bytes={total_bytes}"
        )
        return 0
    except (AtomicCatalogBuildError, AtomicPatternRegistryError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
