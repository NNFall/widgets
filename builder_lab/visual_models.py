from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ScreenshotState(str, Enum):
    DESKTOP_CLOSED = "desktop.closed"
    DESKTOP_OPEN_INITIAL = "desktop.open_initial"
    DESKTOP_AFTER_TURN_2 = "desktop.after_turn_2"
    MOBILE_CLOSED = "mobile.closed"
    MOBILE_OPEN_INITIAL = "mobile.open_initial"
    MOBILE_AFTER_TURN_2 = "mobile.after_turn_2"


class LayoutState(str, Enum):
    DESKTOP_CLOSED = "desktop.closed"
    DESKTOP_OPEN_INITIAL = "desktop.open_initial"
    DESKTOP_AFTER_TURN_1 = "desktop.after_turn_1"
    DESKTOP_AFTER_TURN_2 = "desktop.after_turn_2"
    MOBILE_CLOSED = "mobile.closed"
    MOBILE_OPEN_INITIAL = "mobile.open_initial"
    MOBILE_AFTER_TURN_1 = "mobile.after_turn_1"
    MOBILE_AFTER_TURN_2 = "mobile.after_turn_2"


class VisualSeverity(str, Enum):
    BLOCKER = "blocker"
    MAJOR = "major"
    MINOR = "minor"


class VisualCategory(str, Enum):
    SITE_FIT = "site_fit"
    PAGE_SUBORDINATION = "page_subordination"
    FUNCTIONAL_TRUTH = "functional_truth"
    RESPONSIVE_INTEGRITY = "responsive_integrity"
    ACCESSIBILITY = "accessibility"
    RUNTIME_FEASIBILITY = "runtime_feasibility"


class VisualVerdict(str, Enum):
    PASS = "pass"
    REPAIR = "repair"


def _enum(enum_type: type[Enum], value: Any, field_name: str):
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unsupported {field_name}: {value!r}") from exc


