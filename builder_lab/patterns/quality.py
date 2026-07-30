from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import re

from ..css_contract import (
    contains_unquoted_css_escape,
    is_safe_svg_paint,
    parse_css_contract,
    strip_css_comments,
)
from .models import PatternCategory
from .registry import PatternDefinition, PatternIntegrationMode


_MAX_FRAGMENT_BYTES = 16 * 1024
_MAX_FRAGMENT_NODES = 40
_MAX_SOURCE_CSS_BYTES = 64 * 1024
_FORBIDDEN_ELEMENTS = frozenset(
    {
        "a",
        "area",
        "audio",
        "base",
        "button",
        "details",
        "embed",
        "form",
        "iframe",
        "input",
        "label",
        "link",
        "meta",
        "object",
        "option",
        "script",
        "select",
        "slot",
        "style",
        "summary",
        "textarea",
        "video",
    }
)
_ALLOWED_ELEMENTS = frozenset(
    {
        "aside",
        "circle",
        "defs",
        "div",
        "ellipse",
        "em",
        "g",
        "line",
        "lineargradient",
        "li",
        "ol",
        "p",
        "path",
        "polygon",
        "polyline",
        "radialgradient",
        "rect",
        "small",
        "span",
        "stop",
        "strong",
        "svg",
        "ul",
    }
)
_INLINE_DECORATIVE_ELEMENTS = frozenset(
    {
        "circle",
        "defs",
        "ellipse",
        "em",
        "g",
        "line",
        "lineargradient",
        "path",
        "polygon",
        "polyline",
        "radialgradient",
        "rect",
        "small",
        "span",
        "stop",
        "strong",
        "svg",
    }
)
_CATEGORY_ELEMENTS = {
    PatternCategory.LAUNCHER: _INLINE_DECORATIVE_ELEMENTS,
    PatternCategory.SHELL: _ALLOWED_ELEMENTS,
    PatternCategory.MESSAGES: _INLINE_DECORATIVE_ELEMENTS,
    PatternCategory.COMPOSER: _INLINE_DECORATIVE_ELEMENTS,
    PatternCategory.MOTION: _ALLOWED_ELEMENTS,
}
_VOID_ELEMENTS = frozenset(
    {"circle", "ellipse", "line", "path", "rect", "stop"}
)
_SVG_ATTRIBUTES = frozenset(
    {
        "cx",
        "cy",
        "d",
        "fill",
        "height",
        "offset",
        "points",
        "preserveaspectratio",
        "r",
        "rx",
        "ry",
        "stop-color",
        "stop-opacity",
        "stroke",
        "stroke-dasharray",
        "stroke-dashoffset",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-width",
        "viewbox",
        "width",
        "x",
        "x1",
        "x2",
        "y",
        "y1",
        "y2",
    }
)
_ALLOWED_ATTRIBUTES = frozenset({"aria-hidden", "class", "role", "title"}) | _SVG_ATTRIBUTES
_SVG_PAINT_ATTRIBUTES = frozenset({"fill", "stroke", "stop-color"})
_URL_ATTRIBUTES = frozenset(
    {"action", "formaction", "href", "poster", "src", "xlink:href"}
)
_CUSTOM_PROPERTY_RE = re.compile(r"--[A-Za-z_][A-Za-z0-9_-]*")
_SAFE_CUSTOM_PROPERTY_RE = re.compile(r"^--kaigo-pattern-[a-z][a-z0-9-]*$")
_KEYFRAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_ALLOWED_AT_RULES = frozenset(
    {"@container", "@keyframes", "@media", "@supports"}
)
_ALLOWED_RUNTIME_ATTRIBUTES = frozenset(
    {
        "data-kaigo-runtime-content",
        "data-kaigo-runtime-label",
        "data-kaigo-runtime-message",
        "data-kaigo-runtime-retry",
        "data-kaigo-runtime-status",
        "data-open",
        "data-region",
        "data-state",
    }
)
_RESERVED_FRAGMENT_CLASS_PREFIXES = (
    "kaigo-pattern-",
    "kaigo-preview-",
    "kaigo-widget--",
    "kaigo-widget-",
    "kaigo-widget__",
)


class PatternSourceError(ValueError):
    """A runtime-source fragment or stylesheet violates the fixed runtime."""


@dataclass(frozen=True, slots=True)
class SourceFragmentFacts:
    node_count: int
    tags: tuple[str, ...]
    class_names: tuple[str, ...]


