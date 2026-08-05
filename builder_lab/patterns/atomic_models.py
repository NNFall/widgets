"""Immutable domain values for the schema v3 atomic pattern catalog.

The original pattern models in :mod:`builder_lab.patterns.models` describe the
legacy five-slot composition contract.  These values intentionally live in a
separate module so a v3 atomic catalog can evolve without changing that API.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from math import log2
from types import MappingProxyType
from typing import Mapping, TypeAlias


JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JSONScalar | tuple["JSONValue", ...] | Mapping[str, "JSONValue"]
SerializedJSONValue: TypeAlias = (
    JSONScalar | list["SerializedJSONValue"] | dict[str, "SerializedJSONValue"]
)

_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_WORD_RE = re.compile(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*", re.UNICODE)
_CODE_STRUCTURE_RE = re.compile(
    r"(?:=>|</?\w[^>]*>|[{}\[\]]|"
    r"\b(?:const|def|let|var)\s+[A-Za-z_$][\w$]*\s*(?:=|\()|"
    r"\bfunction\s*(?:[A-Za-z_$][\w$]*\s*)?\(|"
    r"\b(?:eval|import|export)\s*(?:\(|\{|\*|[\"']))",
    re.IGNORECASE,
)
_HEX_BLOB_RE = re.compile(r"^[0-9a-f]+$", re.IGNORECASE)
_BASE64_BLOB_RE = re.compile(r"^[A-Za-z0-9+/=_-]+$")
_BLOB_EDGE_PUNCTUATION = ".,:;!?()[]{}\"'`"
_MIN_SEGMENTED_HEX_CHUNK = 8
_MIN_SEGMENTED_HEX_CHUNKS = 4
_MIN_SEGMENTED_HEX_TOTAL = 64
_MIN_MULTI_TOKEN_BLOB_CHUNK = 16
_MIN_MULTI_TOKEN_BLOB_TOTAL = 96


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


def _thaw_json(value: JSONValue) -> SerializedJSONValue:
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


def _validate_natural_language(value: str) -> None:
    """Apply a bounded deterministic prose heuristic to selector descriptions."""

    if _CODE_STRUCTURE_RE.search(value):
        raise ValueError("ai_description must be natural-language text")

    words = _WORD_RE.findall(value)
    if len(words) < 8:
        raise ValueError("ai_description must contain at least eight words")
    normalized_words = [word.casefold() for word in words]
    unique_words = set(normalized_words)
    minimum_unique = max(4, (len(normalized_words) + 2) // 3)
    if len(unique_words) < minimum_unique:
        raise ValueError("ai_description contains too little lexical variety")
    dominant_count = max(Counter(normalized_words).values())
    if dominant_count > max(4, (len(normalized_words) + 1) // 2):
        raise ValueError("ai_description contains a repeated-token blob")
    for token in re.findall(r"\S+", value):
        candidate = token.strip(".,:;!?()[]{}\"'")
        if len(candidate) < 64:
            continue
        if not (_HEX_BLOB_RE.fullmatch(candidate) or _BASE64_BLOB_RE.fullmatch(candidate)):
            continue
        frequencies = Counter(candidate)
        entropy = -sum(
            (count / len(candidate)) * log2(count / len(candidate))
            for count in frequencies.values()
        )
        if _HEX_BLOB_RE.fullmatch(candidate) or entropy >= 2.5:
            raise ValueError("ai_description contains an encoded blob token")
    _validate_multitoken_encoded_blob(value)


def _validate_multitoken_encoded_blob(value: str) -> None:
    """Reject long consecutive encoded chunks while preserving ordinary prose."""

    candidates: list[str] = []
    hex_candidates: list[str] = []
    for raw_token in re.findall(r"\S+", value):
        candidate = raw_token.strip(_BLOB_EDGE_PUNCTUATION)
        if (
            len(candidate) >= _MIN_SEGMENTED_HEX_CHUNK
            and _HEX_BLOB_RE.fullmatch(candidate)
        ):
            hex_candidates.append(candidate)
        else:
            _check_segmented_hex_blob_run(hex_candidates)
            hex_candidates = []
        if (
            len(candidate) >= _MIN_MULTI_TOKEN_BLOB_CHUNK
            and _BASE64_BLOB_RE.fullmatch(candidate)
        ):
            candidates.append(candidate)
            continue
        if len(candidates) >= 3:
            _check_multitoken_blob_run(candidates)
        candidates = []
    _check_segmented_hex_blob_run(hex_candidates)
    if len(candidates) >= 3:
        _check_multitoken_blob_run(candidates)


def _check_segmented_hex_blob_run(candidates: list[str]) -> None:
    if (
        len(candidates) >= _MIN_SEGMENTED_HEX_CHUNKS
        and sum(len(candidate) for candidate in candidates)
        >= _MIN_SEGMENTED_HEX_TOTAL
    ):
        raise ValueError("ai_description contains an encoded blob sequence")


def _check_multitoken_blob_run(candidates: list[str]) -> None:
    compact = "".join(candidates)
    if len(compact) < _MIN_MULTI_TOKEN_BLOB_TOTAL:
        return
    if all(_HEX_BLOB_RE.fullmatch(candidate) for candidate in candidates):
        raise ValueError("ai_description contains an encoded blob sequence")

    signal_count = sum(
        character.isupper()
        or character.isdigit()
        or character in "+/=_-"
        for character in compact
    )
    signal_ratio = signal_count / len(compact)
    frequencies = Counter(compact)
    entropy = -sum(
        (count / len(compact)) * log2(count / len(compact))
        for count in frequencies.values()
    )
    if signal_ratio >= 0.12 and entropy >= 3.2:
        raise ValueError("ai_description contains an encoded blob sequence")


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
        _validate_natural_language(ai_description)
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

    def selector_dict(self) -> dict[str, SerializedJSONValue]:
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


@dataclass(frozen=True, slots=True)
class PatternCandidate:
    """One exact, selector-facing pattern version in a shortlist."""

    pattern_id: str
    version: int
    rank: int
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "pattern_id",
            _identifier(self.pattern_id, name="pattern_id"),
        )
        if (
            isinstance(self.version, bool)
            or not isinstance(self.version, int)
            or not 1 <= self.version <= 9_999
        ):
            raise ValueError("version is invalid")
        if (
            isinstance(self.rank, bool)
            or not isinstance(self.rank, int)
            or self.rank < 1
            or self.rank > 5
        ):
            raise ValueError("rank is invalid")
        object.__setattr__(
            self,
            "reason",
            _text(self.reason, name="reason", maximum=500),
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "PatternCandidate":
        if not isinstance(payload, Mapping):
            raise ValueError("candidate must be an object")
        expected = {"pattern_id", "version", "rank", "reason"}
        if set(payload) != expected:
            raise ValueError("candidate fields do not match the contract")
        return cls(
            pattern_id=payload["pattern_id"],  # type: ignore[arg-type]
            version=payload["version"],  # type: ignore[arg-type]
            rank=payload["rank"],  # type: ignore[arg-type]
            reason=payload["reason"],  # type: ignore[arg-type]
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "pattern_id": self.pattern_id,
            "version": self.version,
            "rank": self.rank,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class PatternCandidateGroup:
    """Candidates for one atomic category, ordered by canonical rank."""

    category: AtomicPatternCategory
    candidates: tuple[PatternCandidate, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.category, AtomicPatternCategory):
            raise ValueError("category is invalid")
        candidates = tuple(self.candidates)
        if len(candidates) > 5:
            raise ValueError("a candidate group may contain at most five candidates")
        if any(not isinstance(item, PatternCandidate) for item in candidates):
            raise ValueError("candidates contain an invalid value")
        exact_versions = [(item.pattern_id, item.version) for item in candidates]
        if len(set(exact_versions)) != len(exact_versions):
            raise ValueError("candidate group contains duplicate candidates")
        if candidates and tuple(item.rank for item in candidates) != tuple(
            range(1, len(candidates) + 1)
        ):
            raise ValueError("candidate ranks must be canonical")
        object.__setattr__(self, "candidates", candidates)

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "PatternCandidateGroup":
        if not isinstance(payload, Mapping):
            raise ValueError("candidate group must be an object")
        expected = {"category", "candidates"}
        if set(payload) != expected:
            raise ValueError("candidate group fields do not match the contract")
        try:
            category = AtomicPatternCategory(payload["category"])
        except (TypeError, ValueError) as exc:
            raise ValueError("category is invalid") from exc
        raw_candidates = payload["candidates"]
        if not isinstance(raw_candidates, (list, tuple)):
            raise ValueError("candidates must be an array")
        return cls(
            category=category,
            candidates=tuple(PatternCandidate.from_dict(item) for item in raw_candidates),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "category": self.category.value,
            "candidates": [item.to_dict() for item in self.candidates],
        }


@dataclass(frozen=True, slots=True)
class PatternCandidatePlan:
    """Versioned AI shortlist persisted before stage generation begins."""

    schema_version: int
    direction_id: str
    groups: tuple[PatternCandidateGroup, ...]
    summary: str

    def __post_init__(self) -> None:
        if self.schema_version != 2:
            raise ValueError("schema_version must be 2")
        object.__setattr__(
            self,
            "direction_id",
            _identifier(self.direction_id, name="direction_id"),
        )
        groups = tuple(self.groups)
        if not 0 <= len(groups) <= len(AtomicPatternCategory):
            raise ValueError("groups must contain between zero and fourteen categories")
        if any(not isinstance(item, PatternCandidateGroup) for item in groups):
            raise ValueError("groups contain an invalid value")
        categories = [item.category for item in groups]
        if len(set(categories)) != len(categories):
            raise ValueError("plan contains duplicate categories")
        object.__setattr__(self, "groups", groups)
        object.__setattr__(
            self,
            "summary",
            _text(self.summary, name="summary", maximum=1_000),
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "PatternCandidatePlan":
        if not isinstance(payload, Mapping):
            raise ValueError("candidate plan must be an object")
        expected = {"schema_version", "direction_id", "groups", "summary"}
        if set(payload) != expected:
            raise ValueError("candidate plan fields do not match the contract")
        raw_groups = payload["groups"]
        if not isinstance(raw_groups, (list, tuple)):
            raise ValueError("groups must be an array")
        return cls(
            schema_version=payload["schema_version"],  # type: ignore[arg-type]
            direction_id=payload["direction_id"],  # type: ignore[arg-type]
            groups=tuple(PatternCandidateGroup.from_dict(item) for item in raw_groups),
            summary=payload["summary"],  # type: ignore[arg-type]
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "direction_id": self.direction_id,
            "groups": [item.to_dict() for item in self.groups],
            "summary": self.summary,
        }


@dataclass(frozen=True, slots=True)
class PatternAssetBundle:
    """Immutable exact implementation assets for one registry version."""

    html: str
    css: str
    javascript: str = ""

    def __post_init__(self) -> None:
        for name in ("html", "css", "javascript"):
            value = getattr(self, name)
            if not isinstance(value, str) or "\x00" in value:
                raise ValueError(f"{name} asset is invalid")

    def as_dict(self) -> dict[str, str]:
        return {"html": self.html, "css": self.css, "javascript": self.javascript}

    def __getitem__(self, name: str) -> str:
        if name not in {"html", "css", "javascript"}:
            raise KeyError(name)
        return getattr(self, name)

    @property
    def implementation_dict(self) -> dict[str, str]:
        return self.as_dict()


@dataclass(frozen=True, slots=True)
class PatternVersionRef:
    """Immutable exact ``pattern_id@version`` reference with its assets."""

    category: AtomicPatternCategory
    pattern_id: str
    version: int
    title: str = ""
    summary: str = ""
    ai_description: str = ""
    technical_contract: str = ""
    adaptation_policy: AdaptationPolicy = AdaptationPolicy.ADAPTIVE
    implementation_sha256: str = ""
    html: str = ""
    css: str = ""
    javascript: str = ""
    rank: int = 1
    reason: str = ""
    provenance: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.category, AtomicPatternCategory):
            raise ValueError("category is invalid")
        if not isinstance(self.pattern_id, str) or not self.pattern_id:
            raise ValueError("pattern_id is invalid")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("version is invalid")
        if isinstance(self.rank, bool) or not isinstance(self.rank, int) or self.rank < 1:
            raise ValueError("rank is invalid")
        if not isinstance(self.adaptation_policy, AdaptationPolicy):
            raise ValueError("adaptation_policy is invalid")
        if self.implementation_sha256 and not _SHA256_RE.fullmatch(self.implementation_sha256):
            raise ValueError("implementation_sha256 is invalid")
        for name in (
            "title",
            "summary",
            "ai_description",
            "technical_contract",
            "reason",
            "html",
            "css",
            "javascript",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or "\x00" in value:
                raise ValueError(f"{name} is invalid")
        if not isinstance(self.provenance, Mapping):
            raise ValueError("provenance is invalid")
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance)))

    @classmethod
    def from_definition(
        cls,
        definition: AtomicPatternDefinition,
        candidate: PatternCandidate,
    ) -> "PatternVersionRef":
        return cls(
            category=definition.category,
            pattern_id=definition.pattern_id,
            version=definition.version,
            title=definition.title,
            summary=definition.summary,
            ai_description=definition.ai_description,
            technical_contract=definition.technical_contract,
            adaptation_policy=definition.adaptation_policy,
            implementation_sha256=definition.implementation_sha256,
            html=definition.html,
            css=definition.css,
            javascript=definition.javascript,
            rank=candidate.rank,
            reason=candidate.reason,
            provenance=definition.provenance,
        )

    @property
    def assets(self) -> PatternAssetBundle:
        return PatternAssetBundle(self.html, self.css, self.javascript)

    @property
    def asset_bundle(self) -> PatternAssetBundle:
        return self.assets

    @property
    def implementation(self) -> PatternAssetBundle:
        return self.assets

    @property
    def implementation_assets(self) -> PatternAssetBundle:
        return self.assets

    def implementation_dict(self) -> dict[str, str]:
        return self.assets.as_dict()

    @property
    def exact_version(self) -> tuple[str, int]:
        return self.pattern_id, self.version

    def manifest_metadata(self) -> dict[str, SerializedJSONValue]:
        return {
            "pattern_id": self.pattern_id,
            "version": self.version,
            "category": self.category.value,
            "title": self.title,
            "summary": self.summary,
            "ai_description": self.ai_description,
            "technical_contract": self.technical_contract,
            "adaptation_policy": self.adaptation_policy.value,
            "implementation_sha256": self.implementation_sha256,
            "provenance": _thaw_json(self.provenance),
            "rank": self.rank,
            "reason": self.reason,
        }


PatternCandidateVersionRef = PatternVersionRef
ExactPatternVersionRef = PatternVersionRef
PatternCandidateAssets = PatternAssetBundle


__all__ = [
    "AdaptationPolicy",
    "AtomicPatternCategory",
    "AtomicPatternDefinition",
    "AtomicPatternStatus",
    "JSONValue",
    "PatternAssetBundle",
    "PatternCandidate",
    "PatternCandidateAssets",
    "PatternCandidateGroup",
    "PatternCandidatePlan",
    "PatternCandidateVersionRef",
    "PatternVersionRef",
    "ExactPatternVersionRef",
    "SerializedJSONValue",
]
