from __future__ import annotations

import hashlib
import json
from pathlib import Path

from builder_lab.patterns.models import (
    CompositionPlan,
    PatternCategory,
    PatternSelection,
)
from builder_lab.patterns.registry import PatternRegistry


_FRAGMENT_ROLES = {
    PatternCategory.LAUNCHER: "launcher-content",
    PatternCategory.SHELL: "shell-decoration",
    PatternCategory.MESSAGES: "message-decoration",
    PatternCategory.COMPOSER: "composer-decoration",
    PatternCategory.MOTION: "motion-none",
}


def _source_hash(
    manifest: dict[str, object],
    *,
    html: str,
    css: str,
) -> str:
    payload = {
        key: value
        for key, value in manifest.items()
        if key not in {"implementation_sha256", "status"}
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
    for asset in (html, css):
        digest.update(b"\0")
        digest.update(asset.encode("utf-8"))
    return digest.hexdigest()


def write_source_pattern(
    directory: Path,
    *,
    pattern_id: str,
    category: PatternCategory,
    version: int = 1,
    status: str = "active",
    parameter_name: str | None = None,
    root_class: str | None = None,
    css_variable: str | None = None,
    fragment: str | None = None,
    css: str | None = None,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    root_class = root_class or f"kaigo-pattern-{category.value}-{pattern_id}"
    decoration_class = f"kaigo-decoration-{category.value}"
    fragment = (
        fragment
        if fragment is not None
        else f'<span class="{decoration_class}" aria-hidden="true">AI</span>\n'
    )
    css_variable = css_variable or f"--kaigo-pattern-{pattern_id}-size"
    parameter_schema: dict[str, object] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    parameter_bindings: dict[str, object] = {}
    if parameter_name is not None:
        parameter_schema["properties"] = {
            parameter_name: {
                "type": "integer",
                "minimum": 1,
                "maximum": 200,
            }
        }
        parameter_bindings[parameter_name] = {
            "css_variable": css_variable,
            "unit": "px",
        }
    css = css or (
        f".kaigo-widget.{root_class} .{decoration_class} "
        "{ display: inline-flex; }\n"
    )
    manifest: dict[str, object] = {
        "schema_version": 2,
        "pattern_id": pattern_id,
        "version": version,
        "category": category.value,
        "status": status,
        "description": f"Безопасный тестовый паттерн {category.value}",
        "parameter_schema": parameter_schema,
        "incompatible_with": [],
        "source_contract": {
            "runtime_contract_id": "chat-v1",
            "runtime_contract_version": 1,
            "fragment_role": _FRAGMENT_ROLES[category],
            "root_class": root_class,
            "parameter_bindings": parameter_bindings,
        },
        "provenance": {
            "origin": "kaigo-owned",
            "review_state": "verified",
            "source_revision": 1,
            "supersedes": None,
        },
    }
    manifest["implementation_sha256"] = _source_hash(
        manifest,
        html=fragment,
        css=css,
    )
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (directory / "fragment.html").write_text(fragment, encoding="utf-8")
    (directory / "styles.css").write_text(css, encoding="utf-8")
    return directory


def rewrite_source_manifest(directory: Path, manifest: dict[str, object]) -> None:
    html = (directory / "fragment.html").read_text(encoding="utf-8")
    css = (directory / "styles.css").read_text(encoding="utf-8")
    manifest["implementation_sha256"] = _source_hash(
        manifest,
        html=html,
        css=css,
    )
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def source_registry_and_plan(
    root: Path,
) -> tuple[PatternRegistry, CompositionPlan]:
    selections: list[PatternSelection] = []
    for category in PatternCategory:
        pattern_id = f"test-{category.value}"
        parameter_name = "size_px" if category is PatternCategory.LAUNCHER else None
        write_source_pattern(
            root / f"{pattern_id}-v1",
            pattern_id=pattern_id,
            category=category,
            parameter_name=parameter_name,
        )
        selections.append(
            PatternSelection(
                slot=category,
                pattern_id=pattern_id,
                version=1,
                parameters={"size_px": 64} if parameter_name else {},
                reason="Проверяем безопасную сборку",
            )
        )
    return (
        PatternRegistry.load(root),
        CompositionPlan(
            schema_version=1,
            direction_id="test-direction",
            selections=tuple(selections),
            summary="Проверяем безопасную детерминированную основу",
        ),
    )
