from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from typing import Iterable
from urllib.parse import urlsplit

from .models import ValidationIssue, WidgetArtifact


MAX_HTML_BYTES = 64 * 1024
MAX_CSS_BYTES = 96 * 1024
MAX_DOM_NODES = 400
MAX_ANIMATIONS = 8
MAX_ANIMATION_SECONDS = 20.0
MAX_ANIMATION_ITERATIONS = 12

REQUIRED_REGIONS = frozenset(
    {"root", "launcher", "panel", "header", "messages", "suggestions", "composer"}
)
FORBIDDEN_ELEMENTS = frozenset(
    {"script", "iframe", "object", "embed", "base", "meta", "form", "link", "style"}
)
ALLOWED_ELEMENTS = frozenset(
    {
        "section",
        "div",
        "header",
        "main",
        "footer",
        "aside",
        "nav",
        "button",
        "span",
        "p",
        "h1",
        "h2",
        "h3",
        "h4",
        "ul",
        "ol",
        "li",
        "strong",
        "em",
        "small",
        "details",
        "summary",
        "label",
        "input",
        "textarea",
        "a",
        "img",
        "svg",
        "g",
        "path",
        "circle",
        "ellipse",
        "rect",
        "line",
        "polyline",
        "polygon",
        "defs",
        "lineargradient",
        "radialgradient",
        "stop",
    }
)
VOID_ELEMENTS = frozenset({"input", "img", "line", "path", "circle", "ellipse", "rect", "stop"})
COMMON_ATTRIBUTES = frozenset(
    {
        "class",
        "id",
        "role",
        "title",
        "type",
        "tabindex",
        "placeholder",
        "value",
        "disabled",
        "readonly",
        "checked",
        "open",
        "selected",
        "for",
        "name",
        "autocomplete",
        "inputmode",
        "rows",
        "cols",
        "min",
        "max",
        "step",
        "maxlength",
        "viewbox",
        "width",
        "height",
        "fill",
        "stroke",
        "stroke-width",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-dasharray",
        "stroke-dashoffset",
        "d",
        "cx",
        "cy",
        "r",
        "rx",
        "ry",
        "x",
        "y",
        "x1",
        "x2",
        "y1",
        "y2",
        "points",
        "offset",
        "stop-color",
        "stop-opacity",
        "preserveaspectratio",
        "href",
        "src",
        "alt",
    }
)
URL_ATTRIBUTES = frozenset({"href", "src", "action", "formaction", "poster", "xlink:href"})


def _issue(code: str, field: str, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, field=field, message=message)


class _ArtifactHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.node_count = 0
        self.regions: set[str] = set()
        self.labelled_regions: set[str] = set()
        self.issues: list[ValidationIssue] = []

    def _add(self, issue: ValidationIssue) -> None:
        if issue not in self.issues:
            self.issues.append(issue)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        self.node_count += 1
        normalized_attributes = [(name.lower(), value or "") for name, value in attrs]
        attributes: dict[str, str] = {}
        for name, value in normalized_attributes:
            if name in attributes:
                self._add(
                    _issue(
                        "duplicate_attribute",
                        "body_html",
                        f"Attribute {name} must not be repeated",
                    )
                )
            else:
                attributes[name] = value
        if tag in FORBIDDEN_ELEMENTS or tag not in ALLOWED_ELEMENTS:
            self._add(_issue("forbidden_element", "body_html", f"Element <{tag}> is not allowed"))
        region = attributes.get("data-region")
        if region:
            self.regions.add(region)
            if attributes.get("aria-label") or attributes.get("title"):
                self.labelled_regions.add(region)
        for name, value in normalized_attributes:
            if name.startswith("on"):
                self._add(_issue("forbidden_attribute", "body_html", f"Attribute {name} is not allowed"))
                continue
            if not (
                name in COMMON_ATTRIBUTES
                or name.startswith("aria-")
                or name in {"data-region", "data-action", "data-suggestion", "data-state"}
            ):
                self._add(_issue("forbidden_attribute", "body_html", f"Attribute {name} is not allowed"))
            if name in URL_ATTRIBUTES:
                self._validate_url(name, value)
        if tag == "path":
            path_data = attributes.get("d", "").strip()
            if (
                not path_data
                or re.search(r"[Aa]", path_data)
                or not re.fullmatch(r"[MmLlHhVvCcSsQqTtZz0-9eE+.,\s-]+", path_data)
            ):
                self._add(
                    _issue(
                        "unsafe_svg_path",
                        "body_html",
                        "SVG path must use conservative non-arc commands",
                    )
                )
        if tag not in VOID_ELEMENTS:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if self.stack and self.stack[-1] == tag.lower():
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in VOID_ELEMENTS:
            return
        if not self.stack or self.stack[-1] != tag:
            self._add(_issue("malformed_html", "body_html", "HTML tags are not balanced"))
            if tag in self.stack:
                self.stack = self.stack[: self.stack.index(tag)]
            return
        self.stack.pop()

    def close(self) -> None:
        super().close()
        if self.stack:
            self._add(_issue("malformed_html", "body_html", "HTML tags are not balanced"))

    def _validate_url(self, name: str, value: str) -> None:
        compact = "".join(value.split()).lower()
        if compact.startswith("data:"):
            if name != "src" or not re.match(r"^data:image/(?:png|gif|webp);base64,", compact):
                self._add(_issue("unsafe_data_url", "body_html", "Only raster image data URLs are allowed"))
            return
        if name == "href" and (value == "" or value.startswith("#")):
            return
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc or value:
            self._add(_issue("external_url", "body_html", "External and relative resource URLs are not allowed"))


