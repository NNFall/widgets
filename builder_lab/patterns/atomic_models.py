"""Immutable domain values for the schema v3 atomic pattern catalog.

The original pattern models in :mod:`builder_lab.patterns.models` describe the
legacy five-slot composition contract.  These values intentionally live in a
separate module so a v3 atomic catalog can evolve without changing that API.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, TypeAlias


JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JSONScalar | tuple["JSONValue", ...] | Mapping[str, "JSONValue"]

_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class AtomicPatternCategory(str, Enum):
    LAUNCHER_SHAPE = "launcher_shape"
    LAUNCHER_IDLE = "launcher_idle"
    LAUNCHER_ATTENTION = "launcher_attention"
    SHELL_LAYOUT = "shell_layout"
    WIDGET_OPEN = "widget_open"
    WIDGET_CLOSE = "widget_close"
    BACKGROUND_EFFECT = "background_effect"
    ASSISTANT_MESSAGE_ENTER = "assistant_message_enter"
    USER_MESSAGE_ENTER = "user_message_enter"
    TYPING_INDICATOR = "typing_indicator"
    MESSAGE_SEND = "message_send"
    COMPOSER_FOCUS = "composer_focus"
    CONTROL_HOVER = "control_hover"
    RESPONSIVE_TRANSITION = "responsive_transition"


class AtomicPatternStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class AdaptationPolicy(str, Enum):
    STRICT = "strict"
    ADAPTIVE = "adaptive"
    CREATIVE = "creative"


def _freeze_json(value: object, *, depth: int = 0) -> JSONValue:
    """Return bounded, JSON-shaped immutable metadata.

    Provenance is selector-facing metadata.  Keeping it JSON-only and frozen
    prevents callers from mutating a loaded definition after its hash and
    selector snapshot have been validated.
    """

    if depth > 4:
        raise ValueError("provenance is nested too deeply")
    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, str) and ("\x00" in value or len(value) > 2_000):
            raise ValueError("provenance contains invalid text")
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("provenance contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        if len(value) > 32:
            raise ValueError("provenance contains too many fields")
        frozen: dict[str, JSONValue] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 80 or "\x00" in key:
                raise ValueError("provenance contains an invalid field name")
            frozen[key] = _freeze_json(item, depth=depth + 1)
        return MappingProxyType(frozen)
    if isinstance(value, (tuple, list)):
        if len(value) > 32:
            raise ValueError("provenance contains too many items")
        return tuple(_freeze_json(item, depth=depth + 1) for item in value)
    raise ValueError("provenance must be plain JSON")


def _thaw_json(value: JSONValue) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _text(value: object, *, name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum or "\x00" in normalized:
        raise ValueError(f"{name} is invalid")
    return normalized


def _identifier(value: object, *, name: str) -> str:
    normalized = _text(value, name=name, maximum=80)
    if not _IDENTIFIER_RE.fullmatch(normalized):
        raise ValueError(f"{name} is invalid")
    return normalized


@dataclass(frozen=True, slots=True)
class AtomicPatternDefinition:
    """A validated immutable schema v3 pattern and its exact implementation."""

    schema_version: int
    pattern_id: str
    version: int
    category: AtomicPatternCategory
    status: AtomicPatternStatus
    title: str
    summary: str
    ai_description: str
    technical_contract: str
    adaptation_policy: AdaptationPolicy
    incompatible_with: tuple[str, ...]
    implementation_sha256: str
    provenance: Mapping[str, JSONValue]
    html: str
    css: str
    javascript: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != 3:
            raise ValueError("schema_version must be 3")
        pattern_id = _identifier(self.pattern_id, name="pattern_id")
        if (
            isinstance(self.version, bool)
            or not isinstance(self.version, int)
            or not 1 <= self.version <= 9_999
        ):
            raise ValueError("version is invalid")
        if not isinstance(self.category, AtomicPatternCategory):
            raise ValueError("category is invalid")
        if not isinstance(self.status, AtomicPatternStatus):
            raise ValueError("status is invalid")
        if not isinstance(self.adaptation_policy, AdaptationPolicy):
            raise ValueError("adaptation_policy is invalid")

        title = _text(self.title, name="title", maximum=200)
        summary = _text(self.summary, name="summary", maximum=500)
        ai_description = _text(self.ai_description, name="ai_description", maximum=4_000)
        if not 80 <= len(ai_description) <= 4_000:
            raise ValueError("ai_description must be between 80 and 4000 characters")
        # A selector description must be prose-like, not a code blob or a
        # whitespace-free token.  This deliberately stays a small structural
        # check; semantic quality belongs to review, not the loader.
        if not any(char.isalpha() for char in ai_description) or not any(
            char.isspace() for char in ai_description
        ):
            raise ValueError("ai_description must be natural-language text")
        technical_contract = _text(
            self.technical_contract,
            name="technical_contract",
            maximum=300,
        )
        if not isinstance(self.incompatible_with, (tuple, list)):
            raise ValueError("incompatible_with must be a list")
        if len(self.incompatible_with) > 32:
            raise ValueError("incompatible_with is too large")
        incompatible = tuple(
            _identifier(item, name="incompatible pattern")
            for item in self.incompatible_with
        )
        if len(set(incompatible)) != len(incompatible):
            raise ValueError("incompatible_with contains duplicates")

        if not isinstance(self.implementation_sha256, str) or not _SHA256_RE.fullmatch(
            self.implementation_sha256
        ):
            raise ValueError("implementation_sha256 is invalid")
        for name, asset in (
            ("html", self.html),
            ("css", self.css),
            ("javascript", self.javascript),
        ):
            if not isinstance(asset, str) or "\x00" in asset:
                raise ValueError(f"{name} is invalid")
        if not isinstance(self.provenance, Mapping):
            raise ValueError("provenance must be an object")
        if "origin" not in self.provenance or "review_state" not in self.provenance:
            raise ValueError("provenance requires origin and review_state")
        origin = self.provenance.get("origin")
        if not isinstance(origin, str) or not origin.strip() or "\x00" in origin:
            raise ValueError("provenance origin is invalid")
        review_state = self.provenance.get("review_state")
        if review_state not in {"ready_for_review", "approved", "rejected"}:
            raise ValueError("provenance review_state is invalid")
        try:
            frozen_provenance = _freeze_json(self.provenance)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        if not isinstance(frozen_provenance, Mapping):  # pragma: no cover - guarded
            raise ValueError("provenance must be an object")

        object.__setattr__(self, "pattern_id", pattern_id)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "summary", summary)
        object.__setattr__(self, "ai_description", ai_description)
        object.__setattr__(self, "technical_contract", technical_contract)
        object.__setattr__(self, "incompatible_with", incompatible)
        object.__setattr__(self, "provenance", frozen_provenance)

    def selector_dict(self) -> dict[str, JSONValue]:
        """Return the metadata safe to expose to an AI selector.

        Implementation assets are intentionally absent.  The complete natural
        language description is preserved verbatim (apart from validated outer
        whitespace), along with its contract, policy, incompatibilities,
        provenance, and immutable hash.
        """

        return {
            "schema_version": self.schema_version,
            "pattern_id": self.pattern_id,
            "version": self.version,
            "category": self.category.value,
            "status": self.status.value,
            "title": self.title,
            "summary": self.summary,
            "ai_description": self.ai_description,
            "technical_contract": self.technical_contract,
            "adaptation_policy": self.adaptation_policy.value,
            "incompatible_with": list(self.incompatible_with),
            "implementation_sha256": self.implementation_sha256,
            "provenance": _thaw_json(self.provenance),
        }

    def implementation_dict(self) -> dict[str, str]:
        """Return only the exact validated implementation asset strings."""

        return {
            "html": self.html,
            "css": self.css,
            "javascript": self.javascript,
        }


__all__ = [
    "AdaptationPolicy",
    "AtomicPatternCategory",
    "AtomicPatternDefinition",
    "AtomicPatternStatus",
    "JSONValue",
]
