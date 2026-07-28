from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, TypeAlias


JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JSONScalar | tuple["JSONValue", ...] | Mapping[str, "JSONValue"]

_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_DIRECTION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_CODE_PARAMETER_NAMES = frozenset(
    {
        "code",
        "css",
        "html",
        "javascript",
        "js",
        "markup",
        "script",
        "srcdoc",
    }
)
_MAX_PARAMETERS_BYTES = 4_096
_MAX_PARAMETER_ITEMS = 32
_MAX_PARAMETER_DEPTH = 2


class PatternCategory(str, Enum):
    LAUNCHER = "launcher"
    SHELL = "shell"
    MESSAGES = "messages"
    COMPOSER = "composer"
    MOTION = "motion"


def _required_text(value: object, *, name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum or "\x00" in normalized:
        raise ValueError(f"{name} is invalid")
    return normalized


def _freeze_json(value: object, *, depth: int = 0) -> JSONValue:
    if depth > _MAX_PARAMETER_DEPTH:
        raise ValueError("parameters are nested too deeply")
    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, str) and (len(value) > 512 or "\x00" in value):
            raise ValueError("parameters contain an invalid string")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("parameters must contain finite numbers")
        return value
    if isinstance(value, Mapping):
        if len(value) > _MAX_PARAMETER_ITEMS:
            raise ValueError("parameters contain too many fields")
        frozen: dict[str, JSONValue] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 80:
                raise ValueError("parameters contain an invalid field name")
            normalized_key = key.strip().lower()
            if (
                normalized_key in _CODE_PARAMETER_NAMES
                or normalized_key.startswith("on")
            ):
                raise ValueError("parameters must not contain executable code")
            frozen[key] = _freeze_json(item, depth=depth + 1)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        if len(value) > _MAX_PARAMETER_ITEMS:
            raise ValueError("parameters contain too many items")
        return tuple(_freeze_json(item, depth=depth + 1) for item in value)
    raise ValueError("parameters must be plain JSON")


def _thaw_json(value: JSONValue) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in sorted(value.items())}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _bounded_parameters(value: object) -> Mapping[str, JSONValue]:
    if not isinstance(value, Mapping):
        raise ValueError("parameters must be a JSON object")
    frozen = _freeze_json(value)
    if not isinstance(frozen, Mapping):  # pragma: no cover - guarded above
        raise ValueError("parameters must be a JSON object")
    encoded = json.dumps(
        _thaw_json(frozen),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > _MAX_PARAMETERS_BYTES:
        raise ValueError("parameters are too large")
    return frozen


@dataclass(frozen=True, slots=True)
class PatternSelection:
    slot: PatternCategory
    pattern_id: str
    version: int
    parameters: Mapping[str, JSONValue]
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.slot, PatternCategory):
            raise ValueError("slot must be a PatternCategory")
        pattern_id = _required_text(self.pattern_id, name="pattern_id", maximum=80)
        if not _IDENTIFIER_RE.fullmatch(pattern_id):
            raise ValueError("pattern_id is invalid")
        if (
            isinstance(self.version, bool)
            or not isinstance(self.version, int)
            or not 1 <= self.version <= 9_999
        ):
            raise ValueError("version must be a positive integer")
        parameters = _bounded_parameters(self.parameters)
        reason = _required_text(self.reason, name="reason", maximum=500)
        object.__setattr__(self, "pattern_id", pattern_id)
        object.__setattr__(self, "parameters", parameters)
        object.__setattr__(self, "reason", reason)

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "PatternSelection":
        if not isinstance(payload, Mapping):
            raise ValueError("selection must be an object")
        try:
            slot = PatternCategory(payload.get("slot"))
        except (TypeError, ValueError) as exc:
            raise ValueError("slot is invalid") from exc
        return cls(
            slot=slot,
            pattern_id=payload.get("pattern_id", ""),
            version=payload.get("version", 0),
            parameters=payload.get("parameters", {}),
            reason=payload.get("reason", ""),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "slot": self.slot.value,
            "pattern_id": self.pattern_id,
            "version": self.version,
            "parameters": _thaw_json(self.parameters),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class CustomPatternEscape:
    slot: PatternCategory
    reason: str
    constraints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.slot, PatternCategory):
            raise ValueError("custom_escape slot must be a PatternCategory")
        reason = _required_text(
            self.reason,
            name="custom_escape reason",
            maximum=500,
        )
        if not isinstance(self.constraints, tuple) or len(self.constraints) > 12:
            raise ValueError("custom_escape constraints are invalid")
        normalized_constraints: list[str] = []
        for constraint in self.constraints:
            normalized = _required_text(
                constraint,
                name="custom_escape constraint",
                maximum=80,
            )
            if not _IDENTIFIER_RE.fullmatch(normalized):
                raise ValueError("custom_escape constraint is invalid")
            normalized_constraints.append(normalized)
        if len(set(normalized_constraints)) != len(normalized_constraints):
            raise ValueError("custom_escape constraints contain duplicates")
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "constraints", tuple(normalized_constraints))

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "CustomPatternEscape":
        if not isinstance(payload, Mapping):
            raise ValueError("custom_escape must be an object")
        try:
            slot = PatternCategory(payload.get("slot"))
        except (TypeError, ValueError) as exc:
            raise ValueError("custom_escape slot is invalid") from exc
        constraints = payload.get("constraints", ())
        if not isinstance(constraints, (list, tuple)):
            raise ValueError("custom_escape constraints are invalid")
        return cls(
            slot=slot,
            reason=payload.get("reason", ""),
            constraints=tuple(constraints),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "slot": self.slot.value,
            "reason": self.reason,
            "constraints": list(self.constraints),
        }


