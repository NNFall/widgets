from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True, slots=True)
class CssParseResult:
    selectors: tuple[str, ...]
    at_rules: tuple[str, ...]
    at_rule_preludes: tuple[str, ...]
    malformed: bool
    unsupported_nesting: bool


_SVG_HEX_COLOR_RE = re.compile(
    r"^#[0-9a-f]{3}(?:[0-9a-f]{1}|[0-9a-f]{3}|[0-9a-f]{5})?$"
)
_SVG_NAMED_COLOR_RE = re.compile(r"^[a-z]+$")
_SVG_COLOR_FUNCTION_RE = re.compile(
    r"^(?:rgb|rgba|hsl|hsla)\([0-9+.,%/\s\-degartun]*\)$"
)


def contains_unquoted_css_escape(css: str) -> bool:
    """Return whether executable CSS syntax contains an escape sequence.

    Runtime-source CSS has no need for escaped identifiers. Quoted strings may
    still use escapes for presentational text such as ``content``.
    """

    quote: str | None = None
    escaped = False
    for character in css:
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {"'", '"'}:
            quote = character
        elif character == "\\":
            return True
    return False


def is_safe_svg_paint(value: str) -> bool:
    """Accept inert SVG colors while rejecting every paint-server reference."""

    candidate = value.strip().lower()
    if not candidate or len(candidate) > 128 or "\\" in candidate:
        return False
    if candidate in {
        "currentcolor",
        "inherit",
        "initial",
        "none",
        "transparent",
        "unset",
    }:
        return True
    return bool(
        _SVG_HEX_COLOR_RE.fullmatch(candidate)
        or _SVG_NAMED_COLOR_RE.fullmatch(candidate)
        or _SVG_COLOR_FUNCTION_RE.fullmatch(candidate)
    )


def strip_css_comments(css: str) -> tuple[str, bool]:
    """Remove CSS comments without letting braces inside comments affect parsing."""

    output: list[str] = []
    cursor = 0
    malformed = False
    quote: str | None = None
    escaped = False
    while cursor < len(css):
        character = css[cursor]
        if quote is not None:
            output.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            cursor += 1
            continue
        if character in {"'", '"'}:
            quote = character
            output.append(character)
            cursor += 1
            continue
        if character == "\\":
            output.append(character)
            if cursor + 1 < len(css):
                output.append(css[cursor + 1])
                cursor += 2
            else:
                cursor += 1
            continue
        if css.startswith("/*", cursor):
            closing = css.find("*/", cursor + 2)
            if closing < 0:
                malformed = True
                break
            output.append(" ")
            cursor = closing + 2
            continue
        output.append(character)
        cursor += 1
    return "".join(output), malformed


def matching_brace(css: str, opening: int) -> int | None:
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


def _next_opening_brace(css: str, start: int) -> int | None:
    """Find a block opener without treating quoted selector text as syntax."""

    quote: str | None = None
    escaped = False
    for index in range(start, len(css)):
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
            return index
        elif character == "}":
            return None
    return None


def _selector_branches(prelude: str) -> tuple[str, ...]:
    branches: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    escaped = False
    for index, character in enumerate(prelude):
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
        elif character in "([":
            depth += 1
        elif character in ")]" and depth:
            depth -= 1
        elif character == "," and depth == 0:
            branch = prelude[start:index].strip()
            if branch:
                branches.append(branch)
            start = index + 1
    branch = prelude[start:].strip()
    if branch:
        branches.append(branch)
    return tuple(branches)


def parse_css_contract(css: str) -> CssParseResult:
    normalized, comment_malformed = strip_css_comments(css)
    selectors: list[str] = []
    at_rules: list[str] = []
    at_rule_preludes: list[str] = []
    malformed = comment_malformed
    unsupported_nesting = False

    def walk(segment: str) -> None:
        nonlocal malformed, unsupported_nesting
        cursor = 0
        while cursor < len(segment):
            while cursor < len(segment) and (
                segment[cursor].isspace() or segment[cursor] == ";"
            ):
                cursor += 1
            if cursor >= len(segment):
                return
            opening = _next_opening_brace(segment, cursor)
            if opening is None:
                if segment[cursor:].strip():
                    malformed = True
                return
            prelude = segment[cursor:opening].strip()
            if not prelude:
                malformed = True
                return
            closing = matching_brace(segment, opening)
            if closing is None:
                malformed = True
                return
            content = segment[opening + 1 : closing]
            lower = prelude.lower()
            if lower.startswith("@"):
                name_match = re.match(r"^(@[-a-z]+)\b", lower)
                if name_match is None:
                    malformed = True
                else:
                    name = name_match.group(1)
                    at_rules.append(name)
                    at_rule_preludes.append(prelude)
                    if name in {"@media", "@supports", "@container"}:
                        walk(content)
            else:
                selectors.extend(_selector_branches(prelude))
                if _next_opening_brace(content, 0) is not None:
                    unsupported_nesting = True
            cursor = closing + 1

    walk(normalized)
    return CssParseResult(
        selectors=tuple(selectors),
        at_rules=tuple(at_rules),
        at_rule_preludes=tuple(at_rule_preludes),
        malformed=malformed,
        unsupported_nesting=unsupported_nesting,
    )


def parse_css_rules(css: str) -> tuple[list[str], list[str], bool]:
    parsed = parse_css_contract(css)
    return (
        list(parsed.selectors),
        list(parsed.at_rules),
        parsed.malformed or parsed.unsupported_nesting,
    )


def selector_is_scoped(selector: str) -> bool:
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
            return bool(remainder) and selector_is_scoped(remainder)
        elif depth == 0 and character == ">":
            return True
        elif depth == 0 and character.isspace():
            remainder = candidate[cursor:].lstrip()
            if not remainder:
                return True
            if remainder[0] in "+~":
                sibling = remainder[1:].lstrip()
                return bool(sibling) and selector_is_scoped(sibling)
            return True
        cursor += 1
    return True


__all__ = [
    "CssParseResult",
    "contains_unquoted_css_escape",
    "is_safe_svg_paint",
    "matching_brace",
    "parse_css_contract",
    "parse_css_rules",
    "selector_is_scoped",
    "strip_css_comments",
]
