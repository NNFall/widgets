from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from .contracts import resolve_widget_contract


class EngineName(str, Enum):
    DIRECT = "direct"
    ANTIGRAVITY = "antigravity"


class CreativeProfile(str, Enum):
    BALANCED = "balanced"
    PRODUCT_CHAT = "product_chat"
    BRAND_MOTION = "brand_motion"
    AI_CHARACTER = "ai_character"


class ConceptRole(str, Enum):
    SITE_BRAND_ANALYST = "site_brand_analyst"
    CONVERSATION_DESIGNER = "conversation_designer"
    ART_DIRECTOR_FRONTEND_DEVELOPER = "art_director_frontend_developer"


class DirectionRole(str, Enum):
    BRAND_ARCHAEOLOGIST = "brand_archaeologist"
    INTERACTION_INVENTOR = "interaction_inventor"
    HOSTILE_CONVERSION_ACCESSIBILITY_CRITIC = (
        "hostile_conversion_accessibility_critic"
    )


class Stage(str, Enum):
    ART_DIRECTION = "art_direction"
    FOUNDATION = "foundation"
    IDENTITY = "identity"
    CONVERSATION = "conversation"
    MOTION_POLISH = "motion_polish"
    VALIDATION = "validation"
    AGENT_BUILD = "agent_build"


STAGE_DISPLAY_NAMES = {
    Stage.ART_DIRECTION: "арт-направление",
    Stage.FOUNDATION: "основа виджета",
    Stage.IDENTITY: "фирменный стиль",
    Stage.CONVERSATION: "диалог",
    Stage.MOTION_POLISH: "анимации и финальная отделка",
    Stage.VALIDATION: "техническая проверка",
    Stage.AGENT_BUILD: "агентская сборка",
}


def stage_display_name(stage: Stage) -> str:
    return STAGE_DISPLAY_NAMES[stage]


class RunStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


PUBLIC_ERROR_CODES = frozenset(
    {
        "missing_api_key",
        "provider_unavailable",
        "model_unavailable",
        "agent_unavailable",
        "quota_exceeded",
        "generation_timeout",
        "invalid_artifact",
        "visual_quality_failed",
        "visual_review_inconclusive",
        "snapshot_download_failed",
        "snapshot_rejected",
        "reference_url_unsafe",
        "reference_capture_failed",
        "reference_capture_incomplete",
        "reference_analysis_invalid",
        "reference_analysis_too_large",
        "reference_analysis_failed",
        "run_cancelled",
        "internal_error",
    }
)


def _enum(enum_type: type[Enum], value: Any, field_name: str):
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Unsupported {field_name}: {value!r}") from exc


