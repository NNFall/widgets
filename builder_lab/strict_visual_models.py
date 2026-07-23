from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from .visual_models import NormalizedRegion


_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
_REVISION_FIELDS = frozenset(
    {
        "art_direction",
        "body_html",
        "css",
        "javascript",
        "layout_contract",
        "suggested_actions",
        "theme_tokens",
    }
)


class StrictVisualDimension(str, Enum):
    DIRECTION_FIDELITY = "direction_fidelity"
    PAGE_SUBORDINATION = "page_subordination"
    VISUAL_HIERARCHY = "visual_hierarchy"
    CONVERSATION_CLARITY = "conversation_clarity"
    TYPOGRAPHY_LEGIBILITY = "typography_legibility"
    SPACING_ALIGNMENT = "spacing_alignment"
    SYSTEM_COHERENCE = "system_coherence"
    RESPONSIVE_COMPOSITION = "responsive_composition"
    CRAFT_POLISH = "craft_polish"
    DISTINCTIVENESS = "distinctiveness"


STRICT_VISUAL_DIMENSIONS = tuple(StrictVisualDimension)

CORE_VISUAL_DIMENSIONS = frozenset(
    {
        StrictVisualDimension.DIRECTION_FIDELITY,
        StrictVisualDimension.PAGE_SUBORDINATION,
        StrictVisualDimension.CONVERSATION_CLARITY,
        StrictVisualDimension.RESPONSIVE_COMPOSITION,
    }
)

STRICT_VISUAL_WEIGHTS: dict[StrictVisualDimension, float] = {
    StrictVisualDimension.DIRECTION_FIDELITY: 1.1,
    StrictVisualDimension.PAGE_SUBORDINATION: 1.2,
    StrictVisualDimension.VISUAL_HIERARCHY: 1.0,
    StrictVisualDimension.CONVERSATION_CLARITY: 1.3,
    StrictVisualDimension.TYPOGRAPHY_LEGIBILITY: 1.0,
    StrictVisualDimension.SPACING_ALIGNMENT: 0.8,
    StrictVisualDimension.SYSTEM_COHERENCE: 1.0,
    StrictVisualDimension.RESPONSIVE_COMPOSITION: 1.2,
    StrictVisualDimension.CRAFT_POLISH: 0.8,
    StrictVisualDimension.DISTINCTIVENESS: 0.6,
}


class StrictVisualSeverity(str, Enum):
    BLOCKER = "blocker"
    MAJOR = "major"
    MINOR = "minor"


class StrictVisualVerdict(str, Enum):
    PASS = "pass"
    REPAIR = "repair"


def _enum(enum_type: type[Enum], value: Any, field_name: str):
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unsupported {field_name}: {value!r}") from exc


def _text(value: Any, field_name: str, limit: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    normalized = value.strip()
    if not normalized or len(normalized) > limit:
        raise ValueError(f"{field_name} is invalid")
    return normalized


def _identifier(value: Any, field_name: str) -> str:
    normalized = _text(value, field_name, 80)
    if not _ID_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field_name} is invalid")
    return normalized


def _confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("confidence must be a number")
    result = float(value)
    if not math.isfinite(result) or not 0 <= result <= 1:
        raise ValueError("confidence must be between 0 and 1")
    return result


