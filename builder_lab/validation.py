from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from html.parser import HTMLParser
from typing import Iterable
from urllib.parse import urlsplit

from .css_contract import (
    contains_unquoted_css_escape,
    is_safe_svg_paint,
    parse_css_rules,
    selector_is_scoped,
    strip_css_comments,
)
from .models import ValidationIssue, WidgetArtifact


MAX_HTML_BYTES = 64 * 1024
MAX_CSS_BYTES = 96 * 1024
MAX_JAVASCRIPT_BYTES = 256 * 1024
MAX_DOM_NODES = 400

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
        "article",
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
SVG_PAINT_ATTRIBUTES = frozenset({"fill", "stroke", "stop-color"})
_HTML_TAG = re.compile(r"<[^>]+>")
_PUBLIC_AI_IDENTITY = re.compile(
    r"(?iu)(?<![A-Za-zА-Яа-яЁё])(?:ai|ии)(?![A-Za-zА-Яа-яЁё])"
)
_RESERVED_RUNTIME_ATTRIBUTE = re.compile(
    r"""(?ix)
    \s+
    (?:data-kaigo-runtime-message
      |data-kaigo-runtime-label
      |data-kaigo-runtime-content)
    (?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'=<>`]+))?
    """
)


def _issue(code: str, field: str, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, field=field, message=message)


def strip_reserved_runtime_attributes(
    artifact: WidgetArtifact,
) -> WidgetArtifact:
    """Remove static attributes that belong exclusively to the trusted runtime."""

    def clean_tag(match: re.Match[str]) -> str:
        return _RESERVED_RUNTIME_ATTRIBUTE.sub("", match.group(0))

    body_html = _HTML_TAG.sub(clean_tag, artifact.body_html)
    if body_html == artifact.body_html:
        return artifact
    return replace(artifact, body_html=body_html)


class _ArtifactHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.region_stack: list[str | None] = []
        self.assistant_message_stack: list[bool] = []
        self.assistant_message_text: list[str] = []
        self.node_count = 0
        self.regions: set[str] = set()
        self.region_parents: dict[str, str | None] = {}
        self.labelled_regions: set[str] = set()
        self.root_has_widget_class = False
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
            if region == "root" and "kaigo-widget" in attributes.get(
                "class", ""
            ).split():
                self.root_has_widget_class = True
            parent_region = next(
                (item for item in reversed(self.region_stack) if item), None
            )
            self.region_parents.setdefault(region, parent_region)
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
            if name in SVG_PAINT_ATTRIBUTES and not is_safe_svg_paint(value):
                self._add(
                    _issue(
                        "unsafe_svg_paint",
                        "body_html",
                        "SVG paint must be a color without URL references",
                    )
                )
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
            class_names = frozenset(attributes.get("class", "").split())
            parent_is_assistant_message = bool(
                self.assistant_message_stack
                and self.assistant_message_stack[-1]
            )
            is_assistant_message = parent_is_assistant_message or bool(
                class_names
                & {
                    "kaigo-widget__message--assistant",
                    "kaigo-message--assistant",
                }
            )
            self.stack.append(tag)
            self.region_stack.append(region)
            self.assistant_message_stack.append(is_assistant_message)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if self.stack and self.stack[-1] == tag.lower():
            self.stack.pop()
            self.region_stack.pop()
            self.assistant_message_stack.pop()

    def handle_data(self, data: str) -> None:
        if (
            data.strip()
            and self.assistant_message_stack
            and self.assistant_message_stack[-1]
        ):
            self.assistant_message_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in VOID_ELEMENTS:
            return
        if not self.stack or self.stack[-1] != tag:
            self._add(_issue("malformed_html", "body_html", "HTML tags are not balanced"))
            if tag in self.stack:
                keep = self.stack.index(tag)
                self.stack = self.stack[:keep]
                self.region_stack = self.region_stack[:keep]
                self.assistant_message_stack = self.assistant_message_stack[:keep]
            return
        self.stack.pop()
        self.region_stack.pop()
        self.assistant_message_stack.pop()

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


