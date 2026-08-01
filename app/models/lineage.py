from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class ModelCallStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ModelInvocationContext:
    """Explicit accounting lineage for one semantic model invocation."""

    stage_attempt_id: UUID | None
    stage: str | None
    operation: str
    semantic_attempt: int = 1
    candidate_id: str | None = None
    persona: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.operation, str) or not self.operation.strip():
            raise ValueError("operation must be non-empty")
        if isinstance(self.semantic_attempt, bool) or not isinstance(
            self.semantic_attempt, int
        ) or self.semantic_attempt < 1:
            raise ValueError("semantic_attempt must be a positive integer")
        _validate_optional_label("stage", self.stage)
        _validate_optional_label("candidate_id", self.candidate_id)
        _validate_optional_label("persona", self.persona)


def _validate_optional_label(name: str, value: str | None) -> None:
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ValueError(f"{name} must be non-empty when provided")