def _matching_brace(css: str, opening: int) -> int | None:
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(opening, len(css)):
        character = css[index]
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if quote:
            if character == quote:
                quote = None
            continue
        if character in {"'", '"'}:
            quote = character
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _css_rules(css: str) -> tuple[list[str], list[str], bool]:
    selectors: list[str] = []
    at_rules: list[str] = []
    malformed = False

    def walk(segment: str) -> None:
        nonlocal malformed
        cursor = 0
        while cursor < len(segment):
            while cursor < len(segment) and (segment[cursor].isspace() or segment[cursor] == ";"):
                cursor += 1
            if cursor >= len(segment):
                return
            opening = segment.find("{", cursor)
            if opening < 0:
                if segment[cursor:].strip():
                    malformed = True
                return
            prelude = segment[cursor:opening].strip()
            closing = _matching_brace(segment, opening)
            if closing is None:
                malformed = True
                return
            content = segment[opening + 1 : closing]
            lower = prelude.lower()
            if lower.startswith("@"):
                name = lower.split(None, 1)[0]
                at_rules.append(name)
                if name in {"@media", "@supports", "@container"}:
                    walk(content)
            else:
                selectors.extend(part.strip() for part in prelude.split(",") if part.strip())
            cursor = closing + 1

    walk(css)
    return selectors, at_rules, malformed


def _selector_is_scoped(selector: str) -> bool:
    candidate = selector.strip()
    root = re.match(
        r"^\.kaigo-widget(?:(?:__|--)[A-Za-z0-9_-]+)?(?![-_A-Za-z0-9])",
        candidate,
    )
    if root is None:
        return False
    depth = 0
    quote: str | None = None
    escaped = False
    cursor = root.end()
    while cursor < len(candidate):
        character = candidate[cursor]
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
            remainder = candidate[cursor + 1 :].lstrip()
            return bool(remainder) and _selector_is_scoped(remainder)
        elif depth == 0 and character == ">":
            return True
        elif depth == 0 and character.isspace():
            remainder = candidate[cursor:].lstrip()
            if not remainder:
                return True
            if remainder[0] in "+~":
                sibling = remainder[1:].lstrip()
                return bool(sibling) and _selector_is_scoped(sibling)
            return True
        cursor += 1
    return True