def _source_url(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("source_url must be text")
    raw = value.strip()
    if not raw:
        return ""
    if len(raw) > 2_048:
        raise ValueError("source_url is too large")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("source_url is invalid") from exc
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme.lower() != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("source_url must be a public-style HTTPS URL")
    path = parsed.path or "/"
    return urlunsplit(("https", host, path, "", ""))


@dataclass(frozen=True)
class BuilderRequest:
    engine: EngineName
    brief: str
    reference_context: str = ""
    source_url: str = ""
    locale: str = "ru"
    creativity: float = 0.9
    viewport_targets: tuple[str, ...] = ("desktop", "mobile")
    max_repairs: int = 3
    contract_id: str = "chat-v1"
    creative_profile: CreativeProfile = CreativeProfile.BALANCED
    visual_repair_limit: int = 8

    def __post_init__(self) -> None:
        brief = self.brief.strip()
        reference_context = self.reference_context.strip()
        source_url = _source_url(self.source_url)
        locale = self.locale.strip().lower()
        if not brief:
            raise ValueError("brief must not be empty")
        if len(brief) > 12_000:
            raise ValueError("brief is too large")
        if len(reference_context) > 8_000 or "\x00" in reference_context:
            raise ValueError("reference_context is invalid")
        if not locale or len(locale) > 16:
            raise ValueError("locale is invalid")
        if not 0 <= self.creativity <= 2:
            raise ValueError("creativity must be between 0 and 2")
        if not 0 <= self.max_repairs <= 4:
            raise ValueError("max_repairs must be between 0 and 4")
        resolve_widget_contract(self.contract_id)
        if not isinstance(self.creative_profile, CreativeProfile):
            raise ValueError("creative_profile must be a CreativeProfile")
        if (
            isinstance(self.visual_repair_limit, bool)
            or not isinstance(self.visual_repair_limit, int)
            or not 0 <= self.visual_repair_limit <= 10
        ):
            raise ValueError("visual_repair_limit must be an integer between 0 and 10")
        if not self.viewport_targets or any(
            viewport not in {"desktop", "mobile"}
            for viewport in self.viewport_targets
        ):
            raise ValueError("viewport_targets contains an unsupported viewport")
        object.__setattr__(self, "brief", brief)
        object.__setattr__(self, "reference_context", reference_context)
        object.__setattr__(self, "source_url", source_url)
        object.__setattr__(self, "locale", locale)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BuilderRequest":
        reference_context = payload.get("reference_context", "")
        if not isinstance(reference_context, str):
            raise ValueError("reference_context must be text")
        source_url = payload.get("source_url", "")
        if not isinstance(source_url, str):
            raise ValueError("source_url must be text")
        contract_id = payload.get("contract_id", "chat-v1")
        if not isinstance(contract_id, str):
            raise ValueError("contract_id must be text")
        visual_repair_limit = payload.get("visual_repair_limit", 8)
        if isinstance(visual_repair_limit, bool) or not isinstance(
            visual_repair_limit,
            int,
        ):
            raise ValueError("visual_repair_limit must be an integer")
        return cls(
            engine=_enum(EngineName, payload.get("engine", "direct"), "engine"),
            brief=str(payload.get("brief", "")),
            reference_context=reference_context,
            source_url=source_url,
            locale=str(payload.get("locale", "ru")),
            creativity=float(payload.get("creativity", 0.9)),
            viewport_targets=tuple(payload.get("viewport_targets", ("desktop", "mobile"))),
            max_repairs=int(payload.get("max_repairs", 3)),
            contract_id=contract_id,
            creative_profile=_enum(
                CreativeProfile,
                payload.get("creative_profile", "balanced"),
                "creative profile",
            ),
            visual_repair_limit=visual_repair_limit,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine": self.engine.value,
            "brief": self.brief,
            "reference_context": self.reference_context,
            "source_url": self.source_url,
            "locale": self.locale,
            "creativity": self.creativity,
            "viewport_targets": list(self.viewport_targets),
            "max_repairs": self.max_repairs,
            "contract_id": self.contract_id,
            "creative_profile": self.creative_profile.value,
            "visual_repair_limit": self.visual_repair_limit,
        }


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0

    def __post_init__(self) -> None:
        if min(self.prompt_tokens, self.output_tokens, self.thinking_tokens) < 0:
            raise ValueError("token counts must not be negative")

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.output_tokens + self.thinking_tokens

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        if not isinstance(other, TokenUsage):
            return NotImplemented
        return TokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            thinking_tokens=self.thinking_tokens + other.thinking_tokens,
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "TokenUsage":
        payload = payload or {}
        return cls(
            prompt_tokens=int(payload.get("prompt_tokens", 0)),
            output_tokens=int(payload.get("output_tokens", 0)),
            thinking_tokens=int(payload.get("thinking_tokens", 0)),
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "output_tokens": self.output_tokens,
            "thinking_tokens": self.thinking_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass(frozen=True)
class ConceptRoleBrief:
    role: ConceptRole
    summary: str
    decisions: tuple[str, ...]
    safeguards: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.role, ConceptRole):
            raise ValueError("role must be a ConceptRole")
        summary = self.summary.strip()
        decisions = tuple(value.strip() for value in self.decisions)
        safeguards = tuple(value.strip() for value in self.safeguards)
        if not summary or len(summary) > 80:
            raise ValueError("concept role summary is invalid")
        if not 1 <= len(decisions) <= 4 or any(
            not value or len(value) > 160 for value in decisions
        ):
            raise ValueError("concept role decisions are invalid")
        if len(safeguards) > 8 or any(
            not value or len(value) > 160 for value in safeguards
        ):
            raise ValueError("concept role safeguards are invalid")
        object.__setattr__(self, "summary", summary)
        object.__setattr__(self, "decisions", decisions)
        object.__setattr__(self, "safeguards", safeguards)

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        role: ConceptRole | None = None,
    ) -> "ConceptRoleBrief":
        decisions = payload.get("decisions", ())
        safeguards = payload.get("safeguards", ())
        if not isinstance(decisions, (list, tuple)):
            raise ValueError("concept role decisions must be an array")
        if not isinstance(safeguards, (list, tuple)):
            raise ValueError("concept role safeguards must be an array")
        resolved_role = role or _enum(
            ConceptRole,
            payload.get("role"),
            "concept role",
        )
        return cls(
            role=resolved_role,
            summary=str(payload.get("summary", "")),
            decisions=tuple(str(item) for item in decisions),
            safeguards=tuple(str(item) for item in safeguards),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role.value,
            "summary": self.summary,
            "decisions": list(self.decisions),
            "safeguards": list(self.safeguards),
        }


def _direction_role(value: Any) -> DirectionRole | ConceptRole:
    for enum_type in (DirectionRole, ConceptRole):
        try:
            return enum_type(value)
        except (TypeError, ValueError):
            continue
    raise ValueError(f"Unsupported direction role: {value!r}")


