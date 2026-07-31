from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from builder_lab.patterns.models import PatternCategory
from builder_lab.patterns.registry import (
    PatternIntegrationMode,
    PatternRegistry,
    PatternRegistryError,
    load_builtin_registry,
)

from ._pattern_source_fixtures import (
    rewrite_source_manifest,
    write_source_pattern,
)


def implementation_hash(
    manifest: dict[str, object],
    *,
    html: str,
    css: str,
    javascript: str,
) -> str:
    payload = {
        key: value
        for key, value in manifest.items()
        if key != "implementation_sha256"
    }
    digest = hashlib.sha256()
    digest.update(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    for asset in (html, css, javascript):
        digest.update(b"\0")
        digest.update(asset.encode("utf-8"))
    return digest.hexdigest()


def write_pattern(
    root: Path,
    *,
    pattern_id: str = "orb-pulse",
    version: int = 1,
    category: str = "launcher",
    implementation_sha256: str | None = None,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    html = '<button class="kaigo-launcher" type="button">AI</button>\n'
    css = ".kaigo-launcher { color: var(--kaigo-accent); }\n"
    javascript = "export function mount(root) { return root; }\n"
    manifest: dict[str, object] = {
        "schema_version": 1,
        "pattern_id": pattern_id,
        "version": version,
        "category": category,
        "status": "active",
        "description": "Проверенный тестовый паттерн",
        "parameter_schema": {"type": "object", "additionalProperties": False},
        "incompatible_with": [],
    }
    manifest["implementation_sha256"] = implementation_sha256 or implementation_hash(
        manifest,
        html=html,
        css=css,
        javascript=javascript,
    )
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (root / "fragment.html").write_text(html, encoding="utf-8")
    (root / "styles.css").write_text(css, encoding="utf-8")
    (root / "behavior.js").write_text(javascript, encoding="utf-8")


def test_registry_rejects_asset_hash_mismatch(tmp_path: Path) -> None:
    write_pattern(tmp_path / "orb-pulse-v1", implementation_sha256="0" * 64)

    with pytest.raises(PatternRegistryError, match="hash"):
        PatternRegistry.load(tmp_path)


def test_registry_rejects_duplicate_active_versions(tmp_path: Path) -> None:
    write_pattern(tmp_path / "a", pattern_id="orb-pulse", version=1)
    write_pattern(tmp_path / "b", pattern_id="orb-pulse", version=1)

    with pytest.raises(PatternRegistryError, match="duplicate"):
        PatternRegistry.load(tmp_path)


def test_registry_rejects_unexpected_asset(tmp_path: Path) -> None:
    pattern = tmp_path / "orb-pulse-v1"
    write_pattern(pattern)
    (pattern / "surprise.txt").write_text("not part of the contract", encoding="utf-8")

    with pytest.raises(PatternRegistryError, match="unexpected"):
        PatternRegistry.load(tmp_path)


def test_builtin_catalog_has_two_patterns_per_category() -> None:
    registry = load_builtin_registry()

    assert len(registry.definitions) == 30
    for category in PatternCategory:
        assert len(registry.active_for(category)) == 6


def test_public_catalog_excludes_implementation_assets() -> None:
    catalog = load_builtin_registry().public_catalog()

    assert catalog
    assert all("html" not in item and "css" not in item for item in catalog)
    assert all("implementation_sha256" in item for item in catalog)


def test_v1_is_loaded_for_history_but_not_selectable() -> None:
    registry = load_builtin_registry()

    legacy = registry.resolve("orb-pulse", 1)

    assert legacy.integration_mode is PatternIntegrationMode.LEGACY_REFERENCE
    assert legacy not in registry.selectable_for(PatternCategory.LAUNCHER)
    assert len(registry.planner_catalog()) == 20
    assert len(registry.public_catalog()) == 10


def test_source_manifest_rejects_behavior_javascript(tmp_path: Path) -> None:
    pattern = write_source_pattern(
        tmp_path / "status-capsule-v1",
        pattern_id="status-capsule",
        category=PatternCategory.LAUNCHER,
    )
    (pattern / "behavior.js").write_text(
        "setTimeout(() => {}, 1)",
        encoding="utf-8",
    )

    with pytest.raises(PatternRegistryError, match="unexpected pattern asset"):
        PatternRegistry.load(tmp_path)


def test_source_manifest_requires_runtime_provenance(tmp_path: Path) -> None:
    pattern = write_source_pattern(
        tmp_path / "status-capsule-v1",
        pattern_id="status-capsule",
        category=PatternCategory.LAUNCHER,
    )
    manifest = json.loads((pattern / "manifest.json").read_text(encoding="utf-8"))
    del manifest["provenance"]
    rewrite_source_manifest(pattern, manifest)

    with pytest.raises(PatternRegistryError, match="manifest fields"):
        PatternRegistry.load(tmp_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("root_class", "kaigo-pattern-ok}body"),
        ("css_variable", "--kaigo-pattern-ok;color:red"),
    ],
)
def test_source_manifest_uses_strict_css_token_grammar(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    kwargs = {
        "root_class": value if field == "root_class" else None,
        "css_variable": value if field == "css_variable" else None,
    }
    write_source_pattern(
        tmp_path / "status-capsule-v1",
        pattern_id="status-capsule",
        category=PatternCategory.LAUNCHER,
        parameter_name="size_px" if field == "css_variable" else None,
        **kwargs,
    )

    with pytest.raises(PatternRegistryError, match=field.replace("_", " ")):
        PatternRegistry.load(tmp_path)


def test_source_status_is_operational_and_does_not_change_immutable_hash(
    tmp_path: Path,
) -> None:
    pattern = write_source_pattern(
        tmp_path / "status-capsule-v1",
        pattern_id="status-capsule",
        category=PatternCategory.LAUNCHER,
    )
    active = PatternRegistry.load(tmp_path).resolve("status-capsule", 1)
    manifest = json.loads((pattern / "manifest.json").read_text(encoding="utf-8"))
    manifest["status"] = "deprecated"
    original_hash = manifest["implementation_sha256"]
    (pattern / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    deprecated_registry = PatternRegistry.load(tmp_path)
    deprecated = deprecated_registry.resolve("status-capsule", 1)

    assert deprecated.implementation_sha256 == original_hash
    assert deprecated.implementation_sha256 == active.implementation_sha256
    assert deprecated not in deprecated_registry.selectable_for(
        PatternCategory.LAUNCHER
    )


def test_source_public_snapshot_has_contract_but_no_assets(tmp_path: Path) -> None:
    write_source_pattern(
        tmp_path / "status-capsule-v1",
        pattern_id="status-capsule",
        category=PatternCategory.LAUNCHER,
    )

    public = PatternRegistry.load(tmp_path).resolve("status-capsule", 1).public_dict()

    assert public["integration_mode"] == "runtime_source"
    assert public["source_contract"]["runtime_contract_id"] == "chat-v1"
    assert public["provenance"]["review_state"] == "verified"
    assert not {"html", "css", "javascript"}.intersection(public)


def test_legacy_manifest_snapshot_shape_is_unchanged() -> None:
    legacy = load_builtin_registry().resolve("orb-pulse", 1).public_dict()

    assert set(legacy) == {
        "pattern_id",
        "version",
        "category",
        "status",
        "description",
        "parameter_schema",
        "incompatible_with",
        "implementation_sha256",
    }
