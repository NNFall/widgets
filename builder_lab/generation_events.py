from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .redaction import JsonValue, redact_diagnostic, redact_private_data


class GenerationEventType(StrEnum):
    ARTIFACT_COMMITTED = "artifact.committed"
    ARTIFACT_DRAFT_STAGED = "artifact.draft_staged"
    ARTIFACT_SEEDED = "artifact.seeded"
    ARTIFACT_VALIDATED = "artifact.validated"
    DIRECTION_FAILED = "direction.failed"
    DIRECTION_JUDGED = "direction.judged"
    PROVIDER_DISPATCH_ARMED = "provider.dispatch_armed"
    REFERENCE_COMPLETED = "reference.completed"
    REFERENCE_FAILED = "reference.failed"
    REFERENCE_STARTED = "reference.started"
    REFINEMENT_STARTED = "refinement.started"
    REPAIR_COMPLETED = "repair.completed"
    REPAIR_STARTED = "repair.started"
    RUN_CANCEL_REQUESTED = "run.cancel_requested"
    RUN_CANCELLED = "run.cancelled"
    RUN_COMPLETED = "run.completed"
    RUN_CREATED = "run.created"
    RUN_FAILED = "run.failed"
    RUN_TERMINAL_MARKED = "run.terminal_marked"
    SCREENSHOT_CAPTURED = "screenshot.captured"
    STAGE_COMPLETED = "stage.completed"
    STAGE_FAILED = "stage.failed"
    STAGE_INTERRUPTED = "stage.interrupted"
    STAGE_RESULT_STAGED = "stage.result_staged"
    STAGE_RETRY_SCHEDULED = "stage.retry_scheduled"
    STAGE_STARTED = "stage.started"
    VISUAL_AUDIT_BLOCKED = "visual_audit.blocked"
    VISUAL_AUDIT_COMPLETED = "visual_audit.completed"
    VISUAL_AUDIT_PASSED = "visual_audit.passed"
    VISUAL_AUDIT_STARTED = "visual_audit.started"
    VISUAL_CRITIC_COMPLETED = "visual_critic.completed"
    VISUAL_JUDGE_COMPLETED = "visual_judge.completed"
    VISUAL_REPAIR_COMPLETED = "visual_repair.completed"
    VISUAL_REPAIR_STARTED = "visual_repair.started"
    VISUAL_REPAIR_VERIFIER_COMPLETED = "visual_repair.verifier_completed"
    VISUAL_REPAIR_VERIFIER_STARTED = "visual_repair.verifier_started"


@dataclass(frozen=True, slots=True)
class GenerationEventSpec:
    public_fields: frozenset[str]
    stage_result_allowed: bool = False


@dataclass(frozen=True, slots=True)
class PublicGenerationEvent:
    event_type: str
    message: str | None
    payload: dict[str, JsonValue]


def _spec(*fields: str, stage_result_allowed: bool = False) -> GenerationEventSpec:
    return GenerationEventSpec(
        public_fields=frozenset(fields),
        stage_result_allowed=stage_result_allowed,
    )


_RUN_CREATED = _spec("status")
_RUN_TERMINAL = _spec("status", "stage", "error_code")
_ARTIFACT = _spec(
    "status",
    "stage",
    "revision",
    "issues",
    "changes",
    "output_refs",
)
_VISUAL_REVIEW = _spec(
    "status",
    "stage",
    "revision",
    "usage",
    "issues",
    "changes",
    "output_refs",
    "error_code",
)
_VISUAL_REPAIR = _spec(
    "status",
    "stage",
    "revision",
    "attempt",
    "usage",
    "issues",
    "changes",
    "output_refs",
    "error_code",
)

