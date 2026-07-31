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


def _category_css(
    category: PatternCategory,
    *,
    root_class: str,
    decoration_class: str,
) -> str:
    prefix = f".kaigo-widget.{root_class}"
    decoration = (
        f"{prefix} .{decoration_class} {{ display: inline-flex; }}\n"
    )
    if category is PatternCategory.LAUNCHER:
        return (
            f'{prefix} [data-region="launcher"] '
            "{ min-width:44px; min-height:44px; }\n"
            + decoration
        )
    if category is PatternCategory.SHELL:
        return (
            f'{prefix} [data-region="panel"] '
            "{ max-width:440px; max-height:78dvh; }\n"
            f'{prefix} [data-region="header"] {{ display:flex; }}\n'
            f'{prefix} [data-region="messages"] {{ display:flex; }}\n'
            f'{prefix} [data-region="composer"] {{ display:grid; }}\n'
            + decoration
        )
    if category is PatternCategory.MESSAGES:
        return (
            f"{prefix} .kaigo-widget__message--assistant,\n"
            f'{prefix} [data-kaigo-runtime-message="assistant"] '
            "{ max-width:82%; }\n"
            f'{prefix} [data-kaigo-runtime-message="user"] '
            "{ max-width:82%; }\n"
            f"{prefix} [data-kaigo-runtime-label] {{ display:block; }}\n"
            f"{prefix} [data-kaigo-runtime-content] {{ display:block; }}\n"
            + decoration
        )
    if category is PatternCategory.COMPOSER:
        return (
            f"{prefix} .kaigo-widget__composer textarea "
            "{ min-width:0; min-height:44px; }\n"
            f"{prefix} .kaigo-widget__composer button "
            "{ min-width:44px; min-height:44px; }\n"
            f"{prefix} .kaigo-widget__composer:focus-within {{ outline:2px solid; }}\n"
            f"{prefix} .kaigo-widget__composer button:disabled {{ opacity:.5; }}\n"
            f'{prefix}[data-state="pending"] .kaigo-widget__composer button '
            "{ opacity:.6; }\n"
            + decoration
        )
    return (
        f'{prefix} [data-region="panel"] '
        "{ opacity:0; transition:opacity 180ms ease; }\n"
        f'{prefix}.kaigo-preview-open [data-region="panel"] {{ opacity:1; }}\n'
        "@media (prefers-reduced-motion: reduce) {\n"
        f'  {prefix} [data-region="panel"] {{ transition:none; }}\n'
        "}\n"
        + decoration
    )


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
    css = css or _category_css(
        category,
        root_class=root_class,
        decoration_class=decoration_class,
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
