"""Deterministic quality gate for the schema-v3 atomic pattern catalog.

The selector sees only assets that have an explicit, owner-controlled role and
an executable motion profile.  The measurements here are intentionally small
and conservative; they are safeguards against empty/padded fixtures, not a
claim that static CSS parsing can judge visual taste.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Collection, Mapping

from .atomic_models import (
    AtomicPatternCategory,
    AtomicPatternDefinition,
    normalize_effective_approved,
)


class AtomicPatternRole(str, Enum):
    """How a definition is used by the selector."""

    FIXTURE = "fixture"
    STRUCTURAL = "structural"
    SUPPORT = "support"
    SIGNATURE = "signature"


@dataclass(frozen=True, slots=True)
class AtomicQualityProfile:
    """Bounded, serializable measurements used by selection and reporting."""

    role: AtomicPatternRole
    selector_eligible: bool
    quality_score: int
    keyframe_count: int
    keyframe_phase_count: int
    css_bytes: int
    reduced_motion_present: bool
    animation_rule_count: int
    animated_target_count: int
    duration_band_ok: bool
    rejected: bool

    def __post_init__(self) -> None:
        if not isinstance(self.role, AtomicPatternRole):
            raise ValueError("role is invalid")
        if not 0 <= self.quality_score <= 100:
            raise ValueError("quality_score is invalid")
        for name in (
            "keyframe_count",
            "keyframe_phase_count",
            "css_bytes",
            "animation_rule_count",
            "animated_target_count",
        ):
            if getattr(self, name) < 0:
                raise ValueError("quality metrics are invalid")
        if not isinstance(self.duration_band_ok, bool):
            raise ValueError("duration_band_ok is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "role": self.role.value,
            "selector_eligible": self.selector_eligible,
            "quality_score": self.quality_score,
            "keyframe_count": self.keyframe_count,
            "keyframe_phase_count": self.keyframe_phase_count,
            "css_bytes": self.css_bytes,
            "reduced_motion_present": self.reduced_motion_present,
            "animation_rule_count": self.animation_rule_count,
            "animated_target_count": self.animated_target_count,
            "duration_band_ok": self.duration_band_ok,
            "rejected": self.rejected,
        }


MIN_SELECTOR_QUALITY_SCORE = 60
MIN_STRUCTURAL_QUALITY_SCORE = 50
# A compact but real motion rule can be well below a kilobyte.  The executable
# animation/keyframe metrics below are the stronger signal; this floor only
# rejects empty shells and comments/padding.
MIN_SELECTOR_CSS_BYTES = 256
MIN_SELECTOR_HTML_BYTES = 128
MIN_SIGNATURE_TARGETS = 2
MIN_SIGNATURE_PHASES = 2
MIN_DURATION_MS = 250.0
MAX_DURATION_MS = 12_000.0

_SIGNATURE_CATEGORIES = frozenset(
    {
        AtomicPatternCategory.WIDGET_OPEN,
        AtomicPatternCategory.WIDGET_CLOSE,
        AtomicPatternCategory.LAUNCHER_ATTENTION,
        AtomicPatternCategory.MESSAGE_SEND,
        AtomicPatternCategory.ASSISTANT_MESSAGE_ENTER,
        AtomicPatternCategory.USER_MESSAGE_ENTER,
    }
)
_STRUCTURAL_CATEGORIES = frozenset(
    {AtomicPatternCategory.SHELL_LAYOUT, AtomicPatternCategory.RESPONSIVE_TRANSITION}
)
_ROLE_VALUES = frozenset(item.value for item in AtomicPatternRole)

_CSS_COMMENT_RE = re.compile(r"/\*.*?(?:\*/|$)", re.DOTALL)
_HTML_COMMENT_RE = re.compile(r"<!--.*?(?:-->|$)", re.DOTALL)
_KEYFRAME_AT_RE = re.compile(
    r"@(?:-\w+-)?keyframes\s+(?P<name>[\w-]+)\s*\{", re.IGNORECASE
)
_REDUCED_MEDIA_RE = re.compile(
    r"@media\s*\([^)]*prefers-reduced-motion\s*:\s*reduce[^)]*\)\s*\{",
    re.IGNORECASE,
)
_KEYFRAME_PHASE_RE = re.compile(
    r"((?:from|to|\d+(?:\.\d+)?%)(?:\s*,\s*(?:from|to|\d+(?:\.\d+)?%))*)\s*\{",
    re.IGNORECASE,
)
_ANIMATION_DECLARATION_RE = re.compile(
    r"(?<![\w-])(?P<property>animation(?:-name|-duration)?)\s*:\s*(?P<value>[^;}]+)",
    re.IGNORECASE,
)
_DURATION_RE = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?|\.\d+)(ms|s)\b",
    re.IGNORECASE,
)


def _category_role(definition: AtomicPatternDefinition) -> AtomicPatternRole:
    """Infer a diagnostic role when old manifests omit ``pattern_role``."""

    if definition.pattern_id.endswith("-technical"):
        return AtomicPatternRole.FIXTURE
    if definition.category in _SIGNATURE_CATEGORIES:
        return AtomicPatternRole.SIGNATURE
    if definition.category in _STRUCTURAL_CATEGORIES:
        return AtomicPatternRole.STRUCTURAL
    return AtomicPatternRole.SUPPORT


def _explicit_role(definition: AtomicPatternDefinition) -> tuple[AtomicPatternRole, bool]:
    value = definition.provenance.get("pattern_role")
    if definition.pattern_id.endswith("-technical"):
        # Legacy technical IDs remain fixtures even if a compromised manifest
        # tries to relabel them as a selectable visual role.
        return AtomicPatternRole.FIXTURE, value == AtomicPatternRole.FIXTURE.value
    if isinstance(value, str) and value in _ROLE_VALUES:
        return AtomicPatternRole(value), True
    # The inferred value is useful for diagnostics, but a missing/invalid
    # explicit role is never selector-eligible (fail closed).
    return _category_role(definition), False


def _strip_css_comments(value: str) -> str:
    return _CSS_COMMENT_RE.sub("", value)


def _strip_html_comments(value: str) -> str:
    return _HTML_COMMENT_RE.sub("", value)


def _compact(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _extract_braced(text: str, opening: int) -> tuple[str, int]:
    """Return a balanced block body and its exclusive end index."""

    depth = 0
    for index in range(opening, len(text)):
        character = text[index]
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return text[opening + 1 : index], index + 1
    return text[opening + 1 :], len(text)


def _without_reduced_motion(css: str) -> str:
    """Remove reduced-motion blocks before measuring normal motion durations."""

    chunks: list[str] = []
    cursor = 0
    while True:
        match = _REDUCED_MEDIA_RE.search(css, cursor)
        if match is None:
            chunks.append(css[cursor:])
            break
        chunks.append(css[cursor : match.start()])
        _, end = _extract_braced(css, match.end() - 1)
        cursor = end
    return "".join(chunks)


def _keyframe_details(css: str) -> dict[str, tuple[int, str]]:
    """Return keyframe phase counts and compact executable blocks by name."""

    details: dict[str, tuple[int, str]] = {}
    cursor = 0
    while True:
        match = _KEYFRAME_AT_RE.search(css, cursor)
        if match is None:
            break
        body, end = _extract_braced(css, match.end() - 1)
        phases = sum(
            len([part for part in item.group(1).split(",") if part.strip()])
            for item in _KEYFRAME_PHASE_RE.finditer(body)
        )
        # Keep the full block only for referenced names.  Unreferenced keyframes
        # must not inflate the byte/quality signal.
        details[match.group("name")] = (
            phases,
            _compact(css[match.start() : end]),
        )
        cursor = end
    return details


def _keyframe_metrics(css: str) -> tuple[int, int]:
    """Compatibility helper for callers that need all declared keyframes."""

    details = _keyframe_details(css)
    return len(details), sum(item[0] for item in details.values())


def _duration_values(value: str, *, shorthand: bool = False) -> list[float]:
    matches = [
        float(number) * (1_000 if unit.lower() == "s" else 1)
        for number, unit in _DURATION_RE.findall(value)
    ]
    # In a shorthand declaration the first time token is the animation
    # duration; a second token is the delay and should not make a tiny/fake
    # animation appear to be substantial.
    return matches[:1] if shorthand else matches


def _known_animation_names(value: str, keyframe_details: Mapping[str, object]) -> set[str]:
    return {
        name
        for name in keyframe_details
        if re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", value)
    }


def _animation_metrics(
    css: str,
    keyframe_details: Mapping[str, tuple[int, str]],
) -> tuple[int, int, tuple[float, ...], frozenset[str], int]:
    """Count only executable animation declarations with known keyframes.

    ``animation-name`` values are never scanned for duration tokens; names such
    as ``fake-1000ms`` therefore cannot manufacture timing quality.  CSS bytes
    cover only animated selectors and the keyframe blocks they reference.
    """

    rule_count = 0
    target_count = 0
    durations: list[float] = []
    referenced_names: set[str] = set()
    relevant_chunks: list[str] = []
    # Aggregate repeated selector rules so animation-name and animation-duration
    # declarations can be correlated without counting unrelated CSS padding.
    rule_re = re.compile(r"([^{}]+)\{([^{}]*)\}", re.DOTALL)
    selector_bodies: dict[str, list[str]] = {}
    for match in rule_re.finditer(css):
        selector = _compact(match.group(1))
        body = match.group(2)
        if selector.startswith("@") or "keyframes" in selector.lower():
            continue
        if selector:
            selector_bodies.setdefault(selector, []).append(body)

    for selector, bodies in selector_bodies.items():
        body = " ".join(bodies)
        names: set[str] = set()
        shorthand_values: list[str] = []
        duration_values: list[str] = []
        for declaration in _ANIMATION_DECLARATION_RE.finditer(body):
            property_name = declaration.group("property").lower()
            value = declaration.group("value").strip()
            if property_name == "animation-name":
                for item in value.split(","):
                    names.update(_known_animation_names(item.strip(), keyframe_details))
            elif property_name == "animation":
                for item in value.split(","):
                    shorthand_values.append(item.strip())
                    names.update(_known_animation_names(item, keyframe_details))
            else:
                duration_values.extend(value.split(","))
        if not names:
            continue

        for value in shorthand_values:
            durations.extend(_duration_values(value, shorthand=True))
        for value in duration_values:
            durations.extend(_duration_values(value))

        rule_count += 1
        target_count += sum(bool(item.strip()) for item in selector.split(","))
        relevant_chunks.append(f"{selector}{{{_compact(body)}}}")
        referenced_names.update(names)

    relevant_chunks.extend(
        keyframe_details[name][1]
        for name in sorted(referenced_names)
    )
    relevant_css_bytes = len(_compact(" ".join(relevant_chunks)).encode("utf-8"))
    return (
        rule_count,
        target_count,
        tuple(durations),
        frozenset(referenced_names),
        relevant_css_bytes,
    )


def effective_review_state_for(
    definition: AtomicPatternDefinition,
    effective_approved: Mapping[tuple[str, int], object]
    | Collection[tuple[str, int]]
    | None,
) -> str:
    """Return the persistence review state for an exact definition."""

    if effective_approved is None:
        return str(definition.provenance.get("review_state", "rejected"))
    approved = normalize_effective_approved(effective_approved) or frozenset()
    return (
        "approved"
        if (definition.pattern_id, definition.version) in approved
        else "rejected"
    )


def compute_atomic_quality_profile(
    definition: AtomicPatternDefinition,
    *,
    effective_review_state: str | None = None,
) -> AtomicQualityProfile:
    """Compute a stable selector gate from exact implementation assets."""

    if not isinstance(definition, AtomicPatternDefinition):
        raise TypeError("definition must be an AtomicPatternDefinition")

    role, explicit_role = _explicit_role(definition)
    executable_css = _compact(_strip_css_comments(definition.css))
    executable_html = _compact(_strip_html_comments(definition.html))
    normal_motion_css = _without_reduced_motion(executable_css)
    html_bytes = len(executable_html.encode("utf-8"))
    reduced_motion_present = bool(_REDUCED_MEDIA_RE.search(executable_css))
    keyframe_details = _keyframe_details(normal_motion_css)
    (
        animation_rule_count,
        animated_target_count,
        durations,
        referenced_keyframes,
        css_bytes,
    ) = _animation_metrics(
        normal_motion_css,
        keyframe_details,
    )
    keyframe_count = len(referenced_keyframes)
    keyframe_phase_count = sum(
        keyframe_details[name][0] for name in referenced_keyframes
    )
    duration_band_ok = bool(
        durations
        and any(MIN_DURATION_MS <= duration <= MAX_DURATION_MS for duration in durations)
    )
    review_state = effective_review_state
    if review_state is None:
        review_state = str(definition.provenance.get("review_state", "rejected"))
    manifest_rejected = review_state == "rejected"

    metrics = dict(
        keyframe_count=keyframe_count,
        keyframe_phase_count=keyframe_phase_count,
        css_bytes=css_bytes,
        reduced_motion_present=reduced_motion_present,
        animation_rule_count=animation_rule_count,
        animated_target_count=animated_target_count,
        duration_band_ok=duration_band_ok,
    )
    if role is AtomicPatternRole.FIXTURE:
        return AtomicQualityProfile(
            role=role,
            selector_eligible=False,
            quality_score=0,
            rejected=False,
            **metrics,
        )

    has_real_implementation = (
        html_bytes >= MIN_SELECTOR_HTML_BYTES
        and css_bytes >= MIN_SELECTOR_CSS_BYTES
        and keyframe_count >= 1
        and reduced_motion_present
    )
    signature_motion_ok = (
        role is not AtomicPatternRole.SIGNATURE
        or (
            animated_target_count >= MIN_SIGNATURE_TARGETS
            and keyframe_phase_count >= MIN_SIGNATURE_PHASES
            and duration_band_ok
        )
    )
    structural_motion_ok = (
        role is not AtomicPatternRole.STRUCTURAL
        or (animated_target_count >= 1 and keyframe_phase_count >= 2 and duration_band_ok)
    )
    rejected = bool(manifest_rejected)
    role_ok = explicit_role
    if not has_real_implementation or rejected or not role_ok or not signature_motion_ok or not structural_motion_ok:
        score = 0
        eligible = False
    else:
        # Score substance, executable motion variety, duration and safety; raw
        # comments/whitespace cannot inflate any term.
        score = min(25, css_bytes // 160)
        score += min(20, keyframe_count * 4)
        score += min(15, animated_target_count * 3)
        score += min(15, keyframe_phase_count * 3)
        score += min(10, animation_rule_count * 3)
        score += 10 if duration_band_ok else 0
        score += 10 if reduced_motion_present else 0
        score += 5 if html_bytes >= MIN_SELECTOR_HTML_BYTES else 0
        if (
            animation_rule_count >= 1
            and animated_target_count >= 1
            and keyframe_phase_count >= 2
            and duration_band_ok
        ):
            score += 5
        score = max(0, min(100, score))
        threshold = (
            MIN_STRUCTURAL_QUALITY_SCORE
            if role is AtomicPatternRole.STRUCTURAL
            else MIN_SELECTOR_QUALITY_SCORE
        )
        eligible = score >= threshold

    return AtomicQualityProfile(
        role=role,
        selector_eligible=eligible,
        quality_score=score,
        rejected=rejected,
        **metrics,
    )


quality_profile_for = compute_atomic_quality_profile
build_atomic_quality_profile = compute_atomic_quality_profile


__all__ = [
    "AtomicPatternRole",
    "AtomicQualityProfile",
    "MIN_SELECTOR_CSS_BYTES",
    "MIN_SELECTOR_HTML_BYTES",
    "MIN_SELECTOR_QUALITY_SCORE",
    "MIN_STRUCTURAL_QUALITY_SCORE",
    "build_atomic_quality_profile",
    "compute_atomic_quality_profile",
    "effective_review_state_for",
    "quality_profile_for",
]
