from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from builder_lab.patterns.models import PatternCategory
from builder_lab.patterns.source import (
    PatternCompilationError,
    canonical_anatomy_fingerprint,
    compile_runtime_source,
)
from builder_lab.preview import build_trusted_runtime_document
from builder_lab.validation import validate_artifact

from ._pattern_source_fixtures import source_registry_and_plan


def test_compiler_owns_runtime_anatomy(tmp_path: Path) -> None:
    registry, plan = source_registry_and_plan(tmp_path)

    source = compile_runtime_source(plan, registry)

    assert source.body_html.count('data-region="root"') == 1
    for region in (
        "launcher",
        "panel",
        "header",
        "messages",
        "suggestions",
        "composer",
    ):
        assert source.body_html.count(f'data-region="{region}"') == 1
    assert source.body_html.count('data-action="open"') == 1
    assert source.body_html.count('data-action="close"') == 1
    assert source.body_html.count('data-action="send"') == 1
    assert source.body_html.count("data-suggestion=") == 2
    assert source.body_html.count('id="kaigo-message"') == 1
    assert "<form" not in source.body_html
    assert "<slot" not in source.body_html
    assert source.javascript == ""
    assert validate_artifact(
        source.as_seed(revision=1, art_direction="Тестовое направление")
    ) == ()


def test_compiler_hash_and_bundle_are_canonical(tmp_path: Path) -> None:
    registry, plan = source_registry_and_plan(tmp_path)

    first = compile_runtime_source(plan, registry)
    second = compile_runtime_source(plan, registry)

    assert first.source_sha256 == second.source_sha256
    assert first.body_html == second.body_html
    assert first.css == second.css
    assert first.compiler_bundle_snapshot == second.compiler_bundle_snapshot
    assert first.provenance.base_css_sha256 in first.compiler_bundle_snapshot
    assert first.provenance.compiler_bundle_id
    assert first.provenance.anatomy_sha256 == canonical_anatomy_fingerprint(
        first.body_html,
        expected_root_classes=first.root_classes,
    )


def test_css_parameter_binding_cannot_escape_declaration(tmp_path: Path) -> None:
    registry, plan = source_registry_and_plan(tmp_path)
    selections = list(plan.selections)
    launcher_index = next(
        index
        for index, selection in enumerate(selections)
        if selection.slot is PatternCategory.LAUNCHER
    )
    current = selections[launcher_index]
    selections[launcher_index] = replace(
        current,
        parameters={"size_px": "1px;}body{"},
    )
    escaped = replace(plan, selections=tuple(selections))

    with pytest.raises((ValueError, PatternCompilationError)):
        compile_runtime_source(escaped, registry)


def test_compiler_rejects_custom_escape_before_source_contract_exists(
    tmp_path: Path,
) -> None:
    registry, plan = source_registry_and_plan(tmp_path)
    selections = tuple(
        selection
        for selection in plan.selections
        if selection.slot is not PatternCategory.MOTION
    )
    from builder_lab.patterns.models import CustomPatternEscape

    escaped = replace(
        plan,
        selections=selections,
        custom_escape=CustomPatternEscape(
            slot=PatternCategory.MOTION,
            reason="Нужна нестандартная анимация",
        ),
    )

    with pytest.raises(PatternCompilationError, match="custom_escape"):
        compile_runtime_source(escaped, registry)


def test_anatomy_fingerprint_rejects_duplicate_runtime_region(
    tmp_path: Path,
) -> None:
    registry, plan = source_registry_and_plan(tmp_path)
    source = compile_runtime_source(plan, registry)
    broken = source.body_html.replace(
        "</main>",
        '<div data-region="messages"></div></main>',
    )

    with pytest.raises(PatternCompilationError, match="exactly one"):
        canonical_anatomy_fingerprint(
            broken,
            expected_root_classes=source.root_classes,
        )


def test_compiled_source_exposes_trusted_runtime_audit_contract(
    tmp_path: Path,
) -> None:
    registry, plan = source_registry_and_plan(tmp_path)
    source = compile_runtime_source(plan, registry)

    contract = source.browser_audit_contract()
    document = build_trusted_runtime_document(
        source.as_seed(revision=1, art_direction="Проверка trusted runtime")
    )

    assert contract.runtime_contract == "chat-v1@1"
    assert contract.expected_pattern_root_classes == source.root_classes
    assert contract.source_sha256 == source.source_sha256
    assert "data-kaigo-generated" not in document
    assert all(root_class in document for root_class in source.root_classes)