@dataclass(frozen=True)
class StrictVisualObservation:
    screenshot_id: str
    observation: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "screenshot_id",
            _identifier(self.screenshot_id, "screenshot_id"),
        )
        object.__setattr__(
            self,
            "observation",
            _text(self.observation, "observation", 1200),
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "StrictVisualObservation":
        return cls(
            screenshot_id=payload.get("screenshot_id"),
            observation=payload.get("observation"),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "screenshot_id": self.screenshot_id,
            "observation": self.observation,
        }


@dataclass(frozen=True)
class StrictVisualFinding:
    finding_id: str
    dimension: StrictVisualDimension
    screenshot_id: str
    evidence: str
    region: NormalizedRegion
    confidence: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "finding_id",
            _identifier(self.finding_id, "finding_id"),
        )
        if not isinstance(self.dimension, StrictVisualDimension):
            object.__setattr__(
                self,
                "dimension",
                _enum(
                    StrictVisualDimension,
                    self.dimension,
                    "strict visual dimension",
                ),
            )
        object.__setattr__(
            self,
            "screenshot_id",
            _identifier(self.screenshot_id, "screenshot_id"),
        )
        object.__setattr__(self, "evidence", _text(self.evidence, "evidence", 1600))
        if not isinstance(self.region, NormalizedRegion):
            raise ValueError("region must be a NormalizedRegion")
        confidence = _confidence(self.confidence)
        if confidence < 0.80:
            raise ValueError("finding confidence must be at least 0.80")
        object.__setattr__(self, "confidence", confidence)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "StrictVisualFinding":
        return cls(
            finding_id=payload.get("finding_id"),
            dimension=_enum(
                StrictVisualDimension,
                payload.get("dimension"),
                "strict visual dimension",
            ),
            screenshot_id=payload.get("screenshot_id"),
            evidence=payload.get("evidence"),
            region=NormalizedRegion.from_dict(payload.get("region", {})),
            confidence=payload.get("confidence"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "dimension": self.dimension.value,
            "screenshot_id": self.screenshot_id,
            "evidence": self.evidence,
            "region": self.region.to_dict(),
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class StrictVisualAssessment:
    dimension: StrictVisualDimension
    score: int
    confidence: float
    finding_ids: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.dimension, StrictVisualDimension):
            object.__setattr__(
                self,
                "dimension",
                _enum(
                    StrictVisualDimension,
                    self.dimension,
                    "strict visual dimension",
                ),
            )
        if (
            isinstance(self.score, bool)
            or not isinstance(self.score, int)
            or not 0 <= self.score <= 5
        ):
            raise ValueError("score must be an integer between 0 and 5")
        confidence = _confidence(self.confidence)
        finding_ids = tuple(
            _identifier(value, "assessment finding id")
            for value in self.finding_ids
        )
        if len(finding_ids) != len(set(finding_ids)) or len(finding_ids) > 6:
            raise ValueError("assessment finding ids are invalid")
        if self.score == 0:
            if finding_ids:
                raise ValueError("not_observable assessment cannot link a finding")
        else:
            if confidence < 0.80:
                raise ValueError(
                    "observable assessment confidence must be at least 0.80"
                )
            if self.score <= 3 and not finding_ids:
                raise ValueError("score 1..3 requires a concrete linked finding")
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "finding_ids", finding_ids)

    @property
    def not_observable(self) -> bool:
        return self.score == 0

    @property
    def severity(self) -> StrictVisualSeverity | None:
        return {
            1: StrictVisualSeverity.BLOCKER,
            2: StrictVisualSeverity.MAJOR,
            3: StrictVisualSeverity.MINOR,
        }.get(self.score)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "StrictVisualAssessment":
        raw_ids = payload.get("finding_ids", ())
        if not isinstance(raw_ids, (list, tuple)):
            raise ValueError("finding_ids must be an array")
        return cls(
            dimension=_enum(
                StrictVisualDimension,
                payload.get("dimension"),
                "strict visual dimension",
            ),
            score=payload.get("score"),
            confidence=payload.get("confidence"),
            finding_ids=tuple(raw_ids),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension.value,
            "score": self.score,
            "confidence": self.confidence,
            "finding_ids": list(self.finding_ids),
        }