@dataclass(frozen=True)
class DirectionProposal:
    proposal_id: str
    role: DirectionRole | ConceptRole
    title: str
    art_direction: str
    interaction_model: str
    safeguards: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.role, (DirectionRole, ConceptRole)):
            raise ValueError("role must be a DirectionRole or ConceptRole")
        if self.proposal_id not in {"candidate-1", "candidate-2", "candidate-3"}:
            raise ValueError("proposal_id must be a bounded anonymous candidate id")
        title = self.title.strip()
        art_direction = self.art_direction.strip()
        interaction_model = self.interaction_model.strip()
        safeguards = tuple(value.strip() for value in self.safeguards)
        if not title or len(title) > 80:
            raise ValueError("direction title is invalid")
        if not art_direction or len(art_direction) > 1200:
            raise ValueError("art_direction is invalid")
        if not interaction_model or len(interaction_model) > 800:
            raise ValueError("interaction_model is invalid")
        if len(safeguards) > 8 or any(
            not value or len(value) > 160 for value in safeguards
        ):
            raise ValueError("direction safeguards are invalid")
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "art_direction", art_direction)
        object.__setattr__(self, "interaction_model", interaction_model)
        object.__setattr__(self, "safeguards", safeguards)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DirectionProposal":
        return cls(
            proposal_id=str(payload["proposal_id"]),
            role=_direction_role(payload["role"]),
            title=str(payload["title"]),
            art_direction=str(payload["art_direction"]),
            interaction_model=str(payload["interaction_model"]),
            safeguards=tuple(str(item) for item in payload.get("safeguards", ())),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "role": self.role.value,
            "title": self.title,
            "art_direction": self.art_direction,
            "interaction_model": self.interaction_model,
            "safeguards": list(self.safeguards),
        }

    def to_anonymous_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "title": self.title,
            "art_direction": self.art_direction,
            "interaction_model": self.interaction_model,
            "safeguards": list(self.safeguards),
        }


@dataclass(frozen=True)
class DirectionJudgement:
    selected_proposal_id: str
    rationale: str

    def __post_init__(self) -> None:
        if self.selected_proposal_id not in {
            "candidate-1",
            "candidate-2",
            "candidate-3",
        }:
            raise ValueError("judge selected an unknown proposal")
        rationale = self.rationale.strip()
        if not rationale or len(rationale) > 1200:
            raise ValueError("direction judgement rationale is invalid")
        object.__setattr__(self, "rationale", rationale)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DirectionJudgement":
        return cls(
            selected_proposal_id=str(payload["selected_proposal_id"]),
            rationale=str(payload["rationale"]),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "selected_proposal_id": self.selected_proposal_id,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    field: str
    message: str

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ValidationIssue":
        return cls(
            code=str(payload["code"]),
            field=str(payload["field"]),
            message=str(payload["message"]),
        )

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "field": self.field, "message": self.message}


