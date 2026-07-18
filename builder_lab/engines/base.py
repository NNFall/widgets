from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..models import (
    BuilderRequest,
    Stage,
    TokenUsage,
    ValidationIssue,
    WidgetArtifact,
)


@dataclass(frozen=True)
class EngineResult:
    artifact: WidgetArtifact
    usage: TokenUsage = TokenUsage()
    provider_request_id: str | None = None
    diagnostic: str | None = None


class BuilderEngineError(RuntimeError):
    def __init__(
        self,
        error_code: str,
        public_message: str,
        *,
        diagnostic: str | None = None,
    ) -> None:
        super().__init__(public_message)
        self.error_code = error_code
        self.public_message = public_message
        self.diagnostic = diagnostic


class BuilderEngine(Protocol):
    async def generate(
        self,
        *,
        request: BuilderRequest,
        stage: Stage,
        revision: int,
        previous_artifact: WidgetArtifact | None = None,
        repair_issues: tuple[ValidationIssue, ...] = (),
    ) -> EngineResult: ...

    async def cancel(self) -> None: ...

    async def close(self) -> None: ...
