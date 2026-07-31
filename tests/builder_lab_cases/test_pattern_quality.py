from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from builder_lab.patterns.models import PatternCategory
from builder_lab.patterns.quality import (
    PatternSourceError,
    validate_source_css,
    validate_source_fragment,
)
from builder_lab.patterns.registry import PatternRegistry, source_expected_files

from ._pattern_source_fixtures import write_source_pattern


def source_definition(
    tmp_path: Path,
    *,
    category: PatternCategory = PatternCategory.LAUNCHER,
):
    pattern_id = f"quality-{category.value}"
    root = f"kaigo-pattern-{category.value}-{pattern_id}"
    write_source_pattern(
        tmp_path / f"{pattern_id}-v1",
        pattern_id=pattern_id,
        category=category,
        root_class=root,
        css=_valid_category_css(category, root),
    )
    return PatternRegistry.load(tmp_path).resolve(pattern_id, 1)


def _valid_category_css(category: PatternCategory, root: str) -> str:
    prefix = f".kaigo-widget.{root}"
    if category is PatternCategory.LAUNCHER:
        return (
            f'{prefix} [data-region="launcher"] '
            "{ min-width:44px; min-height:44px; }\n"
        )
    if category is PatternCategory.SHELL:
        return (
            f'{prefix} [data-region="panel"] '
            "{ max-width:440px; max-height:78dvh; }\n"
            f'{prefix} [data-region="header"] {{ display:flex; }}\n'
            f'{prefix} [data-region="messages"] {{ display:flex; }}\n'
            f'{prefix} [data-region="composer"] {{ display:grid; }}\n'
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
        )
    if category is PatternCategory.COMPOSER:
        return (
            f"{prefix} .kaigo-widget__composer textarea "
            "{ min-width:0; min-height:44px; }\n"
            f"{prefix} .kaigo-widget__composer button "
            "{ min-width:44px; min-height:44px; }\n"
            f"{prefix} .kaigo-widget__composer:focus-within {{ outline:2px solid; }}\n"
            f"{prefix} .kaigo-widget__composer button:focus-visible {{ outline:2px solid; }}\n"
            f"{prefix} .kaigo-widget__composer button:disabled {{ opacity:.5; }}\n"
            f'{prefix}[data-state="pending"] .kaigo-widget__composer button '
            "{ opacity:.6; }\n"
        )
    return (
        f'{prefix} [data-region="panel"] {{ opacity:0; transition:opacity 180ms ease; }}\n'
        f'{prefix}.kaigo-preview-open [data-region="panel"] {{ opacity:1; }}\n'
        "@media (prefers-reduced-motion: reduce) {\n"
        f'  {prefix} [data-region="panel"] {{ transition:none; }}\n'
        "}\n"
    )


@pytest.mark.parametrize("category", list(PatternCategory))
def test_runtime_source_css_requires_category_contract(
    tmp_path: Path,
    category: PatternCategory,
) -> None:
    base = source_definition(tmp_path)
    definition = replace(base, category=category)
    root = definition.source_contract.root_class

    with pytest.raises(PatternSourceError, match=category.value):
        validate_source_css(
            definition,
            f".kaigo-widget.{root} {{ display:block; }}",
        )


@pytest.mark.parametrize("category", list(PatternCategory))
def test_runtime_source_css_accepts_complete_category_contract(
    tmp_path: Path,
    category: PatternCategory,
) -> None:
    base = source_definition(tmp_path)
    definition = replace(base, category=category)
    root = definition.source_contract.root_class

    validate_source_css(definition, _valid_category_css(category, root))


def test_category_contract_ignores_selector_names_inside_css_strings(
    tmp_path: Path,
) -> None:
    base = source_definition(tmp_path)
    definition = replace(base, category=PatternCategory.MESSAGES)
    root = definition.source_contract.root_class
    fake_contract = (
        f'.kaigo-widget.{root}::before {{ '
        "content: '.kaigo-widget__message--assistant "
        '[data-kaigo-runtime-message="assistant"] '
        '[data-kaigo-runtime-message="user"] '
        "[data-kaigo-runtime-label] [data-kaigo-runtime-content]'; "
        "max-width:82%; }"
    )

    with pytest.raises(PatternSourceError, match="messages"):
        validate_source_css(definition, fake_contract)