@dataclass(frozen=True)
class StrictVisualRevisionAction:
    action_id: str
    finding_ids: tuple[str, ...]
    artifact_fields: tuple[str, ...]
    instruction: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "action_id",
            _identifier(self.action_id, "action_id"),
        )
        finding_ids = tuple(
            _identifier(value, "revision finding id") for value in self.finding_ids
        )
        if (
            not finding_ids
            or len(finding_ids) > 6
            or len(finding_ids) != len(set(finding_ids))
        ):
            raise ValueError("revision action must link one to six findings")
        fields = tuple(
            _identifier(value, "revision artifact field")
            for value in self.artifact_fields
        )
        if (
            not fields
            or len(fields) > 4
            or len(fields) != len(set(fields))
            or any(value not in _REVISION_FIELDS for value in fields)
        ):
            raise ValueError("revision action artifact fields are invalid")
        object.__setattr__(self, "finding_ids", finding_ids)
        object.__setattr__(self, "artifact_fields", fields)
        object.__setattr__(
            self,
            "instruction",
            _text(self.instruction, "revision instruction", 1200),
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "StrictVisualRevisionAction":
        return cls(
            action_id=payload.get("action_id"),
            finding_ids=tuple(payload.get("finding_ids", ())),
            artifact_fields=tuple(payload.get("artifact_fields", ())),
            instruction=payload.get("instruction"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "finding_ids": list(self.finding_ids),
            "artifact_fields": list(self.artifact_fields),
            "instruction": self.instruction,
        }


@dataclass(frozen=True)
class StrictVisualCritique:
    observations: tuple[StrictVisualObservation, ...]
    assessments: tuple[StrictVisualAssessment, ...]
    findings: tuple[StrictVisualFinding, ...]
    revision_actions: tuple[StrictVisualRevisionAction, ...]
    summary: str

    def __post_init__(self) -> None:
        observations = tuple(self.observations)
        assessments = tuple(self.assessments)
        findings = tuple(self.findings)
        revision_actions = tuple(self.revision_actions)
        if not 1 <= len(observations) <= 12 or any(
            not isinstance(item, StrictVisualObservation)
            for item in observations
        ):
            raise ValueError("strict critique observations are invalid")
        dimensions = tuple(item.dimension for item in assessments)
        if (
            len(assessments) != len(STRICT_VISUAL_DIMENSIONS)
            or set(dimensions) != set(STRICT_VISUAL_DIMENSIONS)
        ):
            raise ValueError("strict critique requires exactly ten dimensions")
        if any(not isinstance(item, StrictVisualFinding) for item in findings):
            raise ValueError("strict critique findings are invalid")
        finding_ids = tuple(item.finding_id for item in findings)
        if len(finding_ids) != len(set(finding_ids)) or len(findings) > 20:
            raise ValueError("strict critique finding ids are invalid")
        finding_by_id = {item.finding_id: item for item in findings}
        linked_ids = {
            finding_id
            for assessment in assessments
            for finding_id in assessment.finding_ids
        }
        unknown = linked_ids - set(finding_ids)
        if unknown:
            raise ValueError(
                "assessment links an unknown finding: " + ",".join(sorted(unknown))
            )
        for assessment in assessments:
            if not 1 <= assessment.score <= 3:
                continue
            if any(
                finding_by_id[finding_id].dimension is not assessment.dimension
                for finding_id in assessment.finding_ids
            ):
                raise ValueError(
                    "score 1..3 must link a finding from the same dimension"
                )
        if len(revision_actions) > 3 or any(
            not isinstance(item, StrictVisualRevisionAction)
            for item in revision_actions
        ):
            raise ValueError("strict critique allows at most three revision actions")
        action_ids = tuple(item.action_id for item in revision_actions)
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("strict critique revision action ids must be unique")
        action_links = {
            finding_id
            for action in revision_actions
            for finding_id in action.finding_ids
        }
        unknown = action_links - set(finding_ids)
        if unknown:
            raise ValueError(
                "revision action links an unknown finding: "
                + ",".join(sorted(unknown))
            )
        uncovered = linked_ids - action_links
        if uncovered:
            raise ValueError(
                "every failing finding must be covered by a revision action: "
                + ",".join(sorted(uncovered))
            )
        ordered = tuple(
            next(
                item for item in assessments if item.dimension is expected_dimension
            )
            for expected_dimension in STRICT_VISUAL_DIMENSIONS
        )
        object.__setattr__(self, "observations", observations)
        object.__setattr__(self, "assessments", ordered)
        object.__setattr__(self, "findings", findings)
        object.__setattr__(self, "revision_actions", revision_actions)
        object.__setattr__(self, "summary", _text(self.summary, "summary", 2000))
        if self.verdict is StrictVisualVerdict.REPAIR and not revision_actions:
            raise ValueError("repair critique requires at least one revision action")

    @property
    def weighted_score(self) -> float:
        observable = tuple(item for item in self.assessments if item.score > 0)
        if not observable:
            return 0.0
        numerator = sum(
            item.score * STRICT_VISUAL_WEIGHTS[item.dimension]
            for item in observable
        )
        denominator = sum(
            STRICT_VISUAL_WEIGHTS[item.dimension] for item in observable
        )
        return numerator / denominator

    @property
    def verdict(self) -> StrictVisualVerdict:
        observable = tuple(item for item in self.assessments if item.score > 0)
        core_scores = tuple(
            item.score
            for item in observable
            if item.dimension in CORE_VISUAL_DIMENSIONS
        )
        repair = (
            self.weighted_score < 4.0
            or any(score < 4 for score in core_scores)
            or any(item.score <= 2 for item in observable)
        )
        return StrictVisualVerdict.REPAIR if repair else StrictVisualVerdict.PASS

    @property
    def allowed_artifact_fields(self) -> frozenset[str]:
        return frozenset(
            field_name
            for action in self.revision_actions
            for field_name in action.artifact_fields
        )

    @classmethod
    def from_model_dict(cls, payload: Mapping[str, Any]) -> "StrictVisualCritique":
        if "verdict" in payload:
            raise ValueError("model must not return verdict")
        expected = {
            "observations",
            "assessments",
            "findings",
            "revision_actions",
            "summary",
        }
        if set(payload) != expected:
            raise ValueError("strict visual critique fields do not match contract")
        return cls(
            observations=tuple(
                StrictVisualObservation.from_dict(item)
                for item in payload["observations"]
            ),
            assessments=tuple(
                StrictVisualAssessment.from_dict(item)
                for item in payload["assessments"]
            ),
            findings=tuple(
                StrictVisualFinding.from_dict(item) for item in payload["findings"]
            ),
            revision_actions=tuple(
                StrictVisualRevisionAction.from_dict(item)
                for item in payload["revision_actions"]
            ),
            summary=payload["summary"],
        )

    def to_model_dict(self) -> dict[str, Any]:
        return {
            "observations": [item.to_dict() for item in self.observations],
            "assessments": [item.to_dict() for item in self.assessments],
            "findings": [item.to_dict() for item in self.findings],
            "revision_actions": [
                item.to_dict() for item in self.revision_actions
            ],
            "summary": self.summary,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.to_model_dict(),
            "weighted_score": self.weighted_score,
            "verdict": self.verdict.value,
        }


__all__ = [
    "CORE_VISUAL_DIMENSIONS",
    "STRICT_VISUAL_DIMENSIONS",
    "STRICT_VISUAL_WEIGHTS",
    "StrictVisualAssessment",
    "StrictVisualCritique",
    "StrictVisualDimension",
    "StrictVisualFinding",
    "StrictVisualObservation",
    "StrictVisualRevisionAction",
    "StrictVisualSeverity",
    "StrictVisualVerdict",
]