class _SourceHTMLParser(HTMLParser):
    def __init__(self, *, category: PatternCategory) -> None:
        super().__init__(convert_charrefs=True)
        self.category = category
        self.stack: list[str] = []
        self.node_count = 0
        self.tags: list[str] = []
        self.class_names: set[str] = set()
        self.errors: list[str] = []

    def _error(self, message: str) -> None:
        if message not in self.errors:
            self.errors.append(message)

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        tag = tag.lower()
        self.node_count += 1
        self.tags.append(tag)
        attributes: dict[str, str] = {}
        for raw_name, raw_value in attrs:
            name = raw_name.lower()
            value = raw_value or ""
            if name in attributes:
                self._error(f"duplicate fragment attribute: {name}")
                continue
            attributes[name] = value
        if tag in _FORBIDDEN_ELEMENTS or tag not in _ALLOWED_ELEMENTS:
            self._error(f"source fragment element <{tag}> is not decorative")
        elif tag not in _CATEGORY_ELEMENTS[self.category]:
            self._error(
                f"{self.category.value} source fragment element <{tag}> "
                "is category-incompatible"
            )
        for name, value in attributes.items():
            if name.startswith("on"):
                self._error(f"source fragment event attribute is forbidden: {name}")
            elif name in {"contenteditable", "id", "style", "tabindex"}:
                self._error(f"source fragment attribute is forbidden: {name}")
            elif name.startswith("data-"):
                self._error(f"source fragment runtime attribute is reserved: {name}")
            elif name in _URL_ATTRIBUTES:
                self._error(f"source fragment URL attribute is forbidden: {name}")
            elif name not in _ALLOWED_ATTRIBUTES:
                self._error(f"source fragment attribute is not allowed: {name}")
            if name == "role" and value.lower() not in {"none", "presentation"}:
                self._error("source fragment cannot declare an interactive role")
            if name in _SVG_PAINT_ATTRIBUTES and not is_safe_svg_paint(value):
                self._error(
                    "source fragment SVG paint must be a safe color without references"
                )
        class_names = attributes.get("class", "").split()
        for class_name in class_names:
            if _is_reserved_fragment_class(class_name):
                self._error(
                    "source fragment class namespace is owned by the compiler"
                )
        self.class_names.update(class_names)
        if tag == "path":
            path_data = attributes.get("d", "").strip()
            if (
                not path_data
                or re.search(r"[Aa]", path_data)
                or not re.fullmatch(r"[MmLlHhVvCcSsQqTtZz0-9eE+.,\s-]+", path_data)
            ):
                self._error("source fragment SVG path uses unsafe commands")
        if tag not in _VOID_ELEMENTS:
            self.stack.append(tag)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)
        if self.stack and self.stack[-1] == tag.lower():
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _VOID_ELEMENTS:
            return
        if not self.stack or self.stack[-1] != tag:
            self._error("source fragment markup is not balanced")
            return
        self.stack.pop()

    def handle_decl(self, decl: str) -> None:
        self._error("source fragment declarations are forbidden")

    def handle_pi(self, data: str) -> None:
        self._error("source fragment processing instructions are forbidden")

    def close(self) -> None:
        super().close()
        if self.stack:
            self._error("source fragment markup is not balanced")


def _require_runtime_source(definition: PatternDefinition) -> None:
    if (
        definition.integration_mode is not PatternIntegrationMode.RUNTIME_SOURCE
        or definition.source_contract is None
    ):
        raise PatternSourceError("definition is not a runtime source")


def _is_reserved_fragment_class(class_name: str) -> bool:
    return class_name == "kaigo-widget" or class_name.startswith(
        _RESERVED_FRAGMENT_CLASS_PREFIXES
    )


def validate_source_fragment(
    definition: PatternDefinition,
    html: str,
) -> SourceFragmentFacts:
    _require_runtime_source(definition)
    if not isinstance(html, str) or len(html.encode("utf-8")) > _MAX_FRAGMENT_BYTES:
        raise PatternSourceError("source fragment exceeds its size limit")
    parser = _SourceHTMLParser(category=definition.category)
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:  # pragma: no cover - parser is deliberately defensive
        raise PatternSourceError("source fragment could not be parsed") from exc
    if parser.node_count > _MAX_FRAGMENT_NODES:
        parser._error("source fragment may contain at most 40 nodes")
    if parser.errors:
        raise PatternSourceError(parser.errors[0])
    return SourceFragmentFacts(
        node_count=parser.node_count,
        tags=tuple(parser.tags),
        class_names=tuple(sorted(parser.class_names)),
    )


def _scope_prefix_matches(selector: str, required_prefix: str) -> bool:
    if not selector.startswith(required_prefix):
        return False
    remainder = selector[len(required_prefix) :]
    if remainder and remainder[0] not in " .:#[]>+~":
        return False
    depth = 0
    quote: str | None = None
    escaped = False
    for character in remainder:
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif quote:
            if character == quote:
                quote = None
        elif character in {"'", '"'}:
            quote = character
        elif character in "([":
            depth += 1
        elif character in ")]" and depth:
            depth -= 1
        elif depth == 0 and character in "+~":
            return False
    return not re.search(
        r"(^|[\s>+~])(html|body|:root)([\s>+~.#:]|$)",
        selector,
        re.IGNORECASE,
    )