@pytest.mark.parametrize(
    "fragment",
    [
        "<form></form>",
        "<slot name='messages'></slot>",
        "<button onclick='send()'>Отправить</button>",
        "<div data-region='messages'></div>",
        "<div data-kaigo-runtime-message='assistant'></div>",
        "<div id='shared-id'></div>",
        "<a href='#'>Ссылка</a>",
        "<div tabindex='0'>Фокус</div>",
        "<div contenteditable='true'>Редактор</div>",
    ],
)
def test_runtime_source_fragment_rejects_owned_or_colliding_markup(
    tmp_path: Path,
    fragment: str,
) -> None:
    definition = source_definition(tmp_path)

    with pytest.raises(PatternSourceError):
        validate_source_fragment(definition, fragment)


def test_runtime_source_fragment_rejects_duplicate_attributes(
    tmp_path: Path,
) -> None:
    definition = source_definition(tmp_path)

    with pytest.raises(PatternSourceError, match="duplicate"):
        validate_source_fragment(
            definition,
            '<span class="one" class="two"></span>',
        )


def test_runtime_source_fragment_is_bounded(tmp_path: Path) -> None:
    definition = source_definition(tmp_path)
    fragment = "".join("<span>x</span>" for _ in range(41))

    with pytest.raises(PatternSourceError, match="40"):
        validate_source_fragment(definition, fragment)


def test_runtime_source_fragment_rejects_category_incompatible_block_tag(
    tmp_path: Path,
) -> None:
    launcher = source_definition(tmp_path)

    with pytest.raises(PatternSourceError, match="launcher"):
        validate_source_fragment(launcher, "<div>Неуместный блок</div>")


@pytest.mark.parametrize(
    "class_name",
    [
        "kaigo-widget",
        "kaigo-widget__messages",
        "kaigo-pattern-launcher-shadow-root",
        "kaigo-preview-open",
    ],
)
def test_runtime_source_fragment_rejects_compiler_owned_class_names(
    tmp_path: Path,
    class_name: str,
) -> None:
    definition = source_definition(tmp_path)

    with pytest.raises(PatternSourceError, match="class namespace"):
        validate_source_fragment(
            definition,
            f'<span class="safe-decoration {class_name}"></span>',
        )


@pytest.mark.parametrize(
    "attribute,value",
    [
        ("fill", "https://evil.test/paint.svg#gradient"),
        ("stroke", "data:image/svg+xml;base64,PHN2Zy8+"),
        ("stop-color", "url(https://evil.test/paint.svg#gradient)"),
        ("fill", r"u\72l(https://evil.test/paint.svg#gradient)"),
        ("fill", "url(#local-gradient)"),
    ],
)
def test_runtime_source_fragment_rejects_svg_paint_references(
    tmp_path: Path,
    attribute: str,
    value: str,
) -> None:
    definition = source_definition(tmp_path)

    with pytest.raises(PatternSourceError, match="SVG paint"):
        validate_source_fragment(
            definition,
            f'<svg viewBox="0 0 10 10"><rect {attribute}="{value}" /></svg>',
        )


@pytest.mark.parametrize(
    "attribute,value",
    [
        ("fill", "none"),
        ("stroke", "currentColor"),
        ("stop-color", "#12abEF"),
        ("fill", "rgb(12 34 56 / 80%)"),
    ],
)
def test_runtime_source_fragment_accepts_safe_svg_paint_values(
    tmp_path: Path,
    attribute: str,
    value: str,
) -> None:
    definition = source_definition(tmp_path)

    validate_source_fragment(
        definition,
        f'<svg viewBox="0 0 10 10"><rect {attribute}="{value}" /></svg>',
    )


def test_runtime_source_shell_fragment_accepts_bounded_flow_markup(
    tmp_path: Path,
) -> None:
    shell = source_definition(tmp_path, category=PatternCategory.SHELL)

    facts = validate_source_fragment(
        shell,
        "<div><p>Декоративная подпись</p></div>",
    )

    assert facts.tags == ("div", "p")