def _validate_css(css: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    def add(code: str, message: str) -> None:
        issue = _issue(code, "css", message)
        if issue not in issues:
            issues.append(issue)

    if len(css.encode("utf-8")) > MAX_CSS_BYTES:
        add("css_too_large", "Stylesheet exceeds the size limit")
    normalized = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    lower = normalized.lower()
    if re.search(r"</\s*style\b", normalized, re.IGNORECASE):
        add("unsafe_style_terminator", "CSS must not contain an HTML style terminator")
    if re.search(r"@import\b", lower):
        add("unsafe_css_at_rule", "CSS @import is not allowed")
    if re.search(r"url\s*\(", lower):
        add("external_css_resource", "CSS url() resources are not allowed")
    if re.search(r"expression\s*\(|javascript\s*:|behavior\s*:", lower):
        add("unsafe_css_value", "Unsafe CSS value is not allowed")

    selectors, at_rules, malformed = _css_rules(normalized)
    if malformed:
        add("malformed_css", "CSS blocks are not balanced")
    for at_rule in at_rules:
        if at_rule not in {"@media", "@supports", "@container", "@keyframes", "@-webkit-keyframes"}:
            add("unsafe_css_at_rule", f"CSS at-rule {at_rule} is not allowed")
    for selector in selectors:
        if not _selector_is_scoped(selector) or re.search(
            r"(^|[\s>+~])(html|body|:root)([\s>+~.#:]|$)", selector, re.I
        ):
            excerpt = re.sub(r"\s+", " ", selector).strip()[:240]
            add(
                "unscoped_css",
                "Every selector must be scoped under .kaigo-widget; "
                f"offending selector: {excerpt}",
            )
            break

    animation_count = len(re.findall(r"@(?:-webkit-)?keyframes\b", lower))
    if animation_count > MAX_ANIMATIONS:
        add("too_many_animations", "Artifact defines too many animations")
    animation_values = re.findall(r"animation(?:-duration|-iteration-count)?\s*:\s*([^;}]+)", lower)
    if animation_count or animation_values:
        if "prefers-reduced-motion" not in lower or not re.search(
            r"prefers-reduced-motion\s*:\s*reduce", lower
        ):
            add("missing_reduced_motion", "Motion requires a prefers-reduced-motion fallback")
    for value in animation_values:
        for number, unit in re.findall(r"(?<![-\w.])(\d+(?:\.\d+)?)(ms|s)\b", value):
            seconds = float(number) / 1000 if unit == "ms" else float(number)
            if seconds > MAX_ANIMATION_SECONDS:
                add("animation_too_long", "Animation duration exceeds the limit")
                break
        if "infinite" in value:
            add("animation_iterations_exceeded", "Infinite animations are not allowed")
        for number in re.findall(r"(?<![-\w.])(\d+)(?![\w.%])", value):
            if int(number) > MAX_ANIMATION_ITERATIONS:
                add("animation_iterations_exceeded", "Animation iteration count exceeds the limit")
                break
    return issues


def validate_artifact(
    artifact: WidgetArtifact,
    *,
    previous_revision: int = 0,
) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    if artifact.schema_version != "1.0":
        issues.append(_issue("unsupported_schema", "schema_version", "Only schema version 1.0 is supported"))
    if artifact.revision <= previous_revision:
        issues.append(_issue("non_monotonic_revision", "revision", "Revision must increase monotonically"))
    if len(artifact.art_direction.encode("utf-8")) > 8 * 1024:
        issues.append(_issue("art_direction_too_large", "art_direction", "Art direction exceeds the size limit"))
    if len(artifact.body_html.encode("utf-8")) > MAX_HTML_BYTES:
        issues.append(_issue("html_too_large", "body_html", "HTML exceeds the size limit"))

    parser = _ArtifactHTMLParser()
    try:
        parser.feed(artifact.body_html)
        parser.close()
    except Exception:
        parser.issues.append(_issue("malformed_html", "body_html", "HTML could not be parsed"))
    issues.extend(parser.issues)
    if parser.node_count > MAX_DOM_NODES:
        issues.append(_issue("too_many_nodes", "body_html", "HTML contains too many elements"))
    for region in sorted(REQUIRED_REGIONS - parser.regions):
        issues.append(_issue("missing_region", "body_html", f"Required region {region} is missing"))
    for region in ("launcher", "composer"):
        if region in parser.regions and region not in parser.labelled_regions:
            issues.append(
                _issue("missing_accessible_label", "body_html", f"Region {region} requires an accessible label")
            )
    issues.extend(_validate_css(artifact.css))

    unique: list[ValidationIssue] = []
    for issue in issues:
        if issue not in unique:
            unique.append(issue)
    return tuple(unique)


def issue_fingerprint(issues: Iterable[ValidationIssue]) -> str:
    normalized = "\n".join(
        f"{issue.code}|{issue.field}|{issue.message}"
        for issue in sorted(issues, key=lambda item: (item.code, item.field, item.message))
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