def _bounded_text(value: str, field_name: str, limit: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized or len(normalized) > limit:
        raise ValueError(f"{field_name} is invalid")
    return normalized


def _bounded_id(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    normalized = value.strip()
    if not _ID_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field_name} is invalid")
    return normalized


def _finite(value: float, field_name: str, *, lower: float, upper: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a number")
    number = float(value)
    if not math.isfinite(number) or not lower <= number <= upper:
        raise ValueError(f"{field_name} is out of bounds")
    return number


def _strict_int(value: Any, field_name: str, *, lower: int, upper: int) -> int:
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError(f"{field_name} is out of bounds")
    return value


def _strict_bool(value: Any, field_name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{field_name} must be a boolean")
    return value


@dataclass(frozen=True)
class ScreenshotEvidence:
    screenshot_id: str
    state: ScreenshotState
    sha256: str
    mime_type: str
    byte_count: int
    width: int
    height: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "screenshot_id", _bounded_id(self.screenshot_id, "screenshot_id"))
        if not isinstance(self.state, ScreenshotState):
            object.__setattr__(self, "state", _enum(ScreenshotState, self.state, "screenshot state"))
        if not isinstance(self.sha256, str):
            raise ValueError("sha256 must be a string")
        digest = self.sha256.strip().lower()
        if not _SHA256_PATTERN.fullmatch(digest):
            raise ValueError("sha256 must be a lowercase hexadecimal digest")
        if not isinstance(self.mime_type, str) or self.mime_type not in {
            "image/jpeg",
            "image/png",
        }:
            raise ValueError("unsupported screenshot mime_type")
        _strict_int(self.byte_count, "byte_count", lower=1, upper=1_500_000)
        _strict_int(self.width, "width", lower=1, upper=8_192)
        _strict_int(self.height, "height", lower=1, upper=8_192)
        object.__setattr__(self, "sha256", digest)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ScreenshotEvidence":
        return cls(
            screenshot_id=payload["screenshot_id"],
            state=_enum(ScreenshotState, payload["state"], "screenshot state"),
            sha256=payload["sha256"],
            mime_type=payload["mime_type"],
            byte_count=payload["byte_count"],
            width=payload["width"],
            height=payload["height"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "screenshot_id": self.screenshot_id,
            "state": self.state.value,
            "sha256": self.sha256,
            "mime_type": self.mime_type,
            "byte_count": self.byte_count,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True)
class RegionEvidence:
    region: str
    x: float
    y: float
    width: float
    height: float
    client_width: int
    scroll_width: int
    client_height: int
    scroll_height: int
    visible: bool
    clipped: bool = False
    overlaps: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "region", _bounded_id(self.region, "region"))
        for field_name in ("x", "y"):
            object.__setattr__(
                self,
                field_name,
                _finite(getattr(self, field_name), field_name, lower=-20_000, upper=20_000),
            )
        for field_name in ("width", "height"):
            object.__setattr__(
                self,
                field_name,
                _finite(getattr(self, field_name), field_name, lower=0, upper=20_000),
            )
        for field_name in (
            "client_width",
            "scroll_width",
            "client_height",
            "scroll_height",
        ):
            _strict_int(
                getattr(self, field_name),
                field_name,
                lower=0,
                upper=100_000,
            )
        _strict_bool(self.visible, "visible")
        _strict_bool(self.clipped, "clipped")
        overlaps = tuple(_bounded_id(value, "overlap region") for value in self.overlaps)
        if len(overlaps) > 16 or len(set(overlaps)) != len(overlaps):
            raise ValueError("overlap regions are invalid")
        object.__setattr__(self, "overlaps", overlaps)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RegionEvidence":
        return cls(
            region=payload["region"],
            x=payload["x"],
            y=payload["y"],
            width=payload["width"],
            height=payload["height"],
            client_width=payload["client_width"],
            scroll_width=payload["scroll_width"],
            client_height=payload["client_height"],
            scroll_height=payload["scroll_height"],
            visible=payload["visible"],
            clipped=payload.get("clipped", False),
            overlaps=tuple(payload.get("overlaps", ())),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "region": self.region,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "client_width": self.client_width,
            "scroll_width": self.scroll_width,
            "client_height": self.client_height,
            "scroll_height": self.scroll_height,
            "visible": self.visible,
            "clipped": self.clipped,
            "overlaps": list(self.overlaps),
        }


@dataclass(frozen=True)
class LayoutEvidence:
    evidence_id: str
    state: LayoutState
    viewport_width: int
    viewport_height: int
    screenshot_id: str | None = None
    regions: tuple[RegionEvidence, ...] = ()
    horizontal_overflow_px: float = 0
    panel_inside_viewport: bool = False
    first_open_transcript_scrollable: bool = False
    visible_action_count: int = 0
    active_element: str = "body"
    transcript_roles: tuple[str, ...] = ()
    aria_states: tuple[str, ...] = ()
    console_errors: tuple[str, ...] = ()
    page_errors: tuple[str, ...] = ()
    request_failures: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_id", _bounded_id(self.evidence_id, "evidence_id"))
        if not isinstance(self.state, LayoutState):
            object.__setattr__(self, "state", _enum(LayoutState, self.state, "layout state"))
        layout_only = self.state in {
            LayoutState.DESKTOP_AFTER_TURN_1,
            LayoutState.MOBILE_AFTER_TURN_1,
        }
        if layout_only:
            if self.screenshot_id is not None:
                raise ValueError("after_turn_1 layout must not claim a seventh screenshot")
        else:
            if self.screenshot_id is None:
                raise ValueError("screenshot-backed layout requires screenshot_id")
            object.__setattr__(
                self,
                "screenshot_id",
                _bounded_id(self.screenshot_id, "screenshot_id"),
            )
        _strict_int(self.viewport_width, "viewport_width", lower=1, upper=20_000)
        _strict_int(self.viewport_height, "viewport_height", lower=1, upper=20_000)
        regions = tuple(self.regions)
        if any(not isinstance(region, RegionEvidence) for region in regions):
            raise ValueError("regions must contain RegionEvidence values")
        region_ids = [region.region for region in regions]
        if len(regions) > 48 or len(region_ids) != len(set(region_ids)):
            raise ValueError("layout regions must be bounded and unique")
        object.__setattr__(self, "regions", regions)
        object.__setattr__(
            self,
            "horizontal_overflow_px",
            _finite(
                self.horizontal_overflow_px,
                "horizontal_overflow_px",
                lower=0,
                upper=20_000,
            ),
        )
        _strict_bool(self.panel_inside_viewport, "panel_inside_viewport")
        _strict_bool(
            self.first_open_transcript_scrollable,
            "first_open_transcript_scrollable",
        )
        _strict_int(
            self.visible_action_count,
            "visible_action_count",
            lower=0,
            upper=100,
        )
        active_element = self.active_element.strip() if isinstance(self.active_element, str) else ""
        if not active_element or len(active_element) > 160:
            raise ValueError("active_element is invalid")
        object.__setattr__(self, "active_element", active_element)
        for field_name, limit, item_limit in (
            ("transcript_roles", 32, 32),
            ("aria_states", 16, 160),
            ("console_errors", 20, 500),
            ("page_errors", 20, 500),
            ("request_failures", 20, 500),
        ):
            raw_values = tuple(getattr(self, field_name))
            if any(not isinstance(value, str) for value in raw_values):
                raise ValueError(f"{field_name} must contain strings")
            values = tuple(value.strip() for value in raw_values)
            if len(values) > limit or any(not value or len(value) > item_limit for value in values):
                raise ValueError(f"{field_name} is invalid")
            object.__setattr__(self, field_name, values)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "LayoutEvidence":
        return cls(
            evidence_id=payload["evidence_id"],
            state=_enum(LayoutState, payload["state"], "layout state"),
            viewport_width=payload["viewport_width"],
            viewport_height=payload["viewport_height"],
            screenshot_id=(
                payload["screenshot_id"]
                if payload.get("screenshot_id") is not None
                else None
            ),
            regions=tuple(RegionEvidence.from_dict(item) for item in payload.get("regions", ())),
            horizontal_overflow_px=payload.get("horizontal_overflow_px", 0),
            panel_inside_viewport=payload.get("panel_inside_viewport", False),
            first_open_transcript_scrollable=payload.get(
                "first_open_transcript_scrollable", False
            ),
            visible_action_count=payload.get("visible_action_count", 0),
            active_element=payload.get("active_element", "body"),
            transcript_roles=tuple(payload.get("transcript_roles", ())),
            aria_states=tuple(payload.get("aria_states", ())),
            console_errors=tuple(payload.get("console_errors", ())),
            page_errors=tuple(payload.get("page_errors", ())),
            request_failures=tuple(payload.get("request_failures", ())),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "state": self.state.value,
            "screenshot_id": self.screenshot_id,
            "viewport_width": self.viewport_width,
            "viewport_height": self.viewport_height,
            "regions": [region.to_dict() for region in self.regions],
            "horizontal_overflow_px": self.horizontal_overflow_px,
            "panel_inside_viewport": self.panel_inside_viewport,
            "first_open_transcript_scrollable": self.first_open_transcript_scrollable,
            "visible_action_count": self.visible_action_count,
            "active_element": self.active_element,
            "transcript_roles": list(self.transcript_roles),
            "aria_states": list(self.aria_states),
            "console_errors": list(self.console_errors),
            "page_errors": list(self.page_errors),
            "request_failures": list(self.request_failures),
        }


@dataclass(frozen=True)
class NormalizedRegion:
    x: float
    y: float
    width: float
    height: float
    semantic_region: str | None = None

    def __post_init__(self) -> None:
        x = _finite(self.x, "region.x", lower=0, upper=1)
        y = _finite(self.y, "region.y", lower=0, upper=1)
        width = _finite(self.width, "region.width", lower=0, upper=1)
        height = _finite(self.height, "region.height", lower=0, upper=1)
        if width <= 0 or height <= 0 or x + width > 1 or y + height > 1:
            raise ValueError("normalized region must fit inside the screenshot")
        semantic_region = self.semantic_region
        if semantic_region is not None:
            semantic_region = _bounded_id(semantic_region, "semantic_region")
        object.__setattr__(self, "x", x)
        object.__setattr__(self, "y", y)
        object.__setattr__(self, "width", width)
        object.__setattr__(self, "height", height)
        object.__setattr__(self, "semantic_region", semantic_region)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "NormalizedRegion":
        return cls(
            x=payload["x"],
            y=payload["y"],
            width=payload["width"],
            height=payload["height"],
            semantic_region=(
                payload["semantic_region"]
                if payload.get("semantic_region") is not None
                else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "semantic_region": self.semantic_region,
        }


@dataclass(frozen=True)
class VisualFinding:
    finding_id: str
    severity: VisualSeverity
    category: VisualCategory
    screenshot_id: str
    evidence: str
    region: NormalizedRegion
    artifact_fields: tuple[str, ...]
    repair_instruction: str
    confidence: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "finding_id", _bounded_id(self.finding_id, "finding_id"))
        if not isinstance(self.severity, VisualSeverity):
            object.__setattr__(self, "severity", _enum(VisualSeverity, self.severity, "severity"))
        if not isinstance(self.category, VisualCategory):
            object.__setattr__(self, "category", _enum(VisualCategory, self.category, "category"))
        object.__setattr__(self, "screenshot_id", _bounded_id(self.screenshot_id, "screenshot_id"))
        object.__setattr__(self, "evidence", _bounded_text(self.evidence, "evidence", 1000))
        if not isinstance(self.region, NormalizedRegion):
            raise ValueError("region must be a NormalizedRegion")
        fields = tuple(_bounded_id(value, "artifact field") for value in self.artifact_fields)
        if not fields or len(fields) > 4 or len(fields) != len(set(fields)):
            raise ValueError("artifact_fields must contain one to four unique fields")
        object.__setattr__(self, "artifact_fields", fields)
        object.__setattr__(
            self,
            "repair_instruction",
            _bounded_text(self.repair_instruction, "repair_instruction", 1000),
        )
        object.__setattr__(
            self,
            "confidence",
            _finite(self.confidence, "confidence", lower=0, upper=1),
        )

    @property
    def fingerprint(self) -> str:
        semantic = self.to_dict()
        semantic.pop("finding_id")
        payload = json.dumps(
            semantic,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "VisualFinding":
        return cls(
            finding_id=payload["finding_id"],
            severity=_enum(VisualSeverity, payload["severity"], "severity"),
            category=_enum(VisualCategory, payload["category"], "category"),
            screenshot_id=payload["screenshot_id"],
            evidence=payload["evidence"],
            region=NormalizedRegion.from_dict(payload["region"]),
            artifact_fields=tuple(payload["artifact_fields"]),
            repair_instruction=payload["repair_instruction"],
            confidence=payload["confidence"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "severity": self.severity.value,
            "category": self.category.value,
            "screenshot_id": self.screenshot_id,
            "evidence": self.evidence,
            "region": self.region.to_dict(),
            "artifact_fields": list(self.artifact_fields),
            "repair_instruction": self.repair_instruction,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class VisualCritique:
    verdict: VisualVerdict
    summary: str
    findings: tuple[VisualFinding, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.verdict, VisualVerdict):
            object.__setattr__(self, "verdict", _enum(VisualVerdict, self.verdict, "verdict"))
        object.__setattr__(self, "summary", _bounded_text(self.summary, "summary", 2000))
        findings = tuple(self.findings)
        if len(findings) > 12:
            raise ValueError("visual critique exceeds finding limit")
        ids = [finding.finding_id for finding in findings]
        if len(ids) != len(set(ids)):
            raise ValueError("visual finding ids must be unique")
        if self.verdict is VisualVerdict.PASS and any(
            finding.severity in {VisualSeverity.BLOCKER, VisualSeverity.MAJOR}
            and finding.confidence >= 0.75
            for finding in findings
        ):
            raise ValueError("pass verdict cannot contain repair-triggering findings")
        if self.verdict is VisualVerdict.REPAIR and not any(
            finding.severity in {VisualSeverity.BLOCKER, VisualSeverity.MAJOR}
            and finding.confidence >= 0.75
            for finding in findings
        ):
            raise ValueError("repair verdict requires a confident blocker or major finding")
        object.__setattr__(self, "findings", findings)

    @property
    def requires_repair(self) -> bool:
        return any(
            finding.severity in {VisualSeverity.BLOCKER, VisualSeverity.MAJOR}
            and finding.confidence >= 0.75
            for finding in self.findings
        )

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            {
                "verdict": self.verdict.value,
                "findings": sorted(finding.fingerprint for finding in self.findings),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "VisualCritique":
        return cls(
            verdict=_enum(VisualVerdict, payload["verdict"], "verdict"),
            summary=payload["summary"],
            findings=tuple(VisualFinding.from_dict(item) for item in payload.get("findings", ())),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "summary": self.summary,
            "findings": [finding.to_dict() for finding in self.findings],
        }