@dataclass(frozen=True, slots=True)
class CompositionPlan:
    schema_version: int
    direction_id: str
    selections: tuple[PatternSelection, ...]
    summary: str
    custom_escape: CustomPatternEscape | None = None

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("schema_version must be 1")
        direction_id = _required_text(
            self.direction_id,
            name="direction_id",
            maximum=80,
        )
        if not _DIRECTION_RE.fullmatch(direction_id):
            raise ValueError("direction_id is invalid")
        if not isinstance(self.selections, tuple) or not all(
            isinstance(item, PatternSelection) for item in self.selections
        ):
            raise ValueError("selections must contain PatternSelection values")
        if self.custom_escape is not None and not isinstance(
            self.custom_escape,
            CustomPatternEscape,
        ):
            raise ValueError("custom_escape is invalid")

        slots = [item.slot for item in self.selections]
        if len(set(slots)) != len(slots):
            raise ValueError("composition contains duplicate slots")
        if self.custom_escape is not None and self.custom_escape.slot in slots:
            raise ValueError("custom_escape duplicates a selected slot")
        covered = set(slots)
        if self.custom_escape is not None:
            covered.add(self.custom_escape.slot)
        required = set(PatternCategory)
        if covered != required:
            missing = ", ".join(sorted(slot.value for slot in required - covered))
            raise ValueError(f"composition is missing required slots: {missing}")

        order = {category: index for index, category in enumerate(PatternCategory)}
        canonical_selections = tuple(sorted(self.selections, key=lambda item: order[item.slot]))
        summary = _required_text(self.summary, name="summary", maximum=1_000)
        object.__setattr__(self, "direction_id", direction_id)
        object.__setattr__(self, "selections", canonical_selections)
        object.__setattr__(self, "summary", summary)

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "CompositionPlan":
        if not isinstance(payload, Mapping):
            raise ValueError("composition plan must be an object")
        selections = payload.get("selections", ())
        if not isinstance(selections, (list, tuple)):
            raise ValueError("selections must be a list")
        raw_escape = payload.get("custom_escape")
        if raw_escape is not None and not isinstance(raw_escape, Mapping):
            raise ValueError("custom_escape must be an object")
        return cls(
            schema_version=payload.get("schema_version", 0),
            direction_id=payload.get("direction_id", ""),
            selections=tuple(PatternSelection.from_dict(item) for item in selections),
            summary=payload.get("summary", ""),
            custom_escape=(
                CustomPatternEscape.from_dict(raw_escape)
                if raw_escape is not None
                else None
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "direction_id": self.direction_id,
            "selections": [item.to_dict() for item in self.selections],
            "custom_escape": (
                self.custom_escape.to_dict() if self.custom_escape else None
            ),
            "summary": self.summary,
        }