def _at_rule_name(prelude: str) -> str:
    match = re.match(r"^(@[-a-z]+)\b", prelude.lower())
    return match.group(1) if match else ""


def validate_source_css(definition: PatternDefinition, css: str) -> None:
    _require_runtime_source(definition)
    if not isinstance(css, str) or len(css.encode("utf-8")) > _MAX_SOURCE_CSS_BYTES:
        raise PatternSourceError("runtime source CSS exceeds its size limit")
    normalized, comment_malformed = strip_css_comments(css)
    lower = normalized.lower()
    if comment_malformed or re.search(r"</\s*style\b", normalized, re.IGNORECASE):
        raise PatternSourceError("runtime source CSS is malformed")
    if contains_unquoted_css_escape(normalized):
        raise PatternSourceError("runtime source CSS escape is not allowed")
    if (
        re.search(r"@import\b", lower)
        or re.search(r"url\s*\(", lower)
        or re.search(r"expression\s*\(|javascript\s*:", lower)
        or re.search(r"(?:^|[;{])\s*behavior\s*:", lower)
    ):
        raise PatternSourceError("runtime source CSS contains an unsafe capability")

    parsed = parse_css_contract(normalized)
    if parsed.unsupported_nesting:
        raise PatternSourceError("runtime source native CSS nesting is not allowed")
    if parsed.malformed:
        raise PatternSourceError("runtime source CSS blocks are not balanced")
    disallowed_at_rule = next(
        (name for name in parsed.at_rules if name not in _ALLOWED_AT_RULES),
        None,
    )
    if disallowed_at_rule is not None:
        raise PatternSourceError(
            f"runtime source CSS at-rule is not allowed: {disallowed_at_rule}"
        )

    contract = definition.source_contract
    if contract is None:  # pragma: no cover - guarded by _require_runtime_source
        raise PatternSourceError("runtime source contract is missing")
    required_prefix = f".kaigo-widget.{contract.root_class}"
    if not parsed.selectors:
        raise PatternSourceError("runtime source CSS must contain a scoped rule")
    offending = next(
        (
            selector
            for selector in parsed.selectors
            if not _scope_prefix_matches(selector, required_prefix)
        ),
        None,
    )
    if offending is not None:
        raise PatternSourceError(
            "runtime source CSS escapes its exact root class: " + offending[:160]
        )

    for variable in _CUSTOM_PROPERTY_RE.findall(normalized):
        if not _SAFE_CUSTOM_PROPERTY_RE.fullmatch(variable):
            raise PatternSourceError("runtime source CSS variable token is invalid")

    keyframe_prefix = f"kaigo-pattern-{definition.pattern_id}-"
    for prelude in parsed.at_rule_preludes:
        if _at_rule_name(prelude) != "@keyframes":
            continue
        parts = prelude.split()
        if len(parts) != 2:
            raise PatternSourceError("runtime source keyframe declaration is invalid")
        name = parts[1]
        if not _KEYFRAME_RE.fullmatch(name) or not name.startswith(keyframe_prefix):
            raise PatternSourceError("runtime source keyframe name is invalid")

    preview_states = set(re.findall(r"\.kaigo-preview-([a-z0-9-]+)", lower))
    if preview_states - {"attention", "open"}:
        raise PatternSourceError("runtime source CSS references a private runtime state")
    data_attributes = set(re.findall(r"\[\s*(data-[a-z0-9-]+)", lower))
    if data_attributes - _ALLOWED_RUNTIME_ATTRIBUTES:
        raise PatternSourceError("runtime source CSS references a private runtime state")
    if definition.category is PatternCategory.MOTION and re.search(
        r"\[\s*data-(?:opening|closing|attention-active|motion)",
        lower,
    ):
        raise PatternSourceError("motion CSS must use only trusted runtime states")

    has_motion = bool(
        re.search(r"(?:^|[;{])\s*(?:animation|transition)(?:-[a-z]+)?\s*:", lower)
        or "@keyframes" in parsed.at_rules
    )
    if has_motion and not re.search(
        r"@media\s*\(\s*prefers-reduced-motion\s*:\s*reduce\s*\)",
        lower,
    ):
        raise PatternSourceError("runtime source CSS requires a reduced motion override")


__all__ = [
    "PatternSourceError",
    "SourceFragmentFacts",
    "validate_source_css",
    "validate_source_fragment",
]