EVENT_REGISTRY: dict[GenerationEventType, GenerationEventSpec] = {
    GenerationEventType.ARTIFACT_COMMITTED: _ARTIFACT,
    GenerationEventType.ARTIFACT_DRAFT_STAGED: _ARTIFACT,
    GenerationEventType.ARTIFACT_SEEDED: _ARTIFACT,
    GenerationEventType.ARTIFACT_VALIDATED: _ARTIFACT,
    GenerationEventType.DIRECTION_FAILED: _spec(
        "status", "stage", "error_code", "usage"
    ),
    GenerationEventType.DIRECTION_JUDGED: _spec(
        "status", "stage", "revision", "usage", "changes", "output_refs"
    ),
    GenerationEventType.PROVIDER_DISPATCH_ARMED: _spec(
        "status", "stage", "attempt", "max_executions"
    ),
    GenerationEventType.REFERENCE_COMPLETED: _spec(
        "status",
        "stage",
        "usage",
        "output_refs",
        stage_result_allowed=True,
    ),
    GenerationEventType.REFERENCE_FAILED: _spec("status", "stage", "error_code"),
    GenerationEventType.REFERENCE_STARTED: _spec("status", "stage"),
    GenerationEventType.REFINEMENT_STARTED: _spec("status", "stage", "revision"),
    GenerationEventType.REPAIR_COMPLETED: _spec(
        "status",
        "stage",
        "revision",
        "usage",
        "issues",
        "changes",
        "output_refs",
        stage_result_allowed=True,
    ),
    GenerationEventType.REPAIR_STARTED: _spec(
        "status", "stage", "revision", "attempt"
    ),
    GenerationEventType.RUN_CANCEL_REQUESTED: _RUN_CREATED,
    GenerationEventType.RUN_CANCELLED: _RUN_TERMINAL,
    GenerationEventType.RUN_COMPLETED: _spec(
        "status", "stage", "revision", "usage", "output_refs"
    ),
    GenerationEventType.RUN_CREATED: _RUN_CREATED,
    GenerationEventType.RUN_FAILED: _spec(
        "status", "stage", "error_code", "usage"
    ),
    GenerationEventType.RUN_TERMINAL_MARKED: _RUN_TERMINAL,
    GenerationEventType.SCREENSHOT_CAPTURED: _spec(
        "status", "stage", "revision", "output_refs"
    ),
    GenerationEventType.STAGE_COMPLETED: _spec(
        "status", "stage", "next_stage", "usage", "output_refs"
    ),
    GenerationEventType.STAGE_FAILED: _spec(
        "status", "stage", "error_code", "usage"
    ),
    GenerationEventType.STAGE_INTERRUPTED: _spec(
        "status", "stage", "error_code", "attempt"
    ),
    GenerationEventType.STAGE_RESULT_STAGED: _spec(
        "status", "stage", "output_refs", "attempt"
    ),
    GenerationEventType.STAGE_RETRY_SCHEDULED: _spec(
        "status",
        "stage",
        "attempt",
        "max_executions",
        "not_before",
        "supersedes_sequence",
        "error_code",
    ),
    GenerationEventType.STAGE_STARTED: _spec(
        "status", "stage", "attempt", "max_executions"
    ),
    GenerationEventType.VISUAL_AUDIT_BLOCKED: _spec(
        "status", "stage", "revision", "issues", "error_code"
    ),
    GenerationEventType.VISUAL_AUDIT_COMPLETED: _spec(
        "status", "stage", "revision", "usage", "issues", "output_refs", "error_code"
    ),
    GenerationEventType.VISUAL_AUDIT_PASSED: _spec(
        "status", "stage", "revision", "issues", "error_code"
    ),
    GenerationEventType.VISUAL_AUDIT_STARTED: _spec(
        "status", "stage", "revision", "attempt"
    ),
    GenerationEventType.VISUAL_CRITIC_COMPLETED: _VISUAL_REVIEW,
    GenerationEventType.VISUAL_JUDGE_COMPLETED: _VISUAL_REVIEW,
    GenerationEventType.VISUAL_REPAIR_COMPLETED: _VISUAL_REPAIR,
    GenerationEventType.VISUAL_REPAIR_STARTED: _VISUAL_REPAIR,
    GenerationEventType.VISUAL_REPAIR_VERIFIER_COMPLETED: _VISUAL_REPAIR,
    GenerationEventType.VISUAL_REPAIR_VERIFIER_STARTED: _VISUAL_REPAIR,
}

if set(EVENT_REGISTRY) != set(GenerationEventType):
    raise RuntimeError("generation event registry must cover every event type")


_TEXT_FIELDS = frozenset({"status", "stage", "next_stage", "error_code"})
_INTEGER_FIELDS = frozenset(
    {"revision", "attempt", "max_executions", "supersedes_sequence"}
)
_OPTIONAL_FIELDS = frozenset(
    {
        "stage",
        "next_stage",
        "revision",
        "attempt",
        "max_executions",
        "not_before",
        "supersedes_sequence",
        "error_code",
    }
)
_COUNTER_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_OPAQUE_OUTPUT_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,255}$")
_ALLOWED_OUTPUT_REF_COLON_PREFIXES = frozenset({"model-call", "provider-call"})
_ISSUE_FIELDS = ("code", "field", "message", "severity")
_MAX_PUBLIC_INTEGER = (1 << 63) - 1
_MAX_USAGE_COUNTERS = 16
_MAX_PUBLIC_FIELDS_SCANNED = 64