def _validate_css(css: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    def add(code: str, message: str) -> None:
        issue = _issue(code, "css", message)
        if issue not in issues:
            issues.append(issue)

    if len(css.encode("utf-8")) > MAX_CSS_BYTES:
        add("css_too_large", "Stylesheet exceeds the size limit")
    normalized, comment_malformed = strip_css_comments(css)
    lower = normalized.lower()
    if comment_malformed:
        add("malformed_css", "CSS comments are not balanced")
    for declaration_block in re.findall(r"\{([^{}]*)\}", lower, flags=re.DOTALL):
        all_unset = list(
            re.finditer(
                r"(?:^|;)\s*all\s*:\s*unset(?:\s*!important)?\s*(?:;|$)",
                declaration_block,
            )
        )
        if not all_unset:
            continue
        declarations_after_reset = declaration_block[all_unset[-1].end() :]
        if not re.search(
            r"(?:^|;)\s*box-sizing\s*:\s*border-box(?:\s*!important)?\s*(?:;|$)",
            declarations_after_reset,
        ):
            add(
                "box_sizing_reset",
                "Every rule using all: unset must redeclare box-sizing: border-box "
                "after all in the same rule so padded controls cannot overflow",
            )
            break
    if re.search(r"</\s*style\b", normalized, re.IGNORECASE):
        add("unsafe_style_terminator", "CSS must not contain an HTML style terminator")
    if re.search(r"@import\b", lower):
        add("unsafe_css_at_rule", "CSS @import is not allowed")
    if re.search(r"url\s*\(", lower):
        add("external_css_resource", "CSS url() resources are not allowed")
    if contains_unquoted_css_escape(normalized):
        add("unsafe_css_value", "CSS identifier escapes are not allowed")
    if re.search(r"expression\s*\(|javascript\s*:", lower) or re.search(
        r"(?:^|[;{])\s*behavior\s*:", lower
    ):
        add("unsafe_css_value", "Unsafe CSS value is not allowed")

    selectors, at_rules, malformed = parse_css_rules(normalized)
    if malformed:
        add("malformed_css", "CSS blocks are not balanced")
    for at_rule in at_rules:
        if at_rule not in {"@media", "@supports", "@container", "@keyframes", "@-webkit-keyframes"}:
            add("unsafe_css_at_rule", f"CSS at-rule {at_rule} is not allowed")
    for selector in selectors:
        if not selector_is_scoped(selector) or re.search(
            r"(^|[\s>+~])(html|body|:root)([\s>+~.#:]|$)", selector, re.I
        ):
            excerpt = re.sub(r"\s+", " ", selector).strip()[:240]
            add(
                "unscoped_css",
                "Every selector must be scoped under .kaigo-widget; "
                f"offending selector: {excerpt}",
            )
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
    if len(artifact.javascript.encode("utf-8")) > MAX_JAVASCRIPT_BYTES:
        issues.append(
            _issue(
                "javascript_too_large",
                "javascript",
                "JavaScript exceeds the size limit",
            )
        )
    if len(artifact.change_summary.encode("utf-8")) > 2 * 1024:
        issues.append(
            _issue(
                "change_summary_too_large",
                "change_summary",
                "Change summary exceeds the size limit",
            )
        )
    if len(artifact.layout_contract) > 64 or any(
        len(key.encode("utf-8")) > 160 or len(value.encode("utf-8")) > 512
        for key, value in artifact.layout_contract.items()
    ):
        issues.append(
            _issue(
                "layout_contract_too_large",
                "layout_contract",
                "Layout contract exceeds the size limit",
            )
        )

    parser = _ArtifactHTMLParser()
    try:
        parser.feed(artifact.body_html)
        parser.close()
    except Exception:
        parser.issues.append(_issue("malformed_html", "body_html", "HTML could not be parsed"))
    issues.extend(parser.issues)
    assistant_message_text = " ".join(parser.assistant_message_text)
    if _PUBLIC_AI_IDENTITY.search(assistant_message_text):
        issues.append(
            _issue(
                "public_ai_identity",
                "body_html",
                "Static assistant greetings and labels must use the selected persona "
                "without public AI or ИИ wording",
            )
        )
    if parser.node_count > MAX_DOM_NODES:
        issues.append(_issue("too_many_nodes", "body_html", "HTML contains too many elements"))
    for region in sorted(REQUIRED_REGIONS - parser.regions):
        issues.append(_issue("missing_region", "body_html", f"Required region {region} is missing"))
    if "root" in parser.regions and not parser.root_has_widget_class:
        issues.append(
            _issue(
                "missing_widget_root_class",
                "body_html",
                'The data-region="root" element must include class="kaigo-widget" '
                "so every scoped CSS selector can match the rendered widget",
            )
        )
    expected_region_parents = {
        "launcher": "root",
        "panel": "root",
        "header": "panel",
        "messages": "panel",
        "suggestions": "panel",
        "composer": "panel",
    }
    invalid_region_parents = [
        f"{region}->{parser.region_parents.get(region) or 'none'}"
        for region, expected_parent in expected_region_parents.items()
        if region in parser.regions
        and parser.region_parents.get(region) != expected_parent
    ]
    if invalid_region_parents:
        issues.append(
            _issue(
                "invalid_region_structure",
                "body_html",
                "Header, messages, suggestions and composer must be peer panel regions; "
                "launcher and panel must be root regions. Invalid: "
                + ", ".join(invalid_region_parents),
            )
        )
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