@pytest.mark.parametrize(
    "css",
    [
        "body { color: red; }",
        ".kaigo-launcher { color: red; }",
        ".kaigo-widget.other-root { color: red; }",
        ".kaigo-widget.kaigo-pattern-launcher-quality-launcher + body { color:red; }",
        "@import url(https://example.com/a.css);",
    ],
)
def test_runtime_source_css_requires_exact_root_scope(
    tmp_path: Path,
    css: str,
) -> None:
    definition = source_definition(tmp_path)

    with pytest.raises(PatternSourceError):
        validate_source_css(definition, css)


def test_runtime_source_css_rejects_untrusted_keyframe_token(
    tmp_path: Path,
) -> None:
    definition = source_definition(tmp_path)
    root = definition.source_contract.root_class
    css = (
        f".kaigo-widget.{root} [data-region='panel'] "
        "{ animation: private-motion 1s ease; }\n"
        "@keyframes private-motion { to { opacity: 1; } }\n"
        "@media (prefers-reduced-motion: reduce) { "
        f".kaigo-widget.{root} [data-region='panel'] "
        "{ animation: none; transition: none; } }"
    )

    with pytest.raises(PatternSourceError, match="keyframe"):
        validate_source_css(definition, css)


def test_runtime_source_css_rejects_private_motion_state(tmp_path: Path) -> None:
    definition = source_definition(tmp_path, category=PatternCategory.MOTION)
    root = definition.source_contract.root_class

    with pytest.raises(PatternSourceError, match="runtime state"):
        validate_source_css(
            definition,
            f".kaigo-widget.{root}[data-opening='true'] {{ opacity: 1; }}",
        )


@pytest.mark.parametrize(
    "escaped_css",
    [
        r"background:u\72l(https://evil.test/a.png)",
        r"--kaigo\2dpattern-escaped:1",
    ],
)
def test_runtime_source_css_rejects_unquoted_css_escapes(
    tmp_path: Path,
    escaped_css: str,
) -> None:
    definition = source_definition(tmp_path)
    root = definition.source_contract.root_class

    with pytest.raises(PatternSourceError, match="CSS escape"):
        validate_source_css(
            definition,
            f".kaigo-widget.{root} {{ {escaped_css}; }}",
        )


def test_runtime_source_css_requires_reduced_motion_override(
    tmp_path: Path,
) -> None:
    definition = source_definition(tmp_path)
    root = definition.source_contract.root_class

    with pytest.raises(PatternSourceError, match="reduced motion"):
        validate_source_css(
            definition,
            f".kaigo-widget.{root} {{ transition: opacity 1s; }}",
        )


def test_runtime_source_css_parser_ignores_brace_inside_quoted_selector_value(
    tmp_path: Path,
) -> None:
    definition = source_definition(tmp_path)
    root = definition.source_contract.root_class

    validate_source_css(
        definition,
        _valid_category_css(PatternCategory.LAUNCHER, root)
        + f'.kaigo-widget.{root}[data-open="{{"] {{ opacity: 1; }}',
    )


def test_runtime_source_css_rejects_native_nesting_escape(tmp_path: Path) -> None:
    definition = source_definition(tmp_path)
    root = definition.source_contract.root_class

    with pytest.raises(PatternSourceError, match="nesting"):
        validate_source_css(
            definition,
            f".kaigo-widget.{root} {{ & ~ * {{ display: none; }} }}",
        )


def test_runtime_source_css_accepts_quoted_brace_in_ordinary_declaration(
    tmp_path: Path,
) -> None:
    definition = source_definition(tmp_path)
    root = definition.source_contract.root_class

    validate_source_css(
        definition,
        _valid_category_css(PatternCategory.LAUNCHER, root)
        + f'.kaigo-widget.{root}::before {{ content: "{{"; display: block; }}',
    )


def test_runtime_source_has_no_javascript_channel() -> None:
    assert source_expected_files() == {
        "manifest.json",
        "fragment.html",
        "styles.css",
    }