def _public_text(value: object, *, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    public = redact_private_data(
        value,
        max_depth=0,
        max_items=0,
        max_string_chars=limit,
    )
    return public if isinstance(public, str) else None


def _public_integer(value: object) -> int | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > _MAX_PUBLIC_INTEGER
    ):
        return None
    return value


def _public_usage(value: object) -> dict[str, JsonValue] | None:
    if not isinstance(value, Mapping):
        return None
    result: dict[str, JsonValue] = {}
    try:
        for scanned, (raw_key, raw_value) in enumerate(value.items()):
            if scanned >= _MAX_PUBLIC_FIELDS_SCANNED:
                return {}
            if len(result) >= _MAX_USAGE_COUNTERS:
                break
            if not isinstance(raw_key, str):
                continue
            key = str.__str__(raw_key)
            if not _COUNTER_NAME.fullmatch(key):
                continue
            counter = _public_integer(raw_value)
            if counter is not None:
                result[key] = counter
    except Exception:
        return {}
    return result


def _public_timestamp(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 64:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def _public_output_refs(value: object) -> list[JsonValue] | None:
    if not isinstance(value, (list, tuple)):
        return None
    result: list[JsonValue] = []
    for candidate in value[:32]:
        if not isinstance(candidate, str) or not _OPAQUE_OUTPUT_REF.fullmatch(candidate):
            continue
        if ":" in candidate and candidate.split(":", 1)[0] not in _ALLOWED_OUTPUT_REF_COLON_PREFIXES:
            continue
        redacted = redact_diagnostic(candidate, limit=257)
        if redacted == candidate:
            result.append(candidate)
    return result


def _public_issues(value: object) -> list[JsonValue] | None:
    if not isinstance(value, (list, tuple)):
        return None
    result: list[JsonValue] = []
    for candidate in value[:32]:
        if isinstance(candidate, str):
            text = _public_text(candidate, limit=1_000)
            if text is not None:
                result.append(text)
            continue
        if not isinstance(candidate, Mapping):
            continue
        issue: dict[str, JsonValue] = {}
        for field in _ISSUE_FIELDS:
            text = _public_text(candidate.get(field), limit=1_000)
            if text is not None:
                issue[field] = text
        if issue:
            result.append(issue)
    return result


def _public_changes(value: object) -> list[JsonValue] | None:
    if not isinstance(value, (list, tuple)):
        return None
    result: list[JsonValue] = []
    for candidate in value[:32]:
        text = _public_text(candidate, limit=1_000)
        if text is not None:
            result.append(text)
    return result


def _project_value(field: str, value: object) -> JsonValue | None:
    if value is None and field in _OPTIONAL_FIELDS:
        return None
    if field in _TEXT_FIELDS:
        return _public_text(value, limit=256)
    if field in _INTEGER_FIELDS:
        return _public_integer(value)
    if field == "usage":
        return _public_usage(value)
    if field == "not_before":
        return _public_timestamp(value)
    if field == "output_refs":
        return _public_output_refs(value)
    if field == "issues":
        return _public_issues(value)
    if field == "changes":
        return _public_changes(value)
    return None


def project_public_generation_event(
    *,
    event_type: object,
    public_message: object,
    payload: object,
) -> PublicGenerationEvent:
    try:
        if isinstance(event_type, str):
            event_type = str.__str__(event_type)
        typed_event = GenerationEventType(event_type)
    except Exception:
        return PublicGenerationEvent(
            event_type="generation.unknown",
            message=None,
            payload={},
        )

    spec = EVENT_REGISTRY[typed_event]
    public_payload: dict[str, JsonValue] = {}
    if isinstance(payload, Mapping):
        try:
            for scanned, (field, raw_value) in enumerate(payload.items()):
                if scanned >= _MAX_PUBLIC_FIELDS_SCANNED:
                    public_payload = {}
                    break
                if not isinstance(field, str):
                    continue
                public_field = str.__str__(field)
                if public_field not in spec.public_fields:
                    continue
                value = _project_value(public_field, raw_value)
                if value is not None or (
                    raw_value is None and public_field in _OPTIONAL_FIELDS
                ):
                    public_payload[public_field] = value
        except Exception:
            public_payload = {}

    return PublicGenerationEvent(
        event_type=typed_event.value,
        message=_public_text(public_message, limit=2_000),
        payload=public_payload,
    )


__all__ = [
    "EVENT_REGISTRY",
    "GenerationEventSpec",
    "GenerationEventType",
    "PublicGenerationEvent",
    "project_public_generation_event",
]
