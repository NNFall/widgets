from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from builder_lab.patterns.atomic_models import (
    AdaptationPolicy,
    AtomicPatternCategory,
    AtomicPatternStatus,
)
from builder_lab.patterns.atomic_registry import (
    AtomicPatternRegistry,
    AtomicPatternRegistryError,
    load_builtin_atomic_registry,
)
from builder_lab.patterns.models import PatternCategory
from builder_lab.patterns.registry import PatternRegistry


ATOMIC_FIELDS = {
    "schema_version",
    "pattern_id",
    "version",
    "category",
    "status",
    "title",
    "summary",
    "ai_description",
    "technical_contract",
    "adaptation_policy",
    "incompatible_with",
    "implementation_sha256",
    "provenance",
}


def implementation_hash(*assets: bytes) -> str:
    digest = hashlib.sha256()
    for asset in assets:
        digest.update(asset)
    return digest.hexdigest()


def write_atomic_pattern(
    directory: Path,
    *,
    pattern_id: str = "widget-open-technical",
    version: int = 1,
    category: str = "widget_open",
    status: str = "active",
    ai_description: str | None = None,
    behavior: str | None = "",
    implementation_sha256: str | None = None,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    html = f'<div class="kaigo-atomic-{category}" data-state="closed">fixture</div>\n'
    css = f".kaigo-atomic-{category} {{ display: block; }}\n"
    js = "" if behavior is None else behavior
    description = ai_description or (
        "A neutral technical reference for this atomic interaction. "
        "It names the runtime state, keeps the DOM contract small, and leaves "
        "visual styling open for adaptation to the approved product direction."
    )
    manifest: dict[str, object] = {
        "schema_version": 3,
        "pattern_id": pattern_id,
        "version": version,
        "category": category,
        "status": status,
        "title": f"{category.replace('_', ' ').title()} technical fixture",
        "summary": "Neutral technical fixture for contract tests.",
        "ai_description": description,
        "technical_contract": "chat-runtime-atomic-v1",
        "adaptation_policy": "adaptive",
        "incompatible_with": [],
        "provenance": {"origin": "kaigo-owned", "review_state": "approved"},
    }
    (directory / "fragment.html").write_text(html, encoding="utf-8")
    (directory / "styles.css").write_text(css, encoding="utf-8")
    if behavior is not None:
        (directory / "behavior.js").write_text(js, encoding="utf-8")
    assets = tuple(
        (directory / name).read_bytes() if (directory / name).exists() else b""
        for name in ("fragment.html", "styles.css", "behavior.js")
    )
    manifest["implementation_sha256"] = implementation_sha256 or implementation_hash(
        *assets
    )
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return directory


def valid_atomic_catalog(tmp_path: Path) -> Path:
    root = tmp_path / "catalog"
    write_atomic_pattern(root / "widget-open-technical-v1")
    return root


def test_v3_registry_exposes_full_ai_description_without_assets() -> None:
    registry = load_builtin_atomic_registry()
    definition = registry.resolve("widget-open-technical", 1)

    public = definition.selector_dict()

    assert public["ai_description"] == definition.ai_description
    assert "html" not in public
    assert "css" not in public
    assert "javascript" not in public
    assert public["technical_contract"] == definition.technical_contract
    assert public["adaptation_policy"] == definition.adaptation_policy.value
    assert public["provenance"] == dict(definition.provenance)


def test_v3_implementation_dict_returns_exact_assets() -> None:
    definition = load_builtin_atomic_registry().resolve("widget-open-technical", 1)

    assert definition.implementation_dict() == {
        "html": definition.html,
        "css": definition.css,
        "javascript": definition.javascript,
    }


def test_builtin_registry_contains_one_active_approved_fixture_per_category() -> None:
    registry = load_builtin_atomic_registry()

    assert {item.category for item in registry.definitions} == set(AtomicPatternCategory)
    assert len(registry.definitions) == len(AtomicPatternCategory)
    assert all(item.status is AtomicPatternStatus.ACTIVE for item in registry.definitions)
    assert all(item.provenance["review_state"] == "approved" for item in registry.definitions)
    assert all(item.adaptation_policy in set(AdaptationPolicy) for item in registry.definitions)


def test_v3_registry_rejects_hash_drift(tmp_path: Path) -> None:
    root = valid_atomic_catalog(tmp_path)
    (root / "widget-open-technical-v1" / "styles.css").write_text(
        "changed", encoding="utf-8"
    )

    with pytest.raises(AtomicPatternRegistryError, match="implementation hash"):
        AtomicPatternRegistry.load(root)


@pytest.mark.parametrize(
    "description",
    ["short", "x" * 79, "x" * 4001, "valid text" + "\x00" + "x" * 90],
)
def test_v3_registry_rejects_invalid_ai_description(
    tmp_path: Path,
    description: str,
) -> None:
    root = tmp_path / "catalog"
    write_atomic_pattern(
        root / "widget-open-technical-v1", ai_description=description
    )

    with pytest.raises(AtomicPatternRegistryError, match="ai_description"):
        AtomicPatternRegistry.load(root)


def test_v3_registry_rejects_unknown_files(tmp_path: Path) -> None:
    root = valid_atomic_catalog(tmp_path)
    (root / "widget-open-technical-v1" / "unexpected.txt").write_text(
        "not allowed", encoding="utf-8"
    )

    with pytest.raises(AtomicPatternRegistryError, match="unexpected"):
        AtomicPatternRegistry.load(root)


def test_v3_registry_rejects_symlink_assets(tmp_path: Path) -> None:
    root = valid_atomic_catalog(tmp_path)
    pattern = root / "widget-open-technical-v1"
    target = tmp_path / "outside.css"
    target.write_text(".outside { display: block; }", encoding="utf-8")
    link = pattern / "behavior.js"
    link.unlink()
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks are unavailable in this environment: {exc}")

    with pytest.raises(AtomicPatternRegistryError, match="symlink"):
        AtomicPatternRegistry.load(root)


def test_v3_registry_rejects_forbidden_markers(tmp_path: Path) -> None:
    root = valid_atomic_catalog(tmp_path)
    (root / "widget-open-technical-v1" / "behavior.js").write_text(
        "fetch('https://example.invalid')", encoding="utf-8"
    )

    with pytest.raises(AtomicPatternRegistryError, match="forbidden"):
        AtomicPatternRegistry.load(root)


def test_v3_registry_allows_missing_optional_behavior(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    write_atomic_pattern(root / "widget-open-technical-v1", behavior=None)

    definition = AtomicPatternRegistry.load(root).resolve("widget-open-technical", 1)

    assert definition.javascript == ""


def test_v3_registry_rejects_duplicate_pattern_version(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    write_atomic_pattern(root / "a", pattern_id="same-pattern")
    write_atomic_pattern(root / "b", pattern_id="same-pattern")

    with pytest.raises(AtomicPatternRegistryError, match="duplicate"):
        AtomicPatternRegistry.load(root)


def test_v3_registry_order_is_deterministic(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    write_atomic_pattern(root / "z-pattern-v1", pattern_id="z-pattern")
    write_atomic_pattern(root / "a-pattern-v1", pattern_id="a-pattern")

    first = AtomicPatternRegistry.load(root)
    second = AtomicPatternRegistry.load(root)

    assert first.selector_catalog() == second.selector_catalog()
    assert [item.pattern_id for item in first.definitions] == [
        "a-pattern",
        "z-pattern",
    ]


def test_v3_manifest_has_exact_top_level_field_set(tmp_path: Path) -> None:
    root = valid_atomic_catalog(tmp_path)
    manifest_path = root / "widget-open-technical-v1" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert set(manifest) == ATOMIC_FIELDS


def test_legacy_registry_remains_available_and_unchanged() -> None:
    registry = PatternRegistry.load(Path("builder_lab/patterns/catalog"))

    assert PatternCategory.LAUNCHER in set(item.category for item in registry.definitions)
    assert len(registry.definitions) == 30
