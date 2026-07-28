from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from builder_lab.patterns.models import PatternCategory
from builder_lab.patterns.registry import (
    PatternRegistry,
    PatternRegistryError,
    load_builtin_registry,
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

    assert len(registry.definitions) == 10
    for category in PatternCategory:
        assert len(registry.active_for(category)) >= 2


def test_public_catalog_excludes_implementation_assets() -> None:
    catalog = load_builtin_registry().public_catalog()

    assert catalog
    assert all("html" not in item and "css" not in item for item in catalog)
    assert all("implementation_sha256" in item for item in catalog)

