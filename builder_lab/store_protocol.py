from __future__ import annotations

from typing import Protocol

from .models import (
    BuilderEvent,
    BuilderRequest,
    BuilderRunSnapshot,
    RunStatus,
    Stage,
    TokenUsage,
    ValidationIssue,
    WidgetArtifact,
)


class RunStoreProtocol(Protocol):
    """Storage operations exercised by orchestration and live run delivery."""

    async def create(self, request: BuilderRequest) -> BuilderRunSnapshot: ...

    async def create_seeded(
        self, request: BuilderRequest, artifact: WidgetArtifact
    ) -> BuilderRunSnapshot: ...

    async def snapshot(self, run_id: str) -> BuilderRunSnapshot: ...

    async def update_request(self, run_id: str, request: BuilderRequest) -> None: ...

    async def append_event(
        self,
        run_id: str,
        *,
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
    ) -> BuilderEvent: ...

    async def set_running(self, run_id: str) -> None: ...

    async def commit_artifact(self, run_id: str, artifact: WidgetArtifact) -> None: ...

    async def stage_visual_candidate(
        self, run_id: str, artifact: WidgetArtifact
    ) -> None: ...

    async def stage_visual_draft(
        self, run_id: str, artifact: WidgetArtifact
    ) -> None: ...

    async def commit_visual_candidate(self, run_id: str) -> WidgetArtifact: ...

    async def artifact(
        self, run_id: str, revision: int | None = None
    ) -> WidgetArtifact: ...

    async def preview_artifact(
        self, run_id: str, revision: int | None = None
    ) -> WidgetArtifact: ...

    async def events_after(
        self, run_id: str, sequence: int
    ) -> tuple[BuilderEvent, ...]: ...

    async def wait_for_events(
        self,
        run_id: str,
        sequence: int,
        *,
        timeout: float = 15.0,
    ) -> tuple[BuilderEvent, ...]: ...

    async def request_cancel(self, run_id: str) -> bool: ...

    async def finish(
        self,
        run_id: str,
        status: RunStatus,
        *,
        event_type: str,
        stage: Stage | None,
        message: str,
        revision: int | None = None,
        error_code: str | None = None,
        diagnostic: str | None = None,
        elapsed_seconds: float = 0.0,
    ) -> BuilderEvent: ...

__all__ = ["RunStoreProtocol"]