@dataclass(frozen=True)
class WidgetArtifact:
    schema_version: str
    revision: int
    stage: Stage
    art_direction: str
    body_html: str
    css: str
    theme_tokens: dict[str, str] = field(default_factory=dict)
    suggested_actions: tuple[str, ...] = ()
    change_summary: str = ""
    javascript: str = ""
    layout_contract: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.revision < 1:
            raise ValueError("revision must be positive")
        if not self.schema_version:
            raise ValueError("schema_version must not be empty")
        object.__setattr__(self, "theme_tokens", dict(self.theme_tokens))
        object.__setattr__(self, "suggested_actions", tuple(self.suggested_actions))
        object.__setattr__(self, "layout_contract", dict(self.layout_contract))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "WidgetArtifact":
        return cls(
            schema_version=str(payload.get("schema_version", "")),
            revision=int(payload.get("revision", 0)),
            stage=_enum(Stage, payload.get("stage"), "stage"),
            art_direction=str(payload.get("art_direction", "")),
            body_html=str(payload.get("body_html", "")),
            css=str(payload.get("css", "")),
            theme_tokens={
                str(key): str(value)
                for key, value in dict(payload.get("theme_tokens", {})).items()
            },
            suggested_actions=tuple(
                str(value) for value in payload.get("suggested_actions", ())
            ),
            change_summary=str(payload.get("change_summary", "")),
            javascript=str(payload.get("javascript", "")),
            layout_contract={
                str(key): str(value)
                for key, value in dict(payload.get("layout_contract", {})).items()
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "revision": self.revision,
            "stage": self.stage.value,
            "art_direction": self.art_direction,
            "body_html": self.body_html,
            "css": self.css,
            "theme_tokens": dict(self.theme_tokens),
            "suggested_actions": list(self.suggested_actions),
            "change_summary": self.change_summary,
            "javascript": self.javascript,
            "layout_contract": dict(self.layout_contract),
        }


ARTIFACT_DIFF_FIELDS = (
    "art_direction",
    "body_html",
    "css",
    "javascript",
    "layout_contract",
    "theme_tokens",
)


def artifact_changed_fields(
    before: WidgetArtifact | None,
    after: WidgetArtifact,
) -> tuple[str, ...]:
    return tuple(
        name
        for name in ARTIFACT_DIFF_FIELDS
        if before is None or getattr(before, name) != getattr(after, name)
    )


def artifact_commit_message(artifact: WidgetArtifact) -> str:
    return (
        artifact.change_summary.strip()
        or f"Ревизия {artifact.revision} готова и передана в предпросмотр."
    )


@dataclass(frozen=True)
class BuilderEvent:
    run_id: str
    sequence: int
    timestamp: datetime
    event_type: str
    stage: Stage | None
    status: str
    message: str
    revision: int | None = None
    usage: TokenUsage = field(default_factory=TokenUsage)
    issues: tuple[ValidationIssue, ...] = ()
    changes: tuple[str, ...] = ()
    error_code: str | None = None
    diagnostic: str | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("sequence must be positive")
        if self.error_code is not None and self.error_code not in PUBLIC_ERROR_CODES:
            raise ValueError("unsupported public error code")
        object.__setattr__(self, "issues", tuple(self.issues))
        object.__setattr__(self, "changes", tuple(self.changes))
        if len(set(self.changes)) != len(self.changes):
            raise ValueError("changes must not contain duplicates")
        if any(change not in ARTIFACT_DIFF_FIELDS for change in self.changes):
            raise ValueError("unsupported artifact change field")

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        sequence: int,
        event_type: str,
        stage: Stage | None,
        status: str,
        message: str,
        revision: int | None = None,
        usage: TokenUsage | None = None,
        issues: tuple[ValidationIssue, ...] = (),
        changes: tuple[str, ...] = (),
        error_code: str | None = None,
        diagnostic: str | None = None,
    ) -> "BuilderEvent":
        return cls(
            run_id=run_id,
            sequence=sequence,
            timestamp=datetime.now(timezone.utc),
            event_type=event_type,
            stage=stage,
            status=status,
            message=message,
            revision=revision,
            usage=usage or TokenUsage(),
            issues=issues,
            changes=changes,
            error_code=error_code,
            diagnostic=diagnostic,
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BuilderEvent":
        return cls(
            run_id=str(payload["run_id"]),
            sequence=int(payload["sequence"]),
            timestamp=datetime.fromisoformat(str(payload["timestamp"])),
            event_type=str(payload["type"]),
            stage=(
                _enum(Stage, payload["stage"], "stage")
                if payload.get("stage") is not None
                else None
            ),
            status=str(payload["status"]),
            message=str(payload["message"]),
            revision=(int(payload["revision"]) if payload.get("revision") is not None else None),
            usage=TokenUsage.from_dict(payload.get("usage")),
            issues=tuple(
                ValidationIssue.from_dict(issue) for issue in payload.get("issues", ())
            ),
            changes=tuple(str(change) for change in payload.get("changes", ())),
            error_code=(str(payload["error_code"]) if payload.get("error_code") else None),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "sequence": self.sequence,
            "timestamp": self.timestamp.isoformat(),
            "type": self.event_type,
            "stage": self.stage.value if self.stage else None,
            "status": self.status,
            "message": self.message,
            "revision": self.revision,
            "usage": self.usage.to_dict(),
            "issues": [issue.to_dict() for issue in self.issues],
            "changes": list(self.changes),
            "error_code": self.error_code,
        }


@dataclass(frozen=True)
class BuilderRunSnapshot:
    run_id: str
    request: BuilderRequest
    status: RunStatus
    created_at: datetime
    updated_at: datetime
    latest_sequence: int
    artifact: WidgetArtifact | None = None
    draft_artifact: WidgetArtifact | None = None
    quality_status: str = "pending"
    usage: TokenUsage = field(default_factory=TokenUsage)
    elapsed_seconds: float = 0.0
    error_code: str | None = None
    cancel_requested: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "request": self.request.to_dict(),
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "latest_sequence": self.latest_sequence,
            "artifact": self.artifact.to_dict() if self.artifact else None,
            "draft_artifact": (
                self.draft_artifact.to_dict() if self.draft_artifact else None
            ),
            "quality_status": self.quality_status,
            "usage": self.usage.to_dict(),
            "elapsed_seconds": self.elapsed_seconds,
            "error_code": self.error_code,
            "cancel_requested": self.cancel_requested,
        }
